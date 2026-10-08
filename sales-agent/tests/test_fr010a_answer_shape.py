"""test_fr010a_answer_shape.py · FR-010-A 验收：轻量看板（A1/A2）与周报不过度分析（A7）。

════════════════════════════════════════════════════════════════════════
【这一组钉的是什么】
════════════════════════════════════════════════════════════════════════
    A1 简单查询（单值档）→ 单值 + **轻量看板**（销售额 / 订单数 / 客户数 / 客单价），
       不进入分析档（没有【为什么】/【建议行动】）。
    A2 具体日期查询 → **单日看板**：该日概览 + 前一天对比 + 最近几天小表 + 本月累计。
    A7 周报：真实数据 + 不过度分析（没有证据就不编【建议行动】）。

三条硬规矩，每条都在这里有个反向用例：
  ① **字段存在才显示**：数据集里没有的（成本/毛利/商品数）不许出现 —— 拿一份
     "显示行里没有客户数"的结果去建看板，看板上就必须没有客户数那张卡。
  ② **不补 0、不插值**：2011-11-26 那天数据里一行都没有 → 看板不画卡片、只给一句如实说明，
     相邻两天的值必须来自真实计算（11-25 / 11-27），不是估的。
  ③ **前端不算数**：看板带的是原始值 + 格式 + 单位，每个单元格的文字都在这儿拼好；
     前端脚本里不许出现 parseFloat / Number( / 加减乘除（静态断言）。

主数据集在这组里是真的（`tools.sales_summary` / `sales_trend`，与线上同一条路径），
不是造出来的小表 —— 看板上的每个数都拿**另一条独立计算**对过账。
"""

from __future__ import annotations

import datetime as _dt
import pathlib
import re
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.ai import answer, dashboard, service, tools  # noqa: E402

WEB = PROJECT_ROOT / "web"

#: 派单里点名的两个真实例子（数据集里 11-25 有成交、11-26 一行都没有）
DAY_OK = _dt.date(2011, 11, 25)
DAY_EMPTY = _dt.date(2011, 11, 26)

#: 数据集里**没有**的字段 —— 它们永远不许出现在看板上
NOT_IN_DATASET: tuple[str, ...] = ("成本", "毛利", "利润", "商品数", "客单价排名", "退货率")


def board_of_day(day: _dt.date) -> dict:
    """真算一次这一天的结果，再走线上同一条看板路径。"""
    result = tools.sales_summary(day, day)
    board = dashboard.build(result=result, params={"start": day.isoformat(), "end": day.isoformat()})
    assert board is not None
    return board


def display_row(result: dict, prefix: str) -> dict:
    """从结果里取那一行（与看板挑卡用的是同一份来源，便于逐位对账）。"""
    for row in result["display"]:
        if str(row["label"]).startswith(prefix):
            return row
    raise AssertionError(f"结果里没有 {prefix} 这一行")


# ════════════════════════════════════════════════════════════════════════
# ① 卡片挑选：字段存在才显示（规矩①，反向用例）
# ════════════════════════════════════════════════════════════════════════
def test_A1_只画结果里真有的字段() -> None:
    """★ 反向：把「客户数」这一行从结果里拿掉 → 看板上就必须没有这张卡（不是显示 0）。"""
    result = tools.sales_summary(DAY_OK, DAY_OK)
    trimmed = dict(result)
    trimmed["display"] = [row for row in result["display"] if not str(row["label"]).startswith("客户数")]

    board = dashboard.range_board(trimmed)
    labels = [card["label"] for card in board["cards"]]
    assert labels == ["销售额", "订单数", "客单价"], labels
    assert "客户数" not in labels


def test_A1_数据集里没有的字段永远不出现() -> None:
    """成本/毛利/商品数这类**数据集里根本没有**的字段：给它们一行也不许画成卡。"""
    result = tools.sales_summary(DAY_OK, DAY_OK)
    forged = dict(result)
    forged["display"] = list(result["display"]) + [
        {"label": f"{name}（编的）", "value": 1.0, "unit": "", "format": "money"}
        for name in NOT_IN_DATASET
    ]

    board = dashboard.range_board(forged)
    labels = " ".join(card["label"] for card in board["cards"])
    for name in NOT_IN_DATASET:
        assert name not in labels, f"看板上出现了数据里没有的字段：{name}"


