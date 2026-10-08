"""merge parallel lane heads

★ 整合（integration）专用：四条并行线的迁移都从 36e74cad14a0 分叉，各自成了 head。
  本 revision 只做「接回线性」——不含任何 DDL（upgrade/downgrade 都是 pass），
  四条线的表与列由各自的父迁移提供：
    · 6b87e62da680 —— TASK-006：customers.lifecycle_status + customer_events
    · 3ccb69d4e8a0 —— TASK-005：customers.handover_state + customer_handover_events
    · 728b1a3fcf7d —— TASK-007：reports
    · c63846e9f46f —— TASK-003/004：customer_messages
  合并后只剩这一个 head，upgrade head 会把四条线一次走完。

Revision ID: 5f4f336eda2e
Revises: 6b87e62da680, 3ccb69d4e8a0, 728b1a3fcf7d, c63846e9f46f
Create Date: 2026-10-03 22:58:01.724482

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5f4f336eda2e'
down_revision: Union[str, Sequence[str], None] = ('6b87e62da680', '3ccb69d4e8a0', '728b1a3fcf7d', 'c63846e9f46f')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
