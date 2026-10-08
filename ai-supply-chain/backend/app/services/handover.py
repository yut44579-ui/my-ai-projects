"""人工接管三态状态机（D8）—— TASK-005 核心，只做后端。

★ 不做工作流引擎：三个状态、五条合法迁移，全部写死在本文件里。
★ 路由层只做 HTTP 出入参转换，判定逻辑全在这里（可被单测直接调用）。

════════════════════════════════════════════════════════════════════════
★ 给 TASK-004（AI 策略闸门）的接线口 —— 整合时由主流程接线
════════════════════════════════════════════════════════════════════════

    from app.services.handover import set_human_required, ai_auto_reply_allowed

    # 闸门判定命中敏感词时调用（在调 LLM **之前**）：
    set_human_required(db, customer_id, reason="命中敏感：报价", rule="QUOTE_PROMISE",
                       matched="已经给你 8 折")

    # 调用 LLM 前的前置判断（AC3：接管期间敏感内容仍不自动回复）：
    if not ai_auto_reply_allowed(customer.handover_state):
        ...  # 直接返回「AI 不会自动回复敏感内容，请人工处理」，不调 LLM

`set_human_required` 是幂等的：已经是 HUMAN_REQUIRED / HUMAN_ACTIVE 时不再改状态、
不再写事件，只把结果原样返回（`changed=False`）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.handover import (
    CustomerHandoverEvent,
    HandoverActorType,
    HandoverEventType,
    HandoverState,
)
from app.schemas.evidence import SourceType, build_evidence

# ── 合法迁移表（D8）。同一状态 -> 同一状态 不是迁移，当成 no-op 处理（不写事件）。 ──
TRANSITIONS: dict[HandoverState, frozenset[HandoverState]] = {
    HandoverState.AUTO: frozenset(
        {
            HandoverState.HUMAN_REQUIRED,  # 闸门判定敏感 → 转人工
            HandoverState.HUMAN_ACTIVE,    # 人工主动接管（还没触发过也允许）
        }
    ),
    HandoverState.HUMAN_REQUIRED: frozenset(
        {
            HandoverState.HUMAN_ACTIVE,    # 人工接管
            HandoverState.AUTO,            # 人工看过后判定无需处理 → 交回 AI
        }
    ),
    HandoverState.HUMAN_ACTIVE: frozenset(
        {
            HandoverState.AUTO,            # 处理完 → 交回 AI
        }
    ),
}


def ai_auto_reply_allowed(state: HandoverState) -> bool:
    """AI 是否允许自动回复。★ 只有 AUTO 允许 —— HUMAN_REQUIRED / HUMAN_ACTIVE 一律不自动回复。

    这是 TASK-004 闸门与前端共用的唯一判据（AC3）：别在各处自己写
    `state != AUTO`，统一走这个函数，语义才不会走样。
    """
    return state is HandoverState.AUTO


class HandoverError(Exception):
    """接管操作的业务失败。路由层据此返回 {"error": code, "message": ...}。"""

    def __init__(self, code: str, message: str, *, http_status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status

    def to_body(self) -> dict:
        return {"error": self.code, "message": self.message}


CUSTOMER_NOT_FOUND = "customer_not_found"
INVALID_TRANSITION = "invalid_transition"
AI_ACTOR_FORBIDDEN = "ai_actor_forbidden"


@dataclass
class HandoverChange:
    """一次接管操作的结果。"""

    customer_id: int
    state: HandoverState
    changed: bool
    message: str
    event: CustomerHandoverEvent | None = None


def _get_customer(db: Session, customer_id: int) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HandoverError(
            CUSTOMER_NOT_FOUND, f"客户 {customer_id} 不存在", http_status=404
        )
    return customer


def _reject_ai_actor(actor_type: HandoverActorType) -> None:
    """★ AI 不许自行改客户接管状态（D7/D8）。带 AI 身份的请求一律 403，且不产生任何事件。"""
    if actor_type is HandoverActorType.AI:
        raise HandoverError(
            AI_ACTOR_FORBIDDEN,
            "AI 不允许修改客户接管状态，只能由人工发起（见 D8）",
            http_status=403,
        )


def _apply(
    db: Session,
    customer: Customer,
    *,
    to_state: HandoverState,
    event_type: HandoverEventType,
    actor_type: HandoverActorType,
    source_type: SourceType,
    reason: str | None,
    metadata: dict | None,
) -> HandoverChange:
    """状态机唯一入口：校验迁移 → 改状态 → 写事件（同值则 no-op，不写事件）。"""
    from_state = customer.handover_state

    if from_state == to_state:
        # AC5 反面：状态没变化就不写事件（历史只记真实变化，不灌水）
        return HandoverChange(
            customer_id=customer.id,
            state=from_state,
            changed=False,
            message=f"客户 {customer.id} 已是 {from_state.value}，无需变更（未写事件）",
        )

    if to_state not in TRANSITIONS.get(from_state, frozenset()):
        allowed = " / ".join(sorted(s.value for s in TRANSITIONS.get(from_state, frozenset())))
        raise HandoverError(
            INVALID_TRANSITION,
            f"非法状态迁移：{from_state.value} → {to_state.value}（只能到 {allowed or '无'}）",
        )

    customer.handover_state = to_state

    event = CustomerHandoverEvent(
        customer_id=customer.id,
        event_type=event_type,
        from_state=from_state,
        to_state=to_state,
        actor_type=actor_type,
        source_type=source_type,
        reason=reason,
        metadata_json=metadata,
        evidence_ref="",  # 拿到 id 后立刻用代码生成（禁止手填）
    )
    db.add(event)
    db.flush()  # 需要 id 才能生成 evidence_ref
    event.evidence_ref = f"customer_handover_event:{event.id}"
    db.commit()
    db.refresh(event)

    return HandoverChange(
        customer_id=customer.id,
        state=to_state,
        changed=True,
        message=f"客户 {customer.id}：{from_state.value} → {to_state.value}",
        event=event,
    )


# ──────────────────────────────────────────────────────────────────────────
# 对外方法
# ──────────────────────────────────────────────────────────────────────────


def set_human_required(
    db: Session,
    customer_id: int,
    reason: str,
    *,
    rule: str | None = None,
    matched: str | None = None,
) -> HandoverChange:
    """★ TASK-004 闸门的接线口：判定命中敏感 → 转人工（AUTO → HUMAN_REQUIRED）。

    幂等：已经是 HUMAN_REQUIRED / HUMAN_ACTIVE 时不再改状态、不再写事件。
    发起方固定为 SYSTEM（这是代码层判定，不是人工操作，也不是 AI 自作主张）。

    reason  —— 人话原因，必填（写进事件，供人工判断）
    rule    —— 命中的规则名（如 QUOTE_PROMISE），可选，落 metadata_json
    matched —— 命中的原文片段（如「已经给你 8 折」），可选，落 metadata_json
    """
    customer = _get_customer(db, customer_id)

    # 已经转人工 / 人工处理中 → 无需再升级（人工已在管，闸门再喊也没用）。
    # ★ 这一条不能靠 _apply 的"同值 no-op"兜住：HUMAN_ACTIVE 时目标态 HUMAN_REQUIRED
    #   是不同的状态，会被判成非法迁移。语义上它本来就该是幂等的。
    if customer.handover_state is not HandoverState.AUTO:
        return HandoverChange(
            customer_id=customer.id,
            state=customer.handover_state,
            changed=False,
            message=(
                f"客户 {customer.id} 已处于 {customer.handover_state.value}"
                "（人工处理中），无需再转人工（未写事件）"
            ),
        )

    metadata = {k: v for k, v in (("rule", rule), ("matched", matched)) if v is not None}
    return _apply(
        db,
        customer,
        to_state=HandoverState.HUMAN_REQUIRED,
        event_type=HandoverEventType.HUMAN_REQUIRED,
        actor_type=HandoverActorType.SYSTEM,
        source_type=SourceType.SYSTEM,
        reason=reason,
        metadata=metadata or None,
    )


def takeover(
    db: Session,
    customer_id: int,
    *,
    actor_type: HandoverActorType = HandoverActorType.HUMAN,
    note: str | None = None,
) -> HandoverChange:
    """人工接管：AUTO / HUMAN_REQUIRED → HUMAN_ACTIVE（写事件 actor_type=HUMAN）。"""
    _reject_ai_actor(actor_type)
    customer = _get_customer(db, customer_id)
    return _apply(
        db,
        customer,
        to_state=HandoverState.HUMAN_ACTIVE,
        event_type=HandoverEventType.TAKEN_OVER,
        actor_type=actor_type,
        source_type=SourceType.MANUAL,
        reason=note,
        metadata=None,
    )


def resume(
    db: Session,
    customer_id: int,
    *,
    actor_type: HandoverActorType = HandoverActorType.HUMAN,
    note: str | None = None,
) -> HandoverChange:
    """交回 AI：HUMAN_REQUIRED / HUMAN_ACTIVE → AUTO（写事件）。"""
    _reject_ai_actor(actor_type)
    customer = _get_customer(db, customer_id)
    return _apply(
        db,
        customer,
        to_state=HandoverState.AUTO,
        event_type=HandoverEventType.RESUMED,
        actor_type=actor_type,
        source_type=SourceType.MANUAL,
        reason=note,
        metadata=None,
    )


def list_events(db: Session, customer_id: int) -> list[CustomerHandoverEvent]:
    """某客户的接管变更历史，按时间**正序**（先发生在前，便于看流转过程）。"""
    return list(
        db.execute(
            select(CustomerHandoverEvent)
            .where(CustomerHandoverEvent.customer_id == customer_id)
            .order_by(
                CustomerHandoverEvent.created_at.asc(),
                CustomerHandoverEvent.id.asc(),
            )
        )
        .scalars()
        .all()
    )


def event_count_evidence(db: Session, customer_id: int):
    """事件条数是业务数字 → 一律走 EvidenceValue（AC5）。

    ★ 没有事件 → NO_DATA（value=None），不是 0 —— 0 会让人误以为「有过零次变更」，
       而事实是「从未被接管/交回过」。两者的界面表现必须能区分。
    """
    ref = f"customer:{customer_id}:handover_events"
    events = list_events(db, customer_id)
    return build_evidence(
        len(events) if events else None,
        source_type=SourceType.SYSTEM,
        evidence_ref=ref,
    )


def get_handover(db: Session, customer_id: int) -> dict:
    """当前状态 + 变更历史（GET /customers/{id}/handover 的数据源）。"""
    customer = _get_customer(db, customer_id)
    state = customer.handover_state
    events = list_events(db, customer_id)
    return {
        "customer_id": customer.id,
        "state": state,
        "ai_auto_reply_allowed": ai_auto_reply_allowed(state),
        "events": events,
        "event_count": event_count_evidence(db, customer_id),
        "since": events[-1].created_at if events else None,
        "updated_at": customer.updated_at,
    }


__all__ = [
    "AI_ACTOR_FORBIDDEN",
    "CUSTOMER_NOT_FOUND",
    "INVALID_TRANSITION",
    "TRANSITIONS",
    "HandoverChange",
    "HandoverError",
    "ai_auto_reply_allowed",
    "event_count_evidence",
    "get_handover",
    "list_events",
    "resume",
    "set_human_required",
    "takeover",
]
