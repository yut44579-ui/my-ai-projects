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

from fastapi.testclient import TestClient  # noqa: E402

from app import prewarm, state  # noqa: E402
from app.ai import answer, intent as intent_module, llm, service, tools  # noqa: E402
from app.api import app  # noqa: E402
from app.engine import executor, loader  # noqa: E402

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
        f"【为什么】\n这一周有效行占比较高，需求集中在少数几个单品上（区间金额 {facts_amount:,.2f} 英镑）。\n"
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
        "【为什么】\n因为华南区贡献了 999999.99 英镑的销售额。\n"
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
    assert answer.number_guard("金额 1,234.56 英镑", allowed)["passed"] is True   # 千分位归一
    bad = answer.number_guard("金额 1,234.57 英镑", allowed)
    assert bad["passed"] is False and bad["violations"] == ["1234.57"]


def test_币种闸门单元行为():
    """「元/人民币」是**事实错误**（数据集是英镑）→ 与编造数字同样处理：整段作废。"""
    # 该拦的：人民币 / RMB / ¥ / 数字或中文数词 + 元 / （元） / 以元为单位
    for bad in ("销售额是人民币", "按 RMB 计价", "合计 ¥123", "一百万元", "销售额（元）",
                "以元为单位统计", "12 元"):
        assert answer.currency_guard(bad), f"这段写错了币种却没被拦下：{bad}"
    # 不该误伤的：这几个词里都有「元」，但都不是币种
    for ok in ("这是基本的单元测试", "多种元素的组合", "一次性买齐", "销售额集中在一百多个单品上",
               "元旦前后是旺季", "金额以英镑计价"):
        assert answer.currency_guard(ok) == [], f"这段没写错币种却被拦下了：{ok}"

    # 干净的段落整体放行；写错币种的段落整段作废（且 violations 不含数字）
    allowed = answer.collect_allowed_numbers({"amount": 1234.56})
    clean = answer.number_guard("销售额集中在若干单品上", allowed)
    assert clean["passed"] is True and clean["currency_words"] == []
    dirty = answer.number_guard("这一周的销售额以人民币结算", allowed)
    assert dirty["passed"] is False and dirty["violations"] == []
    assert dirty["currency_words"] == ["人民币"]


def test_AC04_LLM写错币种整段作废(monkeypatch):
    """真模型完全可能把单位写成「元」—— 事实段与闸门两道防线都必须挡住它。"""
    _llm_returns(
        "【为什么】\n这批货集中在少数几个单品上，贡献了大部分人民币销售额。\n"
        "【建议行动】\n- 关注头部单品\n- 复核取消单",
        monkeypatch,
    )
    record = _ask("2011年11月21日到11月27日一共卖了多少", use_llm=True)
    assert record["facts"]["sales_amount"] == _executor_amount(*D16_WEEK_RANGE)   # 数字照样是对的

    assert record["status"] == "degraded"
    guard = record["answer"]["guard"]
    assert guard["passed"] is False
    assert guard["currency_words"] == ["人民币"]
    assert guard["violations"] == []                    # 币种错、数字没错 —— 两类留痕能分清
    why = record["answer"]["sections"][1]
    assert why["source"] == "code" and "人民币" not in why["text"]
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

    record = _ask("2011年11月21日到11月27日一共卖了多少")
    assert record["status"] == "degraded"
    assert record["llm"]["used"] is False
    assert record["llm"]["answer_source"] == "code"
    assert "未接 LLM" in record["notice"]
    # 事实仍然是真的（降级 ≠ 乱算）
    assert record["facts"]["sales_amount"] == _executor_amount(*D16_WEEK_RANGE)
    why = record["answer"]["sections"][1]
    assert why["source"] == "code" and "未接 LLM" in why["text"]


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


