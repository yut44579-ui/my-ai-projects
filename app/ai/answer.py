"""answer.py · 分区回答 + **数字核对闸门**（TASK-004 链路的最后一步）。

════════════════════════════════════════════════════════════════════════
【强制的三段结构（施工指令：前端要分区显示）】
════════════════════════════════════════════════════════════════════════
    【发生了什么】  **代码**生成 —— 逐条抄 tools.py 算出的事实，一个字都不许是"发挥"
    【为什么】      LLM 生成，**必须标明是推断**（前端也会打"推断"标签）
    【建议行动】    LLM 生成

第一段为什么不让 LLM 写：它是"事实陈述"，让 LLM 复述事实就等于给它一次**改写数字**的机会。
数字一旦经过语言模型的手，就不再可追溯了。所以这一段由本文件用模板拼，数字直接取自 facts。

════════════════════════════════════════════════════════════════════════
【数字核对闸门：这是本文件存在的最重要理由】
════════════════════════════════════════════════════════════════════════
提示词里写"不要写数字"是**请求**，不是**保证**。所以 LLM 回来的每一段都要过闸：

    1. 从**确定性文本**（代码生成的事实段 + 事实 dict 的 JSON）里，
       收集全部"合法数字"（把 `1,234.56` 与 `1234.56` 归一成同一个 token）；
    2. 扫 LLM 段落里的每一个数字 token；
    3. 只要出现一个**不在合法集合**里的数字 → 这段 LLM 输出**整段作废**，
       换成代码生成的降级文案，并把违规数字记进 `guard.violations` 一起落盘。

为什么"整段作废"而不是"把那几个数字抹掉"：抹掉之后句子还留在那儿，
读起来仍然像是模型算过的结论 —— 那比直接说"这段不可信"更糟。
作废的痕迹留在记录里，用户能看见闸门真的拦下过东西（AC-04 的取证点之一）。
"""

from __future__ import annotations

import json
import re
from typing import Any

# 货币单位**只从 tools 取**（`DATASET_CURRENCY` 是全项目唯一出处）。
# 不反向依赖：tools 不 import answer，所以这里 import 它没有环。
from app.ai import export_docs, llm
from app.ai import tools as ai_tools

# 回答分区的小标题（前端按 key 分区渲染，不靠解析中文标题）
SECTION_WHAT = "what"
# 【主要贡献】也是**代码写的**（TASK-005）：连"主要是谁"都不让 LLM 判断 ——
# 那本质上是个排序问题，而排序完全可以在代码里确定地做完。
SECTION_CONTRIBUTION = "contribution"
SECTION_WHY = "why"
SECTION_ACTIONS = "actions"

SECTION_TITLES = {
    SECTION_WHAT: "【发生了什么】",
    SECTION_CONTRIBUTION: "【主要贡献】",
    SECTION_WHY: "【为什么】",
    SECTION_ACTIONS: "【建议行动】",
}

