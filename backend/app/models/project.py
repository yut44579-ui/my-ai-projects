"""项目模型（TASK-017）。★ D28 解冻新增的表。

═══════════════════════════════════════════════════════════════════════
【为什么项目与客户是 N:N，为什么要一张关联表】
═══════════════════════════════════════════════════════════════════════
    一个项目通常服务**多个客户**（如"某园区智能化改造"覆盖 3 家）；
    一个客户也会参与**多个项目**。这是标准 N:N，不是从属关系
    —— 所以没有把 project_id 塞进 customers（塞进去就只能一对一）。

    ★ 关联表 vs JSON 数组的取舍（刻意选关联表）：
      把 customer_ids 存成 JSON 列更省一张表，但**没法建索引**，
      "这个客户参与了哪些项目"只能全表扫。那是客户详情页的真实需求，
      所以宁可多一张关联表换可索引的关系 —— 这是有依据的取舍，不是过度设计。
      （对比 D20：客户访问只有归属关系，没有反向查询需求，所以没建表。）

═══════════════════════════════════════════════════════════════════════
【不做工作流引擎（D8 同一原则）】
═══════════════════════════════════════════════════════════════════════
    状态流转用固定枚举 + 显式接口，合法性写死在 services/project.py。

═══════════════════════════════════════════════════════════════════════
【进度是人工填的还是算出来的】
═══════════════════════════════════════════════════════════════════════
    ★ progress 由**人工填写**（0~100），不是按任务数推算 ——
      V1 没有任务表，任何"自动进度"都会是无依据的推断（§三）。
      字段可空：NULL = 还没评估，与"0%"是两件事。
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.import_batch import _enum_values

Stamp6 = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


class ProjectStatus(str, Enum):
    """项目状态。★ 冻结枚举，禁止自由文本。"""

    PLANNING = "PLANNING"        # 筹备中
    IN_PROGRESS = "IN_PROGRESS"  # 进行中
    ON_HOLD = "ON_HOLD"          # 已暂停
    DELIVERED = "DELIVERED"      # 已交付
    CLOSED = "CLOSED"            # 已结项
    CANCELLED = "CANCELLED"      # 已取消（旁支终态）


class ProjectPriority(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


#: 进行中的状态（看板"活跃项目"口径）
ACTIVE_STATUSES: frozenset[ProjectStatus] = frozenset(
    {ProjectStatus.PLANNING, ProjectStatus.IN_PROGRESS, ProjectStatus.ON_HOLD}
)

#: 终态（结项 / 取消）
CLOSED_STATUSES: frozenset[ProjectStatus] = frozenset(
    {ProjectStatus.DELIVERED, ProjectStatus.CLOSED, ProjectStatus.CANCELLED}
)

#: 主线推进顺序（CANCELLED 是旁支，不进主线）
STATUS_ORDER: tuple[ProjectStatus, ...] = (
    ProjectStatus.PLANNING,
    ProjectStatus.IN_PROGRESS,
    ProjectStatus.ON_HOLD,
    ProjectStatus.DELIVERED,
    ProjectStatus.CLOSED,
)
STATUS_RANK: dict[ProjectStatus, int] = {s: i + 1 for i, s in enumerate(STATUS_ORDER)}


class Project(Base):
    """一个项目。"""

    __tablename__ = "projects"

    __table_args__ = (
        Index("ix_projects_status", "status"),
        Index("ix_projects_dates", "start_date", "due_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, comment="项目编号；人工填，不自动生成"
    )

    status: Mapped[ProjectStatus] = mapped_column(
        SAEnum(ProjectStatus, name="project_status", values_callable=_enum_values),
        nullable=False,
        default=ProjectStatus.PLANNING,
    )
    priority: Mapped[ProjectPriority] = mapped_column(
        SAEnum(ProjectPriority, name="project_priority", values_callable=_enum_values),
        nullable=False,
        default=ProjectPriority.MEDIUM,
    )

    #: 人工填写的进度；NULL = 未评估（与 0% 不同）
    progress: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="进度 0~100；由人工填写，★ 不是按任务数推算"
    )

    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="计划完成日")
    delivered_at: Mapped[datetime | None] = mapped_column(
        Stamp6, nullable=True, comment="实际交付/结项时间"
    )

    owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    evidence_ref: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="证据锚点 = project:{id}"
    )

    created_at: Mapped[datetime] = mapped_column(Stamp6, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        Stamp6, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Project id={self.id} {self.name!r} {self.status}>"


class ProjectCustomer(Base):
    """项目 ↔ 客户 关联表（N:N）。

    ★ 为什么不做软删除：关联被移除就是移除，历史由 customer_events 记录
      （那里是事件流，适合留痕）；关联表只表达"当前关系"。
    """

    __tablename__ = "project_customers"

    __table_args__ = (
        UniqueConstraint("project_id", "customer_id", name="uq_project_customer"),
        Index("ix_project_customers_customer", "customer_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    #: 该客户在这个项目里的角色（如"甲方""使用方"），自由文本但短
    role: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(Stamp6, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ProjectCustomer project={self.project_id} customer={self.customer_id}>"
