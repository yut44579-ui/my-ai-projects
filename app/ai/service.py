"""service.py · 编排：一句话问进来 → 一整条可追溯的链路留出去（TASK-004）。

════════════════════════════════════════════════════════════════════════
【一次问答的完整链路（每一步的产物都留在记录里）】
════════════════════════════════════════════════════════════════════════
    1. 用户问题                         question（原话，不改写）
    2. LLM 解析 → Intent JSON           intent + assumptions + confidence + 解析来源
    3. Schema 校验（代码）               validated params（非法就在这里断掉）
    4. 白名单工具 → 确定性计算           tool.name + params + facts（**数字的唯一出处**）
    5. 事实 → 分区回答                   【发生了什么】= 代码；【为什么】/【建议行动】= LLM（过闸）
    6. 落盘 `state/conversations.json`   走 Repository 抽象（不经 state.py 直写文件）

前端就是按这六步分区显示的，所以记录的形状 = 页面上看到的形状（**不多也不少**）。

════════════════════════════════════════════════════════════════════════
【四种 status —— 前端的三态就是靠它分支】
════════════════════════════════════════════════════════════════════════
    ok           算出来了，且【为什么】/【建议行动】由 LLM 写成
    degraded     算出来了，但 LLM 缺席或越界 → 那两段是代码写的（**明确标注，不假装**）
    unsupported  问的是数据里没有的维度 → 不计算，如实说明数据长什么样
    error        没解析出来 / 工具算失败 → **不猜、不编**，给出可读的原因

为什么 unsupported / error 也返回 200 而不是 4xx：
    这两种都是"请求被正确处理了，只是答案是'我答不了'"。用户问了一个问题、
    我们诚实地回了"答不了" —— 这是一次**有效的问答**，该留在会话历史里（AC-06），
    也该让前端把整条链路展示出来（Intent 为 null 也是链路的一部分）。
    4xx 属于"请求本身有问题"（比如空问题），那才走 api.py 的统一错误处理器。
"""

from __future__ import annotations

import dataclasses
import datetime as _dt

from typing import Any

from app import state
from app.ai import (
    answer,
    arithmetic,
    dashboard,
    doc_qa,
    general,
    intent as intent_module,
    llm,
    region_source,
    report,
    routing,
    tools,
)

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_UNSUPPORTED = "unsupported"
STATUS_ERROR = "error"

STATUSES: tuple[str, ...] = (STATUS_OK, STATUS_DEGRADED, STATUS_UNSUPPORTED, STATUS_ERROR)


#: 资料问答四种出口各配一句提示语（都明说"本次没有读取你的销售数据"——这是事实，不是免责）。
_DOC_NOTICES: dict[str, str] = {
    doc_qa.KIND_ANSWER: "（这条回答引用的是你已经导入的资料原文，位置就是它在资料里的出处 —— 本次没有读取你的销售数据。）",
    doc_qa.KIND_NOT_FOUND: "（这次在资料里没有找到 —— 本次没有读取你的销售数据。）",
    doc_qa.KIND_NO_TEXT: "（这份资料没有可提取的文字，我读不了它的内容 —— 本次没有读取你的销售数据。）",
    doc_qa.KIND_EMPTY: "（本机还没有导入过资料 —— 本次没有读取你的销售数据。）",
}


def _boundary_note(profile: dict[str, Any]) -> str:
    """数据边界如实说明（Gate 硬要求）——**每个回答都带上**，不挑着说。"""
    return (
        f"数据范围：{profile['first_day']} ~ {profile['last_day']}（共 {profile['rows']:,} 行，"
        f"{profile['column_count']} 列）；数据集中**没有区域/省份/城市/门店/渠道字段**。"
    )