# 数字 token：允许千分位逗号与小数点（`1,234.56` / `12` / `3.5`）。
# 前面加 `(?<![A-Za-z])` 是为了**不让 "D16" 里的 16 被当成一个数字** ——
# 否则闸门会白白放行 "16" 这个 token，模型写"16 元"就溜过去了。
_NUMBER_RE = re.compile(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?")

# 币种词：口径声明的是**人民币「元」**（见 `tools.DATASET_CURRENCY`）。
# 模型要是写出「英镑/GBP/£」这类**别的币种**，那是**事实错误** —— 和假数字一样处理：整段作废。
# （TASK-004 时方向是反的：那时声明 GBP，拦的是「元」。声明改成 CNY 后闸门跟着对称反转，
#  机制一个字没改，只是"什么算写错"跟着那唯一一处声明走。）
_FOREIGN_CURRENCY_RE = re.compile(r"英镑|英磅|GBP|GBp|[£￡]")

GUARD_POLICY = "LLM 段落里出现的每一个数字，都必须能在确定性计算结果里找到出处"


def currency_guard(text: str) -> list[str]:
    """扫一段 LLM 输出里**写错币种**的词（返回命中的词，空列表 = 干净）。

    为什么和数字闸门放在一起：这两件事是同一个规矩的两面 ——
    「LLM 只负责组织语言，事实（数字与单位）一律以确定性结果为准」。
    """
    if not text:
        return []
    return sorted(set(_FOREIGN_CURRENCY_RE.findall(text)))


# ════════════════════════════════════════════════════════════════════════
# 格式化（**只在这里**把数字变成给人看的字符串）
# ════════════════════════════════════════════════════════════════════════
def format_value(value: Any, style: str = "auto") -> str:
    if value is None:
        return "—"
    if style == "money":
        return f"{float(value):,.2f}"
    if style == "money_signed":
        # 变化额必须**带符号**：+354,517.03 / -1,234.56。
        # 少了这个 "+"，读者得自己从上下文猜方向 —— 而变化的方向正是这一段的重点。
        return f"{float(value):+,.2f}"
    if style == "int":
        return f"{int(value):,}"
    if style == "qty":
        return f"{float(value):,.0f}"
    if style == "pct":
        return f"{float(value):.2f}"
    if style == "pct_signed":
        return f"{float(value):+.2f}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


_COMPARISON_LABELS = {
    "custom": "自定义两区间",
    "wow": "周环比（本周 vs 上周）",
    "mom": "月环比（本月 vs 上月）",
    "yoy": "同比（与去年同期比）",
}


def comparison_label(value: str | None) -> str:
    return _COMPARISON_LABELS.get(str(value or ""), str(value or "—"))


# ── TASK-006：客户 / 商品分析里"这次做的是哪一种"的人话标题 ─────────────────
# 只在回答正文里出现（前端的事实表用的是后端 display，不读这里）——
# 操作名是内部词，用户看到的必须是"这是什么分析"，并且把口径一并说清。
_CUSTOMER_OPERATION_LABELS = {
    "top": "客户排行（按区间内的金额/订单数排序，排序由程序做）",
    "purchase_frequency": "购买频次（购买次数 = 客户号 + distinct 发票号，**不是行数**）",
    "repeat_rate": "复购率（复购客户 = 购买次数 ≥ 2）",
    "new_customers": "数据集内新客（**整个数据集**内首次有效购买落在目标区间）",
    "inactive_customers": "沉睡客户（规则型：参考日 − 最后一次购买日 ≥ 阈值天数，不是预测）",
}
_PRODUCT_OPERATION_LABELS = {
    "top": "商品排行（按区间内的销售额/数量/订单数排序，排序由程序做）",
    "trend": "指定商品趋势（每个商品**分别**聚合，不合并成一条序列）",
    "return": "退货 / 取消分析（「数量为负」与「取消单」两个口径**分开**给，不合并）",
}


def _display_lines(result: dict[str, Any]) -> list[str]:
    lines = []
    for item in result.get("display", []):
        unit = item.get("unit") or ""
        note = item.get("note") or ""
        suffix = "（派生）" if item.get("derived") else ""
        tail = f"　[{note}]" if note else ""
        lines.append(
            f"· {item['label']}：{format_value(item['value'], item.get('format', 'auto'))}{unit}{suffix}{tail}"
        )
    return lines


def _point_line(point: dict[str, Any]) -> str:
    """趋势里的一个点 → 一行（`partial` 的写法与销售额趋势**完全一致**）。"""
    span = point["period_start"]
    if point.get("period_end") and point["period_end"] != point["period_start"]:
        span = f"{point['period_start']} ~ {point['period_end']}"
    if point.get("partial"):
        # 桶跨越了问句区间 → 把"实际算了哪几天"写出来，不让标签误导
        span += f"（只统计 {point['covered_start']} ~ {point['covered_end']}，不完整）"
    return (
        f"  {span}：{format_value(point['amount'], 'money')}{ai_tools.currency_unit()}"
        f"（{point['orders']} 单）"
    )


def _customer_item_line(item: dict[str, Any]) -> str:
    """客户明细一行 —— 三种问法带的字段不同（沉睡带"距今天数"、新客带"首次购买"）。"""
    head = f"  #{item['rank']} 客户 {item['customer_id']}"
    if "days_since_last_purchase" in item:
        return (
            f"{head}：距参考日已 {item['days_since_last_purchase']} 天未购买"
            f"（最后一次有效购买 {item['last_purchase']}），"
            f"区间内成交 {format_value(item['sales_amount'], 'money')}{ai_tools.currency_unit()}"
            f" / {item['purchase_count']} 次购买"
        )
    if "first_purchase" in item:
        return (
            f"{head}（数据集内首次购买 {item['first_purchase']}）："
            f"{format_value(item['sales_amount'], 'money')}{ai_tools.currency_unit()}"
            f" / {item['purchase_count']} 次购买"
        )
    return (
        f"{head}：{format_value(item['sales_amount'], 'money')}{ai_tools.currency_unit()}"
        f"（{item['purchase_count']} 次购买 / {item['rows']} 行 / "
        f"平均每单 {format_value(item['avg_order_amount'], 'money')}{ai_tools.currency_unit()}）"
    )


# ════════════════════════════════════════════════════════════════════════
# 【发生了什么】—— 代码生成，数字直接来自 facts
# ════════════════════════════════════════════════════════════════════════
def render_facts_text(result: dict[str, Any], *, question: str = "") -> str:
    """把工具返回值拼成"事实"段落（**确定性的**：同一份 facts 永远同一段文字）。"""
    params = result.get("params", {})
    tool = result.get("tool", "")
    lines: list[str] = []

    # ── 报告形态（TASK-010）：正文就是那份报告文档本身 ────────────────────
    # 报告不是"几张指标卡"，所以这里直接渲染整份文档（摘要/指标表/趋势/结构/口径）。
    # 同一个函数产出的 Markdown 也用于导出（见 build_report_export）——**一份内容，两种用途**，
    # 不会出现"页面上看到的"和"下载到的"对不上。
    if result.get("report"):
        return render_report_document(result["report"])

    if tool == "sales_summary":
        lines.append(f"统计区间：{params.get('start')} ~ {params.get('end')}（含首尾全天）")
    elif tool == "sales_trend":
        label = "按日" if params.get("granularity") == "day" else "按周（周一为起点）"
        lines.append(f"统计区间：{params.get('start')} ~ {params.get('end')}（含首尾全天）")
        lines.append(f"聚合方式：{label}")
    elif tool == "top_products":
        lines.append(f"统计区间：{params.get('start')} ~ {params.get('end')}（含首尾全天）")
        lines.append(f"排行口径：销售额降序取前 {params.get('top_n')} 名（按商品编号分组）")
    elif tool == "sales_compare":
        facts = result.get("facts") or {}
        lines.append(f"比较类型：{comparison_label(facts.get('comparison_type'))}"
                     f"（两个区间的日期由程序解析，**不由模型解释**）")
        if facts.get("comparison_status") != "ok":
            lines.append("**数据不足，本次不给变化额与变化率**（不猜、不补、不偷偷截断）。")
    elif tool == "sales_breakdown_by_country":
        lines.append(f"统计区间：{params.get('start')} ~ {params.get('end')}（含首尾全天）")
        lines.append(f"分布口径：按国家分组，销售额降序取前 {params.get('top_n')} 名")
    elif tool == "customer_analysis":
        lines.append(f"统计区间：{params.get('start')} ~ {params.get('end')}（含首尾全天）")
        lines.append(
            f"分析类型：{_CUSTOMER_OPERATION_LABELS.get(str(params.get('operation')), str(params.get('operation')))}"
        )
        # **客户覆盖范围由代码说清楚**（AC-05）：覆盖率是程序算的，不是模型猜的。
        scope = (result.get("facts") or {}).get("customer_scope") or {}
        if scope:
            lines.append(
                f"客户范围：本次客户分析只覆盖**有 CustomerID** 的成交"
                f"（{scope['valid_customer_id_nonnull_rows']} 行有效成交 / "
                f"{format_value(scope['customer_scope_sales_amount'], 'money')}{ai_tools.currency_unit()}），"
                f"占该区间全部销售额 {format_value(scope['customer_scope_sales_share'] * 100, 'pct')}%；"
                f"客户号为空的 {scope['valid_customer_id_null_rows']} 行"
                f"（{format_value(scope['null_customer_sales_amount'], 'money')}{ai_tools.currency_unit()}）"
                f"仍计入销售额，但**不计入**客户数与客户维度分析。"
            )
    elif tool == "product_analysis":
        lines.append(f"统计区间：{params.get('start')} ~ {params.get('end')}（含首尾全天）")
        lines.append(
            f"分析类型：{_PRODUCT_OPERATION_LABELS.get(str(params.get('operation')), str(params.get('operation')))}"
        )
        if params.get("product_codes"):
            lines.append(f"指定商品：{'、'.join(str(code) for code in params['product_codes'])}")

    lines.append("")
    lines.extend(_display_lines(result))

    items = result.get("items") or []
    if items:
        lines.append("")
        lines.append("明细：")
        for item in items:
            if "customer_id" in item:
                lines.append(_customer_item_line(item))
                continue
            if "country" in item:
                lines.append(
                    f"  #{item['rank']} {item['country']}"
                    f"：{format_value(item['amount'], 'money')}{ai_tools.currency_unit()}"
                    f"（{format_value(item['share'] * 100, 'pct')}%，"
                    f"{item['orders']} 单 / {item['customers']} 位客户）"
                )
                continue
            if "return_rows" in item:
                lines.append(
                    f"  #{item['rank']} {item['stock_code']} {item['description'] or '(无描述)'}"
                    f"：退货/取消 {item['return_rows']} 行"
                    f"（其中数量为负 {item['negative_rows']} 行、取消单 {item['cancel_rows']} 行），"
                    f"数量 {format_value(item['return_qty'], 'qty')} 件 / "
                    f"涉及金额 {format_value(item['return_amount'], 'money')}{ai_tools.currency_unit()}"
                )
                continue
            lines.append(
                f"  #{item['rank']} {item['stock_code']} {item['description'] or '(无描述)'}"
                f"：{format_value(item['amount'], 'money')}{ai_tools.currency_unit()}"
                f"（{format_value(item['share'] * 100, 'pct')}%，"
                f"{format_value(item['qty'], 'qty')} 件 / {item['orders']} 单）"
            )

    series = result.get("series") or {}
    points = series.get("points") or []
    if points:
        lines.append("")
        lines.append(f"逐点明细（共 {len(points)} 个点）：")
        lines.extend(_point_line(point) for point in points)
    # 商品趋势：每个商品**一条独立序列**（合并成一条是另一个问题，这里不做）
    for product in series.get("products") or []:
        product_points = product.get("points") or []
        lines.append("")
        lines.append(
            f"商品 {product['stock_code']} {product['description'] or '(无描述)'} 的逐点明细"
            f"（共 {len(product_points)} 个点）："
            if product_points
            else f"商品 {product['stock_code']}：区间内没有任何有效成交，序列为空。"
        )
        lines.extend(_point_line(point) for point in product_points)

    notes = result.get("notes") or []
    if notes:
        lines.append("")
        lines.append("口径与说明：")
        lines.extend(f"· {note}" for note in notes)

    return "\n".join(lines).strip()


def _contributor_line(item: dict[str, Any]) -> str:
    name = item["name"] if not item.get("label") else f"{item['name']}（{item['label']}）"
    delta = f"{format_value(item['delta'], 'money_signed')}{ai_tools.currency_unit()}"
    if item.get("contribution_share_status") == "not_available":
        share = "贡献率不适用（总变化为 0）"
    else:
        share = f"对总变化的贡献率 {format_value(item['contribution_share_of_change'] * 100, 'pct_signed')}%"
    return f"  · {name}：{delta}（{share}）"


def render_contribution_text(result: dict[str, Any] | None) -> str:
    """【主要贡献】—— **代码生成**：名单、排序、金额、贡献率全部来自确定性结果。

    这一段存在的意义就是"不让 LLM 判断主要是谁"：
    谁上榜是 `tools._rank_contributors()` 按 |变化额| 排出来的，
    连"正贡献 / 负贡献"这两个标签都是代码贴的。LLM 只负责在【为什么】里解释可能的原因。
    没有归因（没要求归因，或归因没过一致性校验）时返回空串 —— 这一段就**不出现**。
    """
    attribution = (result or {}).get("attribution")
    if not attribution:
        return ""
    total = attribution["total_delta"]
    lines = [
        f"总变化：{format_value(total, 'money_signed')}{ai_tools.currency_unit()}"
        f"（本期销售额 − 上一期销售额）",
        f"归因维度：{attribution['dimension_field']}"
        f"（{attribution['contributor_count']} 个取值，正/负各取 |变化额| 最大的前 {attribution['top_n']} 名）",
        "",
    ]
    lines.append(f"正贡献者（共 {len(attribution['positive_contributors'])} 个）：")
    lines.extend(
        [_contributor_line(item) for item in attribution["positive_contributors"]]
        or ["  · （没有正贡献者：所有维度都在下降或持平）"]
    )
    lines.append(f"负贡献者（共 {len(attribution['negative_contributors'])} 个）：")
    lines.extend(
        [_contributor_line(item) for item in attribution["negative_contributors"]]
        or ["  · （没有负贡献者：所有维度都在上升或持平）"]
    )
    lines.append("")
    lines.append(f"说明：{ai_tools.CONTRIBUTION_DENOMINATOR_NOTE}")
    return "\n".join(lines)


def render_llm_facts(result: dict[str, Any] | None) -> str:
    """**给 LLM 的唯一输入**：事实段 + （有归因时的）主要贡献段。

    两个都是**代码产生的文本**（不是 DataFrame、不是 Series、不是原始行）。
    之所以把【主要贡献】也塞进来：让模型写【为什么】时有据可依 ——
    但名单是排好的，它只能顺着说，不能另立"最主要的是谁"。
    """
    if result is None:
        return ""
    parts = [render_facts_text(result)]
    contribution = render_contribution_text(result)
    if contribution:
        parts.append(contribution)
    return "\n\n".join(part for part in parts if part)


# ════════════════════════════════════════════════════════════════════════
# 报告文档（TASK-010）：结构化内容（report.py 给的）→ Markdown
#
# 为什么用 Markdown 当"正文"：报告是要被**导出**的东西（用户要的是"一份周报"，
# 能存下来、能发出去）。Markdown 一份内容同时满足两件事 ——
# 页面上预览的就是它，下载下来的也是它（不会"看到的"和"下载到的"两个版本）。
# 这里只做**排版**：一个数字都不产生，全部照抄 report.py 从既有工具搬来的值。
# ════════════════════════════════════════════════════════════════════════
def _md_cell(value: Any) -> str:
    """表格单元格：转义 `|`（否则一行会被拆成两列），换行压成空格。"""
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _md_table(table: dict[str, Any]) -> list[str]:
    headers = [_md_cell(item) for item in table.get("headers") or []]
    rows = table.get("rows") or []
    if not headers:
        return []
    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(_md_cell(cell) for cell in row) + " |")
    return lines


