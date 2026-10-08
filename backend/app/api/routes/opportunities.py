"""商机接口（TASK-016）。

    GET    /api/opportunities                 列表（可按客户 / 阶段 / 只看进行中）
    GET    /api/opportunities/board           看板（按阶段分列）
    POST   /api/opportunities                 新建
    GET    /api/opportunities/{id}            详情
    PATCH  /api/opportunities/{id}            更新（不含阶段）
    POST   /api/opportunities/{id}/stage      推进 / 关闭阶段（校验流转合法性）

★ 路由顺序：/board 必须注册在 /{opportunity_id} 之前，否则 "board" 会被当成 id 解析
  （customers.py 的 /stats、reports.py 的 /drilldown 都踩过同一个坑）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.customer import Customer
from app.models.opportunity import Opportunity, OpportunityStage
from app.schemas.evidence import SourceType, build_evidence, no_data_evidence
from app.schemas.opportunities import (
    OpportunityBoardResponse,
    OpportunityCreateRequest,
    OpportunityItem,
    OpportunityListResponse,
    OpportunityStageRequest,
    OpportunityStageResponse,
    OpportunityUpdateRequest,
)
from app.services.errors import ApiErrorCode, ApiFailure
from app.services.opportunity import (
    StageTransitionError,
    build_board,
    change_stage,
    create_opportunity,
    get_opportunity,
    list_opportunities,
    to_item,
    update_opportunity,
)

router = APIRouter(tags=["opportunities"])


def _customer_name(db: Session, customer_id: int) -> str:
    return db.scalar(select(Customer.name).where(Customer.id == customer_id)) or f"#{customer_id}"


def _require(db: Session, opportunity_id: int) -> Opportunity:
    op = get_opportunity(db, opportunity_id)
    if op is None:
        raise ApiFailure(
            ApiErrorCode.OPPORTUNITY_NOT_FOUND,
            f"商机 {opportunity_id} 不存在",
            http_status=404,
        )
    return op


@router.get("/opportunities/board", response_model=OpportunityBoardResponse, summary="商机看板（按阶段分列）")
def opportunity_board(
    per_stage_limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> OpportunityBoardResponse:
    """看板。★ 金额合计是 EvidenceValue；一笔都没估金额时是 NO_DATA（不是 0）。"""
    return OpportunityBoardResponse(**build_board(db, per_stage_limit=per_stage_limit))


@router.get("/opportunities", response_model=OpportunityListResponse, summary="商机列表")
def opportunities_list(
    customer_id: int | None = Query(default=None),
    stage: OpportunityStage | None = Query(default=None),
    only_open: bool = Query(default=False, description="只看进行中（排除赢单/丢单）"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> OpportunityListResponse:
    items, total = list_opportunities(
        db,
        customer_id=customer_id,
        stage=stage,
        only_open=only_open,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    total_ev = (
        build_evidence(total, source_type=SourceType.SYSTEM, evidence_ref="opportunities:list:total")
        if total
        else no_data_evidence(
            "当前筛选下没有商机",
            source_type=SourceType.SYSTEM,
            evidence_ref="opportunities:list:total",
        )
    )
    return OpportunityListResponse(items=items, page=page, page_size=page_size, total=total_ev)


@router.post(
    "/opportunities",
    response_model=OpportunityItem,
    status_code=201,
    summary="新建商机",
)
def opportunities_create(
    payload: OpportunityCreateRequest, db: Session = Depends(get_db)
) -> OpportunityItem:
    """新建商机。★ 必须挂在真实客户上（不存在的客户 → 404）。"""
    try:
        op = create_opportunity(
            db,
            customer_id=payload.customer_id,
            title=payload.title,
            stage=payload.stage,
            priority=payload.priority,
            amount=payload.amount,
            currency=payload.currency,
            probability=payload.probability,
            expected_close_date=payload.expected_close_date,
            owner=payload.owner,
            note=payload.note,
        )
    except LookupError:
        raise ApiFailure(
            ApiErrorCode.MESSAGE_NOT_FOUND,
            f"客户 {payload.customer_id} 不存在",
            http_status=404,
        )
    return to_item(op, _customer_name(db, op.customer_id))


@router.get("/opportunities/{opportunity_id}", response_model=OpportunityItem, summary="商机详情")
def opportunities_detail(opportunity_id: int, db: Session = Depends(get_db)) -> OpportunityItem:
    op = _require(db, opportunity_id)
    return to_item(op, _customer_name(db, op.customer_id))


@router.patch("/opportunities/{opportunity_id}", response_model=OpportunityItem, summary="更新商机")
def opportunities_update(
    opportunity_id: int, payload: OpportunityUpdateRequest, db: Session = Depends(get_db)
) -> OpportunityItem:
    """更新商机字段。★ 阶段请走 /stage，以便校验流转合法性。"""
    op = _require(db, opportunity_id)
    updated = update_opportunity(db, op, **payload.model_dump(exclude_unset=True))
    return to_item(updated, _customer_name(db, updated.customer_id))


@router.post(
    "/opportunities/{opportunity_id}/stage",
    response_model=OpportunityStageResponse,
    summary="推进 / 关闭商机阶段",
)
def opportunities_stage(
    opportunity_id: int, payload: OpportunityStageRequest, db: Session = Depends(get_db)
) -> OpportunityStageResponse:
    """阶段流转。★ 非法流转 → 400；目标阶段等于当前阶段 → changed=false（不报错）。"""
    op = _require(db, opportunity_id)
    try:
        updated, changed, msg = change_stage(
            db, op, target=payload.stage, won_amount=payload.won_amount, note=payload.note
        )
    except StageTransitionError as exc:
        raise ApiFailure(ApiErrorCode.INVALID_STAGE_TRANSITION, exc.reason, http_status=400)
    return OpportunityStageResponse(
        opportunity=to_item(updated, _customer_name(db, updated.customer_id)),
        changed=changed,
        message=msg,
    )
