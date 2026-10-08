"""人工接管接口（TASK-005 §TASK-005）。

    POST /api/customers/{id}/takeover   人工接管 → HUMAN_ACTIVE（写事件 actor_type=HUMAN）
    POST /api/customers/{id}/resume     交回 AI  → AUTO（写事件）
    GET  /api/customers/{id}/handover   当前状态 + 变更历史

路由层只做 HTTP 出入参转换，状态机判定全在 services/handover.py（可被单测直接调用）。
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.handover import (
    HandoverActionRequest,
    HandoverActionResponse,
    HandoverEventItem,
    HandoverHistoryResponse,
)
from app.services.handover import (
    HandoverChange,
    ai_auto_reply_allowed,
    get_handover,
    resume,
    takeover,
)

router = APIRouter(tags=["handover"])


def _event_item(event) -> HandoverEventItem | None:
    return HandoverEventItem.model_validate(event) if event is not None else None


def _action_response(change: HandoverChange) -> HandoverActionResponse:
    return HandoverActionResponse(
        customer_id=change.customer_id,
        state=change.state,
        ai_auto_reply_allowed=ai_auto_reply_allowed(change.state),
        changed=change.changed,
        message=change.message,
        event=_event_item(change.event),
    )


@router.post(
    "/customers/{customer_id}/takeover",
    response_model=HandoverActionResponse,
    summary="人工接管（→ HUMAN_ACTIVE，写事件）",
)
def take_over(
    customer_id: int,
    payload: HandoverActionRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> HandoverActionResponse:
    """人工接管客户。已经处理中时返回 changed=false（幂等，不重复写事件）。"""
    req = payload or HandoverActionRequest()
    change = takeover(db, customer_id, actor_type=req.actor_type, note=req.note)
    return _action_response(change)


@router.post(
    "/customers/{customer_id}/resume",
    response_model=HandoverActionResponse,
    summary="交回 AI（→ AUTO，写事件）",
)
def resume_ai(
    customer_id: int,
    payload: HandoverActionRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> HandoverActionResponse:
    """交回 AI 自动回复。本来就处于 AUTO 时返回 changed=false（幂等）。"""
    req = payload or HandoverActionRequest()
    change = resume(db, customer_id, actor_type=req.actor_type, note=req.note)
    return _action_response(change)


@router.get(
    "/customers/{customer_id}/handover",
    response_model=HandoverHistoryResponse,
    summary="当前接管状态 + 变更历史",
)
def read_handover(
    customer_id: int, db: Session = Depends(get_db)
) -> HandoverHistoryResponse:
    """当前状态 + 完整流转历史（AC5：不许只有当前值没有历史）。"""
    data = get_handover(db, customer_id)
    return HandoverHistoryResponse(
        customer_id=data["customer_id"],
        state=data["state"],
        ai_auto_reply_allowed=data["ai_auto_reply_allowed"],
        events=[HandoverEventItem.model_validate(e) for e in data["events"]],
        event_count=data["event_count"],
        since=data["since"],
        updated_at=data["updated_at"],
    )