def test_A1_没有可显示的卡片时不给空壳看板() -> None:
    assert dashboard.range_board({"display": []}) is None
    assert dashboard.range_board({"display": None}) is None
    assert dashboard.range_board(None) is None


def test_A1_只有sales_summary才配看板() -> None:
    """比较 / 趋势 / 排行本来就有自己的分段输出，不该再叠一张看板。"""
    other = tools.sales_summary(DAY_OK, DAY_OK)
    other = dict(other, tool="sales_trend")
    assert dashboard.build(result=other, params={"start": "2011-11-25", "end": "2011-11-25"}) is None


# ════════════════════════════════════════════════════════════════════════
# ② A1 区间看板：4 个数与后端确定性结果**逐位一致**
# ════════════════════════════════════════════════════════════════════════
def test_A1_区间看板四个数与确定性结果逐位一致() -> None:
    result = tools.sales_summary(_dt.date(2011, 11, 1), _dt.date(2011, 11, 30))
    board = dashboard.build(
        result=result, params={"start": "2011-11-01", "end": "2011-11-30"}
    )

    assert board["kind"] == "range"
    assert board["table"] is None, "区间看板不给表（表是单日看板的东西）"
    assert [card["label"] for card in board["cards"]] == list(dashboard.RANGE_METRICS)
    for card in board["cards"]:
        row = display_row(result, card["label"])
        assert card["value"] == row["value"], f"{card['label']} 与计算结果不是逐位一致"
        assert card["format"] == row["format"]
        assert card["unit"] == row["unit"]
    # 口径说明要有（且明说数据里没有的指标不会出现）
    assert any("程序计算" in note for note in board["notes"])
    assert any("成本、毛利、商品数" in note for note in board["notes"])


def test_A1_区间看板不进入分析档() -> None:
    """问"卖了多少"只给数 —— 不许顺手给【为什么】/【建议行动】。"""
    record = service.ask("2011年11月卖了多少", use_llm=False)
    keys = {section["key"] for section in record["answer"]["sections"]}
    assert keys <= {"answer", "what"}, keys
    assert "why" not in keys and "actions" not in keys
    assert record["answer"]["dashboard"]["kind"] == "range"


# ════════════════════════════════════════════════════════════════════════
# ③ A2 单日看板：该日 + 前一天 + 最近几天 + 本月累计（凡算得出的都要有）
# ════════════════════════════════════════════════════════════════════════
def test_A2_单日看板的四张卡都与计算一致() -> None:
    result = tools.sales_summary(DAY_OK, DAY_OK)
    board = board_of_day(DAY_OK)

    assert board["kind"] == "day"
    assert board["warning"] is None
    labels = [card["label"] for card in board["cards"]]
    assert labels == ["销售额", "订单数", "客户数", "前一天（11-24）"], labels

    for card in board["cards"][:3]:
        row = display_row(result, card["label"])
        assert card["value"] == row["value"] and card["format"] == row["format"]

    # 前一天那张卡：值来自 sales_trend 的 11-24（另一条独立计算路径）
    previous_point = dashboard._point_map(_dt.date(2011, 11, 24), _dt.date(2011, 11, 24))[
        "2011-11-24"
    ]
    patch = board["cards"][3]
    assert patch["value"] == previous_point["amount"]
    # 变化额 = 当天 − 前一天（由后端算好、连正负号一起给前端）
    assert patch["sub"].startswith(answer.format_value(
        result["facts"]["sales_amount"] - float(previous_point["amount"]), "money_signed"
    ))


def test_A2_最近几天小表的每个格子都与计算一致() -> None:
    board = board_of_day(DAY_OK)
    table = board["table"]
    assert table["headers"] == ["日期", "销售额", "订单数", "较前一日"]
    assert table["title"].startswith("最近 6 天")

    window_start = DAY_OK - _dt.timedelta(days=dashboard.RECENT_DAYS - 1)
    points = dashboard._point_map(window_start, DAY_OK)
    assert len(table["rows"]) == dashboard.RECENT_DAYS
    for row in table["rows"]:
        label = row["cells"][0]
        day = _dt.date(DAY_OK.year, int(label[:2]), int(label[3:]))
        point = points[day.isoformat()]
        assert row["miss"] is False
        assert row["cells"][1] == answer.format_value(point["amount"], "money")
        assert row["cells"][2] == str(point["orders"])
    # 最后一行就是被问的那一天
    assert table["rows"][-1]["cells"][1] == answer.format_value(
        tools.sales_summary(DAY_OK, DAY_OK)["facts"]["sales_amount"], "money"
    )


