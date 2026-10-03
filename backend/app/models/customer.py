"""客户模型（D11 第 2 表，TASK-001 落地）。

★ 本表故意没有 status 字段 —— V1 没有任何流程会改它，留着就是死列。
★ evidence_ref 与 batch_id 双保险且必须一致：evidence_ref = f"import_batch:{batch_id}"，
  由代码生成，禁止手填（见 services/importer.py）。
★ last_seen_at 语义写死：同一客户被导入命中就刷新，不做别的。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import BigInteger, DateTime, Enum as SAEnum, ForeignKey, String, Text, func
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.import_batch import _enum_values

# MySQL 的 DATETIME 默认只精确到秒，同一秒内多次导入就分不出先后。
# first_seen_at / last_seen_at 要能体现"这次导入命中了它"（语义：命中就刷新），
# 所以这两个列在 MySQL 上用 DATETIME(6)（微秒），其它方言仍是普通 DateTime。
Stamp6 = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


class CustomerSourceType(str, Enum):
    """客户数据来源。★ 测试样本文件导入时必须是 TEST，绝不许写 REAL/IMPORT。"""

    REAL = "REAL"
    TEST = "TEST"
    MANUAL = "MANUAL"


class LifecycleStatus(str, Enum):
    """客户生命周期状态（TASK-006）。

    ★ 与 TASK-001 评审砍掉的旧 status 列不是一回事：那是没有流程支撑的死列。
      这里的每个取值都由「状态变更接口 + customer_events 事件」驱动，可追溯。

    流转不做强约束（V1 不写死合法路径），但每次变更都必须留事件。
    """

    NEW = "NEW"
    CONTACTED = "CONTACTED"
    REPLIED = "REPLIED"
    ENGAGED = "ENGAGED"
    QUOTED = "QUOTED"
    WON = "WON"
    LOST = "LOST"


class DedupeState(str, Enum):
    """去重状态。

    CLEAN          —— 独立新建，无冲突
    PENDING_REVIEW —— 命中多条既有客户（冲突），已新建但需人工裁决，★不自动合并
    MERGED         —— 已被人工合并（V1 不产生，枚举先占位）
    """

    CLEAN = "CLEAN"
    PENDING_REVIEW = "PENDING_REVIEW"
    MERGED = "MERGED"


class Customer(Base, TimestampMixin):
    """客户主表。"""

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # 主字段：name 是唯一必填项，其余都可空
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="只存数字（规范化后），空值一律为 NULL 不存空串"
    )
    email: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="小写（规范化后），空值一律为 NULL 不存空串"
    )
    region: Mapped[str | None] = mapped_column(String(128), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 来源追溯：batch_id 可空（MANUAL 录入没有批次）；双保险 evidence_ref 必须与之一致
    batch_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("import_batches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,  # ★ 规格要求加索引
        comment="来源批次；MANUAL 录入为 NULL",
    )
    source_type: Mapped[CustomerSourceType] = mapped_column(
        SAEnum(CustomerSourceType, name="customer_source_type", values_callable=_enum_values),
        nullable=False,
        default=CustomerSourceType.MANUAL,
    )
    evidence_ref: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="= import_batch:{batch_id}，代码生成"
    )
    dedupe_state: Mapped[DedupeState] = mapped_column(
        SAEnum(DedupeState, name="customer_dedupe_state", values_callable=_enum_values),
        nullable=False,
        default=DedupeState.CLEAN,
    )

    # TASK-006：生命周期状态。默认 NEW；每次变更由 POST /customers/{id}/status 写入，
    # 且必须同时落一条 customer_events（历史不许丢）。
    lifecycle_status: Mapped[LifecycleStatus] = mapped_column(
        SAEnum(LifecycleStatus, name="customer_lifecycle_status", values_callable=_enum_values),
        nullable=False,
        default=LifecycleStatus.NEW,
        server_default=LifecycleStatus.NEW.value,
    )

    first_seen_at: Mapped[datetime] = mapped_column(
        Stamp6, server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        Stamp6, server_default=func.now(), nullable=False, comment="命中即刷新：同一客户被导入命中就更新它"
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Customer id={self.id} name={self.name!r} state={self.dedupe_state}>"