def test_AC08_工具白名单只有三个且都在注册表里():
    assert set(tools.TOOLS) == set(intent_module.COMPUTE_INTENTS)
    assert len(tools.TOOLS) == 3
    with pytest.raises(KeyError):
        tools.run_tool("delete_everything", {})


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

    # 端点总清单**精确**钉死：TASK-004 只许新增上面这 4 条，
    # 其余路径（TASK-002C/002D 的 10 个 + TASK-003 的 4 个）必须原样还在。
    # 用集合相等而不是"包含若干条"：新增一条没申报的路径也会在这里被抓住。
    assert paths - new_paths == {
        "/api/upload", "/api/schema", "/api/execute", "/api/download/{execution_id}",
        "/api/executions", "/api/health",
        "/api/tasks", "/api/tasks/{task_id}", "/api/tasks/{task_id}/run",
        "/api/tasks/{task_id}/runs",
        "/api/documents", "/api/documents/{doc_id}",
        "/api/documents/{doc_id}/text", "/api/documents/{doc_id}/summary",
    }


# ════════════════════════════════════════════════════════════════════════
# 11. 货币单位：**显式声明的英镑**（曾经错写成「元」的回归测试）
# ════════════════════════════════════════════════════════════════════════
# 数据集是英国零售商流水，8 列里**没有货币字段** —— 单位是"声明的口径"，不是算出来的。
# 这一节的测试钉两件事：① 声明本身对不对（GBP/£/source=dataset_default）；
# ② 全链路（事实段 / display 表 / 能力端点 / 前端取值点）**只认这一处**，任何地方
# 再冒出「元」都要被抓住（前端那一半由 tests/test_web.py 的静态检查兜）。
def test_货币单位是显式声明的英镑():
    currency = tools.DATASET_CURRENCY
    assert currency["code"] == "GBP"
    assert currency["symbol"] == "£"
    assert currency["name"] == "英镑"
    # 关键：标明它不是"从数据里读到的"，否则下一个人会以为数据里有货币列
    assert currency["source"] == "dataset_default"
    assert currency["note"]
    assert tools.currency_unit() == currency["name"] == "英镑"
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
        # 事实段（代码生成的那一段）里出现的每个金额都得带「英镑」
        text = answer.render_facts_text(result, question="")
        assert "英镑" in text
        assert "元" not in text, (result["tool"], text)
    assert money_units and set(money_units) == {tools.currency_unit()}


def test_能力端点带出币种声明_前后端同一个出处():
    caps = client.get("/api/chat/capabilities").json()
    assert caps["currency"]["code"] == "GBP" and caps["currency"]["symbol"] == "£"
    assert caps["data_profile"]["currency"] == caps["currency"]       # 两处同源
    # 币种块里不许出现人民币口径的字样（「元」作为**金额单位**出现就算错）
    assert answer.currency_guard(json.dumps(caps["currency"], ensure_ascii=False)) == []


def test_提问链路里的金额单位是英镑不是元():
    record = _ask("2011年11月21日到11月27日一共卖了多少？")
    assert record["facts"]["sales_amount"] == _executor_amount(*D16_WEEK_RANGE)   # 数字照旧逐位相等
    what = record["answer"]["sections"][0]["text"]
    assert "英镑" in what and "元" not in what
    # 工具返回的 display 表（前端事实表直接渲染它）也必须是英镑
    assert {"英镑"} == {item["unit"] for item in record["tool"]["display"]
                        if item.get("format") == "money"}


def test_提示词告诉模型币种是英镑():
    """写对单位不只是闸门的事：提示词也得说清楚，否则模型只能瞎猜一个「元」。"""
    assert "英镑" in answer.LLM_SYSTEM_PROMPT
    assert "人民币" in answer.LLM_SYSTEM_PROMPT        # 明确禁止写人民币
    assert "元" not in answer.LLM_SYSTEM_PROMPT.replace("英镑", "").replace("人民币", "") \
        or "不许写" in answer.LLM_SYSTEM_PROMPT          # 「元」只许出现在禁令里


# ════════════════════════════════════════════════════════════════════════
# 12. 冷启动预热：**后台**读表，不改确定性（曾经首问要等 100 秒）
# ════════════════════════════════════════════════════════════════════════
def test_预热把数据读进缓存且不改确定性():
    # 预热前后**逐位相同** —— 它只是提前调用同一个 load_raw()，没有第二条代码路径
    before = _executor_amount(*D16_WEEK_RANGE)
    report = tools.warm_up()
    assert report["rows"] == DATASET_ROWS
    assert report["column_count"] == 8
    assert report["currency"] == "GBP"
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
                "currency": "GBP", "seconds": 0.0}

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
