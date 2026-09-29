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

from typing import Any

from app import state
from app.ai import answer, arithmetic, general, intent as intent_module, llm, report, routing, tools

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_UNSUPPORTED = "unsupported"
STATUS_ERROR = "error"

STATUSES: tuple[str, ...] = (STATUS_OK, STATUS_DEGRADED, STATUS_UNSUPPORTED, STATUS_ERROR)


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
        title, text = general.help_answer(question)
        payload = answer.compose_flat(
            key=answer.SECTION_HELP, text=text, source=answer.SOURCE_SYSTEM, title=f"【{title}】"
        )
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

    # ── single-value 档（DATA_LOOKUP）：一个值就是全部答案，**不问 LLM** ────
    # 为什么这一档不问模型：问的是"11 月卖了多少"这种确定性事实，答案完整且唯一；
    # 让模型在事实之外再写一段【为什么】/【建议行动】，等于给一个单值问题硬加一段推断
    # —— 那正是评审 D 条件禁止的（"禁止出现：完整指标表 / 主要贡献 / 为什么 / 建议行动 / 趋势分析"）。
    mode = _resolve_mode(route, parsed)
    if mode == routing.MODE_DIRECT:
        single = answer.direct_display(result, question)          # 只留被问的那一行
        lean = dict(result)
        lean["display"] = single
        payload = answer.compose_flat(
            key=answer.SECTION_ANSWER,
            text=answer.render_direct_text(lean, question),
            source=answer.SOURCE_DETERMINISTIC,
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
            notice=_direct_notice(result, profile),
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
    if not llm_used:
        status = STATUS_DEGRADED
        # 括号里只写**给用户看的一句话**（llm.user_facing_error）：原因原样留在
        # record["llm"]["error"] 里，后台可查；界面上不出现异常类名/服务商名/参数名。
        notice = (
            f"结果由程序确定性计算得出；**本次没有可用的模型**"
            f"（{llm.user_facing_error(llm_error)}），【为什么】/【建议行动】由程序生成，不做推断。"
            + _boundary_note(profile)
        )
    elif not payload["guard"]["passed"]:
        status = STATUS_DEGRADED
        notice = (
            "结果由确定性计算得出；模型本轮写的内容里出现了**无法追溯到计算结果的数字"
            "或写错的币种**，已按「LLM 只负责组织语言、事实以确定性结果为准」的规则整段作废"
            "（见 guard.violations / guard.currency_words）。" + _boundary_note(profile)
        )
    else:
        status = STATUS_OK
        notice = _boundary_note(profile)

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