def _record(
    *,
    question: str,
    status: str,
    profile: dict[str, Any] | None,
    parsed: intent_module.ParsedIntent | None = None,
    parse_info: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    answer_payload: dict[str, Any] | None = None,
    notice: str = "",
    error: dict[str, Any] | None = None,
    llm_used: bool = False,
    llm_error: dict[str, Any] | None = None,
    route: routing.Route | None = None,
    sales_data_accessed: bool = True,
) -> dict[str, Any]:
    """组装记录（**唯一**的出口形状）并落盘。

    `profile=None` 只出现在**非销售分支**（系统帮助 / 算术 / 概念问答）：
    那几档从头到尾没有读过销售数据，所以既没有数据画像，也没有"数据范围"那句提示 ——
    硬塞一个画像进去，等于在记录里写一句"这次读过数据"的假话。
    """
    route = route or routing.Route(routing.INTENT_CLARIFY, routing.MODE_CLARIFY)
    conversation = {
        "conversation_id": state.new_id("c"),
        "question": question,
        "status": status,
        "created_at": state.now_iso(),
        # ★ FR-007 契约字段①：怎么回答（analysis / direct / general / report / help / clarify）。
        #    同一个字段名同时用于接口响应与落盘记录 —— 不允许出现"后端一个名、前端另一个名"。
        "response_mode": route.response_mode,
        # ★ 契约字段②：意图分类的结论（6 个 intent + clarify 兜底），以及它为什么这么判。
        #    它与下面的 `intent`（销售解析的详细参数）**不是一回事**：
        #    `routing.intent` 回答"用户想做什么"，`intent` 回答"这次销售解析成了什么参数"。
        "routing": route.to_dict(),
        # ★ 契约字段③：这次问答有没有读过销售数据（非销售分支必须为 False，测试直接钉它）。
        "sales_data_accessed": bool(sales_data_accessed),
        # 没有数据画像（非销售分支）时不写"数据范围"那句话 —— 那句话说出去就是假的
        "notice": (notice or _boundary_note(profile)) if profile else notice,
        "data_profile": profile,
        "parse": parse_info or {},
        "llm": {
            "used": bool(llm_used),
            "intent_source": (parse_info or {}).get("source"),
            "answer_source": "llm" if llm_used else "code",
            "model": llm.model_name() if llm.available() else None,
            "provider": "deepseek",
            "error": llm_error,
        },
        # `intent` 字段**始终是同一个形状**（intent/params/assumptions/confidence/reason/report）：
        # 销售分支里是销售解析的结果，非销售分支里是路由的结论。
        "intent": parsed.to_dict() if parsed is not None else route.to_intent_dict(),
        "params": params,
        "tool": None if result is None else {
            "name": result.get("tool"),
            # 标题优先用结果自带的（报告形态不是计算工具，`tools.TOOLS` 里没有它）
            "title": result.get("title")
                     or (tools.TOOLS[result["tool"]].title if result.get("tool") in tools.TOOLS else ""),
            "params": result.get("params", {}),
            "notes": result.get("notes", []),
            "display": result.get("display", []),
            "selfcheck": result.get("selfcheck", {}),
        },
        "facts": None if result is None else result.get("facts"),
        "series": None if result is None else result.get("series"),
        "items": None if result is None else result.get("items"),
        # 报告形态（TASK-010）：文档结构一并落盘 —— 回答里那几段是**照它渲染**的，
        # 留着才能事后逐段复算（数字仍在 facts 里，这里只多一层"报告长什么样"）。
        # 非报告回答没有 `report` 键 → None（前端只判空）。
        "report_document": None if result is None else result.get("report"),
        "answer": answer_payload,
        "error": error,
    }
    return state.record_conversation(conversation)


def _with_mode(route: routing.Route, mode: str) -> routing.Route:
    """把路由结论的 mode 换掉（intent 不动）—— 只用于"没能给出答案"那几条出口。

    为什么这几条出口要改成 clarify：回答渲染的实际形态是"明确说答不了"，
    而不是分析/单值。记录里的 `response_mode` 描述的是**这次实际怎么回答的**，
    写成一个没发生过的档位就是记了一句假话。
    """
    return dataclasses.replace(route, response_mode=mode)


def _direct_notice(result: dict[str, Any], profile: dict[str, Any]) -> str:
    """单值档的提示行：来源 + 口径 + 数据范围（都不进正文，正文只有一个值）。"""
    notes = result.get("notes") or []
    parts = ["单值结果 —— 数字由程序从数据里算出，没有让模型参与。"]
    if notes:
        parts.append(str(notes[0]))
    return "".join(parts) + _boundary_note(profile)


