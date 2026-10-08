"""人工接管接口的 Pydantic 契约（TASK-005）。

★ 业务数字（事件条数）走 EvidenceValue（D4）；状态、布尔、时间戳是元数据，返回裸值。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.handover import HandoverActorType, HandoverEventType, HandoverState
from app.schemas.evidence import EvidenceValue, SourceType


class HandoverActionRequest(BaseModel):
    """接管 / 交回的可选请求体。都不传也能调用（人工点击即可）。"""

    note: str | None = Field(default=None, max_length=2000, description="人工备注，写进事件")
    actor_type: HandoverActorType = Field(
        default=HandoverActorType.HUMAN,
        description="发起方。★ 传 AI 会被 403 拒绝（D7/D8：AI 不许自行改接管状态）",
    )


class HandoverEventItem(BaseModel):
    """一条接管事件留痕。"""

    id: int
    event_type: HandoverEventType
    from_state: HandoverState | None = None
    to_state: HandoverState
    actor_type: HandoverActorType
    source_type: SourceType
    reason: str | None = None
    metadata_json: dict | None = None
    evidence_ref: str
    created_at: datetime

    model_config = {"from_attributes": True}


class HandoverActionResponse(BaseModel):
    """POST takeover / resume 的返回：变更后的状态 + 本次事件。

    changed=False 表示状态本来就等于目标值（no-op，未写事件），前端据此提示而不是报错。
    """

    customer_id: int
    state: HandoverState
    ai_auto_reply_allowed: bool = Field(
        description="★ 统一判据：只有 AUTO 为 true。AI 闸门与前端都用它决定要不要自动回复"
    )
    changed: bool
    message: str
    event: HandoverEventItem | None = Field(
        default=None, description="本次状态变化产生的事件；no-op 时为 null"
    )


class HandoverHistoryResponse(BaseModel):
    """GET handover 的返回：当前状态 + 完整变更历史。"""

    customer_id: int
    state: HandoverState
    ai_auto_reply_allowed: bool
    events: list[HandoverEventItem] = Field(
        default_factory=list, description="按时间正序的完整流转历史（AC5：当前值 + 历史都要有）"
    )
    event_count: EvidenceValue = Field(
        description="事件条数（业务数字）。★ 从未变更过 → NO_DATA，不是 0"
    )
    since: datetime | None = Field(
        default=None, description="进入当前状态的时刻（= 最后一条事件时间）；从未变更 → null"
    )
    updated_at: datetime
