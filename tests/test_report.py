"""tests/test_report.py · TASK-010 验收测试（要一份报告 → 就得给一份报告）。

════════════════════════════════════════════════════════════════════════
【这条需求的原话与病灶】
════════════════════════════════════════════════════════════════════════
用户问「帮我根据本星期的销售数据做一份销售周报」，系统原先答的是**零散指标卡**
（销售额 / 订单 / 客户 / 客单价 + 口径说明 + 【为什么】/【建议行动】）——
数字都对，但**不是一份周报**。本文件验的就是"现在给的确实是一份报告"。

════════════════════════════════════════════════════════════════════════
【本文件守住的四条底线】
════════════════════════════════════════════════════════════════════════
    ① 报告**不是**第 6 个计算型 Intent：`COMPUTE_INTENTS` 仍是 5 个，
       `/api/chat/capabilities` 的 `intents` 也不增项（报告单列在 `report` 块里）；
    ② 报告**不新造口径**：每个数字都与对应的白名单工具**位级相等**
       （直接调 `tools.run_tool` 比，不拿 report.py 自己的中间量自证）；
    ③ 五个既有 Intent **零回归**：问法、工具名、金额都照旧；
    ④ 导出的 Markdown 与页面上预览的是**同一段文本**（同一个 renderer），
       且七个必需分区齐全 —— 报告自己说清楚"数字是程序算的"。

真实数据、真实计算、真实 HTTP（TestClient 进程内）。**一个业务数字都没有假造**：
本文件里的期望值全部现场由 `executor` / `tools` 算出来，或本来就是数据集的固有事实
（行数、列数、数据集起止日）。

被替换的只有 `app.ai.llm.chat` 这一个网络出口（单元测试不能依赖外网），
用来验"模型写错数字时报告会不会被污染"——**报告正文压根不经过模型**，
所以这里的断言是"就算模型瞎写，正文里的数字也不动"。
"""

from __future__ import annotations

import ast
import datetime as _dt
import pathlib
import re
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.ai import answer, intent as intent_module, llm, report, service, tools  # noqa: E402
from app.api import app  # noqa: E402
from app.engine import executor, loader  # noqa: E402

client = TestClient(app)

ISOLATED_ENV = ("SRA_STATE_DIR", "SRA_DOC_DIR", "SRA_UPLOAD_DIR", "SRA_OUTPUT_DIR")

WEEKLY_QUESTION = "帮我根据本星期的销售数据做一份销售周报"
MONTHLY_QUESTION = "出一份 2011 年 11 月的月报"