def test_A2_本月累计与同一个口径的汇总一致() -> None:
    board = board_of_day(DAY_OK)
    month = tools.sales_summary(_dt.date(2011, 11, 1), DAY_OK)
    expected = (
        f"本月累计（2011-11-01 ~ 2011-11-25）："
        f"{answer.format_value(month['facts']['sales_amount'], 'money')}元"
    )
    assert any(note.startswith(expected) for note in board["notes"]), board["notes"]


def test_A2_单日回答给的是这一天的数而不是整个数据集() -> None:
    record = service.ask("2011年11月25日销售额是多少", use_llm=False)
    board = record["answer"]["dashboard"]
    assert board["kind"] == "day" and board["title"] == "2011-11-25 销售概览"
    assert board["cards"][0]["label"] == "销售额"
    assert board["cards"][0]["value"] == 50822.729999999996, "这一天的销售额"
    # 单日档不许给【为什么】/【建议行动】
    keys = {section["key"] for section in record["answer"]["sections"]}
    assert keys <= {"answer", "what"}, keys


# ════════════════════════════════════════════════════════════════════════
# ④ A2 · 空档日：如实说没有，**不许补 0**（规矩②）
# ════════════════════════════════════════════════════════════════════════
def test_A2_空档日不画卡片也不给0() -> None:
    result = tools.sales_summary(DAY_EMPTY, DAY_EMPTY)
    assert result["facts"]["rows_in_range"] == 0, "这个用例的前提：这一天数据里真的没有交易行"

    board = board_of_day(DAY_EMPTY)
    assert board["cards"] == [], "不许画一张 0.00 元的卡（那读起来就是「卖了 0 元」）"
    assert board["table"] is None
    assert board["warning"] == dashboard.NO_RECORDS_WARNING
    assert "不是 0 元" in board["warning"]


def test_A2_空档日的正文如实说没有成交记录() -> None:
    result = tools.sales_summary(DAY_EMPTY, DAY_EMPTY)
    text = answer.render_direct_text(result, "2011年11月26日卖了多少")
    assert "没有任何成交记录" in text
    assert "不是 0 元" in text
    # ★ 不许出现 0.00 元这种"算出来的 0"当答案
    assert "0.00" not in text, text
    assert not re.search(r"销售额：\s*0", text), text


def test_A2_空档日旁边那两天给的是真实值不是估的() -> None:
    board = board_of_day(DAY_EMPTY)
    notes = " ".join(board["notes"])
    before = tools.sales_trend(_dt.date(2011, 11, 25), _dt.date(2011, 11, 25))
    after = tools.sales_trend(_dt.date(2011, 11, 27), _dt.date(2011, 11, 27))
    before_amount = before["series"]["points"][0]["amount"]
    after_amount = after["series"]["points"][0]["amount"]

    assert answer.format_value(before_amount, "money") in notes, notes
    assert answer.format_value(after_amount, "money") in notes, notes
    assert "不补数字、不插值" in notes


def test_A2_空档日在界外时不硬凑相邻日() -> None:
    """数据集第一天/最后一天没有"前一天/后一天" —— 不许拿别的日期顶替。"""
    first_day, last_day = tools.dataset_bounds()
    board = dashboard.day_board(last_day + _dt.timedelta(days=1), {"facts": {"rows_in_range": 0}})
    notes = " ".join(board["notes"])
    assert "没有任何成交记录" in board["warning"]
    assert board["cards"] == []
    # 界外的那一天没有相邻数据可报 —— 不许编一个日期出来
    assert not re.search(r"\d{4}-\d{2}-\d{2}", notes.replace(last_day.isoformat(), "")), notes


def test_A2_全部被口径排除时要说明不是没数据() -> None:
    """有行但全被口径排除（取消单/负数量/非正价）—— 这跟"没有数据"是两件事。"""
    board = dashboard.day_board(
        DAY_OK, {"facts": {"rows_in_range": 10, "rows_valid": 0}}
    )
    notes = " ".join(board["notes"])
    assert "全部被口径排除" in notes, notes
    assert "有效成交为 0" in notes


