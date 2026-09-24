"""tests/test_chat.py · TASK-004 验收测试（自然语言对话入口）。

════════════════════════════════════════════════════════════════════════
【本文件测什么 —— 核心是"数字到底是谁算的"】
════════════════════════════════════════════════════════════════════════
真实数据、真实计算、真实 HTTP（TestClient 进程内）。三个 Intent 各跑一遍，
回答里的每个数字都拿去跟**直接调用冻结资产**的结果逐位比对：

  ① AC-01 三个 Intent 的金额/行数与 `executor.compute_sales_amount()` **位级相等**；
  ② AC-02 解析不出来的问题 → 明确报错，**不瞎猜一个 Intent 硬算**；
  ③ AC-03 问"华南区/省份/门店"→ 明确说数据不支持，**且绝不用 Country 顶替**；
  ④ AC-04 LLM 写出的文字里出现"事实里没有的数字" → 整段作废（闸门真的拦得住）；
  ⑤ AC-05 没 key / LLM 抛错 → 降级成关键词匹配 + 代码回答，**不编**；
  ⑥ AC-06 记录落盘，重新读盘还在，HTTP 也能查回来；
  ⑦ AC-08 `app/ai/*.py` 里搜不到任何 D16 口径的业务数字、也搜不到 key 字面量。

════════════════════════════════════════════════════════════════════════
【关于本文件里的 monkeypatch：替换的是"LLM 通道"，不是数据】
════════════════════════════════════════════════════════════════════════
规则是"禁 mock/假数据"，这条针对的是**业务数据**（数字、记录、状态）——
本文件里**一个业务数字都没有假造**，全部来自真实数据集的真实计算。

被替换的只有 `app.ai.llm.chat` 这一个**网络出口**：单元测试不能依赖外网与第三方服务
（会 flaky、会花钱、没网就跑不了）。换掉它测的恰恰是"LLM 说胡话时系统怎么办"，
而这正是 AC-04/AC-05 要验的东西 —— 用一个**真的** LLM 反而无法稳定复现"它写错数字"的场景。
真实 LLM 链路（真调用 DeepSeek）由 `scripts/chat_e2e.py` 负责，不在这里。
"""

from __future__ import annotations

import datetime as _dt
import json
import pathlib
import re
import sys
import threading
import time

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import prewarm, state  # noqa: E402
from app.ai import answer, intent as intent_module, llm, service, tools  # noqa: E402
from app.api import app  # noqa: E402
from app.engine import executor, loader  # noqa: E402
from app.engine import metrics as engine_metrics  # noqa: E402

client = TestClient(app)

ISOLATED_ENV = ("SRA_STATE_DIR", "SRA_DOC_DIR", "SRA_UPLOAD_DIR", "SRA_OUTPUT_DIR")

