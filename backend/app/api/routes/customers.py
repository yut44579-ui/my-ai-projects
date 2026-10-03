"""客户查询接口（TASK-001 §三.4/5）。

本 TASK 只读：不许顺手做编辑/删除/导出（硬边界）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.customer import Customer, CustomerSourceType
from app.schemas.customers import CustomerItem, CustomerListResponse
from app.schemas.evidence import SourceType, build_evidence

router = APIRouter(tags=["customers"])

MAX_PAGE_SIZE = 100


def _parse_source_type(value: str | None) -> CustomerSourceType | None:
    if value is None or value.strip() == "":
        return None
    try:
        return CustomerSourceType(value.strip().upper())
    except ValueError as exc:
        allowed = " / ".join(item.value for item in CustomerSourceType)
        raise HTTPException(status_code=400, detail=f"source_type 只能是 {allowed}") from exc


@router.get("/customers", response_model=CustomerListResponse, summary="客户列表（分页/搜索/来源筛选）")
def list_customers(
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=MAX_PAGE_SIZE),
    q: str | None = Query(None, description="关键词：姓名/公司/电话/邮箱 模糊匹配"),
    source_type: str | None = Query(None, description="按来源筛选 REAL / TEST / MANUAL"),
) -> CustomerListResponse:
    """列表不做默认来源过滤 —— 前端要靠 TEST 数据把链路跑通并显示 banner。

    ★ §六 的「下游查询默认过滤 source_type='REAL'」是对汇报类查询（TASK-007）的要求，
      已写进 docs/IMPORT_RULES.md。
    """
    wanted = _parse_source_type(source_type)

    filters = []
    if wanted is not None:
        filters.append(Customer.source_type == wanted)
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