def _region_boundary_note(result: dict[str, Any], profile: dict[str, Any]) -> str:
    """地区回答的边界提示：**说清这条结果用的是哪一份数据源**（FR-008 §三.3）。

    为什么不套用 `_boundary_note(profile)`：那句话描述的是**内置数据集**（8 列、没有地区字段），
    摆在一条"各地区销售额"的结果旁边等于自相矛盾 —— 用户会以为数字是从内置数据里算的。
    这条结果来自导入的数据源，边界就得按那份数据源说，并且说清"没拿国家顶替"。
    """
    facts = result.get("facts") or {}
    source = facts.get("source_dataset") or {}
    window = facts.get("window") or {}
    span = (
        f"区间 {window['start']} ~ {window['end']}（含首尾全天）" if window else "未指定时间（按全部行统计）"
    )
    return (
        f"数据源：**{source.get('name') or '（未知）'}** —— {source.get('rule') or ''}"
        f"；{span}；按「{(facts.get('dimension') or {}).get('label')}」分组的销售额由程序算出"
        f"（与「按地区查询」接口同一份实现），**没有拿 Country（国家）代替地区**。"
        f"（内置示例数据集本身没有地区字段，所以这条结果不是从它算的。）"
    )


# ── FR-010-B（B8②）：相对时间说法的"锚点"要看得见 ──────────────────────────
# 现状：「这个月卖了多少」按既有规则锚到数据集最后一天（→ 2011-12-09），
# 这条假设写在正文里，但**用户看不到"本月其实不完整"这件事** —— 一个被数据边界切短的月份，
# 读起来和"整月"没有区别。
#
# 本 TASK 只做**可见性**：正文里的假设一个字不改（相对时间规则是 FR-007/FR-009 冻结的口径），
# 只在提示行里把"按哪天换算、实际取了哪一段、这一段完不完整"如实说出来。
#
# 为什么要求"实际取的区间与换算结果一致"才提示：用户说了「这个月」而系统实际取的是别的区间时
# （例如 LLM 没启用、走了关键词路径→按全区间算），提示里那句"按最后一天换算"就是**错的解释**。
# 这时候宁可不提示，也不给一句与事实不符的说明。
_RELATIVE_WORDS: tuple[tuple[str, str], ...] = (
    ("这个月", "month"), ("本月", "month"), ("上个月", "prev_month"), ("上月", "prev_month"),
    ("这周", "week"), ("本周", "week"), ("上周", "prev_week"),
    ("今天", "day"), ("昨天", "prev_day"), ("前天", "prev_prev_day"),
)


def _relative_word(question: str) -> tuple[str, str]:
    """问句里的相对时间说法 → `(原词, 单位)`；没有就 `("", "")`。"""
    for word, unit in _RELATIVE_WORDS:
        if word in (question or ""):
            return word, unit
    return "", ""


def _natural_span(unit: str, last: _dt.date) -> tuple[_dt.date, _dt.date]:
    """这个说法**天然**指的是哪一段（以数据集最后一天为"今天"）。"""
    if unit == "month":
        start = last.replace(day=1)
        return start, (start + _dt.timedelta(days=32)).replace(day=1) - _dt.timedelta(days=1)
    if unit == "prev_month":
        end = last.replace(day=1) - _dt.timedelta(days=1)
        return end.replace(day=1), end
    if unit in ("week", "prev_week"):
        monday = last - _dt.timedelta(days=last.weekday())
        if unit == "prev_week":
            return monday - _dt.timedelta(days=7), monday - _dt.timedelta(days=1)
        return monday, monday + _dt.timedelta(days=6)
    if unit == "prev_day":
        return last - _dt.timedelta(days=1), last - _dt.timedelta(days=1)
    if unit == "prev_prev_day":
        return last - _dt.timedelta(days=2), last - _dt.timedelta(days=2)
    return last, last                                   # "今天"


