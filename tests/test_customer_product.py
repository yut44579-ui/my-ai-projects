"""tests/test_customer_product.py · TASK-006 验收测试（客户分析 + 商品分析）。

════════════════════════════════════════════════════════════════════════
【本文件测什么 —— 核心是"两条口径不能混、排序不能由模型说了算"】
════════════════════════════════════════════════════════════════════════
真实数据、真实计算、真实 HTTP（TestClient 进程内）。重点：

  ① AC-01/02 客户 TOP / 购买次数 TOP：用一个**独立参考实现**（本文件自己手写 D16 三条掩码，
     不调 `app.ai.tools`、也不调 `metrics` 的掩码函数）逐项复算比对；
  ② AC-05/06 CustomerID 空值是一等公民：客户维度只覆盖非空客户号，销售额仍含空值行，
     覆盖率由**代码**算（本文件独立复算这个比例）；
  ③ AC-07 沉睡客户是**规则型**判定：不得出现 churn_probability / churn_prediction 之类字段；
  ④ AC-08 商品 TOP 与既有 `top_products` 同参数必须一致（兼容性回归）；
  ⑤ AC-09 商品趋势逐商品聚合、桶完整性与销售额趋势**同一规则**、逐桶与参考值比对；
  ⑥ AC-10 退货两个口径分开、统一指标必须**去重**（并集，不是 a+b），且有重叠样本测试；
  ⑦ AC-11/12 VIP 等不存在的客户维度必须拒绝，**销售额 TOP 不许顶替 VIP TOP**；
     区域边界的拒绝行为不许因为加入客户维度而改变；
  ⑧ AC-13/14/15 LLM 只拿结构化事实、不负责排序、写错数字照样被闸门拦。

════════════════════════════════════════════════════════════════════════
【关于本文件里的 monkeypatch】
════════════════════════════════════════════════════════════════════════
与 `tests/test_chat.py` 同一条规矩：**业务数字一个都不许假造**，全部来自真实数据集的真实计算。
被替换的只有 `app.ai.llm` 这个**网络出口**（单元测试不能依赖外网与第三方服务），
换掉它测的恰恰是"模型说胡话时系统怎么办"。
"""

from __future__ import annotations

import datetime as _dt
import json
import sys

import pytest

PROJECT_ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.ai import answer, intent as intent_module, llm, tools  # noqa: E402
from app.api import app  # noqa: E402
from app.engine import executor, loader  # noqa: E402

client = TestClient(app)

# 本 TASK 固定使用的两个窗口（都是真实数据里存在的区间）
MONTH = ("2011-11-01", "2011-11-30")
WEEK = ("2011-11-21", "2011-11-27")


