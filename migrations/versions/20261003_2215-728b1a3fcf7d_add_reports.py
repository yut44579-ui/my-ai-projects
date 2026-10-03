"""add reports

Revision ID: 728b1a3fcf7d
Revises: 36e74cad14a0
Create Date: 2026-10-03 22:15:00.000000

D11 第 7 表 reports：汇报快照（D6：生成时快照，不实时重算）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '728b1a3fcf7d'
down_revision: Union[str, Sequence[str], None] = '36e74cad14a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'reports',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('report_type', sa.Enum('DAILY', 'WEEKLY', 'MANUAL', name='report_type'), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False, comment='统计期间起点（含）'),
        sa.Column('period_end', sa.Date(), nullable=False, comment='统计期间终点（含）'),
        sa.Column('timezone', sa.String(length=64), nullable=False, comment='固定 Asia/Shanghai（D6）'),
        sa.Column('content_json', sa.JSON(), nullable=False, comment='生成时快照：指标 -> EvidenceValue 字典'),
        sa.Column('generated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column(
            'source_type',
            sa.Enum('IMPORT', 'MANUAL', 'SYSTEM', name='report_source_type'),
            nullable=False,
        ),
        sa.Column('generated_by', sa.String(length=64), nullable=False, comment='V1 无登录体系，固定 system'),
        sa.Column(
            'excluded_test_count',
            sa.BigInteger(),
            nullable=False,
            comment='本期被排除在业务数字之外的 TEST 来源客户条数（只计不藏）',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    # 历史报告列表按时间倒序翻页
    op.create_index(op.f('ix_reports_generated_at'), 'reports', ['generated_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_reports_generated_at'), table_name='reports')
    op.drop_table('reports')