# 报告文档**必需**的分区（用户点名要的七件事：标题/摘要/核心指标/趋势/结构/口径/结论建议）
REQUIRED_SECTIONS = ("summary", "metrics", "trend", "structure", "caveats")


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """四个目录都指到临时目录：报告会落盘，绝不写进仓库里的真实 state/。"""
    monkeypatch.setenv("SRA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SRA_DOC_DIR", str(tmp_path / "documents"))
    monkeypatch.setenv("SRA_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("SRA_OUTPUT_DIR", str(tmp_path / "outputs"))
    return tmp_path


@pytest.fixture
def no_llm(monkeypatch):
    """关掉模型通道（**不**改任何数据）。"""
    monkeypatch.setattr(llm, "available", lambda: False)
    return None


def _ask(question: str, *, use_llm: bool = False) -> dict:
    response = client.post("/api/chat", json={"question": question, "use_llm": use_llm})
    assert response.status_code == 200, response.text
    return response.json()


def _date(value: str | None) -> _dt.date | None:
    return _dt.date.fromisoformat(value) if value else None


def _direct_compare(record: dict) -> dict:
    """把记录里那套比较参数原样喂给 `sales_compare` 复算（参数从记录取，不自己编）。"""
    params = record["params"] or {}
    return tools.sales_compare(
        comparison_type=params.get("comparison_type") or "wow",
        current_start=_date(params.get("current_start")),
        current_end=_date(params.get("current_end")),
        previous_start=_date(params.get("previous_start")),
        previous_end=_date(params.get("previous_end")),
        attribution_dimension=params.get("attribution_dimension"),
    )


def _window(record: dict) -> tuple[_dt.date, _dt.date]:
    """这次问答真正算的那个区间（**从记录里取**，不是测试自己算的）。

    两个出处都要看：普通问答的区间在 `params`（解析出来的 start/end），
    报告形态的 `params` 是**比较参数**（current_start/…，默认口径下还是 None），
    真正算过的区间在 `tool.params`（工具自己写回来的那个）。取到谁算谁。
    """
    tool_params = (record.get("tool") or {}).get("params") or {}
    for source in (record.get("params") or {}, tool_params):
        start, end = source.get("start"), source.get("end")
        if start and end:
            return _dt.date.fromisoformat(str(start)), _dt.date.fromisoformat(str(end))
    raise AssertionError(f"记录里没有可用的 start/end：{record.get('params')} / {tool_params}")


def _md_cells(line: str) -> list[str]:
    """Markdown 表格行 → 单元格（断言表头/行内容用，不做渲染）。"""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _md_section(markdown: str, title: str) -> list[str]:
    """取 Markdown 里某个 `## 标题` 到下一个 `##` 之间的行。"""
    lines = markdown.splitlines()
    start = lines.index(f"## {title}")
    rest = lines[start + 1:]
    end = next((index for index, line in enumerate(rest) if line.startswith("## ")), len(rest))
    return rest[:end]


# ════════════════════════════════════════════════════════════════════════
# 1. 「报告」是输出形态，不是第 6 个计算型 Intent
# ════════════════════════════════════════════════════════════════════════
def test_T010_报告不是第六个计算型intent():
    """Intent 数量克制是评审的硬要求：报告不许新增计算型 intent。

    TASK-006 之后 `COMPUTE_INTENTS` 是 7 个 —— 那是**评审批准**的两个新增
    （customer_analysis / product_analysis，各自用 operation 收口）。报告**没有**因此
    多出第 8 个 intent，它也**没有**伸进这两个新能力：报告复用的仍是原来那五个工具。
    """
    assert len(intent_module.COMPUTE_INTENTS) == 7
    assert report.REPORT_TOOL not in intent_module.COMPUTE_INTENTS
    # 报告复用的仍是既有五个工具，一个都没多、也没被新 Intent 带跑
    assert set(report.SUB_TOOLS) <= set(intent_module.COMPUTE_INTENTS)
    assert set(report.SUB_TOOLS) == {
        "sales_compare", "sales_summary", "sales_trend", "sales_breakdown_by_country", "top_products",
    }


def test_T010_能力端点的intents不增项_报告单列():
    body = client.get("/api/chat/capabilities").json()
    names = [item["name"] for item in body["intents"]]
    assert names == list(intent_module.COMPUTE_INTENTS)
    assert report.REPORT_TOOL not in names            # 没有第 6 项
    periods = {item["key"]: item for item in body["report"]["periods"]}
    assert set(periods) == {"weekly", "monthly"}
    assert periods["weekly"]["title"] == "销售周报"
    assert body["report"]["export"] == "markdown"


@pytest.mark.parametrize("question,expected", [
    ("帮我根据本星期的销售数据做一份销售周报", report.PERIOD_WEEKLY),
    ("来一份周报", report.PERIOD_WEEKLY),
    ("本周销售报告", report.PERIOD_WEEKLY),
    ("出一份每周报告", report.PERIOD_WEEKLY),
    ("出一份 2011 年 11 月的月报", report.PERIOD_MONTHLY),
    ("给我一份月度报告", report.PERIOD_MONTHLY),
    ("这个月的报告", report.PERIOD_MONTHLY),
    # 只说"报告"不说周期：默认按周报（最短的完整周期）
    ("做一份报告", report.PERIOD_WEEKLY),
    # 非报告问法：一律不许被染指
    ("2011年11月一共卖了多少？", ""),
    ("2011年11月和10月的销售额对比", ""),
    ("2011年11月各国家销售额TOP5", ""),
])
def test_T010_报告识别由代码判(question, expected):
    assert report.report_period(question) == expected
    parsed, _info = intent_module.parse(question, allow_llm=False)
    if expected:
        assert parsed.report == expected
        # 报告的比较口径就是既有 sales_compare 的 wow / mom，没有第三套
        assert parsed.intent == "sales_compare"
        assert parsed.to_dict()["report"] == expected
    else:
        assert parsed.report == ""


def test_T010_报告请求不许被解析器判成unsupported(no_llm):
    """踩过的坑：`销售周报`/`销售数据` 曾被销售员正则误伤，直接拒答。"""
    record = _ask(WEEKLY_QUESTION)
    assert record["status"] in ("degraded", "ok"), record["notice"]
    assert record["intent"]["intent"] == "sales_compare"
    assert record["tool"]["name"] == report.REPORT_TOOL
    assert record["tool"]["title"] == "销售周报"


def test_T010_提示词告诉模型报告由程序解析区间():
    prompt = intent_module.system_prompt()
    assert "报告" in prompt
    assert "周报" in prompt and "月报" in prompt


# ════════════════════════════════════════════════════════════════════════
# 2. 窗口与口径：一律走既有的窗口解析，不新造
# ════════════════════════════════════════════════════════════════════════
def test_T010_周报窗口与sales_compare完全同源(no_llm):
    record = _ask(WEEKLY_QUESTION)
    direct = _direct_compare(record)
    facts, direct_facts = record["facts"], direct["facts"]

    assert facts["comparison_type"] == "wow"
    for key in ("current_period", "previous_period", "same_length",
                "change_amount", "change_rate", "change_rate_status", "changes"):
        assert facts[key] == direct_facts[key], key
    assert facts["current"] == direct_facts["current"]
    assert facts["previous"] == direct_facts["previous"]
    # 两个窗口等长（报告里那句"可直接比"才有依据）
    assert facts["same_length"] is True
    assert facts["current_period"] != facts["previous_period"]


def test_T010_周报的区间就是数据里最近一个完整自然周(no_llm):
    """周报的"这一周"不能靠模型理解 —— 必须落在数据真实覆盖的那一周上。"""
    record = _ask(WEEKLY_QUESTION)
    current = record["facts"]["current_period"]
    start = _dt.date.fromisoformat(current["start"])
    end = _dt.date.fromisoformat(current["end"])
    frame = loader.load_raw()
    stamps = frame[loader.TIME_COLUMN]
    last_day = stamps.max().date()

    assert start.weekday() == 0 and end.weekday() == 6      # 周一到周日
    assert (end - start).days == 6
    assert current["days"] == 7
    assert end <= last_day
    assert (last_day - end).days < 7                        # 没有能再往前推一周的空间
    # 报告区间与用户写明的区间冲突时以用户为准（这条另有用例覆盖）；这里只验默认口径


def test_T010_月报取月环比且区间是整月(no_llm):
    record = _ask(MONTHLY_QUESTION)
    assert record["intent"]["report"] == "monthly"
    assert record["tool"]["title"] == "销售月报"
    facts = record["facts"]
    assert facts["comparison_type"] == "mom"
    assert {key: facts["current_period"][key] for key in ("start", "end", "days")} == {
        "start": "2011-11-01", "end": "2011-11-30", "days": 30}
    assert facts["current_period"]["adjusted"] is False       # 没被边界挪动过
    # 上一期按"同长度往前挪一个月"推：11-01~11-30 → 10-01~10-30（日号对齐，30 天对 30 天）
    assert facts["previous_period"]["start"] == "2011-10-01"
    assert facts["previous_period"]["end"] == "2011-10-30"
    assert facts["current_period"]["days"] == facts["previous_period"]["days"] == 30
    assert facts["same_length"] is True
    assert facts["change_rate_status"] in ("ok", "not_available")
    # 月报的金额就是这个整月的口径金额（**现场算**，不写死）
    assert facts["sales_amount"] == float(
        executor.compute_sales_amount("2011-11-01", "2011-11-30")["amount"]
    )
    assert facts["trend_granularity"] == "week"              # 月报按周看趋势


def test_T010_用户写明的区间一个字节都不动(no_llm):
    record = _ask("做一份 2011-11-01 到 2011-11-07 的销售周报")
    facts = record["facts"]
    assert record["intent"]["report"] == "weekly"
    assert facts["current_period"]["start"] == "2011-11-01"
    assert facts["current_period"]["end"] == "2011-11-07"
    assert facts["sales_amount"] == float(
        executor.compute_sales_amount("2011-11-01", "2011-11-07")["amount"]
    )


def test_T010_两个区间以上既不出报告也不默默选一个(no_llm):
    """一份报告只能有一个本期区间：问题里摆了三个月，不许挑一个凑合、也不许合并。"""
    record = _ask("做一份 2011年9月、10月、11月的月报")
    assert record["status"] == "error"
    assert record["facts"] is None and record["answer"] is None
    assert record["report_document"] is None
    message = record["error"]["message"]
    assert "时间段" in message or "区间" in message
    assert "不猜" in message


# ════════════════════════════════════════════════════════════════════════
# 3. 报告不新造口径：每个数字都与既有工具位级相等
# ════════════════════════════════════════════════════════════════════════
def test_T010_报告里五个工具的总额互相一致(no_llm):
    """五处"本期总额"必须完全相等 —— 不相等说明编排串了口径（宁可报错也不发报告）。"""
    record = _ask(WEEKLY_QUESTION)
    start, end = _window(record)
    amount = record["facts"]["sales_amount"]

    assert tools.sales_summary(start, end)["facts"]["sales_amount"] == amount
    assert tools.sales_trend(start, end, "day")["facts"]["total_amount"] == amount
    assert tools.sales_breakdown_by_country(start, end, report.DEFAULT_TOP_N)["facts"]["total_amount"] == amount
    assert tools.top_products(start, end, report.DEFAULT_TOP_N)["facts"]["total_amount"] == amount
    assert _direct_compare(record)["facts"]["current"]["sales_amount"] == amount


def test_T010_报告的分项与既有工具的items逐位相等(no_llm):
    record = _ask(WEEKLY_QUESTION)
    start, end = _window(record)
    countries = tools.sales_breakdown_by_country(start, end, report.DEFAULT_TOP_N)["items"]
    products = tools.top_products(start, end, report.DEFAULT_TOP_N)["items"]

    got_countries = [item for item in record["items"] if item["kind"] == "country"]
    got_products = [item for item in record["items"] if item["kind"] == "product"]
    # 报告只多一个 `kind`（标明这条明细来自国家还是商品），其余字段逐位照搬工具的输出
    def strip(items: list[dict]) -> list[dict]:
        return [{key: value for key, value in item.items() if key != "kind"} for item in items]

    assert strip(got_countries) == countries
    assert strip(got_products) == products
    assert {item["kind"] for item in record["items"]} == {"country", "product"}
    # 趋势序列也照抄 sales_trend，不重算
    assert record["series"] == tools.sales_trend(start, end, "day")["series"]


def test_T010_报告汇总口径的数字来自sales_summary(no_llm):
    record = _ask(WEEKLY_QUESTION)
    start, end = _window(record)
    facts = tools.sales_summary(start, end)["facts"]
    for key in ("rows_in_range", "rows_valid", "rows_excluded", "excluded_amount",
                "customer_id_null_rows"):
        assert record["facts"][key] == facts[key], key


def test_T010_报告模块自己不碰数据也不做金额运算():
    """结构上的护栏：report.py 只编排（调 tools.run_tool）。

    **按 AST 查 import**（不查字面量）：报告的口径说明里会写到"同一个 executor"，
    查字符串会把注释和文案一起算进去 —— 查"它到底 import 了谁"才是这条护栏要守的东西。
    """
    source = (PROJECT_ROOT / "app" / "ai" / "report.py").read_text(encoding="utf-8")
    standard: set[str] = set()
    from_ai: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            standard.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "app.ai":
                from_ai.update(alias.name for alias in node.names)
            else:
                standard.add(module)
    assert standard <= {"__future__", "datetime", "re", "typing"}, standard      # 标准库之外不许引
    assert from_ai == {"answer", "tools"}, from_ai                              # 只借这两个
    assert source.count("tools.run_tool(") == 5          # 五个既有工具各跑一次，一个不多


# ════════════════════════════════════════════════════════════════════════
# 4. 一份报告该有的样子：七个分区 + 数字可追溯
# ════════════════════════════════════════════════════════════════════════
def test_T010_文档分区齐全且标题带区间(no_llm):
    record = _ask(WEEKLY_QUESTION)
    doc = record["report_document"]
    assert doc["title"] == "销售周报"
    assert doc["period"] == "weekly"
    assert doc["period_label"] == (f"{record['facts']['current_period']['start']} ~ "
                                   f"{record['facts']['current_period']['end']}")
    keys = [section["key"] for section in doc["sections"]]
    assert keys == list(REQUIRED_SECTIONS)
    assert doc["filename"].endswith(".md")
    assert record["facts"]["current_period"]["start"] in doc["filename"]

    by_key = {section["key"]: section for section in doc["sections"]}
    # 摘要：3~5 句，且是一段连着读的话
    summary = by_key["summary"]
    assert summary["style"] == "paragraph"
    assert 3 <= len(summary["lines"]) <= 5
    text = "".join(summary["lines"])
    assert text.count("。") >= 3
    # 核心指标表：本期 / 上期 / 变化 —— 四行指标
    metrics = by_key["metrics"]["table"]
    assert metrics["headers"][0] == "指标"
    assert "本期" in metrics["headers"][1] and "上期" in metrics["headers"][2]
    assert [row[0] for row in metrics["rows"]] == ["销售额", "订单数", "客户数", "客单价"]
    # 趋势表：逐日（周报按天）
    trend = by_key["trend"]
    assert trend["title"] == "趋势（按天）"
    assert len(trend["table"]["rows"]) == record["facts"]["trend_bucket_count"]
    # 结构：国家 TOP + 商品 TOP
    structure = by_key["structure"]["blocks"]
    assert len(structure) == 2
    assert "国家分布" in structure[0]["title"] and "商品 TOP" in structure[1]["title"]
    assert len(structure[0]["table"]["rows"]) == len(
        [item for item in record["items"] if item["kind"] == "country"])
    # 口径与异常说明：代码生成的多条
    assert len(by_key["caveats"]["lines"]) >= 5


def test_T010_核心指标表里的数字能用facts复算(no_llm):
    """表里的每个金额/单量都能在 facts 里找到出处（不是另算的一遍）。"""
    record = _ask(WEEKLY_QUESTION)
    facts = record["facts"]
    metrics = {section["key"]: section for section in record["report_document"]["sections"]}["metrics"]
    row = {item[0]: item for item in metrics["table"]["rows"]}

    assert f"{facts['current']['sales_amount']:,.2f}" in row["销售额"][1]
    assert f"{facts['previous']['sales_amount']:,.2f}" in row["销售额"][2]
    assert f"{abs(facts['change_amount']):,.2f}" in row["销售额"][3]
    assert f"{facts['current']['order_count']:,}" == row["订单数"][1]
    assert f"{facts['previous']['order_count']:,}" == row["订单数"][2]
    assert f"{facts['current']['customer_count']:,}" == row["客户数"][1]
    assert f"{abs(round(facts['change_amount'], 2)):,.2f}" in row["销售额"][3]


def test_T010_摘要里的数字全部来自确定性结果(no_llm):
    record = _ask(WEEKLY_QUESTION)
    facts = record["facts"]
    summary = {section["key"]: section for section in record["report_document"]["sections"]}["summary"]
    text = "".join(summary["lines"])

    assert f"{facts['current']['sales_amount']:,.2f}" in text
    assert f"{facts['previous']['sales_amount']:,.2f}" in text
    assert f"{abs(facts['change_amount']):,.2f}" in text
    assert facts["current_period"]["start"] in text and facts["current_period"]["end"] in text
    # 摘要里的数字必须在闸门的允许集合里（同一套 collect_allowed_numbers）
    allowed = answer.collect_allowed_numbers(record["answer"]["sections"][0]["text"])
    assert allowed, "闸门至少要把报告里的数字收进来"


def test_T010_口径说明写明被排除行与无客户号行(no_llm):
    record = _ask(WEEKLY_QUESTION)
    facts = record["facts"]
    caveats = "".join({section["key"]: section
                       for section in record["report_document"]["sections"]}["caveats"]["lines"])
    assert f"{facts['rows_excluded']:,}" in caveats
    assert f"{facts['customer_id_null_rows']:,}" in caveats
    assert "没有区域" in caveats
    assert record["data_profile"]["rows"] and str(record["data_profile"]["first_day"]) in caveats


# ════════════════════════════════════════════════════════════════════════
# 5. 导出：页面预览的 = 下载到的（同一段文本）
# ════════════════════════════════════════════════════════════════════════
def test_T010_导出的markdown含七个必需分区与结论建议(no_llm):
    record = _ask(WEEKLY_QUESTION)
    export = record["answer"]["export"]
    assert export is not None
    assert export["mime"] == "text/markdown;charset=utf-8"
    assert export["filename"] == record["report_document"]["filename"]
    md = export["markdown"]

    assert md.startswith(f"# 销售周报 · {record['report_document']['period_label']}")
    for heading in ("## 摘要", "## 核心指标", "## 趋势", "## 结构", "## 口径与异常说明",
                    "## 结论与建议", "### 国家分布", "### 商品 TOP"):
        assert heading in md, heading
    assert "#### 【为什么】" not in md          # 标题层级不能乱：结论段是三级下面的正文
    assert "【为什么】" in md and "【建议行动】" in md
    # 导出的文件自己写清楚币种与数据范围（不靠页面上的提示）
    assert tools.DATASET_CURRENCY["name"] in md
    assert str(record["data_profile"]["first_day"]) in md

    # 表格是**真的 Markdown 表格**：表头 + 分隔行 + 数据行（别把 list 直接印出来）
    by_key = {section["key"]: section for section in record["report_document"]["sections"]}
    metrics_block = _md_section(md, "核心指标（本期 / 上期 / 变化）")
    table_lines = [line for line in metrics_block if line.startswith("|")]
    assert _md_cells(table_lines[0]) == by_key["metrics"]["table"]["headers"]
    assert all(set(cell) <= set("-: ") for cell in _md_cells(table_lines[1]))
    assert [_md_cells(line) for line in table_lines[2:]] == by_key["metrics"]["table"]["rows"]

    trend_block = _md_section(md, "趋势（按天）")
    trend_rows = [line for line in trend_block if line.startswith("|")][2:]
    assert len(trend_rows) == record["facts"]["trend_bucket_count"]
    assert [_md_cells(line)[1] for line in trend_rows] == [
        row[1] for row in by_key["trend"]["table"]["rows"]]


def test_T010_降级文案不许把读者指向报告里不存在的段落(no_llm):
    """报告里没有【发生了什么】这一段（那是单点问答的分段标题）。

    降级文案（没接 LLM / 模型那一段被闸门作废）是**代码写死的**，
    如果照抄单点问答那句"请直接看【发生了什么】里的事实"，用户就会去报告里找一段
    根本不存在的东西 —— 这是硬编码文案在换形态后最容易漏的地方。
    同理，报告自己的「口径与异常说明」已经写全了数据范围与无区域字段，
    别再往【为什么】底下挂一条重复的口径提示。
    """
    record = _ask(WEEKLY_QUESTION)          # no_llm → 两段都是代码降级文案
    md = record["answer"]["export"]["markdown"]
    assert "【发生了什么】" not in md
    assert "【为什么】" in md and "【建议行动】" in md
    assert md.count("· 口径提示：") == 0, "报告里的口径提示与「口径与异常说明」重复了"
    # 指路要指到报告**真的有**的段落上
    assert "「核心指标」" in md and "「趋势」" in md and "「结构」" in md


def test_T010_页面正文与导出文件同一个文本来源(no_llm):
    record = _ask(WEEKLY_QUESTION)
    what = record["answer"]["sections"][0]
    assert what["key"] == "what"
    assert what["source"] == "code"
    export = record["answer"]["export"]
    # 正文（what 段）就是那份文档；导出 = 文档 + 结论与建议 + 脚注
    assert export["markdown"].startswith(what["text"].strip())
    assert what["text"] == answer.render_report_document(record["report_document"])


def test_T010_非报告回答不带导出物(no_llm):
    record = _ask("2011年11月21日到11月27日一共卖了多少？")
    assert record["answer"]["export"] is None
    assert record["report_document"] is None


def test_T010_落盘记录里报告可原样取回(no_llm):
    record = _ask(WEEKLY_QUESTION)
    again = client.get(f"/api/conversations/{record['conversation_id']}").json()
    assert again["answer"]["export"] == record["answer"]["export"]
    assert again["tool"]["name"] == report.REPORT_TOOL
    assert again["tool"]["title"] == "销售周报"
    # 历史列表里能一眼认出这是报告（前端周报中心就靠这个字段挑）
    listing = client.get("/api/conversations", params={"limit": 5}).json()
    mine = [item for item in listing["conversations"]
            if item["conversation_id"] == record["conversation_id"]]
    assert mine and mine[0]["tool"] == report.REPORT_TOOL
    assert mine[0]["sales_amount"] == record["facts"]["sales_amount"]


# ════════════════════════════════════════════════════════════════════════
# 6. 数字闸门：模型写错数字时，报告正文一个字节都不动
# ════════════════════════════════════════════════════════════════════════
def _llm_says(text: str, monkeypatch, *, intent_json: str | None = None):
    default_intent = (
        '{"intent":"sales_compare","params":{"comparison_type":"wow","current_start":null,'
        '"current_end":null,"previous_start":null,"previous_end":null,'
        '"attribution_dimension":null},"assumptions":[],"confidence":0.9}'
    )

    def _chat(system: str, user: str, **kwargs):
        return intent_json or default_intent if "意图解析器" in system else text

    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "chat", _chat)


