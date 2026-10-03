"""客户状态机 / 事件流的 Pydantic 契约（TASK-006）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.customer import LifecycleStatus
from app.models.customer_event import ActorType, CustomerEventType, EventSourceType
from app.schemas.evidence import EvidenceValue


class StatusChangeRequest(BaseModel):
    """POST /api/customers/{id}/status 的请求体。

    ★ to_status / actor_type 刻意声明为 str 而不是枚举：
      枚举会让 Pydantic 直接抛 422，而本 TASK 明确规定非法取值必须返回 **400**
      （AC4）。因此在此收原始串，由路由层解析并给出可读的 400 错误。
    """

    to_status: str = Field(description="目标生命周期状态")
    actor_type: str = Field(description="发起方：HUMAN / AI / SYSTEM")
    note: str | None = Field(default=None, max_length=500, description="可选备注，写入事件 metadata")


class CustomerEventItem(BaseModel):
    """单条事件。事件只增不改，字段直接映射 customer_events 行。"""

    id: int
    customer_id: int
    event_type: CustomerEventType
    from_status: LifecycleStatus | None = None
    to_status: LifecycleStatus | None = None
    actor_type: ActorType
    source_type: EventSourceType
    evidence_ref: str | None = None
    metadata_json: dict | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class StatusChangeResponse(BaseModel):
    """状态变更结果。

    changed=False 时 event 为 None —— 同值调用不写事件（AC：状态没变化不产生假历史）。
    """

    customer_id: int
    lifecycle_status: LifecycleStatus = Field(description="变更后的当前状态")
    changed: bool
    event: CustomerEventItem | None = Field(default=None, description="本次写入的事件；未变化时为 null")
    message: str


class CustomerEventListResponse(BaseModel):
    """事件流（按时间倒序）。

    total 是业务数字，走 EvidenceValue：
      · 有事件 -> {value: N, state: VALID}
      · 无事件 -> {value: null, state: NO_DATA}  ★ 不许拿 0 顶（AC5）
    """

    items: list[CustomerEventItem]
    page: int
    page_size: int
    total: EvidenceValue
