"""项目服务（TASK-017）。

═══════════════════════════════════════════════════════════════════════
【状态流转规则（不做工作流引擎，D8 同一原则）】
═══════════════════════════════════════════════════════════════════════
    允许：
      · 主线内**向前任意步**（筹备 → 进行中是常规；筹备 → 已交付是补录历史数据）
      · 任意状态 → CANCELLED（取消）
      · 终态 → 回到主线（重启项目）
    拒绝：
      · 同状态（no-op，接口返回 changed=false）
      · 主线内**向后回退**（已交付 → 进行中）
        ★ 理由同商机：项目不会"退回"到做过的阶段；要修正请先取消再重开，
          这样数据里留下"曾经到过哪里"的痕迹。
      · CANCELLED ↔ CLOSED 互翻（要改先回到主线）

═══════════════════════════════════════════════════════════════════════
【逾期判定】
═══════════════════════════════════════════════════════════════════════
    is_overdue = 有 due_date 且 未结束（不在 CLOSED_STATUSES）且 due_date < 今天（Asia/Shanghai）
    ★ 这是**日期比较**，不是"预测会不会延期"——不给概率、不给风险评分（§三）。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.project import (
    ACTIVE_STATUSES,
    CLOSED_STATUSES,
    STATUS_ORDER,
    STATUS_RANK,
    Project,
    ProjectCustomer,
    ProjectPriority,
    ProjectStatus,
)
from app.schemas.evidence import SourceType, build_evidence, no_data_evidence
from app.schemas.projects import (
    ProjectCustomerLink,
    ProjectItem,
    ProjectStatusColumn,
)

TZ = ZoneInfo("Asia/Shanghai")

STATUS_LABELS: dict[ProjectStatus, str] = {
    ProjectStatus.PLANNING: "筹备中",
    ProjectStatus.IN_PROGRESS: "进行中",
    ProjectStatus.ON_HOLD: "已暂停",
    ProjectStatus.DELIVERED: "已交付",
    ProjectStatus.CLOSED: "已结项",
    ProjectStatus.CANCELLED: "已取消",
}


class ProjectStatusError(Exception):
    """非法状态流转。路由层翻译成 400。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def status_label(status: ProjectStatus) -> str:
    return STATUS_LABELS.get(status, str(status))


def evidence_ref_for(project_id: int) -> str:
    return f"project:{project_id}"


def assert_transition_ok(current: ProjectStatus, target: ProjectStatus) -> None:
    """校验状态流转是否合法。非法则抛 ProjectStatusError。"""
    if current == target:
        return  # no-op 由调用方处理

    if current == ProjectStatus.CANCELLED:
        if target in CLOSED_STATUSES:
            raise ProjectStatusError(
                f"「{status_label(current)}」不能直接改成「{status_label(target)}」；"
                "请先恢复到某个进行中状态，再按实际情况结项"
            )
        return

    if target == ProjectStatus.CANCELLED:
        return  # 任意状态 → 取消：允许

    if current in CLOSED_STATUSES:
        return  # 终态 → 回到主线：允许重启

    if target in CLOSED_STATUSES:
        return  # 进行中 → 交付/结项：允许

    # 主线内：只允许向前
    if STATUS_RANK[target] < STATUS_RANK[current]:
        raise ProjectStatusError(
            f"不允许从「{status_label(current)}」回退到「{status_label(target)}」；"
            "如果确实是填错了状态，请先取消再重新打开到正确状态"
        )


def overdue(project: Project, today: date | None = None) -> bool:
    """是否已逾期。★ 日期比较，不做延期预测。"""
    if project.due_date is None:
        return False
    if project.status in CLOSED_STATUSES:
        return False
    today = today or datetime.now(timezone.utc).astimezone(TZ).date()
    return project.due_date < today


def _links(db: Session, project_ids: list[int]) -> dict[int, list[ProjectCustomerLink]]:
    """批量取项目关联客户（避免 N+1）。"""
    if not project_ids:
        return {}
    rows = db.execute(
        select(ProjectCustomer.project_id, Customer.id, Customer.name, ProjectCustomer.role)
        .join(Customer, Customer.id == ProjectCustomer.customer_id)
        .where(ProjectCustomer.project_id.in_(project_ids))
    ).all()
    out: dict[int, list[ProjectCustomerLink]] = {}
    for pid, cid, name, role in rows:
        out.setdefault(pid, []).append(
            ProjectCustomerLink(
                customer_id=cid,
                customer_name=name,
                role=role,
                evidence_ref=f"project:{pid}:customer:{cid}",
            )
        )
    return out


