"""AI 供应商服务（TASK-037）。

═══════════════════════════════════════════════════════════════════════
【为什么需要"适配层"而不是一堆 if】
═══════════════════════════════════════════════════════════════════════
    用户要求能切换 DeepSeek / GPT / Claude / Qwen / Gemini。
    它们**不是同一种协议**：
      · OpenAI 兼容（DeepSeek / OpenAI / Qwen / 智谱…）：POST {base}/chat/completions
        请求 {"model","messages":[...]}，响应 choices[0].message.content
      · Anthropic（Claude）：POST {base}/v1/messages
        请求还要 max_tokens，且 system 是**顶层字段**而不是 messages 里的一条；
        响应 content 是**块数组**，要拼出 text 块。鉴权头是 x-api-key 而非 Bearer。
      · Gemini：POST {base}/models/{model}:generateContent?key=...
        system 走 system_instruction，响应 candidates[0].content.parts[].text。

    ★ 所以按 **protocol** 分派（3 种），而不是按供应商名写 5 个分支 ——
      将来加一家 OpenAI 兼容的供应商，只要登记 protocol 即可，不用改代码。

═══════════════════════════════════════════════════════════════════════
【API Key 加密，且只能"是否已配置"对外】
═══════════════════════════════════════════════════════════════════════
    复用 D40 的 core/crypto.py（PBKDF2 派生密钥 + HMAC 流加密 + 认证标签）。
    接口**永不回显 key**，只返回 has_key。更新时传空串表示"不改动"。

═══════════════════════════════════════════════════════════════════════
【生效的供应商：数据库优先，.env 兜底】
═══════════════════════════════════════════════════════════════════════
    ★ 数据库里没有任何启用的供应商时，回退到 `.env` 的 DEEPSEEK_* 配置 ——
      否则新部署的系统会因为"还没在界面里配"而 AI 全不可用。
      这条回退是**刻意的兼容**，不是遗漏。
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crypto import (
    CredentialError,
    CredentialKeyMissing,
    decrypt_credential,
    encrypt_credential,
)
from app.models.user_profile import (
    PROVIDER_PRESETS,
    AiProtocol,
    AiProviderConfig,
    AiProviderKind,
)


class ProviderError(Exception):
    """供应商配置相关业务错误（路由层翻译成 400）。"""


@dataclass
class ResolvedProvider:
    """真正发给 HTTP 客户端的一组配置（已解密）。"""

    kind: AiProviderKind
    protocol: AiProtocol
    base_url: str
    model: str
    api_key: str
    timeout: float
    #: 来源：DATABASE（界面里配的）或 ENV（.env 兜底）
    source: str


def _secret() -> str:
    return (settings.auth_secret_key or "").strip()


def list_providers(db: Session) -> list[AiProviderConfig]:
    """列出所有已登记的供应商配置。"""
    return list(db.scalars(select(AiProviderConfig).order_by(AiProviderConfig.id.asc())).all())


def get_provider(db: Session, kind: AiProviderKind) -> AiProviderConfig | None:
    return db.scalar(select(AiProviderConfig).where(AiProviderConfig.kind == kind))


def ensure_provider(db: Session, kind: AiProviderKind) -> AiProviderConfig:
    """取该供应商的配置行，没有就按预置值创建。

    ★ 预置只填**公开的官方 base_url 与常见模型名**，api_key 必须用户自己填。
    """
    row = get_provider(db, kind)
    if row is not None:
        return row

    preset = PROVIDER_PRESETS[kind]
    row = AiProviderConfig(
        kind=kind,
        protocol=preset["protocol"],
        base_url=preset["base_url"],
        model=preset["model"],
        api_key_encrypted=None,
        is_active=False,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def upsert_provider(
    db: Session,
    kind: AiProviderKind,
    *,
    base_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> AiProviderConfig:
    """新建或更新一个供应商配置。

    ★ `api_key` 传 None = **不改动**（界面里留空提交时的语义）；
      传空串 = 清空（用户想删掉这个 key）。
    """
    row = ensure_provider(db, kind)

    if base_url is not None:
        row.base_url = base_url.strip()
    if model is not None:
        row.model = model.strip()

    if api_key is not None:
        key = api_key.strip()
        if not key:
            row.api_key_encrypted = None
        else:
            try:
                row.api_key_encrypted = encrypt_credential(key, secret=_secret())
            except CredentialKeyMissing as exc:
                raise ProviderError(str(exc)) from exc
        # ★ 换了 key 就把上次的测试结论作废（同 D40：状态只能由真实结果驱动）
        row.last_test_at = None
        row.last_test_ok = None
        row.last_error = None

    db.commit()
    db.refresh(row)
    return row


def activate_provider(db: Session, kind: AiProviderKind) -> AiProviderConfig:
    """把某个供应商设为生效项。

    ★ 全局只允许一个生效：先把其它行的 is_active 清掉。
      否则"当前用的是哪家"就变成不确定的（多条 is_active=True 时行为未定义）。
    """
    row = ensure_provider(db, kind)
    for other in list_providers(db):
        other.is_active = other.id == row.id
    db.commit()
    db.refresh(row)
    return row


def has_key(row: AiProviderConfig) -> bool:
    return bool(row.api_key_encrypted)


def decrypted_key(row: AiProviderConfig) -> str:
    """解密 API Key。失败一律抛错，**绝不回退成空串去请求**。"""
    if not row.api_key_encrypted:
        raise ProviderError("该供应商还没有配置 API Key")
    try:
        return decrypt_credential(row.api_key_encrypted, secret=_secret())
    except CredentialKeyMissing as exc:
        raise ProviderError(str(exc)) from exc
    except CredentialError as exc:
        raise ProviderError(f"API Key 无法解密：{exc}") from exc


def resolve_active(db: Session) -> ResolvedProvider | None:
    """取当前生效的供应商配置。

    ★ 数据库里没有启用的 → 回退 `.env` 的 DEEPSEEK_*。
      新部署系统还没在界面里配时，AI 仍然可用（兼容性兜底，非遗漏）。
    """
    row = db.scalar(select(AiProviderConfig).where(AiProviderConfig.is_active.is_(True)))
    if row is not None and row.base_url and row.model and has_key(row):
        return ResolvedProvider(
            kind=row.kind,
            protocol=row.protocol,
            base_url=row.base_url.rstrip("/"),
            model=row.model,
            api_key=decrypted_key(row),
            timeout=settings.llm_timeout_seconds,
            source="DATABASE",
        )

    env_key = (settings.deepseek_api_key or "").strip()
    if env_key:
        return ResolvedProvider(
            kind=AiProviderKind.DEEPSEEK,
            protocol=AiProtocol.OPENAI_COMPATIBLE,
            base_url=settings.deepseek_base_url.rstrip("/"),
            model=settings.deepseek_model,
            api_key=env_key,
            timeout=settings.llm_timeout_seconds,
            source="ENV",
        )
    return None


# ══════════════════════════════════════════════════════════════════════
# 各协议：组装请求 / 解析响应
# ══════════════════════════════════════════════════════════════════════

def build_request(
    provider: ResolvedProvider,
    *,
    system_prompt: str,
    messages: list[dict],
) -> tuple[str, dict, dict]:
    """按协议组装 (url, json_body, headers)。"""
    p = provider.protocol

    if p == AiProtocol.OPENAI_COMPATIBLE:
        url = f"{provider.base_url}/chat/completions"
        body = {
            "model": provider.model,
            "messages": [{"role": "system", "content": system_prompt}, *messages],
            "temperature": 0.3,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json",
        }
        return url, body, headers

    if p == AiProtocol.ANTHROPIC:
        url = f"{provider.base_url}/v1/messages"
        # ★ Claude 的 system 是**顶层字段**，不放进 messages
        body = {
            "model": provider.model,
            "system": system_prompt,
            # ★ max_tokens 是必填项，不传会 400
            "max_tokens": 1024,
            "messages": messages,
        }
        headers = {
            "x-api-key": provider.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        return url, body, headers

    if p == AiProtocol.GEMINI:
        # ★ Gemini 的 key 走 query 参数，且模型名在路径里
        url = f"{provider.base_url}/models/{provider.model}:generateContent?key={provider.api_key}"
        body = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            # Gemini 的角色名是 user / model，不是 assistant
            "contents": [
                {
                    "role": "model" if m.get("role") == "assistant" else "user",
                    "parts": [{"text": str(m.get("content") or "")}],
                }
                for m in messages
            ],
        }
        headers = {"Content-Type": "application/json"}
        return url, body, headers

    raise ProviderError(f"不支持的协议：{p}")


def parse_response(provider: ResolvedProvider, data: dict) -> str:
    """按协议从响应里取正文。取不到就抛错，**绝不返回空串冒充回答**。"""
    p = provider.protocol

    if p == AiProtocol.OPENAI_COMPATIBLE:
        try:
            return str(data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"响应结构不符合 OpenAI 兼容格式：{exc}") from exc

    if p == AiProtocol.ANTHROPIC:
        # ★ content 是**块数组**，要拼出所有 type=text 的块
        try:
            blocks = data["content"]
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            return text.strip()
        except (KeyError, TypeError) as exc:
            raise ProviderError(f"响应结构不符合 Anthropic 格式：{exc}") from exc

    if p == AiProtocol.GEMINI:
        try:
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(str(x.get("text") or "") for x in parts).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"响应结构不符合 Gemini 格式：{exc}") from exc

    raise ProviderError(f"不支持的协议：{p}")


def test_provider(
    db: Session, kind: AiProviderKind, *, timeout: float | None = None
) -> tuple[bool, str, int]:
    """真实连通性测试：发一句最小请求，看能不能拿到正文。

    ★ 和 D40 一样：**状态只能由真实请求结果驱动**，不允许人工标"可用"。
      返回 (ok, message, elapsed_ms)。
    """
    import time

    row = ensure_provider(db, kind)
    started = time.perf_counter()

    if not row.base_url or not row.model:
        msg = "请先填写接口地址与模型名"
        _record_test(db, row, ok=False, error=msg)
        return False, msg, 0

    if not has_key(row):
        msg = "请先填写 API Key"
        _record_test(db, row, ok=False, error=msg)
        return False, msg, 0

    provider = ResolvedProvider(
        kind=row.kind,
        protocol=row.protocol,
        base_url=row.base_url.rstrip("/"),
        model=row.model,
        api_key=decrypted_key(row),
        timeout=timeout or settings.llm_timeout_seconds,
        source="DATABASE",
    )

    try:
        url, body, headers = build_request(
            provider,
            system_prompt="你是一个测试探针。",
            messages=[{"role": "user", "content": "回复「ok」两个字即可。"}],
        )
        resp = httpx.post(url, json=body, headers=headers, timeout=provider.timeout)
        elapsed = int((time.perf_counter() - started) * 1000)

        if resp.status_code != 200:
            # ★ 把对方返回的原文带上（截断），让人能自己判断是 key 错还是额度问题
            detail = resp.text[:300]
            msg = f"HTTP {resp.status_code}：{detail}"
            _record_test(db, row, ok=False, error=msg)
            return False, msg, elapsed

        text = parse_response(provider, resp.json())
        if not text:
            msg = "接口返回成功，但正文为空（可能是模型名不对）"
            _record_test(db, row, ok=False, error=msg)
            return False, msg, elapsed

        msg = f"连接成功，模型回复：{text[:40]}"
        _record_test(db, row, ok=True)
        return True, msg, elapsed

    except Exception as exc:  # noqa: BLE001 - 真实错误如实回传
        elapsed = int((time.perf_counter() - started) * 1000)
        msg = f"{type(exc).__name__}: {str(exc)[:250]}"
        _record_test(db, row, ok=False, error=msg)
        return False, msg, elapsed


def _record_test(db: Session, row: AiProviderConfig, *, ok: bool, error: str | None = None) -> None:
    from datetime import datetime, timezone

    row.last_test_at = datetime.now(timezone.utc)
    row.last_test_ok = ok
    row.last_error = None if ok else (error or "")[:500]
    db.commit()


__all__ = [
    "ProviderError",
    "ResolvedProvider",
    "activate_provider",
    "build_request",
    "decrypted_key",
    "ensure_provider",
    "get_provider",
    "has_key",
    "list_providers",
    "parse_response",
    "resolve_active",
    "test_provider",
    "upsert_provider",
]