def _as_date(value: Any) -> _dt.date | None:
    if isinstance(value, _dt.date):
        return value
    try:
        return _dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _relative_time_notice(question: str, params: dict[str, Any] | None, profile: dict[str, Any]) -> str:
    """相对时间说法的可见提示（**不改既有规则，只把假设说清楚**）。

    三种情形各说各的实话：

    ① 这条说法**真的被当成一个时间范围**用了，并按数据集最后一天截断 →
       「这个月」按数据集最后一天 2011-12-09 换算：取 2011-12-01 ~ 2011-12-09
       （本月不完整 —— 12-10 之后的数据还没有）
    ② 这条说法**没有被用上**（取的是整个数据集区间，例如没接模型时相对说法不换算）→
       「这个月」这次没有被当成一个时间范围 —— 本次按数据集全区间 2010-12-01 ~ 2011-12-09 统计
    ③ 说法用上了，但取的是**整个日历月**（没有按最后一天截断）→
       「这个月」这次按日历月取了 2011-12-01 ~ 2011-12-31；数据只到 2011-12-09 ——
       这个月后面的 22 天没有数据（不完整）

    ★ ②③ 同样重要：用户问"这个月"，拿到的是**全区间**或**日历月**的数字，而界面上原来什么
      也不说 —— 他会以为那就是"本月这一段"。把这件事说出来，比让他误解一个数要好。

    ★ ③ 是实测补上的：同一句话、同一个页面，解析可能落在"截断到 2011-12-09"（常见）或
      "整个 12 月"（另一条路径）两种窗口上 —— 只解释前一种，后一种就变成了"系统取了一段
      数据却什么都不说"。两种都能**如实说清**，所以都说。
    """
    word, unit = _relative_word(question)
    if not word or not profile:
        return ""
    last = _as_date(profile.get("last_day"))
    if last is None:
        return ""
    start, end = _natural_span(unit, last)
    got_start = _as_date((params or {}).get("start"))
    got_end = _as_date((params or {}).get("end"))

    if got_start == _as_date(profile.get("first_day")) and got_end == last:
        return (
            f"「{word}」这次没有被当成一个时间范围 —— 本次按数据集全区间 {got_start} ~ {got_end} 统计"
            f"（数据只到 {last}）。想只看这一段时间的话，直接写明日期最稳（例如 {start} 到 {min(end, last)}）。"
        )

    if got_start == start and got_end == end and end > last:
        # ③ 取的是**整个日历月**（没按最后一天截断）：说的就是它取了哪一段、缺了哪几天
        span_name = "日历月" if unit == "month" else "整周"
        return (
            f"「{word}」这次按{span_name}取了 {start} ~ {end}；数据只到 {last} —— "
            f"后面的 {(end - last).days} 天没有数据（不完整）。"
        )

    if got_start != start or got_end != min(end, last):
        return ""            # 这条说法没有被真的用上 → 不做解释（见上面那段注释）
    head = f"「{word}」按数据集最后一天 {last} 换算：取 {start} ~ {min(end, last)}"
    if end <= last:
        return head + "（这一段是完整的）。"
    missing = (end - last).days
    return head + f"；数据只到 {last} —— 这一段还差 {missing} 天没有数据（不完整）。"


def _resolve_mode(route: routing.Route, parsed: intent_module.ParsedIntent) -> str:
    """最终用哪一档渲染 —— 路由先定，处理分支发现"名不副实"时**只降不升**。

    唯一的降级：路由判成单值（direct），但这次解析出来的其实是多行结果
    （客户/商品明细等，不是 sales_summary）—— 那就退回 analysis 档。
    宁可用分析档装一张表，**绝不**用单值档去装一张表（那会把一个数说成全部答案）。

    方向仍然没反：档位来自 intent（不是"先定档位再猜调哪个工具"），
    这里只是**事后核对**"选定的档位是否真的装得下这次的确定性结果"。
    """
    mode = route.response_mode
    if mode == routing.MODE_DIRECT and parsed.intent != intent_module.INTENT_SALES_SUMMARY:
        return routing.MODE_ANALYSIS
    return mode


