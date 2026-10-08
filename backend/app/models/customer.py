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


class LifecycleStatus(str, Enum):
    """客户生命周期状态（TASK-006 建立，TASK-021 按需求 §九 补齐）。

    ★ 与 TASK-001 评审砍掉的旧 status 列不是一回事：那是没有流程支撑的死列。
      这里的每个取值都由「状态变更接口 + customer_events 事件」驱动，可追溯。

    ★ TASK-021 对照需求文档 §九 的 8 种状态补齐：
        未联系 / 已联系·未读 / 已读·未回复 / 已回复 / 持续沟通 /
        暂时沉默 / 明确拒绝 / 已成交
      对照结果：
        未联系        -> NEW            （原有）
        已联系·未读    -> CONTACTED_UNREAD（新增）
        已读·未回复    -> READ_NO_REPLY   （新增）
        已回复        -> REPLIED         （原有）
        持续沟通      -> ENGAGED         （原有）
        暂时沉默      -> SILENT          （新增）
        明确拒绝      -> REJECTED        （新增）
        已成交        -> WON             （原有）
      ★ QUOTED（已报价）文档没列，但**保留**：
        它是既有 enum 值且 opportunities 商机阶段与 KPI 口径都在用，
        删枚举值会破坏已有数据。文档说的是"至少支持"，不是"只能是这些"。
      ★ LOST（已失效）保留：既有值，语义上覆盖"最终流失"。

    流转不做强约束（V1 不写死合法路径），但每次变更都必须留事件。
    ★ 已读类状态（CONTACTED_UNREAD / READ_NO_REPLY）**只能由真实渠道写入**
      （企业微信/官网回传），系统不会自己造"客户已读"（D9 禁止伪造客户行为）。
    """

    NEW = "NEW"
    CONTACTED_UNREAD = "CONTACTED_UNREAD"
    READ_NO_REPLY = "READ_NO_REPLY"
    CONTACTED = "CONTACTED"
    REPLIED = "REPLIED"
    ENGAGED = "ENGAGED"
    QUOTED = "QUOTED"
    SILENT = "SILENT"
    REJECTED = "REJECTED"
    WON = "WON"
    LOST = "LOST"


class AcquisitionChannel(str, Enum):
    """**获客渠道**（需求 §九 的 10 种）—— TASK-021 新增。

    ═══════════════════════════════════════════════════════════════════
    ★ 与 CustomerSourceType 是两个不同的维度，**刻意不合并**：
        CustomerSourceType (REAL/TEST/MANUAL) 答的是「这份数据可不可信」
        AcquisitionChannel                    答的是「这个客户怎么来的」

    一次"Excel 导入"的客户：source_type=TEST（数据是测试的），
    acquisition_channel=EXCEL_IMPORT（渠道是 Excel 导入）—— 两个都有意义。
    合并会破坏既有血缘语义（报告排除测试数据、前端黄色告警都依赖 source_type）。
    ═══════════════════════════════════════════════════════════════════

    ★ 可空：既有的 11 个客户没有渠道信息，NULL = 未填写，
       **不硬套一个"其他"** —— 那会把"不知道"伪装成"知道"。
    """

    WEBSITE_ORGANIC = "WEBSITE_ORGANIC"    # 官网自然访问
    WEBSITE_FORM = "WEBSITE_FORM"          # 官网表单
    CONTENT_MARKETING = "CONTENT_MARKETING"  # 内容宣传
    SEARCH_DISCOVERY = "SEARCH_DISCOVERY"  # 搜索发现
    OUTBOUND = "OUTBOUND"                  # 主动开发
    REFERRAL = "REFERRAL"                  # 老客户转介绍
    EXHIBITION = "EXHIBITION"              # 展会
    CRM_IMPORT = "CRM_IMPORT"              # CRM 导入
    EXCEL_IMPORT = "EXCEL_IMPORT"          # Excel 导入
    OTHER = "OTHER"                        # 其他


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
    # TASK-021：获客渠道（需求 §九 的 10 种）。
    # ★ 与 source_type 是不同维度：这里答"客户怎么来的"，source_type 答"数据可不可信"。
    # ★ 可空：存量客户没有这个信息，NULL=未填写，不硬套"其他"。
    acquisition_channel: Mapped[AcquisitionChannel | None] = mapped_column(
        SAEnum(AcquisitionChannel, name="acquisition_channel", values_callable=_enum_values),
        nullable=True,
        index=True,
        comment="获客渠道（需求 §九 的 10 种）；NULL=未填写，不是「其他」",
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

    # TASK-006：生命周期状态。默认 NEW；每次变更由 POST /customers/{id}/status 写入，
    # 且必须同时落一条 customer_events（历史不许丢）。
    # ★ 并行线合并：与上面的 handover_state 并存 —— 两者是不同口径，互不替代。
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
