"""商机模型（TASK-016）。★ D28 解冻新增的表。

═══════════════════════════════════════════════════════════════════════
【与 customers.lifecycle_status 的关系（别搞混）】
═══════════════════════════════════════════════════════════════════════
    customers.lifecycle_status  = 「这个**客户**整体推进到哪一步」（客户级，只能有一个值）
    opportunities.stage         = 「这**一笔生意**谈到哪一步」（商机级，一个客户可以有多笔）

一个客户可能同时有 3 笔商机（不同产品线），各自处在不同阶段 ——
这就是为什么必须单独建表，而不是把阶段塞进 customers（D28 已记录理由）。

★ 阶段枚举刻意与客户生命周期**同名同序**，但含义更细（多了 VALIDATING 验证中、CLOSED_LOST 已丢单）。
  同名是为了让两处口径可以直接对照，不用再记一套词汇。

═══════════════════════════════════════════════════════════════════════
【金额】
═══════════════════════════════════════════════════════════════════════
    金额是**业务数字**，对外一律 EvidenceValue（D4）。
    库里存 Decimal(18,2)，**不做汇率换算**（只存币种代码，换算需要汇率源，D28 明确不做）。

═══════════════════════════════════════════════════════════════════════
【不做工作流引擎（D8 同一原则）】
═══════════════════════════════════════════════════════════════════════
    阶段流转用**固定枚举 + 显式接口**，流转合法性写死在 services/opportunity.py，
    不做可配置状态机。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.import_batch import _enum_values

#: 微秒精度，与其它表一致（D16）
Stamp6 = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


class OpportunityStage(str, Enum):
    """商机阶段。★ 冻结枚举，禁止自由文本。

    顺序即推进顺序；CLOSED_LOST 是**终态旁支**（不算赢单）。
    """

    NEW = "NEW"                    # 新建
    CONTACTED = "CONTACTED"        # 已联系
    QUALIFIED = "QUALIFIED"        # 已确认需求
    VALIDATING = "VALIDATING"      # 方案/报价验证中
    QUOTED = "QUOTED"              # 已报价
    NEGOTIATING = "NEGOTIATING"    # 谈判中
    WON = "WON"                    # 赢单
    CLOSED_LOST = "CLOSED_LOST"    # 丢单（旁支终态）


class OpportunityPriority(str, Enum):
    """优先级。用于看板排序，不是 AI 打分（§三 FACT/INFERENCE 分离）。"""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


#: 推进顺序（CLOSED_LOST 不在主线里）
STAGE_ORDER: tuple[OpportunityStage, ...] = (
    OpportunityStage.NEW,
    OpportunityStage.CONTACTED,
    OpportunityStage.QUALIFIED,
    OpportunityStage.VALIDATING,
    OpportunityStage.QUOTED,
    OpportunityStage.NEGOTIATING,
    OpportunityStage.WON,
)

#: 每个阶段的 rank（累计口径用，与数据分析页漏斗同一套思路）
STAGE_RANK: dict[OpportunityStage, int] = {s: i + 1 for i, s in enumerate(STAGE_ORDER)}

#: 已结束的阶段（不再接受普通推进）
CLOSED_STAGES: frozenset[OpportunityStage] = frozenset(
    {OpportunityStage.WON, OpportunityStage.CLOSED_LOST}
)

#: 赢单口径：只有 WON 算赢单
WON_STAGE = OpportunityStage.WON


class Opportunity(Base):
    """一笔商机。"""

    __tablename__ = "opportunities"

    __table_args__ = (
        Index("ix_opportunities_customer", "customer_id"),
        Index("ix_opportunities_stage", "stage"),
        # 看板按 (stage, 预计成交日) 取数
        Index("ix_opportunities_stage_expected", "stage", "expected_close_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    customer_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属客户；客户删除则其商机一并清除",
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False, comment="商机名称，如「H800 采购意向」")

    stage: Mapped[OpportunityStage] = mapped_column(
        SAEnum(OpportunityStage, name="opportunity_stage", values_callable=_enum_values),
        nullable=False,
        default=OpportunityStage.NEW,
    )
    priority: Mapped[OpportunityPriority] = mapped_column(
        SAEnum(OpportunityPriority, name="opportunity_priority", values_callable=_enum_values),
        nullable=False,
        default=OpportunityPriority.MEDIUM,
    )

    # ── 金额：业务数字，对外走 EvidenceValue ──
    amount: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True, comment="预计金额；NULL = 还没估出来（不是 0）"
    )
    currency: Mapped[str] = mapped_column(
        String(8), nullable=False, default="CNY", comment="币种代码；★ 不做汇率换算"
    )
    #: 赢单后的实际金额（与 expected 区分：预计 vs 实际）
    won_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True, comment="实际成交金额；未赢单为 NULL"
    )

    probability: Mapped[int | None] = mapped_column(
        nullable=True, comment="赢单概率 0~100；★ 由人工填写，不是模型预测（§三）"
    )

    expected_close_date: Mapped[date | None] = mapped_column(
        Date, nullable=True, comment="预计成交日；NULL = 未定"
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        Stamp6, nullable=True, comment="赢单/丢单时间"
    )

    owner: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="负责人（V1 存名字文本）")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="软删除；不用物理删除以保留历史"
    )

    evidence_ref: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="证据锚点 = opportunity:{id}"
    )

    created_at: Mapped[datetime] = mapped_column(Stamp6, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        Stamp6, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Opportunity id={self.id} customer={self.customer_id} {self.stage} {self.amount}>"
