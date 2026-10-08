"""去重规则测试（TASK-001 §二 的四种场景 + AC3a/3b/3c）。

★ 本文件盯的就是那两个坑：
  · SQL 侧 NULL = NULL 得到 NULL（不命中）—— 空值不能参与匹配
  · Python 侧 None == None 是 True —— 一旦用 Python 比就会把空邮箱/空电话的行全表误合并

★ 测试数据统一用命名空间：邮箱一律 @import.test 结尾、电话一律 19999 开头。
  这样断言可以只统计"本测试自己造的数据"，不受开发库里其它数据影响，也能反复运行。
"""

from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.customer import Customer, CustomerSourceType, DedupeState
from app.services.dedupe import (
    CustomerRow,
    ImportContext,
    _new_customer,
    normalize_email,
    normalize_phone,
    resolve_customer,
)
from tests.conftest import commit, upload

CTX = ImportContext(batch_id=None, source_type=CustomerSourceType.TEST)

MARK_EMAIL = "@import.test"
MARK_PHONE = "19999"


def _scoped_count(db: Session) -> int:
    """只数本测试命名空间里的客户。"""
    return db.execute(
        select(func.count())
        .select_from(Customer)
        .where(
            or_(
                Customer.email.like(f"%{MARK_EMAIL}"),
                Customer.phone.like(f"{MARK_PHONE}%"),
            )
        )
    ).scalar_one()


def _row(name: str, email: str | None = None, phone: str | None = None) -> CustomerRow:
    return CustomerRow(name=name, email=email, phone=phone, source_row=2)


# ─────────────── 规范化 ───────────────


def test_normalize_never_returns_empty_string() -> None:
    """NULL / 空串 / 全空白一律 None —— 空值只以 NULL 存在，绝不留空串参与比较。"""
    for blank in (None, "", "   ", "\t", "　"):
        assert normalize_email(blank) is None
        assert normalize_phone(blank) is None


def test_normalize_email_lowercases_and_strips() -> None:
    assert normalize_email("  Foo@Bar.COM ") == "foo@bar.com"


def test_normalize_phone_keeps_digits_only() -> None:
    assert normalize_phone("+86 138-0013-8000") == "8613800138000"
    assert normalize_phone("(010) 8888-6666") == "01088886666"
    assert normalize_phone("abc-def") is None


# ─────────────── §二 要求的四个场景（直接调 resolve_customer）───────────────


def test_scenario_both_blank(db: Session, clean_imports: None) -> None:
    """场景 1：email 空 + phone 空 → 两行各自新建，绝不互相合并（AC3c 的单元版）。"""
    first = resolve_customer(_row("甲"), db, CTX)
    second = resolve_customer(_row("乙"), db, CTX)
    db.flush()

    assert first.action == "created"
    assert second.action == "created"
    assert first.customer is not None and second.customer is not None
    assert first.customer.id != second.customer.id
    # 没有可比键 → 判不了重，交人工
    assert first.dedupe_state is DedupeState.PENDING_REVIEW
    assert second.dedupe_state is DedupeState.PENDING_REVIEW


def test_scenario_duplicate_email_blank_phone(db: Session, clean_imports: None) -> None:
    """场景 2：email 重复 + phone 空 → 第二行命中第一行，只留 1 个客户。"""
    first = resolve_customer(_row("甲", email=f"A{MARK_EMAIL}"), db, CTX)
    second = resolve_customer(_row("甲又来了", email=f"a{MARK_EMAIL}"), db, CTX)
    db.flush()

    assert first.action == "created"
    assert second.action == "deduplicated"
    assert first.customer is not None and second.customer is not None
    assert first.customer.id == second.customer.id
    assert _scoped_count(db) == 1


def test_scenario_blank_email_duplicate_phone(db: Session, clean_imports: None) -> None:
    """场景 3：email 空 + phone 重复 → 第二行命中第一行，只留 1 个客户。"""
    first = resolve_customer(_row("甲", phone="19999-0001"), db, CTX)
    second = resolve_customer(_row("甲又来了", phone="199990001"), db, CTX)
    db.flush()

    assert first.action == "created"
    assert second.action == "deduplicated"
    assert first.customer is not None and second.customer is not None
    assert first.customer.id == second.customer.id
    assert _scoped_count(db) == 1


