"""线索研究模型（TASK-025，需求 §十七 获客与营销）。

═══════════════════════════════════════════════════════════════════════
【本表解决什么问题】
═══════════════════════════════════════════════════════════════════════
§十七 要求的能力：
    老板目标 → 定义目标客户 → 寻找潜在客户 → **公开信息研究** → 客户筛选 →
    建立客户档案 → **生成针对性内容/话术** → **人工确认** → 人工触达 → 进入客户管理
并明确："AI 必须区分 公开事实 / AI推断"，且 "不要默认自动群发"。

本表承载这条链路中"研究"与"话术"两个环节，并**强制**把两类内容分开存：
    facts       —— **公开事实**：必须能在 source_text 里逐字找到（带 quote 引用）
    inferences  —— **AI 推断**：允许有，但必须显式标注、且**不得伪装成事实**

═══════════════════════════════════════════════════════════════════════
【为什么必须存原文 source_text】
═══════════════════════════════════════════════════════════════════════
    ① 没有原文就无法验证 AI 抽出来的"事实"是不是真的在原文里 ——
       那"事实/推断分离"就只是口号。
    ② §十九 的可追溯要求：结论 → 触发原因 → 原始数据 → 来源 → 时间。
       source_text 就是"原始数据"，source_url/source_note 是"来源"，created_at 是"时间"。

═══════════════════════════════════════════════════════════════════════
【数据从哪来：用户提供的公开资料】
═══════════════════════════════════════════════════════════════════════
    用户裁定走 A 方案：**系统不联网抓取**，由用户粘贴/导入公开资料原文
    （公司官网介绍、招聘 JD、行业报道等）。所以本表有 source_url 字段但**只作记录**，
    系统不会自己去访问它 —— 这满足 §二 "经授权的公开互联网数据"与
    §二十六 "禁止编造"。

═══════════════════════════════════════════════════════════════════════
【人工确认是硬闸门】
═══════════════════════════════════════════════════════════════════════
    draft_status 只能由**人工**接口推进（confirm/reject）。
    ★ AI 不能把自己的生成结果标成已确认 —— 那等于自己批准自己。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
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


class ResearchTargetType(str, Enum):
    """研究对象类型。

    潜在客户（还没建档）与已有客户都可能有研究需求，
    所以不强制挂 ForeignKey 到 customers —— 用 target_type 区分。
    """

    PROSPECT = "PROSPECT"  # 潜在客户（尚未建档）
    CUSTOMER = "CUSTOMER"  # 已有客户


class DraftStatus(str, Enum):
    """话术/内容的确认状态。★ 只能由人工接口推进。"""

    NONE = "NONE"          # 还没生成
    GENERATED = "GENERATED"  # AI 已生成，等人工确认
    CONFIRMED = "CONFIRMED"  # 人工已确认可用
    REJECTED = "REJECTED"    # 人工判定不可用


class ResearchSourceType(str, Enum):
    """资料类型（需求 §十七 举的例子）。"""

    COMPANY_SITE = "COMPANY_SITE"  # 公司官网
    NEWS = "NEWS"                  # 新闻
    JOB_POSTING = "JOB_POSTING"    # 招聘信息
    INDUSTRY_SITE = "INDUSTRY_SITE"  # 行业网站
    PRODUCT_INFO = "PRODUCT_INFO"  # 公开产品信息
    OTHER = "OTHER"


class ProspectResearch(Base):
    """一次线索研究。"""

    __tablename__ = "prospect_research"

    __table_args__ = (
        Index("ix_prospect_research_customer", "customer_id"),
        Index("ix_prospect_research_status", "draft_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    target_type: Mapped[ResearchTargetType] = mapped_column(
        SAEnum(ResearchTargetType, name="research_target_type", values_callable=_enum_values),
        nullable=False,
        default=ResearchTargetType.PROSPECT,
    )
    #: 研究对象名称（潜在客户时是公司名/人名；没有客户档案也能做研究）
    target_name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: 关联的已有客户（target_type=CUSTOMER 时必填；PROSPECT 时为 NULL）
    customer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )

    source_type: Mapped[ResearchSourceType] = mapped_column(
        SAEnum(ResearchSourceType, name="research_source_type", values_callable=_enum_values),
        nullable=False,
        default=ResearchSourceType.COMPANY_SITE,
    )
    #: 来源链接或出处说明。★ **只作记录**，系统不会去访问它（A 方案不联网抓取）
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: ★ 原文。必须留存 —— 否则无法验证"事实"是否真的在原文里
    source_text: Mapped[str] = mapped_column(
        Text, nullable=False, comment="用户提供的公开资料原文；AI 只能从中抽取事实"
    )

    #: 研究要点（TASK-042）：这次想弄清哪些问题。
    #: ★ 与 source_text 的区别必须说清，否则会被误当成"证据"：
    #:   focus       = 人的**提问**（要查证什么）—— 允许 AI 起草，它本来就是待验证的
    #:   source_text = 真实的**证据**（公开资料原文）—— 绝不允许 AI 生成
    #: 两者混在一起就会出问题：把 AI 编的问题当成"已核实的资料"。
    focus: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="研究要点（要查证的问题）；AI 可起草，不是证据"
    )

    #: 〔公开事实〕列表：[{statement, quote, source_ref}]，quote 必须是原文的逐字片段
    facts_json: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment="公开事实；每条必须带原文逐字引用 quote"
    )
    #: 〔AI 推断〕列表：[{statement, basis, confidence_note}]，**不得与事实混放**
    inferences_json: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment="AI 推断；显式标注，不得伪装成事实"
    )
    #: 研究结论摘要（人工可读）
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── 话术/内容生成与人工确认 ──
    draft_status: Mapped[DraftStatus] = mapped_column(
        SAEnum(DraftStatus, name="research_draft_status", values_callable=_enum_values),
        nullable=False,
        default=DraftStatus.NONE,
    )
    #: 生成的话术候选：[{channel, content, note}]
    drafts_json: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment="AI 生成的话术候选；需人工确认后才可用"
    )
    confirmed_by: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="确认人（登录用户名）"
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(Stamp6, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    #: 声明来源（TASK-021 的 8 种 SourceType 之一）
    source_type_evidence: Mapped[str] = mapped_column(
        String(32), nullable=False, default="MANUAL", comment="本次研究的来源类型（MANUAL=人工提供资料）"
    )

    llm_called: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="本次是否真的调用了模型"
    )
    llm_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(Stamp6, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        Stamp6, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ProspectResearch id={self.id} {self.target_name!r} {self.draft_status}>"
