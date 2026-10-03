"""客户查询接口（TASK-001 §三.4/5；TASK-002 追加详情 / 事件流 / 指标卡）
+ 客户状态机（TASK-006 §二）。

TASK-001 / TASK-002 时本文件只读：只有 SELECT，见 tests/test_customers_module.py 的 AC6 断言。
TASK-006 增加了**唯一一个写接口**：
POST /customers/{id}/status —— 且每一次成功的变更都必须同时落一条 customer_events。
除此之外依旧不提供编辑 / 删除 / 导出（硬边界）。

★ 并行线合并（integration）：TASK-002 的 timeline / stats 与 TASK-006 的 status / events
  四个接口全部保留，一个都没丢。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.customer import Customer, CustomerSourceType, DedupeState, LifecycleStatus
from app.models.customer_event import ActorType, CustomerEvent, CustomerEventType, EventSourceType
from app.models.import_batch import ImportBatch
from app.schemas.customer_events import (
    CustomerEventItem,
    CustomerEventListResponse,
    StatusChangeRequest,
    StatusChangeResponse,
)
from app.schemas.customers import (
    BatchRef,
    CustomerDetailResponse,
    CustomerItem,
    CustomerListResponse,
    CustomerStatsResponse,
    CustomerTimelineResponse,
    FollowUpStatus,
    SourceTrace,
    TimelineEvent,
    TimelineEventKind,
    TimelineUnavailable,
)
from app.schemas.evidence import (
    EvidenceValue,
    SourceType,
    ValueState,
    build_evidence,
    no_data_evidence,
)
from app.services.messaging import count_for_customer

router = APIRouter(tags=["customers"])

MAX_PAGE_SIZE = 100

# D6 时区固定 Asia/Shanghai。Windows 上 Python 没有系统 tz 库时回落到固定 +08:00，
# 两者对"本周"的判定在本项目里等价（中国不实行夏令时）。
try:  # pragma: no cover - 依赖运行环境
    from zoneinfo import ZoneInfo

    APP_TZ: timezone | ZoneInfo = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover - 兜底分支
    APP_TZ = timezone(timedelta(hours=8))

# 还没有数据源的区块：显式空态，写明等哪个 TASK 接入。
# ★ conversations 自 TASK-003 起已接入（customer_messages），这里只在「该客户确实一条都没有」时用。
NO_DATA_REASONS = {
    "conversations": "暂无沟通记录（TASK-003 已接入 customer_messages，该客户尚无消息）",
    "risks": "暂无风险记录（TASK-005 接入后显示）",
    "batch_details": "V1 不按客户展开批次明细（来源批次见「来源与可追溯」）",
}

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


def _scope_filters(
    q: str | None,
    wanted: CustomerSourceType | None,
    wanted_status: LifecycleStatus | None = None,
) -> list:
    """列表页与指标卡共用同一套过滤条件（★ 前端不许自己算，口径必须一处定义）。

    ★ 并行线合并：TASK-002 的 source_type/q 口径 + TASK-006 的 lifecycle_status 口径
      在这里合流 —— 调用方传 None 就等于不启用该维度，老调用点行为不变。
    """
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
    return filters


def _scope_label(
    q: str | None,
    wanted: CustomerSourceType | None,
    wanted_status: LifecycleStatus | None = None,
) -> str:
    """把口径写进 evidence_ref，界面上悬浮就能看出这个数字是"什么条件下"算出来的。"""
    parts = []
    if wanted is not None:
        parts.append(f"source_type={wanted.value}")
    if wanted_status is not None:
        parts.append(f"lifecycle_status={wanted_status.value}")
    if q and q.strip():
        parts.append(f"q={q.strip()}")
    return "|".join(parts) if parts else "all"


def _count(db: Session, *filters) -> int:
    stmt = select(func.count()).select_from(Customer)
    if filters:
        stmt = stmt.where(*filters)
    return db.execute(stmt).scalar_one()


def _scoped_evidence(value: int, *, empty_scope: bool, evidence_ref: str) -> EvidenceValue:
    """口径内**一条客户都没有** → NO_DATA（界面「—」+「暂无数据」）。

    口径内有客户 → VALID，此时 0 是合法真值（例如"本周新增 0"），不许显示成「—」。
    这条区分就是 D4 里 VALID-0 与 NO_DATA 的分界。
    """
    if empty_scope:
        return no_data_evidence(
            "当前筛选条件下没有客户数据",
            source_type=SourceType.IMPORT,
            evidence_ref=evidence_ref,
        )
    return build_evidence(value, source_type=SourceType.IMPORT, evidence_ref=evidence_ref)


def _week_start(now: datetime | None = None) -> datetime:
    """本周一 00:00（Asia/Shanghai），返回去除时区信息的本地朴素时间。

    customers.first_seen_at 是 MySQL DATETIME（朴素本地时间，server_default=now()），
    所以比较时也要用朴素本地时间，否则会拿带时区的值和裸值比。
    """
    local = (now or datetime.now(tz=APP_TZ)).astimezone(APP_TZ)
    monday = local - timedelta(days=local.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)


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

    filters = _scope_filters(q, wanted, wanted_status)
    total = _count(db, *filters)

    list_stmt = select(Customer).order_by(Customer.id.desc())
    if filters:
        list_stmt = list_stmt.where(*filters)
    customers = (
        db.execute(list_stmt.offset((page - 1) * page_size).limit(page_size)).scalars().all()
    )

    # 全库 TEST 条数（不受当前筛选影响）：前端据它显示「当前包含 N 条测试导入数据」
    test_count = _count(db, Customer.source_type == CustomerSourceType.TEST)

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


@router.get(
    "/customers/stats",
    response_model=CustomerStatsResponse,
    summary="客户指标卡（列表页顶部四个数字）",
)
def customer_stats(
    db: Session = Depends(get_db),
    q: str | None = Query(None, description="与列表页同一个关键词口径"),
    source_type: str | None = Query(None, description="与列表页同一个来源口径"),
) -> CustomerStatsResponse:
    """★ 路由必须注册在 /customers/{customer_id} **之前**，否则 "stats" 会被当成 id 解析。

    口径 = 列表页当前的搜索词 + 来源筛选：口径内没有客户 → 四项全部 NO_DATA（界面显示「—」），
    口径内有客户 → VALID（真零就是 0）。所有数字都在这里算，前端一个都不许自己算（D4）。
    """
    wanted = _parse_source_type(source_type)
    filters = _scope_filters(q, wanted)
    scope = _scope_label(q, wanted)

    total = _count(db, *filters)
    empty_scope = total == 0

    test_count = _count(db, *filters, Customer.source_type == CustomerSourceType.TEST)
    pending_review = _count(db, *filters, Customer.dedupe_state == DedupeState.PENDING_REVIEW)
    new_this_week = _count(db, *filters, Customer.first_seen_at >= _week_start())

    return CustomerStatsResponse(
        scope=scope,
        total=_scoped_evidence(total, empty_scope=empty_scope, evidence_ref=f"customers:stats:total|{scope}"),
        test_count=_scoped_evidence(
            test_count, empty_scope=empty_scope, evidence_ref=f"customers:stats:test|{scope}"
        ),
        pending_review=_scoped_evidence(
            pending_review,
            empty_scope=empty_scope,
            evidence_ref=f"customers:stats:pending_review|{scope}",
        ),
        new_this_week=_scoped_evidence(
            new_this_week,
            empty_scope=empty_scope,
            evidence_ref=f"customers:stats:new_this_week|{scope}",
        ),
    )


@router.get("/customers/{customer_id}", response_model=CustomerDetailResponse, summary="客户详情")
def get_customer(customer_id: int, db: Session = Depends(get_db)) -> CustomerDetailResponse:
    """基础字段（TASK-001）+ 来源可追溯 + 状态占位 + 三个显式空区块（TASK-002）。

    ★ 并行线合并：详情响应继承 CustomerItem，因此 handover_state（TASK-005）与
      lifecycle_status（TASK-006）都随基础字段一起返回。
    """
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail=f"客户 {customer_id} 不存在")

    batch = db.get(ImportBatch, customer.batch_id) if customer.batch_id else None
    batch_ref = (
        BatchRef(
            batch_id=batch.id,
            filename=batch.filename,
            status=batch.status,
            source_type=batch.source_type,
            imported_at=batch.imported_at,
            evidence_ref=batch.evidence_ref or f"import_batch:{batch.id}",
        )
        if batch is not None
        else None
    )

    # TASK-003：「沟通记录」区块接真数据 —— 该客户的消息条数（业务数字 → EvidenceValue）。
    # ★ 一条都没有 → NO_DATA 空态（AC4），绝不用 0 冒充「暂无沟通记录」。
    message_count = count_for_customer(db, customer.id)
    conversations_ref = f"customer_messages:count|customer_id={customer.id}"
    conversations = (
        build_evidence(message_count, source_type=SourceType.SYSTEM, evidence_ref=conversations_ref)
        if message_count
        else no_data_evidence(
            NO_DATA_REASONS["conversations"],
            source_type=SourceType.SYSTEM,
            evidence_ref=conversations_ref,
        )
    )

    base = CustomerItem.model_validate(customer).model_dump()
    return CustomerDetailResponse(
        **base,
        source=SourceTrace(
            source_type=customer.source_type,
            evidence_ref=customer.evidence_ref,
            batch_id=customer.batch_id,
            batch=batch_ref,
        ),
        status=FollowUpStatus(
            available=False,
            label="未开始跟进",
            state="NOT_STARTED",
            note="V1 暂无跟进流程；等 TASK-006 接入状态机",
        ),
        # 沟通记录：TASK-003 起接真数据（见上）；另外两区块仍是显式 NO_DATA
        conversations=conversations,
        risks=no_data_evidence(
            NO_DATA_REASONS["risks"],
            source_type=SourceType.SYSTEM,
            evidence_ref=f"customer:{customer.id}:risks",
        ),
        batch_details=no_data_evidence(
            NO_DATA_REASONS["batch_details"],
            source_type=SourceType.SYSTEM,
            evidence_ref=f"customer:{customer.id}:batch_details",
        ),
    )


@router.get(
    "/customers/{customer_id}/timeline",
    response_model=CustomerTimelineResponse,
    summary="客户事件流（V1 只含真实事件）",
)
def customer_timeline(customer_id: int, db: Session = Depends(get_db)) -> CustomerTimelineResponse:
    """V1 只返回**真实存在**的事件：来源导入（import_batches 行）+ 待人工裁决（dedupe_state）。

    AI 对话 / 人工跟进这些还没有数据源的类型，一律进 `unavailable` 且 state=NO_DATA，
    不用空数组冒充"已接入但为空"，更不编造事件（D9）。
    """
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail=f"客户 {customer_id} 不存在")

    events: list[TimelineEvent] = [
        TimelineEvent(
            kind=TimelineEventKind.CUSTOMER_CREATED,
            title="客户记录创建",
            occurred_at=customer.first_seen_at,
            evidence_ref=f"customer:{customer.id}",
            detail=f"first_seen_at={customer.first_seen_at.isoformat(sep=' ')}",
        )
    ]

    if customer.batch_id:
        batch = db.get(ImportBatch, customer.batch_id)
        if batch is not None:
            events.append(
                TimelineEvent(
                    kind=TimelineEventKind.SOURCE_IMPORT,
                    title=f"由导入批次 #{batch.id} 写入",
                    occurred_at=batch.imported_at,
                    evidence_ref=batch.evidence_ref or f"import_batch:{batch.id}",
                    detail=f"文件 {batch.filename}｜批次状态 {batch.status.value}",
                )
            )

    if customer.dedupe_state is DedupeState.PENDING_REVIEW:
        events.append(
            TimelineEvent(
                kind=TimelineEventKind.DEDUPE_PENDING_REVIEW,
                title="去重命中多条既有客户，待人工裁决",
                occurred_at=customer.first_seen_at,
                evidence_ref=f"customer:{customer.id}",
                detail="导入时判定；★ 不自动合并，等人工处理",
            )
        )

    events.sort(key=lambda e: e.occurred_at)

    return CustomerTimelineResponse(
        customer_id=customer.id,
        events=events,
        events_count=build_evidence(
            len(events) if events else None,
            source_type=SourceType.IMPORT,
            evidence_ref=f"customer:{customer.id}:timeline",
        ),
        unavailable=[
            TimelineUnavailable(
                kind="AI_CONVERSATION",
                label="AI 对话",
                state=ValueState.NO_DATA,
                reason="事件流只放客户/批次的客观事件；对话内容见 TASK-003 的「沟通记录」区块",
            ),
            TimelineUnavailable(
                kind="HUMAN_FOLLOW_UP",
                label="人工跟进",
                state=ValueState.NO_DATA,
                reason="人工跟进记录见 TASK-003 的「沟通记录」区块（本事件流不重复列举）",
            ),
        ],
    )


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
