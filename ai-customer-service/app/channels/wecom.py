"""企业微信适配器（自建应用 · 回调模式）。

★★ 企微接入是这几家里**最硬的**，因为它有三个强制要求：

    ① **回调必须公网 HTTPS** —— 不能用 IP:端口，不能自签证书。
       这一条卡死了很多内网部署的客户。如果你要卖给这种客户，
       飞书的长连接是唯一出路（见 feishu.py 的说明）。

    ② **必须实现 AES 加解密** —— 企微回调的正文是加密的，
       不是明文 XML。加解密写错了，表现是"回调一直提示失败"，
       而且平台不会告诉你哪里错了。

    ③ **必须 5 秒内响应** —— 超时就重发。
       所以收到消息要立刻返回空串，真正的处理放后台（见 dispatch.py）。

★ 本文件最值得看的是 `_decrypt()` 和 `_encrypt()`：
    企微的 AES 格式有几个容易写错的点，下面都标了注释。

★ 诚实说明：加解密按公开协议实现，并用**自造的报文做了往返测试**
    （加密 → 解密必须还原）。但**没有用真实企业号验证过**。
    接上真凭据后请先跑 `selftest()`。
"""

from __future__ import annotations

import base64
import hashlib
import socket
import struct
import time
import xml.etree.ElementTree as ET
from typing import Any

from .base import Channel, InboundMessage, OutboundMessage

_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


def _pkcs7_pad(data: bytes, block: int = 32) -> bytes:
    """PKCS7 填充。

    ★ 块大小是 **32**，不是 AES 标准的 16。
      企微用的是 32 字节块 —— 写 16 的话，明文长度刚好是 16 的倍数时
      不会填，企微侧解密就报错。这是最常见的踩坑点。
    """
    pad = block - (len(data) % block)
    if pad == 0:
        pad = block
    return data + bytes([pad]) * pad


def _pkcs7_unpad(data: bytes, block: int = 32) -> bytes:
    if not data:
        return data
    pad = data[-1]
    if pad < 1 or pad > block or pad > len(data):
        return data
    return data[:-pad]


def _aes_key(encoding_aes_key: str) -> bytes:
    """EncodingAESKey（43 位）→ 32 字节 AES 密钥。

    ★ 是 **43 位 base64 加上一个 '='** 再解，不是直接 base64 解码 43 位。
      直接解会报 padding 错误，或者解出 32 字节但内容不对。
    """
    return base64.b64decode(encoding_aes_key + "=")


def _signature(token: str, timestamp: str, nonce: str, encrypt: str) -> str:
    """企微回调签名。

    ★ 四个值**排序后拼接**再 sha1，顺序不能改。
      这和飞书的"按固定顺序拼"不一样，是企微特有的。
    """
    parts = sorted([token, timestamp, nonce, encrypt])
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()