def test_scenario_email_and_phone_hit_two_different_customers(db: Session, clean_imports: None) -> None:
    """场景 4：email 命中 A、phone 命中 B（两个不同客户）→ 冲突：新建 + PENDING_REVIEW，不合并。"""
    a = _new_customer(
        _row("甲", email=f"same{MARK_EMAIL}", phone="1999900001"), DedupeState.CLEAN, CTX
    )
    b = _new_customer(
        _row("乙", email=f"same{MARK_EMAIL}", phone="1999900002"), DedupeState.CLEAN, CTX
    )
    db.add_all([a, b])
    db.flush()

    result = resolve_customer(
        _row("丙", email=f"same{MARK_EMAIL}", phone="1999900002"), db, CTX
    )
    db.flush()

    assert result.action == "created", "冲突时绝不能合并，必须是新建"
    assert result.dedupe_state is DedupeState.PENDING_REVIEW
    assert result.customer is not None
    assert result.customer.id not in {a.id, b.id}
    assert sorted(result.conflict_ids) == sorted([a.id, b.id])
    assert _scoped_count(db) == 3


def test_blank_and_null_never_match_each_other(db: Session, clean_imports: None) -> None:
    """★ 反向验证 Python 侧 None == None 的坑：库里全是空邮箱/空电话时也不能互相命中。"""
    for index in range(3):
        db.add(_new_customer(_row(f"空键{index}"), DedupeState.PENDING_REVIEW, CTX))
    db.flush()

    result = resolve_customer(_row("新的空键", email="   ", phone=""), db, CTX)
    db.flush()

    assert result.action == "created"
    assert result.conflict_ids == [], "空值绝不能匹配到任何既有客户"
    assert result.dedupe_state is DedupeState.PENDING_REVIEW


# ─────────────── AC3a / AC3b / AC3c（走真实接口的端到端）───────────────


def _csv(*rows: str, header: str = "客户姓名,联系电话,邮箱") -> bytes:
    return ("\n".join([header, *rows]) + "\n").encode("utf-8")


def test_ac3a_same_email_and_phone_dedups_to_one_customer(
    client: TestClient, db: Session, clean_imports: None
) -> None:
    """AC3a：两行 email+phone 完全相同 → 只产生 1 个 customer，CLEAN，且 last_seen_at 被刷新。"""
    raw = _csv(
        f"甲,1999900011,dup{MARK_EMAIL}",
        f"甲（重复行）,1999900011,dup{MARK_EMAIL}",
    )
    preview = upload(client, "ac3a.csv", raw)
    assert preview.status_code == 200, preview.text
    sha = preview.json()["file_sha256"]

    body = commit(client, "ac3a.csv", raw, sha).json()
    assert body["rows_total"]["value"] == 2
    assert body["rows_created"]["value"] == 1
    assert body["rows_deduplicated"]["value"] == 1
    assert body["rows_skipped"]["value"] == 0

    customer = db.execute(
        select(Customer).where(Customer.email == f"dup{MARK_EMAIL}")
    ).scalars().one()
    assert customer.dedupe_state is DedupeState.CLEAN
    assert _scoped_count(db) == 1
    first_seen, first_last = customer.first_seen_at, customer.last_seen_at

    # ★ 换一份文件再次命中同一客户：first_seen_at 不动，last_seen_at 必须被刷新
    time.sleep(0.01)
    raw2 = _csv(f"甲,1999900011,dup{MARK_EMAIL}", header="姓名,电话,邮箱")
    sha2 = upload(client, "ac3a_again.csv", raw2).json()["file_sha256"]
    assert sha2 != sha, "内容不同就该是不同 sha"
    body2 = commit(client, "ac3a_again.csv", raw2, sha2).json()

    assert body2["rows_created"]["value"] == 0
    assert body2["rows_deduplicated"]["value"] == 1

    # 结束读事务，避免 MySQL REPEATABLE READ 拿到旧快照，看不到上面那次提交
    db.rollback()
    refreshed = db.get(Customer, customer.id)
    assert refreshed is not None
    assert refreshed.last_seen_at > first_last, "命中既有客户必须刷新 last_seen_at"
    assert refreshed.first_seen_at == first_seen, "first_seen_at 不许被改"
    assert _scoped_count(db) == 1


