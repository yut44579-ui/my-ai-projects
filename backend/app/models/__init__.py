"""ORM 模型包。

D11：V1 数据模型冻结 7 表 —— users / customers / customer_messages /
customer_events / risk_events / import_batches / reports。
已落地：import_batches、customers（TASK-001）、customer_events（TASK-006）。

★ 模型必须在包内 import 一次，Alembic autogenerate 才能看到表（migrations/env.py 只 import 本包）。
"""

from app.db.base import Base
# ★ 并行线合并（integration）：各线导出取并集，四条线一个都不丢。
#   handover.*        —— TASK-005：人工接管三态 + 接管事件
#   customer_event.*  —— TASK-006：客户状态机通用事件
#   customer.LifecycleStatus —— TASK-006：生命周期状态枚举
#   customer_message.* —— TASK-003/004：沟通记录 + AI 回复
#   report.*          —— TASK-007：自动汇报
from app.models.customer import Customer, CustomerSourceType, DedupeState, LifecycleStatus
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
from app.models.handover import (
    CustomerHandoverEvent,
    HandoverActorType,
    HandoverEventType,
    HandoverState,
)
from app.models.import_batch import (
    BatchSourceType,
    ImportBatch,
    ImportBatchStatus,
    SkipReason,
)
from app.models.report import REPORT_TIMEZONE, Report, ReportSourceType, ReportType

__all__ = [
    "ActorType",
    "AiStatus",
    "Base",
    "BatchSourceType",
    "Customer",
    "CustomerEvent",
    "CustomerEventType",
    "CustomerHandoverEvent",
    "CustomerMessage",
    "CustomerSourceType",
    "DedupeState",
    "EventSourceType",
    "HandoverActorType",
    "HandoverEventType",
    "HandoverState",
    "ImportBatch",
    "ImportBatchStatus",
    "LifecycleStatus",
    "MessageSourceType",
    "MessageType",
    "REPORT_TIMEZONE",
    "Report",
    "ReportSourceType",
    "ReportType",
    "SenderType",
    "SkipReason",
    "message_evidence_ref",
]
