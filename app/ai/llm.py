"""llm.py · DeepSeek 客户端（TASK-004）。

════════════════════════════════════════════════════════════════════════
【职责边界：本文件**只**负责"把一段文字发出去、把一段文字收回来"】
════════════════════════════════════════════════════════════════════════
它不认识 Intent、不认识销售数据、不知道怎么算钱。JSON 怎么解析在 intent.py，
事实怎么组织在 answer.py。这样换模型（甚至换厂商）时只动这一个文件。

════════════════════════════════════════════════════════════════════════
【key 从哪里来（施工指令硬要求）】
════════════════════════════════════════════════════════════════════════
key 只从**环境变量 `DEEPSEEK_API_KEY`** 读，来源是项目根目录的 `.env`（已被 .gitignore 挡住）。
    · 代码里**没有**任何 key 字面量，也**不许**有 —— 本文件里搜不到 "sk-" 开头的串；
    · 日志/出错信息里**不回显** key（连前几位都不回显，只回显"有没有"）；
    · 没有 key **不是崩溃**，而是**降级**：`available()` 返回 False，
      service.py 会走关键词匹配并明确告知"未接 LLM"，**不假装 LLM 在场**（AC-05）。

════════════════════════════════════════════════════════════════════════
【推理模型的坑（用户实测提醒 + 探针验证）】
════════════════════════════════════════════════════════════════════════
DeepSeek 的模型是**推理模型**：思考过程走 `reasoning_content`，最终答案走 `content`。
`max_tokens` 给小了会出现"思考没结束就被截断"→ `content` 为空字符串、
`finish_reason == "length"`。所以这里：
    · `max_tokens` 默认给足 4000（施工指令要求 ≥2000）；
    · `content` 为空时**回退读 `reasoning_content`**（把它当答案，不静默返回空串）。
    两者都空 → 抛 `LLMError("llm_empty_response")`，让上层明确报错，**绝不编答案**。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 模型与端点：默认值即 CLAUDE.md 技术栈里声明的那个；可用环境变量覆盖（不改代码就能换模型）
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"

# 超时给得比默认宽：推理模型的"想"要花时间，掐太紧会把正常请求掐成"失败"
DEFAULT_TIMEOUT = float(os.environ.get("SRA_LLM_TIMEOUT") or 180.0)
DEFAULT_MAX_TOKENS = int(os.environ.get("SRA_LLM_MAX_TOKENS") or 4000)

_ENV_LOADED = False

# API key 的环境变量名（**名字**是常量，**值**永远不落进代码）
API_KEY_ENV = "DEEPSEEK_API_KEY"


class LLMError(RuntimeError):
    """LLM 调用失败（网络/鉴权/超时/返回空）。

    带机器可读的 `code`，上层（service.py）据此决定降级还是报错，
    前端据此显示不同的提示 —— 与 api.py 的 ApiError 同一套"错误要有码"的习惯。
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ════════════════════════════════════════════════════════════════════════
# 配置读取（每次调用现读，测试才能靠改环境变量隔离）
# ════════════════════════════════════════════════════════════════════════
def load_env() -> None:
    """把项目根目录 `.env` 读进 `os.environ`（幂等；只做一次）。

    用 python-dotenv（就是为这件事而生的库，不自己解析 .env —— 那是个轮子）。
    `override=False`：**已经在环境里的变量优先**，测试用 `monkeypatch.setenv` 设的值
    不会被 .env 文件盖掉（否则 AC-05 的"无 key 降级"根本没法测）。
    """
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    try:
        from dotenv import load_dotenv
    except ImportError:                       # 没装 dotenv 也应能跑（key 直接给环境变量即可）
        return
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def api_key() -> str | None:
    """当前的 key；没有就返回 None（**不抛异常** —— 没 key 是合法状态，走降级）。"""
    load_env()
    value = (os.environ.get(API_KEY_ENV) or "").strip()
    return value or None


