"""ORM 模型包。

D11：V1 数据模型冻结 7 表 —— users / customers / customer_messages /
customer_events / risk_events / import_batches / reports。
TASK-001 落地其中 2 张：import_batches、customers（其余按 TASK 逐步来）。

★ 模型必须在包内 import 一次，Alembic autogenerate 才能看到表（migrations/env.py 只 import 本包）。
"""

from app.db.base import Base
from app.models.customer import Customer, CustomerSourceType, DedupeState
from app.models.customer_message import (
    AiStatus,
    CustomerMessage,
    MessageSourceType,
    MessageType,
    SenderType,
    message_evidence_ref,
)
from app.models.import_batch import (
    BatchSourceType,
    ImportBatch,
    ImportBatchStatus,
    SkipReason,
)

__all__ = [
    "AiStatus",
    "Base",
    "BatchSourceType",
    "Customer",
    "CustomerMessage",
    "CustomerSourceType",
    "DedupeState",
    "ImportBatch",
    "ImportBatchStatus",
    "MessageSourceType",
    "MessageType",
    "SenderType",
    "SkipReason",
    "message_evidence_ref",
]
