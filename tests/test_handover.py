"""TASK-005：人工接管三态（D8：AUTO → HUMAN_REQUIRED → HUMAN_ACTIVE）。

真连本机 MySQL（biz_assistant_wt5），不 mock：
  · 服务层直接调（模拟 TASK-004 闸门调用 set_human_required 的接线口）
  · 接口层用 TestClient 走真 HTTP

AC 对照：
  AC1 触发敏感 → HUMAN_REQUIRED（列表 + 详情都可见）
  AC2 接管 → HUMAN_ACTIVE + 事件留痕
  AC3 接管期间敏感内容仍不自动回复（ai_auto_reply_allowed=False）
  AC4 交回 AI → AUTO + 事件留痕
  AC5 每次状态变化都有事件记录（不许只有当前值没有历史）
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.customer import Customer, CustomerSourceType
from app.models.handover import (
    CustomerHandoverEvent,
    HandoverActorType,
    HandoverEventType,
    HandoverState,
)
from app.schemas.evidence import ValueState
from app.services.handover import (
    TRANSITIONS,
    ai_auto_reply_allowed,
    get_handover,
    resume,
    set_human_required,
    takeover,
)

TEST_CUSTOMER_NAME = "接管测试客户_TEST_DO_NOT_KEEP"


@pytest.fixture
def customer(db: Session) -> Generator[Customer, None, None]:
    """建一个 MANUAL 客户做靶子（没有建客户的接口，只能直接写库）。

    测试结束连带清掉它的事件（FK ondelete=CASCADE，这里显式删更直观）。
    """
    c = Customer(name=TEST_CUSTOMER_NAME, source_type=CustomerSourceType.MANUAL)
    db.add(c)
    db.commit()
    db.refresh(c)

    yield c

    db.rollback()
    db.execute(
        delete(CustomerHandoverEvent).where(CustomerHandoverEvent.customer_id == c.id)
    )
    db.execute(delete(Customer).where(Customer.id == c.id))
    db.commit()


def _fresh(db: Session, customer_id: int) -> Customer:
    """读最新状态前先结束当前事务。

    ★ 测试用的 db 是长生命周期 Session，MySQL 默认 REPEATABLE READ：
      不结束事务的话，后续 SELECT 会一直看第一条 SELECT 时建立的旧快照，
      接口（另一个 Session）刚写的事件/状态就"看不见"。rollback 同时会作废缓存对象。
    """
    db.rollback()
    return db.get(Customer, customer_id)


def _events(db: Session, customer_id: int) -> list[CustomerHandoverEvent]:
    db.rollback()
    return list(
        db.execute(
            select(CustomerHandoverEvent)
            .where(CustomerHandoverEvent.customer_id == customer_id)
            .order_by(CustomerHandoverEvent.id.asc())
        )
        .scalars()
        .all()
    )


# ── 纯逻辑（不碰库）：状态机与判据 ──────────────────────────────────────────


def test_ai_auto_reply_allowed_only_when_auto() -> None:
    """AC3 的判据：只有 AUTO 允许 AI 自动回复。"""
    assert ai_auto_reply_allowed(HandoverState.AUTO) is True
    assert ai_auto_reply_allowed(HandoverState.HUMAN_REQUIRED) is False
    assert ai_auto_reply_allowed(HandoverState.HUMAN_ACTIVE) is False


def test_transition_table_matches_d8() -> None:
    """合法迁移写死（D8），不接受工作流引擎式的自由跳转。"""
    assert TRANSITIONS[HandoverState.AUTO] == frozenset(
        {HandoverState.HUMAN_REQUIRED, HandoverState.HUMAN_ACTIVE}
    )
    assert TRANSITIONS[HandoverState.HUMAN_REQUIRED] == frozenset(
        {HandoverState.HUMAN_ACTIVE, HandoverState.AUTO}
    )
    assert TRANSITIONS[HandoverState.HUMAN_ACTIVE] == frozenset({HandoverState.AUTO})


# ── AC1：闸门判定敏感 → HUMAN_REQUIRED，且列表/详情可见 ─────────────────────


def test_set_human_required_marks_customer_visible_in_list_and_detail(
    client: TestClient, db: Session, customer: Customer
) -> None:
    change = set_human_required(
        db, customer.id, reason="命中敏感：报价", rule="QUOTE_PROMISE", matched="已经给你 8 折"
    )
    assert change.changed is True
    assert change.state is HandoverState.HUMAN_REQUIRED

    assert _fresh(db, customer.id).handover_state is HandoverState.HUMAN_REQUIRED

    # 详情可见
    detail = client.get(f"/api/customers/{customer.id}")
    assert detail.status_code == 200
    assert detail.json()["handover_state"] == "HUMAN_REQUIRED"

    # 列表可见（AC1 明确要求列表也能看到）
    listing = client.get("/api/customers", params={"page_size": 100})
    assert listing.status_code == 200
    rows = {item["id"]: item for item in listing.json()["items"]}
    assert rows[customer.id]["handover_state"] == "HUMAN_REQUIRED"

    # 事件里带上了原因与命中片段（可追溯）
    events = _events(db, customer.id)
    assert len(events) == 1
    assert events[0].event_type is HandoverEventType.HUMAN_REQUIRED
    assert events[0].actor_type is HandoverActorType.SYSTEM  # 代码层判定，不是人工也不是 AI
    assert events[0].reason == "命中敏感：报价"
    assert events[0].metadata_json == {"rule": "QUOTE_PROMISE", "matched": "已经给你 8 折"}


def test_set_human_required_is_idempotent(db: Session, customer: Customer) -> None:
    """已经需要人工处理时再判一次敏感 → 不再改状态、不再写事件（历史不灌水）。"""
    first = set_human_required(db, customer.id, reason="命中敏感：折扣")
    second = set_human_required(db, customer.id, reason="命中敏感：折扣")

    assert first.changed is True
    assert second.changed is False
    assert second.state is HandoverState.HUMAN_REQUIRED
    assert second.event is None
    assert len(_events(db, customer.id)) == 1

    # 接管期间再判敏感也不该动 HUMAN_ACTIVE
    takeover(db, customer.id, note="人工接手")
    third = set_human_required(db, customer.id, reason="命中敏感：退款")
    assert third.changed is False
    assert third.state is HandoverState.HUMAN_ACTIVE
    assert len(_events(db, customer.id)) == 2


# ── AC2 / AC3：接管 → HUMAN_ACTIVE + 事件；期间 AI 不许自动回复 ──────────────


def test_takeover_moves_to_human_active_and_writes_human_event(
    client: TestClient, db: Session, customer: Customer
) -> None:
    set_human_required(db, customer.id, reason="命中敏感：合同承诺")

    resp = client.post(f"/api/customers/{customer.id}/takeover", json={"note": "我来处理"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "HUMAN_ACTIVE"
    assert body["changed"] is True
    assert body["ai_auto_reply_allowed"] is False  # AC3：接管期间 AI 不自动回复
    assert body["event"]["actor_type"] == "HUMAN"  # AC2：写事件 actor_type=HUMAN
    assert body["event"]["event_type"] == "TAKEN_OVER"
    assert body["event"]["from_state"] == "HUMAN_REQUIRED"
    assert body["event"]["to_state"] == "HUMAN_ACTIVE"
    assert body["event"]["reason"] == "我来处理"

    assert _fresh(db, customer.id).handover_state is HandoverState.HUMAN_ACTIVE


def test_takeover_without_body_works(client: TestClient, db: Session, customer: Customer) -> None:
    """人工点按钮不该强制带 body。"""
    resp = client.post(f"/api/customers/{customer.id}/takeover")
    assert resp.status_code == 200
    assert resp.json()["state"] == "HUMAN_ACTIVE"


def test_takeover_directly_from_auto_is_allowed(
    client: TestClient, db: Session, customer: Customer
) -> None:
    """人工可以主动接管（还没触发敏感也行）：AUTO → HUMAN_ACTIVE。"""
    resp = client.post(f"/api/customers/{customer.id}/takeover")
    assert resp.status_code == 200
    assert resp.json()["state"] == "HUMAN_ACTIVE"


def test_ai_auto_reply_blocked_in_both_manual_states(
    client: TestClient, db: Session, customer: Customer
) -> None:
    """AC3：HUMAN_REQUIRED 与 HUMAN_ACTIVE 期间，AI 都不得自动回复。"""
    set_human_required(db, customer.id, reason="命中敏感：大额订单")
    required = client.get(f"/api/customers/{customer.id}/handover").json()
    assert required["state"] == "HUMAN_REQUIRED"
    assert required["ai_auto_reply_allowed"] is False

    client.post(f"/api/customers/{customer.id}/takeover")
    active = client.get(f"/api/customers/{customer.id}/handover").json()
    assert active["state"] == "HUMAN_ACTIVE"
    assert active["ai_auto_reply_allowed"] is False


# ── AC4：交回 AI → AUTO + 事件 ──────────────────────────────────────────────


def test_resume_returns_to_auto_and_writes_event(
    client: TestClient, db: Session, customer: Customer
) -> None:
    set_human_required(db, customer.id, reason="命中敏感：赔偿")
    client.post(f"/api/customers/{customer.id}/takeover", json={"note": "接手"})

    resp = client.post(f"/api/customers/{customer.id}/resume", json={"note": "处理完了"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "AUTO"
    assert body["changed"] is True
    assert body["ai_auto_reply_allowed"] is True  # 交回后 AI 恢复自动回复
    assert body["event"]["event_type"] == "RESUMED"
    assert body["event"]["from_state"] == "HUMAN_ACTIVE"
    assert body["event"]["to_state"] == "AUTO"

    assert _fresh(db, customer.id).handover_state is HandoverState.AUTO
    assert ai_auto_reply_allowed(HandoverState.AUTO) is True


def test_resume_from_human_required_is_allowed(
    client: TestClient, db: Session, customer: Customer
) -> None:
    """人工看过判定无需处理 → 直接从 HUMAN_REQUIRED 交回 AI。"""
    set_human_required(db, customer.id, reason="命中敏感：折扣")
    resp = client.post(f"/api/customers/{customer.id}/resume")
    assert resp.status_code == 200
    assert resp.json()["state"] == "AUTO"


def test_resume_when_already_auto_is_noop(db: Session, customer: Customer) -> None:
    """本来就在 AUTO：返回说明，不写事件。"""
    change = resume(db, customer.id)
    assert change.changed is False
    assert change.event is None
    assert change.state is HandoverState.AUTO
    assert _events(db, customer.id) == []


# ── AC5：完整历史（当前值 + 历史都要有） ────────────────────────────────────


def test_full_cycle_keeps_complete_history(
    client: TestClient, db: Session, customer: Customer
) -> None:
    """敏感 → 接管 → 交回：三次状态变化 = 三条事件，历史一条不丢。"""
    set_human_required(db, customer.id, reason="命中敏感：退款")
    client.post(f"/api/customers/{customer.id}/takeover", json={"note": "接手"})
    client.post(f"/api/customers/{customer.id}/resume", json={"note": "完成"})

    resp = client.get(f"/api/customers/{customer.id}/handover")
    assert resp.status_code == 200
    body = resp.json()

    assert body["state"] == "AUTO"
    assert [e["event_type"] for e in body["events"]] == [
        "HUMAN_REQUIRED",
        "TAKEN_OVER",
        "RESUMED",
    ]
    assert [(e["from_state"], e["to_state"]) for e in body["events"]] == [
        ("AUTO", "HUMAN_REQUIRED"),
        ("HUMAN_REQUIRED", "HUMAN_ACTIVE"),
        ("HUMAN_ACTIVE", "AUTO"),
    ]
    assert body["event_count"]["state"] == "VALID"
    assert body["event_count"]["value"] == 3
    assert body["since"] is not None

    # 库里确实是三条，不是接口拼出来的
    assert len(_events(db, customer.id)) == 3


def test_every_event_has_generated_evidence_ref(db: Session, customer: Customer) -> None:
    """D5：每条事件都有代码生成的证据锚点，禁止手填。"""
    set_human_required(db, customer.id, reason="命中敏感：付款")
    takeover(db, customer.id)

    for ev in _events(db, customer.id):
        assert ev.evidence_ref == f"customer_handover_event:{ev.id}"
        assert ev.evidence_ref  # 不许是空串


# ── 空态：没有接管历史 → NO_DATA（不是 0、不是假事件） ──────────────────────


def test_no_history_is_no_data_not_zero(client: TestClient, db: Session, customer: Customer) -> None:
    resp = client.get(f"/api/customers/{customer.id}/handover")
    assert resp.status_code == 200
    body = resp.json()

    assert body["state"] == "AUTO"
    assert body["events"] == []
    assert body["event_count"]["state"] == "NO_DATA"
    assert body["event_count"]["value"] is None
    assert body["since"] is None

    data = get_handover(db, customer.id)
    assert data["event_count"].state is ValueState.NO_DATA


# ── 硬边界：AI 不许改状态；客户不存在 ───────────────────────────────────────


def test_ai_actor_is_forbidden_and_writes_no_event(
    client: TestClient, db: Session, customer: Customer
) -> None:
    """D7/D8：AI 不许自行改客户接管状态 → 403，且**不产生**任何事件。"""
    resp = client.post(
        f"/api/customers/{customer.id}/takeover", json={"actor_type": "AI", "note": "AI 想接管"}
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "ai_actor_forbidden"
    assert _fresh(db, customer.id).handover_state is HandoverState.AUTO
    assert _events(db, customer.id) == []


def test_unknown_customer_returns_404(client: TestClient) -> None:
    resp = client.post("/api/customers/999999999/takeover")
    assert resp.status_code == 404
    assert resp.json()["error"] == "customer_not_found"


def test_invalid_actor_type_is_400(client: TestClient, customer: Customer) -> None:
    resp = client.post(
        f"/api/customers/{customer.id}/takeover", json={"actor_type": "ROBOT"}
    )
    assert resp.status_code == 422  # pydantic 枚举校验
