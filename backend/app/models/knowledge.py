"""知识库模型（TASK-026，需求 §二十二）。

═══════════════════════════════════════════════════════════════════════
【§二十二 的两条硬要求】
═══════════════════════════════════════════════════════════════════════
    ① 链路：导入资料 → 解析 → **保存原始资料** → 建立知识 → AI 调用 →
       结合真实业务数据 → 形成分析 → 生成方案/话术/报告
    ② ★ "**但不能把知识库内容伪装成真实业务事实**"

第 ② 条决定了本表的设计：知识库内容与业务数据（客户/商机/项目）
**在存储上完全分开**（不同表、不同 evidence_ref 前缀），
在返回给前端时也分开两个字段返回 —— 不是靠提示词"注意区分"。

═══════════════════════════════════════════════════════════════════════
【为什么不用向量库（§五）】
═══════════════════════════════════════════════════════════════════════
    §五 明确禁止：向量数据库 / 知识图谱 / RAG Pipeline。
    用户也裁定走 A 方案。所以检索用**关键词匹配 + 结构化管理**
    （标签、来源、生效期），AI 调用时把命中片段作为上下文。
    ★ 这不是"退而求其次"：对"产品资料/竞品资料/行业报告"这类
      文档量（几十到几百篇），关键词 + 标签过滤的效果足够，
      且**不需要额外模型、不需要外部服务、结果可解释**
      （命中原因就是"包含这些词"），比向量相似度更便于人工核对。

═══════════════════════════════════════════════════════════════════════
【为什么切片单独一张表】
═══════════════════════════════════════════════════════════════════════
    · 检索要返回**片段级**结果（而非整篇），否则喂给模型的上下文过大；
    · 每个片段要能作为**证据锚点**（§十九 可追溯），指向原文的具体位置；
    · LIKE 检索需要 content 是真实列而不是 JSON 里的一段文本。
    所以切片独立成表，带 char_start/char_end 指向原文位置。
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.import_batch import _enum_values

Stamp6 = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


class KnowledgeDocType(str, Enum):
    """资料类型（§二十二 举的例子：产品/市场/用户/竞品/商业资料）。"""

    PRODUCT = "PRODUCT"    # 产品资料
    MARKET = "MARKET"      # 市场资料
    USER = "USER"          # 用户资料
    COMPETITOR = "COMPETITOR"  # 竞品资料
    BUSINESS = "BUSINESS"  # 商业资料
    OTHER = "OTHER"


class KnowledgeSourceType(str, Enum):
    """资料怎么进来的。

    ★ 只有这几种 —— 系统**不联网抓取**（用户裁定 A 方案）：
        PASTED   —— 人工粘贴原文
        UPLOAD   —— 上传文件（txt/md/csv 等文本类）
        MANUAL   —— 人工录入
    """

    PASTED = "PASTED"
    UPLOAD = "UPLOAD"
    MANUAL = "MANUAL"


class KnowledgeDocument(Base):
    """一份知识库资料（原文归档）。"""

    __tablename__ = "knowledge_documents"

    __table_args__ = (
        Index("ix_knowledge_documents_type", "doc_type"),
        Index("ix_knowledge_documents_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    doc_type: Mapped[KnowledgeDocType] = mapped_column(
        SAEnum(KnowledgeDocType, name="knowledge_doc_type", values_callable=_enum_values),
        nullable=False,
        default=KnowledgeDocType.OTHER,
    )
    source_type: Mapped[KnowledgeSourceType] = mapped_column(
        SAEnum(KnowledgeSourceType, name="knowledge_source_type", values_callable=_enum_values),
        nullable=False,
        default=KnowledgeSourceType.PASTED,
    )
    #: 出处说明/链接。★ 只作记录，系统不会去访问（A 方案不联网抓取）
    source_note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    #: ★ 原文。必须留存 —— 否则片段无法回溯到出处，可追溯就断了
    source_text: Mapped[str] = mapped_column(
        Text, nullable=False, comment="资料原文；切片通过 char_start/char_end 指向这里"
    )

    #: 标签（人工打的，用于检索时过滤）
    tags_json: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="标签列表")

    #: 生效期。★ 市场/竞品资料会过期，过期资料不该继续影响判断
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(
        Date, nullable=True, comment="失效日；NULL=长期有效"
    )

    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(Stamp6, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        Stamp6, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<KnowledgeDocument id={self.id} {self.title!r} {self.doc_type}>"


class KnowledgeChunk(Base):
    """资料切片。

    ★ 独立成表的原因见模块头。每个切片带 char_start/char_end，
      可以精确定位回 source_text 的哪一段 —— 这是"证据可追溯"的落点。
    """

    __tablename__ = "knowledge_chunks"

    __table_args__ = (
        Index("ix_knowledge_chunks_document", "document_id"),
        # 检索按切片内容 LIKE，给 document_id + seq 组合索引保证按篇取切片有序
        Index("ix_knowledge_chunks_doc_seq", "document_id", "seq"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    document_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, comment="在文档内的序号（从 1 起）")

    content: Mapped[str] = mapped_column(Text, nullable=False, comment="切片正文")

    #: 在原文中的字符区间 [start, end) —— 证据锚点的定位依据
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)

    #: 证据锚点 = knowledge_chunk:{id}
    evidence_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(Stamp6, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<KnowledgeChunk id={self.id} doc={self.document_id} seq={self.seq}>"
