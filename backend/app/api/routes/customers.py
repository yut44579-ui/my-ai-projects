"""客户查询接口（TASK-001 §三.4/5）+ 客户状态机（TASK-006 §二）。

TASK-001 时本文件只读。TASK-006 增加了**唯一一个写接口**：
POST /customers/{id}/status —— 且每一次成功的变更都必须同时落一条 customer_events。
除此之外依旧不提供编辑 / 删除 / 导出（硬边界）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.customer import Customer, CustomerSourceType, LifecycleStatus
from app.models.customer_event import ActorType, CustomerEvent, CustomerEventType, EventSourceType
from app.schemas.customer_events import (
    CustomerEventItem,
    CustomerEventListResponse,
    StatusChangeRequest,
    StatusChangeResponse,
)
from app.schemas.customers import CustomerItem, CustomerListResponse
from app.schemas.evidence import EvidenceValue, SourceType, ValueState, build_evidence

router = APIRouter(tags=["customers"])

MAX_PAGE_SIZE = 100

# D7：AI 安全拦截写在代码层，不靠提示词。
AI_STATUS_FORBIDDEN = (
    "AI 不得自行变更客户状态（D7：AI 安全拦截在代码层）。"
    "客户状态只能由人工发起变更（actor_type=HUMAN），请转人工处理。"
)


def _parse_source_type(value: str | None) -> CustomerSourceType | None:
    if value is None or value.strip() == "":
        return None
    try:
        return CustomerSourceType(value.strip().upper())
    except ValueError as exc:
        allowed = " / ".join(item.value for item in CustomerSourceType)
        raise HTTPException(status_code=400, detail=f"source_type 只能是 {allowed}") from exc


def _parse_lifecycle_status(value: str | None) -> LifecycleStatus | None:
    if value is None or value.strip() == "":
        return None
    try:
        return LifecycleStatus(value.strip().upper())
    except ValueError as exc:
        allowed = " / ".join(item.value for item in LifecycleStatus)
        raise HTTPException(status_code=400, detail=f"lifecycle_status 只能是 {allowed}") from exc


def _parse_actor_type(raw: str) -> ActorType:
    """★ 非法取值返回 400（而不是 FastAPI 默认的 422）—— AC4 明确要求 400。"""
    try:
        return ActorType((raw or "").strip().upper())
    except ValueError as exc:
        allowed = " / ".join(item.value for item in ActorType)
        raise HTTPException(status_code=400, detail=f"非法 actor_type {raw!r}：只能是 {allowed}") from exc


def _parse_to_status(raw: str) -> LifecycleStatus:
    """★ 非法状态值返回 400 —— AC4。"""
    try:
        return LifecycleStatus((raw or "").strip().upper())
    except ValueError as exc:
        allowed = " / ".join(item.value for item in LifecycleStatus)
        raise HTTPException(
            status_code=400, detail=f"非法状态值 {raw!r}：只能是 {allowed}"
        ) from exc


@router.get("/customers", response_model=CustomerListResponse, summary="客户列表（分页/搜索/来源筛选）")
def list_customers(
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=MAX_PAGE_SIZE),
    q: str | None = Query(None, description="关键词：姓名/公司/电话/邮箱 模糊匹配"),
    source_type: str | None = Query(None, description="按来源筛选 REAL / TEST / MANUAL"),
    lifecycle_status: str | None = Query(
        None, description="TASK-006：按生命周期状态筛选 NEW / CONTACTED / ... / WON / LOST"
    ),
) -> CustomerListResponse:
    """列表不做默认来源过滤 —— 前端要靠 TEST 数据把链路跑通并显示 banner。

    ★ §六 的「下游查询默认过滤 source_type='REAL'」是对汇报类查询（TASK-007）的要求，
      已写进 docs/IMPORT_RULES.md。
    """
    wanted = _parse_source_type(source_type)
    wanted_status = _parse_lifecycle_status(lifecycle_status)

    filters = []
    if wanted is not None:
        filters.append(Customer.source_type == wanted)
    if wanted_status is not None:
        filters.append(Customer.lifecycle_status == wanted_status)
    if q and q.strip():
        like = f"%{q.strip()}%"
        filters.append(
            or_(
                Customer.name.like(like),
                Customer.company_name.like(like),
                Customer.phone.like(like),
                Customer.email.like(like),
            )
        )

    total_stmt = select(func.count()).select_from(Customer)
    if filters:
        total_stmt = total_stmt.where(*filters)
    total = db.execute(total_stmt).scalar_one()

    list_stmt = select(Customer).order_by(Customer.id.desc())
    if filters:
        list_stmt = list_stmt.where(*filters)
    customers = (
        db.execute(list_stmt.offset((page - 1) * page_size).limit(page_size)).scalars().all()
    )

    # 全库 TEST 条数（不受当前筛选影响）：前端据它显示「当前包含 N 条测试导入数据」
    test_count = db.execute(
        select(func.count())
        .select_from(Customer)
        .where(Customer.source_type == CustomerSourceType.TEST)
    ).scalar_one()

    ref = "customers:count"
    if wanted is not None:
        ref += f"|source_type={wanted.value}"
    if wanted_status is not None:
        ref += f"|lifecycle_status={wanted_status.value}"
    if q and q.strip():
        ref += f"|q={q.strip()}"

    return CustomerListResponse(
        items=[CustomerItem.model_validate(c) for c in customers],
        page=page,
        page_size=page_size,
        total=build_evidence(total, source_type=SourceType.IMPORT, evidence_ref=ref[:128]),
        test_count=build_evidence(
            test_count,
            source_type=SourceType.IMPORT,
            evidence_ref="customers:count|source_type=TEST",
        ),
    )


@router.get("/customers/{customer_id}", response_model=CustomerItem, summary="客户详情")
def get_customer(customer_id: int, db: Session = Depends(get_db)) -> CustomerItem:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail=f"客户 {customer_id} 不存在")
    return CustomerItem.model_validate(customer)


@router.post(
    "/customers/{customer_id}/status",
    response_model=StatusChangeResponse,
    summary="变更客户生命周期状态（仅人工，AI 一律 403）",
)
def change_customer_status(
    customer_id: int,
    payload: StatusChangeRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> StatusChangeResponse:
    """变更状态 = 改 customers.lifecycle_status + 写一条 STATUS_CHANGED 事件（同事务）。

    顺序是刻意的：**安全闸门 → 参数校验 → 读客户 → 同值短路 → 写库**。
    AI 的请求在任何一步之前就被 403 拦掉，因此绝不会留下事件（AC3）。
    """
    # ① AI 闸门先于一切：请求头标了 AI 就直接拒，连参数都不解析
    header_actor = (
        request.headers.get("x-actor-type") or request.headers.get("x-source-type") or ""
    ).strip().upper()
    if header_actor == ActorType.AI.value:
        raise HTTPException(status_code=403, detail=AI_STATUS_FORBIDDEN)

    actor = _parse_actor_type(payload.actor_type)
    if actor is ActorType.AI:
        raise HTTPException(status_code=403, detail=AI_STATUS_FORBIDDEN)

    to_status = _parse_to_status(payload.to_status)

    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail=f"客户 {customer_id} 不存在")

    from_status = customer.lifecycle_status
    if from_status is to_status:
        # 同值不写事件：事件流是「变化历史」，无变化就没有历史可记
        return StatusChangeResponse(
            customer_id=customer_id,
            lifecycle_status=from_status,
            changed=False,
            event=None,
            message=f"状态已是 {to_status.value}，未发生变化，未写入事件",
        )

    customer.lifecycle_status = to_status
    note = (payload.note or "").strip()
    event = CustomerEvent(
        customer_id=customer_id,
        event_type=CustomerEventType.STATUS_CHANGED,
        from_status=from_status,
        to_status=to_status,
        actor_type=actor,
        # actor 与来源必须如实对应：人工变更 = MANUAL
        source_type=EventSourceType.SYSTEM if actor is ActorType.SYSTEM else EventSourceType.MANUAL,
        metadata_json={"note": note} if note else None,
    )
    db.add(event)
    db.flush()  # 取到自增 id，才能生成证据锚点
    event.evidence_ref = f"customer_event:{event.id}"
    db.commit()
    db.refresh(event)

    return StatusChangeResponse(
        customer_id=customer_id,
        lifecycle_status=to_status,
        changed=True,
        event=CustomerEventItem.model_validate(event),
        message=f"状态已由 {from_status.value} 变更为 {to_status.value}，事件已留痕",
    )


@router.get(
    "/customers/{customer_id}/events",
    response_model=CustomerEventListResponse,
    summary="客户事件流（时间倒序，分页）",
)
def list_customer_events(
    customer_id: int,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=MAX_PAGE_SIZE),
) -> CustomerEventListResponse:
    """按时间倒序返回事件流。无事件 -> 空列表 + EvidenceValue{value:null, state:NO_DATA}。"""
    if db.get(Customer, customer_id) is None:
        raise HTTPException(status_code=404, detail=f"客户 {customer_id} 不存在")

    total = db.execute(
        select(func.count())
        .select_from(CustomerEvent)
        .where(CustomerEvent.customer_id == customer_id)
    ).scalar_one()

    events = (
        db.execute(
            select(CustomerEvent)
            .where(CustomerEvent.customer_id == customer_id)
            # created_at 是 DATETIME(6)，同秒也能排序；id 兜底保证全序稳定
            .order_by(CustomerEvent.created_at.desc(), CustomerEvent.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )

    ref = f"customer:{customer_id}:events"
    if total == 0:
        # ★ 一条事件都没有 = 确实没有数据，必须 NO_DATA + value=null，不许拿 0 冒充（AC5）
        total_evidence = EvidenceValue(
            value=None,
            state=ValueState.NO_DATA,
            source_type=SourceType.SYSTEM,
            evidence_ref=ref,
            reason="该客户暂无任何事件记录",
        )
    else:
        total_evidence = build_evidence(total, source_type=SourceType.SYSTEM, evidence_ref=ref)

    return CustomerEventListResponse(
        items=[CustomerEventItem.model_validate(e) for e in events],
        page=page,
        page_size=page_size,
        total=total_evidence,
    )
