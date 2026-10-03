"""人工接管三态模型（D8：AUTO → HUMAN_REQUIRED → HUMAN_ACTIVE）。

★ 本文件是 TASK-005（并行线 5）的独立实现，整合阶段由主流程接线。
  见 docs/HANDOVER_INTEGRATION.md。

设计边界（写给整合的人看，很重要）：
  · TASK-006 拥有**通用**事件表 `customer_events`（生命周期状态机 lifecycle_status +
    通用事件留痕），那是另一条并行线的内容，本 TASK 不碰、也不在基线上建它。
  · 因此接管留痕落在本 TASK 专属的 `customer_handover_events` 上：
    两条并行线的迁移各自接在基线 36e74cad14a0 上，互不冲突、可独立验收。
  · 本表列结构与通用事件表同形（event_type / from_state / to_state / actor_type /
    source_type / reason / metadata_json / evidence_ref / created_at），
    整合时若要并入 `customer_events`，成本 = 一次 INSERT..SELECT + 枚举映射。

★ 不引工作流引擎（D8）：只有三个状态、五条合法迁移，全部写死在 services/handover.py。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import BigInteger, DateTime, Enum as SAEnum, ForeignKey, Index, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.import_batch import _enum_values
from app.schemas.evidence import SourceType


class HandoverState(str, Enum):
    """接管三态（D8）。★ 冻结取值，禁止自由文本。

    AUTO           —— AI 自动回复（默认）
    HUMAN_REQUIRED —— 代码层闸门判定「敏感」，必须人工处理，AI 不许自动回复
    HUMAN_ACTIVE   —— 人工已接管，处理中
    """

    AUTO = "AUTO"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"
    HUMAN_ACTIVE = "HUMAN_ACTIVE"


class HandoverActorType(str, Enum):
    """状态变更的发起方。

    HUMAN  —— 人工点击接管 / 交回
    SYSTEM —— 代码层闸门（TASK-004 PolicyGate）判定敏感
    AI     —— ★ 保留取值但**禁止**用于改状态（AI 不许自行改客户接管状态，见 D7/D8）
    """

    HUMAN = "HUMAN"
    AI = "AI"
    SYSTEM = "SYSTEM"


class HandoverEventType(str, Enum):
    """接管事件的类型。★ 冻结枚举，一次状态变化 = 一条事件。"""

    HUMAN_REQUIRED = "HUMAN_REQUIRED"  # 闸门判定敏感 → 转人工
    TAKEN_OVER = "TAKEN_OVER"          # 人工接管 → HUMAN_ACTIVE
    RESUMED = "RESUMED"                # 交回 AI → AUTO


class CustomerHandoverEvent(Base):
    """接管事件留痕（AC5：每次状态变化都必须有事件，不许只有当前值没有历史）。"""

    __tablename__ = "customer_handover_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    customer_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属客户；客户删除时事件一并删除（CASCADE）",
    )

    event_type: Mapped[HandoverEventType] = mapped_column(
        SAEnum(HandoverEventType, name="handover_event_type", values_callable=_enum_values),
        nullable=False,
    )
    from_state: Mapped[HandoverState | None] = mapped_column(
        SAEnum(HandoverState, name="handover_from_state", values_callable=_enum_values),
        nullable=True,
        comment="迁移前状态（首条事件理论上仍有 from，留空只为容错）",
    )
    to_state: Mapped[HandoverState] = mapped_column(
        SAEnum(HandoverState, name="handover_to_state", values_callable=_enum_values),
        nullable=False,
    )
    actor_type: Mapped[HandoverActorType] = mapped_column(
        SAEnum(HandoverActorType, name="handover_actor_type", values_callable=_enum_values),
        nullable=False,
    )
    source_type: Mapped[SourceType] = mapped_column(
        SAEnum(SourceType, name="handover_source_type", values_callable=_enum_values),
        nullable=False,
        comment="数据来源：人工操作=MANUAL；代码层判定=SYSTEM",
    )

    reason: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="为什么转人工（闸门命中原因 / 人工备注）"
    )
    # 结构自由，例如 {"rule": "QUOTE_PROMISE", "matched": "已经给你 8 折"}
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    evidence_ref: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="证据锚点，由代码生成 customer_handover_event:{id}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_customer_handover_events_customer_created", "customer_id", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<CustomerHandoverEvent id={self.id} customer={self.customer_id} "
            f"{self.from_state}->{self.to_state} by={self.actor_type}>"
        )
