"""线索研究接口（TASK-025，需求 §十七 获客与营销）。

    GET    /api/researches                       研究列表
    GET    /api/researches/summary               概览
    POST   /api/researches                       新建研究（提供公开资料**原文**）
    GET    /api/researches/{id}                  详情（含原文）
    POST   /api/researches/{id}/extract          AI 抽取事实与推断（★ 引用会被回验）
    POST   /api/researches/{id}/drafts           基于已通过验证的事实生成话术
    POST   /api/researches/{id}/confirm          人工确认话术可用
    POST   /api/researches/{id}/reject           人工驳回话术

★ 路由顺序：/summary 必须在 /{research_id} 之前（多处已踩过同一个坑）。
★ 本模块**没有任何发送能力**：confirm 只是标记"这条话术可用"，
  触达由人工自己去做（§十六：AI建议 ≠ 自动发送）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.customer import Customer
from app.models.prospect_research import (
    DraftStatus,
    ProspectResearch,
    ResearchSourceType,
)
from app.models.user import User
from app.schemas.evidence import SourceType, build_evidence, no_data_evidence
from app.schemas.prospect_research import (
    ConfirmRequest,
    DraftRequest,
    DraftResponse,
    ExtractRequest,
    ExtractResponse,
    ProspectResearchDetail,
    ProspectResearchItem,
    RejectRequest,
    ResearchCreateRequest,
    ResearchDraft,
    ResearchFact,
    ResearchInference,
    ResearchListResponse,
    ResearchSummaryResponse,
)
from app.services.errors import ApiErrorCode, ApiFailure
from app.services.prospect_research import (
    ResearchError,
    confirm_draft,
    create_research,
    extract_facts,
    generate_drafts,
    get_research,
    list_research,
    reject_draft,
    stage_counts,
)

router = APIRouter(tags=["prospect-research"])

SOURCE_LABELS: dict[ResearchSourceType, str] = {
    ResearchSourceType.COMPANY_SITE: "公司官网",
    ResearchSourceType.NEWS: "新闻",
    ResearchSourceType.JOB_POSTING: "招聘信息",
    ResearchSourceType.INDUSTRY_SITE: "行业网站",
    ResearchSourceType.PRODUCT_INFO: "公开产品信息",
    ResearchSourceType.OTHER: "其他",
}

DRAFT_STATUS_LABELS: dict[DraftStatus, str] = {
    DraftStatus.NONE: "未生成",
    DraftStatus.GENERATED: "待人工确认",
    DraftStatus.CONFIRMED: "已确认",
    DraftStatus.REJECTED: "已驳回",
}


def _base(row: ProspectResearch, customer_name: str | None = None) -> dict:
    return dict(
        id=row.id,
        target_type=row.target_type,
        target_name=row.target_name,
        customer_id=row.customer_id,
        customer_name=customer_name,
        source_type=row.source_type,
        source_type_label=SOURCE_LABELS.get(row.source_type, row.source_type.value),
        source_url=row.source_url,
        source_length=len(row.source_text or ""),
        # ★ TASK-042：研究要点（要查证的问题）。与 source_text（证据）分开返回，
        #   界面上也分开显示 —— 不能让人把"AI 起草的提问"看成"已核实的资料"。
        focus=row.focus,
        # ★ facts 与 inferences 分开返回，永不混放
        facts=[ResearchFact(**f) for f in (row.facts_json or [])],
        inferences=[ResearchInference(**i) for i in (row.inferences_json or [])],
        summary=row.summary,
        draft_status=row.draft_status,
        draft_status_label=DRAFT_STATUS_LABELS.get(row.draft_status, row.draft_status.value),
        drafts=[ResearchDraft(**d) for d in (row.drafts_json or [])],
        confirmed_by=row.confirmed_by,
        confirmed_at=row.confirmed_at,
        reject_reason=row.reject_reason,
        llm_called=row.llm_called,
        llm_error=row.llm_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _customer_names(db: Session, rows: list[ProspectResearch]) -> dict[int, str]:
    ids = [r.customer_id for r in rows if r.customer_id]
    if not ids:
        return {}
    return {
        cid: name
        for cid, name in db.execute(
            select(Customer.id, Customer.name).where(Customer.id.in_(ids))
        ).all()
    }


def _to_item(db: Session, row: ProspectResearch) -> ProspectResearchItem:
    names = _customer_names(db, [row])
    return ProspectResearchItem(**_base(row, names.get(row.customer_id or 0)))


def _to_detail(db: Session, row: ProspectResearch) -> ProspectResearchDetail:
    names = _customer_names(db, [row])
    return ProspectResearchDetail(
        **_base(row, names.get(row.customer_id or 0)), source_text=row.source_text
    )


def _require(db: Session, research_id: int) -> ProspectResearch:
    row = get_research(db, research_id)
    if row is None:
        raise ApiFailure(
            ApiErrorCode.RESEARCH_NOT_FOUND, f"研究 {research_id} 不存在", http_status=404
        )
    return row


@router.get("/researches/summary", response_model=ResearchSummaryResponse, summary="获客研究概览")
def researches_summary(db: Session = Depends(get_db)) -> ResearchSummaryResponse:
    counts = stage_counts(db)
    total = sum(counts.values())

    def ev_or_nodata(n: int, ref: str, empty: str):
        if total:
            return build_evidence(n, source_type=SourceType.MANUAL, evidence_ref=ref)
        return no_data_evidence(empty, source_type=SourceType.MANUAL, evidence_ref=ref)

    return ResearchSummaryResponse(
        total=ev_or_nodata(total, "researches:count", "还没有做任何线索研究"),
        pending_confirm_total=ev_or_nodata(
            counts["GENERATED"], "researches:pending_confirm", "没有待确认的话术"
        ),
        confirmed_total=ev_or_nodata(
            counts["CONFIRMED"], "researches:confirmed", "还没有确认过话术"
        ),
        rejected_total=ev_or_nodata(
            counts["REJECTED"], "researches:rejected", "没有驳回记录"
        ),
        note=(
            "资料由人工提供原文，系统不联网抓取；AI 只能从原文抽取事实，"
            "每条事实的引用会被逐条回验（原文里找不到的一律剔除）。"
            "生成的话术必须人工确认后才可用 —— 本系统不发送任何内容。"
        ),
    )


@router.get("/researches", response_model=ResearchListResponse, summary="研究列表")
def researches_list(
    draft_status: DraftStatus | None = Query(default=None),
    customer_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> ResearchListResponse:
    rows, total = list_research(
        db,
        draft_status=draft_status,
        customer_id=customer_id,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    names = _customer_names(db, rows)
    total_ev = (
        build_evidence(total, source_type=SourceType.MANUAL, evidence_ref="researches:list:total")
        if total
        else no_data_evidence(
            "当前筛选下没有研究记录",
            source_type=SourceType.MANUAL,
            evidence_ref="researches:list:total",
        )
    )
    return ResearchListResponse(
        items=[
            ProspectResearchItem(**_base(r, names.get(r.customer_id or 0))) for r in rows
        ],
        page=page,
        page_size=page_size,
        total=total_ev,
    )


@router.post(
    "/researches",
    response_model=ProspectResearchDetail,
    status_code=201,
    summary="新建线索研究（提供公开资料原文）",
)
def researches_create(
    payload: ResearchCreateRequest, db: Session = Depends(get_db)
) -> ProspectResearchDetail:
    """★ 必须提供**原文**：没有原文就无法验证抽取出的事实，防幻觉就没有依据。"""
    try:
        row = create_research(
            db,
            target_type=payload.target_type,
            target_name=payload.target_name,
            source_type=payload.source_type,
            source_text=payload.source_text,
            source_url=payload.source_url,
        focus=payload.focus,
            customer_id=payload.customer_id,
        )
    except ResearchError as exc:
        raise ApiFailure(ApiErrorCode.RESEARCH_INVALID, str(exc))
    return _to_detail(db, row)


@router.get("/researches/{research_id}", response_model=ProspectResearchDetail, summary="研究详情")
def researches_detail(research_id: int, db: Session = Depends(get_db)) -> ProspectResearchDetail:
    return _to_detail(db, _require(db, research_id))


@router.post(
    "/researches/{research_id}/extract",
    response_model=ExtractResponse,
    summary="AI 抽取事实与推断（引用逐条回验）",
)
def researches_extract(
    research_id: int, payload: ExtractRequest, db: Session = Depends(get_db)
) -> ExtractResponse:
    """从原文抽取〔公开事实〕与〔AI 推断〕。

    ★ 防幻觉机制：每条事实的 `quote` 必须能在原文里逐字找到，找不到的一律剔除，
      并在 `rejected_count` 里如实报告剔除了几条。
    """
    row = _require(db, research_id)
    try:
        outcome = extract_facts(db, row, question=payload.question)
    except ResearchError as exc:
        raise ApiFailure(ApiErrorCode.RESEARCH_INVALID, str(exc))
    db.refresh(row)

    if outcome.llm_error:
        raise ApiFailure(
            ApiErrorCode.LLM_UNAVAILABLE,
            f"AI 暂时不可用，无法抽取资料（原因：{outcome.llm_error}）",
            http_status=502,
        )

    return ExtractResponse(
        research=_to_detail(db, row),
        facts_count=len(outcome.facts),
        inferences_count=len(outcome.inferences),
        rejected_count=len(outcome.rejected_quotes),
        note=(
            f"已抽取 {len(outcome.facts)} 条公开事实、{len(outcome.inferences)} 条 AI 推断。"
            + (
                f"★ 有 {len(outcome.rejected_quotes)} 条因「引用在原文里找不到」被剔除，"
                "这类条目不会展示给你（很可能是模型编的）。"
                if outcome.rejected_quotes
                else "所有事实的引用都已在原文中逐字核对通过。"
            )
        ),
    )


@router.post(
    "/researches/{research_id}/drafts",
    response_model=DraftResponse,
    summary="基于已验证的事实生成话术候选（待人工确认）",
)
def researches_drafts(
    research_id: int,
    payload: DraftRequest,
    db: Session = Depends(get_db),
) -> DraftResponse:
    """生成话术。★ 生成后状态是「待人工确认」，**本系统不发送任何内容**。"""
    row = _require(db, research_id)
    try:
        drafts, llm_error = generate_drafts(db, row)
    except ResearchError as exc:
        raise ApiFailure(ApiErrorCode.RESEARCH_INVALID, str(exc))

    if llm_error:
        raise ApiFailure(
            ApiErrorCode.LLM_UNAVAILABLE,
            f"AI 暂时不可用，无法生成话术（原因：{llm_error}）",
            http_status=502,
        )

    db.refresh(row)
    return DraftResponse(
        research=_to_detail(db, row),
        drafts_count=len(drafts),
        note=(
            f"已生成 {len(drafts)} 条话术候选，状态为「待人工确认」。"
            "★ 系统不会自动发送任何内容 —— 请人工确认后自行触达（§十六：AI建议≠自动发送）。"
        ),
    )


@router.post(
    "/researches/{research_id}/confirm",
    response_model=ProspectResearchDetail,
    summary="人工确认话术可用",
)
def researches_confirm(
    research_id: int,
    payload: ConfirmRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProspectResearchDetail:
    """人工确认。★ 只能由人调用（需要登录），不存在"AI 自己确认"的路径。"""
    row = _require(db, research_id)
    try:
        updated = confirm_draft(
            db,
            row,
            confirmed_by=user.username,
            accepted_indexes=payload.accepted_indexes,
        )
    except ResearchError as exc:
        raise ApiFailure(ApiErrorCode.RESEARCH_INVALID, str(exc))
    return _to_detail(db, updated)


@router.post(
    "/researches/{research_id}/reject",
    response_model=ProspectResearchDetail,
    summary="人工驳回话术",
)
def researches_reject(
    research_id: int,
    payload: RejectRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProspectResearchDetail:
    row = _require(db, research_id)
    try:
        updated = reject_draft(
            db, row, confirmed_by=user.username, reason=payload.reason
        )
    except ResearchError as exc:
        raise ApiFailure(ApiErrorCode.RESEARCH_INVALID, str(exc))
    return _to_detail(db, updated)
