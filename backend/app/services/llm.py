"""LLM 客户端（TASK-004）：DeepSeek 的 OpenAI 兼容 /chat/completions。

★ key 一律从配置（.env / 环境变量）读，源码里没有任何硬编码密钥。
★ 失败一律抛 LLMUnavailable，**绝不返回"编造的智能回复"**（D10）：
  调用方据此落 ai_status=FAILED 并显示「AI 暂时无法回复，请人工处理」。
★ 复用项目已有的 httpx（TASK-001 起就在用，不新增依赖，也不引 LLM SDK）。
"""

from __future__ import annotations

import httpx

from app.core.config import settings

# Prompt 只负责表达，安全由代码层的 PolicyGate 把关（D7）
SYSTEM_PROMPT = (
    "你是一家公司的客户沟通助理，负责回复客户的咨询。要求：\n"
    "1) 用中文，简洁、礼貌，直接回答客户的问题；\n"
    "2) 不要编造任何公司政策、时间承诺或数据；不确定的就说需要同事确认；\n"
    "3) 绝不要给出报价、折扣、合同条款、付款/退款条件、赔偿或任何法律承诺。"
)


class LLMUnavailable(Exception):
    """LLM 不可用（未配 key / 网络失败 / 超时 / 非 200 / 返回为空）。

    ★ 这是"如实告知"，不是"降级"：上层必须落 FAILED 并给人工兜底文案，
      绝不允许拿别的内容冒充 AI 回复。
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _request(payload: dict, headers: dict, timeout: float) -> httpx.Response:
    """单独抽出来，测试里可以 monkeypatch 掉（网络层零真实调用）。"""
    url = settings.deepseek_base_url.rstrip("/") + "/chat/completions"
    return httpx.post(url, json=payload, headers=headers, timeout=timeout)


def generate_reply(question: str, *, customer_name: str | None = None) -> str:
    """调 LLM 生成回复。任何异常路径都抛 LLMUnavailable（绝不返回假内容）。"""
    api_key = (settings.deepseek_api_key or "").strip()
    if not api_key:
        raise LLMUnavailable("未配置 DEEPSEEK_API_KEY，无法调用 AI")

    user_content = question.strip()
    if customer_name:
        user_content = f"客户姓名：{customer_name}\n客户问题：{user_content}"

    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.3,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        response = _request(payload, headers, settings.llm_timeout_seconds)
    except httpx.HTTPError as exc:  # 超时 / 连接失败 / 协议错误
        raise LLMUnavailable(f"调用 LLM 失败：{type(exc).__name__}") from exc

    if response.status_code != 200:
        raise LLMUnavailable(f"LLM 返回 HTTP {response.status_code}")

    try:
        data = response.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    except (ValueError, AttributeError, IndexError) as exc:
        raise LLMUnavailable(f"LLM 响应解析失败：{type(exc).__name__}") from exc

    reply = (content or "").strip()
    if not reply:
        raise LLMUnavailable("LLM 返回内容为空")
    return reply


__all__ = ["LLMUnavailable", "SYSTEM_PROMPT", "generate_reply"]
