"""客户接口的 Pydantic 契约（AC5/AC6）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.customer import CustomerSourceType, DedupeState
from app.models.handover import HandoverState
from app.schemas.evidence import EvidenceValue


class CustomerItem(BaseModel):
    id: int
    name: str
    company_name: str | None = None
    phone: str | None = None
    email: str | None = None
    region: str | None = None
    note: str | None = None
    batch_id: int | None = Field(default=None, description="来源批次；MANUAL 录入为 null")
    source_type: CustomerSourceType
    evidence_ref: str | None = Field(default=None, description="= import_batch:{batch_id}")
    dedupe_state: DedupeState
    handover_state: HandoverState = Field(
        default=HandoverState.AUTO,
        description="人工接管三态（D8/TASK-005）：AUTO / HUMAN_REQUIRED / HUMAN_ACTIVE。"
        "列表与详情都带它，前端据此打「需人工处理」标记（AC1）",
    )
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CustomerListResponse(BaseModel):
    """★ page / page_size 是分页元数据，不是业务数字，正常返回裸值（AC6）。

    业务数字（客户总数、测试数据条数）一律走 EvidenceValue。
    """

    items: list[CustomerItem]
    page: int
    page_size: int
    total: EvidenceValue = Field(description="当前筛选条件下的客户总数（业务数字）")
    test_count: EvidenceValue = Field(
        description="全库 TEST 来源客户条数（业务数字）——前端据此显示黄色 TEST banner"
    )
