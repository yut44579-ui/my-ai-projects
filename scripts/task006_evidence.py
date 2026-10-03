"""TASK-006 证据采集：对真实运行的 API 发真实 HTTP 请求，打印原始返回。

跑法（服务需先起在 8016）：
    .venv/Scripts/python.exe scripts/task006_evidence.py

★ 这里用 httpx 而不是 curl，只是因为本机权限策略禁掉了 curl；
  发出的就是真实 HTTP 请求，每条都会同时打印等价的 curl 命令。
★ 客户数据不是编的：先用 /api/imports/preview + commit 把 tests/fixtures 里的
  TEST 样本文件真实导入，再在这批真实客户上做状态流转。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8016/api"
ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "customers_sample_TEST_clean.csv"

sys.path.insert(0, str(ROOT / "backend"))
from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.customer import Customer  # noqa: E402


def show(title: str, curl: str, resp: httpx.Response) -> None:
    print("\n" + "=" * 78)
    print(f"# {title}")
    print(f"$ {curl}")
    print(f"HTTP {resp.status_code}")
    try:
        print(json.dumps(resp.json(), ensure_ascii=False, indent=2))
    except Exception:
        print(resp.text)


def main() -> None:
    client = httpx.Client(base_url=BASE, timeout=30)

    # ---- 0. 先造真实客户：走导入链路，不手写假数据 ----
    raw = FIXTURE.read_bytes()
    prev = client.post(
        "/imports/preview", files={"file": (FIXTURE.name, raw, "text/csv")}
    )
    assert prev.status_code == 200, prev.text
    sha = prev.json()["file_sha256"]
    com = client.post(
        "/imports/commit",
        files={"file": (FIXTURE.name, raw, "text/csv")},
        data={"sha256": sha, "source_type": "TEST"},
    )
    assert com.status_code == 200, com.text
    print("导入样本文件：", json.dumps(
        {k: com.json().get(k) for k in ("batch_id", "status", "rows_created", "rows_deduplicated")},
        ensure_ascii=False))

    with SessionLocal() as db:
        ids = list(
            db.execute(
                select(Customer.id).order_by(Customer.id.desc()).limit(3)
            ).scalars()
        )
    cid, other_id = ids[0], ids[1]
    print(f"本轮测试客户：customer_id={cid}（另一个无事件客户 id={other_id}）")

    # ---- 1. 成功改状态（HUMAN） ----
    r = client.post(
        f"/customers/{cid}/status",
        json={"to_status": "CONTACTED", "actor_type": "HUMAN", "note": "首次电话触达"},
    )
    show(
        "① 人工改状态 NEW -> CONTACTED（成功）",
        f'curl -s -X POST {BASE}/customers/{cid}/status '
        f'-H "Content-Type: application/json" '
        f'-d \'{{"to_status":"CONTACTED","actor_type":"HUMAN","note":"首次电话触达"}}\'',
        r,
    )

    # ---- 2. 连续再改两次，凑够三条历史（AC2） ----
    for st in ("REPLIED", "ENGAGED"):
        r = client.post(f"/customers/{cid}/status", json={"to_status": st, "actor_type": "HUMAN"})
        print(f"\n（连续变更追加）HTTP {r.status_code} -> {st}")

    # ---- 3. AI 改状态必须 403（AC3） ----
    r = client.post(f"/customers/{cid}/status", json={"to_status": "WON", "actor_type": "AI"})
    show(
        "② AI 改状态（必须 403，且不留事件）",
        f'curl -s -o /dev/null -w "%{{http_code}}\\n" -X POST {BASE}/customers/{cid}/status '
        f'-H "Content-Type: application/json" -d \'{{"to_status":"WON","actor_type":"AI"}}\'',
        r,
    )

    # ---- 4. 非法状态值必须 400（AC4） ----
    r = client.post(
        f"/customers/{cid}/status",
        json={"to_status": "PENDING_WHATEVER", "actor_type": "HUMAN"},
    )
    show(
        "③ 非法状态值（必须 400）",
        f'curl -s -o /dev/null -w "%{{http_code}}\\n" -X POST {BASE}/customers/{cid}/status '
        f'-H "Content-Type: application/json" '
        f'-d \'{{"to_status":"PENDING_WHATEVER","actor_type":"HUMAN"}}\'',
        r,
    )

    # ---- 5. 完整事件流（AC2） ----
    r = client.get(f"/customers/{cid}/events")
    show(
        "④ 事件流（三条历史齐全，时间倒序）",
        f"curl -s {BASE}/customers/{cid}/events",
        r,
    )

    # ---- 6. 无事件客户 -> NO_DATA（AC5） ----
    r = client.get(f"/customers/{other_id}/events")
    show(
        "⑤ 无事件客户的事件流（必须 NO_DATA 空态）",
        f"curl -s {BASE}/customers/{other_id}/events",
        r,
    )

    # ---- 7. 详情带 lifecycle_status ----
    r = client.get(f"/customers/{cid}")
    show(
        "⑥ 客户详情带 lifecycle_status",
        f"curl -s {BASE}/customers/{cid}",
        r,
    )

    client.close()


if __name__ == "__main__":
    main()
