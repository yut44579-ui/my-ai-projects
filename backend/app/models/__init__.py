"""ORM 模型包。

D11：V1 数据模型冻结 7 表 —— users / customers / customer_messages /
customer_events / risk_events / import_batches / reports。
本 TASK（工程骨架）不建任何业务表，模型自 TASK-001 起在此包内落地。
"""

from app.db.base import Base

__all__ = ["Base"]
