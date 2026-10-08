"""SQLAlchemy 2.x 声明式基类。

本 TASK 不建任何业务表（见 docs/DECISIONS.md D11，
业务表自 TASK-001 起逐步落地）。此处只提供 Base 与通用 Mixin，
供 Alembic autogenerate 识别 metadata。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """所有 ORM 模型的公共基类。"""


class TimestampMixin:
    """统一的创建/更新时间列（MySQL 侧由服务端时间维护）。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