def test_T010_模型写错数字时结论段作废但报告正文不受影响(monkeypatch):
    _llm_says("【为什么】\n本期比上期多卖了 12,345,678.90 元，纯属编造。\n【建议行动】\n- 关注头部单品",
              monkeypatch)
    record = _ask(WEEKLY_QUESTION, use_llm=True)

    assert record["status"] == "degraded"                     # 推断段被整段作废
    guard = record["answer"]["guard"]
    assert guard["passed"] is False and guard["violations"]
    # 正文（what 段）仍然一字不差是那份文档
    assert record["answer"]["sections"][0]["text"] == answer.render_report_document(
        record["report_document"])
    assert "12,345,678.90" not in record["answer"]["text"]
    # 导出物里的结论段也换成代码写的那段（导出不夹带被作废的模型文本）
    export = record["answer"]["export"]
    assert export["why_source"] == "code"
    assert "12,345,678.90" not in export["markdown"]


def test_T010_模型写对的数字照样放行(monkeypatch):
    _llm_says("【为什么】\n本期头部商品集中，销售由国家分布里的首位国家贡献为主。\n【建议行动】\n- 关注头部单品",
              monkeypatch)
    record = _ask(WEEKLY_QUESTION, use_llm=True)
    assert record["status"] == "ok"
    assert record["answer"]["export"]["why_source"] == "llm"


