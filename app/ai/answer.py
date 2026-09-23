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
from app.ai import tools as ai_tools

# 三段的小标题（前端按 key 分区渲染，不靠解析中文标题）
SECTION_WHAT = "what"
SECTION_WHY = "why"
SECTION_ACTIONS = "actions"

SECTION_TITLES = {
    SECTION_WHAT: "【发生了什么】",
    SECTION_WHY: "【为什么】",
    SECTION_ACTIONS: "【建议行动】",
}

# 数字 token：允许千分位逗号与小数点（`1,234.56` / `12` / `3.5`）。
# 前面加 `(?<![A-Za-z])` 是为了**不让 "D16" 里的 16 被当成一个数字** ——
# 否则闸门会白白放行 "16" 这个 token，模型写"16 元"就溜过去了。
_NUMBER_RE = re.compile(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?")

# 币种词：数据集声明的是**英镑**（见 `tools.DATASET_CURRENCY`）。
# 模型要是写出「元/人民币/RMB/¥」，那是**事实错误** —— 和假数字一样处理：整段作废。
# 「元」单独扫会误伤「单元/元素/多元」，所以只盯它作为**金额单位**出现的写法
# （前面跟着数字或中文数词、后面跟着括号/顿号/句读、或者直接写「（元）」）。
_FOREIGN_CURRENCY_RE = re.compile(
    r"人民币|RMB|[¥￥]"
    r"|(?:[\d０-９]|[一二三四五六七八九十百千万亿几])\s*元"
    r"|元[/）)】」、，。;；:：]|（元）|\(元\)|以元为单位"
)

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
    if style == "int":
        return f"{int(value):,}"
    if style == "qty":
        return f"{float(value):,.0f}"
    if style == "pct":
        return f"{float(value):.2f}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


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


# ════════════════════════════════════════════════════════════════════════
# 【发生了什么】—— 代码生成，数字直接来自 facts
# ════════════════════════════════════════════════════════════════════════
def render_facts_text(result: dict[str, Any], *, question: str = "") -> str:
    """把工具返回值拼成"事实"段落（**确定性的**：同一份 facts 永远同一段文字）。"""
    params = result.get("params", {})
    tool = result.get("tool", "")
    lines: list[str] = []

    if tool == "sales_summary":
        lines.append(f"统计区间：{params.get('start')} ~ {params.get('end')}（含首尾全天，口径 D16）")
    elif tool == "sales_trend":
        label = "按日" if params.get("granularity") == "day" else "按周（周一为起点）"
        lines.append(f"统计区间：{params.get('start')} ~ {params.get('end')}（含首尾全天，口径 D16）")
        lines.append(f"聚合方式：{label}")
    elif tool == "top_products":
        lines.append(f"统计区间：{params.get('start')} ~ {params.get('end')}（含首尾全天，口径 D16）")
        lines.append(f"排行口径：销售额降序取前 {params.get('top_n')} 名（按 StockCode 分组）")

    lines.append("")
    lines.extend(_display_lines(result))

    items = result.get("items") or []
    if items:
        lines.append("")
        lines.append("明细：")
        for item in items:
            lines.append(
                f"  #{item['rank']} {item['stock_code']} {item['description'] or '(无描述)'}"
                f"：{format_value(item['amount'], 'money')}{ai_tools.currency_unit()}"
                f"（{format_value(item['share'] * 100, 'pct')}%，"
                f"{format_value(item['qty'], 'qty')} 件 / {item['orders']} 单）"
            )

    points = (result.get("series") or {}).get("points") or []
    if points:
        lines.append("")
        lines.append(f"逐点明细（共 {len(points)} 个点）：")
        for point in points:
            span = point["period_start"]
            if point.get("period_end") and point["period_end"] != point["period_start"]:
                span = f"{point['period_start']} ~ {point['period_end']}"
            if point.get("partial"):
                # 桶跨越了问句区间 → 把"实际算了哪几天"写出来，不让标签误导
                span += f"（只统计 {point['covered_start']} ~ {point['covered_end']}，不完整）"
            lines.append(
                f"  {span}：{format_value(point['amount'], 'money')}{ai_tools.currency_unit()}"
                f"（{point['orders']} 单）"
            )

    notes = result.get("notes") or []
    if notes:
        lines.append("")
        lines.append("口径与说明：")
        lines.extend(f"· {note}" for note in notes)

    return "\n".join(lines).strip()


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
            "本版支持的三类问题：①某时间段卖了多少 ②某时间段的趋势 ③某时间段卖得最好的产品。",
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
        currency_words  把币种写错（写成「元/人民币」之类；数据集声明的币种见 tools）
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
- 不要写"12,345.67 英镑"，要写"销售额"；不要写"3 个"，要写"三个"（中文数字可以）。
- 不要自己算任何比例、差额、平均值 —— 你没看到原始数据，算了就是编。
- 需要引用数字时，用它的名称（如"销售额""订单数""最高的一天"）。
- 金额的币种是**英镑（GBP）**：提到金额单位只能写"英镑"，**不许写"元""人民币"**
  （数据来自英国零售商，写成人民币就是错的）。

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


def _fallback_why(reason: str, result: dict[str, Any] | None) -> str:
    """LLM 缺席或越界时的降级文案 —— **只陈述程序能确定的事实**，不假装分析过。"""
    lines = [reason]
    if result:
        check = result.get("selfcheck") or {}
        if "executor_checks_all_passed" in check:
            lines.append(
                "· 本次销售额由既有 executor 计算，其内置自检"
                f"（{len(check)} 项口径核对）{'全部通过' if check['executor_checks_all_passed'] else '**有未通过项**'}。"
            )
        if "bit_identical" in check:
            lines.append(
                "· 用同一套 D16 掩码独立复算了一遍金额，"
                f"与 executor 的输出{'逐位一致' if check['bit_identical'] else '**不一致（需排查）**'}。"
            )
        notes = result.get("notes") or []
        if notes:
            lines.append("· 口径提示：" + notes[0])
    return "\n".join(lines)


def _fallback_actions(reason: str) -> str:
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
) -> dict[str, Any]:
    """拼出最终回答：三段 + 逐段来源 + **闸门报告**。

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

    # 合法数字集合：只从**代码产生的东西**里收集
    allowed = collect_allowed_numbers(what_text, result)

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
        elif llm_error:
            reason = f"（未接 LLM：{llm_error.get('message', '')}）—— 本段不生成，以免编造。"
        else:
            reason = "（未接 LLM —— 本段本应由模型基于上面的事实做推断，这里不生成，以免编造。）"
        why_text = _fallback_why(reason, result)
        actions_text = _fallback_actions(reason)
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
                )
            else:
                why_text = _fallback_why("（模型这一段没能给出可用内容。）", result)

        if guard_report["by_section"].get(SECTION_ACTIONS, {}).get("passed") and actions_raw.strip():
            actions_text, actions_source = actions_raw.strip(), "llm"
        else:
            actions_source = "code"
            if actions_raw.strip():
                actions_text = _fallback_actions(
                    _dropped_section_reason(guard_report["by_section"].get(SECTION_ACTIONS, {}))
                )
            else:
                actions_text = _fallback_actions("（模型这一段没能给出可用内容。）")

    sections.append(_section(SECTION_WHY, why_text, why_source, inferred=why_source == "llm"))
    sections.append(_section(SECTION_ACTIONS, actions_text, actions_source))

    text = "\n\n".join(f"{item['title']}\n{item['text']}" for item in sections)
    return {"sections": sections, "text": text, "guard": guard_report}


__all__ = [
    "GUARD_POLICY",
    "LLM_SYSTEM_PROMPT",
    "SECTION_ACTIONS",
    "SECTION_TITLES",
    "SECTION_WHAT",
    "SECTION_WHY",
    "build_llm_prompt",
    "collect_allowed_numbers",
    "compose",
    "currency_guard",
    "format_value",
    "number_guard",
    "parse_llm_sections",
    "render_facts_text",
    "render_unsupported_text",
]