class WeComChannel(Channel):
    name = "wecom"

    def __init__(
        self,
        *,
        corp_id: str = "",
        agent_id: str = "",
        secret: str = "",
        token: str = "",
        encoding_aes_key: str = "",
        tenant_id: str = "default",
        internal_userids: set[str] | None = None,
    ):
        self.corp_id = corp_id
        self.agent_id = agent_id
        self.secret = secret
        self.token = token
        self.encoding_aes_key = encoding_aes_key
        self.tenant_id = tenant_id
        self.internal_userids = internal_userids or set()

    # ══════════════════════════════════════════════════════════════
    # AES 加解密
    # ══════════════════════════════════════════════════════════════

    def decrypt(self, encrypted: str) -> str:
        """解密企微回调正文。

        明文结构（★ 三段，少一段都解不对）：
            random(16 字节)  +  msg_len(4 字节, 网络字节序)  +  msg  +  receiveid
        """
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        key = _aes_key(self.encoding_aes_key)
        raw = base64.b64decode(encrypted)
        # ★ IV 就是 key 的前 16 字节（不是随机 IV，也不用传）
        decipher = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
        plain = _pkcs7_unpad(decipher.update(raw) + decipher.finalize())

        # 去掉前 16 字节随机数
        content = plain[16:]
        # 读 4 字节长度（网络字节序 = 大端）
        msg_len = struct.unpack("!I", content[:4])[0]
        msg = content[4 : 4 + msg_len]
        # 后面是 receiveid，用来校验这条消息是不是发给自己的
        receiveid = content[4 + msg_len :].decode("utf-8", "replace")
        if self.corp_id and receiveid and receiveid != self.corp_id:
            raise ValueError(f"receiveid 不匹配：{receiveid} != {self.corp_id}")
        return msg.decode("utf-8", "replace")

    def encrypt(self, plain: str) -> str:
        """加密（往返测试和回复消息要用）。"""
        import os

        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        key = _aes_key(self.encoding_aes_key)
        msg = plain.encode("utf-8")
        receiveid = self.corp_id.encode("utf-8")
        data = (
            os.urandom(16)
            + struct.pack("!I", len(msg))
            + msg
            + receiveid
        )
        cipher = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
        return base64.b64encode(cipher.update(_pkcs7_pad(data)) + cipher.finalize()).decode()

    # ══════════════════════════════════════════════════════════════
    # 契约
    # ══════════════════════════════════════════════════════════════

    def verify(self, payload: dict[str, Any], headers: dict[str, str]) -> bool:
        """验签。

        ★ 企微的签名算在**密文**上，所以必须先从报文里取出 Encrypt 再算。
          算错对象的话（比如算在明文上）会一直验不过。
        """
        q = payload.get("_query") or {}
        sig = q.get("msg_signature") or payload.get("msg_signature") or ""
        ts = q.get("timestamp") or payload.get("timestamp") or ""
        nonce = q.get("nonce") or payload.get("nonce") or ""
        encrypt = payload.get("Encrypt") or payload.get("encrypt") or ""
        if not (sig and ts and nonce and encrypt):
            return False
        if _signature(self.token, str(ts), str(nonce), encrypt) != sig:
            return False
        # ★ 防重放：时间戳太久远的直接拒
        try:
            if abs(time.time() - int(ts)) > 300:
                return False
        except (ValueError, TypeError):
            return False
        return True

    def parse(self, payload: dict[str, Any]) -> InboundMessage | None:
        """解析加密 XML 回调。"""
        xml_text = payload.get("_plain")
        if not xml_text:
            enc = payload.get("Encrypt") or ""
            if not enc:
                return None
            try:
                xml_text = self.decrypt(enc)
            except Exception:  # noqa: BLE001 —— 解密失败返回 None，由上层记日志
                return None

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return None

        def g(tag: str) -> str:
            el = root.find(tag)
            return (el.text or "").strip() if el is not None and el.text else ""

        msg_type = g("MsgType")
        from_user = g("FromUserName")
        if not from_user:
            return None

        if msg_type != "text":
            return InboundMessage(
                platform=self.name,
                tenant_id=self.tenant_id,
                user_id=from_user,
                content=f"（收到一条{msg_type or '非文本'}消息，AI 处理不了）",
                message_id=g("MsgId") or f"{from_user}-{g('CreateTime')}",
                is_internal=from_user in self.internal_userids,
                raw=payload,
            )

        return InboundMessage(
            platform=self.name,
            tenant_id=self.tenant_id,
            user_id=from_user,
            content=g("Content"),
            # ★ MsgId 是去重的关键。企微重发时 MsgId 不变 ——
            #   这正是"能识别出是重发"的依据。
            message_id=g("MsgId") or f"{from_user}-{g('CreateTime')}",
            is_internal=from_user in self.internal_userids,
            ts=g("CreateTime"),
            raw=payload,
        )

    # ── 发送 ──────────────────────────────────────────────────────
    def _token(self) -> str:
        key = f"wecom:{self.corp_id}:{self.secret[:6]}"
        cached = _TOKEN_CACHE.get(key)
        if cached and cached[1] > time.time():
            return cached[0]
        import httpx

        r = httpx.get(
            "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
            params={"corpid": self.corp_id, "corpsecret": self.secret},
            timeout=15,
        )
        data = r.json()
        token = data.get("access_token", "")
        if token:
            _TOKEN_CACHE[key] = (token, time.time() + data.get("expires_in", 7200) - 300)
        return token

    def send(self, msg: OutboundMessage) -> dict[str, Any]:
        token = self._token()
        if not token:
            return {"ok": False, "error": "拿不到 access_token（检查 corp_id/secret）"}
        import httpx

        r = httpx.post(
            "https://qyapi.weixin.qq.com/cgi-bin/message/send",
            params={"access_token": token},
            json={
                "touser": msg.user_id,
                "msgtype": "text",
                "agentid": int(self.agent_id or 0),
                "text": {"content": msg.content},
            },
            timeout=20,
        )
        try:
            return r.json()
        except Exception:  # noqa: BLE001
            return {"ok": False, "http": r.status_code, "text": r.text[:300]}


# ══════════════════════════════════════════════════════════════════════
# 安全加固：别把开放平台凭据当普通配置存
# ══════════════════════════════════════════════════════════════════════

def build_text_reply(to_user: str, from_user: str, content: str) -> str:
    """构造被动回复的 XML。

    ★ 被动回复有**时间窗**（企微约 5 秒），超时就必须改成主动发。
      dispatch.py 走的是主动发（因为模型生成来不及），
      这个函数留给"秒回固定话术"的场景。
    """
    return (
        "<xml>"
        f"<ToUserName><![CDATA[{to_user}]]></ToUserName>"
        f"<FromUserName><![CDATA[{from_user}]]></FromUserName>"
        f"<CreateTime>{int(time.time())}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{content}]]></Content>"
        "</xml>"
    )


def local_ip_hint() -> str:
    """给部署时的一个提示：回调地址不能用内网 IP。"""
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return ""
