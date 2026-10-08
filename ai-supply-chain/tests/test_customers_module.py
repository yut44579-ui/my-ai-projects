"""TASK-002 客户模块测试：指标卡 / 详情 / 事件流 + 只读硬边界（AC1~AC7 的后端半边）。

★ TASK-001 的断言一条都没改（见 tests/test_imports_api.py）；本文件只**追加**。
★ 本模块全部是 GET：AC6 用「调用前后库里的行数 / 自增 id 完全不变」来证明没有写库。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from app.api.routes.customers import _week_start
from app.models.customer import Customer, CustomerSourceType, DedupeState
from app.models.import_batch import ImportBatch
from app.services.dedupe import now_local

MARK = "单测手工客户TASK002"


# ─────────────── 夹具 ───────────────


@pytest.fixture
def manual_customer(db: Session):
    """一条 MANUAL 来源、没有批次的客户（用完删掉，只删自己造的这一行）。"""
    stamp = now_local()
    customer = Customer(
        name=MARK,
        company_name=None,
        phone=None,
        email=None,
        note="由测试创建，用于验证「无批次客户」的详情与事件流",
        batch_id=None,
        source_type=CustomerSourceType.MANUAL,
        dedupe_state=DedupeState.CLEAN,
        evidence_ref=None,
        first_seen_at=stamp,
        last_seen_at=stamp,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    yield customer
    db.rollback()
    row = db.get(Customer, customer.id)
    if row is not None:
        db.delete(row)
        db.commit()


def _any_customer_with_batch(db: Session) -> Customer:
    row = db.execute(
        select(Customer).where(Customer.batch_id.is_not(None)).order_by(Customer.id.desc())
    ).scalars().first()
    if row is None:  # pragma: no cover - 当前库里必然有
        pytest.skip("库里没有带批次的客户")
    return row


# ─────────────── AC1 / AC5：指标卡 ───────────────


def test_stats_matches_db_counts_and_uses_evidence_values(client: TestClient, db: Session) -> None:
    """AC1：四个指标卡的数字必须与库里的真实条数一致，且全部是 EvidenceValue。"""
    body = client.get("/api/customers/stats").json()

    assert set(body) == {"scope", "total", "test_count", "pending_review", "new_this_week"}
    for key in ("total", "test_count", "pending_review", "new_this_week"):
        assert set(body[key]) == {"value", "state", "source_type", "evidence_ref", "reason"}

    expected_total = db.execute(select(func.count()).select_from(Customer)).scalar_one()
    expected_test = db.execute(
        select(func.count()).select_from(Customer).where(
            Customer.source_type == CustomerSourceType.TEST
        )
    ).scalar_one()
    expected_pending = db.execute(
        select(func.count()).select_from(Customer).where(
            Customer.dedupe_state == DedupeState.PENDING_REVIEW
        )
    ).scalar_one()
    expected_week = db.execute(
        select(func.count()).select_from(Customer).where(Customer.first_seen_at >= _week_start())
    ).scalar_one()

    assert body["total"]["value"] == expected_total
    assert body["test_count"]["value"] == expected_test
    assert body["pending_review"]["value"] == expected_pending
    assert body["new_this_week"]["value"] == expected_week
    assert body["total"]["state"] == "VALID"
    assert body["scope"] == "all"


def test_stats_scope_matches_list_total(client: TestClient) -> None:
    """AC1：指标卡与列表页同一个口径 —— 同一个筛选条件下 total 必须一致。"""
    for params in ({}, {"source_type": "TEST"}, {"q": "张"}, {"source_type": "TEST", "q": "a"}):
        stats = client.get("/api/customers/stats", params=params).json()
        listing = client.get("/api/customers", params={**params, "page_size": 1}).json()
        assert stats["total"]["state"] == listing["total"]["state"]
        assert stats["total"]["value"] == listing["total"]["value"], params


def test_stats_empty_scope_is_no_data_not_zero(client: TestClient, db: Session) -> None:
    """AC5：筛选出一个空集（REAL 一条都没有）→ 四项全部 NO_DATA，绝不用 0 冒充。

    ★ 断言按库里的真实情况走：真有一条 REAL 时就不能要求 NO_DATA（那是 VALID）。
    """
    real_count = db.execute(
        select(func.count()).select_from(Customer).where(
            Customer.source_type == CustomerSourceType.REAL
        )
    ).scalar_one()

    body = client.get("/api/customers/stats", params={"source_type": "REAL"}).json()
    assert body["scope"] == "source_type=REAL"

    if real_count == 0:
        for key in ("total", "test_count", "pending_review", "new_this_week"):
            assert body[key]["state"] == "NO_DATA", key
            assert body[key]["value"] is None, key
            assert body[key]["reason"], key
            assert body[key]["value"] != 0, "NO_DATA 绝不许用 0 冒充"
    else:
        assert body["total"]["state"] == "VALID"
        assert body["total"]["value"] == real_count


def test_stats_search_without_match_is_no_data(client: TestClient) -> None:
    """AC2 后端半边：搜一个不存在的词 → 口径内空集 → 全是 NO_DATA（页面上不出现业务数字）。"""
    body = client.get("/api/customers/stats", params={"q": "zzz-绝对不存在的客户-zzz"}).json()
    assert body["total"]["state"] == "NO_DATA"
    assert body["total"]["value"] is None


def test_stats_zero_is_valid_when_scope_has_rows(client: TestClient, db: Session) -> None:
    """真零要显示 0（VALID），不能显示成「—」—— NO_DATA 与 VALID-0 必须分得开。"""
    total = db.execute(select(func.count()).select_from(Customer)).scalar_one()
    if total == 0:  # pragma: no cover - 空库时才走这里
        pytest.skip("空库：整个口径都是 NO_DATA")

    body = client.get("/api/customers/stats").json()
    week_start = _week_start()
    future = db.execute(
        select(func.count()).select_from(Customer).where(Customer.first_seen_at >= week_start)
    ).scalar_one()
    if future == 0:  # pragma: no cover - 取决于运行日期
        assert body["new_this_week"]["state"] == "VALID"
        assert body["new_this_week"]["value"] == 0
    else:
        assert body["new_this_week"]["state"] == "VALID"
        assert body["new_this_week"]["value"] == future


def test_stats_rejects_unknown_source_type(client: TestClient) -> None:
    resp = client.get("/api/customers/stats", params={"source_type": "NOPE"})
    assert resp.status_code == 400


# ─────────────── AC3：客户详情 ───────────────


def test_detail_returns_all_fields_and_source_traceability(client: TestClient, db: Session) -> None:
    """AC3：详情页要有全部字段 + 来源批次信息 + evidence_ref。"""
    customer = _any_customer_with_batch(db)
    batch = db.get(ImportBatch, customer.batch_id)
    assert batch is not None

    body = client.get(f"/api/customers/{customer.id}").json()

    # 基础字段
    assert body["id"] == customer.id
    assert body["name"] == customer.name
    assert body["phone"] == customer.phone
    assert body["email"] == customer.email
    assert body["region"] == customer.region
    assert body["note"] == customer.note
    assert body["dedupe_state"] == customer.dedupe_state.value
    assert body["first_seen_at"] and body["last_seen_at"]

    # 来源与可追溯
    assert body["source"]["source_type"] == customer.source_type.value
    assert body["source"]["evidence_ref"] == customer.evidence_ref
    assert body["source"]["batch_id"] == customer.batch_id
    assert body["source"]["batch"]["batch_id"] == batch.id
    assert body["source"]["batch"]["filename"] == batch.filename
    assert body["source"]["batch"]["evidence_ref"] == f"import_batch:{batch.id}"
    assert body["source"]["batch"]["imported_at"]


def test_detail_status_is_explicit_placeholder_without_db_column(
    client: TestClient, db: Session
) -> None:
    """状态是**占位**：库里没有 status 列（不许偷偷加回），文案指向 TASK-006。"""
    customer = db.execute(select(Customer).order_by(Customer.id.desc())).scalars().first()
    assert customer is not None

    assert "status" not in {col["name"] for col in inspect(db.get_bind()).get_columns("customers")}

    status = client.get(f"/api/customers/{customer.id}").json()["status"]
    assert status["available"] is False
    assert status["label"] == "未开始跟进"
    assert "TASK-006" in status["note"]


def test_detail_empty_blocks_are_explicit_no_data(
    client: TestClient, manual_customer: Customer
) -> None:
    """AC4：沟通记录 / 风险提醒 / 相关批次明细必须是显式 NO_DATA，不许假数据、不许 0。

    ★ TASK-003 起「沟通记录」区块接真数据，本用例因此把主体从"id 最大的客户"换成
      夹具里这条**确定没有消息**的客户（manual_customer，用完即删）：
      断言一条没动，只是不再依赖"最新那条客户恰好没消息"这种库状态假设。
    """
    customer = manual_customer

    body = client.get(f"/api/customers/{customer.id}").json()
    for key in ("conversations", "risks", "batch_details"):
        block = body[key]
        assert block["state"] == "NO_DATA", key
        assert block["value"] is None, key
        assert block["reason"], key
        assert block["value"] != 0, key

    assert "TASK-003" in body["conversations"]["reason"]
    assert "TASK-005" in body["risks"]["reason"]


def test_detail_batchless_customer_has_null_batch(client: TestClient, manual_customer: Customer) -> None:
    """没有批次的客户（MANUAL）：source.batch 必须是 null，不是编出来的空对象。"""
    body = client.get(f"/api/customers/{manual_customer.id}").json()
    assert body["source"]["source_type"] == "MANUAL"
    assert body["source"]["batch_id"] is None
    assert body["source"]["batch"] is None
    assert body["source"]["evidence_ref"] is None


def test_detail_not_found(client: TestClient) -> None:
    resp = client.get("/api/customers/999999999")
    assert resp.status_code == 404
    assert "不存在" in resp.json()["detail"]


# ─────────────── 事件流 ───────────────


def test_timeline_only_real_events(client: TestClient, db: Session) -> None:
    """事件流只放真实事件：创建 + 来源导入；未接入的类型显式 NO_DATA。"""
    customer = _any_customer_with_batch(db)
    body = client.get(f"/api/customers/{customer.id}/timeline").json()

    assert body["customer_id"] == customer.id
    kinds = [e["kind"] for e in body["events"]]
    assert "CUSTOMER_CREATED" in kinds
    assert "SOURCE_IMPORT" in kinds
    assert not any(k in kinds for k in ("AI_CONVERSATION", "HUMAN_FOLLOW_UP"))

    source_event = next(e for e in body["events"] if e["kind"] == "SOURCE_IMPORT")
    assert source_event["evidence_ref"] == f"import_batch:{customer.batch_id}"
    assert source_event["occurred_at"]

    # 未接入的类型：显式 NO_DATA + 说明，不用空数组冒充"已接入但为空"
    unavailable = {u["kind"]: u for u in body["unavailable"]}
    assert unavailable["AI_CONVERSATION"]["state"] == "NO_DATA"
    assert unavailable["HUMAN_FOLLOW_UP"]["state"] == "NO_DATA"
    assert "TASK-003" in unavailable["AI_CONVERSATION"]["reason"]

    assert body["events_count"]["state"] == "VALID"
    assert body["events_count"]["value"] == len(body["events"])


def test_timeline_batchless_customer_has_no_import_event(
    client: TestClient, manual_customer: Customer
) -> None:
    """没有批次的客户不许出现"来源导入"事件（编事件 = 造假）。"""
    body = client.get(f"/api/customers/{manual_customer.id}/timeline").json()
    kinds = [e["kind"] for e in body["events"]]
    assert "SOURCE_IMPORT" not in kinds
    assert kinds == ["CUSTOMER_CREATED"]


def test_timeline_not_found(client: TestClient) -> None:
    assert client.get("/api/customers/999999999/timeline").status_code == 404


def test_stats_route_is_not_shadowed_by_detail_route(client: TestClient) -> None:
    """/customers/stats 必须真的命中指标卡接口，而不是被 /{customer_id} 当成 id=stats。"""
    resp = client.get("/api/customers/stats")
    assert resp.status_code == 200
    assert "total" in resp.json()


# ─────────────── AC6：本 TASK 只读 ───────────────


def test_get_endpoints_never_write(client: TestClient, db: Session) -> None:
    """AC6：GET 系列不许产生任何新行 —— 调用前后行数与自增 id 完全一致。"""
    def snapshot() -> tuple:
        return (
            db.execute(select(func.count()).select_from(Customer)).scalar_one(),
            db.execute(select(func.coalesce(func.max(Customer.id), 0))).scalar_one(),
            db.execute(select(func.count()).select_from(ImportBatch)).scalar_one(),
            db.execute(select(func.coalesce(func.max(ImportBatch.id), 0))).scalar_one(),
        )

    before = snapshot()
    customer = db.execute(select(Customer).order_by(Customer.id.desc())).scalars().first()
    batch = db.execute(select(ImportBatch).order_by(ImportBatch.id.desc())).scalars().first()
    assert customer is not None and batch is not None

    paths = [
        "/api/customers",
        "/api/customers?page=1&page_size=5&q=a&source_type=TEST",
        "/api/customers/stats",
        "/api/customers/stats?source_type=REAL",
        f"/api/customers/{customer.id}",
        f"/api/customers/{customer.id}/timeline",
        "/api/imports",
        f"/api/imports/{batch.id}",
        "/api/health",
    ]
    for path in paths:
        resp = client.get(path)
        assert resp.status_code == 200, (path, resp.text)

    db.rollback()  # 丢掉本会话可能缓存的快照，重新读库
    assert snapshot() == before, "GET 系列产生了写库操作（本 TASK 必须只读）"
