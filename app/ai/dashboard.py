"""dashboard.py · 轻量看板（FR-010-A1 / A2）。

════════════════════════════════════════════════════════════════════════
【它解决的问题：单值回答太"干"，但也不该因此套一整套分析模板】
════════════════════════════════════════════════════════════════════════
用户问「这个月卖了多少」时，FR-007 的单值档只回**一行字**（一个值，没有别的）。
这是对的 —— 但它把"这一段时间的规模"这件事也一起省掉了：订单多少、客户多少、
客单价多少，都是同一批确定性结果里**本来就有**的数，只是没地方显示。

FR-010-A 的裁决是：**给一张轻量看板**（KPI 行），**但不进入分析档**
（不给【主要贡献】/【为什么】/【建议行动】/趋势分析 —— 那些只在用户问"为什么"时才有）。

════════════════════════════════════════════════════════════════════════
【三条硬规矩（每一条都在代码里，不靠自觉）】
════════════════════════════════════════════════════════════════════════
① **字段存在才显示**：卡片从确定性结果的 `display` 行里**按标签前缀挑**——
   挑不到就不画。数据集里没有成本/毛利/商品数，所以它们永远不会出现在看板上
   （不是"这里写死不要"，而是"后端结果里根本没有这一行"）。

② **不补 0、不插值**：某一天在数据里一行都没有时，看板**不画卡片**
   （画一张 0.00 元的卡片就等于告诉用户"那天卖了 0 元"），只给一句"这一天没有成交记录"，
   外加相邻两天**真有的**值（真没有也说没有）。

③ **前端不算数**：卡片带的是确定性结果里的原始数值 + 格式 + 单位，
   表格里的每个单元格都是这里用 `answer.format_value` 拼好的字符串。
   前端只负责画，不做任何加减乘除，也不写死币种。

════════════════════════════════════════════════════════════════════════
【数字从哪来（口径一个字都没新造）】
════════════════════════════════════════════════════════════════════════
    A1 区间看板   直接用**本次已经算出来的**那份 sales_summary 结果（不重算）
    A2 单日看板   当天 = 同一份结果；前一天/最近几天 = `tools.sales_trend`（D16 同一套掩码）；
                  本月累计 = `tools.sales_summary`（同一个函数、同一个口径）

即：看板上的每个数都来自**既有白名单工具**的输出，本文件只做"挑哪几行、怎么排版"。
它不 import executor / metrics，也不自己 groupby —— 没有第二套算法可言。
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Iterable

from app.ai import answer as answer_module
from app.ai import tools

#: 区间看板要哪几张卡（顺序即显示顺序）。
#: 这些标签**必须是后端 display 行真实存在的标签前缀**，否则挑不到、也就不显示。
RANGE_METRICS: tuple[str, ...] = ("销售额", "订单数", "客户数", "客单价")

#: 单日看板的三张卡（参考图的单日看板就是这三个 —— 第四个位置留给"前一天"）
DAY_METRICS: tuple[str, ...] = ("销售额", "订单数", "客户数")

#: 单日看板的"最近几天"看几天（含当天）
RECENT_DAYS = 6

#: 空档日（数据里整天没有交易行）必须**如实说**，不许补 0 —— 这句话是 FR-010-A2 的验收点。
NO_RECORDS_WARNING = (
    "这一天在数据里没有任何成交记录（不是 0 元 —— 是数据源当天没有交易行）。"
    "所以我不能给你销售额，也不能把 0 当成「卖出去 0 元」。"
)

_CURRENCY_NOTE = "口径：含首尾全天；排除取消单（单号以 C 开头）、数量≤0、单价≤0 的行。"


def _currency() -> str:
    """金额单位（**只从 tools 的声明取**，本文件不写「元」这个字面量）。"""
    return tools.currency_unit()


def _cards_from_display(
    display: Iterable[dict[str, Any]] | None, wanted: tuple[str, ...] = RANGE_METRICS
) -> list[dict[str, Any]]:
    """从确定性结果的 display 行里挑出看板卡片 —— **挑不到就不画**（规矩①）。

    为什么按"标签前缀"而不是按 key 取：display 行是既有工具产出的（`销售额` /
    `订单数（去重发票号）` / `客单价（销售额 ÷ 订单数）`…），本文件不引第二份 key 表，
    也不改工具的输出形状 —— 工具的 display 变了，看板跟着变，不会两边漂移。
    """
    rows = list(display or [])
    cards: list[dict[str, Any]] = []
    for prefix in wanted:
        for row in rows:
            if str(row.get("label") or "").startswith(prefix):
                cards.append(
                    {
                        "label": prefix,
                        "value": row.get("value"),
                        "format": row.get("format") or "auto",
                        "unit": row.get("unit") or "",
                    }
                )
                break
    return cards


def _point_map(window_start: _dt.date, window_end: _dt.date) -> dict[str, dict[str, Any]]:
    """`{日期: 逐日点}`（走既有的 sales_trend：同一套掩码、同一套分桶）。

    `sales_trend` 的语义是**空档不补点**（见它的 notes）—— 这里**照单全收**：
    "某天不在 map 里"就是"那天没有成交记录"，正是看板要如实表达的东西。
    """
    series = tools.sales_trend(window_start, window_end, "day")
    points = (series.get("series") or {}).get("points") or []
    return {str(point.get("period_start")): point for point in points}


def _delta_text(delta: float, previous: float) -> str:
    """变化额 + 变化率的人话（上一期为 0 时**不给变化率** —— 那会除出一个假数）。"""
    amount = f"{answer_module.format_value(delta, 'money_signed')}{_currency()}"
    if not previous:
        return f"{amount}（上一期是 0，变化率不适用）"
    return f"{amount}（{answer_module.format_value(delta / previous * 100, 'pct_signed')}%）"


# ════════════════════════════════════════════════════════════════════════
# A1 · 区间看板（「这个月卖了多少」这类）
# ════════════════════════════════════════════════════════════════════════
def range_board(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """区间看板：只把**本次已经算出来的**那几个数摆成卡片（一个数都不重算）。

    没有可显示的卡片时返回 None —— 后端就不生成看板，而不是给一张空壳。
    """
    cards = _cards_from_display((result or {}).get("display"))
    if not cards:
        return None
    return {
        "kind": "range",
        "title": "",
        "cards": cards,
        "table": None,
        "notes": [
            "看板上的数都来自程序计算（" + _CURRENCY_NOTE.rstrip("。") + "）。"
            "数据里没有的指标（成本、毛利、商品数）不会出现在这里。"
        ],
        "warning": None,
    }


# ════════════════════════════════════════════════════════════════════════
# A2 · 单日看板（「2011年11月25日销售额是多少」这类）
# ════════════════════════════════════════════════════════════════════════
def _recent_table(
    day: _dt.date, window_start: _dt.date, points: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """最近几天的小表：日期 / 销售额 / 订单数 / 较前一日。

    没有成交记录的那一天写成「无成交记录」——**不是 0**（规矩②）。
    表格里的每个单元格都在这里拼好，前端只写文本。
    """
    rows: list[dict[str, Any]] = []
    cursor = window_start
    while cursor <= day:
        point = points.get(cursor.isoformat())
        if point is None:
            rows.append(
                {"cells": [f"{cursor:%m-%d}", "无成交记录", "—", "—"], "miss": True}
            )
        else:
            previous = points.get((cursor - _dt.timedelta(days=1)).isoformat())
            delta = "—"
            if previous is not None:
                delta = answer_module.format_value(
                    float(point["amount"]) - float(previous["amount"]), "money_signed"
                )
            rows.append(
                {
                    "cells": [
                        f"{cursor:%m-%d}",
                        answer_module.format_value(point["amount"], "money"),
                        f"{point['orders']}",
                        delta,
                    ],
                    "miss": False,
                }
            )
        cursor += _dt.timedelta(days=1)
    return {
        "title": f"最近 {len(rows)} 天（{window_start:%m-%d} ~ {day:%m-%d}）",
        "headers": ["日期", "销售额", "订单数", "较前一日"],
        "rows": rows,
    }


def _neighbour_note(day: _dt.date) -> str:
    """空档日旁边那两天**真有的**值（真没有就说没有）—— 参考图的"相邻…不补数字"那句。"""
    first_day, last_day = tools.dataset_bounds()
    window_start = max(day - _dt.timedelta(days=1), first_day)
    window_end = min(day + _dt.timedelta(days=1), last_day)
    try:
        points = _point_map(window_start, window_end)
    except Exception:                                     # noqa: BLE001 —— 算不出来就说算不出来
        return "相邻两天的数这次没能算出来 —— 缺的那天如实说缺，不补数字、不插值。"
    described: list[str] = []
    for offset, word in ((-1, "前一天"), (1, "后一天")):
        neighbour = day + _dt.timedelta(days=offset)
        if neighbour < first_day or neighbour > last_day:
            continue
        point = points.get(neighbour.isoformat())
        if point is None:
            described.append(f"{word}（{neighbour:%m-%d}）也没有成交记录")
        else:
            described.append(
                f"{word}（{neighbour:%m-%d}）是 "
                f"{answer_module.format_value(point['amount'], 'money')}{_currency()}"
            )
    if not described:                                     # 前后都不在数据范围内 → 没什么可说的
        return ""
    return "相邻的 " + "、".join(described) + " —— 缺的那天如实说缺，不补数字、不插值。"


def day_board(day: _dt.date, result: dict[str, Any] | None) -> dict[str, Any]:
    """单日看板：该日概览 + 前一天对比 + 最近几天的表 + 本月累计（算得出才给）。

    ★ 该日**没有任何成交记录**时只给一句如实说明（不画卡片、不画表、不补 0）。
    """
    facts = dict((result or {}).get("facts") or {})
    title = f"{day.isoformat()} 销售概览"

    if int(facts.get("rows_in_range") or 0) == 0:
        neighbour = _neighbour_note(day)
        return {
            "kind": "day",
            "title": title,
            "cards": [],
            "table": None,
            "notes": [neighbour] if neighbour else [],
            "warning": NO_RECORDS_WARNING,
        }

    notes: list[str] = []
    cards = _cards_from_display((result or {}).get("display"), DAY_METRICS)

    first_day, last_day = tools.dataset_bounds()
    window_start = max(day - _dt.timedelta(days=RECENT_DAYS - 1), first_day)
    window_end = min(day + _dt.timedelta(days=1), last_day)
    points: dict[str, dict[str, Any]] = {}
    try:
        points = _point_map(window_start, window_end)
    except Exception:                                     # noqa: BLE001
        notes.append("最近几天的趋势这次没能算出来，所以这里不显示它（不补、不猜）。")

    # ── 前一天（真能算出来才显示这一张卡）────────────────────────────
    previous_day = day - _dt.timedelta(days=1)
    previous = points.get(previous_day.isoformat())
    if previous is not None:
        amount = float(facts.get("sales_amount") or 0.0)
        cards.append(
            {
                "label": f"前一天（{previous_day:%m-%d}）",
                "value": previous["amount"],
                "format": "money",
                "unit": _currency(),
                "sub": _delta_text(amount - float(previous["amount"]), float(previous["amount"])),
            }
        )
    elif previous_day >= first_day:
        notes.append(f"前一天（{previous_day.isoformat()}）在数据里也没有成交记录，算不出变化额。")

    # ── 有效成交为 0 但当天其实有行（全被口径排除）——如实说明，别让人以为是"没数据"──
    if int(facts.get("rows_valid") or 0) == 0 and int(facts.get("rows_in_range") or 0) > 0:
        notes.append(
            f"当天在数据里有 {int(facts['rows_in_range'])} 行记录，但全部被口径排除"
            "（取消单 / 数量≤0 / 单价≤0），有效成交为 0。"
        )

    table = _recent_table(day, window_start, points) if points else None

    # ── 本月累计（到该日为止；算不出来就不给）─────────────────────────
    month_start = max(day.replace(day=1), first_day)
    try:
        month = tools.sales_summary(month_start, day)
        month_facts = month.get("facts") or {}
        notes.append(
            f"本月累计（{month_start.isoformat()} ~ {day.isoformat()}）："
            f"{answer_module.format_value(month_facts.get('sales_amount'), 'money')}{_currency()}"
            f" / {answer_module.format_value(month_facts.get('order_count'), 'int')} 单"
        )
    except Exception:                                     # noqa: BLE001
        notes.append("本月累计这次没能算出来，所以这里不显示它（不补、不猜）。")

    notes.append(_CURRENCY_NOTE)
    return {
        "kind": "day",
        "title": title,
        "cards": cards,
        "table": table,
        "notes": notes,
        "warning": None,
    }


# ════════════════════════════════════════════════════════════════════════
# 入口：单值档（direct）统一从这里取看板
# ════════════════════════════════════════════════════════════════════════
def _as_date(value: Any) -> _dt.date | None:
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str) and value:
        try:
            return _dt.date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def build(
    *, result: dict[str, Any] | None, params: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """单值档的看板：**单日 → 单日看板；其余 → 区间看板**（都不是就 None）。

    只认 `sales_summary` 的结果：那是"某段时间卖了多少"这一类问题算出来的东西。
    别的工具的结果（比较/趋势/排行）本来就有自己的分段输出，不该再叠一张看板。
    """
    if not result or (result or {}).get("tool") != "sales_summary":
        return None
    start = _as_date((params or {}).get("start"))
    end = _as_date((params or {}).get("end"))
    if start is None or end is None:
        return range_board(result)
    if start == end:
        return day_board(start, result)
    return range_board(result)


__all__ = [
    "DAY_METRICS",
    "NO_RECORDS_WARNING",
    "RANGE_METRICS",
    "RECENT_DAYS",
    "build",
    "day_board",
    "range_board",
]