# ════════════════════════════════════════════════════════════════════════
# 夹具（与 test_chat.py 同一套：目录隔离 + 可关掉 LLM 通道）
# ════════════════════════════════════════════════════════════════════════
@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("SRA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SRA_DOC_DIR", str(tmp_path / "documents"))
    monkeypatch.setenv("SRA_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("SRA_OUTPUT_DIR", str(tmp_path / "outputs"))
    return tmp_path


@pytest.fixture
def no_llm(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: False)
    return None


def _ask(question: str, *, use_llm: bool = False) -> dict:
    response = client.post("/api/chat", json={"question": question, "use_llm": use_llm})
    assert response.status_code == 200, response.text
    return response.json()


# ════════════════════════════════════════════════════════════════════════
# 独立参考实现（**另写一条代码路径**）
#
# 规矩：这里**不 import** `app.ai.tools` 的取数/聚合函数，也**不用** `app.engine.metrics`
# 的掩码 helper —— D16 的三条规则与"含首尾全天"在这里按 DECISIONS.md 的字面意思重写一遍。
# 只有两条路径彼此独立，比对才有意义（test_executor.py 的 Oracle 是同一个思路）。
# ════════════════════════════════════════════════════════════════════════
def _ref_window(start: str, end: str) -> pd.DataFrame:
    """区间内**原始行**（只做时间筛选，不套排除规则）。"""
    frame = loader.load_raw()
    low = pd.Timestamp(start)
    high = pd.Timestamp(end) + pd.Timedelta(days=1)          # 含首尾全天 → 半开区间
    return frame.loc[(frame["InvoiceDate"] >= low) & (frame["InvoiceDate"] < high)].copy()


def _ref_valid(rows: pd.DataFrame) -> pd.DataFrame:
    """D16 三条排除规则（手写）：取消单 / 数量≤0 / 单价≤0。"""
    keep = (
        ~rows["InvoiceNo"].astype("string").str.startswith("C").fillna(False)
        & (rows["Quantity"] > 0)
        & (rows["UnitPrice"] > 0)
    )
    valid = rows.loc[keep].copy()
    valid["_ref_amount"] = valid["Quantity"] * valid["UnitPrice"]
    return valid


def _ref_customer_table(rows: pd.DataFrame) -> pd.DataFrame:
    """客户表：金额 = 有效行金额合计；购买次数 = **distinct InvoiceNo**。"""
    scoped = rows.dropna(subset=["CustomerID"])
    grouped = scoped.groupby("CustomerID")
    table = pd.DataFrame({
        "amount": grouped["_ref_amount"].sum(),
        "purchase_count": grouped["InvoiceNo"].nunique(),
        "rows": grouped.size(),
    })
    return table.sort_values(["amount", "CustomerID"], ascending=[False, True], kind="mergesort")


def _ref_first_last_purchase() -> tuple[pd.Series, pd.Series]:
    """整个数据集里每个客户的**首次 / 最后一次有效购买日期**（**归一成日期**）。

    为什么必须 `.dt.normalize()`：口径说的是"日期减日期"（`reference_date − last_purchase_date ≥ N`）。
    留着时分秒再相减 `.dt.days` 会**向下取整** —— 2011-11-09 10:31 到 2011-12-09 00:00 会算成
    29 天（而不是整 30 天），阈值正好落在边界上的客户就被漏掉。这类差一天的问题很难从数字上看出来。
    """
    frame = loader.load_raw()
    valid = _ref_valid(frame)
    grouped = valid.groupby("CustomerID")["InvoiceDate"]
    return grouped.min().dt.normalize(), grouped.max().dt.normalize()


def _ref_id(value) -> int | float:
    number = float(value)
    return int(number) if number.is_integer() else number


# ════════════════════════════════════════════════════════════════════════
# AC-01 · 客户 TOP N（排序由代码、与独立参考逐项一致）
# ════════════════════════════════════════════════════════════════════════
def test_AC01_客户TOP10与独立参考逐项一致():
    start, end = MONTH
    result = tools.customer_analysis(
        _dt.date.fromisoformat(start), _dt.date.fromisoformat(end),
        operation="top", metric="sales_amount", top_n=10,
    )
    reference = _ref_customer_table(_ref_valid(_ref_window(start, end))).head(10)

    assert len(result["items"]) == 10
    for item, (customer_id, row) in zip(result["items"], reference.iterrows()):
        assert item["customer_id"] == _ref_id(customer_id), "客户号（去重后按金额降序）"
        assert item["sales_amount"] == pytest.approx(float(row["amount"]), abs=1e-9)
        assert item["purchase_count"] == int(row["purchase_count"])
        assert item["rows"] == int(row["rows"])
    # 榜单必须**严格降序**（排序是代码做的）
    amounts = [item["sales_amount"] for item in result["items"]]
    assert amounts == sorted(amounts, reverse=True)
    assert [item["rank"] for item in result["items"]] == list(range(1, 11))


def test_AC01_并列时按客户号升序且不含空客户号():
    start, end = MONTH
    result = tools.customer_analysis(
        _dt.date.fromisoformat(start), _dt.date.fromisoformat(end),
        operation="top", metric="purchase_count", top_n=20,
    )
    reference = _ref_customer_table(_ref_valid(_ref_window(start, end)))
    ordered = reference.sort_values(
        ["purchase_count", "CustomerID"], ascending=[False, True], kind="mergesort"
    ).head(20)

    assert [item["customer_id"] for item in result["items"]] == [
        _ref_id(customer_id) for customer_id in ordered.index
    ]
    # 并列（购买次数相同）时客户号必须升序 —— 问多少次榜单都一样
    pairs = [(item["purchase_count"], item["customer_id"]) for item in result["items"]]
    assert pairs == sorted(pairs, key=lambda pair: (-pair[0], pair[1]))
    assert any(a[0] == b[0] for a, b in zip(pairs, pairs[1:])), "榜单里必须真的存在并列，才验得到 tie-break"
    # 客户号为空的成交**不许**出现在客户榜单里
    assert all(item["customer_id"] is not None for item in result["items"])


# ════════════════════════════════════════════════════════════════════════
# AC-02 · 购买次数 = CustomerID + distinct InvoiceNo（不是行数）
# ════════════════════════════════════════════════════════════════════════
def test_AC02_购买次数按distinct发票号而非行数():
    start, end = MONTH
    result = tools.customer_analysis(
        _dt.date.fromisoformat(start), _dt.date.fromisoformat(end),
        operation="top", metric="purchase_count", top_n=10,
    )
    # 独立参考：同一个客户的行数 vs 发票号去重数
    valid = _ref_valid(_ref_window(start, end))
    scoped = valid.dropna(subset=["CustomerID"])
    reference = (
        scoped.groupby("CustomerID")
        .agg(purchase_count=("InvoiceNo", "nunique"), rows=("InvoiceNo", "size"))
        .sort_values(["purchase_count", "CustomerID"], ascending=[False, True], kind="mergesort")
    )

    for item in result["items"]:
        row = reference.loc[float(item["customer_id"])]
        assert item["purchase_count"] == int(row["purchase_count"])
        assert item["rows"] == int(row["rows"])
        # **关键**：一个客户的行数通常远大于购买次数（一单多商品行）
        assert item["rows"] >= item["purchase_count"]
    # 至少有一个客户能证明"行数 ≠ 次数"（否则这条测试等于没测）
    assert any(item["rows"] > item["purchase_count"] for item in result["items"])


# ════════════════════════════════════════════════════════════════════════
# AC-03 · 复购率：三个数可互相验算
# ════════════════════════════════════════════════════════════════════════
def test_AC03_复购率三者可互相验算():
    start, end = MONTH
    result = tools.customer_analysis(
        _dt.date.fromisoformat(start), _dt.date.fromisoformat(end), operation="repeat_rate"
    )
    facts = result["facts"]
    reference = _ref_customer_table(_ref_valid(_ref_window(start, end)))

    assert facts["total_customers"] == len(reference)
    assert facts["repeat_customers"] == int((reference["purchase_count"] >= 2).sum())
    assert facts["repeat_rate"] == pytest.approx(
        facts["repeat_customers"] / facts["total_customers"], abs=1e-12
    )
    assert facts["repeat_customers"] + facts["single_purchase_customers"] == facts["total_customers"]
    assert result["selfcheck"]["repeat_rate_consistent"] is True
    assert result["selfcheck"]["repeat_plus_single_equals_total"] is True
    # 复购客户榜只许出现购买次数 ≥ 2 的客户
    assert all(item["purchase_count"] >= 2 for item in result["items"])


def test_AC03_复购率经真HTTP返回且字段齐全(no_llm):
    record = _ask("2011年11月的复购率是多少")
    assert record["intent"]["intent"] == "customer_analysis"
    facts = record["facts"]
    for key in ("total_customers", "repeat_customers", "repeat_rate"):
        assert key in facts
    assert facts["repeat_rate"] == pytest.approx(
        facts["repeat_customers"] / facts["total_customers"], abs=1e-12
    )
    assert record["status"] in ("ok", "degraded")
    # 回答正文里也要能看到这三个数（页面/记录同源）
    labels = [item["label"] for item in record["tool"]["display"]]
    assert "复购率" in labels


# ════════════════════════════════════════════════════════════════════════
# AC-04 · 数据集内新客
# ════════════════════════════════════════════════════════════════════════
def test_AC04_数据集内新客与独立参考一致():
    start, end = MONTH
    result = tools.customer_analysis(
        _dt.date.fromisoformat(start), _dt.date.fromisoformat(end), operation="new_customers"
    )
    first, _last = _ref_first_last_purchase()
    window_customers = _ref_customer_table(
        _ref_valid(_ref_window(start, end))
    ).index
    # 首购日期已归一成日期，区间端点也按日期比（含首尾全天）—— 不这样写会把
    # "11-30 当天买的"漏掉（那是差一天的经典错法）
    expected = int(
        ((first.reindex(window_customers) >= pd.Timestamp(start))
         & (first.reindex(window_customers) <= pd.Timestamp(end))).sum()
    )

    assert result["facts"]["new_customers"] == expected
    assert result["facts"]["new_customers"] + result["facts"]["existing_customers"] == len(window_customers)
    assert result["facts"]["definition_label"] == "数据集内新客"
    assert result["selfcheck"]["first_purchase_all_mapped"] is True
    # 新客榜里每个客户的首次购买日期都必须落在目标区间内
    for item in result["items"]:
        assert start <= item["first_purchase"] <= end


def test_AC04_回答里明确写的是数据集内新客(no_llm):
    record = _ask("2011年11月有多少新客")
    assert record["facts"]["definition_label"] == "数据集内新客"
    text = record["answer"]["text"]
    assert "数据集内新客" in text
    # 不许把它说成"人生第一次购买"（数据集只到某一天，"真新客"推不出来）
    assert "人生第一次" not in text.replace("**不是**「人生第一次购买」", "")
    assert record["tool"]["display"][0]["label"].endswith("新客")


# ════════════════════════════════════════════════════════════════════════
# AC-05 / AC-06 · CustomerID 空值是一等公民
# ════════════════════════════════════════════════════════════════════════
def test_AC05_customer_scope必备字段且全部由代码算():
    start, end = MONTH
    result = tools.customer_analysis(
        _dt.date.fromisoformat(start), _dt.date.fromisoformat(end), operation="top"
    )
    scope = result["facts"]["customer_scope"]
    for key in (
        "customer_id_null_rows", "customer_id_nonnull_rows",
        "customer_scope_sales_amount", "total_sales_amount", "customer_scope_sales_share",
    ):
        assert key in scope, key

    window = _ref_window(start, end)
    valid = _ref_valid(window)
    scoped = valid.dropna(subset=["CustomerID"])

    # 行数：与计算引擎 validations 同口径（区间内**原始行**）
    assert scope["customer_id_null_rows"] == int(window["CustomerID"].isna().sum())
    assert scope["customer_id_nonnull_rows"] == int(window["CustomerID"].notna().sum())
    # 金额：有效行口径（客户范围 vs 全部）
    assert scope["customer_scope_sales_amount"] == pytest.approx(float(scoped["_ref_amount"].sum()), abs=1e-6)
    assert scope["total_sales_amount"] == pytest.approx(float(valid["_ref_amount"].sum()), abs=1e-6)
    assert scope["customer_scope_sales_share"] == pytest.approx(
        scope["customer_scope_sales_amount"] / scope["total_sales_amount"], abs=1e-12
    )
    # 覆盖率必须与页面上的那一行是同一个数
    share_row = next(item for item in result["display"] if item["label"] == "客户分析覆盖率")
    assert share_row["value"] == pytest.approx(scope["customer_scope_sales_share"] * 100, abs=1e-9)


def test_AC05_每个客户分析operation都带customer_scope(no_llm):
    questions = {
        "2011年11月销售额最高的10个客户": "top",
        "2011年11月购买次数最多的客户": "purchase_frequency",
        "2011年11月的复购率是多少": "repeat_rate",
        "2011年11月有多少新客": "new_customers",
        "2011年11月有多少沉睡客户（90天没买）": "inactive_customers",
    }
    for question, operation in questions.items():
        record = _ask(question)
        assert record["intent"]["intent"] == "customer_analysis", question
        assert record["facts"]["operation"] == operation, question
        scope = record["facts"]["customer_scope"]
        assert scope["customer_id_null_rows"] > 0
        assert 0 < scope["customer_scope_sales_share"] < 1
        # 回答正文里要能说出"覆盖了有客户号的成交，占全部销售额 XX%"
        assert "客户范围" in record["answer"]["text"]


def test_AC06_总销售额含空客户行_客户分析不含(no_llm):
    record = _ask("2011年11月销售额最高的10个客户")
    facts = record["facts"]
    scope = facts["customer_scope"]
    direct = executor.compute_sales_amount(*MONTH)

    # ① 销售额口径仍然**含** CustomerID 为空的行 —— 与冻结资产逐位相等
    assert facts["customer_scope"]["total_sales_amount"] == float(direct["amount"])
    assert scope["total_sales_amount"] == pytest.approx(1509496.33, abs=1e-6)
    # ② 客户维度**不含**空值行：两者相加正好是总额
    assert scope["customer_scope_sales_amount"] + scope["null_customer_sales_amount"] == pytest.approx(
        scope["total_sales_amount"], abs=1e-6
    )
    assert scope["null_customer_sales_amount"] > 0
    # ③ 客户表分组求和 == 客户范围金额（两条独立路径对上）
    assert facts["all_customers_amount"] == pytest.approx(scope["customer_scope_sales_amount"], abs=1e-6)
    assert record["tool"]["selfcheck"]["table_matches_scope"] is True


# ════════════════════════════════════════════════════════════════════════
# AC-07 · 沉睡客户（规则型，不是预测）
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("days", [30, 90, 180])
def test_AC07_沉睡客户规则型判定与参考一致(days):
    start, end = MONTH
    result = tools.customer_analysis(
        _dt.date.fromisoformat(start), _dt.date.fromisoformat(end),
        operation="inactive_customers", inactive_days=days,
    )
    _first, last = _ref_first_last_purchase()
    window_customers = _ref_customer_table(_ref_valid(_ref_window(start, end))).index
    reference = (pd.Timestamp("2011-12-09") - last.reindex(window_customers)).dt.days   # 两侧都已是日期

    facts = result["facts"]
    assert facts["inactive_days"] == days
    assert facts["reference_date"] == "2011-12-09"          # 缺省 = 数据集最后一天
    assert facts["inactive_customers"] == int((reference >= days).sum())
    assert facts["inactive_customers"] + facts["active_customers"] == facts["total_customers"]
    assert facts["inactive_rate"] == pytest.approx(
        facts["inactive_customers"] / facts["total_customers"], abs=1e-12
    )
    # 榜单里每个客户的"距今天数"都必须真的 ≥ 阈值，且按天数降序
    day_list = [item["days_since_last_purchase"] for item in result["items"]]
    assert all(day >= days for day in day_list)
    assert day_list == sorted(day_list, reverse=True)


def test_AC07_不得出现任何churn或预测类字段(no_llm):
    record = _ask("2011年11月有多少沉睡客户（90天没买）")
    dumped = json.dumps(record, ensure_ascii=False).lower()
    for word in ("churn", "churn_probability", "churn_prediction", "probability", "predict"):
        assert word not in dumped, word
    # 用词必须是"沉睡/长期未购买"，而且明确说是规则型判定
    text = record["answer"]["text"]
    assert "沉睡客户" in text
    assert "规则型" in text
    assert "不是预测" in text


# ════════════════════════════════════════════════════════════════════════
# AC-08 · 商品 TOP 与既有 top_products 同参数一致
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("window", [MONTH, WEEK, ("2011-11-01", "2011-11-07")])
@pytest.mark.parametrize("top_n", [5, 10])
def test_AC08_产品TOP与既有top_products完全一致(window, top_n):
    start, end = (_dt.date.fromisoformat(value) for value in window)
    new = tools.product_analysis(start, end, operation="top", metric="sales_amount", top_n=top_n)
    old = tools.top_products(start, end, top_n=top_n)

    assert new["items"] == old["items"]                     # 逐项（含顺序）完全一致
    assert new["facts"]["top_amount"] == old["facts"]["top_amount"]
    assert new["facts"]["total_amount"] == old["facts"]["total_amount"]
    assert new["facts"]["product_count"] == old["facts"]["product_count"]
    assert new["params"]["operation"] == "top"
    assert new["selfcheck"]["compat_with_top_products"]["delegated_to"] == "top_products"
    # 兼容性不是"测试对齐"出来的：同参数走的是同一个函数调用，结果连 notes 都是加了一句而已
    assert new["display"] == old["display"]
    assert set(old["notes"]) <= set(new["notes"])


def test_AC08_既有top_products回归(no_llm):
    record = _ask("2011年11月卖得最好的5个产品")
    assert record["intent"]["intent"] == "top_products"
    assert record["status"] in ("ok", "degraded")
    direct = tools.top_products(_dt.date(2011, 11, 1), _dt.date(2011, 11, 30), top_n=5)
    assert record["facts"]["top_amount"] == direct["facts"]["top_amount"]


# ════════════════════════════════════════════════════════════════════════
# AC-09 · 商品趋势（逐商品聚合、桶规则与销售额趋势一致）
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("granularity", ["day", "week"])
def test_AC09_商品趋势逐桶与独立参考一致(granularity):
    start, end = MONTH
    codes = ("85123A", "10002", "22423")
    result = tools.product_analysis(
        _dt.date.fromisoformat(start), _dt.date.fromisoformat(end),
        operation="trend", product_codes=list(codes), granularity=granularity,
    )
    valid = _ref_valid(_ref_window(start, end))

    for product in result["series"]["products"]:
        block = valid.loc[valid["StockCode"].astype(str) == product["stock_code"]]
        if block.empty:
            assert product["points"] == []
            assert product["stock_code"] in result["facts"]["codes_missing"]
            continue
        # 独立复算：按同一套桶规则（day=当天；week=周一）求和，逐桶比对
        stamps = pd.to_datetime(block["InvoiceDate"])
        if granularity == "day":
            bucket = stamps.dt.normalize()
        else:
            offset = (stamps.dt.weekday - 0) % 7
            bucket = stamps.dt.normalize() - pd.to_timedelta(offset, unit="D")
        expected = (
            block.assign(_bucket=bucket).groupby("_bucket")["_ref_amount"].sum().sort_index()
        )
        assert [point["period_start"] for point in product["points"]] == [
            pd.Timestamp(key).date().isoformat() for key in expected.index
        ]
        for point, (key, amount) in zip(product["points"], expected.items()):
            assert point["amount"] == pytest.approx(float(amount), abs=1e-6)
    # 每个商品各自的逐桶求和 == 该商品合计（自检项）
    assert result["selfcheck"]["per_product_point_sum_matches_total"] is True
    assert all(
        item["bucket_count"] == len(next(
            product["points"] for product in result["series"]["products"]
            if product["stock_code"] == item["stock_code"]
        ))
        for item in result["items"]
    )


def test_AC09_不完整桶标记与销售额趋势同一规则():
    """同一个区间、同一个粒度：点上的 `partial` 必须与销售额趋势**逐桶一致**。

    这条是"复用同一段分桶代码"的回归证明 —— 两边各写一遍，早晚有一边忘了标 partial。
    """
    start, end = MONTH
    product = tools.product_analysis(
        _dt.date.fromisoformat(start), _dt.date.fromisoformat(end),
        operation="trend", product_codes=["22423"], granularity="week",
    )
    sales = tools.sales_trend(_dt.date.fromisoformat(start), _dt.date.fromisoformat(end), "week")

    for key in ("period_start", "period_end", "covered_start", "covered_end", "partial"):
        assert [point[key] for point in product["series"]["products"][0]["points"]] == [
            point[key] for point in sales["series"]["points"]
        ]
    # 首尾桶不完整这件事本身必须成立（区间没对齐周边界）
    assert any(point["partial"] for point in product["series"]["products"][0]["points"])


def test_AC09_趋势必须给商品编码否则明确报错():
    """参数层就把这件事钉死：operation=trend 不带编码 → 明确报错（不猜看哪个商品）。"""
    parsed = intent_module.ParsedIntent(
        intent="product_analysis",
        params={"start": "2011-11-01", "end": "2011-11-30", "operation": "trend"},
    )
    with pytest.raises(intent_module.IntentError) as excinfo:
        parsed.validated_params()
    assert "product_codes" in excinfo.value.message

    # 反过来也不许：非 trend 却带了编码 → 收口闸门把它清掉（并在 assumptions 里留痕）
    messy = intent_module.ParsedIntent(
        intent="product_analysis",
        params={"start": "2011-11-01", "end": "2011-11-30", "operation": "top",
                "product_codes": ["85123A"]},
    )
    fixed = intent_module.enforce_product_semantics(messy, "2011年11月卖得最好的产品")
    assert "product_codes" not in fixed.params
    assert any("清掉" in item for item in fixed.assumptions)
    fixed.validated_params()          # 清完就能过校验

    # trend 但问题里写了编码 → 闸门把它补进去（用户说了看哪个商品）
    rescued = intent_module.enforce_product_semantics(
        intent_module.ParsedIntent(
            intent="product_analysis",
            params={"start": "2011-11-01", "end": "2011-11-30", "operation": "trend"},
        ),
        "85123A 和 10002 在 2011 年 11 月的销售趋势",
    )
    assert rescued.params["product_codes"] == ["85123A", "10002"]


# ════════════════════════════════════════════════════════════════════════
# AC-10 · 退货分析：两个口径分开、统一指标必须去重
# ════════════════════════════════════════════════════════════════════════
def test_AC10_两个退货口径分开且与独立参考一致():
    start, end = MONTH
    result = tools.product_analysis(
        _dt.date.fromisoformat(start), _dt.date.fromisoformat(end), operation="return", top_n=5
    )
    facts = result["facts"]
    window = _ref_window(start, end)                      # **原始行**（退货行正是被排除的那些）
    negative = window["Quantity"] < 0
    cancel = window["InvoiceNo"].astype("string").str.startswith("C").fillna(False)

    assert facts["window_rows"] == len(window)
    assert facts["negative_quantity_rows"] == int(negative.sum())
    assert facts["cancel_invoice_rows"] == int(cancel.sum())
    assert facts["negative_quantity_rate"] == pytest.approx(int(negative.sum()) / len(window), abs=1e-12)
    assert facts["cancel_invoice_rate"] == pytest.approx(int(cancel.sum()) / len(window), abs=1e-12)
    # **两个口径不许合并**：回复里两个数都要在（前端 display 行）
    labels = [item["label"] for item in result["display"]]
    assert any("数量为负" in label for label in labels)
    assert any("取消单" in label for label in labels)


def test_AC10_统一指标去重并覆盖重叠样本():
    """统一指标必须是**真并集**：不能是 a+b。

    本数据集里所有 C 单同时都是负数量行 → 重叠样本真实存在（>0），
    这正好能验出"相加"与"去重"的差别：相加会多算正好一个 overlap。
    """
    start, end = MONTH
    result = tools.product_analysis(
        _dt.date.fromisoformat(start), _dt.date.fromisoformat(end), operation="return"
    )
    facts = result["facts"]
    window = _ref_window(start, end)
    negative = window["Quantity"] < 0
    cancel = window["InvoiceNo"].astype("string").str.startswith("C").fillna(False)

    assert facts["overlap_rows"] == int((negative & cancel).sum()) > 0        # 重叠样本非空
    assert facts["return_or_cancel_rows"] == int((negative | cancel).sum())   # 真并集
    assert facts["return_or_cancel_rows"] == (
        facts["negative_quantity_rows"] + facts["cancel_invoice_rows"] - facts["overlap_rows"]
    )
    assert facts["return_or_cancel_rows"] < (
        facts["negative_quantity_rows"] + facts["cancel_invoice_rows"]
    ), "统一指标若是相加，就会大于真并集 —— 这条断言专门盯住它"
    assert result["selfcheck"]["union_equals_dedup"] is True
    assert result["selfcheck"]["double_count_if_summed"] == facts["overlap_rows"]
    # 发票号级的统一指标也是去重的
    assert facts["return_or_cancel_invoices"] <= int(window["InvoiceNo"].nunique())


# ════════════════════════════════════════════════════════════════════════
# AC-11 · 不支持的客户维度（VIP 等）
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("question,hit", [
    ("VIP客户TOP10", "VIP"),
    ("vip 客户销售额排行", "VIP"),
    ("客户等级分布", "客户等级"),
    ("大客户排名前10", "大客户"),
    ("客户行业分布", "客户行业"),
    ("按客户地区看销售额", "客户地区"),
    ("客户渠道分析", "客户渠道"),
    ("客户生命周期阶段分布", "客户生命周期"),
])
def test_AC11_不支持的客户维度必须拒绝(question, hit, no_llm):
    record = _ask(question)
    assert record["status"] == "unsupported", question
    assert record["intent"]["intent"] == "unsupported"
    assert hit in record["intent"]["reason"]
    # 拒绝时**不许**给出任何客户榜单（"销售额 TOP"顶替 VIP TOP 就是最糟的答法）
    assert record["items"] is None
    assert record["facts"] is None


def test_AC11_销售额TOP10不会自动解释成VIP_TOP10(no_llm):
    """两句问法必须是两种结果：一句答客户排行，一句明确拒绝 —— 绝不互相顶替。"""
    normal = _ask("2011年11月销售额最高的10个客户")
    assert normal["intent"]["intent"] == "customer_analysis"
    assert len(normal["items"]) == 10

    vip = _ask("2011年11月VIP客户销售额最高的10个")
    assert vip["status"] == "unsupported"
    assert vip["items"] is None
    assert vip["intent"]["intent"] != "customer_analysis"


# ════════════════════════════════════════════════════════════════════════
# AC-12 · 区域边界回归（TASK-005 的拒绝行为不许被 TASK-006 改变）
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("question", [
    "华南区上个月卖了多少？", "华东区销售额最高的10个客户", "上海门店的客户排行",
    "按门店看客户复购率", "销售员张三的客户数", "各省份的退货率",
])
def test_AC12_区域门店销售员继续unsupported(question, no_llm):
    record = _ask(question)
    assert record["status"] == "unsupported", question
    assert record["intent"]["intent"] == "unsupported"
    assert record["facts"] is None


def test_AC12_国家维度仍然可用_两者不许混(no_llm):
    record = _ask("2011年11月各国家销售额TOP5")
    assert record["intent"]["intent"] == "sales_breakdown_by_country"
    assert record["status"] in ("ok", "degraded")


# ════════════════════════════════════════════════════════════════════════
# AC-13 / AC-14 / AC-15 · LLM 的边界
# ════════════════════════════════════════════════════════════════════════
def _llm_returns(intent_json: str, text: str, monkeypatch):
    """把模型出口换掉：解析那一步回 intent_json，组织语言那一步回 text。

    与 test_chat.py 的写法同源：解析 Prompt 里有"意图解析器"，组织语言的没有 ——
    用这个特征区分两次调用。
    """
    captured: dict[str, str] = {"facts_prompt": ""}

    def _chat(system: str, user: str, **kwargs):
        if "意图解析器" in system:
            return intent_json
        captured["facts_prompt"] = user
        return text

    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "chat", _chat)
    return captured


