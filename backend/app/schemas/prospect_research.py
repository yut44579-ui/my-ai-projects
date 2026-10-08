"""线索研究契约（TASK-025，需求 §十七）。

★ 结构性分离：`facts` 与 `inferences` 是**两个独立字段**，不是一个混在一起
  的列表。前端因此不可能把推断当事实展示（除非故意），这是格式层面的保证。
★ 每条 fact 带 `quote`（原文逐字片段），可点开核对。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.prospect_research import (
    DraftStatus,
    ResearchSourceType,
    ResearchTargetType,
)
from app.schemas.evidence import EvidenceValue


class ResearchFact(BaseModel):
    """〔公开事实〕一条。★ quote 是**原文逐字片段**，服务端已回验它在原文里存在。"""

    statement: str
    quote: str = Field(description="原文逐字片段；服务端已回验，可核对")


class ResearchInference(BaseModel):
    """〔AI 推断〕一条。★ 与事实分开返回，且必须给出依据。"""

    statement: str
    basis: str = Field(description="依据哪条事实推出来的")


class ResearchDraft(BaseModel):
    """话术候选。★ 需人工确认后才可用；本系统不发送任何内容。"""

    channel: str
    content: str
    note: str = Field(default="", description="内部备注：这条基于什么写的")


class ProspectResearchItem(BaseModel):
    id: int
    target_type: ResearchTargetType
    target_name: str
    customer_id: int | None = None
    customer_name: str | None = None
    source_type: ResearchSourceType
    source_type_label: str
    source_url: str | None = Field(
        default=None, description="来源链接（★ 仅记录，系统不会访问它）"
    )
    source_length: int = Field(description="原文长度（字符数）")
    focus: str | None = Field(
        default=None,
        description=(
            "研究要点（要查证的问题）。★ 允许 AI 起草 —— 它本来就是待验证的提问；"
            "与原文（证据）是两个字段，不可混用"
        ),
    )
    facts: list[ResearchFact] = Field(default_factory=list, description="〔公开事实〕")
    inferences: list[ResearchInference] = Field(default_factory=list, description="〔AI 推断〕")
    summary: str | None = None
    draft_status: DraftStatus
    draft_status_label: str
    drafts: list[ResearchDraft] = Field(default_factory=list)
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    reject_reason: str | None = None
    llm_called: bool = False
    llm_error: str | None = None
    created_at: datetime
    updated_at: datetime


class ProspectResearchDetail(ProspectResearchItem):
    """详情比列表多返回原文（列表里不返回，避免列表接口体积过大）。"""

    source_text: str


class ResearchCreateRequest(BaseModel):
    target_type: ResearchTargetType = ResearchTargetType.PROSPECT
    target_name: str = Field(min_length=1, max_length=200, description="研究对象名称（公司名/人名）")
    customer_id: int | None = Field(default=None, description="target_type=CUSTOMER 时必填")
    source_type: ResearchSourceType = ResearchSourceType.COMPANY_SITE
    source_text: str = Field(
        description=(
            "★ 公开资料**原文**（用户提供）。必须有足够原文，否则无法验证抽取出的事实。"
            "★ 这里刻意**不写 min_length**：长度交给服务层校验，"
            "以便返回 400 + research_invalid（明确说明「原文太短」），"
            "而不是 Pydantic 的 422（语义不同，且 422 的提示不如自定义文案清楚）。见 D37/D42。"
        ),
    )
    source_url: str | None = Field(default=None, max_length=500, description="来源链接（仅记录）")
    focus: str | None = Field(
        default=None,
        max_length=1000,
        description="研究要点（要查证的问题，选填）。★ 可用 AI 起草 —— 它不是证据，是提问",
    )


class ResearchListResponse(BaseModel):
    items: list[ProspectResearchItem]
    page: int
    page_size: int
    total: EvidenceValue


class ExtractRequest(BaseModel):
    question: str | None = Field(
        default=None, max_length=300, description="可选：让模型重点关注什么"
    )


class ExtractResponse(BaseModel):
    """抽取结果。★ 同时返回被剔除的引用数量，让"防幻觉"这件事可见。"""

    research: ProspectResearchDetail
    facts_count: int
    inferences_count: int
    rejected_count: int = Field(
        description="★ 因「引用在原文里找不到」被剔除的条目数（防幻觉机制生效的次数）"
    )
    note: str


class DraftRequest(BaseModel):
    pass


class DraftResponse(BaseModel):
    research: ProspectResearchDetail
    drafts_count: int
    note: str


class ConfirmRequest(BaseModel):
    accepted_indexes: list[int] = Field(
        min_length=1, description="采纳哪几条话术（下标从 0 起）"
    )


class RejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class ResearchSummaryResponse(BaseModel):
    """获客研究概览。"""

    total: EvidenceValue
    pending_confirm_total: EvidenceValue = Field(description="等人工确认的话术数")
    confirmed_total: EvidenceValue
    rejected_total: EvidenceValue
    note: str
