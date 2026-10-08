"""风险事件模型（D11 冻结 7 表之一：risk_events）。

═══════════════════════════════════════════════════════════════════════
【V1 范围：只记**可复现**的风险，不做推断打分】
═══════════════════════════════════════════════════════════════════════
参考产品里写的是「正常 / 需要关注 / 高风险 / 资金风险」四级。V1 只落**两类真实来源**：

    POLICY_BLOCKED —— 代码层策略闸门判定敏感（报价/折扣/合同/收款账户…）
                      ★ 这是 D7 的闸门结论，不是模型打分
    AI_FAILED      —— AI 回复失败，需要人工接手（D10）

★ 刻意**不做**「87% 概率是骗子」这类推断：没有依据的精确数字一律不产出
  （总控提示词 §三：FACT / INFERENCE / SUGGESTION 必须分离）。

═══════════════════════════════════════════════════════════════════════
【证据锚点（D5）】
═══════════════════════════════════════════════════════════════════════
    D5：风险证据用 **message_id** 作为锚点，trigger_text 只作摘要。
    因此本表 evidence_ref = `customer_message:{message_id}`，
    trigger_text 存命中的原话片段（**摘要**，不是全文，也不做二次解释）。

═══════════════════════════════════════════════════════════════════════
【幂等】
═══════════════════════════════════════════════════════════════════════
    同一 (source_type, source_ref) 只允许一条：重复扫描不会产生重复风险。
    由数据库唯一约束兜底，而不是靠代码"记得去重"。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.import_batch import _enum_values
from app.schemas.evidence import SourceType

#: 与 customers / customer_messages 一致：微秒精度，同一秒内也能稳定排序（D16/D17）
Stamp6 = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


class RiskEventType(str, Enum):
    """风险事件类型。★ 冻结枚举，禁止自由文本。"""

    POLICY_BLOCKED = "POLICY_BLOCKED"  # 命中策略闸门（D7）
    AI_FAILED = "AI_FAILED"            # AI 回复失败（D10）
    MANUAL_FLAG = "MANUAL_FLAG"        # 人工主动标记（预留：V1 无标记接口，先占位）


class RiskLevel(str, Enum):
    """风险等级。★ 由**事件类型**决定，不是模型打分。

    参考产品里的「资金风险」在 V1 归入 HIGH（命中收款账户/付款类词表即资金相关）。
    """

    LOW = "LOW"
    ATTENTION = "ATTENTION"
    HIGH = "HIGH"


class RiskStatus(str, Enum):
    """处置状态。OPEN → RESOLVED / DISMISSED（人工处置，V1 只做状态流转，不做工作流引擎）。"""

    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class RiskEvent(Base):
    """客户风险事件。

    ★ 与 customer_events 的分工：那是「发生了什么」的通用事件流；
      本表是「需要人注意的风险」，有等级和处置状态，两者不互相替代。
    """

    __tablename__ = "risk_events"

    # 幂等：同一来源只留一条风险（扫描可反复跑，不会重复堆）
    __table_args__ = (
        UniqueConstraint("source_type", "source_ref", name="uq_risk_events_source"),
        Index("ix_risk_events_customer_status", "customer_id", "status"),
        Index("ix_risk_events_level_status", "level", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    customer_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属客户；客户删除则其风险一并清除",
    )

    event_type: Mapped[RiskEventType] = mapped_column(
        SAEnum(RiskEventType, name="risk_event_type", values_callable=_enum_values),
        nullable=False,
    )
    level: Mapped[RiskLevel] = mapped_column(
        SAEnum(RiskLevel, name="risk_level", values_callable=_enum_values),
        nullable=False,
    )
    status: Mapped[RiskStatus] = mapped_column(
        SAEnum(RiskStatus, name="risk_status", values_callable=_enum_values),
        nullable=False,
        default=RiskStatus.OPEN,
    )

    # 来源可追溯（幂等键的一半）
    source_type: Mapped[SourceType] = mapped_column(
        SAEnum(SourceType, name="evidence_source_type", values_callable=_enum_values),
        nullable=False,
        comment="风险由什么产生：IMPORT=导入数据 / MANUAL=人工 / SYSTEM=代码判定",
    )
    source_ref: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="来源唯一标识，如 customer_message:346"
    )

    trigger_text: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="触发摘要（D5：只作摘要，证据锚点用 message_id）"
    )
    reason: Mapped[str | None] = mapped_column(
        String(512), nullable=True, comment="为什么判为风险（如「命中敏感策略：报价」）"
    )

    evidence_ref: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="证据锚点（D5：= customer_message:{id}）"
    )

    resolved_at: Mapped[datetime | None] = mapped_column(Stamp6, nullable=True)
    resolved_note: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        Stamp6, server_default=func.now(), nullable=False, comment="风险发生时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        Stamp6, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<RiskEvent id={self.id} customer={self.customer_id} "
            f"{self.event_type} {self.level} {self.status}>"
        )
