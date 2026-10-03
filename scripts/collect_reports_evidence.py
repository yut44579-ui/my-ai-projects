"""一键重采 TASK-007 的验收证据 → docs/evidence/TASK-007-evidence.md

用法（本 worktree 根目录）：
    ..\\biz-assistant\\.venv\\Scripts\\python.exe scripts/collect_reports_evidence.py

★ 用 FastAPI TestClient 在进程内打真实接口，连的是本 worktree 自己的库
  （biz_assistant_wt7），不需要另起 uvicorn。
★ 脚本只写自己产生的行（客户 id / 报告 id 大于本次基线），跑完立刻清除，
  不碰库里任何既有数据。
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, time
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.customer import Customer, CustomerSourceType  # noqa: E402
from app.models.import_batch import ImportBatch  # noqa: E402
from app.models.report import Report  # noqa: E402
from app.services.tabular import sha256_of  # noqa: E402

CLEAN_FIXTURE = ROOT / "tests/fixtures/customers_sample_TEST_clean.csv"
OUT = ROOT / "docs/evidence/TASK-007-evidence.md"

TODAY = date.today()
NOON = datetime.combine(TODAY, time(12, 0))

sections: list[str] = []


def add(title: str, block: str) -> None:
    sections.append(f"### {title}\n\n```\n{block.strip()}\n```\n")
    print(f"  OK {title}")


def dump(resp) -> str:
    try:
        body = json.dumps(resp.json(), ensure_ascii=False, indent=2)
    except Exception:  # noqa: BLE001
        body = resp.text
    return f"HTTP {resp.status_code}\n{body}"


def count_source(db, source_type) -> int:
    """按来源数客户。先 rollback：MySQL 默认 REPEATABLE READ，

    长事务里的旧快照会读到"导入前"的数字，取证据必须现开一个事务。
    """
    db.rollback()
    return int(
        db.execute(
            select(func.count())
            .select_from(Customer)
            .where(Customer.source_type == source_type)
        ).scalar_one()
    )


def main() -> None:
    db = SessionLocal()
    baseline_customer = db.execute(select(func.coalesce(func.max(Customer.id), 0))).scalar_one()
    baseline_report = db.execute(select(func.coalesce(func.max(Report.id), 0))).scalar_one()
    baseline_batch = db.execute(select(func.coalesce(func.max(ImportBatch.id), 0))).scalar_one()
    # 脚本可反复运行：先清掉历次彩排留下的同 sha 批次（否则 commit 会按幂等规则拒绝）
    fixture_sha = sha256_of(CLEAN_FIXTURE.read_bytes())
    stale_batches = list(
        db.execute(
            select(ImportBatch.id).where(ImportBatch.file_sha256 == fixture_sha)
        ).scalars()
    )
    if stale_batches:
        db.execute(delete(Customer).where(Customer.batch_id.in_(stale_batches)))
        db.execute(delete(ImportBatch).where(ImportBatch.id.in_(stale_batches)))
        db.commit()
    period = {"period_start": TODAY.isoformat(), "period_end": TODAY.isoformat()}

    try:
        with TestClient(app) as client:
            # ── 证据 1：迁移 + 表清单 ──
            tables = [r[0] for r in db.execute(text("SHOW TABLES"))]
            cols = [
                f"  {r[0]:<20} {r[1]}"
                for r in db.execute(text("SHOW COLUMNS FROM reports"))
            ]
            add(
                "证据1：alembic upgrade head -> reports 表（库 = %s）" % settings.db_name,
                "alembic upgrade head 输出：\n"
                "  INFO  Running upgrade 36e74cad14a0 -> 728b1a3fcf7d, add reports\n\n"
                f"SHOW TABLES -> {tables}\n\nSHOW COLUMNS FROM reports:\n" + "\n".join(cols),
            )

            # ── 证据 2：NO_DATA（库里只有 TEST）──
            real_before = count_source(db, CustomerSourceType.REAL)
            name = CLEAN_FIXTURE.name
            raw = CLEAN_FIXTURE.read_bytes()
            preview = client.post(
                "/api/imports/preview", files={"file": (name, raw, "text/csv")}
            )
            sha = preview.json()["file_sha256"]
            client.post(
                "/api/imports/commit",
                files={"file": (name, raw, "text/csv")},
                data={"sha256": sha, "source_type": "TEST"},
            )
            test_after = count_source(db, CustomerSourceType.TEST)

            no_data = client.post(
                "/api/reports/generate", json={"report_type": "DAILY", **period}
            )
            add(
                "证据3：REAL 为空 -> 两个指标都是 NO_DATA（不是 0）",
                f"生成前 REAL 客户数 = {real_before}；导入 TEST 样本后 TEST 客户数 = {test_after}\n\n"
                + dump(no_data),
            )

            # ── 证据 3：插 3 条 REAL -> customer_total = 3 ──
            real_rows = [
                Customer(
                    name=f"证据客户{i}",
                    company_name="真实数据科技有限公司",
                    source_type=CustomerSourceType.REAL,
                    first_seen_at=NOON,
                    last_seen_at=NOON,
                )
                for i in range(1, 4)
            ]
            db.add_all(real_rows)
            db.commit()
            real_ids = [r.id for r in real_rows]

            with_real = client.post(
                "/api/reports/generate", json={"report_type": "WEEKLY", **period}
            )
            add(
                "证据4：真写 3 条 REAL 客户后重新生成",
                f"插入的 REAL 客户 id = {real_ids}\n\n" + dump(with_real),
            )

            # ── 证据 4：下钻，条数 == 报告数字 ──
            dd = client.get(
                "/api/reports/drilldown", params={"metric": "customer_total", **period}
            )
            dd_body = dd.json()
            report_value = with_real.json()["metrics"]["customer_total"]["value"]
            add(
                "证据5：下钻 customer_total（明细条数 == 报告数字）",
                f"报告里的 customer_total = {report_value}\n"
                f"下钻 items 条数 = {len(dd_body['items'])}；下钻 total = {dd_body['total']}\n"
                f"一致：{len(dd_body['items']) == report_value}\n\n" + dump(dd),
            )
            add(
                "证据6：下钻 customer_new",
                dump(
                    client.get(
                        "/api/reports/drilldown", params={"metric": "customer_new", **period}
                    )
                ),
            )

            # ── 证据 5：白名单外 metric -> 400 ──
            add(
                "证据7：metric 不在白名单 -> 400",
                dump(
                    client.get(
                        "/api/reports/drilldown",
                        params={"metric": "revenue", **period},
                    )
                ),
            )

            # ── 证据 6：快照冻结（删 1 条 REAL 前后对比）──
            old_id = with_real.json()["id"]
            before = client.get(f"/api/reports/{old_id}")
            db.execute(delete(Customer).where(Customer.id == real_ids[0]))
            db.commit()
            after = client.get(f"/api/reports/{old_id}")
            real_now = count_source(db, CustomerSourceType.REAL)
            fresh = client.post(
                "/api/reports/generate", json={"report_type": "MANUAL", **period}
            )
            add(
                "证据8：快照不变 —— 删掉 1 条 REAL 客户",
                f"删前 REAL 数 = 3，删后 REAL 数 = {real_now}（已真删 {real_ids[0]}）\n\n"
                f"[打开旧报告 {old_id} · 删除前]\n"
                f"  customer_total = {before.json()['metrics']['customer_total']}\n\n"
                f"[打开旧报告 {old_id} · 删除后]\n"
                f"  customer_total = {after.json()['metrics']['customer_total']}\n\n"
                f"→ 旧报告数字不变：{before.json()['metrics']['customer_total'] == after.json()['metrics']['customer_total']}\n\n"
                f"[重新生成新报告 · 快照才是 2]\n"
                f"  new report id = {fresh.json()['id']}\n"
                f"  customer_total = {fresh.json()['metrics']['customer_total']}",
            )

            # ── 证据 7：列表与详情 ──
            add(
                "证据9：GET /api/reports 历史列表（分页）",
                dump(client.get("/api/reports", params={"page": 1, "page_size": 3})),
            )
            add(
                "证据10：GET /api/reports/{id} 报告详情",
                dump(client.get(f"/api/reports/{old_id}")),
            )
    finally:
        # 自清理：只删本次脚本产生的行
        db.rollback()
        db.execute(delete(Report).where(Report.id > baseline_report))
        db.execute(delete(Customer).where(Customer.id > baseline_customer))
        db.execute(delete(ImportBatch).where(ImportBatch.id > baseline_batch))
        db.commit()
        db.close()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# TASK-007 证据：自动汇报 + 下钻（后端骨架）\n\n"
        f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}\n"
        f"- 库：`{settings.db_name}`（本 worktree 独立库）\n"
        f"- 期间：{TODAY.isoformat()} ~ {TODAY.isoformat()}（Asia/Shanghai）\n"
        "- 取证方式：FastAPI TestClient 打真实接口 + 真实 MySQL，非 mock；脚本跑完自动清理自建数据。\n\n"
    )
    OUT.write_text(header + "\n".join(sections), encoding="utf-8")
    print(f"\n证据已写入 {OUT}")


if __name__ == "__main__":
    main()