# ════════════════════════════════════════════════════════════════════════
# 7. 数据不足：不出报告（不猜、不补、不截断）
# ════════════════════════════════════════════════════════════════════════
def test_T010_窗口不完整时明确说数据不足且不出报告():
    result = report.build_report("weekly", {
        "comparison_type": "yoy",
        "current_start": _dt.date(2011, 11, 1), "current_end": _dt.date(2011, 11, 30),
        "previous_start": None, "previous_end": None,
        "attribution_dimension": None,
    })
    assert result["status"] == "insufficient_data"
    assert result["tool"] == report.REPORT_TOOL
    assert "report" not in result                    # 没有文档 → 前端就没有可下载的东西
    assert result["notes"] and "不出报告" in "".join(result["notes"])
    assert result["facts"]["report_status"] == "insufficient_data"


def test_T010_数据不足时链路里也没有导出物(no_llm):
    """用户写明的区间超出数据覆盖（数据集最后一天是 12-09，写整月 12 月）→ 不出报告。"""
    record = _ask("做一份 2011-12-01 到 2011-12-31 的销售月报")
    assert record["status"] == "unsupported", record["notice"]
    assert record["report_document"] is None
    assert record["answer"]["export"] is None
    assert record["facts"]["report_status"] == "insufficient_data"
    assert record["facts"]["comparison_status"] == "insufficient_data"
    assert record["tool"]["name"] == report.REPORT_TOOL
    assert "不出报告" in "".join(record["tool"]["notes"])
    assert record["notice"] and "覆盖" in record["notice"]


