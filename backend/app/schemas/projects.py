"""项目接口的 Pydantic 契约（TASK-017）。

★ 业务数字（计数、进度汇总）走 EvidenceValue（D4）；
  项目名、状态、日期、负责人是元数据，返回裸值。
★ 进度是**人工填写**的，不是按任务数推算（V1 没有任务表）。
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.project import ProjectPriority, ProjectStatus
from app.schemas.evidence import EvidenceValue


class ProjectCustomerLink(BaseModel):
    """项目关联的客户。"""

    customer_id: int
    customer_name: str
    role: str | None = Field(default=None, description="该客户在项目里的角色，如「甲方」「使用方」")
    evidence_ref: str


class ProjectItem(BaseModel):
    """一个项目。"""

    id: int
    name: str
    code: str | None = None
    status: ProjectStatus
    status_label: str
    priority: ProjectPriority
    progress: int | None = Field(default=None, description="进度 0~100；★ 人工填写，NULL=未评估")
    start_date: date | None = None
    due_date: date | None = None
    delivered_at: datetime | None = None
    owner: str | None = None
    description: str | None = None
    customer_count: int = Field(description="关联客户数")
    customers: list[ProjectCustomerLink] = Field(default_factory=list)
    is_overdue: bool = Field(description="是否已逾期（有 due_date、未结束、且已过计划完成日）")
    is_active: bool
    evidence_ref: str


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=64)
    status: ProjectStatus = ProjectStatus.PLANNING
    priority: ProjectPriority = ProjectPriority.MEDIUM
    progress: int | None = Field(default=None, ge=0, le=100)
    start_date: date | None = None
    due_date: date | None = None
    owner: str | None = Field(default=None, max_length=64)
    description: str | None = None
    customer_ids: list[int] = Field(
        default_factory=list, description="关联客户 id 列表（N:N）；不存在的 id 会被拒绝"
    )


class ProjectUpdateRequest(BaseModel):
    """更新项目（只传要改的字段）。★ status 请走 /status，以便校验流转合法性。"""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=64)
    priority: ProjectPriority | None = None
    progress: int | None = Field(default=None, ge=0, le=100)
    start_date: date | None = None
    due_date: date | None = None
    owner: str | None = Field(default=None, max_length=64)
    description: str | None = None


class ProjectStatusRequest(BaseModel):
    status: ProjectStatus
    note: str | None = Field(default=None, max_length=500)


class ProjectStatusResponse(BaseModel):
    project: ProjectItem
    changed: bool = Field(description="false = 状态本来就等于目标值（no-op，未写任何变更）")
    message: str


class ProjectListResponse(BaseModel):
    items: list[ProjectItem]
    page: int
    page_size: int
    total: EvidenceValue


class ProjectStatusColumn(BaseModel):
    """看板一列 = 一个状态。"""

    status: ProjectStatus
    label: str
    count: int
    items: list[ProjectItem]


class ProjectBoardResponse(BaseModel):
    """项目看板。"""

    columns: list[ProjectStatusColumn]
    total: EvidenceValue
    active_total: EvidenceValue = Field(description="进行中的项目数（筹备/进行/暂停）")
    overdue_total: EvidenceValue = Field(description="已逾期的项目数")
    avg_progress: EvidenceValue = Field(
        description="进行中项目的平均进度；★ 没有任何项目填过进度则 NO_DATA，不是 0"
    )
    note: str


class ProjectLinkRequest(BaseModel):
    """给项目增删客户关联。"""

    customer_ids: list[int] = Field(min_length=1)
    role: str | None = Field(default=None, max_length=64)