def to_item(project: Project, customers: list[ProjectCustomerLink]) -> ProjectItem:
    ref = project.evidence_ref or evidence_ref_for(project.id)
    return ProjectItem(
        id=project.id,
        name=project.name,
        code=project.code,
        status=project.status,
        status_label=status_label(project.status),
        priority=project.priority,
        progress=project.progress,
        start_date=project.start_date,
        due_date=project.due_date,
        delivered_at=project.delivered_at,
        owner=project.owner,
        description=project.description,
        customer_count=len(customers),
        customers=customers,
        is_overdue=overdue(project),
        is_active=project.is_active,
        evidence_ref=ref,
    )


def to_items(db: Session, projects: list[Project]) -> list[ProjectItem]:
    links = _links(db, [p.id for p in projects])
    return [to_item(p, links.get(p.id, [])) for p in projects]


def get_project(db: Session, project_id: int) -> Project | None:
    p = db.get(Project, project_id)
    if p is None or not p.is_active:
        return None
    return p


def _validate_customers(db: Session, customer_ids: list[int]) -> None:
    """校验客户都存在。★ 有一个不存在就整体拒绝，不做"部分成功"。"""
    if not customer_ids:
        return
    found = set(
        db.scalars(select(Customer.id).where(Customer.id.in_(customer_ids))).all()
    )
    missing = sorted(set(customer_ids) - found)
    if missing:
        raise LookupError(f"客户不存在：{missing}")


def create_project(
    db: Session,
    *,
    name: str,
    code: str | None = None,
    status: ProjectStatus = ProjectStatus.PLANNING,
    priority: ProjectPriority = ProjectPriority.MEDIUM,
    progress: int | None = None,
    start_date=None,
    due_date=None,
    owner: str | None = None,
    description: str | None = None,
    customer_ids: list[int] | None = None,
) -> Project:
    ids = list(dict.fromkeys(customer_ids or []))  # 去重且保序
    _validate_customers(db, ids)

    project = Project(
        name=name.strip(),
        code=(code.strip() if code else None),
        status=status,
        priority=priority,
        progress=progress,
        start_date=start_date,
        due_date=due_date,
        owner=owner,
        description=description,
        is_active=True,
        delivered_at=(
            datetime.now(timezone.utc)
            if status in (ProjectStatus.DELIVERED, ProjectStatus.CLOSED)
            else None
        ),
    )
    db.add(project)
    db.flush()
    project.evidence_ref = evidence_ref_for(project.id)

    for cid in ids:
        db.add(ProjectCustomer(project_id=project.id, customer_id=cid))

    db.commit()
    db.refresh(project)
    return project


def change_status(
    db: Session, project: Project, *, target: ProjectStatus, note: str | None = None
) -> tuple[Project, bool, str]:
    """流转状态。返回 (项目, 是否变化, 说明)。★ no-op 不写任何变更。"""
    if project.status == target:
        return project, False, f"状态已经是「{status_label(target)}」，未做变更"

    assert_transition_ok(project.status, target)

    previous = project.status
    project.status = target
    if note:
        project.description = (project.description or "") + f"\n[{status_label(target)}] {note}"

    if target in (ProjectStatus.DELIVERED, ProjectStatus.CLOSED):
        project.delivered_at = datetime.now(timezone.utc)
    elif target == ProjectStatus.CANCELLED:
        project.delivered_at = None
    else:
        project.delivered_at = None  # 重启

    db.commit()
    db.refresh(project)
    return project, True, f"状态已从「{status_label(previous)}」变更为「{status_label(target)}」"


def update_project(db: Session, project: Project, **fields) -> Project:
    for key, value in fields.items():
        if value is not None and hasattr(project, key):
            setattr(project, key, value)
    db.commit()
    db.refresh(project)
    return project