def model_name() -> str:
    return (os.environ.get("SRA_LLM_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def base_url() -> str:
    return (os.environ.get("SRA_LLM_BASE_URL") or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL


def available() -> bool:
    """现在能不能真的调 LLM（有 key 且装得上 openai SDK）。"""
    if not api_key():
        return False
    try:
        import openai  # noqa: F401
    except ImportError:
        return False
    return True


def status() -> dict[str, Any]:
    """给前端/健康检查看的**不含密钥**的配置概况（只说"有没有"，不说"是什么"）。"""
    return {
        "configured": available(),
        "provider": "deepseek",
        "model": model_name(),
        "base_url": base_url(),
        "key_env": API_KEY_ENV,
        "max_tokens": DEFAULT_MAX_TOKENS,
    }


_client: Any = None
_client_key: str | None = None


def client() -> Any:
    """取 OpenAI 兼容客户端（惰性构造，key 变了自动重建）。

    为什么 key 变了要重建：`OpenAI(api_key=...)` 把 key 存在实例里，
    复用一个旧实例 = 换了 key 却还在用旧的（测试里会很难查）。
    """
    key = api_key()
    if key is None:
        raise LLMError("llm_no_key", "没有配置模型服务 —— 本次只做确定性计算")
    global _client, _client_key
    if _client is None or _client_key != key:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMError("llm_sdk_missing", "没装 openai SDK（pip install openai）") from exc
        _client = OpenAI(api_key=key, base_url=base_url(), timeout=DEFAULT_TIMEOUT)
        _client_key = key
    return _client


# ════════════════════════════════════════════════════════════════════════
# 调用
# ════════════════════════════════════════════════════════════════════════
def chat(
    system: str,
    user: str,
    *,
    max_tokens: int | None = None,
    temperature: float = 0.0,
) -> str:
    """发一次对话，返回**最终答案文本**（推理模型的 reasoning_content 已按需回退）。

    失败一律抛 `LLMError`（带 code），**不返回空串、不返回兜底文案** ——
    "编一个答案混过去"是这一层最不能犯的错（AC-05：降级且不编造）。
    """
    payload_tokens = int(max_tokens or DEFAULT_MAX_TOKENS)
    try:
        response = client().chat.completions.create(
            model=model_name(),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=payload_tokens,
            temperature=temperature,
        )
    except LLMError:
        raise
    except Exception as exc:              # 网络/鉴权/限流/超时 —— 统一收成 LLMError
        name = type(exc).__name__
        detail = str(exc)[:300]
        # 鉴权类错误单独给码：前端能提示"key 不对"，与"网络不通"区分开
        code = "llm_auth_failed" if ("auth" in name.lower() or "401" in detail) else "llm_call_failed"
        raise LLMError(code, f"DeepSeek 调用失败（{name}）：{detail}") from exc

    choice = response.choices[0]
    message = choice.message
    content = (message.content or "").strip()
    reasoning = (getattr(message, "reasoning_content", None) or "").strip()

    if content:
        return content
    if reasoning:
        # 推理模型被 max_tokens 截断时，答案可能整段留在 reasoning_content 里 —— 用它，别丢
        return reasoning
    raise LLMError(
        "llm_empty_response",
        f"DeepSeek 返回空内容（finish_reason={choice.finish_reason}，"
        f"max_tokens={payload_tokens}）—— 未编造答案，请重试或调大 max_tokens",
    )


# ════════════════════════════════════════════════════════════════════════
# 给用户看的一句话（用户 2026-09-24：「技术细节不要出现在业务界面上」）
# ════════════════════════════════════════════════════════════════════════
# 为什么需要这张表：LLMError 的 message 是给开发者看的（异常类名、finish_reason、
# max_tokens、服务商名），它会被 service.py 拼进 notice、被 answer.py 拼进【为什么】——
# 两处都是**用户要读的文字**。所以：**原始 message 一个字不动地留在记录里**
# （record["llm"]["error"] / record["parse"]["llm_error"]，后台照样能查），
# 上界面的那一份走这张表，换成人话。
USER_FACING_ERRORS = {
    "llm_not_configured": "没有配置可用的模型服务",
    "llm_no_key": "没有配置可用的模型服务",
    "llm_sdk_missing": "模型服务组件未就绪",
    "llm_auth_failed": "模型服务鉴权没通过",
    "llm_call_failed": "这次没连上模型服务",
    "llm_empty_response": "模型这次没有返回内容",
    "llm_disabled": "本次没有让模型参与（只做确定性计算）",
}
_ERROR_FALLBACK = "模型服务暂时不可用"


def user_facing_error(llm_error: dict[str, Any] | None) -> str:
    """把 LLMError 的 code 翻成一句给用户看的话（认不出的码一律用兜底句，不回声原文）。"""
    if not llm_error:
        return _ERROR_FALLBACK
    return USER_FACING_ERRORS.get(str(llm_error.get("code") or ""), _ERROR_FALLBACK)


def last_call_info() -> dict[str, Any]:
    """最近一次调用的**非敏感**元信息（模型名/是否配置），供记录留痕。"""
    return {"provider": "deepseek", "model": model_name(), "configured": available()}


__all__ = [
    "API_KEY_ENV",
    "DEFAULT_BASE_URL",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "LLMError",
    "PROJECT_ROOT",
    "api_key",
    "available",
    "base_url",
    "chat",
    "client",
    "last_call_info",
    "load_env",
    "model_name",
    "status",
]
