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

    model_config = {"from_attributes": True}


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