# ════════════════════════════════════════════════════════════════════════
# ⑤ 规矩③：前端只摆不算
# ════════════════════════════════════════════════════════════════════════
def test_A1_前端看板脚本不做任何计算() -> None:
    """★ 看板的值、格式、单位、表格文字全部由后端给 —— 前端一旦自己算，口径就分叉了。"""
    source = (WEB / "app.js").read_text(encoding="utf-8")
    start = source.index("function dashboardPanel(")
    end = source.index("function renderChatAnswer(")
    body = source[start:end]

    for forbidden in ("parseFloat", "Number(", "+=", "toFixed", "reduce("):
        assert forbidden not in body, f"前端看板里出现了计算：{forbidden}"
    # 它只做三件事：读 card.value 交给统一的格式化、摆文本、摆备注
    assert "fmtByStyle(card.value, card.format)" in body
    assert "card.unit" in body


def test_A1_看板契约字段齐全() -> None:
    board = board_of_day(DAY_OK)
    assert set(board) == {"kind", "title", "cards", "table", "notes", "warning"}
    for card in board["cards"]:
        assert set(card) <= {"label", "value", "format", "unit", "sub"}
        assert card["format"] in {"money", "money_signed", "int", "pct_signed", "qty", "auto"}
        assert card["unit"], "金额/计数都要带单位（前端不写死币种）"


# ════════════════════════════════════════════════════════════════════════
# ⑥ A7 周报：真实数据 + 不过度分析
# ════════════════════════════════════════════════════════════════════════
def _window_of(label: str) -> tuple[_dt.date, _dt.date]:
    match = re.search(r"(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})", label)
    assert match, label
    return _dt.date.fromisoformat(match.group(1)), _dt.date.fromisoformat(match.group(2))


def test_A7_周报给的是真实数据且四件事都在() -> None:
    """派单点名要有：本周销售额 / 与上一周期对比 / 销售趋势 / 主要变化 —— 都来自真实计算。"""
    record = service.ask("帮我生成一份销售周报", use_llm=False)
    assert record["status"] in {"ok", "degraded"}, record["status"]
    document = record["report_document"]
    assert document and document["period"] == "weekly"

    titles = [section["title"] for section in document["sections"]]
    assert "摘要" in titles and "核心指标（本期 / 上期 / 变化）" in titles, titles
    assert any("趋势" in title for title in titles), titles

    # 本期 / 上期两个窗口的销售额：窗口从**文档自己写出来的表头**里取，
    # 再拿同一套工具独立算一遍对账 —— 不用 record 里的 params（那等于自己跟自己比）。
    metrics = next(section for section in document["sections"] if section["key"] == "metrics")
    this_start, this_end = _window_of(metrics["table"]["headers"][1])
    prev_start, prev_end = _window_of(metrics["table"]["headers"][2])
    lines = [line for section in document["sections"] for line in section.get("lines", [])]
    rows = [row for section in document["sections"] for row in (section.get("table") or {}).get("rows", [])]
    whole = "\n".join(lines + [" ".join(map(str, row)) for row in rows])
    this_amount = tools.sales_summary(this_start, this_end)["facts"]["sales_amount"]
    previous_amount = tools.sales_summary(prev_start, prev_end)["facts"]["sales_amount"]
    assert answer.format_value(this_amount, "money") in whole, "本周销售额与计算结果对不上"
    assert answer.format_value(previous_amount, "money") in whole, "上一期的数与计算结果对不上"


def test_A7_周报没有证据就不编建议() -> None:
    """没有模型参与时【建议行动】必须**不生成**，而不是硬写一段听着像建议的话。"""
    record = service.ask("帮我生成一份销售周报", use_llm=False)
    actions = [
        section for section in record["answer"]["sections"] if section["key"] == "actions"
    ]
    assert actions, "报告档的分段结构不变（建议这一段要在，只是内容如实说明不生成）"
    text = actions[0]["text"]
    assert "不生成" in text, text
    # ★ 这一段里**一个数字都不许有**：建议类文字一旦带上数字，读起来就成了数据结论
    assert not re.search(r"\d", text), f"「不生成」的那一段里出现了数字：{text}"


def test_A7_周报不进入逐日之外的花哨分析() -> None:
    """A7 的反向：报告文档的分段**就是这几个**，不因为"是报告"就无限加料。"""
    record = service.ask("帮我生成一份销售周报", use_llm=False)
    keys = [section["key"] for section in record["report_document"]["sections"]]
    assert keys == ["summary", "metrics", "trend", "structure", "caveats"], keys


__all__ = ["board_of_day", "display_row"]
