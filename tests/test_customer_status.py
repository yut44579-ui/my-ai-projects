"""TASK-006：客户状态机 + 事件留痕（AC1–AC5）。

真实库、真 HTTP 调用（TestClient），不 mock。每个用例都用 test_customer 夹具建临时客户并在结束后清理。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.customer import Customer, LifecycleStatus
from app.models.customer_event import ActorType, CustomerEvent, CustomerEventType, EventSourceType
from tests.conftest import fresh, refresh_events


def _post_status(client: TestClient, customer_id: int, to_status: str, actor_type: str = "HUMAN", **extra):
    return client.post(
        f"/api/customers/{customer_id}/status",
        json={"to_status": to_status, "actor_type": actor_type, **extra},
    )


# ---------------------------------------------------------------- AC1
def test_ac1_status_change_updates_column_and_writes_event(
    client: TestClient, db: Session, test_customer: Customer
) -> None:
    """改状态 → customers.lifecycle_status 更新，且多一条 STATUS_CHANGED（from/to/actor 正确）。"""
    before = len(refresh_events(db, test_customer.id))

    resp = _post_status(client, test_customer.id, "CONTACTED", "HUMAN", note="首次电话触达")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["changed"] is True
    assert body["lifecycle_status"] == "CONTACTED"

    # 主表真的被改了
    fresh(db)
    assert db.get(Customer, test_customer.id).lifecycle_status is LifecycleStatus.CONTACTED

    # 事件真的落库了，且只有一条新增
    events = refresh_events(db, test_customer.id)
    assert len(events) == before + 1, "每次状态变化必须恰好留一条事件"

    ev = events[-1]
    assert ev.event_type is CustomerEventType.STATUS_CHANGED
    assert ev.from_status is LifecycleStatus.NEW
    assert ev.to_status is LifecycleStatus.CONTACTED
    assert ev.actor_type is ActorType.HUMAN
    assert ev.source_type is EventSourceType.MANUAL, "人工变更的 source_type 必须是 MANUAL"
    assert ev.evidence_ref == f"customer_event:{ev.id}", "证据锚点必须由代码生成且指向本行"
    assert ev.metadata_json == {"note": "首次电话触达"}

    # 返回体里的事件与库里一致
    assert body["event"]["to_status"] == "CONTACTED"
    assert body["event"]["actor_type"] == "HUMAN"
    assert body["event"]["evidence_ref"] == f"customer_event:{ev.id}"


# ---------------------------------------------------------------- AC2
def test_ac2_three_consecutive_changes_keep_full_history(
    client: TestClient, db: Session, test_customer: Customer
) -> None:
    """连续改三次 → 三条事件齐全，事件流按时间倒序，历史不丢。"""
    for status in ("CONTACTED", "REPLIED", "ENGAGED"):
        resp = _post_status(client, test_customer.id, status)
        assert resp.status_code == 200, resp.text

    events = refresh_events(db, test_customer.id)
    assert len(events) == 3, f"三次变更必须有三条事件，实际 {len(events)}"

    # 发生顺序（id 升序）必须是 NEW→CONTACTED→REPLIED→ENGAGED 的链
    chain = [(e.from_status.value, e.to_status.value) for e in events]
    assert chain == [
        ("NEW", "CONTACTED"),
        ("CONTACTED", "REPLIED"),
        ("REPLIED", "ENGAGED"),
    ]
    # 每条事件的首尾相接 = 历史连续，没有被覆盖
    assert events[0].to_status == events[1].from_status
    assert events[1].to_status == events[2].from_status

    # 事件流接口必须是时间倒序（最新在前）
    resp = client.get(f"/api/customers/{test_customer.id}/events")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [i["to_status"] for i in body["items"]] == ["ENGAGED", "REPLIED", "CONTACTED"]
    assert body["total"] == {"value": 3, "state": "VALID", "source_type": "SYSTEM",
                             "evidence_ref": f"customer:{test_customer.id}:events", "reason": None}


# ---------------------------------------------------------------- AC3
def test_ac3_ai_actor_is_rejected_and_writes_no_event(
    client: TestClient, db: Session, test_customer: Customer
) -> None:
    """actor_type=AI → 403，且**没有**产生事件、主表状态不变。"""
    before = len(refresh_events(db, test_customer.id))

    resp = _post_status(client, test_customer.id, "WON", "AI")
    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert "AI" in detail and "人工" in detail, f"403 必须说明原因，实际：{detail}"

    assert len(refresh_events(db, test_customer.id)) == before, "AI 被拒后绝不能留下事件"
    fresh(db)
    assert db.get(Customer, test_customer.id).lifecycle_status is LifecycleStatus.NEW


def test_ac3b_ai_request_header_is_rejected_too(
    client: TestClient, db: Session, test_customer: Customer
) -> None:
    """来源为 AI 的请求（请求头标记）同样 403，且不留痕。"""
    before = len(refresh_events(db, test_customer.id))

    resp = client.post(
        f"/api/customers/{test_customer.id}/status",
        json={"to_status": "WON", "actor_type": "HUMAN"},
        headers={"X-Actor-Type": "AI"},
    )
    assert resp.status_code == 403, resp.text
    assert len(refresh_events(db, test_customer.id)) == before
    fresh(db)
    assert db.get(Customer, test_customer.id).lifecycle_status is LifecycleStatus.NEW


# ---------------------------------------------------------------- AC4
def test_ac4_invalid_status_value_returns_400(
    client: TestClient, db: Session, test_customer: Customer
) -> None:
    """合法状态值之外的输入 → 400（不是 422，也不是悄悄兜底）。"""
    before = len(refresh_events(db, test_customer.id))

    resp = _post_status(client, test_customer.id, "PENDING_WHATEVER")
    assert resp.status_code == 400, f"非法状态值必须 400，实际 {resp.status_code}: {resp.text}"
    assert "非法状态值" in resp.json()["detail"]

    # 非法请求不许有任何副作用
    assert len(refresh_events(db, test_customer.id)) == before
    fresh(db)
    assert db.get(Customer, test_customer.id).lifecycle_status is LifecycleStatus.NEW


def test_ac4b_invalid_actor_type_returns_400(
    client: TestClient, db: Session, test_customer: Customer
) -> None:
    resp = _post_status(client, test_customer.id, "CONTACTED", "ROBOT")
    assert resp.status_code == 400, resp.text
    assert "actor_type" in resp.json()["detail"]


# ---------------------------------------------------------------- AC5
def test_ac5_customer_without_events_returns_no_data(
    client: TestClient, test_customer: Customer
) -> None:
    """没有事件历史的客户 → 空列表 + NO_DATA 空态（不是 0、不是假事件）。"""
    resp = client.get(f"/api/customers/{test_customer.id}/events")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["items"] == [], "无事件必须是空列表，不许塞占位事件"
    assert body["total"]["state"] == "NO_DATA"
    assert body["total"]["value"] is None, "NO_DATA 必须 value=null，禁止用 0 冒充"
    assert body["total"]["reason"], "NO_DATA 必须说明原因"


def test_ac5b_events_of_missing_customer_is_404(client: TestClient) -> None:
    assert client.get("/api/customers/99999999/events").status_code == 404
    assert client.get("/api/customers/99999999").status_code == 404


# ---------------------------------------------------------------- 其它必备行为
def test_no_change_writes_no_event(
    client: TestClient, db: Session, test_customer: Customer
) -> None:
    """同值调用：不写事件，返回说明。"""
    before = len(refresh_events(db, test_customer.id))

    resp = _post_status(client, test_customer.id, "NEW")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["changed"] is False
    assert body["event"] is None
    assert "未发生变化" in body["message"]
    assert len(refresh_events(db, test_customer.id)) == before


def test_detail_exposes_lifecycle_status(
    client: TestClient, db: Session, test_customer: Customer
) -> None:
    """详情接口补上 lifecycle_status，且不改变已有字段语义。"""
    assert client.get(f"/api/customers/{test_customer.id}").json()["lifecycle_status"] == "NEW"

    assert _post_status(client, test_customer.id, "QUOTED").status_code == 200
    detail = client.get(f"/api/customers/{test_customer.id}").json()
    assert detail["lifecycle_status"] == "QUOTED"
    # 既有字段语义没被改动
    assert detail["id"] == test_customer.id
    assert detail["source_type"] == "TEST"
    assert detail["dedupe_state"] == "CLEAN"
    assert set(detail) >= {"name", "phone", "email", "evidence_ref", "first_seen_at", "last_seen_at"}


def test_list_can_filter_by_lifecycle_status(
    client: TestClient, db: Session, test_customer: Customer
) -> None:
    """列表按状态筛选（前端状态筛选列要用）。"""
    assert _post_status(client, test_customer.id, "WON").status_code == 200

    resp = client.get("/api/customers", params={"lifecycle_status": "WON", "page_size": 100})
    assert resp.status_code == 200, resp.text
    ids = [i["id"] for i in resp.json()["items"]]
    assert test_customer.id in ids
    assert all(i["lifecycle_status"] == "WON" for i in resp.json()["items"])

    # 非法筛选值同样 400，不静默忽略
    assert client.get("/api/customers", params={"lifecycle_status": "BOGUS"}).status_code == 400


def test_events_are_paginated_newest_first(
    client: TestClient, db: Session, test_customer: Customer
) -> None:
    """分页参数生效，且倒序稳定（同一秒内连改也能正确排序）。"""
    for status in ("CONTACTED", "REPLIED", "ENGAGED", "QUOTED"):
        assert _post_status(client, test_customer.id, status).status_code == 200

    page1 = client.get(
        f"/api/customers/{test_customer.id}/events", params={"page": 1, "page_size": 2}
    ).json()
    page2 = client.get(
        f"/api/customers/{test_customer.id}/events", params={"page": 2, "page_size": 2}
    ).json()

    assert [i["to_status"] for i in page1["items"]] == ["QUOTED", "ENGAGED"]
    assert [i["to_status"] for i in page2["items"]] == ["REPLIED", "CONTACTED"]
    assert page1["total"]["value"] == 4

    fresh(db)
    assert db.execute(
        select(func.count()).select_from(CustomerEvent).where(
            CustomerEvent.customer_id == test_customer.id
        )
    ).scalar_one() == 4