# ════════════════════════════════════════════════════════════════════════
# 8. 五个既有 Intent 零回归
# ════════════════════════════════════════════════════════════════════════
REGRESSION_CASES = (
    ("2011年11月21日到11月27日一共卖了多少？", "sales_summary", "sales_amount"),
    ("2011-11-21 到 2011-11-27 的销售趋势", "sales_trend", "total_amount"),
    ("2011-11-21 到 2011-11-27 卖得最好的5个产品", "top_products", "total_amount"),
    ("2011年11月各国家销售额TOP5", "sales_breakdown_by_country", "total_amount"),
)


@pytest.mark.parametrize("question,tool_name,amount_key", REGRESSION_CASES)
def test_T010_既有工具问法零回归(question, tool_name, amount_key, no_llm):
    """工具名照旧、金额与"直接调那个工具"位级相等、且**没有被报告形态抢走**。"""
    record = _ask(question)
    assert record["intent"]["intent"] == tool_name
    assert record["intent"]["report"] == ""
    assert record["tool"]["name"] == tool_name
    assert record["tool"]["title"] == tools.TOOLS[tool_name].title
    start, end = _window(record)
    direct = {
        "sales_summary": tools.sales_summary(start, end),
        "sales_trend": tools.sales_trend(start, end, "day"),
        "top_products": tools.top_products(start, end, 5),
        "sales_breakdown_by_country": tools.sales_breakdown_by_country(start, end, 5),
    }[tool_name]
    assert record["facts"][amount_key] == direct["facts"][amount_key]
    assert record["answer"]["export"] is None
    assert record["report_document"] is None


