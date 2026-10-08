"""风险事件接口（TASK-009）。

    GET  /api/risks                 风险列表（可按客户 / 状态 / 等级筛选）
    GET  /api/risks/summary         风险中心汇总（全部 EvidenceValue）
    POST /api/risks/scan            扫描消息生成风险（幂等，可反复跑）
    POST /api/risks/{id}/resolve    处置风险（OPEN → RESOLVED / DISMISSED）

★ 路由顺序注意：/risks/summary 必须注册在 /risks/{risk_id} **之前**，
  否则 "summary" 会被当成 id 解析（customers.py 里踩过同样的坑）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.customer import Customer
from app.models.risk_event import RiskEvent, RiskEventType, RiskLevel, RiskStatus
from app.schemas.evidence import SourceType, build_evidence, no_data_evidence
from app.schemas.risks import (
    RiskEventItem,
    RiskListResponse,
    RiskResolveRequest,
    RiskScanResponse,
    RiskSummaryResponse,
)
from app.services.errors import ApiErrorCode, ApiFailure
from app.services.risk import get_risk, list_risks, resolve_risk, scan_all, scan_customer

router = APIRouter(tags=["risks"])

#: 风险类型 → 中文展示名
TYPE_LABELS: dict[RiskEventType, str] = {
    RiskEventType.POLICY_BLOCKED: "命中敏感策略",
    RiskEventType.AI_FAILED: "AI 回复失败",
    RiskEventType.MANUAL_FLAG: "人工标记",
}


def _to_item(risk: RiskEvent, customer_name: str) -> RiskEventItem:
    return RiskEventItem(
        id=risk.id,
        customer_id=risk.customer_id,
        customer_name=customer_name,
        event_type=risk.event_type,
        level=risk.level,
        status=risk.status,
        source_type=risk.source_type.value
        if hasattr(risk.source_type, "value")
        else str(risk.source_type),
        source_ref=risk.source_ref,
        trigger_text=risk.trigger_text,
        reason=risk.reason,
        evidence_ref=risk.evidence_ref,
        resolved_at=risk.resolved_at,
        resolved_note=risk.resolved_note,
        created_at=risk.created_at,
    )


def _ev_or_no_data(count: int, ref: str, empty_reason: str):
    if count:
        return build_evidence(count, source_type=SourceType.SYSTEM, evidence_ref=ref)
    return no_data_evidence(empty_reason, source_type=SourceType.SYSTEM, evidence_ref=ref)


@router.get("/risks/summary", response_model=RiskSummaryResponse, summary="风险中心汇总")
def risks_summary(db: Session = Depends(get_db)) -> RiskSummaryResponse:
    """风险汇总。★ 每个数字都是真实计数；一条都没有时是 NO_DATA（界面显示「—」）。"""

    def count(*conditions) -> int:
        stmt = select(func.count(RiskEvent.id))
        for c in conditions:
            stmt = stmt.where(c)
        return db.scalar(stmt) or 0

    total = count()
    open_total = count(RiskEvent.status == RiskStatus.OPEN)
    high_open = count(RiskEvent.status == RiskStatus.OPEN, RiskEvent.level == RiskLevel.HIGH)
    attention_open = count(
        RiskEvent.status == RiskStatus.OPEN, RiskEvent.level == RiskLevel.ATTENTION
    )
    resolved_total = count(RiskEvent.status != RiskStatus.OPEN)

    rows = db.execute(
        select(RiskEvent.event_type, func.count(RiskEvent.id)).group_by(RiskEvent.event_type)
    ).all()
    by_type = [
        {"event_type": et.value, "label": TYPE_LABELS.get(et, et.value), "count": int(n)}
        for et, n in rows
        if n
    ]

    return RiskSummaryResponse(
        total=_ev_or_no_data(total, "risks:total", "还没有风险记录"),
        open_total=_ev_or_no_data(open_total, "risks:open", "没有待处理的风险"),
        high_open=_ev_or_no_data(high_open, "risks:open:high", "没有高风险待处理"),
        attention_open=_ev_or_no_data(
            attention_open, "risks:open:attention", "没有需关注的风险"
        ),
        resolved_total=_ev_or_no_data(resolved_total, "risks:resolved", "还没有处置过风险"),
        by_type=by_type,
        note=(
            "风险只来自两条可复现依据：消息命中策略闸门（D7）、AI 回复失败（D10）。"
            "等级由类别决定（资金/不可逆类 = 高风险），不是模型打分。"
        ),
    )


@router.post("/risks/scan", response_model=RiskScanResponse, summary="扫描消息生成风险（幂等）")
def risks_scan(
    customer_id: int | None = Query(default=None, description="只扫这个客户；不传=扫全部"),
    db: Session = Depends(get_db),
) -> RiskScanResponse:
    """扫描并把命中项落成风险。★ 幂等：同一来源只留一条，可反复跑。"""
    if customer_id is not None:
        try:
            result = scan_customer(db, customer_id)
        except LookupError:
            # 与 messaging.require_customer 同一惯例：客户不存在用 MESSAGE_NOT_FOUND + 404
            raise ApiFailure(
                ApiErrorCode.MESSAGE_NOT_FOUND,
                f"客户 {customer_id} 不存在",
                http_status=404,
            )
        scope = f"客户 {customer_id}"
    else:
        result = scan_all(db)
        scope = "全部客户"

    return RiskScanResponse(
        scanned_messages=result.scanned_messages,
        created=result.created,
        skipped_existing=result.skipped_existing,
        note=f"已扫描 {scope} 的 {result.scanned_messages} 条消息；"
        f"新增 {result.created} 条风险，跳过 {result.skipped_existing} 条已存在。",
    )


@router.get("/risks", response_model=RiskListResponse, summary="风险列表（新的在前）")
def risks_list(
    customer_id: int | None = Query(default=None),
    status: RiskStatus | None = Query(default=None),
    level: RiskLevel | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> RiskListResponse:
    rows, total = list_risks(
        db,
        customer_id=customer_id,
        status=status,
        level=level,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    names = {
        cid: name
        for cid, name in db.execute(
            select(Customer.id, Customer.name).where(
                Customer.id.in_([r.customer_id for r in rows] or [0])
            )
        ).all()
    }
    return RiskListResponse(
        items=[_to_item(r, names.get(r.customer_id, f"#{r.customer_id}")) for r in rows],
        page=page,
        page_size=page_size,
        total=_ev_or_no_data(total, "risks:list:total", "当前筛选下没有风险"),
    )


@router.post("/risks/{risk_id}/resolve", response_model=RiskEventItem, summary="处置风险")
def risks_resolve(
    risk_id: int, payload: RiskResolveRequest, db: Session = Depends(get_db)
) -> RiskEventItem:
    """处置风险。★ 只做状态流转，不引工作流引擎。"""
    risk = get_risk(db, risk_id)
    if risk is None:
        raise ApiFailure(ApiErrorCode.RISK_NOT_FOUND, f"风险 {risk_id} 不存在", http_status=404)
    updated = resolve_risk(db, risk_id, status=payload.status, note=payload.note)
    name = db.scalar(select(Customer.name).where(Customer.id == updated.customer_id)) or ""
    return _to_item(updated, name)
