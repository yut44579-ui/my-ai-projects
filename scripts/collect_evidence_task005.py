"""一键重采 TASK-005（人工接管三态）的验收证据 → docs/evidence/TASK-005-evidence.md

前提：
  1. 本机 MySQL 已起，.env 里 5 个数据库值已填（照 README 第 3 步）。
  2. 后端已在本机运行（默认 127.0.0.1:8000，可用环境变量 API_BASE 覆盖）。

用法（项目根目录）：
    .venv/Scripts/python.exe scripts/collect_evidence_task005.py

★ 本脚本会**临时**在库里建一个名为 `接管证据客户_TEST_DO_NOT_KEEP` 的 MANUAL 客户，
  跑完立刻删掉（其接管事件随 FK CASCADE 一起删）。不碰库里其它任何数据。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import inspect, select, text  # noqa: E402

from app.db.session import SessionLocal, engine  # noqa: E402
from app.models.customer import Customer, CustomerSourceType  # noqa: E402
from app.models.handover import CustomerHandoverEvent  # noqa: E402
from app.services.handover import set_human_required  # noqa: E402

BASE_URL = os.environ.get("API_BASE", "http://127.0.0.1:8000")
DEMO_NAME = "接管证据客户_TEST_DO_NOT_KEEP"
OUT = ROOT / "docs/evidence/TASK-005-evidence.md"

sections: list[str] = []


def add(title: str, block: str) -> None:
    sections.append(f"### {title}\n\n```\n{block.strip()}\n```\n")
    print(f"  ✓ {title}")


def jdump(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def main() -> None:
    client = httpx.Client(base_url=BASE_URL, timeout=10)

    # ── 证据①：健康检查（证明连的是本 worktree 的独立库）──────────────────
    health = client.get("/api/health").json()
    add("① 健康检查（独立库）", jdump(health["db"]))

    # ── 证据②：迁移后的表结构 ─────────────────────────────────────────────
    insp = inspect(engine)
    lines = ["customers.handover_state:"]
    for col in insp.get_columns("customers"):
        if col["name"] == "handover_state":
            lines.append(
                f"  {col['name']} {col['type']} nullable={col['nullable']} default={col['default']!r}"
            )
    lines.append("customer_handover_events:")
    for col in insp.get_columns("customer_handover_events"):
        lines.append(f"  {col['name']} {col['type']} nullable={col['nullable']}")
    lines.append("indexes: " + ", ".join(ix["name"] for ix in insp.get_indexes("customer_handover_events")))
    with engine.connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    lines.append(f"alembic head: {rev}")
    add("② 迁移结果（列 / 表 / 索引 / 版本）", "\n".join(lines))

    db = SessionLocal()
    customer = Customer(name=DEMO_NAME, source_type=CustomerSourceType.MANUAL)
    db.add(customer)
    db.commit()
    db.refresh(customer)
    cid = customer.id
    db.close()

    try:
        # ── 证据③：初始态 AUTO 且无历史 → NO_DATA（不是 0）─────────────────
        r = client.get(f"/api/customers/{cid}/handover")
        add(f"③ 初始 handover（客户 {cid}，空态 NO_DATA）", f"HTTP {r.status_code}\n{jdump(r.json())}")

        # ── 证据④：闸门判定敏感 → HUMAN_REQUIRED（直接调服务层，模拟 TASK-004）──
        db = SessionLocal()
        change = set_human_required(
            db, cid, reason="命中敏感：报价承诺", rule="QUOTE_PROMISE", matched="已经给你 8 折"
        )
        detail = client.get(f"/api/customers/{cid}").json()
        db.close()
        add(
            "④ 闸门判定敏感 → HUMAN_REQUIRED（服务层调用 + 详情可见）",
            f"set_human_required: changed={change.changed} state={change.state.value}\n"
            f"事件: {change.event.evidence_ref if change.event else None}\n"
            f"GET /api/customers/{cid} 的 handover_state = {detail['handover_state']}",
        )

        # 列表也可见（AC1）
        listing = client.get("/api/customers", params={"page_size": 100}).json()
        row = next(i for i in listing["items"] if i["id"] == cid)
        add("④b 列表可见（AC1）", f"GET /api/customers item[{cid}].handover_state = {row['handover_state']}")

        # ── 证据⑤：人工接管 → HUMAN_ACTIVE + 事件 actor_type=HUMAN ──────────
        r = client.post(f"/api/customers/{cid}/takeover", json={"note": "我来处理这个报价"})
        add("⑤ 人工接管 → HUMAN_ACTIVE（写事件 actor_type=HUMAN）", f"HTTP {r.status_code}\n{jdump(r.json())}")

        # ── 证据⑥：交回 AI → AUTO + 事件 ────────────────────────────────────
        r = client.post(f"/api/customers/{cid}/resume", json={"note": "处理完了，交回 AI"})
        add("⑥ 交回 AI → AUTO（写事件）", f"HTTP {r.status_code}\n{jdump(r.json())}")

        # ── 证据⑦：完整历史（AC5：当前值 + 历史都要有）─────────────────────
        r = client.get(f"/api/customers/{cid}/handover")
        add(f"⑦ 完整变更历史（AC5，客户 {cid}）", f"HTTP {r.status_code}\n{jdump(r.json())}")

        # ── 证据⑧：硬边界 —— AI 不许改状态（403，且无事件）────────────────
        before = db_snapshot(cid)
        r = client.post(f"/api/customers/{cid}/takeover", json={"actor_type": "AI", "note": "AI 想接管"})
        after = db_snapshot(cid)
        add(
            "⑧ 硬边界：AI 身份改状态 → 403，且不产生事件（D7/D8）",
            f"HTTP {r.status_code}\n{jdump(r.json())}\n"
            f"事件条数 before={before['events']} after={after['events']}（应相同）\n"
            f"handover_state after={after['state']}",
        )

        # ── 证据⑨：客户不存在 → 404 ────────────────────────────────────────
        r = client.post("/api/customers/999999999/takeover")
        add("⑨ 客户不存在 → 404", f"HTTP {r.status_code}\n{jdump(r.json())}")

    finally:
        # ── 清理：删客户（事件 FK CASCADE 一并删）─────────────────────────
        db = SessionLocal()
        db.execute(text("DELETE FROM customer_handover_events WHERE customer_id = :c"), {"c": cid})
        db.execute(text("DELETE FROM customers WHERE id = :c"), {"c": cid})
        db.commit()
        left = db.execute(
            select(Customer.id).where(Customer.name == DEMO_NAME)
        ).scalars().all()
        db.close()
        print(f"  ✓ 清理完成（残留同名客户：{left}）")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        "# TASK-005 验收证据（人工接管三态）\n\n"
        f"来源脚本：`scripts/collect_evidence_task005.py`（API_BASE={BASE_URL}）\n\n"
        + "\n".join(sections),
        encoding="utf-8",
    )
    print(f"\n已写出 {OUT}")


def db_snapshot(customer_id: int) -> dict:
    db = SessionLocal()
    try:
        state = db.get(Customer, customer_id)
        n = len(
            db.execute(
                select(CustomerHandoverEvent.id).where(
                    CustomerHandoverEvent.customer_id == customer_id
                )
            ).scalars().all()
        )
        return {"state": state.handover_state.value if state else None, "events": n}
    finally:
        db.close()


if __name__ == "__main__":
    main()