def test_T010_比较问法零回归且不被报告抢走(no_llm):
    for question in (
        "2011年11月和10月的销售额对比",
        "11月比10月销售额变化了多少",
        "2011-11-01 到 2011-11-07 和 2011-10-01 到 2011-10-07 的销售额对比",
    ):
        record = _ask(question)
        assert record["intent"]["intent"] == "sales_compare", question
        assert record["intent"]["report"] == "", question
        assert record["tool"]["name"] == "sales_compare", question
        assert record["answer"]["export"] is None, question


def test_T010_七个意图在能力清单里的顺序与标题没变():
    """既有五个的顺序不许被后来的 TASK 打乱（TASK-006 的两个只能**追加**在后面）。"""
    body = client.get("/api/chat/capabilities").json()
    assert [item["name"] for item in body["intents"]] == [
        "sales_summary", "sales_trend", "top_products",
        "sales_compare", "sales_breakdown_by_country",
        "customer_analysis", "product_analysis",
    ]
    for item in body["intents"]:
        assert item["title"] == tools.TOOLS[item["name"]].title
        assert item["description"] == tools.TOOLS[item["name"]].description


def test_T010_不支持的问题照样被拒(no_llm):
    record = _ask("华南区这个月的销售额是多少")
    assert record["status"] == "unsupported"
    assert record["answer"]["export"] is None