def _answer_non_sales(question: str, *, route: routing.Route, use_llm: bool) -> dict[str, Any]:
    """系统帮助 / 算术 / 概念问答 —— **在销售工具调用之前就结束分支**（评审 GO 条件 4）。

    这一支的代码调用图上**没有** tools / executor / loader：
    它不查数据、不建 DataFrame、不算任何销售指标，所以记录里没有数据画像，
    `sales_data_accessed` 也必然是 False（tests/test_fr007_isolation.py 把它钉死）。
    """
    if route.intent == routing.INTENT_SYSTEM_HELP:
        # ★ FR-010-B：这一档里现在还有一个**资料问答** —— 「资料里提到的市场计划是什么」。
        #   它排在最前面判（与 routing 里那条分支同一个判据、同一个顺序）：
        #   "我导入的资料里都写了什么"这种句子两边都像（元信息 / 资料内容），
        #   而用户真的想要的显然是**内容**，不是"你导了几份记录"。
        doc = doc_qa.answer_question(question)
        if doc is not None:
            # 资料回答一律是**引用原文**（代码组织，没有模型参与）：来源标 deterministic。
            payload = answer.compose_flat(
                key=answer.SECTION_HELP,
                text=doc.text,
                source=answer.SOURCE_DETERMINISTIC,
                title=f"【{doc.title}】",
                sources=list(doc.findings),
            )
            # 命中 / 概览 = 答得上（ok）；"没找到"/"读不出文字"/"还没导入过" = 答不了（unsupported）。
            # 后三种不是故障，是"我诚实地告诉你没有" —— 与 AC-03 里"数据不支持某个维度"同一档。
            status = STATUS_OK if doc.kind == doc_qa.KIND_ANSWER else STATUS_UNSUPPORTED
            notice = _DOC_NOTICES.get(
                doc.kind, "（这条回答来自你已经导入的资料原文 —— 本次没有读取你的销售数据。）"
            )
            return _record(
                question=question,
                status=status,
                profile=None,                        # 资料问答没读过销售数据，就没有数据画像
                parse_info={"source": "routing", "fallback": None, "llm_error": None},
                answer_payload=payload,
                notice=notice,
                route=route,
                sales_data_accessed=False,
            )

        # ★ FR-010-A3/A4：这一档里还有四种答案 —— 系统元信息（查真实记录）/ 身份（一句话）/
        #   我没有的能力（明说没有）/ 既有的静态使用说明。**都是纯查表或静态文案，不碰销售数据。**
        title, text, kind = general.system_answer(question)
        payload = answer.compose_flat(
            key=answer.SECTION_HELP, text=text, source=answer.SOURCE_SYSTEM, title=f"【{title}】"
        )
        if kind == "capability":
            # 没有这项能力 → 状态是"我给不了"，而不是"系统出了问题"
            status, notice = STATUS_UNSUPPORTED, "（这项能力我没有 —— 本次没有读取你的销售数据。）"
        elif kind in ("meta", "identity"):
            status = STATUS_OK
            notice = "（这条回答来自你本机的导入记录 —— 本次没有读取你的销售数据。）" \
                if kind == "meta" else "（这是关于我自己的说明 —— 本次没有读取你的销售数据。）"
        else:
            status, notice = STATUS_OK, "（这是系统使用说明 —— 本次没有读取你的销售数据。）"
    elif route.intent == routing.INTENT_ARITHMETIC:
        result = arithmetic.solve(question)
        text = result.render() if result is not None else "这道算式我没能识别出来。"
        payload = answer.compose_flat(
            key=answer.SECTION_ANSWER, text=text, source=answer.SOURCE_DETERMINISTIC
        )
        if result is not None and result.ok:
            status, notice = STATUS_OK, "（这是一道算术题 —— 由本机计算器算出，没有读取销售数据。）"
        else:
            # 被安全闸门拒了（除零 / 指数过大 / 写了代码）：**如实说不算**，不产出数字
            status, notice = STATUS_UNSUPPORTED, f"（这道算式没有计算：{text}）"
    else:                                            # INTENT_GENERAL_QA
        title, text, source = general.general_answer(question, use_llm=use_llm)
        payload = answer.compose_flat(
            key=answer.SECTION_ANSWER, text=text, source=source, title=f"【{title}】"
        )
        status = STATUS_OK
        notice = (
            "（这是概念解释 —— 本次没有读取你的销售数据。）"
            if source == answer.SOURCE_SYSTEM
            else "（这是概念解释，由模型写成 —— 本次没有读取你的销售数据。）"
        )

    return _record(
        question=question,
        status=status,
        profile=None,                                # 没读过数据，就没有数据画像
        parse_info={"source": "routing", "fallback": None, "llm_error": None},
        answer_payload=payload,
        notice=notice,
        route=route,
        sales_data_accessed=False,
    )