def test_AC13_LLM只拿到结构化事实不拿原始数据(monkeypatch):
    captured = _llm_returns(
        '{"intent":"customer_analysis","params":{"start":"2011-11-01","end":"2011-11-30",'
        '"operation":"top","metric":"sales_amount","top_n":10},"assumptions":[],"confidence":0.9}',
        "【为什么】\n客户集中度较高。\n【建议行动】\n- 维护头部客户",
        monkeypatch,
    )
    record = _ask("2011年11月销售额最高的10个客户", use_llm=True)
    assert record["tool"]["name"] == "customer_analysis"

    prompt = captured["facts_prompt"]
    assert prompt, "组织语言那一步必须真的被调用（否则这条测试没测到东西）"
    # 只喂了"代码写好的事实文本"：没有 DataFrame 结构、没有 SQL、没有全量客户明细
    assert "DataFrame" not in prompt and "dtype" not in prompt and "SELECT" not in prompt
    # 客户明细最多 = top_n（不是把 1664 个客户倒给模型）
    listed = [line for line in prompt.splitlines() if line.strip().startswith("#")]
    assert len(listed) <= 10
    assert len(prompt) < 8000


def test_AC14_TOP顺序来自代码而非模型(monkeypatch):
    """模型在【为什么】里断言"B 最高"也不能成为事实来源 —— 榜单仍是代码排的。"""
    intent_json = (
        '{"intent":"customer_analysis","params":{"start":"2011-11-01","end":"2011-11-30",'
        '"operation":"top","metric":"sales_amount","top_n":3},"assumptions":[],"confidence":0.9}'
    )
    _llm_returns(intent_json, "【为什么】\n其中客户 99999 是最高的。\n【建议行动】\n- 跟进", monkeypatch)
    record = _ask("2011年11月销售额最高的3个客户", use_llm=True)

    direct = tools.customer_analysis(
        _dt.date(2011, 11, 1), _dt.date(2011, 11, 30), operation="top", metric="sales_amount", top_n=3
    )
    assert [item["customer_id"] for item in record["items"]] == [
        item["customer_id"] for item in direct["items"]
    ]
    assert record["facts"]["best_customer_id"] == direct["facts"]["best_customer_id"]