def link_customers(
    db: Session, project: Project, customer_ids: list[int], role: str | None = None
) -> int:
    """添加客户关联（幂等：已关联的跳过）。返回新增条数。"""
    ids = list(dict.fromkeys(customer_ids))
    _validate_customers(db, ids)

    existing = set(
        db.scalars(
            select(ProjectCustomer.customer_id).where(ProjectCustomer.project_id == project.id)
        ).all()
    )
    added = 0
    for cid in ids:
        if cid in existing:
            continue
        db.add(ProjectCustomer(project_id=project.id, customer_id=cid, role=role))
        added += 1
    if added:
        db.commit()
    return added


def unlink_customer(db: Session, project: Project, customer_id: int) -> bool:
    row = db.scalar(
        select(ProjectCustomer).where(
            ProjectCustomer.project_id == project.id,
            ProjectCustomer.customer_id == customer_id,
        )
    )
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def list_projects(
    db: Session,
    *,
    status: ProjectStatus | None = None,
    customer_id: int | None = None,
    only_active: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[ProjectItem], int]:
    conditions = [Project.is_active.is_(True)]
    if status is not None:
        conditions.append(Project.status == status)
    if only_active:
        conditions.append(Project.status.in_(list(ACTIVE_STATUSES)))
    if customer_id is not None:
        # 按客户查项目：走 project_customers 索引（这正是建关联表的原因）
        conditions.append(
            Project.id.in_(
                select(ProjectCustomer.project_id).where(
                    ProjectCustomer.customer_id == customer_id
                )
            )
        )

    base = select(Project)
    counter = select(func.count(Project.id))
    for c in conditions:
        base = base.where(c)
        counter = counter.where(c)

    total = db.scalar(counter) or 0
    rows = db.scalars(
        base.order_by(Project.updated_at.desc(), Project.id.desc()).limit(limit).offset(offset)
    ).all()
    return to_items(db, list(rows)), total


def _progress_evidence(values: list[int | None], ref: str):
    """平均进度。★ 一个都没填 → NO_DATA（不是 0）。"""
    known = [v for v in values if v is not None]
    if not known:
        return no_data_evidence(
            "进行中的项目都没有填写进度", source_type=SourceType.MANUAL, evidence_ref=ref
        )
    return build_evidence(
        round(sum(known) / len(known)), source_type=SourceType.MANUAL, evidence_ref=ref
    )


def build_board(db: Session, *, per_status_limit: int = 50) -> dict:
    """项目看板：按状态分列。"""
    rows = db.scalars(
        select(Project)
        .where(Project.is_active.is_(True))
        .order_by(Project.updated_at.desc(), Project.id.desc())
    ).all()
    items = to_items(db, list(rows))
    by_status: dict[ProjectStatus, list[ProjectItem]] = {s: [] for s in STATUS_LABELS}
    for item in items:
        by_status[item.status].append(item)

    columns = [
        ProjectStatusColumn(
            status=s,
            label=status_label(s),
            count=len(by_status.get(s, [])),
            items=by_status.get(s, [])[:per_status_limit],
        )
        for s in ProjectStatus
    ]

    total = len(items)
    active_count = sum(1 for p in rows if p.status in ACTIVE_STATUSES)
    overdue_count = sum(1 for p in rows if overdue(p))
    progress_values = [p.progress for p in rows if p.status in ACTIVE_STATUSES]

    def ev_or_nodata(n: int, ref: str, empty: str):
        if total:
            return build_evidence(n, source_type=SourceType.SYSTEM, evidence_ref=ref)
        return no_data_evidence(empty, source_type=SourceType.SYSTEM, evidence_ref=ref)

    return {
        "columns": columns,
        "total": ev_or_nodata(total, "projects:count", "还没有项目"),
        "active_total": ev_or_nodata(active_count, "projects:active:count", "还没有项目"),
        "overdue_total": ev_or_nodata(overdue_count, "projects:overdue:count", "还没有项目"),
        "avg_progress": _progress_evidence(progress_values, "projects:active:avg_progress"),
        "note": (
            "项目进度由人工填写，不是按任务数推算（V1 没有任务表）；"
            "逾期是日期比较（计划完成日已过且未结束），不是延期预测。"
        ),
    }
