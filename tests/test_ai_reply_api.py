"""TASK-004 接口测试：AC1 / AC2 / AC3 + 顺序、落库、失败兜底。

★ 用 monkeypatch 计数断言"命中敏感时一次 LLM 都没调"（AC2）——
  计数打在路由模块引用的 generate_reply 上，走的是真实代码路径，只是把网络换成假实现。
★ 所有测试都不碰真实 LLM：要么 monkeypatch，要么命中闸门。
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.customer import Customer, CustomerSourceType, DedupeState
from app.models.customer_message import AiStatus, CustomerMessage, SenderType
from app.services import ai_reply as ai_reply_service
from app.services import llm as llm_service
from app.services.ai_reply import LLM_FAILURE_TEXT

NORMAL_QUESTION = "你们的售后支持时间是怎样的？"
SENSITIVE_QUESTION = "老客户有没有折扣？"


class FakeLLM:
    """假的 LLM：只计数，不打网络。"""

    def __init__(self, reply: str = "我们的售后支持时间是工作日 9:00-18:00。") -> None:
        self.calls: list[str] = []
        self.reply = reply
        self.raises: Exception | None = None

    def __call__(self, question: str, *, customer_name: str | None = None) -> str:
        self.calls.append(question)
        if self.raises is not None:
            raise self.raises
        return self.reply


@pytest.fixture
def temp_customer(db: Session) -> Generator[Customer, None, None]:
    """干净客户（MANUAL 来源，只删自己造的）。"""
    baseline = db.execute(select(func.coalesce(func.max(Customer.id), 0))).scalar_one()

    def purge() -> None:
        db.rollback()
        db.execute(delete(CustomerMessage).where(CustomerMessage.customer_id > baseline))
        db.execute(delete(Customer).where(Customer.id > baseline))
        db.commit()

    purge()
    customer = Customer(
        name="AI 回复测试客户",
        company_name="TASK-004 测试",
        source_type=CustomerSourceType.MANUAL,
        dedupe_state=DedupeState.CLEAN,
        evidence_ref=None,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    yield customer
    purge()


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    """把编排里用的 generate_reply 换成计数器（真实代码路径 + 假网络）。"""
    stub = FakeLLM()
    monkeypatch.setattr(ai_reply_service, "generate_reply", stub)
    return stub


def _fresh(db: Session) -> None:
    """结束读事务，避免 MySQL REPEATABLE READ 拿到旧快照。"""
    db.rollback()


def _timeline(client: TestClient, customer_id: int) -> dict:
    return client.get(f"/api/customers/{customer_id}/messages").json()


# ─────────────── AC1 ───────────────


def test_ac1_normal_question_gets_ai_reply_and_is_persisted(
    client: TestClient, db: Session, temp_customer: Customer, fake_llm: FakeLLM
) -> None:
    """AC1：普通问题 → 调 LLM → AI 回复落库（sender_type=AI, ai_status=REPLIED）。"""
    resp = client.post(
        f"/api/customers/{temp_customer.id}/ai-reply", json={"content": NORMAL_QUESTION}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["llm_called"] is True
    assert len(fake_llm.calls) == 1, "普通问题必须真的调用一次 LLM"
    assert body["policy"]["allowed"] is True
    assert body["ai_status"] == "REPLIED"
    assert body["message"]["sender_type"] == "AI"
    assert body["message"]["content"] == fake_llm.reply
    assert body["message"]["evidence_ref"] == f"customer_message:{body['message']['id']}"
    assert body["failure_reason"] is None

    # 真落库（换一个会话读）
    _fresh(db)
    row = db.get(CustomerMessage, body["message"]["id"])
    assert row is not None
    assert row.sender_type is SenderType.AI
    assert row.ai_status is AiStatus.REPLIED
    assert row.source_type.value == "SYSTEM"

    timeline = _timeline(client, temp_customer.id)
    assert [item["sender_type"] for item in timeline["items"]] == ["AI"]
    assert timeline["total"]["value"] == 1


def test_ac1_question_can_come_from_a_recorded_message(
    client: TestClient, temp_customer: Customer, fake_llm: FakeLLM
) -> None:
    """客户问题也可以指向"已录入的那条消息"（in_reply_to）。"""
    recorded = client.post(
        f"/api/customers/{temp_customer.id}/messages",
        json={"content": NORMAL_QUESTION, "sender_type": "HUMAN"},
    ).json()

    body = client.post(
        f"/api/customers/{temp_customer.id}/ai-reply", json={"in_reply_to": recorded["id"]}
    ).json()

    assert body["question"] == NORMAL_QUESTION
    assert fake_llm.calls == [NORMAL_QUESTION]
    assert body["ai_status"] == "REPLIED"

    # 指向别的客户的消息 → 404（不许跨客户取内容）
    other = client.post(
        f"/api/customers/{temp_customer.id}/messages", json={"content": "另一条"}
    ).json()
    assert (
        client.post("/api/customers/999999999/ai-reply", json={"in_reply_to": other["id"]}).status_code
        == 404
    )


# ─────────────── AC2 ───────────────


def test_ac2_sensitive_question_never_calls_llm_and_records_human_required(
    client: TestClient, db: Session, temp_customer: Customer, fake_llm: FakeLLM
) -> None:
    """AC2：敏感问题 → ① 一次 LLM 都不调 ② 落库 HUMAN_REQUIRED ③ 没有 AI 回复。"""
    resp = client.post(
        f"/api/customers/{temp_customer.id}/ai-reply", json={"content": SENSITIVE_QUESTION}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert fake_llm.calls == [], "★ 命中敏感策略时绝不允许调用 LLM"
    assert body["llm_called"] is False
    assert body["policy"]["allowed"] is False
    assert body["policy"]["category"] == "DISCOUNT"
    assert body["policy"]["matched"] == "折扣"
    assert body["ai_status"] == "HUMAN_REQUIRED"
    assert body["message"]["sender_type"] == "SYSTEM"  # 转人工说明，不是 AI 回复
    assert "转人工" in body["message"]["content"]

    _fresh(db)
    assert (
        db.execute(
            select(func.count())
            .select_from(CustomerMessage)
            .where(
                CustomerMessage.customer_id == temp_customer.id,
                CustomerMessage.sender_type == SenderType.AI,
            )
        ).scalar_one()
        == 0
    ), "敏感问题下不许有任何 AI 回复落库"

    timeline = _timeline(client, temp_customer.id)
    assert [item["ai_status"] for item in timeline["items"]] == ["HUMAN_REQUIRED"]


@pytest.mark.parametrize(
    "question",
    [
        "麻烦把报价单发我",
        "合同今天能签约吗",
        "退款怎么办",
        "请提供你们的银行账户",
        "这批大额订单要赔偿",
        "我要投诉你们",
    ],
)
def test_ac2_each_sensitive_kind_blocks_before_llm(
    client: TestClient, temp_customer: Customer, fake_llm: FakeLLM, question: str
) -> None:
    """多种敏感问题都必须在**调用 LLM 之前**被拦下。"""
    body = client.post(
        f"/api/customers/{temp_customer.id}/ai-reply", json={"content": question}
    ).json()
    assert body["llm_called"] is False
    assert body["ai_status"] == "HUMAN_REQUIRED"
    assert fake_llm.calls == []


# ─────────────── AC3 ───────────────


def test_ac3_llm_failure_records_failed_with_fallback_text_and_no_fake_reply(
    client: TestClient, db: Session, temp_customer: Customer, fake_llm: FakeLLM
) -> None:
    """AC3：LLM 失败 → 落 FAILED + 人工兜底文案，库里没有任何假回复。"""
    fake_llm.raises = llm_service.LLMUnavailable("模拟超时：ReadTimeout")

    resp = client.post(
        f"/api/customers/{temp_customer.id}/ai-reply", json={"content": NORMAL_QUESTION}
    )
    assert resp.status_code == 502, resp.text
    error = resp.json()
    assert error["error"] == "llm_unavailable"
    assert error["message"] == LLM_FAILURE_TEXT  # 「AI 暂时无法回复，请人工处理」
    assert error["ai_status"] == "FAILED"
    assert "ReadTimeout" in error["reason"]

    _fresh(db)
    rows = (
        db.execute(
            select(CustomerMessage).where(CustomerMessage.customer_id == temp_customer.id)
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1, "失败时只落一行（兜底说明），不许有编造的回复"
    assert rows[0].ai_status is AiStatus.FAILED
    assert rows[0].sender_type is SenderType.SYSTEM
    assert LLM_FAILURE_TEXT in rows[0].content
    assert rows[0].evidence_ref == f"customer_message:{rows[0].id}"

    # 界面据这条时间线显示「AI 暂时无法回复，请人工处理」
    timeline = _timeline(client, temp_customer.id)
    assert LLM_FAILURE_TEXT in timeline["items"][0]["content"]
    assert timeline["items"][0]["ai_status"] == "FAILED"


def test_llm_failure_keeps_earlier_ai_replies_untouched(
    client: TestClient, temp_customer: Customer, fake_llm: FakeLLM
) -> None:
    """失败不许影响历史消息：之前那条 REPLIED 的 AI 回复必须原样还在。"""
    ok = client.post(
        f"/api/customers/{temp_customer.id}/ai-reply", json={"content": NORMAL_QUESTION}
    ).json()
    fake_llm.raises = llm_service.LLMUnavailable("模拟网络错误")
    client.post(f"/api/customers/{temp_customer.id}/ai-reply", json={"content": NORMAL_QUESTION})

    timeline = _timeline(client, temp_customer.id)
    kinds = {item["id"]: item["ai_status"] for item in timeline["items"]}
    assert kinds[ok["message"]["id"]] == "REPLIED"
    assert sorted(kinds.values()) == ["FAILED", "REPLIED"]


# ─────────────── 顺序 / 二次把关 / 边界 ───────────────


def test_llm_draft_with_commitment_tone_is_blocked_before_persisting(
    client: TestClient, db: Session, temp_customer: Customer, fake_llm: FakeLLM
) -> None:
    """模型自己写出"已经给你 8 折"这类对外承诺 → 草稿拦下、不落库，改落 HUMAN_REQUIRED。"""
    fake_llm.reply = "没问题，已经给你 8 折了，这周就按这个价执行。"

    body = client.post(
        f"/api/customers/{temp_customer.id}/ai-reply", json={"content": NORMAL_QUESTION}
    ).json()

    assert body["llm_called"] is True  # 问题本身不敏感，确实调了 LLM
    assert body["ai_status"] == "HUMAN_REQUIRED"
    assert body["message"]["sender_type"] == "SYSTEM"

    _fresh(db)
    contents = [
        row.content
        for row in db.execute(
            select(CustomerMessage).where(CustomerMessage.customer_id == temp_customer.id)
        ).scalars()
    ]
    assert len(contents) == 1
    assert "已经给你 8 折" not in contents[0], "★ 草稿里的承诺绝不能落库"


def test_suggestion_tone_question_passes_and_reply_is_persisted(
    client: TestClient, temp_customer: Customer, fake_llm: FakeLLM
) -> None:
    """"建议可考虑 9 折"这类建议语气放行（D7 的「建议」≠「执行」）。"""
    fake_llm.reply = "这个思路可以记录，具体方案我们同事会与您确认。"
    body = client.post(
        f"/api/customers/{temp_customer.id}/ai-reply",
        json={"content": "建议可考虑 9 折，仅供参考"},
    ).json()

    assert body["policy"]["tone"] == "SUGGESTION"
    assert body["policy"]["allowed"] is True
    assert body["llm_called"] is True
    assert body["ai_status"] == "REPLIED"


def test_missing_question_or_customer_is_rejected(
    client: TestClient, temp_customer: Customer, fake_llm: FakeLLM
) -> None:
    """两个字段都不给 → 422；客户不存在 → 404；两种情况都不许调 LLM。"""
    assert client.post(f"/api/customers/{temp_customer.id}/ai-reply", json={}).status_code == 422
    assert (
        client.post(
            "/api/customers/999999999/ai-reply", json={"content": NORMAL_QUESTION}
        ).status_code
        == 404
    )
    assert fake_llm.calls == []


def test_ai_reply_never_writes_customer_sender(
    client: TestClient, db: Session, temp_customer: Customer, fake_llm: FakeLLM
) -> None:
    """跑完正常/敏感/失败三条路径，库里依然一行 CUSTOMER 都没有（D9）。"""
    client.post(f"/api/customers/{temp_customer.id}/ai-reply", json={"content": NORMAL_QUESTION})
    client.post(f"/api/customers/{temp_customer.id}/ai-reply", json={"content": SENSITIVE_QUESTION})
    fake_llm.raises = llm_service.LLMUnavailable("模拟失败")
    client.post(f"/api/customers/{temp_customer.id}/ai-reply", json={"content": NORMAL_QUESTION})

    _fresh(db)
    assert (
        db.execute(
            select(func.count())
            .select_from(CustomerMessage)
            .where(CustomerMessage.sender_type == SenderType.CUSTOMER)
        ).scalar_one()
        == 0
    )

    fresh = SessionLocal()
    try:
        assert (
            fresh.execute(
                select(func.count())
                .select_from(CustomerMessage)
                .where(CustomerMessage.sender_type == SenderType.CUSTOMER)
            ).scalar_one()
            == 0
        )
    finally:
        fresh.close()