def test_AC15_数字闸门对新intent同样生效(monkeypatch):
    intent_json = (
        '{"intent":"customer_analysis","params":{"start":"2011-11-01","end":"2011-11-30",'
        '"operation":"top","metric":"sales_amount","top_n":3},"assumptions":[],"confidence":0.9}'
    )
    _llm_returns(intent_json, "【为什么】\n因为头部客户贡献了 999999.99 元。\n【建议行动】\n- 加大投入", monkeypatch)
    record = _ask("2011年11月销售额最高的3个客户", use_llm=True)

    assert record["status"] == "degraded"
    guard = record["answer"]["guard"]
    assert guard["checked"] is True and guard["passed"] is False
    assert "999999.99" in guard["violations"]
    assert record["answer"]["sections"][1]["source"] == "code"    # 【为什么】被整段作废


# ════════════════════════════════════════════════════════════════════════
# 真 HTTP：能力清单 + 七类问题端到端
# ════════════════════════════════════════════════════════════════════════
def test_能力清单含两个新Intent且不新增多余项():
    body = client.get("/api/chat/capabilities").json()
    names = [item["name"] for item in body["intents"]]
    # ★ FR-008：地区分布只在**真的有带地区字段的数据源**时进清单；本用例库里没有 →
    #   清单 = COMPUTE_INTENTS 去掉那一个（其余顺序与项数照旧，TASK-006 的两个仍在末尾）。
    assert names == [
        name for name in intent_module.COMPUTE_INTENTS
        if name != intent_module.INTENT_SALES_BY_REGION
    ]
    assert names[-2:] == ["customer_analysis", "product_analysis"]
    assert len(names) == 7
    # 不支持的维度里必须点出 VIP 这类"不存在但很诱人"的客户维度
    dims = " ".join(body["unsupported"]["dimensions"])
    assert "VIP" in dims and "客户等级" in dims and "客户渠道" in dims
    assert "不会用「销售额 TOP」顶替「VIP TOP」" in body["unsupported"]["reason"]


