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

from typing import Any

from app import state
from app.ai import answer, intent as intent_module, llm, tools

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
    profile: dict[str, Any],
    parsed: intent_module.ParsedIntent | None = None,
    parse_info: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    answer_payload: dict[str, Any] | None = None,
    notice: str = "",
    error: dict[str, Any] | None = None,
    llm_used: bool = False,
    llm_error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """组装记录（**唯一**的出口形状）并落盘。"""
    conversation = {
        "conversation_id": state.new_id("c"),
        "question": question,
        "status": status,
        "created_at": state.now_iso(),
        "notice": notice or _boundary_note(profile),
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
        "intent": parsed.to_dict() if parsed is not None else None,
        "params": params,
        "tool": None if result is None else {
            "name": result.get("tool"),
            "title": tools.TOOLS[result["tool"]].title if result.get("tool") in tools.TOOLS else "",
            "params": result.get("params", {}),
            "notes": result.get("notes", []),
            "display": result.get("display", []),
            "selfcheck": result.get("selfcheck", {}),
        },
        "facts": None if result is None else result.get("facts"),
        "series": None if result is None else result.get("series"),
        "items": None if result is None else result.get("items"),
        "answer": answer_payload,
        "error": error,
    }
    return state.record_conversation(conversation)


def ask(question: str, *, use_llm: bool = True) -> dict[str, Any]:
    """回答一个问题，返回落盘的记录。

    `use_llm=False` 用于测试与"强制降级演示"：完全不走 LLM，走关键词匹配 + 代码回答。
    它与"没配 key"时的行为**完全一致**（AC-05 要验的就是这条路径）。
    """
    question = (question or "").strip()
    profile = tools.dataset_profile()

    if not question:
        return _record(
            question=question,
            status=STATUS_ERROR,
            profile=profile,
            error={"code": "intent_empty_question", "message": "问题不能为空"},
            notice="问题为空 —— 没有可解析的内容。",
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
        )

    params = validated.model_dump()
    # date 对象转成字符串再交给工具（工具签名要 date，pydantic 已保证类型正确）
    try:
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
    )


__all__ = ["STATUSES", "STATUS_DEGRADED", "STATUS_ERROR", "STATUS_OK", "STATUS_UNSUPPORTED", "ask"]
