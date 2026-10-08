"""客户访问追踪接口（TASK-010）。

    POST /api/customers/{id}/visits    记录一次访问（只记可识别到客户的访问）
    GET  /api/visits                   访问列表（可按客户 / 渠道筛选）
    GET  /api/visits/summary           访问概览（全部 EvidenceValue）

★ 路由顺序：/visits/summary 必须在 /visits/{id} 之前（本模块没有 /visits/{id}，
  但仍保持与 risks / customers 一致的写法，避免将来加详情页时踩坑）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.customer import Customer
from app.schemas.evidence import SourceType, build_evidence, no_data_evidence
from app.schemas.visits import (
    VisitCreateRequest,
    VisitCreateResponse,
    VisitListResponse,
    VisitSummaryResponse,
)
from app.services.messaging import require_customer
from app.services.visits import list_visits, record_visit, visits_summary

router = APIRouter(tags=["visits"])


@router.post(
    "/customers/{customer_id}/visits",
    response_model=VisitCreateResponse,
    summary="记录一次客户访问（写 VISIT 事件）",
)
def create_visit(
    customer_id: int, payload: VisitCreateRequest, db: Session = Depends(get_db)
) -> VisitCreateResponse:
    """记录访问。★ 必须指定客户：匿名访问不落库（无法归属、无法驱动跟进）。"""
    customer = require_customer(db, customer_id)
    event = record_visit(
        db,
        customer,
        page=payload.page,
        channel=payload.channel,
        referrer=payload.referrer,
        note=payload.note,
    )
    from app.services.visits import to_item

    return VisitCreateResponse(
        customer_id=customer_id,
        visit=to_item(event, customer.name),
        next_handover_state=customer.handover_state.value
        if hasattr(customer.handover_state, "value")
        else str(customer.handover_state),
    )


@router.get("/visits/summary", response_model=VisitSummaryResponse, summary="访问概览")
def get_visits_summary(db: Session = Depends(get_db)) -> VisitSummaryResponse:
    """访问概览。★ 零访问时各数字是 NO_DATA（界面显示「—」），不是 0。"""
    return visits_summary(db)


@router.get("/visits", response_model=VisitListResponse, summary="访问列表（新的在前）")
def get_visits(
    customer_id: int | None = Query(default=None),
    channel: str | None = Query(default=None, description="渠道代码，如 WEBSITE"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> VisitListResponse:
    items, total = list_visits(
        db,
        customer_id=customer_id,
        channel=channel,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    if total:
        total_ev = build_evidence(
            total, source_type=SourceType.SYSTEM, evidence_ref="visits:list:total"
        )
    else:
        total_ev = no_data_evidence(
            "当前筛选下没有访问记录",
            source_type=SourceType.SYSTEM,
            evidence_ref="visits:list:total",
        )
    return VisitListResponse(items=items, page=page, page_size=page_size, total=total_ev)
