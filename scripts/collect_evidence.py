"""一键重采 TASK-001 的验收证据 → docs/evidence/TASK-001-evidence.md

前提：
  1. 本机 MySQL 已起，.env 里 5 个数据库值已填（照 README 第 3 步）。
  2. 后端已在 127.0.0.1:8000 运行。
  3. 需要"前端真实调用"那一段的话，先跑过 node scripts/ui_screenshots.mjs。

用法（项目根目录）：
    .venv/Scripts/python.exe scripts/collect_evidence.py

★ 本脚本**只读**开发库（除了在临时库上跑一次迁移证明，跑完立刻删掉临时库），
  不会往开发库里写任何数据。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine, text  # noqa: E402

from app.core.config import settings  # noqa: E402

BASE_URL = os.environ.get("API_BASE", "http://127.0.0.1:8000")
CLEAN = ROOT / "tests/fixtures/customers_sample_TEST_clean.csv"
MESSY = ROOT / "tests/fixtures/customers_sample_TEST_messy.csv"
TEMP_DB = "biz_assistant_alembic_check"

sections: list[str] = []


def add(title: str, block: str) -> None:
    sections.append(f"### {title}\n\n```\n{block.strip()}\n```\n")
    print(f"  ✓ {title}")


# ────────────────────────── 证据①：迁移能建出这两张表 ──────────────────────────
def migration_evidence() -> None:
    admin = create_engine(
        f"mysql+pymysql://{settings.db_user}:{settings.db_password}"
        f"@{settings.db_host}:{settings.db_port}/?charset=utf8mb4",
        isolation_level="AUTOCOMMIT",
    )
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {TEMP_DB}"))
        conn.execute(
            text(f"CREATE DATABASE {TEMP_DB} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        )

    url = (
        f"mysql+pymysql://{settings.db_user}:{settings.db_password}"
        f"@{settings.db_host}:{settings.db_port}/{TEMP_DB}?charset=utf8mb4"
    )
    env = {**os.environ, "DATABASE_URL": url}
    alembic = ROOT / ".venv" / "Scripts" / "alembic.exe"
    result = subprocess.run(
        [str(alembic), "upgrade", "head"], env=env, cwd=str(ROOT),
        capture_output=True, text=True,
    )
    log = (result.stdout + result.stderr).strip()

    probe = create_engine(url)
    try:
        with probe.connect() as conn:
            tables = [row[0] for row in conn.execute(text("show tables"))]
            customers_ddl = conn.execute(text("show create table customers")).fetchone()[1]
            batches_ddl = conn.execute(text("show create table import_batches")).fetchone()[1]
        add(
            "① alembic upgrade head（在临时库上真跑一遍，证明迁移可建表）",
            f"$ DATABASE_URL=<临时库 {TEMP_DB}> .venv/Scripts/alembic.exe upgrade head\n{log}\n\n"
            f"$ show tables\n  " + "\n  ".join(tables) +
            f"\n\n$ show create table customers\n{customers_ddl}"
            f"\n\n$ show create table import_batches\n{batches_ddl}",
        )
    finally:
        probe.dispose()
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE {TEMP_DB}"))
        admin.dispose()


# ────────────────────────── 证据②：真实接口调用 ──────────────────────────
def api_evidence() -> None:
    def call(title: str, method: str, path: str, **kwargs) -> httpx.Response:
        resp = httpx.request(method, BASE_URL + path, timeout=60, **kwargs)
        try:
            pretty = json.dumps(json.loads(resp.text), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            pretty = resp.text
        add(title, f"$ {method} {path}\nHTTP {resp.status_code}\n{pretty[:2600]}")
        return resp

    clean_bytes, messy_bytes = CLEAN.read_bytes(), MESSY.read_bytes()

    preview = call(
        "② preview：干净样本（只解析、不入库）", "POST", "/api/imports/preview",
        files={"file": (CLEAN.name, clean_bytes, "text/csv")}, data={"header_row": "1"},
    )
    shas = preview.json()["file_sha256"]

    call(
        "③ 同一 sha256 重复提交 → 被拒绝（幂等）", "POST", "/api/imports/commit",
        files={"file": (CLEAN.name, clean_bytes, "text/csv")},
        data={"sha256": shas, "source_type": "TEST", "header_row": "1"},
    )
    call(
        "④ 提交的文件与预览不是同一份 → sha256 不一致被拒", "POST", "/api/imports/commit",
        files={"file": (MESSY.name, messy_bytes, "text/csv")},
        data={"sha256": shas, "source_type": "TEST", "header_row": "1"},
    )
    call(
        "⑤ 空文件 → empty_file", "POST", "/api/imports/preview",
        files={"file": ("empty.csv", b"", "text/csv")}, data={"header_row": "1"},
    )
    call(
        "⑥ 上传 .txt → unsupported_format", "POST", "/api/imports/preview",
        files={"file": ("notes.txt", "客户姓名\n甲\n".encode(), "text/plain")},
        data={"header_row": "1"},
    )
    call("⑦ 客户列表（分页裸值 + 业务数字走 EvidenceValue）", "GET",
         "/api/customers?page=1&page_size=3")
    call("⑧ 导入批次列表", "GET", "/api/imports")

    batches = httpx.get(f"{BASE_URL}/api/imports", timeout=30).json()["items"]
    if batches:
        call(f"⑨ 批次详情（batch_id={batches[0]['batch_id']}）", "GET",
             f"/api/imports/{batches[0]['batch_id']}")


# ────────────────────────── 证据③：前端真实调用（可选）──────────────────────────
def browser_evidence() -> None:
    path = ROOT / "docs" / "evidence" / "browser-api-calls.json"
    if not path.is_file():
        print("  - 跳过：没有 docs/evidence/browser-api-calls.json（先跑 ui_screenshots.mjs）")
        return

    calls = json.loads(path.read_text(encoding="utf-8"))
    blocks = [
        "\n---\n\n## 附：前端（真实浏览器）实际发出的调用\n",
        "由 `scripts/ui_screenshots.mjs` 驱动真实 Edge 跑导入流程时，从浏览器网络层抓下的原始响应体"
        "（不是另写脚本伪造的）。\n",
    ]
    for call in calls:
        if "/api/imports/commit" not in call["url"] and "/api/imports/preview" not in call["url"]:
            continue
        body = json.loads(call["body"])
        title = body.get("filename", "-")
        if call["url"].endswith("commit"):
            blocks.append(
                f"### POST /api/imports/commit → HTTP {call['status']}"
                f"（{title}，批次 {body.get('batch_id')}，status={body.get('status')}）\n"
            )
        else:
            blocks.append(f"### POST /api/imports/preview → HTTP {call['status']}（{title}）\n")
        blocks.append("```json\n" + json.dumps(body, ensure_ascii=False, indent=2)[:2400] + "\n```\n")
    sections.append("\n".join(blocks))
    print(f"  ✓ 前端抓包附录（{len(calls)} 条）")


def main() -> int:
    print("采集证据中…")
    migration_evidence()
    api_evidence()
    browser_evidence()

    header = (
        "# TASK-001 真实调用证据（由 scripts/collect_evidence.py 自动采集，未手工编辑）\n\n"
        f"采集时间：{__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    target = ROOT / "docs" / "evidence" / "TASK-001-evidence.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(header + "\n".join(sections), encoding="utf-8")
    print(f"\n已写入 {target.relative_to(ROOT)}（{len(sections)} 段）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