def ask(question: str, *, use_llm: bool = True) -> dict[str, Any]:
    """回答一个问题，返回落盘的记录。

    `use_llm=False` 用于测试与"强制降级演示"：完全不走 LLM，走关键词匹配 + 代码回答。
    它与"没配 key"时的行为**完全一致**（AC-05 要验的就是这条路径）。

    FR-007 起，进门的**第一件事**是路由（`routing.classify`，纯代码）：
    判出"用户想做什么"之后，非销售的三类（系统帮助 / 算术 / 概念问答）就地结束，
    根本不会走到下面的销售链路 —— 这就是"1+1 被算成销售汇总"那个洞的堵法。
    """
    question = (question or "").strip()
    route = routing.classify(question)

    # ── 非销售分支：不读数据、不调销售工具（评审 GO 条件 4/6 + §六安全边界③④）──
    if route.intent in routing.NON_SALES_INTENTS:
        return _answer_non_sales(question, route=route, use_llm=use_llm)

    # ── 澄清兜底（FR-010-A4/A5）：**同样不读销售数据** ──────────────────────
    # 走到这里说明这句话既不是销售问题，也不是算术/概念/系统类 —— 没有任何工具能回答它。
    # 以前这一支会先读一遍数据画像、再报一段又长又冷的"没听懂…该调哪个工具"；
    # 现在：不读数据（没读就不该在记录里写画像），措辞里也不出现任何内部名词；
    # 问的是"我没有的能力"（天气/预测）时直接说没有 —— 那比"没听懂"更准确。
    #
    # ★ 记录形状没动：仍是 status=error / intent_unparseable / 没有回答段
    #   （tests/test_chat.py 与 tests/test_fr007_isolation.py 两条既有验收钉着它）。
    if route.intent == routing.INTENT_CLARIFY and question:
        message = intent_module.unparseable_message()
        return _record(
            question=question,
            status=STATUS_ERROR,
            profile=None,                            # 这一支一次都没读数据 → 不写画像
            parse_info={"source": "routing", "fallback": None, "llm_error": None},
            error={"code": "intent_unparseable", "message": message},
            notice=general.clarification_notice(question) or f"没能解析这个问题：{message}",
            route=route,
            sales_data_accessed=False,
        )

    profile = tools.dataset_profile()

    if not question:
        return _record(
            question=question,
            status=STATUS_ERROR,
            profile=profile,
            error={"code": "intent_empty_question", "message": "问题不能为空"},
            notice="问题为空 —— 没有可解析的内容。",
            route=route,
        )

    # ── 第 2、3 步：解析 + 校验 ────────────────────────────────────────
    try:
        parsed, parse_info = intent_module.parse(question, allow_llm=use_llm)
    except intent_module.IntentError as exc:
        return _record(
            question=question,
            status=STATUS_ERROR,
            profile=profile,
            parse_info={"source": "none", "llm_error": None},
            llm_error={"code": exc.code, "message": exc.message},
            error={"code": exc.code, "message": exc.message},
            notice=f"没能解析这个问题：{exc.message}",
            route=_with_mode(route, routing.MODE_CLARIFY),
        )

    llm_error = parse_info.get("llm_error")

    # ── 数据不支持的维度：不计算，如实说明（AC-03）──────────────────────
    if parsed.intent == intent_module.INTENT_UNSUPPORTED:
        payload = answer.compose(
            question=question,
            result=None,
            llm_raw=None,
            llm_used=False,
            llm_error=llm_error,
            unsupported_reason=parsed.reason or "数据里没有这个维度",
            profile=profile,
        )
        return _record(
            question=question,
            status=STATUS_UNSUPPORTED,
            profile=profile,
            parsed=parsed,
            parse_info=parse_info,
            answer_payload=payload,
            llm_error=llm_error,
            notice=f"数据不支持这个问题：{parsed.reason}",
            # 数据里没有这个维度 → 归类到「无法确定」的兜底档（施工指令 §三）：
            # 这一条既不是分析、也不是单值，回答形态由"不猜"那套文案决定。
            route=_with_mode(route, routing.MODE_CLARIFY),
        )

    # ── 第 4 步：白名单工具 + 确定性计算 ───────────────────────────────
    try:
        validated = parsed.validated_params()
    except intent_module.IntentError as exc:
        return _record(
            question=question,
            status=STATUS_ERROR,
            profile=profile,
            parsed=parsed,
            parse_info=parse_info,
            llm_error=llm_error,
            error={"code": exc.code, "message": exc.message},
            notice=f"参数不合法：{exc.message}",
            route=_with_mode(route, routing.MODE_CLARIFY),
        )

    params = validated.model_dump()
    # date 对象转成字符串再交给工具（工具签名要 date，pydantic 已保证类型正确）
    try:
        # 报告形态（TASK-010）：**不是**第 6 个计算型 intent ——
        # 它仍然走 sales_compare 的窗口解析与金额口径，只是由 report.py 把四个既有工具的
        # 确定性结果再编排成一份文档（见 report.py 开头"为什么是输出形态"）。
        if parsed.report:
            result = report.build_report(parsed.report, params)
        else:
            result = tools.run_tool(parsed.intent, params)
    except Exception as exc:                      # 计算失败 → 明确报错，**不编一个数字**
        return _record(
            question=question,
            status=STATUS_ERROR,
            profile=profile,
            parsed=parsed,
            parse_info=parse_info,
            llm_error=llm_error,
            error={"code": "tool_failed", "message": f"{type(exc).__name__}: {exc}"},
            notice=f"计算失败（{type(exc).__name__}）：{exc}",
            route=_with_mode(route, routing.MODE_CLARIFY),
        )

    # ── 数据不足（如窗口不完整）：不猜不补，如实说明，**也不问 LLM** ─────
    # 没有可比的两个窗口就没有事实可讲，让模型去"圆"只会圆出编造的原因。
    if result.get("status") == "insufficient_data":
        reason = (result.get("notes") or ["数据不足，无法比较。"])[0]
        payload = answer.compose(
            question=question,
            result=result,
            llm_raw=None,
            llm_used=False,
            llm_error=llm_error,
            unsupported_reason=None,
            profile=profile,
            fallback_reason="（数据不足，本次不给推断与建议 —— 没有可比的事实，说了就是编。）",
        )
        return _record(
            question=question,
            status=STATUS_UNSUPPORTED,
            profile=profile,
            parsed=parsed,
            parse_info=parse_info,
            params={key: (value.isoformat() if hasattr(value, "isoformat") else value)
                    for key, value in params.items()},
            result=result,
            answer_payload=payload,
            llm_error=llm_error,
            notice=reason,
            route=_with_mode(route, routing.MODE_CLARIFY),
        )

    # ── 地区维度用不了（没有带地区字段的数据源 / 维度名不对 / 多个维度要先澄清）──
    # FR-008：这一支**不产出任何数字**，也绝不回退到"用国家凑一个"。
    # 正常情况下 intent 的硬闸门已经拦在前面了；这里是竞态与边界（数据源刚被删、
    # 维度名对不上、数据源有多个地区字段）时的**同一个诚实出口**。
    if result.get("status") == "region_unavailable":
        reason = str(result.get("reason") or region_source.NO_REGION_REASON)
        payload = answer.compose(
            question=question,
            result=None,
            llm_raw=None,
            llm_used=False,
            llm_error=llm_error,
            unsupported_reason=reason,
            profile=profile,
        )
        return _record(
            question=question,
            status=STATUS_UNSUPPORTED,
            profile=profile,
            parsed=parsed,
            parse_info=parse_info,
            params={key: (value.isoformat() if hasattr(value, "isoformat") else value)
                    for key, value in params.items()},
            answer_payload=payload,
            llm_error=llm_error,
            notice=f"数据不支持这个问题：{reason}",
            route=_with_mode(route, routing.MODE_CLARIFY),
        )

    # ── single-value 档（DATA_LOOKUP）：一个值就是全部答案，**不问 LLM** ────
    # 为什么这一档不问模型：问的是"11 月卖了多少"这种确定性事实，答案完整且唯一；
    # 让模型在事实之外再写一段【为什么】/【建议行动】，等于给一个单值问题硬加一段推断
    # —— 那正是评审 D 条件禁止的（"禁止出现：完整指标表 / 主要贡献 / 为什么 / 建议行动 / 趋势分析"）。
    mode = _resolve_mode(route, parsed)
    if mode == routing.MODE_DIRECT:
        single = answer.direct_display(result, question)          # 只留被问的那一行
        lean = dict(result)
        lean["display"] = single
        # ★ FR-010-A1/A2：单值档配一张**轻量看板**（区间看板 / 单日看板）。
        #   它只是把**同一份确定性结果**摆成卡片（外加几天趋势，也是同一套工具的产出），
        #   不进分析档、不给推断与建议 —— "问题有多大，回答就做到多大"。
        payload = answer.compose_flat(
            key=answer.SECTION_ANSWER,
            text=answer.render_direct_text(lean, question),
            source=answer.SOURCE_DETERMINISTIC,
            dashboard=dashboard.build(result=result, params=params),
        )
        return _record(
            question=question,
            status=STATUS_OK,
            profile=profile,
            parsed=parsed,
            parse_info=parse_info,
            params={key: (value.isoformat() if hasattr(value, "isoformat") else value)
                    for key, value in params.items()},
            result=lean,
            answer_payload=payload,
            # 口径那句话（排除取消单/数量≤0…）留在 notice 里，与数据范围并排 ——
            # 正文保持"一个值"的形态，说明放在紧挨着它的地方，不塞进答案里。
            # ★ FR-010-B（B8②）：相对时间说法的换算与"这一段完不完整"也跟在这句后面
            #   （正文里的假设保持原样，可见性走 notice）。
            notice=_direct_notice(result, profile) + _relative_time_notice(question, params, profile),
            route=route,
        )

    # ── 第 5 步：LLM 只负责"组织语言" ─────────────────────────────────
    # 喂给模型的**只有代码产生的文本**（事实段 + 主要贡献段），没有 DataFrame、没有原始行。
    facts_text = answer.render_llm_facts(result)
    llm_raw: str | None = None
    llm_used = False
    if use_llm and llm.available():
        try:
            llm_raw = llm.chat(
                answer.LLM_SYSTEM_PROMPT,
                answer.build_llm_prompt(question, facts_text),
                max_tokens=llm.DEFAULT_MAX_TOKENS,
            )
            llm_used = True
        except llm.LLMError as exc:
            llm_error = {"code": exc.code, "message": exc.message}

    payload = answer.compose(
        question=question,
        result=result,
        llm_raw=llm_raw,
        llm_used=llm_used,
        llm_error=llm_error,
        unsupported_reason=None,
        profile=profile,
    )

    # ── 第 6 步：状态判定 + 落盘 ───────────────────────────────────────
    # ★ FR-008：地区分布的边界要按**那份导入数据源**说，不能套内置数据集那句话
    #   （内置数据集没有地区字段，套上去就成了一句与结果自相矛盾的边界说明）。
    boundary = (
        _region_boundary_note(result, profile)
        if parsed.intent == intent_module.INTENT_SALES_BY_REGION
        else _boundary_note(profile)
    )
    if not llm_used:
        status = STATUS_DEGRADED
        # 括号里只写**给用户看的一句话**（llm.user_facing_error）：原因原样留在
        # record["llm"]["error"] 里，后台可查；界面上不出现异常类名/服务商名/参数名。
        notice = (
            f"结果由程序确定性计算得出；**本次没有可用的模型**"
            f"（{llm.user_facing_error(llm_error)}），【为什么】/【建议行动】由程序生成，不做推断。"
            + boundary + _relative_time_notice(question, params, profile)
        )
    elif not payload["guard"]["passed"]:
        status = STATUS_DEGRADED
        notice = (
            "结果由确定性计算得出；模型本轮写的内容里出现了**无法追溯到计算结果的数字"
            "或写错的币种**，已按「LLM 只负责组织语言、事实以确定性结果为准」的规则整段作废"
            "（见 guard.violations / guard.currency_words）。" + boundary
            + _relative_time_notice(question, params, profile)
        )
    else:
        status = STATUS_OK
        notice = boundary + _relative_time_notice(question, params, profile)

    return _record(
        question=question,
        status=status,
        profile=profile,
        parsed=parsed,
        parse_info=parse_info,
        params={key: (value.isoformat() if hasattr(value, "isoformat") else value)
                for key, value in params.items()},
        result=result,
        answer_payload=payload,
        notice=notice,
        llm_used=llm_used,
        llm_error=llm_error,
        route=route,
    )


__all__ = ["STATUSES", "STATUS_DEGRADED", "STATUS_ERROR", "STATUS_OK", "STATUS_UNSUPPORTED", "ask"]
