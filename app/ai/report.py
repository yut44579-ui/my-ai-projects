"""report.py · 报告形态（周报 / 月报）—— **组合既有白名单工具，一个计算口径都不新造**。

════════════════════════════════════════════════════════════════════════
【为什么是"输出形态"，不是第 6 个计算型 Intent（评审要求说明理由）】
════════════════════════════════════════════════════════════════════════
用户说「帮我根据本星期的销售数据做一份销售周报」，要的是**一份周报**，
而不是零散的指标卡。这件事可以有两种做法：

    做法A：新增 `weekly_report` intent + 一套"周报算法"（自己算周、自己算环比、
           自己算国家占比…）—— 于是项目里出现**第二套口径**，
           和 sales_compare / sales_trend / sales_breakdown_by_country / top_products
           算出来的数字总有一天会对不上（那天就是灾难）。
    做法B：**把"周报"当成一种输出形态**（本文件）—— 报告里的每一个数字，
           都还是那五个白名单工具算出来的，本文件**只做编排与排版**，不做计算。

选了 B。理由：
  ① 零新口径：报告里的销售额就是 `sales_compare`/`sales_summary` 的销售额
     （同一个掩码、同一个 executor），不存在"报告口径 vs 问答口径"两套数；
  ② 意图不膨胀：`COMPUTE_INTENTS` 仍然是 5 个（既有 5 个 intent 的回归面没变），
     「周报」在解析层只是 `sales_compare` + 一个 `report` 标记；
  ③ 周报 = 「按周（wow）+ 多段组合输出」，本来就不是一种新的**计算**，
     而是一种新的**呈现**。

════════════════════════════════════════════════════════════════════════
【报告区间从哪来（不由 LLM 解释日期）】
════════════════════════════════════════════════════════════════════════
报告区间一律走 `tools.resolve_compare_windows()` —— 那是**全项目唯一**的窗口推导处：
    · 周报 = `wow`：最近一个完整自然周（周一为起点）；被数据边界切断时退到最近完整周并写明；
    · 月报 = `mom`：最近一个完整自然月；
    · 用户给了明确日期 → 用用户的（程序不动它）；给了两个区间 → 明确拒绝（报告只有一个本期）。
LLM 在这条链路上**碰不到日期**（与 TASK-005 的收口闸门同一个思路）。

════════════════════════════════════════════════════════════════════════
【本文件产出的东西】
════════════════════════════════════════════════════════════════════════
    result["report"]  报告文档的**结构化**形态（标题 / 摘要 / 核心指标表 / 趋势 / 结构 / 口径）
    result["facts"]   报告级的确定性事实（数字的唯一出处，供闸门与落盘追溯）
    result["items"]   国家 TOP + 商品 TOP 的明细（落盘可追溯）
    result["series"]  趋势序列（与 sales_trend 逐字一致）
排版成文字（普通文本 / Markdown 导出）在 `answer.py` —— 本文件只管"内容"，
不管"长什么样"，这样一处内容可以有多种呈现而不会分叉出两个版本的数字。
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any

from app.ai import answer, tools

# 报告工具名：落进记录 `tool.name`（`tools.TOOLS` 里**没有**这一项 ——
# 它不是可直接 run_tool 的计算工具，而是"组合输出"，见模块开头）
REPORT_TOOL = "sales_report"

PERIOD_WEEKLY = "weekly"
PERIOD_MONTHLY = "monthly"
REPORT_PERIODS: tuple[str, ...] = (PERIOD_WEEKLY, PERIOD_MONTHLY)

PERIOD_TITLES: dict[str, str] = {
    PERIOD_WEEKLY: "销售周报",
    PERIOD_MONTHLY: "销售月报",
}

# 报告 = 「按周/月」+「多段组合」：比较类型与趋势粒度都从这一张表取，不散落到调用点
PERIOD_COMPARISON: dict[str, str] = {PERIOD_WEEKLY: "wow", PERIOD_MONTHLY: "mom"}
PERIOD_TREND_GRANULARITY: dict[str, str] = {PERIOD_WEEKLY: "day", PERIOD_MONTHLY: "week"}
PERIOD_TREND_LABEL: dict[str, str] = {PERIOD_WEEKLY: "天", PERIOD_MONTHLY: "周"}

# 报告由这几个白名单工具组合而成（顺序即文档里"结构"一节的顺序）
SUB_TOOLS: tuple[str, ...] = (
    "sales_compare",
    "sales_summary",
    "sales_trend",
    "sales_breakdown_by_country",
    "top_products",
)

DEFAULT_TOP_N = 5
_TOL = 1e-6

_COMPARE_KEYS = (
    "comparison_type",
    "current_start",
    "current_end",
    "previous_start",
    "previous_end",
    "attribution_dimension",
)

# ════════════════════════════════════════════════════════════════════════
# 「这句话是不是要一份报告」—— **代码判**（不看 LLM 的脸色）
# ════════════════════════════════════════════════════════════════════════
# 与 sales_compare 的收口闸门同一个思路：理解需求可以靠 LLM，
# 「报告请求要不要走报告形态」必须由代码说了算 —— 否则同一句话今天出报告、明天出指标卡。
REPORT_WORDS: tuple[str, ...] = ("周报", "月报", "周报告", "月报告", "报告", "汇报")
WEEKLY_WORDS: tuple[str, ...] = ("周报", "周报告", "每周", "这周", "本周", "上周", "星期")
MONTHLY_WORDS: tuple[str, ...] = ("月报", "月报告", "月度", "这个月", "本月", "上月", "上个月")
# 「11月」「2011年11月」这种月份说法（**只是提个醒**用来判断周期，真正的日期解析在 intent 层）
_MONTH_HINT_RE = re.compile(r"\d{1,2}\s*月")


def report_period(question: str) -> str:
    """问题要的是不是「一份报告」？→ `weekly` / `monthly` / `""`（不是报告）。

    默认值为什么是周报：用户只说「做一份报告」时总得给一个周期，周报是最短的完整周期、
    也是最常用的那种报告；提了月份（「11月的报告」）才按月报。
    """
    text = question or ""
    if not any(word in text for word in REPORT_WORDS):
        return ""
    weekly = any(word in text for word in WEEKLY_WORDS)
    monthly = any(word in text for word in MONTHLY_WORDS)
    if monthly and not weekly:
        return PERIOD_MONTHLY
    if weekly and not monthly:
        return PERIOD_WEEKLY
    if not weekly and not monthly:
        return PERIOD_MONTHLY if _MONTH_HINT_RE.search(text) else PERIOD_WEEKLY
    return PERIOD_WEEKLY                  # 两个都说到了（如「这个月的周报」）→ 按周报


# ════════════════════════════════════════════════════════════════════════
# 小工具（格式化一律走 answer.format_value，不在这儿另写一套；币种走 tools.currency_unit）
# ════════════════════════════════════════════════════════════════════════
def _money(value: Any) -> str:
    return f"{answer.format_value(value, 'money')}{tools.currency_unit()}"


def _money_signed(value: Any) -> str:
    return f"{answer.format_value(value, 'money_signed')}{tools.currency_unit()}"


def _int(value: Any) -> str:
    return answer.format_value(value, "int")


def _int_signed(value: Any) -> str:
    return f"{int(value):+,}"


def _rate(rate: Any, status: Any) -> str:
    """变化率：`not_available`（上一期为 0）时**不显示数字**，写人能读的原因。"""
    if status == "not_available":
        return "不适用（上期为 0）"
    return f"{answer.format_value(float(rate) * 100, 'pct_signed')}%"


def _direction(change: float) -> str:
    if change > 0:
        return "增加"
    if change < 0:
        return "减少"
    return "持平"


def _dedupe(items: list[str]) -> list[str]:
    """去掉逐字重复的口径说明（几个工具各自都带"排除取消单"那句，只留一次）。"""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = (item or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _compare_only(params: dict[str, Any]) -> dict[str, Any]:
    """只把 sales_compare 认的参数传给它（报告参数里的 period/top_n 不能漏进去）。"""
    return {key: params.get(key) for key in _COMPARE_KEYS}


# ════════════════════════════════════════════════════════════════════════
# 各分区的内容（**全部由确定性结果拼出**，不含任何 LLM 产物）
# ════════════════════════════════════════════════════════════════════════
def _summary_lines(
    *,
    current: dict[str, Any],
    previous: dict[str, Any],
    compare: dict[str, Any],
    trend: dict[str, Any],
    countries: dict[str, Any],
    products: dict[str, Any],
    label: str,
) -> list[str]:
    """摘要 3~5 句：本期整体表现。每个数字都能在 facts 里找到出处。"""
    cur, prev = compare["current"], compare["previous"]
    changes = compare["changes"]
    amount = float(compare["change_amount"])
    change_text = ("与上一期持平" if amount == 0
                   else f"{_direction(amount)} {_money(abs(amount))}")
    lines = [
        f"本期（{current['start']} ~ {current['end']}，{current['days']} 天）销售额 "
        f"{_money(cur['sales_amount'])}，上一期（{previous['start']} ~ {previous['end']}，"
        f"{previous['days']} 天）{_money(prev['sales_amount'])}，"
        f"{change_text}（{_rate(compare['change_rate'], compare['change_rate_status'])}）。",
        f"本期订单 {_int(cur['order_count'])} 单（上期 {_int(prev['order_count'])} 单，"
        f"{_int_signed(changes['order_count']['change'])} 单），"
        f"客户 {_int(cur['customer_count'])} 位（上期 {_int(prev['customer_count'])} 位，"
        f"{_int_signed(changes['customer_count']['change'])} 位），"
        f"客单价 {_money(cur['avg_order_value'])}（上期 {_money(prev['avg_order_value'])}，"
        f"{_money_signed(changes['avg_order_amount']['change'])}）。",
    ]

    trend_facts = trend["facts"]
    if trend_facts.get("max_bucket_start"):
        lines.append(
            f"本期最高的一{label}是 {trend_facts['max_bucket_start']}"
            f"（{_money(trend_facts['max_bucket_amount'])}），"
            f"最低的一{label}是 {trend_facts['min_bucket_start']}"
            f"（{_money(trend_facts['min_bucket_amount'])}）。"
        )

    country_items = countries.get("items") or []
    product_items = products.get("items") or []
    if country_items:
        top = country_items[0]
        tail = ""
        if product_items:
            best = product_items[0]
            name = f"{best['stock_code']}（{best['description']}）" if best.get("description") \
                else str(best["stock_code"])
            tail = (f"商品排行里 {name} 以 {_money(best['amount'])} 居首"
                    f"（占本期 {answer.format_value(best['share'] * 100, 'pct')}%）。")
        lines.append(
            f"国家分布上 {top['country']} 以 {_money(top['amount'])} 居首"
            f"（占本期 {answer.format_value(top['share'] * 100, 'pct')}%）。{tail}"
        )
    return lines


def _metrics_table(compare: dict[str, Any]) -> dict[str, Any]:
    """核心指标表：本期 / 上期 / 变化 / 变化率 —— 数字全部来自 sales_compare。"""
    cur, prev = compare["current"], compare["previous"]
    changes = compare["changes"]
    return {
        "headers": ["指标", f"本期（{compare['current_period']['start']} ~ "
                            f"{compare['current_period']['end']}）",
                    f"上期（{compare['previous_period']['start']} ~ "
                    f"{compare['previous_period']['end']}）", "变化", "变化率"],
        "rows": [
            ["销售额", _money(cur["sales_amount"]), _money(prev["sales_amount"]),
             _money_signed(compare["change_amount"]),
             _rate(compare["change_rate"], compare["change_rate_status"])],
            ["订单数", _int(cur["order_count"]), _int(prev["order_count"]),
             _int_signed(changes["order_count"]["change"]),
             _rate(changes["order_count"]["rate"], changes["order_count"]["rate_status"])],
            ["客户数", _int(cur["customer_count"]), _int(prev["customer_count"]),
             _int_signed(changes["customer_count"]["change"]),
             _rate(changes["customer_count"]["rate"], changes["customer_count"]["rate_status"])],
            ["客单价", _money(cur["avg_order_value"]), _money(prev["avg_order_value"]),
             _money_signed(changes["avg_order_amount"]["change"]),
             _rate(changes["avg_order_amount"]["rate"], changes["avg_order_amount"]["rate_status"])],
        ],
    }


def _trend_table(trend: dict[str, Any], label: str) -> dict[str, Any]:
    """趋势表：逐（日/周）销售额。桶的起止与"不完整"标注照抄 sales_trend 的 series。

    区间不是按桶对齐时，首尾桶只装了部分天数 —— 那个 `partial` 必须跟着数字一起出现，
    否则读者会拿半个桶去和整桶比高低（那会得出错的结论）。
    """
    rows = []
    for point in (trend.get("series") or {}).get("points") or []:
        span = str(point["period_start"])
        if point.get("period_end") and point["period_end"] != point["period_start"]:
            span = f"{point['period_start']} ~ {point['period_end']}"
        note = ""
        if point.get("partial"):
            note = (f"不完整（落在报告区间内的只有 "
                    f"{point['covered_start']} ~ {point['covered_end']}）")
        rows.append([span, _money(point["amount"]), _int(point["orders"]), note])
    headers = [f"每{label}", f"销售额（{tools.currency_unit()}）", "订单"]
    if any(row[3] for row in rows):
        headers.append("说明")           # 没有不完整的桶时不留一个空列
    else:
        rows = [row[:3] for row in rows]
    return {"headers": headers, "rows": rows}


def _structure_tables(countries: dict[str, Any], products: dict[str, Any]) -> list[dict[str, Any]]:
    """结构：国家分布 TOP N + 商品 TOP N（各自由既有工具算出，报告只搬运）。"""
    country_rows = [
        [f"#{item['rank']}", str(item["country"]), _money(item["amount"]),
         f"{answer.format_value(item['share'] * 100, 'pct')}%",
         _int(item["orders"]), _int(item["customers"])]
        for item in countries.get("items") or []
    ]
    product_rows = [
        [f"#{item['rank']}", str(item["stock_code"]), str(item["description"] or "（无描述）"),
         _money(item["amount"]), f"{answer.format_value(item['share'] * 100, 'pct')}%",
         answer.format_value(item["qty"], "qty")]
        for item in products.get("items") or []
    ]
    return [
        {
            "title": f"国家分布 TOP{len(country_rows)}（销售额降序）",
            "table": {
                "headers": ["名次", "国家", f"销售额（{tools.currency_unit()}）",
                            "占本期", "订单", "客户"],
                "rows": country_rows,
            },
        },
        {
            "title": f"商品 TOP{len(product_rows)}（按商品编号分组，销售额降序）",
            "table": {
                "headers": ["名次", "商品编号", "商品名",
                            f"销售额（{tools.currency_unit()}）", "占本期", "件数"],
                "rows": product_rows,
            },
        },
    ]


def _caveats(
    *,
    period: str,
    current: dict[str, Any],
    previous: dict[str, Any],
    compare: dict[str, Any],
    summary: dict[str, Any],
    trend: dict[str, Any],
    countries: dict[str, Any],
    products: dict[str, Any],
    profile: dict[str, Any],
    granularity: str,
    label: str,
) -> list[str]:
    """口径与异常说明 —— **代码生成**：被排除的行、无客户号的行、不完整的桶、窗口怎么定的。"""
    facts = summary["facts"]
    lines = [
        f"数据范围：{profile['first_day']} ~ {profile['last_day']}"
        f"（共 {profile['rows']:,} 行、{profile['column_count']} 列）；"
        f"数据集里**没有区域/省份/城市/门店/渠道字段**，本报告不含这类维度。",
        f"本期（{current['start']} ~ {current['end']}）落在数据集里的行：共 "
        f"{_int(facts['rows_in_range'])} 行，有效 {_int(facts['rows_valid'])} 行，"
        f"被排除 {_int(facts['rows_excluded'])} 行"
        f"（排除行金额合计 {_money(facts['excluded_amount'])}）。",
        f"客户号：本期有效行里有 {_int(facts['customer_id_null_rows'])} 行没有客户号，"
        f"它们**计入销售额但计不进客户数**（客户号为空的行不排除）。",
        f"本期区间共 {current['days']} 天，上一期共 {previous['days']} 天，"
        + ("两个窗口**等长**，可直接比。" if compare["same_length"]
           else "两个窗口**不等长**，变化额只能当参考。"),
        f"国家分布按 Country 分组、商品排行按 StockCode 分组；"
        f"国家**不是区域**（数据集里没有区域字段），两者不可互相顶替。",
    ]

    points = (trend.get("series") or {}).get("points") or []
    if granularity == "day" and current["days"] > len(points):
        lines.append(
            f"本期 {current['days']} 天里有 {current['days'] - len(points)} 天在数据里"
            f"**一行交易都没有** —— 那是当天真的没有单，不是缺数据。"
        )
    # 各工具自带的说明原样跟上（它们在各自工具里就写好了、经过评审）：
    # 聚合不补零、首尾桶 partial、口径、空窗口…都在里面 —— 这里**不重抄一遍**
    # （上面几行只写工具没覆盖的信息；逐字重复的由 `_dedupe` 去掉）。
    for extra in (compare.get("notes") or []) + (trend.get("notes") or []) \
            + (countries.get("notes") or []) + (products.get("notes") or []):
        lines.append(str(extra))
    return _dedupe(lines)


# ════════════════════════════════════════════════════════════════════════
# 入口：编排五个白名单工具 → 一份报告
# ════════════════════════════════════════════════════════════════════════
def build_report(
    period: str,
    params: dict[str, Any],
    *,
    top_n: int = DEFAULT_TOP_N,
) -> dict[str, Any]:
    """按 `period`（weekly / monthly）把五个白名单工具的确定性结果拼成一份报告。

    返回的形状与其它工具**同构**（tool/params/status/facts/items/series/notes/display/selfcheck），
    多一个 `report` 键放文档结构 —— 这样 service.py 的落盘、answer.py 的分区渲染都不用改形状。
    """
    if period not in REPORT_PERIODS:
        raise ValueError(f"不认识的报告类型：{period!r}（只支持 {list(REPORT_PERIODS)}）")

    title = PERIOD_TITLES[period]
    compare_params = _compare_only(params)
    compare = tools.run_tool("sales_compare", compare_params)
    comparison_type = str(compare_params.get("comparison_type") or PERIOD_COMPARISON[period])

    # ── 数据不足（窗口没被完整覆盖等）→ 明确拒绝，**一份报告都不出** ──────
    if compare.get("status") != "ok":
        return {
            "tool": REPORT_TOOL,
            "title": title,
            "params": {"period": period, "comparison_type": comparison_type},
            "status": "insufficient_data",
            "facts": {
                "report_period": period,
                "report_status": "insufficient_data",
                "comparison_type": comparison_type,
                "comparison_status": "insufficient_data",
            },
            "notes": list(compare.get("notes") or []) + [
                "数据不足时**不出报告** —— 不猜、不补、不偷偷截断。"
            ],
            "display": [
                {"label": "报告类型", "value": title, "unit": "", "format": "text"},
                {"label": "报告结果", "value": "数据不足，未生成报告", "unit": "", "format": "text"},
            ],
            "selfcheck": {"sub_tools": [REPORT_TOOL, "sales_compare"],
                          "compare_status": "insufficient_data"},
        }

    facts_c = compare["facts"]
    current, previous = facts_c["current_period"], facts_c["previous_period"]
    granularity = PERIOD_TREND_GRANULARITY[period]
    label = PERIOD_TREND_LABEL[period]

    # ── 复用的四个工具：窗口一律取**比较窗口解析出来的本期**（同一个窗口，五处一致）
    window = {"start": _dt_date(current["start"]), "end": _dt_date(current["end"])}
    summary = tools.run_tool("sales_summary", dict(window))
    trend = tools.run_tool("sales_trend", {**window, "granularity": granularity})
    countries = tools.run_tool("sales_breakdown_by_country", {**window, "top_n": top_n})
    products = tools.run_tool("top_products", {**window, "top_n": top_n})

    # ── 交叉对账：五个工具算的"本期总额"必须**完全相等** ─────────────────
    # 它们走的是同一套掩码 + 同一个 executor 金额；不相等说明编排错了（宁可报错也不发报告）。
    amount = float(facts_c["current"]["sales_amount"])
    totals = {
        "sales_compare": amount,
        "sales_summary": float(summary["facts"]["sales_amount"]),
        "sales_trend": float(trend["facts"]["total_amount"]),
        "sales_breakdown_by_country": float(countries["facts"]["total_amount"]),
        "top_products": float(products["facts"]["total_amount"]),
    }
    max_diff = max(abs(value - amount) for value in totals.values())
    amounts_consistent = max_diff <= max(_TOL, abs(amount) * _TOL)

    summary_lines = _summary_lines(
        current=current, previous=previous, compare=facts_c, trend=trend,
        countries=countries, products=products, label=label,
    )
    metrics_table = _metrics_table(facts_c)
    trend_table = _trend_table(trend, label)
    structure = _structure_tables(countries, products)
    caveats = _caveats(
        period=period, current=current, previous=previous, compare=facts_c,
        summary=summary, trend=trend, countries=countries, products=products,
        profile=tools.dataset_profile(), granularity=granularity, label=label,
    )

    country_items = countries.get("items") or []
    product_items = products.get("items") or []
    zero_sales_days = (current["days"] - len((trend.get("series") or {}).get("points") or [])
                       if granularity == "day" else 0)

    facts: dict[str, Any] = {
        "report_period": period,
        "report_title": title,
        "report_status": "ok",
        # 历史列表一眼看到"当时算出来多少"（api_chat._summary 取的就是这个键）
        "sales_amount": amount,
        "total_amount": amount,
        "comparison_type": comparison_type,
        "current_period": current,
        "previous_period": previous,
        "same_length": facts_c["same_length"],
        "current": facts_c["current"],
        "previous": facts_c["previous"],
        "change_amount": facts_c["change_amount"],
        "change_rate": facts_c["change_rate"],
        "change_rate_status": facts_c["change_rate_status"],
        "changes": facts_c["changes"],
        "rows_in_range": summary["facts"]["rows_in_range"],
        "rows_valid": summary["facts"]["rows_valid"],
        "rows_excluded": summary["facts"]["rows_excluded"],
        "excluded_amount": summary["facts"]["excluded_amount"],
        "customer_id_null_rows": summary["facts"]["customer_id_null_rows"],
        "trend_granularity": granularity,
        "trend_bucket_count": trend["facts"]["bucket_count"],
        "zero_sales_buckets": zero_sales_days,
        "top_n": int(top_n),
        "country_count": countries["facts"]["country_count"],
        "top_country": countries["facts"]["top_country"],
        "top_country_amount": countries["facts"]["top_country_amount"],
        "top_country_share": countries["facts"]["top_share"],
        "product_count": products["facts"]["product_count"],
        "top_product": products["facts"]["best_stock_code"],
        "top_product_amount": products["facts"]["best_amount"],
        "top_product_share": products["facts"]["top_share"],
    }

    document = {
        "period": period,
        "title": title,
        "period_label": f"{current['start']} ~ {current['end']}",
        "comparison_type": comparison_type,
        "comparison_label": answer.comparison_label(comparison_type),
        # 文件名的**主干**（不带扩展名）：导出时可以出 Word / Excel / Markdown 三种，
        # 扩展名由导出那一层按格式补（见 export_docs），这里只定"这份报告叫什么"。
        "filename": f"{title}-{current['start']}至{current['end']}",
        "sections": [
            # 摘要是一段连着读的话（`style=paragraph`），不是一串要点
            {"key": "summary", "title": "摘要", "style": "paragraph", "lines": summary_lines},
            {"key": "metrics", "title": "核心指标（本期 / 上期 / 变化）",
             "lines": [f"比较口径：{answer.comparison_label(comparison_type)}；"
                       f"两个区间的日期由程序解析，不由模型解释。"],
             "table": metrics_table},
            {"key": "trend", "title": f"趋势（按{label}）", "table": trend_table},
            {"key": "structure", "title": "结构", "blocks": structure},
            {"key": "caveats", "title": "口径与异常说明", "lines": caveats},
        ],
    }

    return {
        "tool": REPORT_TOOL,
        "title": title,
        "params": {
            **compare["params"],
            "period": period,
            "start": current["start"],
            "end": current["end"],
            "top_n": int(top_n),
            "granularity": granularity,
        },
        "status": "ok",
        "facts": facts,
        # 结构明细落盘可追溯：国家 TOP 与商品 TOP 都在（kind 区分来源）
        "items": ([{"kind": "country", **item} for item in country_items]
                  + [{"kind": "product", **item} for item in product_items]),
        "series": trend.get("series"),
        "notes": caveats,
        "display": [
            {"label": "报告类型", "value": f"{title}（{answer.comparison_label(comparison_type)}）",
             "unit": "", "format": "text"},
            {"label": "本期区间", "value": f"{current['start']} ~ {current['end']}"
                                            f"（{current['days']} 天）", "unit": "", "format": "text"},
            {"label": "上一期区间", "value": f"{previous['start']} ~ {previous['end']}"
                                              f"（{previous['days']} 天）", "unit": "", "format": "text"},
            {"label": "本期销售额", "value": amount, "unit": tools.currency_unit(), "format": "money"},
            {"label": "上一期销售额", "value": facts_c["previous"]["sales_amount"],
             "unit": tools.currency_unit(), "format": "money"},
            {"label": "销售额变化额（本期 − 上期）", "value": facts_c["change_amount"],
             "unit": tools.currency_unit(), "format": "money_signed", "derived": True},
            {"label": "销售额变化率", "value": facts_c["change_rate"] * 100,
             "unit": "%", "format": "pct_signed", "derived": True,
             "note": "不适用（上期为 0）" if facts_c["change_rate_status"] == "not_available" else ""},
            {"label": "本期订单数（去重发票号）", "value": facts_c["current"]["order_count"],
             "unit": "单", "format": "int"},
            {"label": "上一期订单数", "value": facts_c["previous"]["order_count"], "unit": "单",
             "format": "int"},
            {"label": "本期客户数（去重客户号）", "value": facts_c["current"]["customer_count"],
             "unit": "位", "format": "int"},
            {"label": "上一期客户数", "value": facts_c["previous"]["customer_count"], "unit": "位",
             "format": "int"},
            {"label": "本期客单价（销售额 ÷ 订单数）", "value": facts_c["current"]["avg_order_value"],
             "unit": tools.currency_unit(), "format": "money", "derived": True},
            {"label": "本期被排除行数", "value": summary["facts"]["rows_excluded"], "unit": "行",
             "format": "int"},
            {"label": "本期无客户号行数", "value": summary["facts"]["customer_id_null_rows"], "unit": "行",
             "format": "int"},
        ],
        "selfcheck": {
            "window_source": compare["selfcheck"].get("window_source"),
            "amount_source": "五个工具走同一套掩码 + 同一个 executor 金额，报告不自己算钱",
            "totals_by_tool": totals,
            "amounts_consistent": amounts_consistent,
            "max_abs_diff": max_diff,
            "trend_selfcheck_passed": bool(trend["selfcheck"]["bucket_sum_matches_total"]),
            "country_grouped_matches_detail": bool(countries["selfcheck"]["grouped_matches_detail"]),
            "product_grouped_matches_detail": bool(products["selfcheck"]["grouped_matches_detail"]),
            "product_top_le_total": bool(products["selfcheck"]["top_amount_le_detail"]),
            "comparison_type": comparison_type,
            "trend_granularity": granularity,
            "document_sections": [item["key"] for item in document["sections"]],
        },
        "report": document,
    }


def _dt_date(value: str) -> _dt.date:
    """报告窗口里的日期字符串 → date（工具的签名要 date，转换只在这一处做）。"""
    return _dt.date.fromisoformat(str(value))


__all__ = [
    "DEFAULT_TOP_N",
    "PERIOD_COMPARISON",
    "PERIOD_MONTHLY",
    "PERIOD_TITLES",
    "PERIOD_TREND_GRANULARITY",
    "PERIOD_TREND_LABEL",
    "PERIOD_WEEKLY",
    "REPORT_PERIODS",
    "REPORT_TOOL",
    "REPORT_WORDS",
    "SUB_TOOLS",
    "build_report",
    "report_period",
]
