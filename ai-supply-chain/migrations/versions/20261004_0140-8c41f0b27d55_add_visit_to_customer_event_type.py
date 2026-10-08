"""add VISIT to customer_event_type enum (TASK-010 客户访问追踪)

TASK-010：客户访问追踪。访问**不新建表**（D11 冻结 7 表），而是作为
customer_events 的一种事件类型 VISIT 留痕；访问细节（page / channel / referrer）
放 metadata_json。

★ 为什么必须单独写这条迁移：
  MySQL 的 ENUM 列是**字面量列表**，在 Python 侧往 Enum 里加一个成员
  不会改变数据库列定义，Alembic autogenerate 也检测不到。
  不迁移就直接插 VISIT → 报 1265 "Data truncated for column 'event_type'"（实测踩过）。
  这里用 ALTER TABLE MODIFY COLUMN 把新值补上；downgrade 时改回旧列表。

Revision ID: 8c41f0b27d55
Revises: 2a1e5d573abb
Create Date: 2026-10-04 01:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '8c41f0b27d55'
down_revision: Union[str, Sequence[str], None] = '2a1e5d573abb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: 旧列表（迁移前）—— downgrade 用
OLD_VALUES = ('STATUS_CHANGED', 'MESSAGE_SENT', 'HANDOVER', 'NOTE')
#: 新列表（加上 VISIT）
NEW_VALUES = ('STATUS_CHANGED', 'MESSAGE_SENT', 'HANDOVER', 'NOTE', 'VISIT')

ENUM_NAME = 'customer_event_type'


def _modify(values: tuple[str, ...]) -> None:
    """把 customer_events.event_type 的 ENUM 列表改成给定值。

    ★ 用原生 SQL 而不是 op.alter_column：ENUM 的取值列表在 Alembic 里
      没有稳定的跨方言表达，直接 MODIFY COLUMN 最可靠、也最容易读懂。
      nullable=False 与原定义保持一致。
    """
    joined = ", ".join(f"'{v}'" for v in values)
    op.execute(
        f"ALTER TABLE `customer_events` "
        f"MODIFY COLUMN `event_type` ENUM({joined}) NOT NULL"
    )


def upgrade() -> None:
    """把 VISIT 加进 customer_event_type。"""
    _modify(NEW_VALUES)


def downgrade() -> None:
    """去掉 VISIT。

    ★ 回滚前必须先清掉 VISIT 行，否则 MySQL 在收紧 ENUM 时会把它们截断成空串
      （严格模式下直接报错、非严格模式下静默写坏数据）。这里选择**显式删除**：
      访问事件是 TASK-010 新增的能力，回滚该 TASK 时它本就不该存在。
    """
    op.execute("DELETE FROM `customer_events` WHERE `event_type` = 'VISIT'")
    _modify(OLD_VALUES)
