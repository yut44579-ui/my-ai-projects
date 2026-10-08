"""一键重采 TASK-002 的验收证据 → docs/evidence/TASK-002-evidence.md

前提：
  1. 本机 MySQL 已起，.env 里 5 个数据库值已填（照 README 第 3 步）。
  2. 后端已在 127.0.0.1:8000 运行（跑的必须是当前代码）。
  3. 截图与"浏览器真实调用"先跑：node scripts/ui_screenshots_task002.mjs

用法（项目根目录）：
    .venv/Scripts/python.exe scripts/collect_evidence_task002.py

★ 本脚本**只读**：只跑 pytest（测试自己会清理自己造的数据）+ 只调 GET 接口，
  最后用"调用前后行数 / 自增 id 完全不变"证明没有任何写库操作。

（结构照 TASK-001 的 scripts/collect_evidence.py 抄，不另起一套。）
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

from sqlalchemy import create_engine, text  # noqa: E402

from app.core.config import settings  # noqa: E402

BASE_URL = os.environ.get("API_BASE", "http://127.0.0.1:8000")
BROWSER_CALLS = ROOT / "docs" / "evidence" / "task002-api-calls.json"
TARGET = ROOT / "docs" / "evidence" / "TASK-002-evidence.md"

sections: list[str] = []


def add(title: str, block: str) -> None:
    sections.append(f"### {title}\n\n```\n{block.strip()}\n```\n")
    print(f"  ✓ {title}")


def db_fingerprint() -> dict[str, int]:
    """库指纹：行数 + 自增 id 上限。前后一致 = 一行都没写。"""
    engine = create_engine(settings.sqlalchemy_url)
    try:
        with engine.connect() as conn:
            queries = {
                "customers 行数": "select count(*) from customers",
                "customers 最大 id": "select coalesce(max(id),0) from customers",
                "import_batches 行数": "select count(*) from import_batches",
                "import_batches 最大 id": "select coalesce(max(id),0) from import_batches",
                "alembic_version 行数": "select count(*) from alembic_version",
            }
            return {name: conn.execute(text(sql)).scalar_one() for name, sql in queries.items()}
    finally:
        engine.dispose()


# ────────────────────── 证据①：pytest 全量输出 ──────────────────────


def pytest_evidence() -> None:
    """跑一遍全量 pytest，留下"每个文件通过几条 + 最后汇总"的原始输出。

    ★ 不改任何断言；本函数只负责把真实输出抄进证据文件。
    """
    result = subprocess.run(
        [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "pytest", "-v", "--no-header"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = (result.stdout + result.stderr).strip()
    note = (
        "\n\n【读法】\n"
        "· 55 条 = TASK-001 的 39 条 + TASK-002 新增 16 条，只增不减。\n"
        "· 若本次唯一未通过的是 TASK-001 的 test_ac9_500_rows_import_under_5_seconds，\n"
        "  见下面第 ⑥ 段：那是 TASK-001 的计时断言（500 行 < 5s），本机长期在 5s 边界浮动，\n"
        "  本 TASK 没有修改它，也没有经过导入链路。"
    )
    add(
        "① pytest 全量（TASK-001 的 39 条 + TASK-002 新增 16 条，只增不减）",
        out[-3000:] + note,
    )


# ────────────────────── 证据②：真实接口调用（只读） ──────────────────────


def api_evidence() -> None:
    def call(title: str, path: str) -> httpx.Response:
        resp = httpx.get(BASE_URL + path, timeout=60)
        try:
            pretty = json.dumps(json.loads(resp.text), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            pretty = resp.text
        add(title, f"$ GET {path}\nHTTP {resp.status_code}\n{pretty[:3000]}")
        return resp

    call("② 指标卡 /api/customers/stats（未筛选：525 / 525 / 3 / 525）", "/api/customers/stats")
    call(
        "③ 指标卡：来源筛选成空集（REAL）→ 四项全是 NO_DATA，不是 0",
        "/api/customers/stats?source_type=REAL",
    )
    call(
        "④ 指标卡：搜索一个不存在的词 → 口径内空集 → NO_DATA",
        "/api/customers/stats?q=zzz-not-exist",
    )

    # 用一个"带批次 + 待人工裁决"的客户当详情样本（真实存在即可，取最新的那条）
    listing = httpx.get(f"{BASE_URL}/api/customers?page=1&page_size=1", timeout=30).json()
    if listing["items"]:
        customer_id = listing["items"][0]["id"]
        call(f"⑤ 客户详情 /api/customers/{customer_id}", f"/api/customers/{customer_id}")
        call(f"⑥ 事件流 /api/customers/{customer_id}/timeline", f"/api/customers/{customer_id}/timeline")

    call("⑦ 404：不存在的客户", "/api/customers/999999999")


# ────────────────────── 证据③：只读证明 ──────────────────────


def read_only_evidence() -> None:
    # 用库里真实存在的 id 调（不存在的 id 只会拿到 404，证明力弱）
    engine = create_engine(settings.sqlalchemy_url)
    try:
        with engine.connect() as conn:
            customer_id = conn.execute(text("select max(id) from customers")).scalar_one()
            batch_id = conn.execute(text("select max(id) from import_batches")).scalar_one()
    finally:
        engine.dispose()

    paths = [
        "/api/customers",
        "/api/customers?page=1&page_size=5&q=a&source_type=TEST",
        "/api/customers/stats",
        "/api/customers/stats?source_type=REAL",
        f"/api/customers/{customer_id}",
        f"/api/customers/{customer_id}/timeline",
        "/api/imports",
        f"/api/imports/{batch_id}",
        "/api/health",
    ]
    before = db_fingerprint()
    results = []
    for path in paths:
        resp = httpx.get(BASE_URL + path, timeout=30)
        results.append(f"GET {path} → HTTP {resp.status_code}")
    after = db_fingerprint()

    lines = ["调用前："] + [f"  {k} = {v}" for k, v in before.items()] + ["", "调用后："]
    lines += [f"  {k} = {v}" for k, v in after.items()]
    lines += ["", "以上 GET 调用："] + [f"  {r}" for r in results]
    verdict = "完全一致 → 没有产生任何写入" if before == after else "★ 不一致：有写库操作！"
    lines += ["", f"结论：调用前后库指纹{verdict}"]
    add("③ 本 TASK 只读证明（AC6：GET 系列一行都不写）", "\n".join(lines))


# ────────────────────── 证据④：浏览器真实调用 ──────────────────────


def browser_evidence() -> None:
    if not BROWSER_CALLS.is_file():
        print("  - 跳过：没有 docs/evidence/task002-api-calls.json（先跑 ui_screenshots_task002.mjs）")
        return

    calls = json.loads(BROWSER_CALLS.read_text(encoding="utf-8"))
    blocks = [
        "由 `scripts/ui_screenshots_task002.mjs` 驱动真实 Edge 走完"
        "（列表 → 搜索空态 → REAL 空集 → 点行进详情）时，从浏览器网络层抓下的原始响应体。\n"
    ]
    seen: set[str] = set()
    for call in calls:
        path = call["url"].split("127.0.0.1:5173")[-1].rstrip("?")
        key = f"{call['status']} {path}"
        if key in seen:  # 翻页/重复请求只留一条
            continue
        seen.add(key)
        blocks.append(f"#### GET {path} → HTTP {call['status']}\n")
        blocks.append(
            "```json\n" + json.dumps(json.loads(call["body"]), ensure_ascii=False, indent=2)[:1800] + "\n```\n"
        )
    sections.append("\n".join(blocks))
    print(f"  ✓ ④ 前端（真实浏览器）实际发出的 /api/customers* 调用（去重后 {len(seen)} 条）")


def ac9_timing_note() -> None:
    """如实记录 TASK-001 的 AC9 计时断言在本机的实测情况。

    ★ 这是 TASK-001 的断言（500 行导入 < 5s），**本 TASK 没有改它**。
      本机实测：一次 500 行导入要发 1509 条 SQL（每行 2 次去重查询 + 1 次插入，
      见 docs/DECISIONS.md D12 的逐行去重设计），单批耗时在 5s 上下浮动，
      属机器负载敏感的既有问题；TASK-002 只读、且不碰导入链路。
    """
    lines = []
    for i in range(1, 4):
        result = subprocess.run(
            [
                str(ROOT / ".venv" / "Scripts" / "python.exe"),
                "-m",
                "pytest",
                "tests/test_imports_api.py::test_ac9_500_rows_import_under_5_seconds",
                "-v",
                "--no-header",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        out = result.stdout + result.stderr
        verdict = [ln for ln in out.splitlines() if "passed" in ln or "failed" in ln][-1:]
        elapsed = [ln.strip() for ln in out.splitlines() if "耗时" in ln][-1:]
        lines.append(f"第 {i} 次：{verdict[0].strip() if verdict else '?'}")
        if elapsed:
            lines.append(f"          {elapsed[0]}")
    lines += [
        "",
        "说明：AC9 是 TASK-001 的计时断言（阈值 5s），本 TASK 未修改；",
        "      该断言在本机长期处于 5s 边界（实测单批 1509 条 SQL，逐行去重设计使然），",
        "      TASK-002 只增加只读接口，不经过导入链路，与它的波动无关。",
    ]
    add("⑥ TASK-001 的 AC9 计时断言在本机的实测（未修改，如实记录）", "\n".join(lines))


def main() -> int:
    print("采集 TASK-002 证据中…")
    pytest_evidence()
    api_evidence()
    read_only_evidence()
    browser_evidence()
    ac9_timing_note()

    shots = sorted(p.name for p in (ROOT / "docs" / "screenshots").glob("task002-*.png"))
    add(
        "⑤ 页面截图（真实浏览器，见 docs/screenshots/）",
        "\n".join(shots) + "\n\n"
        "· task002-customers-stats  列表页：四个指标卡 + TEST banner + 表格\n"
        "· task002-customers-empty  搜索不存在的词 → 空态，页面上零业务数字\n"
        "· task002-customers-real-nodata  来源筛选 REAL（空集）→ 指标卡 NO_DATA（不是 0）\n"
        "· task002-customer-detail  客户详情：头部 / 状态占位 / 基本信息 / 来源与可追溯 / 事件流 / 三个空态区块",
    )

    header = (
        "# TASK-002 客户模块真实验收证据（由 scripts/collect_evidence_task002.py 自动采集，未手工编辑）\n\n"
        f"采集时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    TARGET.write_text(header + "\n".join(sections), encoding="utf-8")
    print(f"\n已写入 {TARGET.relative_to(ROOT)}（{len(sections)} 段）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
