"""报表类型补 MONTHLY（TASK-023，需求 §十八 要求「本月汇报」）

MySQL ENUM 是字面量列表，Python 侧加成员不会改列定义（见 D20/D36）——
必须手写 ALTER。漏了这条，生成月报时会报 1265 "Data truncated for column 'report_type'"。

★ 按 D36 纪律：先查 `information_schema.COLUMNS` 找出所有引用该枚举的列。
  `report_type` 这个类型名只被 reports.report_type 一列使用，无遗漏。

Revision ID: 8a5c1f3b90d2
Revises: 4d92b7e1a8c3
Create Date: 2026-10-04 03:45:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = '8a5c1f3b90d2'
down_revision: Union[str, Sequence[str], None] = '4d92b7e1a8c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD = ('DAILY', 'WEEKLY', 'MANUAL')
NEW = ('DAILY', 'WEEKLY', 'MONTHLY', 'MANUAL')


def _enum(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE `reports` MODIFY COLUMN `report_type` "
        f"ENUM({_enum(NEW)}) NOT NULL"
    )


def downgrade() -> None:
    # ★ 收紧 ENUM 前先清理新值行，否则 MySQL 会截断成空串（D20 同一个坑）
    #   MONTHLY 报告退回 WEEKLY（周期语义最近的既有值），
    #   宁可类型粗糙也不能让数据变空。
    op.execute("UPDATE `reports` SET `report_type` = 'WEEKLY' WHERE `report_type` = 'MONTHLY'")
    op.execute(
        f"ALTER TABLE `reports` MODIFY COLUMN `report_type` "
        f"ENUM({_enum(OLD)}) NOT NULL"
    )
