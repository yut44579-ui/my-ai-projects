"""客户工作台（客户详情页扩展）：状态 / 下一步 / 风险提醒。

═══════════════════════════════════════════════════════════════════════
【下一步是怎么判定的（每条规则都引用真实字段，不是模型推测）】
═══════════════════════════════════════════════════════════════════════
    优先级从高到低（先命中先返回）：

    1. handover_state == HUMAN_REQUIRED  → TAKE_OVER      策略闸门要求人工
    2. handover_state == HUMAN_ACTIVE    → REPLY          人工已接管，去回复
    3. dedupe_state  == PENDING_REVIEW   → VERIFY_DUPLICATE  去重冲突待裁决
    4. lifecycle_status == NEW           → FOLLOW_UP      新客户，去跟进
    5. lifecycle_status in (CONTACTED,)  → FOLLOW_UP      已联系未回复
    6. lifecycle_status in (REPLIED, QUOTED) → WAIT_CUSTOMER  已回复/已报价，等客户
    7. lifecycle_status in (WON, LOST)   → RECORD_NOTE    已结束，记录结论
    8. 其它                              → NONE

★ 这套规则是**业务规则**，不是 AI 判断；AI 不许自行改客户状态（D7/D8）。

═══════════════════════════════════════════════════════════════════════
【风险提醒只来自两条可复现依据】
═══════════════════════════════════════════════════════════════════════
  · 命中敏感策略的消息（PolicyGate 落库时写进 ai_status=HUMAN_REQUIRED 或内容带"命中敏感策略"）
  · AI 回复失败的消息（ai_status=FAILED）
★ 不做「高风险 / 87% 概率」这类推断打分；没有依据就不产出。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.customer import Customer, DedupeState, HandoverState, LifecycleStatus
from app.models.risk_event import RiskEventType, RiskLevel as RiskEventLevel
from app.schemas.customer_workspace import (
    ACTION_LABELS,
    CustomerWorkspaceResponse,
    HandoverHistoryItem,
    NextAction,
    NextActionCode,
    RiskAlert,
    RiskAlertKind,
    RiskLevel,
)
from app.schemas.evidence import SourceType, build_evidence, no_data_evidence
from app.services.handover import get_handover
from app.services.messaging import require_customer
from app.services.risk import list_customer_risks, scan_customer

router = APIRouter(tags=["customer-workspace"])

LIFECYCLE_LABELS: dict[LifecycleStatus, str] = {
    LifecycleStatus.NEW: "未联系",
    LifecycleStatus.CONTACTED: "已联系",
    LifecycleStatus.REPLIED: "已回复",
    LifecycleStatus.ENGAGED: "持续沟通",
    LifecycleStatus.QUOTED: "已报价",
    LifecycleStatus.WON: "已成交",
    LifecycleStatus.LOST: "已失效",
}

HANDOVER_LABELS: dict[HandoverState, str] = {
    HandoverState.AUTO: "AI 自动回复",
    HandoverState.HUMAN_REQUIRED: "需人工处理",
    HandoverState.HUMAN_ACTIVE: "人工接管中",
}

#: 命中敏感策略的消息，其内容里会出现这个标记（services/ai_reply.py 写入）
POLICY_MARKER = "命中敏感策略"


def _decide_next(customer: Customer) -> NextAction:
    """按真实字段判定下一步（顺序即优先级，先命中先返回）。"""
    cid = customer.id

    if customer.handover_state == HandoverState.HUMAN_REQUIRED:
        return NextAction(
            code=NextActionCode.TAKE_OVER,
            label=ACTION_LABELS[NextActionCode.TAKE_OVER],
            reason="接管状态 = HUMAN_REQUIRED（策略闸门判定敏感，AI 不许自动回复）",
            evidence_ref=f"customer:{cid}:handover_state",
        )
    if customer.handover_state == HandoverState.HUMAN_ACTIVE:
        return NextAction(
            code=NextActionCode.REPLY,
            label=ACTION_LABELS[NextActionCode.REPLY],
            reason="接管状态 = HUMAN_ACTIVE（人工已接管，处理中）",
            evidence_ref=f"customer:{cid}:handover_state",
        )
    if customer.dedupe_state == DedupeState.PENDING_REVIEW:
        return NextAction(
            code=NextActionCode.VERIFY_DUPLICATE,
            label=ACTION_LABELS[NextActionCode.VERIFY_DUPLICATE],
            reason="去重状态 = PENDING_REVIEW（命中多条既有客户，需人工裁决）",
            evidence_ref=f"customer:{cid}:dedupe_state",
        )

    mapping: dict[LifecycleStatus, tuple[NextActionCode, str]] = {
        LifecycleStatus.NEW: (NextActionCode.FOLLOW_UP, "生命周期 = NEW（还没联系过）"),
        LifecycleStatus.CONTACTED: (NextActionCode.FOLLOW_UP, "生命周期 = CONTACTED（已联系，尚未回复）"),
        LifecycleStatus.REPLIED: (NextActionCode.WAIT_CUSTOMER, "生命周期 = REPLIED（客户已回复，等下一步）"),
        LifecycleStatus.QUOTED: (NextActionCode.WAIT_CUSTOMER, "生命周期 = QUOTED（已报价，等客户决定）"),
        LifecycleStatus.WON: (NextActionCode.RECORD_NOTE, "生命周期 = WON（已成交，记录结论）"),
        LifecycleStatus.LOST: (NextActionCode.RECORD_NOTE, "生命周期 = LOST（已失效，记录原因）"),
        LifecycleStatus.ENGAGED: (NextActionCode.FOLLOW_UP, "生命周期 = ENGAGED（持续沟通中）"),
    }
    code, reason = mapping.get(
        customer.lifecycle_status, (NextActionCode.NONE, "没有命中任何动作规则")
    )
    return NextAction(
        code=code, label=ACTION_LABELS[code], reason=reason,
        evidence_ref=f"customer:{cid}:lifecycle_status",
    )


def _collect_risks(db: Session, customer_id: int) -> list[RiskAlert]:
    """风险提醒：**统一来自 risk_events 表**（与风险中心同一来源）。

    之前这里自己算了一套（把「报价」判成 HIGH），而风险中心按类别映射
    （「报价」= ATTENTION），同一件事两个页面等级不一致 —— 真缺陷，已统一。

    扫描是幂等的，所以这里顺手补扫一次即可，不需要定时任务保证。
    """
    try:
        scan_customer(db, customer_id)
    except LookupError:
        pass  # 客户不存在由 require_customer 处理，这里不重复报错

    risks: list[RiskAlert] = []
    for ev in list_customer_risks(db, customer_id, limit=20):
        risks.append(
            RiskAlert(
                kind=(
                    RiskAlertKind.AI_FAILED
                    if ev.event_type == RiskEventType.AI_FAILED
                    else RiskAlertKind.POLICY_BLOCKED
                ),
                level=(
                    RiskLevel.HIGH
                    if ev.level == RiskEventLevel.HIGH
                    else RiskLevel.ATTENTION
                    if ev.level == RiskEventLevel.ATTENTION
                    else RiskLevel.NONE
                ),
                title=(
                    "AI 回复失败"
                    if ev.event_type == RiskEventType.AI_FAILED
                    else "命中敏感策略"
                    if ev.event_type == RiskEventType.POLICY_BLOCKED
                    else "人工标记"
                ),
                detail=ev.trigger_text or ev.reason or "",
                occurred_at=ev.created_at,
                evidence_ref=ev.evidence_ref,
            )
        )
    return risks


@router.get(
    "/customers/{customer_id}/workspace",
    response_model=CustomerWorkspaceResponse,
    summary="客户工作台：状态 / 下一步建议 / 风险提醒（全部来自真实字段）",
)
def customer_workspace(
    customer_id: int, db: Session = Depends(get_db)
) -> CustomerWorkspaceResponse:
    customer = require_customer(db, customer_id)
    handover = get_handover(db, customer_id)
    risks = _collect_risks(db, customer_id)

    history = [
        HandoverHistoryItem(
            id=e.id,
            event_type=e.event_type.value if hasattr(e.event_type, "value") else str(e.event_type),
            from_state=e.from_state.value if getattr(e, "from_state", None) else None,
            to_state=e.to_state.value if hasattr(e.to_state, "value") else str(e.to_state),
            actor_type=e.actor_type.value if hasattr(e.actor_type, "value") else str(e.actor_type),
            reason=getattr(e, "reason", None),
            evidence_ref=e.evidence_ref,
            created_at=e.created_at,
        )
        for e in handover.get("events", [])
    ]

    if risks:
        risk_total = build_evidence(
            len(risks), source_type=SourceType.SYSTEM, evidence_ref=f"customer:{customer_id}:risks"
        )
    else:
        risk_total = no_data_evidence(
            "没有命中敏感策略或 AI 失败的消息",
            source_type=SourceType.SYSTEM,
            evidence_ref=f"customer:{customer_id}:risks",
        )

    return CustomerWorkspaceResponse(
        customer_id=customer.id,
        lifecycle_status=customer.lifecycle_status,
        lifecyle_label=LIFECYCLE_LABELS.get(customer.lifecycle_status, str(customer.lifecycle_status)),
        handover_state=customer.handover_state,
        handover_label=HANDOVER_LABELS.get(customer.handover_state, str(customer.handover_state)),
        ai_auto_reply_allowed=bool(handover.get("ai_auto_reply_allowed")),
        next_action=_decide_next(customer),
        risks=risks,
        risk_total=risk_total,
        handover_history=history,
        note=(
            "下一步与风险均来自真实字段（生命周期 / 接管状态 / 去重状态 / 消息 ai_status），"
            "不是模型推测；AI 不许自行改客户状态。"
        ),
    )
