"""add customer lifecycle_status and customer_events

TASK-006：客户状态机 + 事件留痕。

· customers.lifecycle_status —— 枚举 NEW|CONTACTED|REPLIED|ENGAGED|QUOTED|WON|LOST，
  NOT NULL，server_default='NEW'。已有行由 server_default 回填为 NEW（不是「假数据」：
  NEW 就是「尚未发生任何流转」的准确语义）。
· customer_events —— 每次状态变化/消息/接管/备注都留一条，只增不改。

Revision ID: 6b87e62da680
Revises: 36e74cad14a0
Create Date: 2026-10-03 22:06:13.281681

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = '6b87e62da680'
down_revision: Union[str, Sequence[str], None] = '36e74cad14a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LIFECYCLE_VALUES = ('NEW', 'CONTACTED', 'REPLIED', 'ENGAGED', 'QUOTED', 'WON', 'LOST')


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'customer_events',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('customer_id', sa.BigInteger(), nullable=False,
                  comment='所属客户；客户删除则其事件一并清除（V1 无删除接口）'),
        sa.Column('event_type', sa.Enum(
            'STATUS_CHANGED', 'MESSAGE_SENT', 'HANDOVER', 'NOTE',
            name='customer_event_type'), nullable=False),
        sa.Column('from_status', sa.Enum(*LIFECYCLE_VALUES, name='customer_lifecycle_status'),
                  nullable=True, comment='变更前状态；非状态型事件为 NULL'),
        sa.Column('to_status', sa.Enum(*LIFECYCLE_VALUES, name='customer_lifecycle_status'),
                  nullable=True, comment='变更后状态；非状态型事件为 NULL'),
        sa.Column('actor_type', sa.Enum('HUMAN', 'AI', 'SYSTEM',
                                        name='customer_event_actor_type'), nullable=False),
        sa.Column('source_type', sa.Enum('MANUAL', 'IMPORT', 'SYSTEM',
                                         name='customer_event_source_type'), nullable=False),
        sa.Column('evidence_ref', sa.String(length=128), nullable=True,
                  comment='证据锚点 = customer_event:{id}，代码生成'),
        sa.Column('metadata_json', sa.JSON(), nullable=True,
                  comment='结构化补充信息（如 {"note": ...}），不存自由文本业务字段'),
        sa.Column('created_at',
                  sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'),
                  server_default=sa.text('now()'), nullable=False,
                  comment='事件发生时间（微秒精度，保证同秒内可排序）'),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    # 组合索引最左前缀即 customer_id，同时满足「按客户取事件流」与 FK 查找。
    op.create_index(
        'ix_customer_events_customer_id_created_at',
        'customer_events',
        ['customer_id', 'created_at'],
        unique=False,
    )
    op.add_column(
        'customers',
        sa.Column('lifecycle_status', sa.Enum(*LIFECYCLE_VALUES, name='customer_lifecycle_status'),
                  server_default='NEW', nullable=False,
                  comment='生命周期状态；每次变更同时写一条 customer_events'),
    )


def downgrade() -> None:
    """Downgrade schema.

    ★ 刻意不写 op.drop_index：组合索引 ix_customer_events_customer_id_created_at
      是外键 customer_id 唯一的支撑索引，单独 DROP INDEX 会被 MySQL 拒绝
      （1553 Cannot drop index: needed in a foreign key constraint）。
      drop_table 会把外键与索引一起带走，所以先删表即可。
      已用一次性 scratch 库跑通 upgrade head → downgrade base → upgrade head 往返。
    """
    op.drop_column('customers', 'lifecycle_status')
    op.drop_table('customer_events')
