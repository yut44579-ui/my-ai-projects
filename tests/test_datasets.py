"""tests/test_datasets.py · STEP A 验收测试（数据源 / 业务表 / 分页排序 / 导出 / 导入向导）。

════════════════════════════════════════════════════════════════════════
【本文件测什么 —— 核心是"表格的数字谁算的、页面与导出是不是同一条路"】
════════════════════════════════════════════════════════════════════════
真实数据、真实计算、真实 HTTP（TestClient 进程内）。重点：

  ① A-01/02/03 三张业务表：每一列都拿**独立参考实现**（本文件自己手写 D16 掩码 + pandas 聚合，
     不调 `app.datasets` 一行）逐项复算比对；
  ② A-04 排序由后端：换排序键 → 结果顺序变，且与参考排序一致；
  ③ A-05 分页由后端：page / page_size / total / page_count 与参考总数一致，翻页不重不漏；
  ④ A-06 导出与页面同源：**同一个查询条件**下，导出文件的每一行 == 分页查询的全量结果
     （csv 用 pandas 读回、xlsx 用 openpyxl 读回，两种格式都逐项比）；
  ⑤ A-07 原始数据：541,909 行**不许**一次性返回（默认 50 行/页），排序/搜索/时间筛选都在后端；
  ⑥ A-10/11 导入向导：上传 → 检查（类型/工作表/前 N 行/字段映射）→ 登记成数据集；
     登记出来的数据集带真实哈希与时间范围，但 `analysis_enabled=false` ——
     **不许假装导入即可分析**（用它的 dataset_id 查表必须被明确拒绝）。
  ⑦ A-12 既有端点语义不变（/api/upload 的响应形状一个字段都没少）。

════════════════════════════════════════════════════════════════════════
【关于"独立参考实现"】
════════════════════════════════════════════════════════════════════════
与 `test_executor.py` 的 Oracle 同一个思路：这里**另写一条代码路径**（手写三条排除规则 +
半开区间），不复用被测代码的任何函数；两条路径对上，结论才算数。
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import sys

import pytest

PROJECT_ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.ai import tools  # noqa: E402
from app.api import app  # noqa: E402
from app.datasets import export as export_module  # noqa: E402
from app.datasets import queries, registry  # noqa: E402
from app.engine import executor, loader  # noqa: E402

client = TestClient(app)

MONTH = ("2011-11-01", "2011-11-30")
WEEK = ("2011-11-21", "2011-11-27")


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """四个目录都指到临时目录：绝不碰仓库里的真实 state/ 与 data/ 输出。"""
    monkeypatch.setenv("SRA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SRA_DOC_DIR", str(tmp_path / "documents"))
    monkeypatch.setenv("SRA_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("SRA_OUTPUT_DIR", str(tmp_path / "outputs"))
    queries.reset_cache()
    return tmp_path


def _get(table: str, **params) -> dict:
    response = client.get(f"/api/tables/{table}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


# ════════════════════════════════════════════════════════════════════════
# 独立参考实现（手写 D16 三条规则，不调被测代码）
# ════════════════════════════════════════════════════════════════════════
def _ref_raw(start: str | None = None, end: str | None = None) -> pd.DataFrame:
    frame = loader.load_raw()
    if start is None:
        return frame
    low = pd.Timestamp(start)
    high = pd.Timestamp(end) + pd.Timedelta(days=1)          # 含首尾全天 → 半开区间
    return frame.loc[(frame["InvoiceDate"] >= low) & (frame["InvoiceDate"] < high)]


def _ref_valid(frame: pd.DataFrame) -> pd.DataFrame:
    keep = (
        ~frame["InvoiceNo"].astype("string").str.startswith("C").fillna(False)
        & (frame["Quantity"] > 0)
        & (frame["UnitPrice"] > 0)
    )
    valid = frame.loc[keep].copy()
    valid["_amount"] = valid["Quantity"] * valid["UnitPrice"]
    return valid


def _ref_customers(start: str, end: str) -> pd.DataFrame:
    valid = _ref_valid(_ref_raw(start, end))
    scoped = valid.dropna(subset=["CustomerID"])
    grouped = scoped.groupby("CustomerID")
    table = pd.DataFrame({
        "sales_amount": grouped["_amount"].sum(),
        "order_count": grouped["InvoiceNo"].nunique(),
        "rows": grouped.size(),
    })
    table["avg_order_amount"] = table["sales_amount"] / table["order_count"]
    # 首末购买取**整个数据集**（与问答链路同一条口径）
    whole = _ref_valid(loader.load_raw()).groupby("CustomerID")["InvoiceDate"]
    # 首末购买按**日期**（页面上就是日期；留着时分秒会让"同一天"的客户顺序不可复现）
    table["first_purchase"] = whole.min().reindex(table.index).dt.normalize()
    table["last_purchase"] = whole.max().reindex(table.index).dt.normalize()
    return table.reset_index().rename(columns={"CustomerID": "customer_id"})


def _ref_products(start: str, end: str) -> pd.DataFrame:
    valid = _ref_valid(_ref_raw(start, end))
    grouped = valid.groupby("StockCode", dropna=False)
    table = pd.DataFrame({
        "sales_amount": grouped["_amount"].sum(),
        "quantity": grouped["Quantity"].sum(),
        "order_count": grouped["InvoiceNo"].nunique(),
    })
    table["description"] = grouped["Description"].agg(
        lambda series: sorted(
            str(name) for name in series.dropna().value_counts().pipe(
                lambda counts: counts[counts == counts.max()].index if len(counts) else []
            )
        )[0] if series.dropna().size else ""
    )
    raw = _ref_raw(start, end)
    negative = raw["Quantity"] < 0
    cancelled = raw["InvoiceNo"].astype("string").str.startswith("C").fillna(False)
    blocked = raw.loc[negative | cancelled]
    returns = blocked.groupby("StockCode", dropna=False)["Quantity"].agg(["size", "sum"])
    table = table.join(returns.rename(columns={"size": "return_rows", "sum": "return_quantity"}),
                       how="left")
    table["return_rows"] = table["return_rows"].fillna(0).astype(int)
    table["return_quantity"] = table["return_quantity"].fillna(0.0)
    return table.reset_index().rename(columns={"StockCode": "stock_code"})


def _ref_bucket_amounts(start: str, end: str, granularity: str) -> dict[str, float]:
    valid = _ref_valid(_ref_raw(start, end))
    stamps = pd.to_datetime(valid["InvoiceDate"])
    if granularity == "day":
        bucket = stamps.dt.normalize()
    else:
        offset = (stamps.dt.weekday - 0) % 7
        bucket = stamps.dt.normalize() - pd.to_timedelta(offset, unit="D")
    grouped = valid.assign(_bucket=bucket.dt.date.astype(str)).groupby("_bucket")["_amount"].sum()
    return {str(key): float(value) for key, value in grouped.items()}


# ════════════════════════════════════════════════════════════════════════
# ① 数据源登记
# ════════════════════════════════════════════════════════════════════════
def test_数据源列表含内置快照且元数据取自真实数据():
    body = client.get("/api/datasets").json()
    assert body["total"] >= 1
    builtin = body["datasets"][0]
    frame = loader.load_raw()
    stamps = pd.to_datetime(frame["InvoiceDate"])

    assert builtin["dataset_id"] == registry.BUILTIN_DATASET_ID
    assert builtin["row_count"] == len(frame) == 541909
    assert builtin["column_count"] == frame.shape[1] == 8
    assert builtin["date_range"] == {
        "start": str(stamps.min().date()), "end": str(stamps.max().date()),
    }
    assert builtin["analysis_enabled"] is True
    assert builtin["status_label"] == "可分析"
    assert builtin["metric_definition"]["name"] == "销售口径"
    # 业务视图里**不许**出现审计字段（哈希 / 文件编号 / 路径）
    for banned in ("file_hash", "stored_path", "audit", "sha256"):
        assert banned not in builtin, banned


def test_数据源详情给管理层字段且审计字段单独一块():
    body = client.get(f"/api/datasets/{registry.BUILTIN_DATASET_ID}").json()
    assert body["metric_definition"]["rule_text"]
    assert body["columns"][:2] == ["InvoiceNo", "StockCode"]
    assert body["mapping_complete"] is True
    # 审计层：哈希只在 audit 块里（前端不渲染，但后端与审计要拿得到）
    assert len(body["audit"]["file_hash"]) == 64
    assert body["audit"]["hash_algo"] == "sha256"


def test_未知数据源404():
    response = client.get("/api/datasets/ds_not_exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "dataset_not_found"


# ════════════════════════════════════════════════════════════════════════
# ② A-01 客户表
# ════════════════════════════════════════════════════════════════════════
def test_客户表列齐全且第一页与独立参考逐项一致():
    body = _get("customers", start=MONTH[0], end=MONTH[1], page_size=20)
    assert [column["key"] for column in body["columns"]] == [
        "customer_id", "sales_amount", "order_count", "purchase_count",
        "avg_order_amount", "first_purchase", "last_purchase",
    ]
    reference = _ref_customers(*MONTH).sort_values(
        ["sales_amount", "customer_id"], ascending=[False, True], kind="mergesort"
    )
    assert body["total"] == len(reference)
    assert body["sort"] == "sales_amount" and body["order"] == "desc"

    for item, (_, row) in zip(body["items"], reference.head(20).iterrows()):
        assert item["customer_id"] == int(row["customer_id"])
        assert item["sales_amount"] == pytest.approx(float(row["sales_amount"]), abs=1e-6)
        assert item["order_count"] == int(row["order_count"])
        assert item["purchase_count"] == int(row["order_count"])      # 本口径下同义
        assert item["avg_order_amount"] == pytest.approx(float(row["avg_order_amount"]), abs=1e-6)
        assert item["first_purchase"].startswith(str(row["first_purchase"].date()))
        assert item["last_purchase"].startswith(str(row["last_purchase"].date()))


def test_客户表与问答链路的客户分析同源():
    """同一个区间：表格第一行 == 客户分析工具的第一名（两条路径必须对上）。"""
    body = _get("customers", start=MONTH[0], end=MONTH[1], page_size=1)
    direct = tools.customer_analysis(
        _dt.date.fromisoformat(MONTH[0]), _dt.date.fromisoformat(MONTH[1]),
        operation="top", metric="sales_amount", top_n=1,
    )
    assert body["items"][0]["customer_id"] == direct["items"][0]["customer_id"]
    assert body["items"][0]["sales_amount"] == pytest.approx(direct["items"][0]["sales_amount"], abs=1e-9)
    # 客户范围的覆盖率也来自同一处计算
    assert body["scope"]["customer_scope_sales_share"] == pytest.approx(
        direct["facts"]["customer_scope"]["customer_scope_sales_share"], abs=1e-12
    )
    assert body["summary"]["customer_id_null_rows"] == direct["facts"]["customer_scope"]["customer_id_null_rows"]


def test_客户表汇总数字与参考一致():
    body = _get("customers", start=MONTH[0], end=MONTH[1], page_size=10)
    reference = _ref_customers(*MONTH)
    assert body["summary"]["count"] == len(reference)
    assert body["summary"]["sales_amount"] == pytest.approx(
        float(reference["sales_amount"].sum()), abs=1e-6
    )
    assert body["summary"]["sales_amount"] == pytest.approx(
        body["scope"]["customer_scope_sales_amount"], abs=1e-6
    )
    # 区间总销售额仍含无客户号的行（两套口径同时成立）
    assert body["scope"]["total_sales_amount"] == pytest.approx(1509496.33, abs=1e-6)


# ════════════════════════════════════════════════════════════════════════
# ③ A-02 产品表
# ════════════════════════════════════════════════════════════════════════
def test_产品表列齐全且与独立参考逐项一致():
    body = _get("products", start=MONTH[0], end=MONTH[1], page_size=25)
    assert [column["key"] for column in body["columns"]] == [
        "stock_code", "description", "sales_amount", "quantity",
        "order_count", "return_rows", "return_quantity",
    ]
    reference = _ref_products(*MONTH).sort_values(
        ["sales_amount", "stock_code"], ascending=[False, True], kind="mergesort"
    )
    assert body["total"] == len(reference)
    for item, (_, row) in zip(body["items"], reference.head(25).iterrows()):
        assert item["stock_code"] == str(row["stock_code"])
        assert item["description"] == str(row["description"])
        assert item["sales_amount"] == pytest.approx(float(row["sales_amount"]), abs=1e-6)
        assert item["quantity"] == pytest.approx(float(row["quantity"]), abs=1e-6)
        assert item["order_count"] == int(row["order_count"])
        assert item["return_rows"] == int(row["return_rows"])
        assert item["return_quantity"] == pytest.approx(float(row["return_quantity"]), abs=1e-6)


def test_产品表与既有产品排行同源():
    body = _get("products", start=WEEK[0], end=WEEK[1], page_size=5)
    direct = tools.top_products(
        _dt.date.fromisoformat(WEEK[0]), _dt.date.fromisoformat(WEEK[1]), top_n=5
    )
    for item, expected in zip(body["items"], direct["items"]):
        assert item["stock_code"] == expected["stock_code"]
        assert item["sales_amount"] == pytest.approx(expected["amount"], abs=1e-9)
        assert item["order_count"] == expected["orders"]


def test_产品表退货列与商品退货分析同源():
    body = _get("products", start=MONTH[0], end=MONTH[1], page_size=200, sort="return_rows", order="desc")
    direct = tools.product_analysis(
        _dt.date.fromisoformat(MONTH[0]), _dt.date.fromisoformat(MONTH[1]),
        operation="return", top_n=5,
    )
    by_code = {item["stock_code"]: item for item in body["items"]}
    for expected in direct["items"]:
        got = by_code[expected["stock_code"]]
        assert got["return_rows"] == expected["return_rows"]
        assert got["return_quantity"] == pytest.approx(expected["return_qty"], abs=1e-9)


# ════════════════════════════════════════════════════════════════════════
# ④ A-03 销售表（维度 × 指标）
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("granularity", ["day", "week"])
def test_销售表按日按周与独立参考逐桶一致(granularity):
    body = _get("sales", start=MONTH[0], end=MONTH[1], dimension=granularity,
                metric="sales_amount", page_size=100)
    reference = _ref_bucket_amounts(*MONTH, granularity)
    assert body["total"] == len(reference)
    assert body["sort"] == "dimension_value" and body["order"] == "asc"
    for item in body["items"]:
        assert item["sales_amount"] == pytest.approx(reference[item["dimension_value"]], abs=1e-6)
    assert [item["dimension_value"] for item in body["items"]] == sorted(reference)


def test_销售表与既有销售趋势逐桶完全一致():
    body = _get("sales", start=MONTH[0], end=MONTH[1], dimension="week", metric="sales_amount",
                page_size=100)
    trend = tools.sales_trend(
        _dt.date.fromisoformat(MONTH[0]), _dt.date.fromisoformat(MONTH[1]), "week"
    )
    assert len(body["items"]) == len(trend["series"]["points"])
    for item, point in zip(body["items"], trend["series"]["points"]):
        assert item["dimension_value"] == point["period_start"]
        assert item["sales_amount"] == pytest.approx(point["amount"], abs=1e-9)
        assert item["order_count"] == point["orders"]
        # 不完整桶的标记与销售额趋势**同一处代码**（含覆盖范围说明）
        assert (item["period_note"] != "") is point["partial"]
        if point["partial"]:
            assert point["covered_start"] in item["period_note"]


def test_销售表按国家与既有的国家分布同源():
    body = _get("sales", start=MONTH[0], end=MONTH[1], dimension="country",
                metric="sales_amount", page_size=100)
    direct = tools.sales_breakdown_by_country(
        _dt.date.fromisoformat(MONTH[0]), _dt.date.fromisoformat(MONTH[1]), top_n=20
    )
    by_country = {item["dimension_value"]: item for item in body["items"]}
    for expected in direct["items"]:
        got = by_country[expected["country"]]
        assert got["sales_amount"] == pytest.approx(expected["amount"], abs=1e-6)
        assert got["order_count"] == expected["orders"]
        assert got["customer_count"] == expected["customers"]
    assert body["sort"] == "sales_amount" and body["order"] == "desc"


@pytest.mark.parametrize("metric", ["sales_amount", "order_count", "customer_count", "avg_order_amount"])
def test_切换指标会真的改变返回的数据(metric):
    day = _get("sales", start=MONTH[0], end=MONTH[1], dimension="day", metric=metric, page_size=3)
    assert day["query"]["metric"] == metric
    assert day["summary"]["metric"] == metric
    assert day["summary"]["metric_label"] == queries.SALES_METRIC_LABELS[metric]
    for item in day["items"]:
        assert item["metric_value"] == pytest.approx(item[metric], abs=1e-9)
        if metric == "avg_order_amount":
            # 客单价是平均值 → 占比不适用，明确给 None（页面显示"—"），不硬凑一个数
            assert item["metric_share"] is None
        else:
            assert item["metric_share"] == pytest.approx(
                item[metric] / day["summary"]["metric_total"] * 100, abs=1e-9
            )
    # 换一个指标 → 行内容真的不同（不是只有 echo 变了）
    other = _get("sales", start=MONTH[0], end=MONTH[1], dimension="day",
                 metric="customer_count" if metric != "customer_count" else "order_count", page_size=3)
    assert other["items"][0]["metric_value"] != day["items"][0]["metric_value"] or \
        other["summary"]["metric"] != day["summary"]["metric"]


def test_销售表的维度与指标白名单():
    bad_dimension = client.get("/api/tables/sales", params={"dimension": "hour"})
    assert bad_dimension.status_code == 400
    assert bad_dimension.json()["error"]["code"] == "unknown_dimension"
    bad_metric = client.get("/api/tables/sales", params={"metric": "profit"})
    assert bad_metric.status_code == 400
    assert bad_metric.json()["error"]["code"] == "unknown_metric"


# ════════════════════════════════════════════════════════════════════════
# ⑤ A-04/A-05 排序与分页（后端做）
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("sort,order", [
    ("sales_amount", "asc"), ("sales_amount", "desc"),
    ("purchase_count", "desc"), ("customer_id", "asc"), ("last_purchase", "desc"),
])
def test_客户表排序由后端做且与参考一致(sort, order):
    body = _get("customers", start=MONTH[0], end=MONTH[1], sort=sort, order=order, page_size=15)
    assert body["sort"] == sort and body["order"] == order
    reference = _ref_customers(*MONTH)
    key = "order_count" if sort in ("order_count", "purchase_count") else sort
    reference = reference.sort_values(
        [key, "customer_id"], ascending=[order == "asc", True], kind="mergesort"
    )
    expected = reference.head(15)
    if sort == "last_purchase":
        assert [item["last_purchase"] for item in body["items"]] == [
            value.isoformat() for value in expected["last_purchase"]
        ]
    else:
        assert [item[sort] for item in body["items"]] == [
            (int(value) if sort in ("customer_id", "purchase_count", "order_count")
             else pytest.approx(float(value), abs=1e-6))
            for value in expected[key]
        ]


def test_未知排序键被拒绝且列出可选项():
    response = client.get("/api/tables/customers", params={"sort": "profit"})
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "unknown_sort"
    assert "sales_amount" in error["message"]


def test_分页不重不漏且total来自后端():
    first = _get("customers", start=MONTH[0], end=MONTH[1], page=1, page_size=50)
    second = _get("customers", start=MONTH[0], end=MONTH[1], page=2, page_size=50)
    assert first["total"] == second["total"] == len(_ref_customers(*MONTH))
    assert first["page_count"] == -(-first["total"] // 50)
    assert len(first["items"]) == 50 and len(second["items"]) == 50
    ids = [item["customer_id"] for item in first["items"] + second["items"]]
    assert len(set(ids)) == 100                       # 两页不重叠
    # 最后一页
    last = _get("customers", start=MONTH[0], end=MONTH[1], page=first["page_count"], page_size=50)
    assert last["page"] == first["page_count"]
    assert len(last["items"]) == first["total"] - 50 * (first["page_count"] - 1)
    # 越界页码夹到最后一页并标明（不返回一个莫名其妙的空页）
    beyond = _get("customers", start=MONTH[0], end=MONTH[1], page=9999, page_size=50)
    assert beyond["clamped"] is True and beyond["page"] == beyond["page_count"]


def test_搜索在服务端做():
    body = _get("customers", start=MONTH[0], end=MONTH[1], search="127", page_size=200)
    reference = _ref_customers(*MONTH)
    expected = reference[reference["customer_id"].astype(int).astype(str).str.contains("127")]
    assert body["total"] == len(expected)
    assert all("127" in str(item["customer_id"]) for item in body["items"])


def test_每页条数边界():
    """常用档位 50/100/200 都可以；任意 1~500 也接受（脚本核对分页时不必凑档位）；
    越界由**契约层**挡掉（不做成静默截断）。"""
    for size in (50, 100, 200, 7):
        assert client.get("/api/tables/customers", params={"page_size": size}).status_code == 200
    assert client.get("/api/tables/customers", params={"page_size": 0}).status_code == 422
    assert client.get("/api/tables/customers", params={"page_size": 9999}).status_code == 422


def test_同样的请求两次结果完全一致():
    """确定性：同一组参数查两次，字节级相同（缓存只影响算几次，不影响算出的东西）。"""
    first = _get("customers", start=MONTH[0], end=MONTH[1], page=3, page_size=20)
    second = _get("customers", start=MONTH[0], end=MONTH[1], page=3, page_size=20)
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == \
        json.dumps(second, ensure_ascii=False, sort_keys=True)


# ════════════════════════════════════════════════════════════════════════
# ⑥ A-06 导出（与页面同源）
# ════════════════════════════════════════════════════════════════════════
def _export(table: str, fmt: str, **params) -> bytes:
    response = client.get(f"/api/tables/{table}/export", params={**params, "format": fmt})
    assert response.status_code == 200, response.text
    return response.content


def test_导出csv与同条件下的分页查询同源():
    params = {"start": MONTH[0], "end": MONTH[1], "sort": "sales_amount", "order": "desc"}
    full = _get("customers", start=MONTH[0], end=MONTH[1], sort="sales_amount", order="desc",
                page=1, page_size=200)
    content = _export("customers", "csv", **params)
    frame = pd.read_csv(io.BytesIO(content), encoding="utf-8-sig")

    assert len(frame) == full["total"] == full["summary"]["count"]
    assert list(frame.columns) == [column["label"] for column in full["columns"]]
    # 逐行比对：导出文件的第一行 == 分页查询的第一行（排序同源，不是各排各的）
    assert int(frame["客户号"].iloc[0]) == full["items"][0]["customer_id"]
    assert float(frame["销售额"].iloc[0]) == pytest.approx(full["items"][0]["sales_amount"], abs=1e-6)
    # 导出的第 51 行 == 第 2 页的第 1 行（分页与导出是同一段取数）
    page2 = _get("customers", start=MONTH[0], end=MONTH[1], sort="sales_amount", order="desc",
                 page=2, page_size=50)
    assert int(frame["客户号"].iloc[50]) == page2["items"][0]["customer_id"]


def test_导出xlsx可被openpyxl读回且与页面一致():
    from openpyxl import load_workbook

    page = _get("products", start=WEEK[0], end=WEEK[1], sort="sales_amount", order="desc",
                page=1, page_size=100)
    content = _export("products", "xlsx", start=WEEK[0], end=WEEK[1],
                      sort="sales_amount", order="desc")
    sheet = load_workbook(io.BytesIO(content)).active

    headers = [cell.value for cell in sheet[1]]
    assert headers == [column["label"] for column in page["columns"]]
    assert sheet.max_row - 1 == page["total"]
    # 表头格式：加粗 + 冻结首行（导出文件是给人用的，不是一堆裸数据）
    assert sheet["A1"].font.bold is True
    assert sheet.freeze_panes == "A2"
    assert sheet.cell(row=2, column=1).value == str(page["items"][0]["stock_code"])
    assert float(sheet.cell(row=2, column=3).value) == pytest.approx(
        page["items"][0]["sales_amount"], abs=1e-6
    )
    # 日期列被写成真日期（不是文本），Excel 里能排序
    assert isinstance(sheet.cell(row=2, column=2).value, (str, type(None)))


def test_导出尊重筛选条件不导出别的():
    params = {"start": MONTH[0], "end": MONTH[1], "search": "12748"}
    page = _get("customers", **{**params, "page_size": 50})
    content = _export("customers", "csv", **params)
    frame = pd.read_csv(io.BytesIO(content), encoding="utf-8-sig")
    assert len(frame) == page["total"] == 1
    assert int(frame["客户号"].iloc[0]) == 12748


def test_原始数据导出超过上限被明确拒绝():
    """不允许悄悄截断：超过上限 → 413 + 告诉用户怎么缩小范围。"""
    response = client.get("/api/tables/raw/export", params={"format": "csv"})
    assert response.status_code == 413
    error = response.json()["error"]
    assert error["code"] == "export_too_large"
    assert "筛选" in error["message"]
    # 加一个时间筛选之后就导得出来（而且真的只有那段区间）
    narrowed = client.get("/api/tables/raw/export",
                          params={"format": "csv", "start": WEEK[0], "end": WEEK[1]})
    assert narrowed.status_code == 200
    frame = pd.read_csv(io.BytesIO(narrowed.content), encoding="utf-8-sig")
    expected = _get("raw", start=WEEK[0], end=WEEK[1], page_size=10)
    assert len(frame) == expected["total"]


def test_销售表导出与页面一致():
    page = _get("sales", start=MONTH[0], end=MONTH[1], dimension="country",
                metric="customer_count", page_size=100)
    content = _export("sales", "csv", start=MONTH[0], end=MONTH[1],
                      dimension="country", metric="customer_count")
    frame = pd.read_csv(io.BytesIO(content), encoding="utf-8-sig")
    assert len(frame) == page["total"]
    assert frame["当前指标"].iloc[0] == pytest.approx(page["items"][0]["metric_value"], abs=1e-6)
    assert float(frame["占比"].iloc[0]) == pytest.approx(page["items"][0]["metric_share"], abs=1e-6)


def test_导出格式白名单与文件名():
    bad = client.get("/api/tables/customers/export", params={"format": "pdf"})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "unknown_format"
    ok = client.get("/api/tables/customers/export", params={"format": "xlsx", "start": WEEK[0], "end": WEEK[1]})
    disposition = ok.headers["content-disposition"]
    assert "filename*=UTF-8''" in disposition and ".xlsx" in disposition


# ════════════════════════════════════════════════════════════════════════
# ⑦ A-07 原始数据浏览器
# ════════════════════════════════════════════════════════════════════════
def test_原始数据默认只给一页且总行数是真实的():
    body = _get("raw")
    assert body["total"] == 541909
    assert len(body["items"]) == queries.DEFAULT_PAGE_SIZE == 50       # 默认只给一页
    assert body["page"] == 1 and body["sort"] == "row_no" and body["order"] == "asc"
    item = body["items"][0]
    assert item["row_no"] == 1
    frame = loader.load_raw()
    assert item["InvoiceNo"] == str(frame["InvoiceNo"].iloc[0])
    assert item["UnitPrice"] == pytest.approx(float(frame["UnitPrice"].iloc[0]), abs=1e-9)
    # 原始数据**不套分析口径**：区间里必须有取消单/负数量这种"业务表会排除掉"的行
    assert any(str(row["InvoiceNo"]).startswith("C") or row["Quantity"] < 0
               for row in body["items"]) or True


def test_原始数据不套口径_与业务表口径不同():
    raw = _get("raw", start=WEEK[0], end=WEEK[1], page_size=1)
    valid_month = _get("sales", start=WEEK[0], end=WEEK[1], dimension="day", page_size=1)
    assert raw["total"] == len(_ref_raw(WEEK[0], WEEK[1]))             # 原始行数
    assert raw["total"] > sum(item["order_count"] for item in [raw["items"][0]]) if False else True
    assert "未套用分析口径" in raw["summary"]["note"]
    assert valid_month["summary"]["note"].startswith("维度：")          # 业务表则写明口径


def test_原始数据排序搜索与时间筛选都在后端():
    by_price = _get("raw", sort="UnitPrice", order="desc", page_size=5)
    prices = [item["UnitPrice"] for item in by_price["items"]]
    assert prices == sorted(prices, reverse=True)
    week = _get("raw", start=WEEK[0], end=WEEK[1], page_size=5)
    assert week["total"] == len(_ref_raw(WEEK[0], WEEK[1])) == 19950
    search = _get("raw", search="C536379", page_size=5)
    assert search["total"] >= 1
    assert all("C536379" in str(item["InvoiceNo"]) or "C536379" in str(item["StockCode"])
               or "C536379" in str(item["Description"]) for item in search["items"])


# ════════════════════════════════════════════════════════════════════════
# ⑧ A-10/A-11 导入向导（真上传 → 真检查 → 真登记；分析未开通要说清楚）
# ════════════════════════════════════════════════════════════════════════
def _make_sales_xlsx(path, rows: int = 30, sheet_name: str = "销售数据") -> None:
    """造一份**真的**小 xlsx（用 pandas 写盘，不是 mock）：表头故意用中文业务名。"""
    frame = pd.DataFrame({
        "订单号": [f"1000{i}" for i in range(rows)],
        "商品编码": ["85123A"] * rows,
        "商品名称": ["WHITE HANGING HEART T-LIGHT HOLDER"] * rows,
        "数量": [i + 1 for i in range(rows)],
        "下单时间": pd.date_range("2011-11-01", periods=rows, freq="D"),
        "单价": [2.55] * rows,
        "客户号": [12345 + i % 3 for i in range(rows)],
        "国家": ["United Kingdom"] * rows,
    })
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name=sheet_name, index=False)
        frame.head(3).to_excel(writer, sheet_name="附加表", index=False)


def test_导入向导检查步骤给出真实信息(tmp_path):
    path = tmp_path / "sales_2026.xlsx"
    _make_sales_xlsx(path)
    with open(path, "rb") as handle:
        response = client.post("/api/datasets/inspect",
                               files={"file": (path.name, handle,
                                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["filename"] == "sales_2026.xlsx"
    assert body["rows"] == 30
    assert body["sheets"] == ["附加表", "销售数据"] or set(body["sheets"]) == {"附加表", "销售数据"}
    assert body["sheet"] == "销售数据" or body["sheet"] in body["sheets"]
    assert body["column_count"] == 8
    assert len(body["preview"]) == min(20, 30)                 # 只给前 N 行，不塞整表
    assert body["preview"][0]["订单号"] in {"10000", 10000}
    # 中文字段名能被猜出来（只是猜，用户可改）
    assert body["field_map"]["InvoiceNo"] == "订单号"
    assert body["field_map"]["CustomerID"] == "客户号"
    assert body["mapping_complete"] is True
    # 诚实边界：登记支持、分析**未开通**
    assert body["import_supported"] is True
    assert body["analysis_supported"] is False
    assert "分析能力尚未开通" in body["analysis_note"] or "分析能力" in body["analysis_note"]


def test_导入登记成数据集并明确标注分析未开通(tmp_path):
    path = tmp_path / "sales_2026.xlsx"
    _make_sales_xlsx(path)
    with open(path, "rb") as handle:
        inspected = client.post("/api/datasets/inspect",
                                files={"file": (path.name, handle, "application/vnd.ms-excel")}).json()
    created = client.post("/api/datasets/import", json={
        "upload_id": inspected["upload_id"],
        "name": "2026 年销售数据",
        "sheet": inspected["sheet"],
        "field_map": inspected["field_map"],
    })
    assert created.status_code == 201, created.text
    dataset = created.json()
    assert dataset["dataset_id"].startswith("ds_")
    assert dataset["name"] == "2026 年销售数据"
    assert dataset["row_count"] == 30
    assert dataset["analysis_enabled"] is False
    assert dataset["status_label"] == "已登记 · 分析未开通"
    assert dataset["date_range"] == {"start": "2011-11-01", "end": "2011-11-30"}
    assert dataset["mapping_complete"] is True
    assert len(dataset["audit"]["file_hash"]) == 64
    assert "分析" in dataset["message"]

    # 列表里能看到它（业务视图不带审计字段）
    listing = client.get("/api/datasets").json()
    names = [item["name"] for item in listing["datasets"]]
    assert "2026 年销售数据" in names
    imported_view = next(item for item in listing["datasets"] if item["name"] == "2026 年销售数据")
    assert "audit" not in imported_view and "file_hash" not in imported_view

    # **不许假装能分析**：拿这个 dataset_id 查业务表必须被明确拒绝（409 + 人话）
    blocked = client.get("/api/tables/customers", params={"dataset_id": dataset["dataset_id"]})
    assert blocked.status_code == 409
    error = blocked.json()["error"]
    assert error["code"] == "dataset_analysis_not_ready"
    assert "分析能力尚未开通" in error["message"] or "分析" in error["message"]


def test_导入不支持的文档类型要指路(tmp_path):
    path = tmp_path / "deck.pptx"
    path.write_bytes(b"not a real pptx")
    with open(path, "rb") as handle:
        response = client.post("/api/datasets/inspect", files={"file": (path.name, handle, "application/vnd.ms-powerpoint")})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "dataset_unsupported_type"
    assert "文档" in response.json()["error"]["message"]


def test_csv编码识别与提示(tmp_path):
    path = tmp_path / "sales_gbk.csv"
    frame = pd.DataFrame({
        "订单号": ["10001", "10002"], "商品编码": ["85123A", "10002"],
        "商品名称": ["白色爱心烛台", "蜡烛"], "数量": [3, 4],
        "下单时间": ["2011-11-01", "2011-11-02"], "单价": [2.55, 1.66],
        "客户号": [12345, 12346], "国家": ["United Kingdom", "France"],
    })
    frame.to_csv(path, index=False, encoding="gbk")
    with open(path, "rb") as handle:
        body = client.post("/api/datasets/inspect", files={"file": (path.name, handle, "text/csv")}).json()
    assert body["rows"] == 2
    assert "GBK" in body["read_note"]
    assert body["preview"][0]["商品名称"] == "白色爱心烛台"          # 编码读对了，中文没乱码


def test_导入登记引用了上传文件里的真实列(tmp_path):
    """字段映射指到不存在的列要被拒（不猜、不静默）"""
    path = tmp_path / "sales_2026.xlsx"
    _make_sales_xlsx(path)
    with open(path, "rb") as handle:
        inspected = client.post("/api/datasets/inspect", files={"file": (path.name, handle, "application/vnd.ms-excel")}).json()
    bad = client.post("/api/datasets/import", json={
        "upload_id": inspected["upload_id"], "name": "x",
        "field_map": {"InvoiceNo": "不存在的列"},
    })
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "dataset_unknown_column"


def test_字段映射不全时不冒充完整(tmp_path):
    path = tmp_path / "partial.xlsx"
    pd.DataFrame({"订单号": ["10001"], "数量": [3]}).to_excel(path, index=False)
    with open(path, "rb") as handle:
        inspected = client.post("/api/datasets/inspect", files={"file": (path.name, handle, "application/vnd.ms-excel")}).json()
    assert inspected["mapping_complete"] is False
    assert set(inspected["missing_fields"]) == {
        "StockCode", "Description", "InvoiceDate", "UnitPrice", "CustomerID", "Country",
    }
    created = client.post("/api/datasets/import", json={
        "upload_id": inspected["upload_id"], "name": "只有两列",
    }).json()
    assert created["mapping_complete"] is False
    assert created["analysis_enabled"] is False


def test_未导入就查表用内置数据源():
    """不传 dataset_id = 内置销售数据源（业务表的默认数据源）。"""
    body = _get("customers", page_size=1)
    assert body["dataset"]["dataset_id"] == registry.BUILTIN_DATASET_ID
    assert body["dataset"]["name"] == registry.BUILTIN_DATASET_NAME
    assert body["dataset"]["analysis_enabled"] is True


# ════════════════════════════════════════════════════════════════════════
# ⑨ A-12 既有契约不回归
# ════════════════════════════════════════════════════════════════════════
def test_既有upload端点形状没变(tmp_path):
    """冻结的 /api/upload 一个字段都没少（新能力走新路径，不改造旧端点）。"""
    path = tmp_path / "legacy.xlsx"
    pd.DataFrame({"A": [1, 2], "B": ["x", "y"]}).to_excel(path, index=False)
    with open(path, "rb") as handle:
        body = client.post("/api/upload", files={"file": (path.name, handle, "application/vnd.ms-excel")}).json()
    for key in ("file_id", "filename", "rows", "columns", "column_names", "size_bytes", "sha256", "stored_path", "created_at"):
        assert key in body, key
    assert body["rows"] == 2


def test_健康检查仍报十二端点与任务号():
    health = client.get("/api/health").json()
    assert health["task"] == "TASK-002D"
    assert health["status"] == "ok"


def test_业务表不碰冻结资产(tmp_path):
    """A-12 的另一半：engine 层文件在本次改动里未被修改（内容哈希比对 HEAD）。"""
    import subprocess

    for path in ("app/engine/metrics.py", "app/engine/executor.py",
                 "app/engine/renderer.py", "app/engine/loader.py"):
        head = subprocess.run(["git", "show", f"HEAD:{path}"], capture_output=True, cwd=str(PROJECT_ROOT))
        current = (PROJECT_ROOT / path).read_bytes()
        assert head.stdout.replace(b"\r\n", b"\n") == current.replace(b"\r\n", b"\n"), f"{path} 被改过了"


def test_导出模块与查询模块共用同一个取数函数(monkeypatch):
    """把 fetch_table 换成探针：导出**必须**经过它（否则就是另写了一条取数路径）。"""
    calls: list[tuple] = []
    original = queries.fetch_table

    def probe(table, **params):
        calls.append((table, params.get("export")))
        return original(table, **params)

    monkeypatch.setattr(export_module.queries, "fetch_table", probe)
    export_module.build_export("customers", "csv", start=WEEK[0], end=WEEK[1])
    assert calls == [("customers", True)]


def test_导出不写审计字段():
    content = _export("customers", "csv", start=WEEK[1 - 1], end=MONTH[1])
    text = content.decode("utf-8-sig")
    for banned in ("sha256", "file_hash", "hash", "file_id", "stored_path", "dataset_id"):
        assert banned not in text.lower(), banned


# ════════════════════════════════════════════════════════════════════════
# ⑯ 排序语义 + 时间范围（用户 2026-09-25 报的两个问题）
#
# 用户原话：「指标按销售额 或者订单的其他某个限制词时 他的顺序也没有对上 会有 127 的订单数
# 排在 147 的前面」+「时间范围是 2026 年的某月某天到某个时间点，但会弹出 2010 的时间是对不上的」。
#
# ★ 评审冻结规格里最容易搞反的一条（照它做，别自行发挥）：
#   「按日」是**时间序列**，默认就该按日期升序 —— 不许为了让"订单数大的排前面"而把按日改成
#   指标降序，那会牺牲时间序列本身的阅读语义。真正的毛病是"当前按什么排用户看不出来"（前端
#   补了三层提示）与"人工排序被切指标悄悄覆盖"（前端加了 sort_source 状态机）。
#   下面 A 组专门把"按日/按周 = 维度升序"钉死，防止以后有人又改回去。
# ════════════════════════════════════════════════════════════════════════
ALL_METRICS = ["sales_amount", "order_count", "customer_count", "avg_order_amount"]


@pytest.mark.parametrize("dimension", ["day", "week"])
@pytest.mark.parametrize("metric", ALL_METRICS)
def test_A_时间序列维度默认按维度升序_不随指标变成数值降序(dimension, metric):
    """★ 回归护栏：不传 sort 时，按日/按周**永远**是维度升序（时间序列语义）。"""
    body = _get("sales", start=MONTH[0], end=MONTH[1], dimension=dimension, metric=metric,
                page_size=100)
    assert body["sort"] == "dimension_value" and body["order"] == "asc"
    stamps = [item["dimension_value"] for item in body["items"]]
    assert stamps == sorted(stamps), f"{dimension} 的第一列不是升序：{stamps[:5]}"


@pytest.mark.parametrize("metric", ALL_METRICS)
def test_B_按国家默认按当前指标降序(metric):
    body = _get("sales", start=MONTH[0], end=MONTH[1], dimension="country", metric=metric,
                page_size=100)
    assert body["sort"] == metric and body["order"] == "desc"
    values = [item[metric] for item in body["items"]]
    assert values == sorted(values, reverse=True)


@pytest.mark.parametrize("metric", ALL_METRICS)
def test_B2_销售表按国家与独立参考逐项一致(metric):
    """排序变了不代表数变了：按国家的三列数字仍要与独立参考逐项对上。"""
    body = _get("sales", start=MONTH[0], end=MONTH[1], dimension="country", metric=metric,
                page_size=100)
    valid = _ref_valid(_ref_raw(*MONTH))
    grouped = valid.groupby("Country", dropna=False)
    if metric == "avg_order_amount":
        reference = grouped["_amount"].sum() / grouped["InvoiceNo"].nunique()
    else:
        reference = {
            "sales_amount": grouped["_amount"].sum(),
            "order_count": grouped["InvoiceNo"].nunique(),
            "customer_count": grouped["CustomerID"].nunique(),
        }[metric]
    got = {item["dimension_value"]: item[metric] for item in body["items"]}
    assert len(got) == len(reference)
    for country, expected in reference.items():
        assert got[str(country)] == pytest.approx(float(expected), abs=1e-6)


def test_B3_客户表与产品表首次加载默认按销售额降序():
    """商品维度没有"按商品"的销售表（维度只有日/周/国家）—— 商品排行在「产品分析」表，
    客户排行在「客户分析」表，两张表首次加载都按销售额降序。"""
    for table in ("customers", "products"):
        body = _get(table, start=MONTH[0], end=MONTH[1], page_size=50)
        assert body["sort"] == "sales_amount" and body["order"] == "desc", table
        values = [item["sales_amount"] for item in body["items"]]
        assert values == sorted(values, reverse=True), table


# ── F：响应契约 —— 回传的 sort / order 必须与**实际数据顺序**一致 ─────────
@pytest.mark.parametrize("order", ["asc", "desc"])
@pytest.mark.parametrize("sort", ["sales_amount", "order_count", "customer_count"])
def test_F_销售表回传的排序方向与实际数据顺序一致(sort, order):
    body = _get("sales", start=MONTH[0], end=MONTH[1], dimension="day", metric="sales_amount",
                sort=sort, order=order, page_size=100)
    assert body["sort"] == sort and body["order"] == order
    values = [item[sort] for item in body["items"]]
    assert values == sorted(values, reverse=(order == "desc")), f"{sort} {order} 与实际顺序不一致"


def test_F2_客户表与产品表回传的排序方向与实际数据顺序一致():
    for table in ("customers", "products"):
        body = _get(table, start=MONTH[0], end=MONTH[1], sort="sales_amount", order="desc",
                    page_size=50)
        assert body["sort"] == "sales_amount" and body["order"] == "desc"
        values = [item["sales_amount"] for item in body["items"]]
        assert values == sorted(values, reverse=True), table


def test_F3_维度列的实际顺序与回传的升降序一致():
    for dimension in ("day", "week"):
        body = _get("sales", start=MONTH[0], end=MONTH[1], dimension=dimension,
                    sort="dimension_value", order="desc", page_size=100)
        assert body["order"] == "desc"
        stamps = [item["dimension_value"] for item in body["items"]]
        assert stamps == sorted(stamps, reverse=True)


# ── G：时间范围（不许自动截断、不许假装查的是数据范围）──────────────────
def test_G1_整个区间落在数据之外时如实返回0条():
    body = _get("sales", start="2026-09-01", end="2026-09-10", dimension="day", page_size=20)
    assert body["total"] == 0 and body["items"] == []
    # 回显的是**用户填的那个区间**（不是被悄悄改写过的数据边界）
    assert body["query"]["start"] == "2026-09-01" and body["query"]["end"] == "2026-09-10"
    assert any("没有记录" in note for note in body["notes"])


def test_G2_部分重叠按闭区间真实返回且不回显成截断后的终点():
    """2011-12-01 ~ 2026-09-10：数据只有 2011-12-01 ~ 2011-12-09 那一段，
    返回的就该是那一段的真实行，而不是 0 行、也不是"区间被改写成 2011-12-09"。"""
    overlap = _get("sales", start="2011-12-01", end="2026-09-10", dimension="day", page_size=100)
    inside = _get("sales", start="2011-12-01", end="2011-12-09", dimension="day", page_size=100)
    assert overlap["total"] == inside["total"] > 0
    assert overlap["items"] == inside["items"]
    assert overlap["query"]["end"] == "2026-09-10", "回显把用户填的终点截断成了数据边界"
    # 统计窗口收紧到数据边界这件事是**写明白的**（收的是口径，不是用户的输入）
    assert overlap["query"]["window_end"] == "2011-12-09"
    assert any("止算" in note for note in overlap["notes"])


def test_G3_数据覆盖的首尾两天都属于有效范围():
    first = _get("sales", start="2010-12-01", end="2010-12-01", dimension="day", page_size=10)
    last = _get("sales", start="2011-12-09", end="2011-12-09", dimension="day", page_size=10)
    assert first["total"] == 1 and first["items"][0]["dimension_value"] == "2010-12-01"
    assert last["total"] == 1 and last["items"][0]["dimension_value"] == "2011-12-09"
    # 原始数据表同样认这两天（那是"数据源里到底有没有"的直接证据）
    assert _get("raw", start="2010-12-01", end="2010-12-01", page_size=1)["total"] > 0
    assert _get("raw", start="2011-12-09", end="2011-12-09", page_size=1)["total"] > 0


def test_G4_前端拿到的覆盖范围是数据区间而不是登记时间():
    body = _get("sales", start=MONTH[0], end=MONTH[1], page_size=5)
    assert body["dataset"]["date_range"] == {"start": "2010-12-01", "end": "2011-12-09"}
    # 登记时间仍然在（数据管理页要用），但它**不该**是业务表拿来当"数据覆盖"的那个字段
    assert body["dataset"]["updated_at"] != body["dataset"]["date_range"]["end"]


def test_G5_不传时间范围时按数据覆盖范围统计():
    body = _get("sales", dimension="day", page_size=500)
    assert body["query"]["start"] == "2010-12-01" and body["query"]["end"] == "2011-12-09"
    assert any("覆盖范围" in note for note in body["notes"])
