"""商机服务（TASK-016）。

═══════════════════════════════════════════════════════════════════════
【阶段流转规则（不做工作流引擎，D8 同一原则）】
═══════════════════════════════════════════════════════════════════════
    允许：
      · 主线内**向前推进任意步**（NEW → QUOTED 可以直接跳，业务上常见）
      · 任意阶段 → WON（赢单）或 CLOSED_LOST（丢单）
      · WON / CLOSED_LOST → 重新打开到主线任一阶段（跟错了、客户回头了）
    拒绝：
      · 同一阶段（no-op，由接口层返回 changed=false，不报错）
      · 主线内**向后回退**（QUOTED → CONTACTED）——★ 这是刻意的：
        商机不会"退回"到已谈过的阶段；真要修正填错，请重新打开（走终态再回到主线），
        这样在数据上留下"曾经到过哪里"的痕迹，而不是悄悄抹掉。

★ 金额是业务数字，对外一律 EvidenceValue（D4）；库里 NULL = 未估出，**不是 0**。
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.opportunity import (
    CLOSED_STAGES,
    STAGE_ORDER,
    STAGE_RANK,
    WON_STAGE,
    Opportunity,
    OpportunityPriority,
    OpportunityStage,
)
from app.schemas.evidence import EvidenceValue, SourceType, build_evidence, no_data_evidence
from app.schemas.opportunities import (
    OpportunityItem,
    OpportunityStageColumn,
)

STAGE_LABELS: dict[OpportunityStage, str] = {
    OpportunityStage.NEW: "新建",
    OpportunityStage.CONTACTED: "已联系",
    OpportunityStage.QUALIFIED: "已确认需求",
    OpportunityStage.VALIDATING: "方案验证中",
    OpportunityStage.QUOTED: "已报价",
    OpportunityStage.NEGOTIATING: "谈判中",
    OpportunityStage.WON: "赢单",
    OpportunityStage.CLOSED_LOST: "丢单",
}


class StageTransitionError(Exception):
    """非法阶段流转。路由层翻译成 400。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def stage_label(stage: OpportunityStage) -> str:
    return STAGE_LABELS.get(stage, str(stage))


def evidence_ref_for(opportunity_id: int) -> str:
    return f"opportunity:{opportunity_id}"


def amount_evidence(amount: Decimal | None, ref: str) -> EvidenceValue:
    """金额 → EvidenceValue。

    ★ NULL（未估出）→ NO_DATA 而不是 0：把"没估"显示成"0 元"会让人以为这单不值钱。
      0 是合法的已估值（例如赠单），走 VALID。
    """
    if amount is None:
        return no_data_evidence(
            "尚未估算金额", source_type=SourceType.MANUAL, evidence_ref=ref
        )
    return build_evidence(
        float(amount), source_type=SourceType.MANUAL, evidence_ref=ref
    )


def to_item(op: Opportunity, customer_name: str) -> OpportunityItem:
    ref = op.evidence_ref or evidence_ref_for(op.id)
    return OpportunityItem(
        id=op.id,
        customer_id=op.customer_id,
        customer_name=customer_name,
        title=op.title,
        stage=op.stage,
        stage_label=stage_label(op.stage),
        priority=op.priority,
        amount=amount_evidence(op.amount, f"{ref}:amount"),
        currency=op.currency,
        won_amount=amount_evidence(op.won_amount, f"{ref}:won_amount"),
        probability=op.probability,
        expected_close_date=op.expected_close_date,
        closed_at=op.closed_at,
        owner=op.owner,
        note=op.note,
        is_active=op.is_active,
        evidence_ref=ref,
    )


def require_customer(db: Session, customer_id: int) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise LookupError(f"客户 {customer_id} 不存在")
    return customer


def get_opportunity(db: Session, opportunity_id: int) -> Opportunity | None:
    op = db.get(Opportunity, opportunity_id)
    if op is None or not op.is_active:
        return None
    return op


