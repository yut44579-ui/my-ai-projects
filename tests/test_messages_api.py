"""TASK-003 沟通记录测试：AC1 / AC2 / AC3 / AC4 / AC5 + 落库与闸门细节。

★ 测试只碰自己造的客户（MANUAL 来源，id > 基线），结束时把消息与客户一起删干净 ——
  否则其它测试里「取 id 最大的客户」会取到带消息的客户，空态断言就不再成立。
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.customer import Customer, CustomerSourceType, DedupeState
from app.models.customer_message import (
    AiStatus,
    CustomerMessage,
    MessageSourceType,
    MessageType,
    SenderType,
)
from app.services.errors import ApiErrorCode, ApiFailure
from app.services.messaging import write_message


@pytest.fixture
def temp_customer(db: Session) -> Generator[Customer, None, None]:
    """一个干净的 MANUAL 客户（测试前后各清一次，只删自己造的）。"""
    baseline = db.execute(select(func.coalesce(func.max(Customer.id), 0))).scalar_one()

    def purge() -> None:
        db.rollback()
        db.execute(delete(CustomerMessage).where(CustomerMessage.customer_id > baseline))
        db.execute(delete(Customer).where(Customer.id > baseline))
        db.commit()

    purge()
    customer = Customer(
        name="沟通记录测试客户",
        company_name="TASK-003 测试",
        source_type=CustomerSourceType.MANUAL,
        dedupe_state=DedupeState.CLEAN,
        evidence_ref=None,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)

    yield customer
    purge()


def post_message(client: TestClient, customer_id: int, content: str, **extra):
    payload = {"content": content, **extra}
    return client.post(f"/api/customers/{customer_id}/messages", json=payload)


def _fresh(db: Session) -> None:
    """结束当前读事务，避免 MySQL REPEATABLE READ 让本会话拿到旧快照。

    接口是用自己的 Session 写库并 commit 的，本会话必须重新开始事务才看得到
    （与 tests/test_imports_api.py 里 `db.rollback()` 的用法同源）。
    """
    db.rollback()


def _count_messages(db: Session, customer_id: int) -> int:
    _fresh(db)
    return db.execute(
        select(func.count())
        .select_from(CustomerMessage)
        .where(CustomerMessage.customer_id == customer_id)
    ).scalar_one()


# ─────────────── AC1 ───────────────


def test_ac1_post_message_returns_200_and_shows_up_in_timeline(
    client: TestClient, db: Session, temp_customer: Customer
) -> None:
    """AC1：发一条人工消息 → 200，且时间线里出现，带 evidence_ref。"""
    resp = post_message(client, temp_customer.id, "客户来电询问续约事宜")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["customer_id"] == temp_customer.id
    assert body["sender_type"] == "HUMAN"  # 人工发送
    assert body["message_type"] == "CHAT"
    assert body["source_type"] == "MANUAL"  # 写库必带 source_type
    assert body["content"] == "客户来电询问续约事宜"
    assert body["ai_status"] == "NONE"  # 人工消息与 AI 无关
    assert body["evidence_ref"] == f"customer_message:{body['id']}"  # 由代码生成
    assert body["created_at"]

    timeline = client.get(f"/api/customers/{temp_customer.id}/messages").json()
    assert [item["id"] for item in timeline["items"]] == [body["id"]]
    assert timeline["items"][0]["evidence_ref"] == f"customer_message:{body['id']}"
    assert timeline["total"]["state"] == "VALID"
    assert timeline["total"]["value"] == 1

    # 库里确实有这一行（业务字段不是靠前端拼的）
    _fresh(db)
    row = db.get(CustomerMessage, body["id"])
    assert row is not None
    assert row.sender_type is SenderType.HUMAN
    assert row.source_type is MessageSourceType.MANUAL
    assert row.evidence_ref == f"customer_message:{row.id}"


# ─────────────── AC2 ───────────────


def test_ac2_message_is_really_persisted_not_frontend_state(
    client: TestClient, db: Session, temp_customer: Customer
) -> None:
    """AC2：换一个全新数据库会话（模拟"刷新页面"）仍能读到 —— 真落库，不是前端态。"""
    message_id = post_message(client, temp_customer.id, "刷新后必须还在").json()["id"]
    db.rollback()  # 丢掉本会话快照

    fresh = SessionLocal()
    try:
        row = fresh.get(CustomerMessage, message_id)
        assert row is not None, "消息必须真落库"
        assert row.content == "刷新后必须还在"
        assert row.sender_type is SenderType.HUMAN
    finally:
        fresh.close()

    again = client.get(f"/api/customers/{temp_customer.id}/messages").json()
    assert [item["content"] for item in again["items"]] == ["刷新后必须还在"]


# ─────────────── AC3 ───────────────


def test_ac3_customer_sender_is_rejected_with_explicit_error_code(
    client: TestClient, db: Session, temp_customer: Customer
) -> None:
    """AC3：sender_type=CUSTOMER → 400 + 明确错误码，且一行都不许落库。"""
    before = _count_messages(db, temp_customer.id)

    resp = post_message(
        client, temp_customer.id, "你好，我是客户本人（伪造）", sender_type="CUSTOMER"
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["error"] == "customer_sender_forbidden"
    assert "D9" in body["message"] or "禁止" in body["message"]
    assert body["allowed"] == ["HUMAN", "SYSTEM"]  # ★ CUSTOMER 不在允许列表里

    assert _count_messages(db, temp_customer.id) == before, "被拒绝的请求不许留下任何行"


def test_ac3_service_layer_also_refuses_customer_sender(
    db: Session, temp_customer: Customer
) -> None:
    """服务层是最后一道闸门：绕过 HTTP 直接调用同样写不进去（不靠调用方自觉）。"""
    with pytest.raises(ApiFailure) as caught:
        write_message(
            db,
            customer_id=temp_customer.id,
            content="伪造的客户消息",
            sender_type=SenderType.CUSTOMER,
        )
    assert caught.value.code is ApiErrorCode.CUSTOMER_SENDER_FORBIDDEN
    assert _count_messages(db, temp_customer.id) == 0


def test_customer_messages_never_appear_even_after_other_writes(
    client: TestClient, temp_customer: Customer
) -> None:
    """写入过 HUMAN / SYSTEM / 随导入带入（message_type=IMPORT）之后，时间线里依然不可能出现 CUSTOMER。"""
    assert post_message(client, temp_customer.id, "人工发的", sender_type="HUMAN").status_code == 200
    assert post_message(client, temp_customer.id, "系统发的", sender_type="SYSTEM").status_code == 200
    assert (
        post_message(
            client,
            temp_customer.id,
            "随导入带入的",
            sender_type="SYSTEM",
            message_type="IMPORT",
        ).status_code
        == 200
    )

    timeline = client.get(f"/api/customers/{temp_customer.id}/messages").json()
    assert len(timeline["items"]) == 3
    assert "CUSTOMER" not in {item["sender_type"] for item in timeline["items"]}
    # source_type 跟着发送方 / message_type 走，不许调用方随便传
    assert {item["source_type"] for item in timeline["items"]} == {"MANUAL", "SYSTEM", "IMPORT"}


# ─────────────── AC4 ───────────────


def test_ac4_customer_without_messages_is_explicit_no_data(
    client: TestClient, temp_customer: Customer
) -> None:
    """AC4：无消息客户 → 时间线 NO_DATA 空态，界面无数字（value 必须是 null，不是 0）。"""
    body = client.get(f"/api/customers/{temp_customer.id}/messages").json()

    assert body["items"] == []
    assert body["total"]["state"] == "NO_DATA"
    assert body["total"]["value"] is None
    assert body["total"]["reason"] == "暂无沟通记录"
    assert body["total"]["value"] != 0, "绝不许用 0 冒充『没有数据』"
    # 分页元数据不是业务数字，正常返回裸值
    assert body["page"] == 1 and body["page_size"] == 20

    # 详情页「沟通记录」区块同步为空态（TASK-003 起接真数据，空态依旧显式）
    detail = client.get(f"/api/customers/{temp_customer.id}").json()
    assert detail["conversations"]["state"] == "NO_DATA"
    assert detail["conversations"]["value"] is None


# ─────────────── AC5 ───────────────


def test_ac5_message_count_is_evidence_value(
    client: TestClient, db: Session, temp_customer: Customer
) -> None:
    """AC5：该客户的消息条数走 EvidenceValue（前端不许自己数 items.length）。"""
    for index in range(3):
        assert post_message(client, temp_customer.id, f"第 {index + 1} 条").status_code == 200

    timeline = client.get(f"/api/customers/{temp_customer.id}/messages").json()
    assert timeline["total"]["state"] == "VALID"
    assert timeline["total"]["value"] == 3
    assert timeline["total"]["source_type"] == "SYSTEM"
    assert timeline["total"]["evidence_ref"] == f"customer_messages:count|customer_id={temp_customer.id}"

    detail = client.get(f"/api/customers/{temp_customer.id}").json()
    assert detail["conversations"]["value"] == 3
    assert detail["conversations"]["state"] == "VALID"


# ─────────────── 时间线顺序 / 分页 / 404 / 校验 ───────────────


def test_timeline_is_ascending_and_paginated(
    client: TestClient, temp_customer: Customer
) -> None:
    """时间线按时间正序（老消息在上），分页只影响 items、不影响 total。"""
    ids = [post_message(client, temp_customer.id, f"消息 {i}").json()["id"] for i in range(5)]

    first = client.get(
        f"/api/customers/{temp_customer.id}/messages", params={"page": 1, "page_size": 2}
    ).json()
    assert [item["id"] for item in first["items"]] == ids[:2]
    assert first["total"]["value"] == 5  # total 是业务数字：不受分页影响

    second = client.get(
        f"/api/customers/{temp_customer.id}/messages", params={"page": 2, "page_size": 2}
    ).json()
    assert [item["id"] for item in second["items"]] == ids[2:4]

    third = client.get(
        f"/api/customers/{temp_customer.id}/messages", params={"page": 3, "page_size": 2}
    ).json()
    assert [item["id"] for item in third["items"]] == ids[4:]


def test_missing_customer_is_404_and_writes_nothing(
    client: TestClient, db: Session
) -> None:
    """对不存在的客户：GET/POST 都是 404。"""
    assert client.get("/api/customers/999999999/messages").status_code == 404
    assert post_message(client, 999999999, "写给不存在的客户").status_code == 404
    assert _count_messages(db, 999999999) == 0


def test_blank_content_is_rejected(client: TestClient, temp_customer: Customer) -> None:
    """空 / 纯空白正文：接口层直接拒（不许落一行空消息）。"""
    assert post_message(client, temp_customer.id, "   ").status_code == 422
    assert post_message(client, temp_customer.id, "").status_code == 422


def test_unknown_sender_type_is_rejected(client: TestClient, temp_customer: Customer) -> None:
    """枚举外的 sender_type 一律拒绝。"""
    assert post_message(client, temp_customer.id, "你好", sender_type="ROBOT").status_code == 422


def test_message_type_note_is_persisted(client: TestClient, db: Session, temp_customer: Customer) -> None:
    """NOTE 类型（备注）能正常落库，且 message_type 原样返回。"""
    body = post_message(client, temp_customer.id, "仅内部可见的备注", message_type="NOTE").json()
    assert body["message_type"] == "NOTE"
    _fresh(db)
    row = db.get(CustomerMessage, body["id"])
    assert row is not None and row.message_type is MessageType.NOTE and row.ai_status is AiStatus.NONE
