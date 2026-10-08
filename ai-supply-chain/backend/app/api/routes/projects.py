"""项目接口（TASK-017）。

    GET    /api/projects                        列表（可按状态 / 客户 / 只看进行中）
    GET    /api/projects/board                  看板（按状态分列）
    POST   /api/projects                        新建（可同时关联多个客户）
    GET    /api/projects/{id}                   详情
    PATCH  /api/projects/{id}                   更新（不含状态）
    POST   /api/projects/{id}/status            流转状态（校验合法性）
    POST   /api/projects/{id}/customers         添加客户关联
    DELETE /api/projects/{id}/customers/{cid}   移除客户关联

★ 路由顺序：/board 必须在 /{project_id} 之前（customers /stats、reports /drilldown、
  opportunities /board 都踩过同一个坑）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.project import ProjectStatus
from app.schemas.evidence import SourceType, build_evidence, no_data_evidence
from app.schemas.projects import (
    ProjectBoardResponse,
    ProjectCreateRequest,
    ProjectItem,
    ProjectLinkRequest,
    ProjectListResponse,
    ProjectStatusRequest,
    ProjectStatusResponse,
    ProjectUpdateRequest,
)
from app.services.errors import ApiErrorCode, ApiFailure
from app.services.project import (
    ProjectStatusError,
    build_board,
    change_status,
    create_project,
    get_project,
    link_customers,
    list_projects,
    to_items,
    unlink_customer,
    update_project,
)

router = APIRouter(tags=["projects"])


def _require(db: Session, project_id: int):
    p = get_project(db, project_id)
    if p is None:
        raise ApiFailure(
            ApiErrorCode.PROJECT_NOT_FOUND, f"项目 {project_id} 不存在", http_status=404
        )
    return p


def _item(db: Session, project) -> ProjectItem:
    return to_items(db, [project])[0]


@router.get("/projects/board", response_model=ProjectBoardResponse, summary="项目看板（按状态分列）")
def project_board(
    per_status_limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> ProjectBoardResponse:
    """看板。★ 平均进度在没人填过进度时是 NO_DATA（不是 0）。"""
    return ProjectBoardResponse(**build_board(db, per_status_limit=per_status_limit))


@router.get("/projects", response_model=ProjectListResponse, summary="项目列表")
def projects_list(
    status: ProjectStatus | None = Query(default=None),
    customer_id: int | None = Query(default=None, description="查这个客户参与的项目"),
    only_active: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> ProjectListResponse:
    items, total = list_projects(
        db,
        status=status,
        customer_id=customer_id,
        only_active=only_active,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    total_ev = (
        build_evidence(total, source_type=SourceType.SYSTEM, evidence_ref="projects:list:total")
        if total
        else no_data_evidence(
            "当前筛选下没有项目", source_type=SourceType.SYSTEM, evidence_ref="projects:list:total"
        )
    )
    return ProjectListResponse(items=items, page=page, page_size=page_size, total=total_ev)


@router.post("/projects", response_model=ProjectItem, status_code=201, summary="新建项目")
def projects_create(payload: ProjectCreateRequest, db: Session = Depends(get_db)) -> ProjectItem:
    """新建项目。★ 关联客户里有一个不存在就整体拒绝（不做部分成功）。"""
    try:
        project = create_project(
            db,
            name=payload.name,
            code=payload.code,
            status=payload.status,
            priority=payload.priority,
            progress=payload.progress,
            start_date=payload.start_date,
            due_date=payload.due_date,
            owner=payload.owner,
            description=payload.description,
            customer_ids=payload.customer_ids,
        )
    except LookupError as exc:
        raise ApiFailure(ApiErrorCode.MESSAGE_NOT_FOUND, str(exc), http_status=404)
    return _item(db, project)


@router.get("/projects/{project_id}", response_model=ProjectItem, summary="项目详情")
def projects_detail(project_id: int, db: Session = Depends(get_db)) -> ProjectItem:
    return _item(db, _require(db, project_id))


@router.patch("/projects/{project_id}", response_model=ProjectItem, summary="更新项目")
def projects_update(
    project_id: int, payload: ProjectUpdateRequest, db: Session = Depends(get_db)
) -> ProjectItem:
    """更新项目字段。★ 状态请走 /status。"""
    project = _require(db, project_id)
    updated = update_project(db, project, **payload.model_dump(exclude_unset=True))
    return _item(db, updated)


@router.post(
    "/projects/{project_id}/status",
    response_model=ProjectStatusResponse,
    summary="流转项目状态",
)
def projects_status(
    project_id: int, payload: ProjectStatusRequest, db: Session = Depends(get_db)
) -> ProjectStatusResponse:
    """状态流转。★ 非法流转 → 400；同状态 → changed=false（不报错）。"""
    project = _require(db, project_id)
    try:
        updated, changed, msg = change_status(db, project, target=payload.status, note=payload.note)
    except ProjectStatusError as exc:
        raise ApiFailure(ApiErrorCode.INVALID_PROJECT_STATUS, exc.reason, http_status=400)
    return ProjectStatusResponse(project=_item(db, updated), changed=changed, message=msg)


@router.post(
    "/projects/{project_id}/customers",
    response_model=ProjectItem,
    summary="给项目添加客户关联（幂等）",
)
def projects_link_customers(
    project_id: int, payload: ProjectLinkRequest, db: Session = Depends(get_db)
) -> ProjectItem:
    project = _require(db, project_id)
    try:
        link_customers(db, project, payload.customer_ids, payload.role)
    except LookupError as exc:
        raise ApiFailure(ApiErrorCode.MESSAGE_NOT_FOUND, str(exc), http_status=404)
    db.refresh(project)
    return _item(db, project)


@router.delete(
    "/projects/{project_id}/customers/{customer_id}",
    response_model=ProjectItem,
    summary="移除项目与客户的关联",
)
def projects_unlink_customer(
    project_id: int, customer_id: int, db: Session = Depends(get_db)
) -> ProjectItem:
    project = _require(db, project_id)
    if not unlink_customer(db, project, customer_id):
        raise ApiFailure(
            ApiErrorCode.MESSAGE_NOT_FOUND,
            f"项目 {project_id} 未关联客户 {customer_id}",
            http_status=404,
        )
    db.refresh(project)
    return _item(db, project)
