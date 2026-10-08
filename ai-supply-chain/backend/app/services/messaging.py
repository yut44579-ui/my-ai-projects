"""沟通记录服务：写消息 / 读时间线（TASK-003）。

与框架无关，可被单测直接调用（与 services/importer.py、dedupe.py 同风格）。

★ 两道闸门守 D9「禁止伪造客户消息」：
  1) 服务层 write_message() 直接拒绝 sender_type=CUSTOMER —— 任何代码路径都写不进去；
  2) 接口层把它翻译成 400 + customer_sender_forbidden（明确错误码，AC3）。
  本服务自己产生的 AI / SYSTEM 消息（TASK-004）不受影响。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.customer_event import (
    ActorType,
    CustomerEvent,
    CustomerEventType,
    EventSourceType,
)
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


# 已读回执允许的渠道（TASK-021）。
# ★ 做成白名单而不是自由文本：自由文本会让统计碎成一堆同义值，
#   也无法防止把内部标识当渠道写进来。
READ_SOURCES: tuple[str, ...] = (
    "WECHAT",      # 企业微信 / 公众号
    "WEBSITE",     # 官网
    "EMAIL",       # 邮件
    "CRM",         # CRM 系统
    "MANUAL",      # 人工在界面标注（如客户电话里说看到了）
)


def mark_read(
    db: Session,
    customer_id: int,
    message_id: int,
    *,
    source: str,
    read_at: datetime | None = None,
) -> CustomerMessage:
    """标记一条消息为「客户已读」（TASK-021，需求 §九 钉钉语义）。

    ★ 硬边界：必须给**真实回执来源**。
      站内没有真实客户渠道（D9 禁止伪造客户行为），若允许无来源标记，
      任何一次误调用都能造出"客户已读"——那是编造客户行为。
    幂等：重复标记不会覆盖首次已读时间（首次已读才是事实），
          但会返回当前记录；不重复写事件。
    """
    normalized = (source or "").strip().upper()
    if not normalized:
        raise ApiFailure(ApiErrorCode.EMPTY_CONTENT, "必须给出已读回执来源（source）")
    if normalized not in READ_SOURCES:
        raise ApiFailure(
            ApiErrorCode.INVALID_READ_SOURCE,
            f"不支持的已读回执来源 {source!r}；可用：{' / '.join(READ_SOURCES)}",
        )

    message = db.get(CustomerMessage, message_id)
    if message is None or message.customer_id != customer_id:
        raise ApiFailure(
            ApiErrorCode.MESSAGE_NOT_FOUND,
            f"客户 {customer_id} 下没有消息 {message_id}",
            http_status=404,
        )

    if message.read_at is not None:
        # 幂等：已读过就不改（首次已读时间才是事实）
        return message

    message.read_at = read_at or datetime.now(timezone.utc)
    message.read_source = normalized
    db.flush()

    # ★ 同时写一条客户事件：满足需求 §十一「所有事件必须记录」，
    #   让"客户什么时候读的"出现在客户时间线上，而不只藏在消息行里。
    event = CustomerEvent(
        customer_id=customer_id,
        event_type=CustomerEventType.NOTE,
        actor_type=ActorType.SYSTEM,
        source_type=EventSourceType.SYSTEM,
        metadata_json={
            "note": f"客户已读消息（回执来源 {normalized}）",
            "message_id": message.id,
            "read_source": normalized,
        },
    )
    db.add(event)
    db.flush()
    event.evidence_ref = f"customer_event:{event.id}"

    # 需求 §十二「是否触发业务事件」：把这条消息与它触发的事件挂上
    message.triggered_event_ref = event.evidence_ref

    db.commit()
    db.refresh(message)
    return message


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
