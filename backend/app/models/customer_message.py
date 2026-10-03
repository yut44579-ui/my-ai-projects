"""客户沟通记录模型（D11 第 3 表，TASK-003 落地）。

★ 铁律（D9「禁止伪造客户消息」）：站内没有真实客户渠道，
  **本服务永远不许写入 sender_type=CUSTOMER 的行**。接口层用代码闸门拒绝
  （见 api/routes/messages.py 的 customer_sender_forbidden，AC3）。
  CUSTOMER 枚举保留，是为了将来真的接了客户渠道后写入时不用改表结构。

★ evidence_ref 由代码生成为 customer_message:{id}，禁止手填。
  D5 规定「风险证据以 message_id 为锚点」，本表就是那个 message_id 的来源。

★ ai_status 是「AI 回复状态机」的字段（TASK-004 使用）：
    NONE            —— 人工 / 系统 / 导入写入的消息，与 AI 无关
    REQUESTED       —— 已向 LLM 发起请求（★ V1 是同步调用，成功后直接落 REPLIED，
                       中间态不单独落库；枚举保留给异步化，不是死值）
    REPLIED         —— AI 回复已落库（sender_type=AI）
    FAILED          —— LLM 调用失败，本轮**没有** AI 回复（D10：如实显示，禁止编造）
    HUMAN_REQUIRED  —— 代码层策略闸门命中敏感内容，转人工（D7：不调 LLM）
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import BigInteger, Enum as SAEnum, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.customer import Stamp6
from app.models.import_batch import _enum_values


class SenderType(str, Enum):
    """消息发送方。★ CUSTOMER 只留给将来的真实客户渠道，V1 禁止写入。"""

    CUSTOMER = "CUSTOMER"  # ★ V1 不许写入（D9）
    HUMAN = "HUMAN"
    AI = "AI"
    SYSTEM = "SYSTEM"


class MessageType(str, Enum):
    """消息类型。CHAT=对话；NOTE=备注/系统说明；IMPORT=随导入带入的原始信息。"""

    CHAT = "CHAT"
    NOTE = "NOTE"
    IMPORT = "IMPORT"


class MessageSourceType(str, Enum):
    """消息来源类型（与 schemas/evidence.py 的 SourceType 取值一致）。

    ★ 写库必须带 source_type（硬规则）：人工发送=MANUAL，导入带入=IMPORT，系统产生=SYSTEM。
    """

    MANUAL = "MANUAL"
    IMPORT = "IMPORT"
    SYSTEM = "SYSTEM"


class AiStatus(str, Enum):
    """AI 回复状态（见模块 docstring）。"""

    NONE = "NONE"
    REQUESTED = "REQUESTED"
    REPLIED = "REPLIED"
    FAILED = "FAILED"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


# 写库字段与证据锚点（evidence_ref）的生成规则：一处定义，两处（API / 测试）共用
EVIDENCE_REF_PREFIX = "customer_message:"


def message_evidence_ref(message_id: int) -> str:
    """消息的证据锚点 —— 由 id 生成，禁止手填。"""
    return f"{EVIDENCE_REF_PREFIX}{message_id}"


class CustomerMessage(Base):
    """客户沟通记录：时间线的一行。"""

    __tablename__ = "customer_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    customer_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,  # ★ 规格要求加索引（时间线按客户查）
    )

    sender_type: Mapped[SenderType] = mapped_column(
        SAEnum(SenderType, name="message_sender_type", values_callable=_enum_values),
        nullable=False,
    )
    message_type: Mapped[MessageType] = mapped_column(
        SAEnum(MessageType, name="message_type", values_callable=_enum_values),
        nullable=False,
        default=MessageType.CHAT,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="消息正文（非空）")

    source_type: Mapped[MessageSourceType] = mapped_column(
        SAEnum(MessageSourceType, name="message_source_type", values_callable=_enum_values),
        nullable=False,
        comment="写库必带：MANUAL=人工 / IMPORT=导入 / SYSTEM=系统",
    )
    evidence_ref: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="= customer_message:{id}，由代码生成（flush 拿到 id 后立刻回填）",
    )

    ai_status: Mapped[AiStatus] = mapped_column(
        SAEnum(AiStatus, name="message_ai_status", values_callable=_enum_values),
        nullable=False,
        default=AiStatus.NONE,
    )

    # DATETIME(6)：同一秒内的多条消息也能稳定排出先后（时间线必须严格正序）
    created_at: Mapped[datetime] = mapped_column(
        Stamp6,
        server_default=func.now(),
        nullable=False,
        comment="消息时间（时间线排序键）",
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<CustomerMessage id={self.id} customer={self.customer_id} "
            f"sender={self.sender_type} ai={self.ai_status}>"
        )
