"""渠道接入的契约。

★ 设计原则：核心逻辑**不依赖任何平台**（和连接器同一套思路）。

    各家平台的 API 天差地别（企微要 AES 加密回调、飞书有长连接、
    钉钉又是另一套），但它们对上层只需要提供两件事：
      ① 把平台的消息转成统一的 InboundMessage
      ② 把统一的 OutboundMessage 转成平台能发的格式

★ 平台适配器**只做翻译，不做判断**。
    不该在适配器里写"这句话该不该转人工" ——
    那样每家平台都要实现一遍，必然对不上（另两个项目都踩过这个坑）。
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class InboundMessage:
    """统一入站消息。所有平台的消息都转成它。"""

    platform: str                 # feishu / wecom / web / sim
    tenant_id: str
    user_id: str                  # 平台内的用户标识
    content: str                  # 纯文本内容
    message_id: str               # ★ 平台消息 ID，用于去重
    conversation_id: str | None = None
    is_internal: bool = True      # 内部员工还是外部客户
    ts: str | None = None
    # ★ 保留原始报文。出问题时这是唯一能还原现场的东西 ——
    #   平台字段会变，解析器会写错，没有原始报文就只能靠猜。
    raw: dict[str, Any] = field(default_factory=dict)
    # 平台侧的回执信息（回复时要用，比如企微的 open_kfid）
    reply_token: str | None = None

    def dedup_key(self) -> str:
        """去重键。

        ★ 去重是必须的，不是优化：
          企微要求 5 秒内响应，超时会**重发同一条消息**。
          不去重的话，用户问一句会被答两遍 —— 而且两遍可能不一样。
        """
        base = f"{self.platform}:{self.tenant_id}:{self.message_id}"
        return hashlib.sha256(base.encode("utf-8")).hexdigest()[:32]


@dataclass
class OutboundMessage:
    """统一出站消息。"""

    platform: str
    tenant_id: str
    user_id: str
    content: str
    conversation_id: str | None = None
    reply_token: str | None = None
    # 发出去的原因，便于日志和排查（auto/draft 确认/人工）
    reason: str = ""


class Channel(ABC):
    """渠道适配器接口。"""

    name: str = "base"

    @abstractmethod
    def parse(self, payload: dict[str, Any]) -> InboundMessage | None:
        """把平台回调的原始报文转成 InboundMessage。

        返回 None 表示这条不用处理（比如平台发来的心跳、事件通知）。
        ★ 不抛异常：解析失败要**如实返回 None 并记日志**，
          不能因为一条格式怪的报文把整个回调打挂。
        """

    @abstractmethod
    def verify(self, payload: dict[str, Any], headers: dict[str, str]) -> bool:
        """验签。

        ★ 这是安全关键：不验签的话，**任何人都能伪造消息让 AI 回复**。
          而且伪造的请求会真的消耗模型额度。
        """

    @abstractmethod
    def send(self, msg: OutboundMessage) -> dict[str, Any]:
        """把消息发回平台。返回平台响应（便于排查）。"""

    def describe(self) -> str:
        return self.name
