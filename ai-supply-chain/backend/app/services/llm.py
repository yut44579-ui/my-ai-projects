"""LLM 客户端（TASK-004）：DeepSeek 的 OpenAI 兼容 /chat/completions。

★ key 一律从配置（.env / 环境变量）读，源码里没有任何硬编码密钥。
★ 失败一律抛 LLMUnavailable，**绝不返回"编造的智能回复"**（D10）：
  调用方据此落 ai_status=FAILED 并显示「AI 暂时无法回复，请人工处理」。
★ 复用项目已有的 httpx（TASK-001 起就在用，不新增依赖，也不引 LLM SDK）。
"""

from __future__ import annotations

import httpx

from app.core.config import settings
# ★ TASK-037：多供应商。llm.py 只负责"用当前生效的供应商拿回复"，
#   具体协议差异（OpenAI 兼容 / Anthropic / Gemini）都在 ai_provider 里。
from app.services.ai_provider import (
    ProviderError,
    ResolvedProvider,
    build_request,
    parse_response,
    resolve_active,
)


def _resolve_provider() -> ResolvedProvider | None:
    """取当前生效的供应商配置。

    ★ 单独抽出来（而不是在 generate_reply 里直接调）有两个原因：
      ① 需要一个**独立的数据库会话**：generate_reply 是无 db 参数的纯函数，
         调用方（ai_reply / dashboard_ai / proposals / knowledge…）都不方便传 db；
      ② 测试里可以 monkeypatch 掉，网络与数据库都不碰。
    """
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        return resolve_active(db)
    except Exception:  # noqa: BLE001 - 读配置失败按"没有可用供应商"处理
        return None
    finally:
        db.close()

# Prompt 只负责表达，安全由代码层的 PolicyGate 把关（D7）
#
# ★★ TASK-033：按需求 §十三「AI 自动回复必须"像人"，不能"很AI"」重写。
#    此前这里只有 3 条（中文/不编造/不给承诺），而 §十三 明确给了
#    **9 条回复原则 + 4 条禁止**，一条都没进来 —— 那等于 §十三 没有落地。
#
#    §十三 原文（L544-584）：
#      禁止机械式：「您好，感谢您的咨询，很高兴为您服务……」
#      应该像真实业务人员：「我先帮你确认一下。」「你大概需要几张？」
#                          「主要是训练还是推理？」「这个我得确认一下。」
#      9 条原则：短 / 自然 / 有上下文 / 不重复 / 不啰嗦 / 不强行销售 /
#                不装懂 / 不知道就说不知道 / 需要确认就去确认
#      另 3 条：不要每次都重新介绍公司；不要客户问一句就回一篇小作文；
#              不要为表现"智能"而堆专业术语。
#
#    ★ 为什么把示例原话写进提示词：模型对"具体例句"的遵循度远高于抽象形容词
#      （"自然"这种词它无法校准，但给了例句它能模仿语气）。
SYSTEM_PROMPT = (
    "你是一家公司的客户沟通助理，负责回复客户的咨询。你是业务人员，不是客服机器人。\n"
    "\n"
    "【语气】像真人同事在微信上聊天：\n"
    "· 短。一两句话为主，能一句说清就不写第二句。\n"
    "· 自然。用口语，不用「尊敬的客户」「感谢您的咨询」这类模板话。\n"
    "· 有上下文。接着上文说，不要每轮从头开始。\n"
    "· 不重复。客户已经说过的信息不要再问一遍。\n"
    "· 不啰嗦。不要铺垫、不要总结、不要问候语收尾。\n"
    "· 不强行销售。不追问需求、不催单、不推销。\n"
    "\n"
    "【可以这样说】「我先帮你确认一下。」／「你大概需要几张？」／"
    "「主要是训练还是推理？」／「这个我得确认一下。」／「可以，我看看。」／"
    "「你预计什么时候开始用？」\n"
    "\n"
    "【不知道就说不知道】不确定的、需要内部核实的，直接说「我得确认一下」或"
    "「这个我需要问一下同事」，不要猜、不要圆滑地含糊过去。\n"
    "\n"
    "【绝对不要】\n"
    "1) 不要编造任何公司政策、时间承诺或数据；\n"
    "2) 不要给出报价、折扣、合同条款、付款/退款条件、赔偿或任何法律承诺；\n"
    "3) 不要每次自我介绍或介绍公司；\n"
    "4) 不要客户问一句就回一篇小作文（超过三句就太长了）；\n"
    "5) 不要为了显得专业而堆术语（客户听不懂的词就别用）。"
)


