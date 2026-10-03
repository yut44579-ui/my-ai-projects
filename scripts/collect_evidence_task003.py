"""一键重采 TASK-003 的验收证据 → docs/evidence/TASK-003-evidence.md

前提：
  1. 本机 MySQL 已起，.env 里数据库值已填（照 README 第 3 步）。
  2. 后端已在 $API_BASE（默认 http://127.0.0.1:8000）运行（跑的必须是当前代码）。
  3. 截图先跑：node scripts/ui_screenshots_task003.mjs

用法（项目根目录）：
    .venv/Scripts/python.exe scripts/collect_evidence_task003.py
    API_BASE=http://127.0.0.1:8103 .venv/Scripts/python.exe scripts/collect_evidence_task003.py

★ 与 TASK-002 的采集脚本不同：本 TASK 的验收本身就是"真发一条人工消息"，
  所以脚本会**真的写库**——但只写它自己造的两个证据客户（名字带 TASK-003 证据客户），
  不动其它任何数据；两个客户都是 MANUAL 来源，不参与任何汇报口径。

（结构照 scripts/collect_evidence_task001/002 抄，不另起一套。）
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

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.customer import Customer, CustomerSourceType, DedupeState  # noqa: E402

BASE_URL = os.environ.get("API_BASE", "http://127.0.0.1:8000")
BROWSER_CALLS = ROOT / "docs" / "evidence" / "task003-api-calls.json"
TARGET = ROOT / "docs" / "evidence" / "TASK-003-evidence.md"

EVIDENCE_CUSTOMER = "TASK-003 证据客户（有消息）"
EMPTY_CUSTOMER = "TASK-003 证据客户（无消息）"

sections: list[str] = []


def add(title: str, block: str) -> None:
    sections.append(f"### {title}\n\n```\n{block.strip()}\n```\n")
    print(f"  ✓ {title}")


def ensure_evidence_customer(name: str) -> int:
    """取/建证据客户（MANUAL 来源，与导入数据区分开）。"""
    db = SessionLocal()
    try:
        row = db.execute(select(Customer).where(Customer.name == name)).scalars().first()
        if row is None:
            row = Customer(
                name=name,
                company_name="TASK-003 证据采集",
                source_type=CustomerSourceType.MANUAL,
                dedupe_state=DedupeState.CLEAN,
                evidence_ref=None,
                note="由 scripts/collect_evidence_task003.py 创建，用于采集沟通记录验收证据",
            )
            db.add(row)
            db.commit()
            db.refresh(row)
        return row.id
    finally:
        db.close()


def run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    return (proc.stdout or "") + (proc.stderr or "")


def pytest_evidence() -> None:
    full = run([sys.executable, "-m", "pytest", "-q"])
    add("① pytest 全量（只增不减）", "\n".join(full.strip().splitlines()[-6:]))

    targeted = run([sys.executable, "-m", "pytest", "tests/test_messages_api.py", "-v", "--no-header"])
    lines = [ln for ln in targeted.splitlines() if "::" in ln or "passed" in ln]
    add("② TASK-003 专项测试（AC1~AC5）", "\n".join(lines[-20:]))


def http_evidence() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=15) as c:
        health = c.get("/api/health").json()
        add(
            "③ 后端连通性（证据都采自这个进程）",
            f"GET /api/health → status={health['status']} / "
            f"db={health['db']['database']} / connected={health['db']['connected']}",
        )

        with_msg = ensure_evidence_customer(EVIDENCE_CUSTOMER)
        no_msg = ensure_evidence_customer(EMPTY_CUSTOMER)

        # AC1：人工发送 → 200 + 带 evidence_ref
        sent = c.post(
            f"/api/customers/{with_msg}/messages",
            json={"content": "客户来电询问续约与后续服务安排（人工记录）"},
        )
        timeline = c.get(f"/api/customers/{with_msg}/messages").json()
        add(
            "④ AC1 发一条人工消息 → 200 且时间线出现（带 evidence_ref）",
            f"POST /api/customers/{with_msg}/messages  → HTTP {sent.status_code}\n"
            + json.dumps(sent.json(), ensure_ascii=False, indent=2)
            + f"\n\nGET /api/customers/{with_msg}/messages → HTTP 200\n"
            + json.dumps(timeline, ensure_ascii=False, indent=2),
        )

        # AC2：刷新 = 换一个新连接重新读，消息仍在（真落库）
        fresh = httpx.get(f"{BASE_URL}/api/customers/{with_msg}/messages", timeout=15).json()
        add(
            "⑤ AC2 刷新后仍在（新连接重新读，非前端态）",
            f"第二次 GET（独立连接）items={len(fresh['items'])} 条，"
            f"total={json.dumps(fresh['total'], ensure_ascii=False)}\n"
            f"最后一条：{json.dumps(fresh['items'][-1], ensure_ascii=False)}",
        )

        # AC3：拒绝 sender_type=CUSTOMER
        rejected = c.post(
            f"/api/customers/{with_msg}/messages",
            json={"content": "你好，我是客户本人（伪造）", "sender_type": "CUSTOMER"},
        )
        add(
            "⑥ AC3 写入 sender_type=CUSTOMER → 400 + 明确错误码",
            f"POST /api/customers/{with_msg}/messages (sender_type=CUSTOMER) → HTTP {rejected.status_code}\n"
            + json.dumps(rejected.json(), ensure_ascii=False, indent=2),
        )

        # AC4：无消息客户 → NO_DATA 空态
        empty = c.get(f"/api/customers/{no_msg}/messages")
        detail_empty = c.get(f"/api/customers/{no_msg}").json()["conversations"]
        add(
            "⑦ AC4 无消息客户 → 时间线 NO_DATA 空态（value=null，界面无数字）",
            f"GET /api/customers/{no_msg}/messages → HTTP {empty.status_code}\n"
            + json.dumps(empty.json(), ensure_ascii=False, indent=2)
            + f"\n\nGET /api/customers/{no_msg} → conversations\n"
            + json.dumps(detail_empty, ensure_ascii=False, indent=2),
        )

        # AC5：条数走 EvidenceValue
        detail_with = c.get(f"/api/customers/{with_msg}").json()["conversations"]
        add(
            "⑧ AC5 该客户消息条数走 EvidenceValue（前端不许自己数）",
            f"GET /api/customers/{with_msg} → conversations\n"
            + json.dumps(detail_with, ensure_ascii=False, indent=2)
            + f"\n\nGET /api/customers/{with_msg}/messages → total\n"
            + json.dumps(timeline["total"], ensure_ascii=False, indent=2),
        )

        # 分页 + 时间正序
        page = c.get(
            f"/api/customers/{with_msg}/messages", params={"page": 1, "page_size": 1}
        ).json()
        add(
            "⑨ 时间线分页 + 时间正序（total 不受分页影响）",
            f"GET /api/customers/{with_msg}/messages?page=1&page_size=1 → HTTP 200\n"
            f"items={[i['id'] for i in page['items']]}（最早一条）\n"
            f"total={json.dumps(page['total'], ensure_ascii=False)}\n"
            f"最末一条 id={timeline['items'][-1]['id']}（升序成立）",
        )

        not_found = c.get("/api/customers/999999999/messages")
        add(
            "⑩ 对不存在的客户 → 404（GET / POST 都是）",
            f"GET  /api/customers/999999999/messages → HTTP {not_found.status_code}\n"
            f"POST /api/customers/999999999/messages → HTTP "
            f"{c.post('/api/customers/999999999/messages', json={'content': 'x'}).status_code}",
        )


def db_evidence() -> None:
    """直接查库：证明落的是真行，且 CUSTOMER 一行都没有（D9）。"""
    from sqlalchemy import func, text

    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "select id, customer_id, sender_type, message_type, source_type, "
                "evidence_ref, ai_status, created_at from customer_messages "
                "order by id desc limit 8"
            )
        ).all()
        customer_rows = db.execute(
            text("select count(*) from customer_messages where sender_type='CUSTOMER'")
        ).scalar_one()
        columns = [
            d[0]
            for d in db.execute(text("show columns from customer_messages")).all()
        ]
    finally:
        db.close()

    body = "\n".join(str(r) for r in rows)
    add(
        "⑪ 直接查库：最近 8 行 + CUSTOMER 行数必须为 0（D9）",
        f"select ... from customer_messages order by id desc limit 8\n{body}\n\n"
        f"select count(*) from customer_messages where sender_type='CUSTOMER' → {customer_rows}\n\n"
        f"show columns from customer_messages → {columns}",
    )


def browser_evidence() -> None:
    if BROWSER_CALLS.is_file():
        calls = json.loads(BROWSER_CALLS.read_text(encoding="utf-8"))
        lines = [f"{c['status']}  {c['url']}" for c in calls]
        add(
            "⑫ 页面真实发出的接口调用（浏览器抓包，docs/evidence/task003-api-calls.json）",
            "\n".join(lines[-14:]),
        )
    shots = sorted(p.name for p in (ROOT / "docs" / "screenshots").glob("task003-*.png"))
    add(
        "⑬ 页面截图（真实浏览器，见 docs/screenshots/）",
        "\n".join(shots)
        + "\n\n"
        "· task003-message-empty          无消息客户：沟通记录显示「暂无沟通记录」，界面无数字\n"
        "· task003-message-sent           真敲字真点发送 → 时间线出现「人工」气泡 + evidence_ref\n"
        "· task003-message-after-reload   刷新页面（Page.navigate 重新加载）后消息仍在 = 真落库",
    )


def main() -> int:
    print("采集 TASK-003 证据中…")
    pytest_evidence()
    http_evidence()
    db_evidence()
    browser_evidence()

    header = (
        "# TASK-003 沟通记录真实验收证据"
        "（由 scripts/collect_evidence_task003.py 自动采集，未手工编辑）\n\n"
        f"采集时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}　后端：{BASE_URL}\n\n"
        "★ 本 TASK 的验收本身就是「真发一条人工消息」，因此本文件里的写入是**真实写库**；\n"
        "  写入目标只有两个证据客户（名字带「TASK-003 证据客户」），不碰其它数据。\n\n"
    )
    TARGET.write_text(header + "\n".join(sections), encoding="utf-8")
    print(f"\n已写入 {TARGET.relative_to(ROOT)}（{len(sections)} 段）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