def create_opportunity(
    db: Session,
    *,
    customer_id: int,
    title: str,
    stage: OpportunityStage = OpportunityStage.NEW,
    priority: OpportunityPriority = OpportunityPriority.MEDIUM,
    amount: Decimal | None = None,
    currency: str = "CNY",
    probability: int | None = None,
    expected_close_date=None,
    owner: str | None = None,
    note: str | None = None,
) -> Opportunity:
    require_customer(db, customer_id)

    op = Opportunity(
        customer_id=customer_id,
        title=title.strip(),
        stage=stage,
        priority=priority,
        amount=amount,
        currency=(currency or "CNY").strip().upper(),
        probability=probability,
        expected_close_date=expected_close_date,
        owner=owner,
        note=note,
        is_active=True,
        # 已经建成终态时也要落关闭时间，否则看板上会出现"赢单但没有成交时间"
        closed_at=datetime.now(timezone.utc) if stage in CLOSED_STAGES else None,
        won_amount=amount if stage == WON_STAGE else None,
    )
    db.add(op)
    db.flush()
    op.evidence_ref = evidence_ref_for(op.id)
    db.commit()
    db.refresh(op)
    return op


def assert_transition_ok(current: OpportunityStage, target: OpportunityStage) -> None:
    """校验阶段流转是否合法。非法则抛 StageTransitionError。"""
    if current == target:
        return  # no-op 由调用方处理成 changed=false

    if current in CLOSED_STAGES:
        # 终态 → 主线：允许重新打开
        if target in CLOSED_STAGES:
            # 赢单 ↔ 丢单 互相翻转不允许（要改就重新打开再关）
            raise StageTransitionError(
                f"{stage_label(current)} 不能直接改成 {stage_label(target)}；"
                "请先重新打开到某个进行中阶段，再按实际情况关闭"
            )
        return

    if target in CLOSED_STAGES:
        return  # 任意进行中阶段 → 赢单/丢单：允许

    # 主线内：只允许向前
    if STAGE_RANK[target] < STAGE_RANK[current]:
        raise StageTransitionError(
            f"不允许从「{stage_label(current)}」回退到「{stage_label(target)}」；"
            "如果确实是填错了阶段，请先关闭（赢单/丢单）再重新打开到正确阶段，"
            "这样能保留它曾经到过哪里"
        )


def change_stage(
    db: Session,
    op: Opportunity,
    *,
    target: OpportunityStage,
    won_amount: Decimal | None = None,
    note: str | None = None,
) -> tuple[Opportunity, bool, str]:
    """推进阶段。返回 (商机, 是否变化, 说明)。★ no-op 不写任何变更。"""
    if op.stage == target:
        return op, False, f"阶段已经是「{stage_label(target)}」，未做变更"

    assert_transition_ok(op.stage, target)

    previous = op.stage
    op.stage = target
    if note:
        op.note = note

    if target in CLOSED_STAGES:
        op.closed_at = datetime.now(timezone.utc)
    else:
        op.closed_at = None  # 重新打开

    if target == WON_STAGE:
        # 赢单金额：显式传入优先；否则回落到预计金额（但不能把 NULL 变成 0）
        op.won_amount = won_amount if won_amount is not None else op.amount
    elif previous == WON_STAGE:
        # 从赢单重新打开 → 清掉成交金额与时间，避免"进行中却有成交额"
        op.won_amount = None

    db.commit()
    db.refresh(op)
    return op, True, f"阶段已从「{stage_label(previous)}」推进到「{stage_label(target)}」"


def update_opportunity(db: Session, op: Opportunity, **fields) -> Opportunity:
    """按需更新字段（只更新显式传入且非 None 的）。"""
    for key, value in fields.items():
        if value is not None and hasattr(op, key):
            setattr(op, key, value)
    db.commit()
    db.refresh(op)
    return op