def render_report_document(report: dict[str, Any]) -> str:
    """报告文档 → Markdown。**纯排版**（内容全部来自 report.py 的确定性结果）。"""
    lines = [f"# {report.get('title', '销售报告')} · {report.get('period_label', '')}", ""]
    lines.append(
        f"> 比较口径：{report.get('comparison_label', '—')}；"
        f"报告里的数字全部由程序确定性计算得出（两区间比较 / 汇总 / 趋势 / 国家分布 / 商品排行 "
        # ⚠️ 这句里**不写内部机制的名字**（原来写的是"经过数字闸门核对"）：报告是要发出去的
        #    业务文档，读者不需要知道我们内部管那套校验叫什么 —— 说"数字全部核对过"就够了。
        f"五个既有计算能力），数字逐项核对过，文字部分不含无法追溯到计算结果的内容。"
    )
    lines.append("")

    for section in report.get("sections") or []:
        lines.append(f"## {section.get('title') or ''}")
        lines.append("")
        for block in section.get("blocks") or []:
            lines.append(f"### {block.get('title') or ''}")
            lines.append("")
            lines.extend(_md_table(block.get("table") or {}))
            lines.append("")
        if section.get("table"):
            lines.extend(_md_table(section["table"]))
            lines.append("")
        body = [str(item) for item in section.get("lines") or [] if str(item).strip()]
        if body:
            if section.get("style") == "paragraph":
                # 摘要：一段连着读的话（不是一串要点）
                lines.append("".join(body))
            else:
                lines.extend(f"- {item}" for item in body)
            lines.append("")

    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def build_report_export(
    report: dict[str, Any],
    *,
    why_text: str,
    actions_text: str,
    why_source: str,
    actions_source: str,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """把报告文档 + 【为什么】/【建议行动】拼成**可下载的那几份文件**。

    三件事在这里定下来：
      ① `markdown` —— 页面上预览的那段文本（也是 Markdown 格式下载到的内容），一个字都不重排；
      ② `formats`  —— 可下载的格式清单（Word / Excel / Markdown），**默认 Word**：
         · Word 是文档形态，双击就能打开（用户实测：默认给 .md，Windows 上没有关联程序 →
           体感是"下载不了"）；Excel 给"要拿数字继续算"的场合；
         · 三种格式的文件名由后端拼好，前端不拼文件名、不拼扩展名。
      ③ `filename` / `mime` —— 默认格式的那一份（老字段照旧在，值是新的默认格式）。

    文件内容不在这里生成：Word / Excel 由 `export_docs` 从**同一份冻结报告**渲染，
    下载接口按 conversation_id 现取现渲染（见 api_chat 的导出端点）。
    """
    currency = ai_tools.DATASET_CURRENCY
    parts = [
        render_report_document(report),
        "",
        "## 结论与建议",
        "",
        SECTION_TITLES[SECTION_WHY],
        "",
        (why_text or "").strip(),
        "",
        SECTION_TITLES[SECTION_ACTIONS],
        "",
        (actions_text or "").strip(),
        "",
        f"> 数据范围：{profile.get('first_day')} ~ {profile.get('last_day')}；"
        f"金额单位：{currency['name']}（{currency['code']}，由项目唯一声明的币种配置给出，数值未做换算）。",
    ]
    base = report.get("filename") or export_docs.base_filename(
        report.get("title", "销售报告"), report.get("period_label", "")
    )
    choices = export_docs.format_choices(base)
    default = next(item for item in choices if item["format"] == export_docs.DEFAULT_FORMAT)
    return {
        "title": report.get("title", "销售报告"),
        "period": report.get("period"),
        "period_label": report.get("period_label"),
        "filename": default["filename"],
        "mime": default["mime"],
        "default_format": export_docs.DEFAULT_FORMAT,
        "formats": choices,
        "markdown": "\n".join(parts).strip() + "\n",
        # 哪几段是模型写的、哪几段是代码写的 —— 导出的文件自己说清楚（不假装）
        "why_source": why_source,
        "actions_source": actions_source,
    }


def render_unsupported_text(reason: str, *, profile: dict[str, Any]) -> str:
    """问到了数据里没有的东西 —— 如实说明**数据长什么样**，并明确说"答不了"。"""
    columns = " / ".join(profile.get("columns", []))
    return "\n".join(
        [
            "这个问题**当前数据集回答不了**。",
            "",
            f"原因：{reason}",
            "",
            "数据集实际情况：",
            f"· 共 {profile.get('rows')} 行、{profile.get('column_count')} 列：{columns}",
            f"· 时间范围：{profile.get('first_day')} ~ {profile.get('last_day')}",
            f"· 国家 {profile.get('country_count')} 个、客户 {profile.get('customer_count')} 位、"
            f"商品编码 {profile.get('stock_code_count')} 个",
            "· **没有区域字段**（只有 Country，国别不能当区域用）",
            "",
            "本版支持：①某时间段卖了多少 ②某时间段的趋势 ③某时间段卖得最好的产品 "
            "④两个时间段比大小（含按国家/商品归因） ⑤某时间段各国销售额分布。",
        ]
    )


# ════════════════════════════════════════════════════════════════════════
# 数字核对闸门
# ════════════════════════════════════════════════════════════════════════
def _normalize_number(token: str) -> str:
    """把 `1,234.56` / `1234.56` / `1,234.560` 归一成同一个 token。"""
    text = token.replace(",", "")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def collect_allowed_numbers(*sources: Any) -> set[str]:
    """从确定性文本/dict 里收集"合法数字"集合。

    传进来的东西必须**全部是代码产生的**（事实段文本 + 工具返回的 dict）——
    这里绝不能混进 LLM 的输出，否则闸门等于没装。
    """
    allowed: set[str] = set()
    for source in sources:
        if source is None:
            continue
        text = source if isinstance(source, str) else json.dumps(source, ensure_ascii=False, default=str)
        for token in _NUMBER_RE.findall(text):
            allowed.add(_normalize_number(token))
    return allowed


def number_guard(text: str, allowed: set[str]) -> dict[str, Any]:
    """扫一段 LLM 输出里的数字与币种，返回检查报告（`passed=False` 时调用方必须丢弃这段）。

    两类越界：
        violations      编造的数字（不在确定性结果里的 token）
        currency_words  把币种写错（写成「英镑/GBP/£」之类；口径声明的币种见 tools）
    """
    if not text:
        return {"checked": True, "passed": True, "violations": [],
                "currency_words": [], "numbers_found": []}
    found = _NUMBER_RE.findall(text)
    normalized = [_normalize_number(token) for token in found]
    violations = sorted({token for token in normalized if token not in allowed})
    currency_words = currency_guard(text)
    return {
        "checked": True,
        "passed": not violations and not currency_words,
        "violations": violations,
        "currency_words": currency_words,
        "numbers_found": sorted(set(normalized)),
    }


# ════════════════════════════════════════════════════════════════════════
# LLM 的提示词（出方向：事实 → 人话）
# ════════════════════════════════════════════════════════════════════════
LLM_SYSTEM_PROMPT = """你是销售数据分析师。下面会给你一份**已经算好**的事实，以及用户的问题。
你要写两段中文分析，除此之外什么都不要写。

**最重要的规矩：绝对不要在回答里写任何阿拉伯数字（0-9）。一个都不许出现。**
- 不要写"12,345.67 元"，要写"销售额"；不要写"3 个"，要写"三个"（中文数字可以）。
- 不要自己算任何比例、差额、平均值 —— 你没看到原始数据，算了就是编。
- 需要引用数字时，用它的名称（如"销售额""订单数""最高的一天"）。
- 金额的币种是**人民币「元」（CNY）**：提到金额单位只能写"元"，**不许写"英镑""GBP""£"**。

**第二重要的规矩：你已经看到的那份事实里，如果有【主要贡献】名单，那份名单不是你写的。**
- 名单、排序、金额、贡献率都由程序算好了。你**不许**改名单、不许重排、不许补人、
  更不许另立一个"其中最主要的是 X" —— 你没资格判断谁是主要贡献者，程序已经判断完了。
- 你只能在【为什么】里解释"这些贡献者背后可能是什么原因"，且**不要复述名单和数字**。

【为什么】这一段：解释你**推断**出的原因。只写 2~4 句，务必基于给你的事实，
  不要引入事实之外的任何信息（不要提天气、节假日、竞品，除非事实里就有）。
【建议行动】这一段：给 2~4 条具体、可执行的建议，每条一行，用「- 」开头。

严格按下面的格式输出，不要加别的标题、不要加开场白、不要加结尾总结：
【为什么】
（你的推断，2~4 句）
【建议行动】
- （建议一）
- （建议二）
"""


def build_llm_prompt(question: str, facts_text: str) -> str:
    return f"用户的问题：{question}\n\n已经算好的事实（**只许用这些**）：\n{facts_text}\n\n请按要求写【为什么】和【建议行动】两段。"


_SECTION_SPLIT_RE = re.compile(r"【为什么】|【建议行动】")


def parse_llm_sections(raw: str) -> tuple[str, str]:
    """从 LLM 输出里切出（为什么, 建议行动）两段。切不出来就给空串（由调用方降级）。"""
    text = (raw or "").strip()
    if not text:
        return "", ""
    why, actions = "", ""
    parts = _SECTION_SPLIT_RE.split(text)
    markers = _SECTION_SPLIT_RE.findall(text)
    for marker, body in zip(markers, parts[1:]):
        cleaned = body.strip().strip("：:").strip()
        if marker == "【为什么】":
            why = cleaned
        else:
            actions = cleaned
    if not markers:                      # 模型没按格式来 → 整段当成"为什么"，"建议行动"留空
        why = text
    return why, actions


# ════════════════════════════════════════════════════════════════════════
# 拼装
# ════════════════════════════════════════════════════════════════════════
def _section(key: str, text: str, source: str, **extra: Any) -> dict[str, Any]:
    body = {
        "key": key,
        "title": SECTION_TITLES[key],
        "text": text,
        "source": source,                 # code | llm
    }
    body.update(extra)
    return body


def _fallback_why(reason: str, result: dict[str, Any] | None, *, is_report: bool = False) -> str:
    """LLM 缺席或越界时的降级文案 —— **只陈述程序能确定的事实**，不假装分析过。"""
    lines = [reason]
    if result:
        check = result.get("selfcheck") or {}
        if "executor_checks_all_passed" in check:
            # ⚠️ 这里**故意不写"（6 项口径核对）"**：那个数字是 selfcheck 字典的键数，
            #    既不是 executor 真正跑了几项（那在 validations.checks 里），也不在
            #    数字闸门可追溯的范围内（闸门只认 facts/selfcheck 里的**值**，
            #    键名里凑出来的数字不算）——写上去就会被 AC-04 判成"越界数字"。
            lines.append(
                "· 本次销售额由既有计算引擎算出，其内置的口径自检"
                f"{'全部通过' if check['executor_checks_all_passed'] else '**有未通过项**'}。"
            )
        if "bit_identical" in check:
            lines.append(
                "· 用同一套口径规则独立复算了一遍金额，"
                f"与计算引擎的输出{'逐位一致' if check['bit_identical'] else '**不一致（需排查）**'}。"
            )
        notes = result.get("notes") or []
        if notes and not is_report:
            # 报告形态**不抄**这一条：报告自己的「口径与异常说明」已经逐条写全了
            # （数据范围、无客户号行、没有区域字段…），再在【为什么】底下挂一条只是重复。
            lines.append("· 口径提示：" + notes[0])
    return "\n".join(lines)


def _fallback_actions(reason: str, *, is_report: bool = False) -> str:
    if is_report:
        # 报告里**没有**【发生了什么】那一段（那是单点问答的分段标题）——
        # 降级文案不能把人指到一个报告里不存在的段落去。指报告自己的三段事实。
        return (
            f"{reason}\n为避免编造，本段不生成建议 —— "
            "请直接看上面「核心指标」「趋势」「结构」几段里的事实。"
        )
    return f"{reason}\n为避免编造，本段不生成建议 —— 请直接看【发生了什么】里的事实。"


def _dropped_section_reason(report: dict[str, Any]) -> str:
    """整段作废时给用户看的原因（**说清是哪一类越界**，别让人以为只是"没内容"）。

    只写"哪一类"，**不把越界的数字/词抄进正文** —— 正文里一旦出现那个数字，
    哪怕标着"这是编的"，也等于让一个不可追溯的数混进了回答（AC-04 的读法就是这么严）。
    具体命中了什么，留在 `guard.violations` / `guard.currency_words` 里（机器可读、
    前端在核对结论那一行展示），正文只负责说"这段被作废了，因为什么"。
    """
    problems = []
    if report.get("violations"):
        problems.append("无法追溯到计算结果的数字")
    if report.get("currency_words"):
        problems.append("写错的币种")
    if not problems:
        return "（模型这一段没能给出可用内容。）"
    return (
        "（模型这一段里出现了" + "、".join(problems) +
        "，已按「LLM 只负责组织语言、事实以确定性结果为准」的规则整段作废。）"
    )


def compose(
    *,
    question: str,
    result: dict[str, Any] | None,
    llm_raw: str | None,
    llm_used: bool,
    llm_error: dict[str, Any] | None,
    unsupported_reason: str | None,
    profile: dict[str, Any],
    fallback_reason: str | None = None,
) -> dict[str, Any]:
    """拼出最终回答：分段 + 逐段来源 + **闸门报告**。

    `fallback_reason` 用于"不能问 LLM"的非降级情形（如窗口不完整导致数据不足）——
    这时【为什么】/【建议行动】要写清"为什么不生成"，而不是甩一句"未接 LLM"。

    返回的每个字段都会原样落进 `state/conversations.json` —— 事后能回答
    "这段字是谁写的""那个数字当时核过没有"。
    """
    # ── 第一段永远是代码写的 ──────────────────────────────────────────
    if unsupported_reason:
        what_text = render_unsupported_text(unsupported_reason, profile=profile)
    elif result is not None:
        what_text = render_facts_text(result, question=question)
    else:
        what_text = "没有可展示的计算结果。"

    sections = [_section(SECTION_WHAT, what_text, "code")]

    # ── 第二段【主要贡献】也是代码写的（有归因时才出现）──────────────────
    # 位置刻意放在【为什么】之前：读者先看到"谁造成了变化"（事实），
    # 再看"可能是什么原因"（推断）—— 顺序本身就说明了谁是事实、谁是猜测。
    contribution_text = render_contribution_text(result)
    if contribution_text:
        attribution = result["attribution"]
        sections.append(
            _section(
                SECTION_CONTRIBUTION,
                contribution_text,
                "code",
                contributors={
                    "dimension": attribution["dimension"],
                    "total_delta": attribution["total_delta"],
                    "positive": [item["name"] for item in attribution["positive_contributors"]],
                    "negative": [item["name"] for item in attribution["negative_contributors"]],
                    "decided_by": "代码排序（|变化额| 降序），LLM 未参与",
                },
            )
        )

    # 合法数字集合：只从**代码产生的东西**里收集
    allowed = collect_allowed_numbers(what_text, contribution_text, result)

    # 报告形态（TASK-010）的判定只认一件事：结果里有没有 report 文档块。
    # 降级文案要据此换措辞 —— 报告里没有【发生了什么】这一段，
    # 也不能再挂一条与「口径与异常说明」重复的口径提示。
    is_report = bool((result or {}).get("report"))

    # ── 第二、三段：LLM 写的（过闸），或代码降级 ────────────────────────
    guard_report: dict[str, Any] = {
        "policy": GUARD_POLICY,
        "allowed_count": len(allowed),
        "checked": False,
        "passed": True,
        "by_section": {},
        "violations": [],
        "currency_words": [],
    }
    why_text, actions_text = "", ""

    if not llm_used:
        if unsupported_reason:
            # 这一支**不是**"没接 LLM"，而是"没有可推断的事实"——别把两种原因说成一回事
            reason = "（这个问题数据里没有对应的事实可依据，因此不生成推断与建议 —— 免得编。）"
        elif fallback_reason:
            reason = fallback_reason
        elif llm_error:
            # 同 service.py：只上人话（原因原文留在记录的 llm.error 里），
            # 并且**不再重复**notice 里已经说过的「本次没有可用的模型」。
            reason = f"（{llm.user_facing_error(llm_error)} —— 本段不生成，以免编造。）"
        else:
            reason = "（本次没有可用的模型 —— 本段本应由模型基于上面的事实做推断，这里不生成，以免编造。）"
        why_text = _fallback_why(reason, result, is_report=is_report)
        actions_text = _fallback_actions(reason, is_report=is_report)
        why_source = actions_source = "code"
    else:
        why_raw, actions_raw = parse_llm_sections(llm_raw or "")
        guard_report["checked"] = True
        for key, raw_text in ((SECTION_WHY, why_raw), (SECTION_ACTIONS, actions_raw)):
            report = number_guard(raw_text, allowed)
            guard_report["by_section"][key] = report
            if not report["passed"]:
                guard_report["passed"] = False
                guard_report["violations"].extend(report["violations"])
                guard_report["currency_words"].extend(report["currency_words"])
        guard_report["violations"] = sorted(set(guard_report["violations"]))
        guard_report["currency_words"] = sorted(set(guard_report["currency_words"]))

        if guard_report["by_section"].get(SECTION_WHY, {}).get("passed") and why_raw.strip():
            why_text, why_source = why_raw.strip(), "llm"
        else:
            why_source = "code"
            if why_raw.strip():
                why_text = _fallback_why(
                    _dropped_section_reason(guard_report["by_section"].get(SECTION_WHY, {})),
                    result,
                    is_report=is_report,
                )
            else:
                why_text = _fallback_why(
                    "（模型这一段没能给出可用内容。）", result, is_report=is_report
                )

        if guard_report["by_section"].get(SECTION_ACTIONS, {}).get("passed") and actions_raw.strip():
            actions_text, actions_source = actions_raw.strip(), "llm"
        else:
            actions_source = "code"
            if actions_raw.strip():
                actions_text = _fallback_actions(
                    _dropped_section_reason(guard_report["by_section"].get(SECTION_ACTIONS, {})),
                    is_report=is_report,
                )
            else:
                actions_text = _fallback_actions(
                    "（模型这一段没能给出可用内容。）", is_report=is_report
                )

    sections.append(_section(SECTION_WHY, why_text, why_source, inferred=why_source == "llm"))
    sections.append(_section(SECTION_ACTIONS, actions_text, actions_source))

    text = "\n\n".join(f"{item['title']}\n{item['text']}" for item in sections)

    # ── 报告形态（TASK-010）：给前端的**导出物** ───────────────────────
    # 只在"这次真的出了报告"时才有（数据不足时没有 report 块，也就没有可导出的东西）。
    # 报告文档 + 【为什么】/【建议行动】拼在一起 —— 两段 LLM 文字已经被上面的闸门核过，
    # 越界的那段早已换成代码降级文案，所以导出的文件里不存在"没核过的数字"。
    # 非报告的回答 `export` 恒为 None：前端只需要判空，不用去猜"这条能不能导出"。
    export = None
    report_block = (result or {}).get("report")
    if report_block:
        export = build_report_export(
            report_block,
            why_text=why_text,
            actions_text=actions_text,
            why_source=why_source,
            actions_source=actions_source,
            profile=profile,
        )

    return {"sections": sections, "text": text, "guard": guard_report, "export": export}


__all__ = [
    "GUARD_POLICY",
    "LLM_SYSTEM_PROMPT",
    "SECTION_ACTIONS",
    "SECTION_CONTRIBUTION",
    "SECTION_TITLES",
    "SECTION_WHAT",
    "SECTION_WHY",
    "build_llm_prompt",
    "build_report_export",
    "collect_allowed_numbers",
    "comparison_label",
    "compose",
    "currency_guard",
    "format_value",
    "number_guard",
    "parse_llm_sections",
    "render_contribution_text",
    "render_facts_text",
    "render_llm_facts",
    "render_report_document",
    "render_unsupported_text",
]
