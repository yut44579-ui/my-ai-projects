"""客户事件留痕模型（TASK-006 落地，D11 第 4 表）。

★ 本表的唯一使命：**每次客户状态变化都必须留一条不可变的事件**，
  不许出现「只有当前值、没有历史」的情况（TASK-006 目标）。
★ 事件只增不改：没有任何接口会 UPDATE / DELETE customer_events。
★ evidence_ref 由代码生成为 customer_event:{id}，禁止手填。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    JSON,
    String,
    func,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.customer import LifecycleStatus
from app.models.import_batch import _enum_values

# 与 customers.first_seen_at 同理：MySQL DATETIME 只精确到秒，
# 同一秒内连改三次状态就分不出先后。事件流要能体现「谁先谁后」，
# 所以 created_at 在 MySQL 上用 DATETIME(6)（微秒）。
Stamp6 = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


class CustomerEventType(str, Enum):
    """事件类型。冻结枚举，禁止自由文本。

    ★ VISIT（TASK-010 追加）：客户访问行为。
      为什么记在这里而不是新开 customer_visits 表：
        · D11 冻结 7 表，新增表超出冻结范围；
        · 访问本质就是「客户发生了什么」，与状态变更/消息/接管同类；
        · metadata_json 已能承载结构化补充（page / channel / referrer），
          不需要为几个字段再建一张表（总控提示词 §五：能简单解决不复杂化）。
      ★ 只记录**可识别到客户**的访问（§十）；匿名访问不落库，避免造出"客户"。
    """

    STATUS_CHANGED = "STATUS_CHANGED"
    MESSAGE_SENT = "MESSAGE_SENT"
    HANDOVER = "HANDOVER"
    NOTE = "NOTE"
    VISIT = "VISIT"


class ActorType(str, Enum):
    """事件发起方。★ AI 不得自行改客户状态（TASK-006 §二.1）。"""

    HUMAN = "HUMAN"
    AI = "AI"
    SYSTEM = "SYSTEM"


class EventSourceType(str, Enum):
    """事件的数据来源类型。

    刻意不复用 CustomerSourceType（REAL/TEST/MANUAL）—— 那是「客户资料从哪来」，
    与「这条事件由谁/怎么产生」不是一回事：SYSTEM 事件在那边没有对应取值，
    硬映射成 MANUAL 就是写入假来源（违反「数据来源一律如实标注」）。
    """

    MANUAL = "MANUAL"
    IMPORT = "IMPORT"
    SYSTEM = "SYSTEM"


class CustomerEvent(Base):
    """客户事件流：状态变更 / 消息 / 接管 / 备注，全部在此留痕。"""

    __tablename__ = "customer_events"

    # 事件流按客户 + 时间取，组合索引的最左前缀即 customer_id，
    # 因此不再单独建一个 customer_id 单列索引（重复索引只增写入成本）。
    __table_args__ = (
        Index("ix_customer_events_customer_id_created_at", "customer_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    customer_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属客户；客户删除则其事件一并清除（V1 无删除接口）",
    )

    event_type: Mapped[CustomerEventType] = mapped_column(
        SAEnum(CustomerEventType, name="customer_event_type", values_callable=_enum_values),
        nullable=False,
    )

    # 仅 STATUS_CHANGED / HANDOVER 这类状态型事件有值；NOTE 等可以为 NULL
    from_status: Mapped[LifecycleStatus | None] = mapped_column(
        SAEnum(LifecycleStatus, name="customer_lifecycle_status", values_callable=_enum_values),
        nullable=True,
        comment="变更前状态；非状态型事件为 NULL",
    )
    to_status: Mapped[LifecycleStatus | None] = mapped_column(
        SAEnum(LifecycleStatus, name="customer_lifecycle_status", values_callable=_enum_values),
        nullable=True,
        comment="变更后状态；非状态型事件为 NULL",
    )

    actor_type: Mapped[ActorType] = mapped_column(
        SAEnum(ActorType, name="customer_event_actor_type", values_callable=_enum_values),
        nullable=False,
    )
    source_type: Mapped[EventSourceType] = mapped_column(
        SAEnum(EventSourceType, name="customer_event_source_type", values_callable=_enum_values),
        nullable=False,
    )
    evidence_ref: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="证据锚点 = customer_event:{id}，代码生成"
    )
    metadata_json: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="结构化补充信息（如 {\"note\": ...}），不存自由文本业务字段"
    )

    created_at: Mapped[datetime] = mapped_column(
        Stamp6, server_default=func.now(), nullable=False, comment="事件发生时间（微秒精度，保证同秒内可排序）"
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<CustomerEvent id={self.id} customer={self.customer_id} "
            f"{self.from_status}->{self.to_status} actor={self.actor_type}>"
        )
