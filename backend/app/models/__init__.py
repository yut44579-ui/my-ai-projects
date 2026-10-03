"""ORM 模型包。

D11：V1 数据模型冻结 7 表 —— users / customers / customer_messages /
customer_events / risk_events / import_batches / reports。
已落地：import_batches、customers（TASK-001）、customer_events（TASK-006）。

★ 模型必须在包内 import 一次，Alembic autogenerate 才能看到表（migrations/env.py 只 import 本包）。
"""

from app.db.base import Base
from app.models.customer import Customer, CustomerSourceType, DedupeState, LifecycleStatus
from app.models.customer_event import (
    ActorType,
    CustomerEvent,
    CustomerEventType,
    EventSourceType,
)
from app.models.import_batch import (
    BatchSourceType,
    ImportBatch,
    ImportBatchStatus,
    SkipReason,
)

__all__ = [
    "ActorType",
    "Base",
    "BatchSourceType",
    "Customer",
    "CustomerEvent",
    "CustomerEventType",
    "CustomerSourceType",
    "DedupeState",
    "EventSourceType",
    "ImportBatch",
    "ImportBatchStatus",
    "LifecycleStatus",
    "SkipReason",
]