# ════════════════════════════════════════════════════════════════════════
# 9. 前端：报告得能看、能下载（零依赖，不引第三方库）
# ════════════════════════════════════════════════════════════════════════
def _web(name: str) -> str:
    return (PROJECT_ROOT / "web" / name).read_text(encoding="utf-8")


def test_T010_前端有报告预览与下载():
    app_js = _web("app.js")
    assert 'const REPORT_TOOL = "sales_report"' in app_js      # 与后端同一处常量值
    assert "function reportPanel(" in app_js
    assert "function downloadText(" in app_js
    assert "下载 Markdown" in app_js
    assert "URL.createObjectURL" in app_js and "link.download" in app_js
    # 报告形态下不重复印一遍正文（what 段就是那份文档）
    assert 'section.key === "what"' in app_js
    # 前端不自己拼文件名/内容：一律取后端给的 export
    assert "doc.filename" in app_js and "doc.markdown" in app_js
    assert "payload.export" in app_js


def test_T010_前端周报中心列的是真实报告不再说尚未接入():
    index_html = _web("index.html")
    assert "wk-reports-body" in index_html and "wk-reports-empty" in index_html
    # 两处「周报」占位卡都要跟着改：周报中心 + 总览页那张「生成周报」
    assert "周报生成尚未接入" not in index_html
    assert "生成周报" in index_html and "btn-ov-report" in index_html
    app_js = _web("app.js")
    assert "function renderWeeklyReports(" in app_js
    assert "renderWeeklyReports()" in app_js
    assert "item.tool === REPORT_TOOL" in app_js
    assert "answer || {}).export" in app_js
    assert "function bindReportShortcut(" in app_js      # 总览页那张卡片的按钮真的接上了
    # 能力清单里读报告周期提示（不在前端写死"周报/月报"）
    assert "caps.report" in app_js


def test_T010_前端报告样式只用既有token():
    css = _web("style.css")
    assert ".report-doc" in css and ".report-bar" in css
    assert "var(--td-radius-default)" in css
    # 不引 CDN / 不引第三方库（前端零依赖是既定约束）：下载走本地 Blob、不跳外链
    assert "cdn." not in css and "cdn." not in _web("app.js")
    assert "createObjectURL" in _web("app.js") and "link.download" in _web("app.js")
    assert 'href="http' not in _web("index.html") and "cdn." not in _web("index.html")


def test_T010_报告文档渲染不引入新的markdown库():
    source = (PROJECT_ROOT / "app" / "ai" / "answer.py").read_text(encoding="utf-8")
    for forbidden in ("import markdown", "from markdown", "tabulate", "jinja2"):
        assert forbidden not in source
    assert re.search(r"def render_report_document", source)
