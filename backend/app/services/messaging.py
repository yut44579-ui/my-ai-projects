"""沟通记录服务：写消息 / 读时间线（TASK-003）。

与框架无关，可被单测直接调用（与 services/importer.py、dedupe.py 同风格）。

★ 两道闸门守 D9「禁止伪造客户消息」：
  1) 服务层 write_message() 直接拒绝 sender_type=CUSTOMER —— 任何代码路径都写不进去；
  2) 接口层把它翻译成 400 + customer_sender_forbidden（明确错误码，AC3）。
  本服务自己产生的 AI / SYSTEM 消息（TASK-004）不受影响。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.customer_message import (
    AiStatus,
    CustomerMessage,
    MessageSourceType,
    MessageType,
    SenderType,
    message_evidence_ref,
)
from app.services.errors import ApiErrorCode, ApiFailure

# 接口层允许外部写入的发送方。
# ★ sender_type 枚举（规格冻结）里没有 IMPORT —— 规格说的「只允许写 HUMAN / SYSTEM / IMPORT」
#   落在本表上的含义是：人工写 HUMAN、系统写 SYSTEM、"随导入带入"写 message_type=IMPORT。
# ★ CUSTOMER 不在其中，D9。
API_WRITABLE_SENDERS: frozenset[SenderType] = frozenset({SenderType.HUMAN, SenderType.SYSTEM})

# source_type 由发送方决定，一处定义、禁止调用方随便传（写库必带 source_type）
SOURCE_TYPE_BY_SENDER: dict[SenderType, MessageSourceType] = {
    SenderType.HUMAN: MessageSourceType.MANUAL,
    SenderType.SYSTEM: MessageSourceType.SYSTEM,
    SenderType.AI: MessageSourceType.SYSTEM,  # AI 回复是系统产生的，不是人工录入
    SenderType.CUSTOMER: MessageSourceType.SYSTEM,  # V1 写不进来，仅为字典完备
}


def resolve_source_type(
    sender_type: SenderType, message_type: MessageType
) -> MessageSourceType:
    """消息来源类型：随导入带入（message_type=IMPORT）优先，其余按发送方推导。"""
    if message_type is MessageType.IMPORT:
        return MessageSourceType.IMPORT
    return SOURCE_TYPE_BY_SENDER[sender_type]


def assert_api_writable(sender_type: SenderType) -> None:
    """接口层闸门：只允许 HUMAN / SYSTEM / IMPORT。

    ★ sender_type=CUSTOMER 一律 400 + customer_sender_forbidden ——
      站内没有真实客户渠道，把客户没发过的消息写成 CUSTOMER 就是造假（D9 / AC3）。
    """
    if sender_type is SenderType.CUSTOMER:
        raise ApiFailure(
            ApiErrorCode.CUSTOMER_SENDER_FORBIDDEN,
            "站内没有真实客户渠道，禁止写入 sender_type=CUSTOMER（D9 禁止伪造客户消息）",
            detail={"allowed": sorted(item.value for item in API_WRITABLE_SENDERS)},
        )
    if sender_type not in API_WRITABLE_SENDERS:
        raise ApiFailure(
            ApiErrorCode.INVALID_SENDER_TYPE,
            "sender_type 只能是 "
            f"{sorted(item.value for item in API_WRITABLE_SENDERS)}"
            "（随导入带入的消息请用 message_type=IMPORT）",
        )


def write_message(
    db: Session,
    *,
    customer_id: int,
    content: str,
    sender_type: SenderType,
    message_type: MessageType = MessageType.CHAT,
    ai_status: AiStatus = AiStatus.NONE,
    source_type: MessageSourceType | None = None,
    created_at: datetime | None = None,
) -> CustomerMessage:
    """落库一条消息并**提交**。

    铁律：
      · sender_type=CUSTOMER → 直接抛（服务层最后一道闸门，不依赖调用方自觉）
      · 写库必带 source_type（未显式给时按发送方推导）
      · evidence_ref 由代码生成 customer_message:{id}，禁止手填
    """
    if sender_type is SenderType.CUSTOMER:
        raise ApiFailure(
            ApiErrorCode.CUSTOMER_SENDER_FORBIDDEN,
            "服务层拒绝写入 sender_type=CUSTOMER（D9 禁止伪造客户消息）",
        )

    text = content.strip()
    if not text:
        raise ApiFailure(ApiErrorCode.EMPTY_CONTENT, "消息正文不能为空")

    message = CustomerMessage(
        customer_id=customer_id,
        sender_type=sender_type,
        message_type=message_type,
        content=text,
        source_type=source_type or resolve_source_type(sender_type, message_type),
        evidence_ref="",  # 拿到 id 后立刻回填（禁止手填）
        ai_status=ai_status,
    )
    if created_at is not None:
        message.created_at = created_at

    db.add(message)
    db.flush()  # 需要 id 才能生成 evidence_ref
    message.evidence_ref = message_evidence_ref(message.id)
    db.commit()
    db.refresh(message)
    return message


def list_messages(
    db: Session,
    customer_id: int,
    *,
    page: int,
    page_size: int,
) -> tuple[Sequence[CustomerMessage], int]:
    """时间线：按 (created_at, id) **正序**（老消息在上），分页。"""
    total = db.execute(
        select(func.count())
        .select_from(CustomerMessage)
        .where(CustomerMessage.customer_id == customer_id)
    ).scalar_one()

    rows = (
        db.execute(
            select(CustomerMessage)
            .where(CustomerMessage.customer_id == customer_id)
            .order_by(CustomerMessage.created_at.asc(), CustomerMessage.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return rows, total


def count_for_customer(db: Session, customer_id: int) -> int:
    """该客户的消息条数（详情页「沟通记录」区块用）。"""
    return db.execute(
        select(func.count())
        .select_from(CustomerMessage)
        .where(CustomerMessage.customer_id == customer_id)
    ).scalar_one()


def require_customer(db: Session, customer_id: int) -> Customer:
    """找不到客户一律 404，不许对不存在的客户写消息。"""
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise ApiFailure(
            ApiErrorCode.MESSAGE_NOT_FOUND,
            f"客户 {customer_id} 不存在",
            http_status=404,
        )
    return customer


__all__ = [
    "API_WRITABLE_SENDERS",
    "SOURCE_TYPE_BY_SENDER",
    "assert_api_writable",
    "count_for_customer",
    "list_messages",
    "require_customer",
    "resolve_source_type",
    "write_message",
]
