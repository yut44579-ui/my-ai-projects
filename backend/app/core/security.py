"""口令哈希与令牌签发/校验（TASK-019）。规格见 docs/AUTH_SPEC.md。

═══════════════════════════════════════════════════════════════════════
【为什么不引 bcrypt / argon2 / pyjwt】
═══════════════════════════════════════════════════════════════════════
    §五「能简单解决就不复杂化」+ D14 式"不可替代理由"把关：
      · 口令：标准库 hashlib.pbkdf2_hmac 就是 PBKDF2，NIST 认可；
      · 令牌：只需签名/校验/过期，hmac + hashlib + base64 + json 足够。
    引第三方库会改依赖清单，而这里没有不可替代的理由。

═══════════════════════════════════════════════════════════════════════
【口令哈希格式】
═══════════════════════════════════════════════════════════════════════
    pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
    ★ 迭代次数写进串里 → 将来上调迭代，旧哈希仍能验证（不会把所有人锁在门外）。
    ★ 比较用 hmac.compare_digest（恒定时间），防计时侧信道。

═══════════════════════════════════════════════════════════════════════
【令牌格式】
═══════════════════════════════════════════════════════════════════════
    <base64url(payload_json)>.<base64url(hmac_sha256(payload, SECRET))>
    payload: {"sub": 用户id, "usr": 用户名, "rol": 角色, "iat": 签发, "exp": 过期, "tv": 令牌版本}
    ★ 校验顺序：格式 → 签名（恒定时间）→ 过期 → 交给调用方比对 tv / is_active
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

#: PBKDF2 迭代次数。★ 该值随硬件演进应上调；因为写进哈希串，上调不会使旧哈希失效。
PBKDF2_ITERATIONS = 210_000
PBKDF2_ALGO = "pbkdf2_sha256"
SALT_BYTES = 16
HASH_BYTES = 32


class TokenError(Exception):
    """令牌无效（格式错 / 签名不符 / 已过期）。路由层翻译成 401。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ────────────────────────────── 口令 ──────────────────────────────

def hash_password(password: str) -> str:
    """生成口令哈希串。★ 每次调用都用新的随机盐（同一口令两次结果不同）。"""
    if not password:
        raise ValueError("口令不能为空")
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS, HASH_BYTES)
    return f"{PBKDF2_ALGO}${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """校验口令。任何异常（格式损坏等）都返回 False，**不抛异常**。

    ★ 用恒定时间比较；解析失败也算不通过，绝不放行。
    """
    if not password or not stored:
        return False
    try:
        algo, iterations_s, salt_hex, hash_hex = stored.split("$")
        if algo != PBKDF2_ALGO:
            return False
        iterations = int(iterations_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False

    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, len(expected))
    return hmac.compare_digest(digest, expected)


# ────────────────────────────── 令牌 ──────────────────────────────

def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(payload_b64: str, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256)
    return _b64e(mac.digest())


def issue_token(
    *,
    user_id: int,
    username: str,
    role: str,
    token_version: int,
    secret: str,
    ttl_hours: int,
) -> tuple[str, int]:
    """签发令牌。返回 (token, 过期时间戳)。"""
    now = int(time.time())
    exp = now + ttl_hours * 3600
    payload = {
        "sub": user_id,
        "usr": username,
        "rol": role,
        "iat": now,
        "exp": exp,
        "tv": token_version,
    }
    payload_b64 = _b64e(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    return f"{payload_b64}.{_sign(payload_b64, secret)}", exp


def decode_token(token: str, secret: str) -> dict:
    """校验并解析令牌。★ 顺序：格式 → 签名 → 过期。任一步失败抛 TokenError。"""
    if not token or token.count(".") != 1:
        raise TokenError("令牌格式不正确")
    payload_b64, signature = token.split(".", 1)

    expected_sig = _sign(payload_b64, secret)
    # 恒定时间比较签名
    if not hmac.compare_digest(signature, expected_sig):
        raise TokenError("令牌签名校验失败")

    try:
        payload = json.loads(_b64d(payload_b64).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise TokenError("令牌内容无法解析") from exc

    if not isinstance(payload, dict) or "exp" not in payload or "sub" not in payload:
        raise TokenError("令牌缺少必要字段")

    if int(payload["exp"]) < int(time.time()):
        raise TokenError("令牌已过期，请重新登录")

    return payload


__all__ = [
    "PBKDF2_ITERATIONS",
    "TokenError",
    "decode_token",
    "hash_password",
    "issue_token",
    "verify_password",
]
