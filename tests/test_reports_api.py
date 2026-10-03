"""TASK-007 汇报接口测试（AC1~AC6）。

★ 库里用的是真实 MySQL（本 worktree 自己的库 biz_assistant_wt7）。
★ 只增不减：不改已有测试断言，只新增本文件。
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.customer import Customer, CustomerSourceType
from app.models.report import Report

# 期间取"今天"：新写入的 REAL/TEST 客户的 first_seen_at 都落在当天。
# 新增客户指标按 first_seen_at 统计，测试里显式写死为当天 12:00，避免时区/跨零点抖动。
TODAY = date.today()
NOON = datetime.combine(TODAY, time(12, 0))


@pytest.fixture
def clean_reports(db: Session):
    """隔离：测试前后清掉本测试新建的报告与客户行（按 id 基线），不碰库里原有数据。"""
    baseline_report = db.execute(select(func.coalesce(func.max(Report.id), 0))).scalar_one()
    baseline_customer = db.execute(select(func.coalesce(func.max(Customer.id), 0))).scalar_one()

    def purge() -> None:
        db.rollback()
        db.execute(delete(Report).where(Report.id > baseline_report))
        db.execute(delete(Customer).where(Customer.id > baseline_customer))
        db.commit()

    purge()
    yield
    purge()


def _real_customers(db: Session, count: int, *, prefix: str = "REAL客户") -> list[int]:
    """真写 count 条 REAL 客户（不是 mock）。"""
    rows = [
        Customer(
            name=f"{prefix}{i}",
            company_name="测试科技",
            source_type=CustomerSourceType.REAL,
            first_seen_at=NOON,
            last_seen_at=NOON,
        )
        for i in range(1, count + 1)
    ]
    db.add_all(rows)
    db.commit()
    return [r.id for r in rows]


def _real_count(db: Session) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(Customer)
            .where(Customer.source_type == CustomerSourceType.REAL)
        ).scalar_one()
    )


def _generate(client: TestClient, report_type: str = "DAILY") -> dict:
    resp = client.post(
        "/api/reports/generate",
        json={
            "report_type": report_type,
            "period_start": TODAY.isoformat(),
            "period_end": TODAY.isoformat(),
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ─────────────── AC2：REAL 为空 -> NO_DATA（不是 0）+ excluded_test_count > 0 ───────────────


def test_ac2_no_real_data_yields_no_data_not_zero(
    client: TestClient,
    db: Session,
    clean_reports: None,
    clean_imports: None,
    clean_csv: tuple[str, bytes],
) -> None:
    """AC2：库里只有 TEST 数据时，两个指标都必须是 NO_DATA（value=null），且 TEST 条数被记下。"""
    assert _real_count(db) == 0, "本用例前提：库里没有 REAL 客户（测试前请清干净）"

    # 造出真实的 TEST 数据：用 TASK-001 的导入接口把样本文件导进来（不伪造行）
    filename, raw = clean_csv
    preview = client.post("/api/imports/preview", files={"file": (filename, raw, "text/csv")})
    sha = preview.json()["file_sha256"]
    commit = client.post(
        "/api/imports/commit",
        files={"file": (filename, raw, "text/csv")},
        data={"sha256": sha, "source_type": "TEST"},
    )
    assert commit.status_code == 200, commit.text

    body = _generate(client)
    metrics = body["metrics"]

    for key in ("customer_total", "customer_new"):
        assert metrics[key]["state"] == "NO_DATA", metrics[key]
        assert metrics[key]["value"] is None, "★ 绝不许用 0 冒充『没有数据』"
        assert metrics[key]["source_type"] == "IMPORT"
        assert metrics[key]["evidence_ref"], "每个指标都必须带证据锚点"

    assert body["excluded_test_count"] > 0, "TEST 数据必须被记进 excluded_test_count"


# ─────────────── AC6：白名单外的 metric -> 400 ───────────────


def test_ac6_unknown_metric_returns_400(client: TestClient) -> None:
    resp = client.get(
        "/api/reports/drilldown",
        params={"metric": "revenue", "period_start": TODAY.isoformat(), "period_end": TODAY.isoformat()},
    )
    assert resp.status_code == 400
    assert "metric" in resp.json()["detail"]


# ─────────────── AC1 / AC3 / AC4：插 3 条 REAL -> 总数 3 + 下钻恰好 3 条 ───────────────


def test_ac1_ac3_ac4_real_customers_count_and_drilldown_match(
    client: TestClient, db: Session, clean_reports: None
) -> None:
    _real_customers(db, 3)
    body = _generate(client)
    metrics = body["metrics"]

    # AC1：两个指标 + 各自 source_type/evidence_ref + excluded_test_count
    assert set(metrics) == {"customer_total", "customer_new"}
    assert body["excluded_test_count"] == 0  # 本次没有 TEST 行参与
    assert body["timezone"] == "Asia/Shanghai"
    assert body["source_type"] == "IMPORT"

    # AC3：REAL 客户总数 = 3
    assert metrics["customer_total"]["state"] == "VALID"
    assert metrics["customer_total"]["value"] == 3
    assert metrics["customer_total"]["source_type"] == "IMPORT"
    assert metrics["customer_new"]["value"] == 3  # first_seen_at = 今天，落在期间内
    assert metrics["customer_total"]["evidence_ref"] != metrics["customer_new"]["evidence_ref"]

    # AC4：下钻明细条数 == 报告里的数字（同一口径，这是硬断言）
    for key in ("customer_total", "customer_new"):
        resp = client.get(
            "/api/reports/drilldown",
            params={
                "metric": key,
                "period_start": TODAY.isoformat(),
                "period_end": TODAY.isoformat(),
            },
        )
        assert resp.status_code == 200, resp.text
        detail = resp.json()
        assert len(detail["items"]) == metrics[key]["value"] == 3
        assert detail["total"]["value"] == metrics[key]["value"]
        assert detail["evidence_ref"] == metrics[key]["evidence_ref"]
        assert all(item["source_type"] == "REAL" for item in detail["items"])


# ─────────────── AC5：快照冻结 ───────────────


def test_ac5_snapshot_frozen_after_delete(
    client: TestClient, db: Session, clean_reports: None
) -> None:
    ids = _real_customers(db, 3, prefix="快照客户")
    old = _generate(client)
    assert old["metrics"]["customer_total"]["value"] == 3

    # 删掉 1 条 REAL 客户
    db.execute(delete(Customer).where(Customer.id == ids[0]))
    db.commit()
    assert _real_count(db) == 2

    # 旧报告重新打开：数字不许变
    again = client.get(f"/api/reports/{old['id']}")
    assert again.status_code == 200
    assert again.json()["metrics"]["customer_total"]["value"] == 3, "★ 快照必须冻结"

    # 重新生成的新报告才是 2
    fresh = _generate(client)
    assert fresh["metrics"]["customer_total"]["value"] == 2
    assert fresh["id"] != old["id"]


# ─────────────── 历史列表 / 详情（接口 2、3） ───────────────


def test_list_and_detail(client: TestClient, db: Session, clean_reports: None) -> None:
    _real_customers(db, 1)
    created = _generate(client, report_type="WEEKLY")

    listed = client.get("/api/reports", params={"page": 1, "page_size": 5})
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["total"]["value"] >= 1
    assert payload["total"]["state"] == "VALID"
    assert any(item["id"] == created["id"] for item in payload["items"])

    detail = client.get(f"/api/reports/{created['id']}").json()
    assert detail["report_type"] == "WEEKLY"
    assert detail["excluded_test_count"] == 0
    assert detail["metrics"]["customer_total"]["evidence_ref"].startswith("customers:source_type=REAL")

    assert client.get("/api/reports/99999999").status_code == 404
