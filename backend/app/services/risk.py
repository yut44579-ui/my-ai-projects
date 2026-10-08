"""风险事件服务（TASK-009）。

═══════════════════════════════════════════════════════════════════════
【风险从哪来：只有两条真实来源】
═══════════════════════════════════════════════════════════════════════
    ① 消息命中策略闸门（D7）→ POLICY_BLOCKED
    ② AI 回复失败（D10）    → AI_FAILED

★ 不做「87% 概率是骗子」这类推断打分。等级由**类别**决定，不是模型判断：
    资金/不可逆类（银行账户 / 异常资金 / 付款 / 退款 / 赔偿 / 法律）→ HIGH
    价格/承诺类（报价 / 折扣 / 合同 / 大额订单 / 投诉 / 特殊资源 / 关键承诺）→ ATTENTION
    AI 失败                                                              → ATTENTION

═══════════════════════════════════════════════════════════════════════
【幂等（可反复扫描，不会重复堆）】
═══════════════════════════════════════════════════════════════════════
    唯一键 = (source_type, source_ref)，其中 source_ref = `customer_message:{id}`。
    scan 时先查已有，再插新的；数据库唯一约束兜底。
    这样"每天扫一遍"不会产生重复风险，也不需要 remembered-state。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.customer_message import AiStatus, CustomerMessage
from app.models.risk_event import RiskEvent, RiskEventType, RiskLevel, RiskStatus
from app.schemas.evidence import SourceType
from app.services.policy_gate import PolicyCategory

#: 资金 / 不可逆类 → 高风险（参考产品里的「资金风险」在这一档）
HIGH_RISK_CATEGORIES: frozenset[PolicyCategory] = frozenset(
    {
        PolicyCategory.BANK_ACCOUNT,
        PolicyCategory.ABNORMAL_FUNDS,
        PolicyCategory.PAYMENT,
        PolicyCategory.REFUND,
        PolicyCategory.COMPENSATION,
        PolicyCategory.LEGAL,
    }
)

#: 命中敏感策略的消息，其内容里有这个标记（services/ai_reply.py 写入）
POLICY_MARKER = "命中敏感策略"


def level_for_category(category: PolicyCategory | None) -> RiskLevel:
    """类别 → 风险等级。★ 纯映射，无打分。"""
    if category is None:
        return RiskLevel.ATTENTION
    return RiskLevel.HIGH if category in HIGH_RISK_CATEGORIES else RiskLevel.ATTENTION


@dataclass
class ScanResult:
    """一次扫描的结果（便于接口/脚本回报）。"""

    scanned_messages: int
    created: int
    skipped_existing: int
    risks: list[RiskEvent]


def _existing_source_refs(db: Session, customer_id: int | None) -> set[str]:
    stmt = select(RiskEvent.source_ref)
    if customer_id is not None:
        stmt = stmt.where(RiskEvent.customer_id == customer_id)
    return set(db.scalars(stmt).all())


def _classify_message(msg: CustomerMessage) -> tuple[RiskEventType, RiskLevel, str, str] | None:
    """判断一条消息是否构成风险。返回 (类型, 等级, 原因, 触发摘要) 或 None。"""
    content = msg.content or ""

    if msg.ai_status == AiStatus.FAILED:
        return (
            RiskEventType.AI_FAILED,
            RiskLevel.ATTENTION,
            "AI 回复失败，需人工接手（D10）",
            content[:200],
        )

    if msg.ai_status == AiStatus.HUMAN_REQUIRED:
        # 两种情况：闸门判定敏感（内容带标记）或人工接管说明
        if POLICY_MARKER in content:
            # 从内容里取回类别标签（写入时格式固定：命中敏感策略「报价」（命中词：报价））
            label = None
            start = content.find("「")
            end = content.find("」", start + 1)
            if start != -1 and end != -1:
                label = content[start + 1 : end]
            # 用标签反查类别（标签与 CATEGORY_LABELS 一一对应）
            from app.services.policy_gate import CATEGORY_LABELS

            category = next((c for c, l in CATEGORY_LABELS.items() if l == label), None)
            level = level_for_category(category)
            return (
                RiskEventType.POLICY_BLOCKED,
                level,
                f"命中敏感策略：{label or '未知类别'}（闸门 D7 判定，未调用模型）",
                content[:200],
            )
        return (
            RiskEventType.MANUAL_FLAG,
            RiskLevel.ATTENTION,
            "已转人工处理",
            content[:200],
        )

    return None


def scan_customer(db: Session, customer_id: int) -> ScanResult:
    """扫描**一个客户**的消息，把命中项落成风险事件（幂等）。"""
    messages = db.scalars(
        select(CustomerMessage)
        .where(CustomerMessage.customer_id == customer_id)
        .order_by(CustomerMessage.created_at.asc(), CustomerMessage.id.asc())
    ).all()

    # 顺带确认客户存在（不存在就让调用方 404，而不是静默扫 0 条）
    if db.get(Customer, customer_id) is None:
        raise LookupError(f"customer {customer_id} not found")

    existing = _existing_source_refs(db, customer_id)
    created: list[RiskEvent] = []
    skipped = 0

    for msg in messages:
        source_ref = f"customer_message:{msg.id}"
        verdict = _classify_message(msg)
        if verdict is None:
            continue
        if source_ref in existing:
            skipped += 1
            continue
        event_type, level, reason, trigger = verdict
        risk = RiskEvent(
            customer_id=customer_id,
            event_type=event_type,
            level=level,
            status=RiskStatus.OPEN,
            source_type=SourceType.SYSTEM,
            source_ref=source_ref,
            trigger_text=trigger,
            reason=reason,
            # D5：证据锚点用 message_id
            evidence_ref=source_ref,
        )
        db.add(risk)
        created.append(risk)
        existing.add(source_ref)

    if created:
        db.commit()
        for r in created:
            db.refresh(r)

    return ScanResult(
        scanned_messages=len(messages),
        created=len(created),
        skipped_existing=skipped,
        risks=created,
    )


def scan_all(db: Session) -> ScanResult:
    """扫描全部客户（供定时任务/脚本调用）。"""
    ids = db.scalars(select(Customer.id)).all()
    total_msgs = 0
    all_created: list[RiskEvent] = []
    skipped = 0
    for cid in ids:
        r = scan_customer(db, cid)
        total_msgs += r.scanned_messages
        all_created.extend(r.risks)
        skipped += r.skipped_existing
    return ScanResult(
        scanned_messages=total_msgs,
        created=len(all_created),
        skipped_existing=skipped,
        risks=all_created,
    )


def list_risks(
    db: Session,
    *,
    customer_id: int | None = None,
    status: RiskStatus | None = None,
    level: RiskLevel | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[RiskEvent], int]:
    """风险列表（新的在前）+ 总数。"""
    conditions = []
    if customer_id is not None:
        conditions.append(RiskEvent.customer_id == customer_id)
    if status is not None:
        conditions.append(RiskEvent.status == status)
    if level is not None:
        conditions.append(RiskEvent.level == level)

    base = select(RiskEvent)
    counter = select(func.count(RiskEvent.id))
    for c in conditions:
        base = base.where(c)
        counter = counter.where(c)

    total = db.scalar(counter) or 0
    rows = db.scalars(
        base.order_by(RiskEvent.created_at.desc(), RiskEvent.id.desc()).limit(limit).offset(offset)
    ).all()
    return list(rows), total


def get_risk(db: Session, risk_id: int) -> RiskEvent | None:
    return db.get(RiskEvent, risk_id)


def list_customer_risks(
    db: Session, customer_id: int, *, open_only: bool = False, limit: int = 20
) -> list[RiskEvent]:
    """某个客户的风险（新的在前）。

    ★ 客户详情页与风险中心**共用这一个来源**：
      之前客户详情页自己算了一套（把「报价」判成 HIGH），而风险中心按类别映射
      （「报价」= ATTENTION），同一件事两个页面等级不一致 —— 属于真缺陷。
      统一到本表之后，两处永远一致。
    """
    stmt = select(RiskEvent).where(RiskEvent.customer_id == customer_id)
    if open_only:
        stmt = stmt.where(RiskEvent.status == RiskStatus.OPEN)
    rows = db.scalars(
        stmt.order_by(RiskEvent.created_at.desc(), RiskEvent.id.desc()).limit(limit)
    ).all()
    return list(rows)


def resolve_risk(
    db: Session, risk_id: int, *, status: RiskStatus, note: str | None = None
) -> RiskEvent | None:
    """处置风险：OPEN → RESOLVED / DISMISSED。

    ★ 只做状态流转，不引工作流引擎（D8 同一原则）。
    """
    from datetime import datetime, timezone

    risk = db.get(RiskEvent, risk_id)
    if risk is None:
        return None
    risk.status = status
    risk.resolved_note = note
    risk.resolved_at = datetime.now(timezone.utc) if status != RiskStatus.OPEN else None
    db.commit()
    db.refresh(risk)
    return risk