@pytest.mark.parametrize("question,intent,operation", [
    ("2011年11月销售额最高的10个客户", "customer_analysis", "top"),
    ("2011年11月购买次数最多的客户", "customer_analysis", "purchase_frequency"),
    ("2011年11月的复购率", "customer_analysis", "repeat_rate"),
    ("2011年11月有多少新客", "customer_analysis", "new_customers"),
    ("2011年11月有多少沉睡客户（90天没买）", "customer_analysis", "inactive_customers"),
    ("2011年11月各商品的退货情况前10名", "product_analysis", "return"),
    ("85123A 在2011年11月的销售趋势", "product_analysis", "trend"),
])
def test_新能力的真HTTP端到端(question, intent, operation, no_llm):
    record = _ask(question, use_llm=True)          # 真 HTTP；LLM 未接 → 走降级路径
    assert record["status"] in ("ok", "degraded"), record["notice"]
    assert record["intent"]["intent"] == intent
    assert record["facts"]["operation"] == operation
    assert record["tool"]["name"] == intent
    assert record["answer"]["text"]
    # 记录必须落盘可查（刷新页面靠它恢复）
    got = client.get(f"/api/conversations/{record['conversation_id']}")
    assert got.status_code == 200
    assert got.json()["intent"]["intent"] == intent
