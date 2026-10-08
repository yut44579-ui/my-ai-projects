"""方案接口（TASK-030，需求 §八 输出中心 → 方案）。

    GET    /api/proposals/summary              概览
    GET    /api/proposals                      方案列表
    GET    /api/proposals/candidates           可选对象（客户/商机）
    POST   /api/proposals                      新建方案
    GET    /api/proposals/{id}                 详情（含正文与冻结输入）
    PATCH  /api/proposals/{id}                 改标题/类型/需求
    POST   /api/proposals/{id}/generate        生成草稿（★ 引用知识库 + 真实业务数据）
    POST   /api/proposals/{id}/confirm         人工确认可用
    POST   /api/proposals/{id}/reject          人工驳回
    DELETE /api/proposals/{id}                 移除（软删除）

★ 路由顺序：/summary 与 /candidates 必须在 /{proposal_id} 之前（踩过多次的坑）。
★ 本模块**没有任何发送能力**：confirm 只标记"这份方案可用"（§十六）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.customer import Customer
from app.models.knowledge import KnowledgeDocType
from app.models.opportunity import CLOSED_STAGES, Opportunity
from app.models.proposal import Proposal, ProposalKind, ProposalStatus
from app.models.user import User
from app.schemas.evidence import SourceType, build_evidence, no_data_evidence
from app.schemas.proposals import (
    FrozenKnowledgeHit,
    ProposalCandidateCustomer,
    ProposalCandidateOpportunity,
    ProposalCandidatesResponse,
    ProposalConfirmRequest,
    ProposalCreateRequest,
    ProposalDetail,
    ProposalGenerateRequest,
    ProposalGenerateResponse,
    ProposalInputs,
    ProposalItem,
    ProposalListResponse,
    ProposalRejectRequest,
    ProposalSection,
    ProposalSummaryResponse,
    ProposalUpdateRequest,
)
from app.services.errors import ApiErrorCode, ApiFailure
from app.services.proposals import (
    ProposalError,
    confirm_proposal,
    create_proposal,
    generate,
    get_proposal,
    list_proposals,
    reject_proposal,
    stage_counts,
    update_proposal,
)

router = APIRouter(tags=["proposals"])

KIND_LABELS: dict[ProposalKind, str] = {
    ProposalKind.SOLUTION: "解决方案",
    ProposalKind.QUOTATION_NOTE: "报价说明",
    ProposalKind.SERVICE_PLAN: "服务方案",
    ProposalKind.COOPERATION: "合作建议",
    ProposalKind.OTHER: "其他",
}

STATUS_LABELS: dict[ProposalStatus, str] = {
    ProposalStatus.NONE: "未生成",
    ProposalStatus.GENERATED: "待人工确认",
    ProposalStatus.CONFIRMED: "已确认",
    ProposalStatus.REJECTED: "已驳回",
}


def _base(row: Proposal, customer_name: str | None, opp_title: str | None) -> dict:
    return dict(
        id=row.id,
        title=row.title,
        kind=row.kind,
        kind_label=KIND_LABELS.get(row.kind, row.kind.value),
        status=row.status,
        status_label=STATUS_LABELS.get(row.status, row.status.value),
        customer_id=row.customer_id,
        customer_name=customer_name,
        opportunity_id=row.opportunity_id,
        opportunity_title=opp_title,
        target_name=row.target_name,
        requirement=row.requirement,
        section_count=len(row.content_json or []),
        confirmed_by=row.confirmed_by,
        confirmed_at=row.confirmed_at,
        reject_reason=row.reject_reason,
        llm_called=row.llm_called,
        llm_error=row.llm_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _names(db: Session, rows: list[Proposal]) -> tuple[dict[int, str], dict[int, str]]:
    cids = [r.customer_id for r in rows if r.customer_id]
    oids = [r.opportunity_id for r in rows if r.opportunity_id]
    cnames = (
        dict(
            db.execute(
                select(Customer.id, Customer.name).where(Customer.id.in_(cids))
            ).all()
        )
        if cids
        else {}
    )
    otitles = (
        dict(
            db.execute(
                select(Opportunity.id, Opportunity.title).where(Opportunity.id.in_(oids))
            ).all()
        )
        if oids
        else {}
    )
    return cnames, otitles


def _to_item(db: Session, row: Proposal) -> ProposalItem:
    cnames, otitles = _names(db, [row])
    return ProposalItem(
        **_base(row, cnames.get(row.customer_id or 0), otitles.get(row.opportunity_id or 0))
    )


def _to_detail(db: Session, row: Proposal) -> ProposalDetail:
    cnames, otitles = _names(db, [row])
    inputs = None
    if row.inputs_json:
        raw = row.inputs_json
        inputs = ProposalInputs(
            frozen_at=raw.get("frozen_at"),
            knowledge_hits=[FrozenKnowledgeHit(**h) for h in (raw.get("knowledge_hits") or [])],
            business_data=raw.get("business_data") or {},
            requirement=raw.get("requirement"),
            missing_info=raw.get("missing_info") or [],
        )
    return ProposalDetail(
        **_base(row, cnames.get(row.customer_id or 0), otitles.get(row.opportunity_id or 0)),
        content=[ProposalSection(**s) for s in (row.content_json or [])],
        inputs=inputs,
    )


def _require(db: Session, proposal_id: int) -> Proposal:
    row = get_proposal(db, proposal_id)
    if row is None:
        raise ApiFailure(
            ApiErrorCode.PROPOSAL_NOT_FOUND, f"方案 {proposal_id} 不存在", http_status=404
        )
    return row


@router.get("/proposals/summary", response_model=ProposalSummaryResponse, summary="方案概览")
def proposals_summary(db: Session = Depends(get_db)) -> ProposalSummaryResponse:
    counts = stage_counts(db)
    total = sum(counts.values())

    def ev_or_nodata(n: int, ref: str, empty: str):
        if total:
            return build_evidence(n, source_type=SourceType.MANUAL, evidence_ref=ref)
        return no_data_evidence(empty, source_type=SourceType.MANUAL, evidence_ref=ref)

    return ProposalSummaryResponse(
        total=ev_or_nodata(total, "proposals:count", "还没有做过方案"),
        pending_confirm_total=ev_or_nodata(
            counts["GENERATED"], "proposals:pending", "没有待确认的方案"
        ),
        confirmed_total=ev_or_nodata(counts["CONFIRMED"], "proposals:confirmed", "还没有确认过方案"),
        rejected_total=ev_or_nodata(counts["REJECTED"], "proposals:rejected", "没有驳回记录"),
        note=(
            "方案基于「知识库里的真实产品资料」与「真实业务数据」生成，"
            "生成时会冻结当时的输入以便事后核对（§十九）。"
            "★ 必须人工确认后才可用 —— 本系统不发送任何内容，也不替代报价审批。"
        ),
    )


@router.get("/proposals/candidates", response_model=ProposalCandidatesResponse, summary="可选对象")
def proposals_candidates(db: Session = Depends(get_db)) -> ProposalCandidatesResponse:
    customers = db.scalars(select(Customer).order_by(Customer.id.desc()).limit(200)).all()
    opps = db.scalars(
        select(Opportunity)
        .where(Opportunity.is_active.is_(True))
        .order_by(Opportunity.id.desc())
        .limit(200)
    ).all()
    return ProposalCandidatesResponse(
        customers=[
            ProposalCandidateCustomer(id=c.id, name=c.name, company_name=c.company_name)
            for c in customers
        ],
        opportunities=[
            ProposalCandidateOpportunity(
                id=o.id,
                title=o.title,
                customer_id=o.customer_id,
                stage=o.stage.value if hasattr(o.stage, "value") else str(o.stage),
            )
            for o in opps
            if o.stage not in CLOSED_STAGES
        ],
    )


@router.get("/proposals", response_model=ProposalListResponse, summary="方案列表")
def proposals_list(
    status: ProposalStatus | None = Query(default=None),
    customer_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> ProposalListResponse:
    rows, total = list_proposals(
        db, status=status, customer_id=customer_id, limit=page_size, offset=(page - 1) * page_size
    )
    cnames, otitles = _names(db, rows)
    total_ev = (
        build_evidence(total, source_type=SourceType.MANUAL, evidence_ref="proposals:list")
        if total
        else no_data_evidence(
            "当前筛选下没有方案", source_type=SourceType.MANUAL, evidence_ref="proposals:list"
        )
    )
    return ProposalListResponse(
        items=[
            ProposalItem(**_base(r, cnames.get(r.customer_id or 0), otitles.get(r.opportunity_id or 0)))
            for r in rows
        ],
        page=page,
        page_size=page_size,
        total=total_ev,
    )


@router.post(
    "/proposals", response_model=ProposalDetail, status_code=201, summary="新建方案"
)
def proposals_create(
    payload: ProposalCreateRequest, db: Session = Depends(get_db)
) -> ProposalDetail:
    try:
        row = create_proposal(
            db,
            title=payload.title,
            kind=payload.kind,
            customer_id=payload.customer_id,
            opportunity_id=payload.opportunity_id,
            target_name=payload.target_name,
            requirement=payload.requirement,
        )
    except ProposalError as exc:
        raise ApiFailure(ApiErrorCode.PROPOSAL_INVALID, str(exc))
    return _to_detail(db, row)


@router.get("/proposals/{proposal_id}", response_model=ProposalDetail, summary="方案详情")
def proposals_detail(proposal_id: int, db: Session = Depends(get_db)) -> ProposalDetail:
    return _to_detail(db, _require(db, proposal_id))


@router.patch("/proposals/{proposal_id}", response_model=ProposalDetail, summary="改方案")
def proposals_update(
    proposal_id: int, payload: ProposalUpdateRequest, db: Session = Depends(get_db)
) -> ProposalDetail:
    row = _require(db, proposal_id)
    updated = update_proposal(
        db, row, title=payload.title, requirement=payload.requirement, kind=payload.kind
    )
    return _to_detail(db, updated)


@router.post(
    "/proposals/{proposal_id}/generate",
    response_model=ProposalGenerateResponse,
    summary="生成方案草稿（引用知识库 + 真实业务数据）",
)
def proposals_generate(
    proposal_id: int, payload: ProposalGenerateRequest, db: Session = Depends(get_db)
) -> ProposalGenerateResponse:
    """生成方案草稿。

    ★ 输入三块**分开标注**：知识库资料（唯一可引用的产品信息）、
      真实业务数据（客户/商机）、人工填的需求背景。
      生成后把输入冻结进 `inputs`，事后可核对"这份方案当初依据什么"。
    """
    row = _require(db, proposal_id)
    doc_type = KnowledgeDocType(payload.doc_type) if payload.doc_type else None
    try:
        outcome = generate(db, row, doc_type=doc_type, top_k=payload.top_k)
    except ProposalError as exc:
        raise ApiFailure(ApiErrorCode.PROPOSAL_INVALID, str(exc))

    if outcome.llm_error:
        raise ApiFailure(
            ApiErrorCode.LLM_UNAVAILABLE,
            f"AI 暂时不可用，无法生成方案（原因：{outcome.llm_error}）",
            http_status=502,
        )

    db.refresh(row)
    return ProposalGenerateResponse(
        proposal=_to_detail(db, row),
        section_count=len(outcome.sections),
        knowledge_hit_count=len(outcome.hits),
        missing_info=outcome.missing_info,
        note=(
            f"已生成 {len(outcome.sections)} 节，引用了 {len(outcome.hits)} 条知识库资料，"
            "状态为「待人工确认」。"
            + (
                "★ 知识库没有命中资料，涉及产品的内容请务必人工核实后再用。"
                if not outcome.hits
                else ""
            )
            + " ★ 系统不会自动发送任何内容 —— 请人工确认后自行使用（§十六）。"
        ),
    )


@router.post(
    "/proposals/{proposal_id}/confirm",
    response_model=ProposalDetail,
    summary="人工确认方案可用",
)
def proposals_confirm(
    proposal_id: int,
    payload: ProposalConfirmRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProposalDetail:
    row = _require(db, proposal_id)
    try:
        updated = confirm_proposal(db, row, confirmed_by=user.username)
    except ProposalError as exc:
        raise ApiFailure(ApiErrorCode.PROPOSAL_INVALID, str(exc))
    return _to_detail(db, updated)


@router.post(
    "/proposals/{proposal_id}/reject", response_model=ProposalDetail, summary="人工驳回方案"
)
def proposals_reject(
    proposal_id: int,
    payload: ProposalRejectRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProposalDetail:
    row = _require(db, proposal_id)
    try:
        updated = reject_proposal(db, row, confirmed_by=user.username, reason=payload.reason)
    except ProposalError as exc:
        raise ApiFailure(ApiErrorCode.PROPOSAL_INVALID, str(exc))
    return _to_detail(db, updated)


@router.delete("/proposals/{proposal_id}", response_model=ProposalItem, summary="移除方案")
def proposals_delete(proposal_id: int, db: Session = Depends(get_db)) -> ProposalItem:
    """软删除：保留记录，便于回溯曾经写过什么。"""
    row = _require(db, proposal_id)
    row.is_active = False
    db.commit()
    db.refresh(row)
    return _to_item(db, row)