def test_ac3b_conflict_creates_pending_review_without_merging(
    client: TestClient, db: Session, clean_imports: None
) -> None:
    """AC3b：email 相同但 phone 不同、库里已有第三行 email 相同 → 新建 + PENDING_REVIEW，不自动合并。"""
    # 库里先有两个共用同一 email 的客户（phone 不同）—— 这就是"第三行 email 相同"的现场
    seed_a = _new_customer(
        _row("既有甲", email=f"conflict{MARK_EMAIL}", phone="1999900021"), DedupeState.CLEAN, CTX
    )
    seed_b = _new_customer(
        _row("既有乙", email=f"conflict{MARK_EMAIL}", phone="1999900022"), DedupeState.CLEAN, CTX
    )
    db.add_all([seed_a, seed_b])
    db.commit()  # ★ 必须真提交：接口走的是另一个连接，未提交的种子数据它看不见

    raw = _csv(f"冲突行,1999900022,conflict{MARK_EMAIL}")
    sha = upload(client, "ac3b.csv", raw).json()["file_sha256"]
    body = commit(client, "ac3b.csv", raw, sha).json()

    assert body["rows_created"]["value"] == 1, "冲突必须新建，不许合并进既有客户"
    assert body["rows_deduplicated"]["value"] == 0

    new_customer = db.execute(select(Customer).where(Customer.name == "冲突行")).scalars().one()
    assert new_customer.dedupe_state is DedupeState.PENDING_REVIEW
    # 冲突的既有客户 id 被记下来供人工看
    conflicts = body["conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["kind"] == "multiple_matches"
    assert sorted(conflicts[0]["conflict_with"]) == sorted([seed_a.id, seed_b.id])
    assert _scoped_count(db) == 3


def test_ac3c_two_rows_without_any_key_stay_separate(
    client: TestClient, db: Session, clean_imports: None
) -> None:
    """AC3c：两行 email 和 phone 都为空 → 产生 2 个 customer，各自 PENDING_REVIEW，绝不合并。"""
    raw = _csv("无联系方式甲,,", "无联系方式乙,,")
    sha = upload(client, "ac3c.csv", raw).json()["file_sha256"]
    body = commit(client, "ac3c.csv", raw, sha).json()

    assert body["rows_created"]["value"] == 2
    assert body["rows_deduplicated"]["value"] == 0
    assert body["rows_skipped"]["value"] == 0

    created = db.execute(
        select(Customer).where(Customer.name.like("无联系方式%")).order_by(Customer.id)
    ).scalars().all()
    assert len(created) == 2
    assert all(c.dedupe_state is DedupeState.PENDING_REVIEW for c in created)
    assert len({c.id for c in created}) == 2
    # 无键行会被记成 no_match_key，供人工看；★ 记录里必须带得动新客户 id（不能是 null）
    assert {"no_match_key"} == {item["kind"] for item in body["conflicts"]}
    assert len(body["conflicts"]) == 2
    assert all(item["customer_id"] is not None for item in body["conflicts"])
    assert all(item["conflict_with"] == [] for item in body["conflicts"])


def test_mapping_json_string_is_accepted(
    client: TestClient, db: Session, clean_imports: None
) -> None:
    """映射表以 JSON 字符串传入（前端弹窗确认后的形态）也能正确落库。"""
    raw = _csv(f"甲,1999900031,mapping{MARK_EMAIL}")
    sha = upload(client, "mapping.csv", raw).json()["file_sha256"]
    mapping = json.dumps(
        [
            {"column": "客户姓名", "target": "name"},
            {"column": "联系电话", "target": "phone"},
            {"column": "邮箱", "target": "email"},
        ]
    )
    body = commit(client, "mapping.csv", raw, sha, mapping=mapping).json()
    assert body["rows_created"]["value"] == 1
    assert body["skipped_columns"] == []
