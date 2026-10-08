"""方案契约（TASK-030，需求 §八 输出中心 → 方案）。

★ 与知识库/获客一致的分离原则：
  `inputs` 返回**生成时冻结的输入**（引用过哪些资料片段、哪些业务数字），
  `content` 是 AI 生成的正文。两者分开，人能核对"这句话是从哪来的"。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.proposal import ProposalKind, ProposalStatus
from app.schemas.evidence import EvidenceValue


class ProposalSection(BaseModel):
    """方案的一节。★ basis 说明这一节依据什么，便于核对是否编造。"""

    heading: str
    body: str
    basis: str = Field(default="", description="这一节依据的资料片段或业务数字")


class FrozenKnowledgeHit(BaseModel):
    """生成时冻结的知识片段引用（§十九 可追溯）。"""

    evidence_ref: str
    document_title: str
    seq: int
    content: str
    score: int


class ProposalInputs(BaseModel):
    """生成时冻结的输入快照。"""

    frozen_at: str | None = None
    knowledge_hits: list[FrozenKnowledgeHit] = Field(default_factory=list)
    business_data: dict = Field(default_factory=dict)
    requirement: str | None = None
    missing_info: list[str] = Field(default_factory=list)


class ProposalItem(BaseModel):
    id: int
    title: str
    kind: ProposalKind
    kind_label: str
    status: ProposalStatus
    status_label: str
    customer_id: int | None = None
    customer_name: str | None = None
    opportunity_id: int | None = None
    opportunity_title: str | None = None
    target_name: str | None = None
    requirement: str | None = None
    section_count: int = 0
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    reject_reason: str | None = None
    llm_called: bool = False
    llm_error: str | None = None
    created_at: datetime
    updated_at: datetime


class ProposalDetail(ProposalItem):
    content: list[ProposalSection] = Field(default_factory=list)
    inputs: ProposalInputs | None = Field(
        default=None, description="生成时冻结的输入（资料引用 + 业务数据）"
    )


class ProposalCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    kind: ProposalKind = ProposalKind.SOLUTION
    customer_id: int | None = None
    opportunity_id: int | None = None
    target_name: str | None = Field(default=None, max_length=200)
    requirement: str | None = Field(
        default=None, description="客户需求/背景，人工填写；AI 不得添加这里没有的需求"
    )


class ProposalUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    kind: ProposalKind | None = None
    requirement: str | None = Field(
        default=None,
        description="★ 改了需求会把已生成内容清空并退回「未生成」（旧内容基于旧需求，留着会误导）",
    )


class ProposalGenerateRequest(BaseModel):
    doc_type: str | None = Field(default=None, description="只在某类知识库资料里检索")
    top_k: int = Field(default=5, ge=1, le=20)


class ProposalGenerateResponse(BaseModel):
    proposal: ProposalDetail
    section_count: int
    knowledge_hit_count: int
    missing_info: list[str] = Field(default_factory=list)
    note: str


class ProposalConfirmRequest(BaseModel):
    pass


class ProposalRejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class ProposalListResponse(BaseModel):
    items: list[ProposalItem]
    page: int
    page_size: int
    total: EvidenceValue


class ProposalSummaryResponse(BaseModel):
    total: EvidenceValue
    pending_confirm_total: EvidenceValue
    confirmed_total: EvidenceValue
    rejected_total: EvidenceValue
    note: str


class ProposalCandidateCustomer(BaseModel):
    """可选对象（建方案时挑客户用）。"""

    id: int
    name: str
    company_name: str | None = None


class ProposalCandidateOpportunity(BaseModel):
    id: int
    title: str
    customer_id: int | None = None
    stage: str


class ProposalCandidatesResponse(BaseModel):
    customers: list[ProposalCandidateCustomer]
    opportunities: list[ProposalCandidateOpportunity]