def list_opportunities(
    db: Session,
    *,
    customer_id: int | None = None,
    stage: OpportunityStage | None = None,
    only_open: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[OpportunityItem], int]:
    conditions = [Opportunity.is_active.is_(True)]
    if customer_id is not None:
        conditions.append(Opportunity.customer_id == customer_id)
    if stage is not None:
        conditions.append(Opportunity.stage == stage)
    if only_open:
        conditions.append(Opportunity.stage.notin_(list(CLOSED_STAGES)))

    base = select(Opportunity)
    counter = select(func.count(Opportunity.id))
    for c in conditions:
        base = base.where(c)
        counter = counter.where(c)

    total = db.scalar(counter) or 0
    rows = db.scalars(
        base.order_by(Opportunity.updated_at.desc(), Opportunity.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return _with_customer_names(db, list(rows)), total


def _with_customer_names(db: Session, rows: list[Opportunity]) -> list[OpportunityItem]:
    names = {
        cid: name
        for cid, name in db.execute(
            select(Customer.id, Customer.name).where(
                Customer.id.in_([r.customer_id for r in rows] or [0])
            )
        ).all()
    }
    return [to_item(r, names.get(r.customer_id, f"#{r.customer_id}")) for r in rows]


def _sum_evidence(amounts: list[Decimal | None], ref: str):
    """金额合计。★ 全部未估 → NO_DATA（不是 0）。"""
    known = [a for a in amounts if a is not None]
    if not known:
        return no_data_evidence(
            "该范围内没有已估算金额的商机", source_type=SourceType.MANUAL, evidence_ref=ref
        )
    return build_evidence(float(sum(known)), source_type=SourceType.MANUAL, evidence_ref=ref)


def build_board(db: Session, *, per_stage_limit: int = 50) -> dict:
    """商机看板：按阶段分列。"""
    rows = db.scalars(
        select(Opportunity)
        .where(Opportunity.is_active.is_(True))
        .order_by(Opportunity.updated_at.desc(), Opportunity.id.desc())
    ).all()

    all_items = _with_customer_names(db, list(rows))
    by_stage: dict[OpportunityStage, list[OpportunityItem]] = {s: [] for s in STAGE_LABELS}
    for item in all_items:
        by_stage[item.stage].append(item)

    columns: list[OpportunityStageColumn] = []
    for stage in (*STAGE_ORDER, OpportunityStage.CLOSED_LOST):
        items = by_stage.get(stage, [])
        amounts = [r.amount for r in rows if r.stage == stage]
        columns.append(
            OpportunityStageColumn(
                stage=stage,
                label=stage_label(stage),
                count=len(items),
                amount_total=_sum_evidence(amounts, f"opportunities:board:{stage.value}:amount"),
                items=items[:per_stage_limit],
            )
        )

    total = len(all_items)
    open_count = sum(1 for i in all_items if i.stage not in CLOSED_STAGES)
    won_count = sum(1 for i in all_items if i.stage == WON_STAGE)
    pipeline_amounts = [r.amount for r in rows if r.stage not in CLOSED_STAGES]
    won_amounts = [r.won_amount for r in rows if r.stage == WON_STAGE]

    return {
        "columns": columns,
        "total": (
            build_evidence(total, source_type=SourceType.SYSTEM, evidence_ref="opportunities:count")
            if total
            else no_data_evidence(
                "还没有商机记录", source_type=SourceType.SYSTEM, evidence_ref="opportunities:count"
            )
        ),
        "open_total": (
            build_evidence(
                open_count, source_type=SourceType.SYSTEM, evidence_ref="opportunities:open:count"
            )
            if total
            else no_data_evidence(
                "还没有商机记录", source_type=SourceType.SYSTEM, evidence_ref="opportunities:open:count"
            )
        ),
        "won_total": (
            build_evidence(
                won_count, source_type=SourceType.SYSTEM, evidence_ref="opportunities:won:count"
            )
            if total
            else no_data_evidence(
                "还没有商机记录", source_type=SourceType.SYSTEM, evidence_ref="opportunities:won:count"
            )
        ),
        "pipeline_amount": _sum_evidence(pipeline_amounts, "opportunities:open:amount"),
        "won_amount_total": _sum_evidence(won_amounts, "opportunities:won:amount"),
        "note": (
            "商机按阶段分列；金额是业务数字（EvidenceValue），未估算的商机金额为「暂无数据」而不是 0。"
            "赢单概率由人工填写，不是模型预测。"
        ),
    }
