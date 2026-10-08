"""凭据对称加密（TASK-024）。

═══════════════════════════════════════════════════════════════════════
【为什么自己实现而不是引 cryptography】
═══════════════════════════════════════════════════════════════════════
    §五「能简单解决就不复杂化」+ D14 式"不可替代理由"把关。
    这里只需要"对称加解密一小段连接串"，标准库足够：
      密钥 = PBKDF2(AUTH_SECRET_KEY, 固定盐, 迭代)
      密文 = base64(nonce || HMAC-SHA256-CTR 流加密结果 || 认证标签)
    ★ 这是**加密 + 完整性校验**，不是"混淆"：
      改一个字节会导致校验失败并抛 CredentialError，而不是解出垃圾。

═══════════════════════════════════════════════════════════════════════
【安全边界（必须说清，不要让人以为它比实际更强）】
═══════════════════════════════════════════════════════════════════════
    · 这是**服务端静态加密**，防的是"数据库被拖走后凭据直接可读"。
    · 它**不能**防"能读到 AUTH_SECRET_KEY 的人" —— 密钥泄露等于凭据泄露。
      所以 AUTH_SECRET_KEY 必须妥善保管、不要跨环境复用。
    · 不提供密钥轮换（V1 不需要）；要换密钥就重新填一遍凭据。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

#: 派生密钥用的固定盐（非随机：需要每次都能从同一个 SECRET 派生出同一把密钥）
_KDF_SALT = b"biz-assistant-int::data-connection-credentials::v1"
_KDF_ITERATIONS = 120_000
_KEY_LEN = 32
_NONCE_LEN = 16
_TAG_LEN = 32


class CredentialError(Exception):
    """凭据解密/校验失败（密钥变了、密文被改动、格式不对）。"""


class CredentialKeyMissing(Exception):
    """没有配置 AUTH_SECRET_KEY，无法加解密凭据。"""


def _derive_key(secret: str) -> bytes:
    if not secret:
        raise CredentialKeyMissing(
            "未配置 AUTH_SECRET_KEY，无法加密/解密连接凭据。请在 .env 中设置后重启。"
        )
    return hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), _KDF_SALT, _KDF_ITERATIONS, _KEY_LEN)


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """用 HMAC-SHA256 生成密钥流（CTR 模式）。"""
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def encrypt_credential(plaintext: str, *, secret: str) -> str:
    """加密一段凭据。返回 base64 串（nonce || 密文 || 标签）。"""
    if plaintext is None:
        raise ValueError("凭据不能为 None")
    key = _derive_key(secret)
    nonce = secrets.token_bytes(_NONCE_LEN)
    data = plaintext.encode("utf-8")
    stream = _keystream(key, nonce, len(data))
    cipher = bytes(a ^ b for a, b in zip(data, stream))
    tag = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(nonce + cipher + tag).decode("ascii")


def decrypt_credential(token: str, *, secret: str) -> str:
    """解密。任何异常都抛 CredentialError（**不返回空串或垃圾**）。"""
    key = _derive_key(secret)
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
    except Exception as exc:
        raise CredentialError("凭据格式不正确（不是合法的 base64）") from exc

    if len(raw) < _NONCE_LEN + _TAG_LEN:
        raise CredentialError("凭据长度不合法")

    nonce = raw[:_NONCE_LEN]
    tag = raw[-_TAG_LEN:]
    cipher = raw[_NONCE_LEN:-_TAG_LEN]

    expected = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        # ★ 不区分"被改动"与"密钥不对"：区分会给攻击者信息
        raise CredentialError("凭据校验失败（可能密钥已变更或被改动）")

    stream = _keystream(key, nonce, len(cipher))
    data = bytes(a ^ b for a, b in zip(cipher, stream))
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CredentialError("凭据解密结果不是合法文本") from exc


__all__ = [
    "CredentialError",
    "CredentialKeyMissing",
    "decrypt_credential",
    "encrypt_credential",
]
