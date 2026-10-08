"""沟通记录接口的 Pydantic 契约（TASK-003）。

★ 业务数字（该客户的消息条数）一律走 EvidenceValue（D4），前端不许自己算 items.length。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.customer_message import AiStatus, MessageSourceType, MessageType, SenderType
from app.schemas.evidence import EvidenceValue


def _strip_content(value: str) -> str:
    """正文去首尾空白；空正文必须在接口层被拒（不许落一行空消息）。"""
    return value.strip()


class MessageCreateRequest(BaseModel):
    """POST /api/customers/{id}/messages 的入参。

    ★ sender_type 默认 HUMAN，且**只允许 HUMAN / SYSTEM / IMPORT**：
      CUSTOMER 会被接口层以 400 customer_sender_forbidden 拒绝（D9 / AC3）。
    """

    content: str = Field(min_length=1, max_length=4000, description="消息正文，不可为空白")
    sender_type: SenderType = Field(default=SenderType.HUMAN, description="只能 HUMAN / SYSTEM / IMPORT")
    message_type: MessageType = Field(default=MessageType.CHAT)

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, value: str) -> str:
        stripped = _strip_content(value)
        if not stripped:
            raise ValueError("content 不能是空白")
        return stripped


class MessageItem(BaseModel):
    """时间线上的一条消息。"""

    id: int
    customer_id: int
    sender_type: SenderType
    message_type: MessageType
    content: str
    source_type: MessageSourceType = Field(description="写库必带：MANUAL / IMPORT / SYSTEM")
    evidence_ref: str = Field(description="= customer_message:{id}，由代码生成")
    ai_status: AiStatus
    created_at: datetime

    # ── TASK-021：需求 §十二 的三个追溯维度 ──
    # ★ 三态：None = 未记录（界面显示「—」），True/False = 明确记录过。
    #   "还没确认"与"确认了是否"是两件事，不能都显示成"否"。
    human_confirmed: bool | None = Field(
        default=None, description="是否经人工确认；None=未记录，不是「否」"
    )
    auto_sent: bool | None = Field(
        default=None, description="是否自动发送；None=未记录（§十六：AI建议≠自动发送）"
    )
    triggered_event_ref: str | None = Field(
        default=None, description="本条消息触发的业务事件锚点；None=未触发"
    )

    # ── TASK-021：已读状态（需求 §九，钉钉语义）──
    read_at: datetime | None = Field(
        default=None, description="客户已读时间；None=未读或渠道不支持回执"
    )
    read_source: str | None = Field(default=None, description="回执来源渠道；None=没有回执")

    model_config = {"from_attributes": True}


class MessageReadRequest(BaseModel):
    """标记消息已读（TASK-021）。

    ★ 必须给 source：站内没有真实客户渠道（D9 禁止伪造客户行为），
      所以"已读"只能由**真实渠道回传**。没有来源的已读请求一律 400 ——
      否则任何一次误调用都能造出假的"客户已读"。
    """

    source: str = Field(
        max_length=32,
        description=(
            "回执来源渠道，如 WECHAT / WEBSITE；★ 必填。"
            "★ 这里刻意**不写 min_length**：空值交给路由层校验，"
            "以便返回 400 + empty_content（与项目其它空值错误码一致），"
            "而不是 Pydantic 的 422（那是「结构不对」，语义不同）。"
        ),
    )
    read_at: datetime | None = Field(
        default=None, description="渠道给出的已读时间；不传则用服务端当前时间"
    )


class MessageTimelineResponse(BaseModel):
    """GET /api/customers/{id}/messages —— 时间线（时间正序）。

    ★ page / page_size 是分页元数据，不是业务数字，返回裸值；
      total 是该客户的消息条数（业务数字）→ EvidenceValue（AC5）。
    """

    customer_id: int
    items: list[MessageItem] = Field(description="按 (created_at, id) 正序")
    page: int
    page_size: int
    total: EvidenceValue = Field(
        description="该客户的消息条数；一条都没有时 state=NO_DATA、value=null（AC4）"
    )
