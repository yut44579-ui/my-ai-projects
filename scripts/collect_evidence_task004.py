"""一键重采 TASK-004 的验收证据 → docs/evidence/TASK-004-evidence.md

前提（三个进程都跑当前代码）：
  1. 正常后端：   .venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --port 8103
  2. 失败态后端： DEEPSEEK_BASE_URL=<连不上的地址> ... --port 8104
     （用来真实制造 LLM 调用失败，验证 FAILED + 人工兜底文案，而不是靠 mock）
  3. 前端两个：   5183→8103、5184→8104（见 frontend/vite.config.wt345.mjs）
     截图先跑：node scripts/ui_screenshots_task004.mjs
               EXPECT_FAILURE=1 APP_URL=http://127.0.0.1:5184/customers node scripts/ui_screenshots_task004.mjs

用法（项目根目录）：
    API_BASE=http://127.0.0.1:8103 FAIL_API_BASE=http://127.0.0.1:8104 \
      .venv/Scripts/python.exe scripts/collect_evidence_task004.py

★ 本脚本会**真的调用一次 DeepSeek**（AC1 的普通问题）——这是验收要求的一部分；
  敏感问题那条不会调（正是要证明的），失败那条调的是坏地址。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select, text  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.customer import Customer, CustomerSourceType, DedupeState  # noqa: E402

BASE_URL = os.environ.get("API_BASE", "http://127.0.0.1:8000")
FAIL_API_BASE = os.environ.get("FAIL_API_BASE", "http://127.0.0.1:8104")
TARGET = ROOT / "docs" / "evidence" / "TASK-004-evidence.md"

EVIDENCE_CUSTOMER = "TASK-004 证据客户"
NORMAL_QUESTION = "你们的售后支持时间是怎样的？"
SENSITIVE_QUESTION = "老客户有没有折扣？能给我 8 折吗"
FAIL_QUESTION = "请问你们公司的服务网点有哪些？"

sections: list[str] = []


def add(title: str, block: str) -> None:
    sections.append(f"### {title}\n\n```\n{block.strip()}\n```\n")
    print(f"  ✓ {title}")


def ensure_customer(name: str) -> int:
    db = SessionLocal()
    try:
        row = db.execute(select(Customer).where(Customer.name == name)).scalars().first()
        if row is None:
            row = Customer(
                name=name,
                company_name="TASK-004 证据采集",
                source_type=CustomerSourceType.MANUAL,
                dedupe_state=DedupeState.CLEAN,
                evidence_ref=None,
                note="由 scripts/collect_evidence_task004.py 创建，用于采集 AI 回复验收证据",
            )
            db.add(row)
            db.commit()
            db.refresh(row)
        return row.id
    finally:
        db.close()


def run(cmd: list[str], env: dict | None = None) -> str:
    merged = {**os.environ, **(env or {})}
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=merged)
    return (proc.stdout or "") + (proc.stderr or "")


def pytest_evidence() -> None:
    gate = run([sys.executable, "-m", "pytest", "tests/test_policy_gate.py", "-q", "--no-header"])
    lines = [ln for ln in gate.strip().splitlines() if "passed" in ln or "failed" in ln]
    add(
        "① AC4 PolicyGate 独立单测（每类敏感词至少一条用例）",
        "\n".join(lines) + "\n\n"
        f"用例清单（每类敏感词一条，见 tests/test_policy_gate.py:SENSITIVE_CASES）：\n"
        + "\n".join(
            f"  · {cat}  ←  {case}"
            for cat, case in _gate_cases()
        ),
    )

    api = run([sys.executable, "-m", "pytest", "tests/test_ai_reply_api.py", "-v", "--no-header"])
    lines = [ln for ln in api.splitlines() if "::" in ln or "passed" in ln]
    add("② AC1~AC3 接口测试（含 monkeypatch 计数断言：敏感时 LLM 一次都没调）", "\n".join(lines[-20:]))

    full = run([sys.executable, "-m", "pytest", "-q"])
    add("③ pytest 全量（只增不减）", "\n".join(full.strip().splitlines()[-6:]))


def _gate_cases() -> list[tuple[str, str]]:
    """从测试文件里读回"类别 → 用例"清单，保证证据与测试同源。"""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from tests.test_policy_gate import SENSITIVE_CASES  # noqa: PLC0415

    return [(case.value if hasattr(case, "value") else str(case), text) for case, text in SENSITIVE_CASES]


def http_evidence() -> None:
    db_customer = ensure_customer(EVIDENCE_CUSTOMER)
    with httpx.Client(base_url=BASE_URL, timeout=60) as c:
        health = c.get("/api/health").json()
        add(
            "④ 后端连通性（普通/敏感两条都采自这个进程）",
            f"GET /api/health → status={health['status']} / "
            f"db={health['db']['database']} / connected={health['db']['connected']}",
        )

        normal = c.post(f"/api/customers/{db_customer}/ai-reply", json={"content": NORMAL_QUESTION})
        add(
            "⑤ AC1 普通问题 → 真调 LLM → 回复落库 REPLIED",
            f"POST /api/customers/{db_customer}/ai-reply  {{\"content\": \"{NORMAL_QUESTION}\"}}\n"
            f"→ HTTP {normal.status_code}\n"
            + json.dumps(normal.json(), ensure_ascii=False, indent=2),
        )

        sensitive = c.post(
            f"/api/customers/{db_customer}/ai-reply", json={"content": SENSITIVE_QUESTION}
        )
        add(
            "⑥ AC2 敏感问题 → 不调 LLM（llm_called=false）、落库 HUMAN_REQUIRED、无 AI 回复",
            f"POST /api/customers/{db_customer}/ai-reply  {{\"content\": \"{SENSITIVE_QUESTION}\"}}\n"
            f"→ HTTP {sensitive.status_code}\n"
            + json.dumps(sensitive.json(), ensure_ascii=False, indent=2),
        )

        suggestion = c.post(
            f"/api/customers/{db_customer}/ai-reply",
            json={"content": "建议可考虑 9 折，仅供参考"},
        ).json()
        add(
            "⑦ 「建议」≠「执行」：建议语气放行（tone=SUGGESTION，真调 LLM）",
            json.dumps(
                {
                    "policy": suggestion["policy"],
                    "llm_called": suggestion["llm_called"],
                    "ai_status": suggestion["ai_status"],
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

        timeline = c.get(f"/api/customers/{db_customer}/messages").json()
        add(
            "⑧ 该客户时间线（AI 回复 / 转人工说明都真落库，条数走 EvidenceValue）",
            "\n".join(
                f"  id={i['id']} sender={i['sender_type']} ai_status={i['ai_status']} "
                f"evidence_ref={i['evidence_ref']} | {i['content'][:48]}"
                for i in timeline["items"]
            )
            + f"\n\ntotal = {json.dumps(timeline['total'], ensure_ascii=False)}",
        )

    # 失败态后端（DEEPSEEK_BASE_URL 指向连不上的地址）
    try:
        with httpx.Client(base_url=FAIL_API_BASE, timeout=60) as f:
            failed = f.post(f"/api/customers/{db_customer}/ai-reply", json={"content": FAIL_QUESTION})
            add(
                "⑨ AC3 制造 LLM 失败 → 502 + 人工兜底文案 + 落库 FAILED（库里无假回复）",
                f"（失败态后端 {FAIL_API_BASE}，其 DEEPSEEK_BASE_URL 指向连不上的地址）\n"
                f"POST /api/customers/{db_customer}/ai-reply  {{\"content\": \"{FAIL_QUESTION}\"}}\n"
                f"→ HTTP {failed.status_code}\n"
                + json.dumps(
                    {k: v for k, v in failed.json().items() if k != "result"}, ensure_ascii=False, indent=2
                ),
            )
    except httpx.HTTPError as exc:  # pragma: no cover - 只影响证据完整性
        add("⑨ AC3 制造 LLM 失败", f"失败态后端不可用（{FAIL_API_BASE}）：{type(exc).__name__}")


def db_evidence() -> None:
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "select id, customer_id, sender_type, message_type, source_type, evidence_ref, "
                "ai_status, left(content, 40) as content from customer_messages "
                "where ai_status <> 'NONE' order by id desc limit 8"
            )
        ).all()
        customer_rows = db.execute(
            text("select count(*) from customer_messages where sender_type='CUSTOMER'")
        ).scalar_one()
        ai_rows = db.execute(
            text("select count(*) from customer_messages where sender_type='AI' and ai_status='REPLIED'")
        ).scalar_one()
    finally:
        db.close()
    add(
        "⑩ 直接查库：AI 相关消息（ai_status<>NONE）+ CUSTOMER 行数必须为 0（D9）",
        "\n".join(str(r) for r in rows)
        + f"\n\nsender_type='AI' and ai_status='REPLIED' 的行数 → {ai_rows}\n"
        + f"sender_type='CUSTOMER' 的行数 → {customer_rows}",
    )


def browser_evidence() -> None:
    for name, label in (
        ("task004-api-calls.json", "普通问题 + 敏感问题"),
        ("task004-api-calls-failed.json", "LLM 失败"),
    ):
        path = ROOT / "docs" / "evidence" / name
        if not path.is_file():
            continue
        calls = json.loads(path.read_text(encoding="utf-8"))
        add(
            f"⑪ 页面真实发出的接口调用（{label}，docs/evidence/{name}）",
            "\n".join(f"{c['status']}  {c['url']}" for c in calls[-10:]),
        )

    shots = sorted(p.name for p in (ROOT / "docs" / "screenshots").glob("task004-*.png"))
    add(
        "⑫ 页面截图（真实浏览器，见 docs/screenshots/）",
        "\n".join(shots)
        + "\n\n"
        "· task004-ai-reply            普通问题：AI 气泡（AI 已回复）+ evidence_ref\n"
        "· task004-ai-human-required   敏感问题：转人工提示 +「需人工处理」系统气泡（未调用 AI）\n"
        "· task004-ai-failed-fallback  LLM 失败：「AI 暂时无法回复，请人工处理」+ 时间线 FAILED 行",
    )


def main() -> int:
    print("采集 TASK-004 证据中…")
    pytest_evidence()
    http_evidence()
    db_evidence()
    browser_evidence()

    header = (
        "# TASK-004 AI 回复真实验收证据"
        "（由 scripts/collect_evidence_task004.py 自动采集，未手工编辑）\n\n"
        f"采集时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}　"
        f"后端：{BASE_URL}（失败态：{FAIL_API_BASE}）\n\n"
        "★ 顺序铁律：① 代码层 PolicyGate →（命中即转人工，不调 LLM）→ ② 调 LLM → ③ 回复落库。\n"
        "★ 本文件里的 LLM 调用是**真实调用**（AC1）；AC2 那条恰恰证明没有调用；AC3 那条调的是坏地址。\n\n"
    )
    TARGET.write_text(header + "\n".join(sections), encoding="utf-8")
    print(f"\n已写入 {TARGET.relative_to(ROOT)}（{len(sections)} 段）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
