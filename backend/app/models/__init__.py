"""ORM 模型包。

D11：V1 数据模型冻结 7 表 —— users / customers / customer_messages /
customer_events / risk_events / import_batches / reports。
TASK-001 落地其中 2 张：import_batches、customers（其余按 TASK 逐步来）。

★ 模型必须在包内 import 一次，Alembic autogenerate 才能看到表（migrations/env.py 只 import 本包）。
"""

from app.db.base import Base
from app.models.customer import Customer, CustomerSourceType, DedupeState
from app.models.import_batch import (
    BatchSourceType,
    ImportBatch,
    ImportBatchStatus,
    SkipReason,
)
from app.models.report import REPORT_TIMEZONE, Report, ReportSourceType, ReportType

__all__ = [
    "Base",
    "BatchSourceType",
    "Customer",
    "CustomerSourceType",
    "DedupeState",
    "ImportBatch",
    "ImportBatchStatus",
    "REPORT_TIMEZONE",
    "Report",
    "ReportSourceType",
    "ReportType",
    "SkipReason",
]
