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
from app.models.handover import HandoverState
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

    # 人工接管三态（D8，TASK-005）：AUTO → HUMAN_REQUIRED → HUMAN_ACTIVE
    # 与 TASK-001 故意砍掉的旧 status 不同 —— 它由闸门 / 人工操作驱动，每一步都有事件留痕
    # （customer_handover_events），不是没有流程支撑的死列。
    handover_state: Mapped[HandoverState] = mapped_column(
        SAEnum(HandoverState, name="customer_handover_state", values_callable=_enum_values),
        nullable=False,
        default=HandoverState.AUTO,
        server_default=HandoverState.AUTO.value,  # 给存量行兜底（ADD COLUMN NOT NULL 需要）
        index=True,  # 列表页要按它筛「需要人工处理」并打醒目标记
        comment="人工接管三态：AUTO / HUMAN_REQUIRED / HUMAN_ACTIVE",
    )

    first_seen_at: Mapped[datetime] = mapped_column(
        Stamp6, server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        Stamp6, server_default=func.now(), nullable=False, comment="命中即刷新：同一客户被导入命中就更新它"
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Customer id={self.id} name={self.name!r} state={self.dedupe_state}>"
