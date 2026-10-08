"""P0 规格修正（TASK-021）：扩枚举 + 加获客渠道 + 补沟通记录追溯字段

依据 `原始需求-项目总控提示词.txt` §二 / §九 / §十二。方案见 docs/PLAN_P0_SPEC_FIX.md。

═══════════════════════════════════════════════════════════════════════
【为什么必须手写，而不是 autogenerate（D20 已记的坑）】
═══════════════════════════════════════════════════════════════════════
    MySQL 的 ENUM 列是**字面量列表**：在 Python 侧往 Enum 加成员不会改变列定义，
    Alembic autogenerate 也检测不到。不迁移直接写入新值会报
    1265 "Data truncated for column ..."（TASK-010 实测踩过）。

═══════════════════════════════════════════════════════════════════════
【本次改动的两处关键判断】
═══════════════════════════════════════════════════════════════════════
    ① `evidence_source_type` 这个 ENUM 名被 **5 张表共用**
       （customer_events / customer_handover_events / customer_messages /
         reports / risk_events）。SQLAlchemy 按 name 复用类型，
       所以必须**把所有同名列一起扩**，否则同名两套定义会冲突。
    ② `customers.acquisition_channel` 是**新列**（可空），
       与既有 `customers.source_type` 是**两个维度**：
         source_type          = 数据可不可信（REAL/TEST/MANUAL）
         acquisition_channel  = 客户怎么来的（§九 的 10 种）
       ★ 刻意不合并：合并会破坏既有血缘语义（报告排除测试数据依赖 source_type）。

★ 迁移只**加值不删值**；downgrade 前先清掉新值行再收紧 ENUM（同 D20 写法）。

Revision ID: 4d92b7e1a8c3
Revises: 11feb023df05
Create Date: 2026-10-04 03:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

#: 与其它表一致：微秒精度（D16/D17，同一秒内也要能稳定排序）
Stamp6 = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql')

# revision identifiers, used by Alembic.
revision: str = '4d92b7e1a8c3'
down_revision: Union[str, Sequence[str], None] = '11feb023df05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ── §二 数据来源：3 种 → 8 种 ──
OLD_EVIDENCE_SOURCE = ('MANUAL', 'IMPORT', 'SYSTEM')
NEW_EVIDENCE_SOURCE = ('REAL', 'IMPORT', 'SYNC', 'WEB', 'TEST', 'DEMO', 'MANUAL', 'SYSTEM')

#: 共用 evidence_source_type 的表
EVIDENCE_SOURCE_TABLES = (
    'customer_events',
    'customer_handover_events',
    'customer_messages',
    'reports',
    'risk_events',
)

# ── §九 客户生命周期：7 种 → 11 种 ──
OLD_LIFECYCLE = ('NEW', 'CONTACTED', 'REPLIED', 'ENGAGED', 'QUOTED', 'WON', 'LOST')
NEW_LIFECYCLE = (
    'NEW', 'CONTACTED_UNREAD', 'READ_NO_REPLY', 'CONTACTED', 'REPLIED',
    'ENGAGED', 'QUOTED', 'SILENT', 'REJECTED', 'WON', 'LOST',
)

# ── §九 获客渠道：新枚举 ──
ACQUISITION_CHANNELS = (
    'WEBSITE_ORGANIC', 'WEBSITE_FORM', 'CONTENT_MARKETING', 'SEARCH_DISCOVERY',
    'OUTBOUND', 'REFERRAL', 'EXHIBITION', 'CRM_IMPORT', 'EXCEL_IMPORT', 'OTHER',
)

#: 本次新增的生命周期状态（downgrade 时需要清理）
NEW_LIFECYCLE_VALUES = ('CONTACTED_UNREAD', 'READ_NO_REPLY', 'SILENT', 'REJECTED')


def _enum(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    # ① §二：扩 evidence_source_type（5 张表共用同一个类型名）
    for table in EVIDENCE_SOURCE_TABLES:
        op.execute(
            f"ALTER TABLE `{table}` MODIFY COLUMN `source_type` "
            f"ENUM({_enum(NEW_EVIDENCE_SOURCE)}) NOT NULL"
        )

    # ①b §九：customer_lifecycle_status 这个类型名被**三列**共用 ——
    #     customers.lifecycle_status、customer_events.from_status、customer_events.to_status。
    #     ★ 第一次施工只改了 customers 那一列，导致写新状态时
    #       customer_events 插入报 1265 "Data truncated for column 'to_status'"（实测踩过）。
    #     教训：扩 ENUM 时要按**类型名**找出所有引用它的列，不是只改"表名像的那个"。
    op.execute(
        f"ALTER TABLE `customer_events` MODIFY COLUMN `from_status` "
        f"ENUM({_enum(NEW_LIFECYCLE)}) NULL COMMENT '变更前状态；非状态型事件为 NULL'"
    )
    op.execute(
        f"ALTER TABLE `customer_events` MODIFY COLUMN `to_status` "
        f"ENUM({_enum(NEW_LIFECYCLE)}) NULL COMMENT '变更后状态；非状态型事件为 NULL'"
    )

    # ② §九：扩 customers.lifecycle_status
    op.execute(
        f"ALTER TABLE `customers` MODIFY COLUMN `lifecycle_status` "
        f"ENUM({_enum(NEW_LIFECYCLE)}) NOT NULL DEFAULT 'NEW'"
    )

    # ③ §九：新增获客渠道列（可空 —— 存量客户没有这个信息，NULL 表示未填写，
    #    不硬套 'OTHER'：那会把"不知道"伪装成"知道"）
    op.add_column(
        'customers',
        sa.Column(
            'acquisition_channel',
            sa.Enum(*ACQUISITION_CHANNELS, name='acquisition_channel'),
            nullable=True,
            comment='获客渠道（需求 §九 的 10 种）；NULL=未填写，不是「其他」',
        ),
    )
    op.create_index('ix_customers_acquisition_channel', 'customers', ['acquisition_channel'])

    # ④ §十二：补沟通记录的三个追溯维度（全部可空 ——
    #    "还没确认"与"确认了是否"是两件事，NULL 与 False 必须能分开）
    op.add_column(
        'customer_messages',
        sa.Column('human_confirmed', sa.Boolean(), nullable=True,
                  comment='是否经人工确认；NULL=未记录，False=明确为否'),
    )
    op.add_column(
        'customer_messages',
        sa.Column('auto_sent', sa.Boolean(), nullable=True,
                  comment='是否自动发送；NULL=未记录（§十六：AI建议≠自动发送）'),
    )
    op.add_column(
        'customer_messages',
        sa.Column('triggered_event_ref', sa.String(length=128), nullable=True,
                  comment='本条消息触发的业务事件锚点；NULL=未触发'),
    )

    # ⑤ §九 已读状态（钉钉语义）：只能由真实渠道回传，系统不自己造
    op.add_column(
        'customer_messages',
        sa.Column(
            'read_at',
            Stamp6,
            nullable=True,
            comment='客户已读时间；NULL=未读或渠道不支持回执',
        ),
    )
    op.add_column(
        'customer_messages',
        sa.Column('read_source', sa.String(length=32), nullable=True,
                  comment='回执来源渠道（如 WECHAT / WEBSITE）；★ 无来源的已读请求一律拒绝'),
    )


def downgrade() -> None:
    # 反向顺序清理。★ 先删列，再收紧 ENUM。
    op.drop_column('customer_messages', 'read_source')
    op.drop_column('customer_messages', 'read_at')
    op.drop_column('customer_messages', 'triggered_event_ref')
    op.drop_column('customer_messages', 'auto_sent')
    op.drop_column('customer_messages', 'human_confirmed')

    op.drop_index('ix_customers_acquisition_channel', table_name='customers')
    op.drop_column('customers', 'acquisition_channel')

    # ★ 收紧 lifecycle_status 前必须先清掉新值行：
    #   否则 MySQL 会把它们截断成空串（严格模式报错、非严格模式静默写坏数据）。
    #   把这些客户退回语义最近的既有状态（未读→已联系、已读未回→已联系、
    #   沉默/拒绝→已失效），宁可状态粗糙也不能让数据变空。
    op.execute(
        "UPDATE `customers` SET `lifecycle_status` = 'CONTACTED' "
        "WHERE `lifecycle_status` IN ('CONTACTED_UNREAD', 'READ_NO_REPLY')"
    )
    op.execute(
        "UPDATE `customers` SET `lifecycle_status` = 'LOST' "
        "WHERE `lifecycle_status` IN ('SILENT', 'REJECTED')"
    )
    op.execute(
        f"ALTER TABLE `customers` MODIFY COLUMN `lifecycle_status` "
        f"ENUM({_enum(OLD_LIFECYCLE)}) NOT NULL DEFAULT 'NEW'"
    )

    # 回退 customer_events 的两列（同样先清值再收紧）
    for col in ('from_status', 'to_status'):
        op.execute(
            f"UPDATE `customer_events` SET `{col}` = 'CONTACTED' "
            f"WHERE `{col}` IN ('CONTACTED_UNREAD', 'READ_NO_REPLY')"
        )
        op.execute(
            f"UPDATE `customer_events` SET `{col}` = 'LOST' "
            f"WHERE `{col}` IN ('SILENT', 'REJECTED')"
        )
        op.execute(
            f"ALTER TABLE `customer_events` MODIFY COLUMN `{col}` "
            f"ENUM({_enum(OLD_LIFECYCLE)}) NULL"
        )

    # evidence_source_type 回退：新值在既有表上还没有真实数据，
    # 但为稳妥仍先清空这些列的新值（退回 SYSTEM，语义为"系统计算"）
    for table in EVIDENCE_SOURCE_TABLES:
        op.execute(
            f"UPDATE `{table}` SET `source_type` = 'SYSTEM' "
            f"WHERE `source_type` IN ('REAL', 'SYNC', 'WEB', 'TEST', 'DEMO')"
        )
        op.execute(
            f"ALTER TABLE `{table}` MODIFY COLUMN `source_type` "
            f"ENUM({_enum(OLD_EVIDENCE_SOURCE)}) NOT NULL"
        )
