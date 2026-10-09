"""模拟渠道 —— 不依赖任何平台凭据，用来端到端验证整条链路。

★ 为什么需要一个"假"渠道：
  真实渠道（企微/飞书）都要企业账号和公网地址才能测。
  但**"收到消息 → 去重 → 异步处理 → 按判定路由 → 回复"**这套逻辑
  才是最容易出错的地方，而且它跟平台无关。

  有了模拟渠道，这套逻辑可以**完全离线地测**；
  等真接了飞书/企微，只需再验证"翻译层"写对了没有。

★ 它还解决另一个问题：**演示**。
  客户不想为了看效果先去注册企业微信。
"""

from __future__ import annotations

import uuid
from typing import Any

from .base import Channel, InboundMessage, OutboundMessage


class SimChannel(Channel):
    """模拟渠道。发出的消息存在内存里，测试和演示都能读。"""

    name = "sim"

    def __init__(self, *, is_internal: bool = False, tenant_id: str = "default"):
        self.is_internal = is_internal
        self.tenant_id = tenant_id
        self.sent: list[OutboundMessage] = []
        # ★ 模拟"平台会重发"：可以让同一个 message_id 发两次，
        #   用来验证去重真的生效。
        self._seq = 0

    # ── 契约 ──────────────────────────────────────────────────────
    def parse(self, payload: dict[str, Any]) -> InboundMessage | None:
        text = (payload.get("text") or "").strip()
        if not text:
            return None
        self._seq += 1
        return InboundMessage(
            platform=self.name,
            tenant_id=payload.get("tenant_id") or self.tenant_id,
            user_id=payload.get("user_id") or "sim-user",
            content=text,
            # ★ 允许调用方指定 message_id，用来模拟平台重发
            message_id=payload.get("message_id") or f"sim-{self._seq}-{uuid.uuid4().hex[:8]}",
            conversation_id=payload.get("conversation_id") or "sim-conv",
            is_internal=payload.get("is_internal", self.is_internal),
            raw=payload,
        )

    def verify(self, payload: dict[str, Any], headers: dict[str, str]) -> bool:
        # 模拟渠道不验签，但保留这个契约 —— 真实渠道必须实现。
        return True

    def send(self, msg: OutboundMessage) -> dict[str, Any]:
        self.sent.append(msg)
        return {"ok": True, "platform": self.name, "to": msg.user_id}

    # ── 便利方法 ──────────────────────────────────────────────────
    def last(self) -> str:
        return self.sent[-1].content if self.sent else ""

    def texts(self) -> list[str]:
        return [m.content for m in self.sent]
