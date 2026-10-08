"""知识库契约（TASK-026，需求 §二十二）。

★ §二十二 第 ② 条的结构性落地：
  `KnowledgeAnalyzeResponse` 把两类内容**分成两个字段**返回：
    knowledge_used —— 命中的资料片段（锚点 knowledge_chunk:{id}）
    business_data  —— 真实业务数字（EvidenceValue 口径）
  前端因此不可能把资料当业务事实展示（除非故意拼在一起）。
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.knowledge import (
    KnowledgeDocType,
    KnowledgeSourceType,
)
from app.schemas.evidence import EvidenceValue


class KnowledgeChunkItem(BaseModel):
    id: int
    seq: int
    content: str
    char_start: int
    char_end: int
    evidence_ref: str = Field(description="= knowledge_chunk:{id}，可回溯到原文位置")


class KnowledgeDocumentItem(BaseModel):
    id: int
    title: str
    doc_type: KnowledgeDocType
    doc_type_label: str
    source_type: KnowledgeSourceType
    source_type_label: str
    source_note: str | None = Field(default=None, description="出处说明（仅记录，系统不访问）")
    tags: list[str] = Field(default_factory=list)
    effective_from: date | None = None
    effective_to: date | None = Field(default=None, description="失效日；NULL=长期有效")
    is_expired: bool = Field(description="是否已过失效日（过期资料默认不参与检索）")
    source_length: int = Field(description="原文长度（字符数）")
    chunk_count: int
    created_at: datetime
    updated_at: datetime


class KnowledgeDocumentDetail(KnowledgeDocumentItem):
    source_text: str = Field(description="资料原文")
    chunks: list[KnowledgeChunkItem] = Field(default_factory=list)


class KnowledgeCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    doc_type: KnowledgeDocType = KnowledgeDocType.OTHER
    source_type: KnowledgeSourceType = KnowledgeSourceType.PASTED
    source_text: str = Field(
        description=(
            "资料原文。★ 刻意不写 min_length：长度交给服务层校验，"
            "以便返回 400 + knowledge_invalid（说明「至少 50 字」）而不是 422（见 D37/D42）。"
        ),
    )
    source_note: str | None = Field(default=None, max_length=500, description="出处（仅记录）")
    tags: list[str] = Field(default_factory=list, description="标签，用于检索过滤")
    effective_from: date | None = None
    effective_to: date | None = None


class KnowledgeListResponse(BaseModel):
    items: list[KnowledgeDocumentItem]
    page: int
    page_size: int
    total: EvidenceValue


class KnowledgeSearchHit(BaseModel):
    chunk_id: int
    document_id: int
    document_title: str
    doc_type: KnowledgeDocType
    doc_type_label: str
    seq: int
    content: str
    score: int = Field(description="相关度分值（命中词数 + 整串命中加权）")
    matched_terms: list[str] = Field(description="★ 命中了哪些词 —— 让「为什么选它」可解释")
    evidence_ref: str


class KnowledgeSearchResponse(BaseModel):
    query: str
    terms: list[str] = Field(description="查询被切成的检索词（中文取 2-gram）")
    hits: list[KnowledgeSearchHit]
    total: EvidenceValue = Field(description="命中片段数")
    note: str


class KnowledgeAnalyzeRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    doc_type: KnowledgeDocType | None = Field(default=None, description="只在这些类型的资料里检索")
    tag: str | None = Field(default=None, max_length=64)
    top_k: int = Field(default=6, ge=1, le=20)


class BusinessDataItem(BaseModel):
    """一条真实业务数据。★ 与资料分开返回，各自带来源。"""

    key: str
    label: str
    evidence: EvidenceValue


class KnowledgeAnalyzeResponse(BaseModel):
    """分析结果。★ 资料与业务数据分成两个字段，防止互相冒充（§二十二）。"""

    question: str
    analysis: str
    knowledge_used: list[KnowledgeSearchHit] = Field(
        default_factory=list, description="〔参考资料〕背景知识，不是本公司的业务事实"
    )
    business_data: list[BusinessDataItem] = Field(
        default_factory=list, description="〔真实业务数据〕系统查库所得"
    )
    from_knowledge: list[str] = Field(default_factory=list, description="哪些结论来自资料")
    from_business_data: list[str] = Field(
        default_factory=list, description="哪些结论来自真实业务数据"
    )
    gaps: list[str] = Field(default_factory=list, description="还缺什么信息")
    llm_called: bool = False
    llm_error: str | None = None
    note: str


class KnowledgeSummaryResponse(BaseModel):
    total: EvidenceValue
    chunk_total: EvidenceValue
    expired_total: EvidenceValue
    by_type: list[dict] = Field(default_factory=list, description="按资料类型分布")
    note: str