class LLMUnavailable(Exception):
    """LLM 不可用（未配 key / 网络失败 / 超时 / 非 200 / 返回为空）。

    ★ 这是"如实告知"，不是"降级"：上层必须落 FAILED 并给人工兜底文案，
      绝不允许拿别的内容冒充 AI 回复。
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _request(url: str, payload: dict, headers: dict, timeout: float) -> httpx.Response:
    """单独抽出来，测试里可以 monkeypatch 掉（网络层零真实调用）。

    ★ TASK-037：url 由调用方传入（多供应商后不能再写死 DeepSeek 的地址）。
    """
    return httpx.post(url, json=payload, headers=headers, timeout=timeout)


def generate_reply(
    question: str,
    *,
    customer_name: str | None = None,
    system_prompt: str | None = None,
    history: list[dict] | None = None,
) -> str:
    """调 LLM 生成回复。任何异常路径都抛 LLMUnavailable（绝不返回假内容）。

    `system_prompt` 可覆盖默认人设。默认人设是**客户沟通助理**（TASK-004 的语义，
    其中包含"不确定的就说需要同事确认"这类客户话术）；
    首页 AI 助手是**数据问答**场景，套用客户人设会串味（模型会说"请同事确认"），
    因此由调用方传入自己的人设。★ 不传时行为与改动前完全一致。

    ★★ TASK-033 **`history`：多轮上下文**（需求 §十三 的"有上下文""不重复"）：
      此前只传单条 question，模型看不到之前聊过什么 ——
      于是会重复提问、答非所问，"像人"就无从谈起。
      `history` 是 `[{"role": "user"|"assistant", "content": "..."}]`，
      **按时间正序**传入。调用方负责裁剪条数（见 services/ai_reply.py）。
      ★ 仍然是"不传就与改动前完全一致"，所以首页 AI 助手等场景不受影响。

    ★★ TASK-037 **多供应商**：不再写死 DeepSeek。每次调用都向
      `ai_provider.resolve_active()` 要当前生效的供应商配置
      （数据库里配的优先，没有则回退 `.env` 的 DEEPSEEK_*），
      再按协议组装请求、按协议解析响应。
      ★ 为什么每次现查而不是缓存：供应商可以在界面上随时切换，
        缓存会让"刚换的模型不生效"，那种问题很难查。
        代价是一次主键查询 —— 相比网络往返可以忽略。
    """
    provider = _resolve_provider()
    if provider is None:
        raise LLMUnavailable(
            "没有可用的 AI 供应商：请到「个人中心 → AI 设置」里配置并启用一家，"
            "或在 .env 里配置 DEEPSEEK_API_KEY"
        )

    user_content = question.strip()
    if customer_name:
        user_content = f"客户姓名：{customer_name}\n客户问题：{user_content}"

    # ★ 历史放在当前问题之前；只接受合法 role，防止脏数据破坏请求体
    history_messages: list[dict] = []
    for item in history or []:
        role = str(item.get("role") or "")
        content = str(item.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            history_messages.append({"role": role, "content": content})
    history_messages.append({"role": "user", "content": user_content})

    try:
        url, payload, headers = build_request(
            provider,
            system_prompt=(system_prompt or SYSTEM_PROMPT),
            messages=history_messages,
        )
    except Exception as exc:  # noqa: BLE001 - 组装失败也要如实报
        raise LLMUnavailable(f"组装请求失败：{type(exc).__name__}: {exc}") from exc

    try:
        response = _request(url, payload, headers, provider.timeout)
    except httpx.HTTPError as exc:  # 超时 / 连接失败 / 协议错误
        raise LLMUnavailable(f"调用 LLM 失败：{type(exc).__name__}") from exc

    if response.status_code != 200:
        # ★ 带上对方返回的原文（截断）：只报"HTTP 401"没法排查，是 key 错还是额度问题看不出
        detail = response.text[:200] if response.text else ""
        raise LLMUnavailable(f"LLM 返回 HTTP {response.status_code}：{detail}")

    try:
        reply = parse_response(provider, response.json())
    except Exception as exc:  # noqa: BLE001
        raise LLMUnavailable(f"LLM 响应解析失败：{type(exc).__name__}: {exc}") from exc

    if not reply:
        raise LLMUnavailable("LLM 返回内容为空")
    return reply


__all__ = ["LLMUnavailable", "SYSTEM_PROMPT", "generate_reply"]