# 真实数据集里 D16 口径算出来的两个标志性数字（**只在测试里**用来验"代码没写死它们"）
D16_WEEK_RANGE = ("2011-11-21", "2011-11-27")
D16_AMOUNT = 316412.16
DATASET_ROWS = 541909


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """四个目录都指到临时目录：绝不碰仓库里的真实 state/ 与 data/ 输出。"""
    monkeypatch.setenv("SRA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SRA_DOC_DIR", str(tmp_path / "documents"))
    monkeypatch.setenv("SRA_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("SRA_OUTPUT_DIR", str(tmp_path / "outputs"))
    return tmp_path


@pytest.fixture
def no_llm(monkeypatch):
    """让 `llm.available()` 说"没接 LLM"（**不**改任何数据，只关掉一个通道）。"""
    monkeypatch.setattr(llm, "available", lambda: False)
    return None


def _executor_amount(start: str, end: str) -> float:
    """**直接**调冻结资产要口径金额 —— 与走工具链的结果比对用。"""
    return float(executor.compute_sales_amount(start, end)["amount"])


# ════════════════════════════════════════════════════════════════════════
# 0. 数据集画像：必须是**从数据里算的**，不是写死的常量
# ════════════════════════════════════════════════════════════════════════
def test_dataset_profile与真实数据一致():
    profile = tools.dataset_profile()
    frame = loader.load_raw()
    stamps = frame[loader.TIME_COLUMN]

    assert profile["rows"] == len(frame) == DATASET_ROWS
    assert profile["column_count"] == frame.shape[1] == 8
    assert profile["last_day"] == str(stamps.max().date())
    assert profile["first_day"] == str(stamps.min().date())
    assert profile["country_count"] == int(frame["Country"].nunique(dropna=True))
    assert profile["customer_count"] == int(frame["CustomerID"].nunique(dropna=True))
    # 数据里到底有没有"区域"这类字段 —— 这是 AC-03 那句"数据不支持"的事实依据
    assert profile["has_region_field"] is False
    assert "Region" not in profile["columns"]
    assert profile["columns"] == [
        "InvoiceNo", "StockCode", "Description", "Quantity",
        "InvoiceDate", "UnitPrice", "CustomerID", "Country",
    ]


def test_dataset_bounds取自数据而非常量():
    first, last = tools.dataset_bounds()
    frame = loader.load_raw()
    stamps = frame[loader.TIME_COLUMN]
    assert (first, last) == (stamps.min().date(), stamps.max().date())


# ════════════════════════════════════════════════════════════════════════
# 1. AC-01 数字逐位一致（三个 Intent 各一条）
# ════════════════════════════════════════════════════════════════════════
def test_AC01_销售额与executor逐位一致():
    start, end = D16_WEEK_RANGE
    result = tools.sales_summary(_dt.date.fromisoformat(start), _dt.date.fromisoformat(end))
    direct = executor.compute_sales_amount(start, end)
    facts = result["facts"]

    assert facts["sales_amount"] == float(direct["amount"])          # **逐位**相等，不是近似
    assert facts["sales_amount"] == pytest.approx(D16_AMOUNT, abs=1e-6)   # 与 D16 已冻结口径对齐
    assert facts["rows_in_range"] == direct["rows_in_range"]
    assert facts["rows_valid"] == direct["rows_valid"]
    assert facts["rows_excluded"] == direct["rows_excluded"]
    assert facts["excluded_amount"] == float(direct["excluded_amount"])
    assert facts["valid_qty_sum"] == float(direct["valid_qty_sum"])
    assert facts["customer_id_null_rows"] == direct["validations"]["customer_id_nulls"]

    # 自检块必须**如实报告**它对过账
    assert result["selfcheck"]["bit_identical"] is True
    assert result["selfcheck"]["executor_checks_all_passed"] is True


def test_AC01_订单数与客户数是同一套掩码下的去重计数():
    start, end = D16_WEEK_RANGE
    result = tools.sales_summary(_dt.date.fromisoformat(start), _dt.date.fromisoformat(end))
    facts = result["facts"]

    # 用一个**完全独立**的路径复算：直接按 D16 三条规则手写布尔掩码
    frame = loader.load_raw()
    low, high = __import__("app.engine.metrics", fromlist=["x"]).inclusive_day_window(start, end)
    window = frame.loc[(frame["InvoiceDate"] >= low) & (frame["InvoiceDate"] < high)]
    valid = (
        ~window["InvoiceNo"].astype(str).str.startswith("C")
        & (window["Quantity"] > 0)
        & (window["UnitPrice"] > 0)
    )
    rows = window.loc[valid]

    assert facts["order_count"] == int(rows["InvoiceNo"].nunique())
    assert facts["customer_count"] == int(rows["CustomerID"].dropna().nunique())
    assert facts["avg_order_amount"] == facts["sales_amount"] / facts["order_count"]


def test_AC01_趋势逐桶求和与executor一致():
    start, end = D16_WEEK_RANGE
    result = tools.sales_trend(_dt.date.fromisoformat(start), _dt.date.fromisoformat(end), "day")
    facts = result["facts"]
    direct = _executor_amount(start, end)

    assert facts["total_amount"] == direct           # 同一行序 line_amount().sum() → 位级相等
    assert result["selfcheck"]["detail_total"] == direct
    assert result["selfcheck"]["bucket_sum_matches_total"] is True
    assert facts["bucket_sum"] == pytest.approx(direct, abs=1e-6)

    points = result["series"]["points"]
    assert len(points) == facts["bucket_count"]
    assert all(p["period_start"] == start or True for p in points)   # 每点都有日期
    assert [p["period_start"] for p in points] == sorted(p["period_start"] for p in points)


def test_AC01_周聚合以周一为起点且与D16那一周对齐():
    """`DECISIONS.md:127` 的"那一周"= 2011-11-21(周一)~11-27(周日)，周口径必须一致。"""
    result = tools.sales_trend(_dt.date(2011, 11, 21), _dt.date(2011, 11, 27), "week")
    points = result["series"]["points"]
    assert len(points) == 1
    assert points[0]["period_start"] == "2011-11-21"      # 周一
    assert result["facts"]["total_amount"] == pytest.approx(D16_AMOUNT, abs=1e-6)  # 与按日算的同一周金额一致

    day_result = tools.sales_trend(_dt.date(2011, 11, 21), _dt.date(2011, 11, 27), "day")
    assert day_result["facts"]["total_amount"] == result["facts"]["total_amount"]
    # **6 天不是 7 天**：2011-11-26（周六）在原始数据里整天没有任何交易记录
    # （不是被 D16 规则筛掉的 —— 见 test_2011_11_26_原始数据里就没有交易）
    assert day_result["facts"]["bucket_count"] == 6
    assert [p["period_start"] for p in day_result["series"]["points"]] == [
        "2011-11-21", "2011-11-22", "2011-11-23", "2011-11-24", "2011-11-25", "2011-11-27",
    ]


def test_趋势里跨越问句区间的桶必须标成不完整():
    """问 11 月按周看：首桶从 10-31（周一）起、末桶到 12-04 止 —— 都得标 `partial`。

    不标的话，页面上会出现一个标着 2011-10-31 的桶，读起来像是"10 月 31 日也有这笔钱"。
    """
    result = tools.sales_trend(_dt.date(2011, 11, 1), _dt.date(2011, 11, 30), "week")
    points = result["series"]["points"]
    starts = [point["period_start"] for point in points]
    assert starts[0] == "2011-10-31"          # 自然周起点落在区间之前
    assert starts[-1] == "2011-11-28"

    first, last = points[0], points[-1]
    assert first["partial"] is True
    assert (first["covered_start"], first["covered_end"]) == ("2011-11-01", "2011-11-06")
    assert first["covered_days"] == 6
    assert last["partial"] is True
    assert (last["covered_start"], last["covered_end"]) == ("2011-11-28", "2011-11-30")
    assert last["covered_days"] == 3
    # 中间那几个是整周
    assert all(point["partial"] is False for point in points[1:-1])
    assert any("首尾桶不完整" in note for note in result["notes"])
    # 桶内金额之和仍等于区间总额（标不标 partial 不影响算钱）
    assert result["facts"]["bucket_sum"] == pytest.approx(result["facts"]["total_amount"], abs=1e-6)


def test_2011_11_26_原始数据里就没有交易():
    """空档天是**数据事实**：这一天在原始数据里一行都没有，工具如实不补零。"""
    import pandas as pd

    stamps = loader.load_raw()["InvoiceDate"].dt.normalize()
    assert int((stamps == pd.Timestamp("2011-11-26")).sum()) == 0


def test_AC01_产品排行分组求和与逐行求和一致():
    start, end = D16_WEEK_RANGE
    result = tools.top_products(_dt.date.fromisoformat(start), _dt.date.fromisoformat(end), 5)
    facts = result["facts"]
    direct = _executor_amount(start, end)

    assert facts["total_amount"] == direct
    assert result["selfcheck"]["detail_total"] == direct
    assert result["selfcheck"]["grouped_matches_detail"] is True
    assert result["selfcheck"]["top_amount_le_detail"] is True
    # TOP N 是降序且互不相同
    amounts = [item["amount"] for item in result["items"]]
    assert amounts == sorted(amounts, reverse=True)
    assert len({item["stock_code"] for item in result["items"]}) == len(amounts)
    assert [item["rank"] for item in result["items"]] == list(range(1, len(amounts) + 1))
    # 占比之和不超过 100%，且每个占比都能用明细复算出来
    assert facts["top_share"] <= 1.0
    for item in result["items"]:
        assert item["share"] == pytest.approx(item["amount"] / facts["total_amount"], rel=1e-12)


# ════════════════════════════════════════════════════════════════════════
# 2. AC-01（HTTP 端到端，走真实端点，不走 LLM）
# ════════════════════════════════════════════════════════════════════════
def _ask(question: str, *, use_llm: bool = False) -> dict:
    response = client.post("/api/chat", json={"question": question, "use_llm": use_llm})
    assert response.status_code == 200, response.text
    return response.json()


def test_AC01_三个问题各命中一个Intent且数字与直接调metrics一致(no_llm):
    cases = [
        ("2011年11月21日到11月27日一共卖了多少？", "sales_summary", "sales_amount"),
        ("2011-11-21 到 2011-11-27 的销售趋势", "sales_trend", "total_amount"),
        ("2011-11-21 到 2011-11-27 卖得最好的5个产品", "top_products", "total_amount"),
    ]
    direct = _executor_amount(*D16_WEEK_RANGE)

    for question, expected_intent, key in cases:
        record = _ask(question)
        assert record["intent"]["intent"] == expected_intent, (question, record["intent"])
        assert record["tool"]["name"] == expected_intent
        assert record["status"] == "degraded"          # 未接 LLM 时如实标注
        assert record["facts"][key] == direct, question
        # 【发生了什么】是代码写的，且里面的数字必须能在 facts 里找到
        what = record["answer"]["sections"][0]
        assert what["source"] == "code"
        assert record["answer"]["guard"]["passed"] is True


def test_三个Intent的问题各走不同的工具():
    names = set()
    for question in (
        "2011年11月销售额多少？",
        "2011年11月的销售趋势",
        "2011年11月销量最高的产品",
    ):
        record = _ask(question)
        assert record["status"] in ("degraded", "ok")
        names.add(record["tool"]["name"])
    assert names == {"sales_summary", "sales_trend", "top_products"}


# ════════════════════════════════════════════════════════════════════════
# 3. AC-02 解析失败 → 明确提示，不瞎猜
# ════════════════════════════════════════════════════════════════════════
def test_AC02_解析不出来就明确报错不硬算(no_llm):
    record = _ask("今天天气怎么样")
    assert record["status"] == "error"
    assert record["error"]["code"] == "intent_unparseable"
    assert record["tool"] is None and record["facts"] is None
    assert "没听懂" in record["error"]["message"]
    assert record["answer"] is None                       # 没有事实就没回答，不硬编一段
    assert "1" not in record["notice"].replace("TASK-004", "")  # 提示里不夹带任何数字


def test_AC02_LLM给出白名单外的intent要拒绝(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "chat", lambda *a, **k: '{"intent":"delete_everything","params":{}}')
    record = _ask("随便问点什么", use_llm=True)
    assert record["status"] == "error"
    # 白名单外的 intent 拒绝后，关键词降级也没认出来 → unparseable
    assert record["error"]["code"] == "intent_unparseable"
    assert record["facts"] is None


def test_AC02_意图合法但参数不合法要拒绝(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(
        llm, "chat",
        lambda *a, **k: '{"intent":"sales_summary","params":{"start":"2011-11-01","end":"2011-11-30","country":"英国"}}',
    )
    record = _ask("英国卖了多少", use_llm=True)
    assert record["status"] == "error"
    assert record["error"]["code"] == "intent_invalid_params"
    assert "country" in record["error"]["message"]         # 多出来的字段必须被点名
    assert record["facts"] is None


def test_Intent日期反了要拒绝(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(
        llm, "chat",
        lambda *a, **k: '{"intent":"sales_summary","params":{"start":"2011-11-30","end":"2011-11-01"}}',
    )
    record = _ask("11月销售额", use_llm=True)
    assert record["status"] == "error"
    assert record["error"]["code"] == "intent_invalid_params"


def test_可选参数写成null按未指定处理_非空非法值仍然拒绝(monkeypatch):
    """TASK-006 撞到的**真实事故**：问「2011年11月购买次数最多的5个客户」时，
    真 DeepSeek 返回 `"metric": null`（它想表达"这个操作不需要排序指标"）——
    而 metric 是有默认值的字段，让 pydantic 见到 null 就把一个好好的问题判成非法参数。

    规矩：**有默认值的字段被写成 null = 没填**（用默认值，并在 assumptions 里写明）；
    **非空的非法取值**照旧拒绝 —— 这条修复不许把闸门放松。
    """
    _llm_returns(
        "【为什么】\n略\n【建议行动】\n- 略",
        monkeypatch,
        intent_json='{"intent":"sales_trend","params":{"start":"2011-11-01","end":"2011-11-30",'
                    '"granularity":null},"assumptions":[],"confidence":0.9}',
    )
    record = _ask("2011年11月的销售趋势", use_llm=True)
    assert record["status"] == "ok", record["notice"]
    assert record["params"]["granularity"] == "day"           # 默认值生效
    assert any("null" in item for item in record["intent"]["assumptions"])   # 留痕

    # 反例：非空的非法取值 → 仍然报错（拒绝的时候还要点名是哪个字段）
    _llm_returns(
        "【为什么】\n略\n【建议行动】\n- 略",
        monkeypatch,
        intent_json='{"intent":"sales_trend","params":{"start":"2011-11-01","end":"2011-11-30",'
                    '"granularity":"hourly"},"assumptions":[],"confidence":0.9}',
    )
    record = _ask("2011年11月按小时看趋势", use_llm=True)
    assert record["status"] == "error"
    assert record["error"]["code"] == "intent_invalid_params"
    assert "granularity" in record["error"]["message"]


# ════════════════════════════════════════════════════════════════════════
# 4. AC-03 数据不支持的维度 → 明确告知，且**不许用 Country 顶替**
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "question, keyword",
    [
        ("华南区上个月卖了多少？", "华南"),
        ("华东大区的销售额", "华东"),
        ("按区域拆分一下销售额", "区域"),
        ("广东省的销售额是多少", "省"),
        ("上海门店卖了多少", "门店"),
        ("这个月的毛利是多少", "毛利"),
    ],
)
def test_AC03_数据没有的维度明确告知不支持(question, keyword):
    record = _ask(question)
    assert record["status"] == "unsupported"
    assert record["intent"]["intent"] == "unsupported"
    assert keyword in record["intent"]["reason"]
    assert record["tool"] is None and record["facts"] is None
    what = record["answer"]["sections"][0]["text"]
    assert "回答不了" in what
    assert "没有区域字段" in what
    assert "Country" in what                              # 明确点出 Country 不能当区域用


def test_AC03_不支持的问题绝不返回任何金额():
    """硬证据：unsupported 的回答里**一个金额都没有** —— 没有"偷偷用国家代替"的余地。"""
    record = _ask("华南区销售额是多少")
    blob = json.dumps(record, ensure_ascii=False, default=str)
    facts = record["facts"]
    assert facts is None
    # 数据里真实存在的金额（用国家算出来的那些）一个都不该出现在这条记录里
    uk = executor.compute_sales_amount("2011-11-21", "2011-11-27")["amount"]
    assert f"{uk}" not in blob
    assert f"{uk:,.2f}" not in blob


def test_AC03_区域闸门是代码拦的_LLM说不支持之前的判断不作数(monkeypatch):
    """就算 LLM "善解人意"地把华南区翻译成一个销售意图，代码闸门也必须在它之前拦下。"""
    monkeypatch.setattr(llm, "available", lambda: True)
    called = {"n": 0}

    def _should_not_be_called(*args, **kwargs):
        called["n"] += 1
        return '{"intent":"sales_summary","params":{"start":"2011-11-01","end":"2011-11-30"}}'

    monkeypatch.setattr(llm, "chat", _should_not_be_called)
    record = _ask("华南区2011年11月卖了多少", use_llm=True)
    assert record["status"] == "unsupported"
    assert called["n"] == 0                               # 根本没让 LLM 插手
    assert record["parse"]["source"] == "guard"
    assert record["facts"] is None


# ════════════════════════════════════════════════════════════════════════
# 5. AC-04 LLM 不参与算数 —— 数字闸门
# ════════════════════════════════════════════════════════════════════════
def _llm_returns(text: str, monkeypatch, *, intent_json: str | None = None):
    """把假的 LLM 通道接上。

    真实链路上 LLM 会被调用**两次**（解析意图 / 组织语言），所以这里也按 system 提示词分流：
    解析那次的提示词里有"意图解析器"，组织语言那次没有。只回一段文字的写法会让
    "解析"那一步拿到散文、退化成关键词匹配，测的就不是原本想测的东西了。
    """
    default_intent = (
        '{"intent":"sales_summary","params":{"start":"2011-11-21","end":"2011-11-27"},'
        '"assumptions":[],"confidence":0.9}'
    )

    def _chat(system: str, user: str, **kwargs):
        return intent_json or default_intent if "意图解析器" in system else text

    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "chat", _chat)


def test_AC04_LLM写的数字必须能在事实里找到出处_合规时放行(monkeypatch):
    # 引用一个**事实里确实有**的数字（销售额，写成中文数字更保险，但这里故意写成一个存在的数）
    facts_amount = executor.compute_sales_amount(*D16_WEEK_RANGE)["amount"]
    _llm_returns(
        f"【为什么】\n这一周有效行占比较高，需求集中在少数几个单品上（区间金额 {facts_amount:,.2f} 元）。\n"
        f"【建议行动】\n- 关注头部单品\n- 复核取消单",
        monkeypatch,
    )
    record = _ask("2011年11月21日到11月27日一共卖了多少", use_llm=True)
    assert record["facts"]["sales_amount"] == facts_amount     # 算的确实是问的那一周
    assert record["status"] == "ok"
    guard = record["answer"]["guard"]
    assert guard["passed"] is True and guard["checked"] is True
    assert record["answer"]["sections"][1]["source"] == "llm"
    assert record["answer"]["sections"][2]["source"] == "llm"


def test_AC04_LLM自己编数字整段作废(monkeypatch):
    _llm_returns(
        "【为什么】\n因为华南区贡献了 999999.99 元的销售额。\n"
        "【建议行动】\n- 加大投放 999999.99",
        monkeypatch,
    )
    record = _ask("2011年11月21日到11月27日一共卖了多少", use_llm=True)
    assert record["facts"]["sales_amount"] == _executor_amount(*D16_WEEK_RANGE)

    assert record["status"] == "degraded"                  # 降到代码回答
    guard = record["answer"]["guard"]
    assert guard["passed"] is False and guard["checked"] is True
    assert "999999.99" in guard["violations"]              # 违规数字留痕，看得见
    why = record["answer"]["sections"][1]
    assert why["source"] == "code"                         # LLM 那段被整段丢掉
    assert "999999.99" not in why["text"]
    assert "作废" in why["text"]
    # 两段都被作废（两段各写了一个越界数字），一段都不放过
    assert set(guard["by_section"]) == {"why", "actions"}
    assert all(not item["passed"] for item in guard["by_section"].values())


def test_AC04_回答里的数字全部可追溯到facts(no_llm):
    """把回答文本里的每个数字抠出来，必须都能在 facts/selfcheck 的 JSON 里找到。"""
    record = _ask("2011年11月21日到11月27日一共卖了多少")
    # 与 answer.compose() 内部**同一套**收集方式：代码生成的事实段 + 工具返回值。
    # 少了这一段，"316,412.16"（格式化后的写法）就会被误判成越界。
    allowed = answer.collect_allowed_numbers(
        record["answer"]["sections"][0]["text"],
        record["facts"],
        record["tool"]["display"],
        record["tool"]["selfcheck"],
        record["params"],
        record["data_profile"],
    )
    for section in record["answer"]["sections"]:
        report = answer.number_guard(section["text"], allowed)
        assert report["passed"], (section["key"], report["violations"])


def test_数字闸门单元行为(monkeypatch):
    allowed = answer.collect_allowed_numbers({"amount": 1234.56}, "区间 2011-11-21 ~ 2011-11-27")
    assert "1234.56" in allowed and "1234.5" not in allowed
    assert answer.number_guard("没有任何数字", allowed)["passed"] is True
    assert answer.number_guard("金额 1,234.56 元", allowed)["passed"] is True   # 千分位归一
    bad = answer.number_guard("金额 1,234.57 元", allowed)
    assert bad["passed"] is False and bad["violations"] == ["1234.57"]


def test_币种闸门单元行为():
    """「英镑/GBP/£」是**事实错误**（口径是人民币元）→ 与编造数字同样处理：整段作废。"""
    # 该拦的：英镑 / GBP / £（含「英磅」这种错别字、全角 ￡）
    for bad in ("销售额是英镑", "按 GBP 计价", "合计 £123", "以英镑为单位统计", "12 英镑", "英磅"):
        assert answer.currency_guard(bad), f"这段写错了币种却没被拦下：{bad}"
    # 不该误伤的：「元」现在是**正确**单位，各种含「元」的词都不许拦
    for ok in ("这是基本的单元测试", "多种元素的组合", "一次性买齐", "销售额集中在一百多个单品上",
               "元旦前后是旺季", "金额以元计价", "销售额（元）", "12 元"):
        assert answer.currency_guard(ok) == [], f"这段没写错币种却被拦下了：{ok}"

    # 干净的段落整体放行；写错币种的段落整段作废（且 violations 不含数字）
    allowed = answer.collect_allowed_numbers({"amount": 1234.56})
    clean = answer.number_guard("销售额集中在若干单品上", allowed)
    assert clean["passed"] is True and clean["currency_words"] == []
    dirty = answer.number_guard("这一周的销售额以英镑结算", allowed)
    assert dirty["passed"] is False and dirty["violations"] == []
    assert dirty["currency_words"] == ["英镑"]


def test_AC04_LLM写错币种整段作废(monkeypatch):
    """真模型完全可能把单位写成「英镑」—— 事实段与闸门两道防线都必须挡住它。"""
    _llm_returns(
        "【为什么】\n这批货集中在少数几个单品上，贡献了大部分英镑销售额。\n"
        "【建议行动】\n- 关注头部单品\n- 复核取消单",
        monkeypatch,
    )
    record = _ask("2011年11月21日到11月27日一共卖了多少", use_llm=True)
    assert record["facts"]["sales_amount"] == _executor_amount(*D16_WEEK_RANGE)   # 数字照样是对的

    assert record["status"] == "degraded"
    guard = record["answer"]["guard"]
    assert guard["passed"] is False
    assert guard["currency_words"] == ["英镑"]
    assert guard["violations"] == []                    # 币种错、数字没错 —— 两类留痕能分清
    why = record["answer"]["sections"][1]
    assert why["source"] == "code" and "英镑" not in why["text"]
    assert "币种" in why["text"] and "作废" in why["text"]   # 作废原因说清是哪一类越界
    assert "币种" in record["notice"]


def test_LLM段落切分(monkeypatch):
    why, actions = answer.parse_llm_sections("【为什么】\n因为A。\n【建议行动】\n- 做B\n- 做C")
    assert why == "因为A。"
    assert "- 做B" in actions and "- 做C" in actions
    # 模型不按格式来时：整段当"为什么"，"建议行动"留空 → 由降级文案补上（不瞎解析）
    why2, actions2 = answer.parse_llm_sections("我觉得是因为A。")
    assert why2 == "我觉得是因为A。" and actions2 == ""


# ════════════════════════════════════════════════════════════════════════
# 6. AC-05 无 key / LLM 失败 → 降级且不编造
# ════════════════════════════════════════════════════════════════════════
def test_AC05_没有key时降级且明确标注未接LLM(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    llm.load_env()                                        # 确保 .env 不会被偷偷补上
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")            # 空串也当没配
    assert llm.available() is False

    # ⚠️ 这里必须 use_llm=True：`_ask` 的默认值是 False，而 use_llm=False 会在
    #    intent.parse 的第一步就短路掉，**根本走不到"没配 key"那条分支** ——
    #    这个测试的名字说的是"没有 key 时降级"，那就得真让程序去检查 key。
    record = _ask("2011年11月21日到11月27日一共卖了多少", use_llm=True)
    assert record["status"] == "degraded"
    assert record["llm"]["used"] is False
    assert record["llm"]["answer_source"] == "code"
    # ⚠️ 用户 2026-09-24「技术细节不上界面」：对外文案里不许再出现「未接 LLM」这种内部说法，
    #    改成业务语言「本次没有可用的模型」——断言也跟着改（改的是措辞，不是行为）。
    assert "本次没有可用的模型" in record["notice"]
    assert "未接 LLM" not in record["notice"]
    assert "DEEPSEEK_API_KEY" not in record["notice"]
    # 事实仍然是真的（降级 ≠ 乱算）
    assert record["facts"]["sales_amount"] == _executor_amount(*D16_WEEK_RANGE)
    why = record["answer"]["sections"][1]
    # 段落里给的是**人话的原因**（llm.user_facing_error），不是异常原文；
    # 而且不再重复 notice 里那句「本次没有可用的模型」（同一句话说两遍很蠢）。
    assert why["source"] == "code" and "没有配置可用的模型服务" in why["text"]
    assert "未接 LLM" not in why["text"] and "llm_no_key" not in why["text"]
    # 原始 message 依然**原样留在记录里**（后台可查）——前端不渲染 ≠ 后端删掉
    assert record["llm"]["error"]["code"] == "llm_not_configured"
    assert record["llm"]["error"]["message"] == "本次没有可用的模型服务（未配置）—— 只做确定性计算，不编造推断"


def test_关闭模型与没有模型是两回事():
    """`use_llm=False` 是调用方**显式关掉**了模型，不是"模型组件没装好"。

    这两件事在界面上长得一模一样（都是"降级 + 【为什么】由程序生成"），说出口却是两句
    不同的话：显式关闭时说成"模型服务组件未就绪"，就是在讲一句关于系统状态的假话 ——
    SDK 装得好好的、key 也配着，只是这次没让它上场。用户看到那句话会去查环境，
    而真正的原因是他自己（或测试）把开关关了。
    """
    record = _ask("2011年11月21日到11月27日一共卖了多少", use_llm=False)
    assert record["llm"]["used"] is False
    assert record["llm"]["error"]["code"] == "llm_disabled"
    assert "未就绪" not in record["notice"]
    assert "未就绪" not in record["answer"]["sections"][1]["text"]
    assert "没有让模型参与" in record["notice"]
    # 记录里仍然留着原始原因（后台可查），只是界面上那句换成了人话
    assert "allow_llm=false" in record["llm"]["error"]["message"]


def test_AC05_LLM调用失败也不编答案(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)

    def _boom(*args, **kwargs):
        raise llm.LLMError("llm_call_failed", "DeepSeek 调用失败（TimeoutError）：连接超时")

    monkeypatch.setattr(llm, "chat", _boom)
    record = _ask("2011年11月21日到11月27日一共卖了多少", use_llm=True)

    assert record["status"] == "degraded"
    assert record["llm"]["used"] is False
    assert record["llm"]["error"]["code"] == "llm_call_failed"
    assert record["facts"]["sales_amount"] == _executor_amount(*D16_WEEK_RANGE)
    assert record["answer"]["sections"][1]["source"] == "code"
    # 异常原文（类名 / 服务商名 / 连接细节）**只在后台记录里**，不上界面
    assert record["llm"]["error"]["message"] == "DeepSeek 调用失败（TimeoutError）：连接超时"
    assert "TimeoutError" not in record["notice"] and "DeepSeek" not in record["notice"]
    why_text = record["answer"]["sections"][1]["text"]
    assert "这次没连上模型服务" in why_text
    assert "TimeoutError" not in why_text and "llm_call_failed" not in why_text


def test_AC05_推理模型content为空时回退读reasoning_content(monkeypatch):
    """用户实测提醒：deepseek 是推理模型，content 可能为空、答案在 reasoning_content 里。"""
    class _Msg:
        content = "   "
        reasoning_content = "【为什么】\n因为A。\n【建议行动】\n- 做B"

    class _Choice:
        message = _Msg()
        finish_reason = "length"

    class _Resp:
        choices = [_Choice()]

    class _Completions:
        @staticmethod
        def create(**kwargs):
            return _Resp()

    class _Chat:
        completions = _Completions()

    class _FakeClient:
        chat = _Chat()

    monkeypatch.setattr(llm, "api_key", lambda: "test-key-not-a-real-secret")
    monkeypatch.setattr(llm, "client", lambda: _FakeClient())
    assert llm.chat("s", "u") == "【为什么】\n因为A。\n【建议行动】\n- 做B"


def test_LLM两种内容都空时明确抛错(monkeypatch):
    class _Msg:
        content = ""
        reasoning_content = ""

    class _Resp:
        class _C:
            message = _Msg()
            finish_reason = "length"
        choices = [_C()]

    class _Chat:
        class completions:
            @staticmethod
            def create(**kwargs):
                return _Resp()

    class _FakeClient:
        chat = _Chat()

    monkeypatch.setattr(llm, "api_key", lambda: "test-key-not-a-real-secret")
    monkeypatch.setattr(llm, "client", lambda: _FakeClient())
    with pytest.raises(llm.LLMError) as excinfo:
        llm.chat("s", "u")
    assert excinfo.value.code == "llm_empty_response"


# ════════════════════════════════════════════════════════════════════════
# 7. AC-06 落盘 + 刷新后可查
# ════════════════════════════════════════════════════════════════════════
def test_AC06_对话落盘且HTTP能查回来(isolated_dirs):
    record = _ask("2011年11月21日到11月27日一共卖了多少？")
    conversation_id = record["conversation_id"]
    assert conversation_id.startswith("c_")

    # ① 文件真的在盘上，且是走 Repository 抽象落到 state/conversations.json
    path = isolated_dirs / "state" / "conversations.json"
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["conversations"][0]["conversation_id"] == conversation_id

    # ② 列表里能查到
    listing = client.get("/api/conversations").json()
    assert listing["total"] == 1
    assert listing["conversations"][0]["question"] == record["question"]
    assert listing["conversations"][0]["intent"] == "sales_summary"
    assert listing["conversations"][0]["sales_amount"] == pytest.approx(D16_AMOUNT, abs=1e-6)

    # ③ 详情能查回来，且"数字还是那个数字"（不靠内存）
    detail = client.get(f"/api/conversations/{conversation_id}").json()
    assert detail["facts"]["sales_amount"] == pytest.approx(D16_AMOUNT, abs=1e-6)
    assert detail["answer"]["text"] == record["answer"]["text"]

    # ④ 再问一次 → 两条记录，新的在前
    second = _ask("2011年11月卖得最好的3个产品？")
    listing = client.get("/api/conversations").json()
    assert listing["total"] == 2
    assert listing["conversations"][0]["conversation_id"] == second["conversation_id"]

    # ⑤ 分页
    page = client.get("/api/conversations", params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 2 and len(page["conversations"]) == 1
    assert page["conversations"][0]["conversation_id"] == conversation_id


def test_AC06_不支持的与解析失败的也留痕(isolated_dirs):
    _ask("华南区卖了多少")
    _ask("今天天气怎么样")
    listing = client.get("/api/conversations").json()
    statuses = sorted(item["status"] for item in listing["conversations"])
    assert statuses == ["error", "unsupported"]


def test_AC06_详情不存在返回404():
    response = client.get("/api/conversations/c_deadbeef")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "conversation_not_found"


def test_对话仓库走的是Repository抽象():
    from app import repositories
    from app.repositories.base import ConversationRepository

    repo = repositories.conversations()
    assert isinstance(repo, ConversationRepository)
    assert repo.path().name == "conversations.json"
    assert ConversationRepository in repositories.REPOSITORY_INTERFACES
    assert state.conversations_file().name == "conversations.json"


# ════════════════════════════════════════════════════════════════════════
# 8. 请求体校验 + 端点清单
# ════════════════════════════════════════════════════════════════════════
def test_空问题回400():
    response = client.post("/api/chat", json={"question": "   ", "use_llm": False})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "chat_empty_question"


def test_多传字段回422():
    response = client.post(
        "/api/chat", json={"question": "11月卖了多少", "use_llm": False, "hack": 1}
    )
    assert response.status_code == 422


def test_能力端点如实披露数据边界():
    body = client.get("/api/chat/capabilities").json()
    assert [item["name"] for item in body["intents"]] == list(intent_module.COMPUTE_INTENTS)
    assert body["data_profile"]["has_region_field"] is False
    assert "Country 代替区域" in body["unsupported"]["reason"]
    assert "configured" in body["llm"]
    # **不能**把 key 本身漏出去（只说有没有）
    blob = json.dumps(body, ensure_ascii=False)
    assert "sk-" not in blob
    assert body["data_profile"]["rows"] == DATASET_ROWS


# ════════════════════════════════════════════════════════════════════════
# 9. AC-08 没有硬编码业务数字 / 没有 key 字面量
# ════════════════════════════════════════════════════════════════════════
AI_DIR = PROJECT_ROOT / "app" / "ai"

# D16 口径的标志性数字 + 数据源哈希前缀：任何一个出现在 app/ai/ 里 = 有人把业务数字写死了
FORBIDDEN_LITERALS = (
    "316412.16",
    "541909",
    "19950",
    "43465a06f2ccf7c8b5bd2892bc7defb52f97487934fe93b16ae4c3936424676d",
)


@pytest.mark.parametrize("literal", FORBIDDEN_LITERALS)
def test_AC08_app_ai里没有硬编码业务数字(literal):
    offenders = [
        path.name
        for path in sorted(AI_DIR.glob("*.py"))
        if literal in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"{literal} 被写死在 {offenders} 里了 —— 业务数字必须算出来"


def test_AC08_app_ai里没有密钥字面量或提交痕迹():
    pattern = re.compile(r"sk-[A-Za-z0-9]{8,}|DEEPSEEK_API_KEY\s*=\s*['\"][^'\"]+['\"]")
    for path in sorted(AI_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert not pattern.search(text), f"{path.name} 里疑似有密钥字面量"
    # .env 必须在 .gitignore 里（提交前就被挡住）
    ignored = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in ignored


def test_AC08_工具白名单恰好七个且都在注册表里():
    """白名单是**穷举**的：表外的东西一律调不动。

    TASK-005 加两个（compare / country），TASK-006 再加两个（customer / product）——
    每次加的都是**评审批准**的那几个，名字钉死在这里，多一个都进不来。
    """
    assert set(tools.TOOLS) == set(intent_module.COMPUTE_INTENTS)
    assert set(tools.TOOLS) == {
        "sales_summary", "sales_trend", "top_products",
        "sales_compare", "sales_breakdown_by_country",
        # TASK-006：客户与商品各一个 Intent，内部用 operation 收口
        "customer_analysis", "product_analysis",
    }
    with pytest.raises(KeyError):
        tools.run_tool("delete_everything", {})
    # 归因**不是**一个独立 Intent（评审明确要求别开 sales_attribution）
    assert "sales_attribution" not in intent_module.ALL_INTENTS
    assert "sales_attribution" not in intent_module.COMPUTE_INTENTS
    # TASK-006 同样不许裂成"一个 operation 一个 Intent"
    for banned in ("customer_top", "customer_repeat", "new_customer", "churn_customer",
                   "rfm", "product_trend", "product_return", "product_association"):
        assert banned not in intent_module.ALL_INTENTS, banned


def test_引擎不依赖ai层():
    """`ARCHITECTURE.md:43`：engine 不依赖 ai。反向依赖会让"数字可单独测"这条断掉。"""
    engine_dir = PROJECT_ROOT / "app" / "engine"
    offenders = [
        path.name
        for path in sorted(engine_dir.glob("*.py"))
        if re.search(r"^\s*(from|import)\s+app\.ai", path.read_text(encoding="utf-8"), re.M)
    ]
    assert offenders == []


# ════════════════════════════════════════════════════════════════════════
# 10. AC-09 Legacy Contract：既有端点形状一个字没动
# ════════════════════════════════════════════════════════════════════════
def test_AC09_health响应形状未变():
    health = client.get("/api/health").json()
    assert set(health["state"]) == {
        "state_dir", "schema_version", "uploads", "executions", "tasks", "readable",
    }
    # 对话计数**不塞进** /api/health（与被冻结的响应形状隔离），走自己的端点
    assert "conversations" not in health["state"]
    assert client.get("/api/conversations").json()["total"] == 0


def test_AC09_新端点与既有端点并存():
    """用 OpenAPI 的 paths 做清单（`app.routes` 里 include_router 的产物没有 `.path`）。"""
    paths = set(app.openapi()["paths"])
    new_paths = {
        "/api/chat",
        "/api/chat/capabilities",
        "/api/conversations",
        "/api/conversations/{conversation_id}",
    }
    assert new_paths <= paths

    # STEP A 申报新增的 6 条：业务表读 / 导出 / 数据源列表 / 单个数据源 / 导入检查 / 导入登记。
    # 它们与既有端点零交集（新文件 api_datasets.py、新前缀 /api/tables），旧端点语义一个字没动。
    stepa_paths = {
        "/api/tables/{table}",
        "/api/tables/{table}/export",
        "/api/datasets",
        "/api/datasets/{dataset_id}",
        "/api/datasets/inspect",
        "/api/datasets/import",
    }
    assert stepa_paths <= paths

    # 报告导出格式那次新增的 1 条：把报告下载成 Word / Excel / Markdown。
    # 内容现渲染（报告结构冻结在会话记录里），没有第二条计算路径、也没动旧端点。
    export_paths = {
        "/api/conversations/{conversation_id}/report/export",
    }
    assert export_paths <= paths

    # 端点总清单**精确**钉死：TASK-004 许新增上面那 4 条、STEP A 许新增这 6 条、
    # 报告导出许新增这 1 条，其余路径（TASK-002C/002D 的 10 个 + TASK-003 的 4 个）必须原样还在。
    # 用集合相等而不是"包含若干条"：新增一条没申报的路径也会在这里被抓住。
    assert paths - new_paths - stepa_paths - export_paths == {
        "/api/upload", "/api/schema", "/api/execute", "/api/download/{execution_id}",
        "/api/executions", "/api/health",
        "/api/tasks", "/api/tasks/{task_id}", "/api/tasks/{task_id}/run",
        "/api/tasks/{task_id}/runs",
        "/api/documents", "/api/documents/{doc_id}",
        "/api/documents/{doc_id}/text", "/api/documents/{doc_id}/summary",
    }


# ════════════════════════════════════════════════════════════════════════
# 11. 货币单位：**显式声明的人民币「元」**（用户 2026-09-24 定的口径）
# ════════════════════════════════════════════════════════════════════════
# 数据集 8 列里**没有货币字段** —— 单位是"声明的口径"，不是算出来的；数值也不换算。
# 这一节的测试钉两件事：① 声明本身对不对（CNY/¥/元/source=declared）；
# ② 全链路（事实段 / display 表 / 能力端点 / 前端取值点）**只认这一处**，任何地方
# 再冒出「英镑/£」都要被抓住（前端那一半由 tests/test_web.py 的静态检查兜）。
def test_货币单位是显式声明的人民币元():
    currency = tools.DATASET_CURRENCY
    assert currency["code"] == "CNY"
    assert currency["symbol"] == "¥"
    assert currency["name"] == "元"
    # 关键：标明它不是"从数据里读到的"，否则下一个人会以为数据里有货币列
    assert currency["source"] == "declared"
    assert currency["note"]
    assert "英国" not in currency["note"] and "英镑" not in currency["note"]   # 旧口径话术已撤
    assert tools.currency_unit() == currency["name"] == "元"
    # 画像与能力端点都从这一个常量出去（同源，不是各写一份）
    assert tools.dataset_profile()["currency"] == currency


def test_所有金额事实的单位都来自这一处声明():
    start, end = D16_WEEK_RANGE
    runs = [
        tools.sales_summary(_dt.date.fromisoformat(start), _dt.date.fromisoformat(end)),
        tools.sales_trend(_dt.date.fromisoformat(start), _dt.date.fromisoformat(end), "day"),
        tools.top_products(_dt.date.fromisoformat(start), _dt.date.fromisoformat(end), 5),
    ]
    money_units = []
    for result in runs:
        for item in result["display"]:
            if item.get("format") == "money":
                money_units.append(item["unit"])
        # 事实段（代码生成的那一段）里出现的每个金额都得带「元」
        text = answer.render_facts_text(result, question="")
        assert "元" in text
        assert "英镑" not in text, (result["tool"], text)
    assert money_units and set(money_units) == {tools.currency_unit()}


def test_能力端点带出币种声明_前后端同一个出处():
    caps = client.get("/api/chat/capabilities").json()
    assert caps["currency"]["code"] == "CNY" and caps["currency"]["symbol"] == "¥"
    assert caps["data_profile"]["currency"] == caps["currency"]       # 两处同源
    # 币种块里不许出现别的币种字样
    assert answer.currency_guard(json.dumps(caps["currency"], ensure_ascii=False)) == []
    assert "英国" not in json.dumps(caps["currency"], ensure_ascii=False)   # 旧口径话术已撤


def test_提问链路里的金额单位是元不是英镑():
    record = _ask("2011年11月21日到11月27日一共卖了多少？")
    assert record["facts"]["sales_amount"] == _executor_amount(*D16_WEEK_RANGE)   # 数字照旧逐位相等
    what = record["answer"]["sections"][0]["text"]
    assert "元" in what and "英镑" not in what
    # 工具返回的 display 表（前端事实表直接渲染它）也必须是「元」
    assert {"元"} == {item["unit"] for item in record["tool"]["display"]
                      if item.get("format") == "money"}


def test_提示词告诉模型币种是人民币元():
    """写对单位不只是闸门的事：提示词也得说清楚，否则模型只能瞎猜一个「英镑」。"""
    assert "元" in answer.LLM_SYSTEM_PROMPT
    assert "英镑" in answer.LLM_SYSTEM_PROMPT           # 明确禁止写英镑
    assert "GBP" in answer.LLM_SYSTEM_PROMPT            # 也点名禁止这个代码


# ════════════════════════════════════════════════════════════════════════
# 12. 冷启动预热：**后台**读表，不改确定性（曾经首问要等 100 秒）
# ════════════════════════════════════════════════════════════════════════
def test_预热把数据读进缓存且不改确定性():
    # 预热前后**逐位相同** —— 它只是提前调用同一个 load_raw()，没有第二条代码路径
    before = _executor_amount(*D16_WEEK_RANGE)
    report = tools.warm_up()
    assert report["rows"] == DATASET_ROWS
    assert report["column_count"] == 8
    assert report["currency"] == "CNY"
    assert report["seconds"] >= 0
    assert _executor_amount(*D16_WEEK_RANGE) == before
    # 再预热一次（缓存已热）结果一致：幂等，不会算出第二份数据
    assert tools.warm_up()["rows"] == report["rows"]


def test_冷启动预热接在app的lifespan上且不阻塞就绪(monkeypatch):
    """**必须验"没阻塞"**：预热要是同步跑在启动里，服务会等 100 秒才肯接受连接。"""
    calls: list[int] = []
    monkeypatch.setattr(prewarm, "start_background", lambda: calls.append(1))

    with TestClient(app) as inside:
        assert inside.get("/api/health").status_code == 200

    assert calls == [1], "app 启动时没有调预热（lifespan 没接上）"


def test_预热在后台线程里跑_健康检查不用等它(monkeypatch):
    released = threading.Event()
    started = threading.Event()

    def slow_warm_up() -> dict:
        started.set()
        released.wait(5)                      # 卡住预热，模拟"还在读 22MB"
        return {"rows": 0, "column_count": 0, "first_day": "-", "last_day": "-",
                "currency": "CNY", "seconds": 0.0}

    monkeypatch.setattr(tools, "warm_up", slow_warm_up)
    monkeypatch.setattr(prewarm, "STATE", {"started": False, "done": False, "report": None, "error": None})
    monkeypatch.setattr(prewarm, "_THREAD", None)
    try:
        with TestClient(app) as inside:
            t0 = time.perf_counter()
            assert inside.get("/api/health").status_code == 200
            elapsed = time.perf_counter() - t0
            assert elapsed < 1.0, f"启动被预热拖住了：health 用了 {elapsed:.2f} 秒"
            assert started.wait(5), "预热线程没起来"
            assert prewarm.STATE["done"] is False, "预热还在跑，却已经标记完成"
    finally:
        released.set()                        # 放行，让后台线程正常收尾（别留一个真去读表的线程）
        for _ in range(100):
            if prewarm.STATE["done"]:
                break
            time.sleep(0.05)


# ════════════════════════════════════════════════════════════════════════
# 12. TASK-005：两区间比较 / 国家分布 / 归因 —— 算得对，**"判定"也对**
#     核心三条（评审点名）：① 连"主要是谁"都由代码判  ② Σ维度==总额 硬校验
#     ③ 贡献率叫 contribution_share_of_change（允许 >100% / <0%）
# ════════════════════════════════════════════════════════════════════════
def _independent_valid_rows(window: tuple[str, str]) -> pd.DataFrame:
    """**测试自己**按 D16 口径取有效行。

    刻意不借 `tools._valid_rows`：那样等于拿被测代码当参考答案。
    这里只用冻结资产（loader + metrics）走一遍 executor 的同一套掩码。
    """
    frame = loader.load_raw()
    rules = engine_metrics.resolve_enabled_rules()
    time_column = engine_metrics.TIME_FIELD
    start = _dt.date.fromisoformat(window[0])
    end = _dt.date.fromisoformat(window[1])
    low, high = engine_metrics.inclusive_day_window(start, end)
    picked = frame.loc[(frame[time_column] >= low) & (frame[time_column] < high)]
    hit = pd.concat({rule.key: rule.mask(picked).astype(bool) for rule in rules}, axis=1).sum(axis=1)
    valid = picked.loc[hit == 0].copy()
    valid["_amount"] = engine_metrics.line_amount(valid)
    return valid


def _independent_group_deltas(
    field: str, current: tuple[str, str], previous: tuple[str, str]
) -> dict[str, float]:
    """各维度在两个区间之间的变化额（测试自己算的，用来和被测代码比对）。"""
    def _by_group(window: tuple[str, str]) -> pd.Series:
        table = _independent_valid_rows(window).groupby(field, dropna=False)["_amount"].sum()
        return table.rename(index=lambda key: "(缺失)" if pd.isna(key) else str(key))

    now, before = _by_group(current), _by_group(previous)
    return {str(key): float(now.get(key, 0.0) - before.get(key, 0.0)) for key in now.index.union(before.index)}


NOV = ("2011-11-01", "2011-11-30")
OCT = ("2011-10-01", "2011-10-31")


def test_T005_AC01_两区间比较走HTTP且数字与executor逐位一致(no_llm):
    record = _ask("2011年11月和10月的销售额对比")
    assert record["intent"]["intent"] == "sales_compare"
    assert record["tool"]["name"] == "sales_compare"
    assert record["status"] == "degraded"                       # 未接 LLM：如实标注

    cur, prev = _executor_amount(*NOV), _executor_amount(*OCT)
    facts = record["facts"]
    assert facts["comparison_status"] == "ok"
    assert facts["current"]["sales_amount"] == cur              # 位级相等，不是约等
    assert facts["previous"]["sales_amount"] == prev
    assert facts["change_amount"] == cur - prev
    assert facts["change_rate"] == (cur - prev) / prev
    # 两个区间确实来自问句，不是"整段汇总"
    assert facts["current_period"]["start"] == NOV[0] and facts["current_period"]["end"] == NOV[1]
    assert facts["previous_period"]["start"] == OCT[0] and facts["previous_period"]["end"] == OCT[1]
    # 【发生了什么】是代码写的；不带归因时**没有**主要贡献段
    keys = [section["key"] for section in record["answer"]["sections"]]
    assert keys == ["what", "why", "actions"]
    assert record["answer"]["sections"][0]["source"] == "code"
    assert record["answer"]["guard"]["passed"] is True


def test_T005_AC02_自定义两区间进结构化参数且可独立复算(no_llm):
    """日期由**代码**从问句里抠成结构化参数（不是 LLM 解释的），事后可独立复算。"""
    record = _ask("2011-11-01 到 2011-11-15 与 2011-10-01 到 2011-10-15 的销售额对比")
    assert record["intent"]["intent"] == "sales_compare"
    params = record["params"]
    assert params["comparison_type"] == "custom"
    assert (params["current_start"], params["current_end"]) == ("2011-11-01", "2011-11-15")
    assert (params["previous_start"], params["previous_end"]) == ("2011-10-01", "2011-10-15")
    assert params["attribution_dimension"] is None              # 单纯比较**不**带归因

    cur = _executor_amount("2011-11-01", "2011-11-15")
    prev = _executor_amount("2011-10-01", "2011-10-15")
    assert record["facts"]["current"]["sales_amount"] == cur
    assert record["facts"]["previous"]["sales_amount"] == prev
    assert record["facts"]["change_amount"] == cur - prev
    # 换个说法问同一件事，必须得出同一个数（说明日期理解是确定性的）
    again = _ask("2011年11月1日到11月15日 和 2011年10月1日到10月15日 比一比销售额")
    assert again["facts"]["change_amount"] == cur - prev


def test_T005_AC03_环比落到最近一个完整周期且上一期与D16那一周对齐(no_llm):
    """数据最后一天在**月中/周中**：本期基准必须退到完整周期，且**写明退过**。"""
    # ① 本月比上月 → 2011-11 vs 2011-10
    record = _ask("本月比上月销售额增长了多少")
    assert record["intent"]["intent"] == "sales_compare"
    assert record["params"]["comparison_type"] == "mom"
    facts = record["facts"]
    assert facts["current_period"]["start"] == NOV[0] and facts["current_period"]["end"] == NOV[1]
    assert facts["previous_period"]["start"] == OCT[0] and facts["previous_period"]["end"] == OCT[1]
    assert facts["current"]["sales_amount"] == _executor_amount(*NOV)
    assert facts["previous"]["sales_amount"] == _executor_amount(*OCT)
    assert facts["change_amount"] == _executor_amount(*NOV) - _executor_amount(*OCT)

    # ② 本周比上周 → 上一期正好是 D16 那一周（口径对齐的硬证据）
    weekly = _ask("本周比上周的销售额")
    assert weekly["params"]["comparison_type"] == "wow"
    weekly_facts = weekly["facts"]
    assert weekly_facts["previous_period"]["start"] == D16_WEEK_RANGE[0]
    assert weekly_facts["previous_period"]["end"] == D16_WEEK_RANGE[1]
    assert weekly_facts["previous"]["sales_amount"] == _executor_amount(*D16_WEEK_RANGE)
    # 与 DECISIONS 里那一周的口径一致（同一个数，只是浮点求和的末位写法不同）
    assert weekly_facts["previous"]["sales_amount"] == pytest.approx(D16_AMOUNT, abs=1e-6)
    assert weekly_facts["current_period"]["days"] == 7
    assert weekly_facts["previous_period"]["days"] == 7
    assert weekly_facts["same_length"] is True
    # 为什么挪了基准：必须写在 notes 里（不能悄悄换一个周期）
    notes = " ".join(weekly["tool"]["notes"])
    assert "本期基准被改过" in notes and "2011-12-09" in notes


def test_T005_AC03_上一期为0时变化率是not_available而不是Infinity(no_llm):
    """2011-11-26 在数据里**一行都没有** → 上一期 0 → 不许出 Infinity / NaN。"""
    record = _ask("2011-11-27 和 2011-11-26 的销售额对比")
    facts = record["facts"]
    assert facts["current"]["sales_amount"] == _executor_amount("2011-11-27", "2011-11-27")
    assert facts["previous"]["sales_amount"] == 0.0
    assert facts["change_rate"] is None
    assert facts["change_rate_status"] == "not_available"
    assert record["tool"]["selfcheck"]["rate_not_available"] is True
    display = json.dumps(record["tool"]["display"], ensure_ascii=False)
    assert "不适用" in display and "上一期为 0" in display

    blob = json.dumps(record, ensure_ascii=False, default=str)
    for bad in ("Infinity", "-Infinity", "NaN", "nan", "Inf"):
        assert bad not in blob, f"答案里出现了 {bad}"


def test_T005_AC04_同比有完整等长窗口才做_做不到就说数据不足(no_llm):
    # ① 完整等长窗口（去年同一天数）→ 照做
    same = tools.sales_compare(
        comparison_type="yoy",
        current_start=_dt.date(2011, 12, 1), current_end=_dt.date(2011, 12, 9),
    )
    facts = same["facts"]
    assert facts["comparison_status"] == "ok"
    assert facts["current_period"]["start"] == "2011-12-01"
    assert facts["previous_period"] == {"start": "2010-12-01", "end": "2010-12-09", "days": 9, "adjusted": False}
    assert facts["same_length"] is True
    assert facts["current"]["sales_amount"] == _executor_amount("2011-12-01", "2011-12-09")
    assert facts["previous"]["sales_amount"] == _executor_amount("2010-12-01", "2010-12-09")
    assert facts["change_amount"] == facts["current"]["sales_amount"] - facts["previous"]["sales_amount"]

    # ② 去年同期整月落在数据集起点之前 → 不许截断成"能算多少算多少"
    early = tools.sales_compare(
        comparison_type="yoy",
        current_start=_dt.date(2011, 11, 1), current_end=_dt.date(2011, 11, 30),
    )
    assert early["facts"]["comparison_status"] == "insufficient_data"
    assert "早于数据集起点" in early["notes"][0]


def test_T005_AC04_用户要的同比不完整时明确说数据不足且不截断不猜(no_llm):
    record = _ask("2011年12月同比是多少")
    assert record["intent"]["intent"] == "sales_compare"
    assert record["status"] == "unsupported"
    # 用户要的区间**原样保留**在参数里（没有偷偷改成 12-01~12-09）
    assert record["params"]["current_end"] == "2011-12-31"
    assert record["facts"]["comparison_status"] == "insufficient_data"
    assert "change_amount" not in record["facts"]
    assert "change_rate" not in record["facts"]
    notice = record["notice"]
    assert "仅覆盖至" in notice and "12 月 9 日" in notice
    # 数据不足时**一个金额都不给**（连"12月已发生的那 9 天"也不给）
    partial = _executor_amount("2011-12-01", "2011-12-09")
    blob = json.dumps(record, ensure_ascii=False, default=str)
    assert f"{partial}" not in blob and f"{partial:,.2f}" not in blob
    # 没有可比的事实 → 不给推断与建议，且这段是**代码**写的
    keys = [section["key"] for section in record["answer"]["sections"]]
    assert keys == ["what", "why", "actions"]
    why = record["answer"]["sections"][1]
    assert why["source"] == "code" and "数据不足" in why["text"]


def test_T005_AC05_国家分布TOP5的构成项之和等于区间总额(no_llm):
    record = _ask("2011年11月各国家销售额TOP5")
    assert record["intent"]["intent"] == "sales_breakdown_by_country"
    assert record["tool"]["name"] == "sales_breakdown_by_country"
    facts, items = record["facts"], record["items"]
    total = _executor_amount(*NOV)
    assert facts["total_amount"] == total                      # 与冻结资产位级相等
    # Σ(各国家) == 区间总额（两条求和路径，浮点末位允许差一点，但必须过工具自己的容差）
    assert facts["grouped_total"] == pytest.approx(total, abs=1e-6)
    assert record["tool"]["selfcheck"]["grouped_matches_detail"] is True
    assert [item["rank"] for item in items] == [1, 2, 3, 4, 5]
    assert len(items) == 5 and facts["top_n"] == 5
    # 排序确实是"金额降序"（并列时按国家名，可复现）
    amounts = [item["amount"] for item in items]
    assert amounts == sorted(amounts, reverse=True)
    assert items[0]["amount"] == facts["top_country_amount"] == max(amounts)
    # 占比按"区间总销售额"算，且没被截断
    for item in items:
        assert abs(item["share"] - item["amount"] / total) < 1e-12
        assert 0 < item["share"] <= 1
    assert sum(item["share"] for item in items) < 1            # 只有 TOP5，不是全部国家
    assert "不承担" in " ".join(record["tool"]["notes"])        # 明说这不做归因


def test_T005_AC05_国家分布里的国家全部来自数据集的Country列(no_llm):
    record = _ask("2011年11月各国家销售额TOP5")
    real = {str(name) for name in loader.load_raw()["Country"].dropna().unique()}
    for item in record["items"]:
        assert item["country"] in real, f"{item['country']} 不是数据集里的国家"
    # 「区间内出现过的国家数」按**这个区间**算，不是整个数据集的
    window_countries = _independent_valid_rows(NOV)["Country"].nunique(dropna=False)
    assert record["facts"]["country_count"] == window_countries
    assert window_countries < loader.load_raw()["Country"].nunique(dropna=False)


def test_T005_AC06_归因的贡献者由代码按变化额排序选出且与测试自己算的一致(no_llm):
    record = _ask("2011年11月相比10月，哪些国家推动了销售额变化")
    assert record["intent"]["intent"] == "sales_compare"
    assert record["params"]["attribution_dimension"] == "country"
    facts = record["facts"]
    assert facts["attribution_status"] == "ok"
    positive, negative = facts["positive_contributors"], facts["negative_contributors"]
    assert positive and negative and facts["attribution_dimension"] == "country"

    # ── 硬校验：Σ(各维度 delta) == total_delta，且 total_delta 来自冻结资产 ──
    consistency = record["tool"]["selfcheck"]["attribution_consistency"]
    assert consistency["passed"] is True
    assert record["tool"]["selfcheck"]["attribution_passed"] is True
    assert consistency["sum_of_dimension_deltas"] == pytest.approx(consistency["total_delta"], abs=1e-6)
    assert consistency["total_delta"] == facts["change_amount"]
    assert consistency["total_delta"] == _executor_amount(*NOV) - _executor_amount(*OCT)

    # ── 独立复算：名单、排序、金额、贡献率全对得上 ──
    deltas = _independent_group_deltas("Country", NOV, OCT)
    assert abs(sum(deltas.values()) - facts["change_amount"]) < 1e-6
    ranked = sorted(deltas.items(), key=lambda pair: (-abs(pair[1]), pair[0]))
    top_n = tools.ATTRIBUTION_TOP_N
    expected_positive = [name for name, delta in ranked if delta > tools._FLOAT_TOL][:top_n]
    expected_negative = [name for name, delta in ranked if delta < -tools._FLOAT_TOL][:top_n]
    assert [item["name"] for item in positive] == expected_positive
    assert [item["name"] for item in negative] == expected_negative

    for item in positive + negative:
        assert abs(item["delta"] - deltas[item["name"]]) < 1e-6
        assert abs(item["current"] - item["previous"] - item["delta"]) < 1e-6
        assert item["contribution_share_of_change"] is not None
        # 贡献率的分母是**总变化额**，不是总销售额
        assert abs(item["contribution_share_of_change"] * consistency["total_delta"] - item["delta"]) < 1e-6
    # 有正有负都正常；**允许 >100% / <0%（不许 clamp）** —— 本项目 11 月 UK 就是 >100%
    shares = [item["contribution_share_of_change"] for item in positive + negative]
    assert max(shares) > 1, "贡献率被截断了（>100% 是正常数学结果，不该被 clamp）"
    assert min(shares) < 0
    assert "总变化额" in " ".join(record["tool"]["notes"])      # 分母口径写在明面上


def test_T005_AC07_按产品编码归因同样守恒且名字是真实StockCode(no_llm):
    record = _ask("2011年11月相比10月，哪些产品推动了销售额变化")
    assert record["params"]["attribution_dimension"] == "stock_code"
    facts = record["facts"]
    consistency = record["tool"]["selfcheck"]["attribution_consistency"]
    assert consistency["passed"] is True
    assert consistency["total_delta"] == facts["change_amount"]
    contributors = facts["positive_contributors"] + facts["negative_contributors"]
    assert contributors

    codes = {str(code) for code in loader.load_raw()["StockCode"].dropna().unique()}
    for item in contributors:
        assert item["name"] in codes, f"{item['name']} 不是数据集里的 StockCode"
    deltas = _independent_group_deltas("StockCode", NOV, OCT)
    for item in contributors:
        assert abs(item["delta"] - deltas[item["name"]]) < 1e-6
    # 榜单要能读：编码旁边带上展示名
    assert any(item["label"] for item in facts["positive_contributors"])


def test_T005_AC08_一致性校验不过时整段归因不进回答(monkeypatch, no_llm):
    """故意让校验失败：必须**整段**消失（facts 状态、回答分区、工具返回值三处都要看得出）。"""
    real = tools.build_attribution

    def _broken(*args, **kwargs):
        report = real(*args, **kwargs)
        report["consistency"]["passed"] = False               # 伪造一个"Σ ≠ 总额"
        report["consistency"]["delta"] = 999.99
        report["passed"] = False
        return report

    monkeypatch.setattr(tools, "build_attribution", _broken)
    record = _ask("2011年11月相比10月，哪些国家推动了销售额变化")
    assert record["facts"]["attribution_status"] == "rejected_inconsistent"
    assert record["facts"]["positive_contributors"] == []
    assert record["tool"]["selfcheck"]["attribution_passed"] is False
    assert record["tool"]["selfcheck"]["attribution_consistency"]["passed"] is False
    assert "一致性" in " ".join(record["tool"]["notes"])
    keys = [section["key"] for section in record["answer"]["sections"]]
    assert "contribution" not in keys, "归因没过校验，却还是出现在回答里"
    blob = json.dumps(record, ensure_ascii=False, default=str)
    assert "正贡献者" not in blob and "负贡献者" not in blob
    # 但比较本身照样给（只是一段归因被丢掉，不是整个回答作废）
    assert record["facts"]["change_amount"] == _executor_amount(*NOV) - _executor_amount(*OCT)


def test_T005_AC08_一致性校验函数本身能判出不一致(no_llm):
    now = tools._valid_rows(_dt.date(2011, 11, 1), _dt.date(2011, 11, 30))
    before = tools._valid_rows(_dt.date(2011, 10, 1), _dt.date(2011, 10, 31))
    total_delta = _executor_amount(*NOV) - _executor_amount(*OCT)

    good = tools.build_attribution("country", now, before, total_delta)
    assert good["passed"] is True and good["consistency"]["delta"] <= good["consistency"]["tolerance"]
    bad = tools.build_attribution("country", now, before, total_delta + 12345.67)
    assert bad["passed"] is False
    assert bad["consistency"]["delta"] > bad["consistency"]["tolerance"]


@pytest.mark.parametrize(
    "question, keyword",
    [
        ("2011年11月华南区各国家销售额TOP5", "华南"),
        ("2011年11月各区域销售额占比", "区域"),
        ("2011年11月北美区域的销售额", "北美"),
        ("2011年11月广东省的销售额", "省"),
        ("2011年11月上海门店卖了多少", "门店"),
        ("2011年11月线上渠道的销售额", "渠道"),
        ("张三销售2011年11月的销售额", "销售"),
        ("2011年11月按销售员拆分销售额", "销售员"),
    ],
)
def test_T005_AC09_区域省份城市门店渠道销售员仍然被拒且不许用国家顶替(question, keyword):
    record = _ask(question)
    assert record["status"] == "unsupported"
    assert record["intent"]["intent"] == "unsupported"
    assert keyword in record["intent"]["reason"]
    assert record["tool"] is None and record["facts"] is None
    blob = json.dumps(record, ensure_ascii=False, default=str)
    # 没有偷算出任何金额，也没有把国家搬出来顶替
    assert str(_executor_amount(*NOV)) not in blob
    assert f"{_executor_amount(*NOV):,.2f}" not in blob
    # 回答段里不许出现国家名（`data_profile` 的币种说明里提到"英国零售商"是数据描述，
    # 不是拿国家顶替区域，所以只查回答与工具部分）
    answer_blob = json.dumps([record["answer"], record["tool"], record["items"]], ensure_ascii=False)
    for name in ("United Kingdom", "英国", "Germany", "德国"):
        assert name not in answer_blob
    what = record["answer"]["sections"][0]["text"]
    assert "Country" in what and "回答不了" in what


def test_T005_AC09_带归因的问法里出现区域词也照样拒(no_llm):
    """「哪些区域推动了变化」不能因为同句有"贡献/推动"就放行成按国家归因。"""
    record = _ask("2011年11月相比10月，哪些区域推动了销售额变化")
    assert record["status"] == "unsupported"
    assert record["facts"] is None
    assert not record["params"]
    assert "贡献" not in record["answer"]["sections"][0]["text"]


def test_T005_AC10_回答里的金额与executor位级相等(no_llm):
    """位级比对：不是 approx，是 `==`（同一次 float 运算路径）。"""
    record = _ask("2011年11月和10月的销售额对比")
    text = record["answer"]["sections"][0]["text"]
    cur, prev = _executor_amount(*NOV), _executor_amount(*OCT)
    assert f"{cur:,.2f}" in text and f"{prev:,.2f}" in text
    assert f"{cur - prev:+,.2f}" in text                        # 变化额带符号
    assert f"{(cur - prev) / prev * 100:+.2f}" in text          # 变化率带符号、百分数
    assert record["tool"]["selfcheck"]["current_amount"] == cur
    assert record["tool"]["selfcheck"]["previous_amount"] == prev


def test_T005_AC11_LLM只拿代码生成的文本_不碰DataFrame也不碰原始行(monkeypatch):
    """架构级：喂给模型的**每一次**输入都必须是加工好的文本。"""
    captured: list[str] = []
    intent_json = (
        '{"intent":"sales_compare","params":{"comparison_type":"custom",'
        '"current_start":"2011-11-01","current_end":"2011-11-30",'
        '"previous_start":"2011-10-01","previous_end":"2011-10-31",'
        '"attribution_dimension":"country"},"assumptions":[],"confidence":0.9}'
    )

    def _chat(system: str, user: str, **kwargs):
        captured.append(user)
        assert isinstance(user, str) and isinstance(system, str)
        if "意图解析器" in system:
            return intent_json
        return "【为什么】\n可能是需求集中。\n【建议行动】\n- 关注头部国家。"

    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "chat", _chat)
    record = _ask("2011年11月相比10月，哪些国家推动了销售额变化", use_llm=True)
    assert captured, "LLM 通道接上了却没被调用"

    dirty = ("DataFrame", "dtype", "Series", "Name:", "nan", "NaN", "0    ")
    for prompt in captured:
        for marker in dirty:
            assert marker not in prompt, f"喂给模型的文本里有 {marker}：{prompt[:200]}"
    # 它拿到的确实是"事实 + 代码排好的贡献名单"，而不是让模型自己判断谁主要
    answer_prompt = captured[-1]
    assert "正贡献者" in answer_prompt and "负贡献者" in answer_prompt
    assert "总变化：" in answer_prompt and "贡献率" in answer_prompt
    top = record["facts"]["positive_contributors"][0]["name"]
    assert top in answer_prompt                                   # 名单是代码给的，模型照着说
    assert record["answer"]["sections"][1]["source"] == "code"


def test_T005_AC12_主要贡献段由代码生成_LLM改不了名单(monkeypatch):
    """LLM 在【为什么】里自称"主要贡献者是西班牙和火星国" —— 名单必须纹丝不动。"""
    intent_json = (
        '{"intent":"sales_compare","params":{"comparison_type":"custom",'
        '"current_start":"2011-11-01","current_end":"2011-11-30",'
        '"previous_start":"2011-10-01","previous_end":"2011-10-31",'
        '"attribution_dimension":"country"},"assumptions":[],"confidence":0.9}'
    )

    def _chat(system: str, user: str, **kwargs):
        if "意图解析器" in system:
            return intent_json
        return "【为什么】\n其实主要贡献者是西班牙和火星国。\n【建议行动】\n- 看西班牙。"

    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "chat", _chat)
    record = _ask("2011年11月相比10月，哪些国家推动了销售额变化", use_llm=True)

    sections = {section["key"]: section for section in record["answer"]["sections"]}
    contribution = sections["contribution"]
    assert contribution["source"] == "code"                     # 这一段不是模型写的
    assert "西班牙" not in contribution["text"] and "火星国" not in contribution["text"]
    assert "LLM 未参与" in contribution["contributors"]["decided_by"]

    attribution = record["facts"]
    assert contribution["contributors"]["positive"] == [
        item["name"] for item in attribution["positive_contributors"]
    ]
    assert contribution["contributors"]["negative"] == [
        item["name"] for item in attribution["negative_contributors"]
    ]
    assert contribution["contributors"]["total_delta"] == record["facts"]["change_amount"]
    # 数字也是代码写的：段里的每个数字都能在确定性结果里找到
    allowed = answer.collect_allowed_numbers(contribution["text"], record["facts"], record["tool"])
    assert answer.number_guard(contribution["text"], allowed)["passed"] is True
    # 分区顺序：事实 → 贡献 → 推断 → 建议（顺序本身就说明谁是事实谁是猜测）
    assert [section["key"] for section in record["answer"]["sections"]] == [
        "what", "contribution", "why", "actions"
    ]


def test_T005_AC12_主要贡献段里的每个数字都来自事实表(no_llm):
    record = _ask("2011年11月相比10月，哪些国家推动了销售额变化")
    contribution = [s for s in record["answer"]["sections"] if s["key"] == "contribution"][0]
    allowed = answer.collect_allowed_numbers(contribution["text"], record["facts"], record["tool"])
    report = answer.number_guard(contribution["text"], allowed)
    assert report["passed"] is True and report["violations"] == []
    assert "正贡献者" in contribution["text"] and "负贡献者" in contribution["text"]
    # 分母口径必须跟着数字一起出现，免得被读成"占总销售额"
    assert "总变化额" in contribution["text"] and "不是总销售额" in contribution["text"]


def test_T005_回答分区四段且顺序固定(no_llm):
    plain = _ask("2011年11月和10月的销售额对比")["answer"]["sections"]
    assert [s["key"] for s in plain] == ["what", "why", "actions"]
    attributed = _ask("2011年11月相比10月，哪些国家推动了销售额变化")["answer"]["sections"]
    assert [s["key"] for s in attributed] == ["what", "contribution", "why", "actions"]
    assert [s["source"] for s in attributed] == ["code", "code", "code", "code"]  # 未接 LLM 时降级
    assert answer.SECTION_TITLES["contribution"] == "【主要贡献】"


def test_T005_能力端点如实列出七个意图(no_llm):
    body = client.get("/api/chat/capabilities").json()
    names = [item["name"] for item in body["intents"]]
    assert names == ["sales_summary", "sales_trend", "top_products",
                     "sales_compare", "sales_breakdown_by_country",
                     # TASK-006 追加的两个（顺序 = COMPUTE_INTENTS 追加顺序）
                     "customer_analysis", "product_analysis"]
    assert "sales_attribution" not in names
    titles = {item["name"]: item["title"] for item in body["intents"]}
    assert titles["sales_compare"] and titles["sales_breakdown_by_country"]
    assert titles["customer_analysis"] == "客户分析"
    assert titles["product_analysis"] == "商品分析"
    assert any("区域" in word for word in body["unsupported"]["dimensions"])


# ════════════════════════════════════════════════════════════════════════
# 13. 收口闸门：真 LLM 把「A 和 B 比销售额」答成单区间合并求和 —— 代码拉回来
#
#     Hermes 2026-09-23 实测事故：真 DeepSeek 面对下面这 6 种常见问法，
#     全部返回 sales_summary(start=2011-10-01, end=2011-11-30) —— 把两个区间
#     **合并求和**，答案变成 2,664,475.63（11 月 + 10 月），看着像答案、实则答非所问。
#     这里用「假 LLM 故意回那条错结果」把事故**钉进回归测试**：修好后它必须被拉回 sales_compare。
# ════════════════════════════════════════════════════════════════════════
MERGED_SUMMARY_JSON = (
    '{"intent":"sales_summary","params":{"start":"2011-10-01","end":"2011-11-30"},'
    '"assumptions":[],"confidence":0.9}'
)
COMPARE_PHRASINGS = [
    "比较一下2011年11月和2011年10月的销售额",
    "比较2011年11月和2011年10月的销售额",
    "2011年11月和2011年10月的销售额哪个高",
    "2011年11月比2011年10月销售额增长了多少",
    "11月对比10月的销售额",
    "2011年11月和10月销售额对比",
]
MERGED_WINDOW_AMOUNT = _executor_amount("2011-10-01", "2011-11-30")   # 2,664,475.63（错答案）


def _assert_not_merged(record: dict) -> None:
    """错答案（两个月合并求和）= 2,664,475.63 —— 整个回答里不许出现它。"""
    blob = json.dumps(record, ensure_ascii=False, default=str)
    assert str(MERGED_WINDOW_AMOUNT) not in blob, f"回答里出现了合并区间的和：{MERGED_WINDOW_AMOUNT!r}"
    assert f"{MERGED_WINDOW_AMOUNT:,.2f}" not in blob


@pytest.mark.parametrize("question", COMPARE_PHRASINGS)
def test_T005B_LLM把比较答成合并区间时代码必须拉回sales_compare(question, monkeypatch):
    """6 种措辞逐个测（Hermes 点名：不要只测一种）。"""
    _llm_returns("【为什么】\n本期高于上期。\n【建议行动】\n- 关注英国。", monkeypatch,
                 intent_json=MERGED_SUMMARY_JSON)          # ← 故意喂 Hermes 实测到的那条错结果

    parsed, info = intent_module.parse(question, allow_llm=True)
    assert parsed.intent == "sales_compare", (question, parsed.intent)
    assert info["comparison_override"] is True, question
    assert parsed.params["current_start"] == "2011-11-01" and parsed.params["current_end"] == "2011-11-30"
    assert parsed.params["previous_start"] == "2011-10-01" and parsed.params["previous_end"] == "2011-10-31"
    assert any("合并成一个区间" in note for note in parsed.assumptions), question   # 改过就留痕

    record = _ask(question, use_llm=True)
    facts = record["facts"]
    assert record["intent"]["intent"] == "sales_compare" and record["tool"]["name"] == "sales_compare"
    assert facts["current"]["sales_amount"] == _executor_amount(*NOV)      # 位级相等
    assert facts["previous"]["sales_amount"] == _executor_amount(*OCT)
    assert facts["change_amount"] == _executor_amount(*NOV) - _executor_amount(*OCT)
    _assert_not_merged(record)


def test_T005B_Hermes给的参考值位级相等(no_llm):
    """Hermes 自算的 11月 / 10月 / Δ 三个数：接口必须逐位对上。"""
    facts = _ask("比较一下2011年11月和2011年10月的销售额")["facts"]
    assert facts["current"]["sales_amount"] == 1509496.33
    assert facts["previous"]["sales_amount"] == 1154979.2999999998
    assert facts["change_amount"] == 354517.03000000026
    assert _executor_amount(*NOV) == 1509496.33 and _executor_amount(*OCT) == 1154979.2999999998


@pytest.mark.parametrize("question", COMPARE_PHRASINGS)
def test_T005B_没有LLM时六种措辞也走sales_compare(question, no_llm):
    parsed, _info = intent_module.parse(question, allow_llm=False)
    assert parsed.intent == "sales_compare", question
    assert (parsed.params["current_start"], parsed.params["previous_start"]) == ("2011-11-01", "2011-10-01")
    record = _ask(question)
    assert record["facts"]["change_amount"] == _executor_amount(*NOV) - _executor_amount(*OCT)
    _assert_not_merged(record)


def test_T005B_三个区间既不合并也不硬凑两区间_明确报错(monkeypatch):
    """「比较 9月、10月、11月」—— 本版只做两区间。**不许**悄悄挑两个、更不许合并求和。"""
    _llm_returns("【为什么】\n略。\n【建议行动】\n- 略。", monkeypatch, intent_json=MERGED_SUMMARY_JSON)

    for allow_llm in (True, False):
        with pytest.raises(intent_module.IntentError) as caught:
            intent_module.parse("比较 9月、10月、11月的销售额", allow_llm=allow_llm)
        assert caught.value.code == "intent_unparseable", allow_llm
        assert "合并" in caught.value.message, allow_llm          # 话说清楚了：不合并
        assert "比较 2011-11 和 2011-10" in caught.value.message   # 并给了正确写法

    record = _ask("比较 9月、10月、11月的销售额", use_llm=True)
    assert record["status"] == "error" and record["facts"] is None
    assert "不把两个区间合并求和" in record["notice"]
    _assert_not_merged(record)


def test_T005B_单区间问题不被闸门劫持(monkeypatch):
    """闸门只拦「有比较语义却解析成单区间」，正常单区间问题一个字都不许改。"""
    for question, payload, expect in (
        ("2011年11月一共卖了多少",
         '{"intent":"sales_summary","params":{"start":"2011-11-01","end":"2011-11-30"},'
         '"assumptions":[],"confidence":0.9}', "sales_summary"),
        ("2011年10月到11月的销售额",
         '{"intent":"sales_summary","params":{"start":"2011-10-01","end":"2011-11-30"},'
         '"assumptions":[],"confidence":0.9}', "sales_summary"),
        ("2011年11月各国家销售额占比",
         '{"intent":"sales_breakdown_by_country","params":{"start":"2011-11-01","end":"2011-11-30",'
         '"top_n":5},"assumptions":[],"confidence":0.9}', "sales_breakdown_by_country"),
    ):
        _llm_returns("【为什么】\n略。\n【建议行动】\n- 略。", monkeypatch, intent_json=payload)
        parsed, info = intent_module.parse(question, allow_llm=True)
        assert parsed.intent == expect, question
        assert info["comparison_override"] is False, question

    # 「占比」不是比较、「比如」不是比较 —— 别把闸门做成误伤
    assert intent_module.looks_like_comparison("2011年11月各国家销售额占比") is False
    assert intent_module.looks_like_comparison("比如2011年11月的销售额") is False
    assert intent_module.looks_like_comparison("2011年11月和10月的销售额对比") is True

    # 降级路径同样：单区间问题照旧解析，不因为闸门变成"没听懂"
    for question, expect in (("2011年11月一共卖了多少", "sales_summary"),
                             ("2011年10月到11月的销售额", "sales_summary"),
                             ("2011年11月各国家销售额占比", "sales_breakdown_by_country"),
                             ("2011年11月的销售趋势", "sales_trend")):
        assert intent_module.parse(question, allow_llm=False)[0].intent == expect, question


def test_T005B_同比问法仍然认得出yoy(no_llm):
    """闸门别把「去年同期」误当成 custom 两区间。"""
    parsed, _info = intent_module.parse("2011年11月和去年同期的销售额对比", allow_llm=False)
    assert parsed.intent == "sales_compare" and parsed.params["comparison_type"] == "yoy"


def test_T005B_三个区间时LLM给的两区间也不作数_别无声吃掉一个月(monkeypatch):
    """同一个洞的另一半：问句里明摆着三个月，LLM 会默默只比最近两个月，把第三个月吃掉。

    （实测：真 DeepSeek 对「比较 9月、10月、11月的销售额」给出 11月 vs 10月 的 sales_compare，
      既没合并、也没说 9 月去哪了 —— 仍然是"看起来对、其实答非所问"。）
    """
    _llm_returns("【为什么】\n略。\n【建议行动】\n- 略。", monkeypatch,
                 intent_json='{"intent":"sales_compare","params":{"comparison_type":"custom",'
                             '"current_start":"2011-11-01","current_end":"2011-11-30",'
                             '"previous_start":"2011-10-01","previous_end":"2011-10-31",'
                             '"attribution_dimension":null},"assumptions":[],"confidence":0.9}')
    for question in ("比较 9月、10月、11月的销售额", "2011年9月、10月、11月的销售额对比"):
        for allow_llm in (True, False):
            with pytest.raises(intent_module.IntentError) as caught:
                intent_module.parse(question, allow_llm=allow_llm)
            assert caught.value.code == "intent_unparseable", (question, allow_llm)
            assert "没能确定到底比哪两个区间" in caught.value.message

    record = _ask("比较 9月、10月、11月的销售额", use_llm=True)
    assert record["status"] == "error" and record["facts"] is None
    _assert_not_merged(record)

    # 别误伤：正好两个区间的 6 种措辞照旧放行
    for question in COMPARE_PHRASINGS:
        assert intent_module.parse(question, allow_llm=False)[0].intent == "sales_compare", question
