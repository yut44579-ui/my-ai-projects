"""客户接口的 Pydantic 契约（AC5/AC6）。

TASK-002 在此**追加**详情页 / 事件流 / 指标卡的契约（不改 TASK-001 已有的两个模型）：
  CustomerItem            —— 基础字段（列表与详情共用）
  CustomerListResponse    —— TASK-001 原样
  CustomerDetailResponse  —— TASK-002：基础字段 + 来源可追溯 + 状态占位 + 三个显式空区块
  CustomerTimelineResponse—— TASK-002：只返回真实事件，未接入的类型一律 NO_DATA
  CustomerStatsResponse   —— TASK-002：列表页四个指标卡
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

# ★ 并行线合并：TASK-005（handover_state）与 TASK-006（lifecycle_status）的字段取并集。
from app.models.customer import CustomerSourceType, DedupeState, LifecycleStatus
from app.models.handover import HandoverState
from app.models.import_batch import ImportBatchStatus
from app.schemas.evidence import EvidenceValue, ValueState


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
    lifecycle_status: LifecycleStatus = Field(
        default=LifecycleStatus.NEW, description="TASK-006：生命周期状态（默认 NEW）"
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


# ─────────────────────── TASK-002：客户详情 ───────────────────────


class BatchRef(BaseModel):
    """客户来源批次的可追溯信息（详情页「来源与可追溯」卡）。

    ★ 只读 TASK-001 已有的 import_batches 行；MANUAL 录入的客户没有批次，此处为 null。
    """

    batch_id: int
    filename: str
    status: ImportBatchStatus
    source_type: CustomerSourceType
    imported_at: datetime
    evidence_ref: str = Field(description="= import_batch:{batch_id}，点击可跳到批次详情")


class SourceTrace(BaseModel):
    """来源：source_type + evidence_ref + batch_id + 该批次的批次信息。"""

    source_type: CustomerSourceType
    evidence_ref: str | None = None
    batch_id: int | None = Field(default=None, description="MANUAL 录入为 null")
    batch: BatchRef | None = Field(default=None, description="批次信息；无批次时为 null")


class FollowUpStatus(BaseModel):
    """跟进状态 —— **V1 没有客户生命周期状态**。

    customers 表已按评审砍掉 status 列，本 TASK 不许偷偷加回；
    这里返回的是显式占位：available=False 说明「流程还没接入」，
    前端据此显示「未开始跟进（V1 暂无流程）」，等 TASK-006 接入状态机。
    """

    available: bool = Field(default=False, description="V1 恒为 false：状态机还没接入")
    label: str = Field(description="展示文案，如「未开始跟进」")
    state: str = Field(description="占位状态码，恒为 NOT_STARTED（不是数据库字段）")
    note: str = Field(description="为什么没有真实状态：等 TASK-006 接入状态机")


class CustomerDetailResponse(CustomerItem):
    """GET /api/customers/{id} —— 基础字段（继承）+ 来源 + 状态占位 + 三个显式空区块。

    ★ 沟通记录 / 风险提醒 / 相关批次明细在本 TASK 全是 NO_DATA 空态：
      不许返回假数据、不许返回 0 冒充。
    """

    source: SourceTrace
    status: FollowUpStatus
    conversations: EvidenceValue = Field(description="沟通记录：本 TASK 恒为 NO_DATA")
    risks: EvidenceValue = Field(description="风险提醒：本 TASK 恒为 NO_DATA")
    batch_details: EvidenceValue = Field(description="相关批次明细：本 TASK 恒为 NO_DATA")


# ─────────────────────── TASK-002：事件流（占位） ───────────────────────


class TimelineEventKind(str, Enum):
    """V1 真实存在的事件类型（全部来自已有数据列，不含任何推断）。"""

    CUSTOMER_CREATED = "CUSTOMER_CREATED"
    SOURCE_IMPORT = "SOURCE_IMPORT"
    DEDUPE_PENDING_REVIEW = "DEDUPE_PENDING_REVIEW"


class TimelineEvent(BaseModel):
    kind: TimelineEventKind
    title: str
    occurred_at: datetime = Field(description="来自真实列：first_seen_at / batch.imported_at")
    evidence_ref: str
    detail: str | None = None


class TimelineUnavailable(BaseModel):
    """V1 还没有真实事件的类型 —— 一律如实说明「待 TASK-003 接入」，绝不编造事件。"""

    kind: str = Field(description="AI_CONVERSATION / HUMAN_FOLLOW_UP")
    label: str
    state: ValueState = Field(description="恒为 NO_DATA")
    reason: str


class CustomerTimelineResponse(BaseModel):
    customer_id: int
    events: list[TimelineEvent] = Field(description="只放真实事件（来源导入 / 去重状态）")
    events_count: EvidenceValue
    unavailable: list[TimelineUnavailable] = Field(
        description="尚未接入的类型：显式 NO_DATA，不用空数组冒充「已接入但为空」"
    )


# ─────────────────────── TASK-002：列表页指标卡 ───────────────────────


class CustomerStatsResponse(BaseModel):
    """GET /api/customers/stats —— 四个指标卡。

    ★ 口径与列表页完全一致（同一个 q / source_type 过滤）：
      该口径下一条数据都没有 → 四项全部 NO_DATA（界面显示「—」+「暂无数据」），
      绝不返回 0 冒充"真实为零"；口径内确有数据时，0 就是合法的 0。
    """

    scope: str = Field(description="口径说明，如 source_type=REAL|q=张")
    total: EvidenceValue = Field(description="客户总数")
    test_count: EvidenceValue = Field(description="测试数据（source_type=TEST）")
    pending_review: EvidenceValue = Field(description="待人工裁决（dedupe_state=PENDING_REVIEW）")
    new_this_week: EvidenceValue = Field(description="本周新增（first_seen_at 在本周内）")
