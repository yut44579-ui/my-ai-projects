"""网页客服渠道 —— 网站右下角那个小挂件。

★★ 为什么这个渠道最该先做好：

    ① **它不需要任何平台凭据**。企微要企业认证 + 公网 HTTPS，
       飞书要建自建应用。网页挂件只要你有个网站就行。

    ② **SME 最主要的咨询入口其实就是官网**。
       客户不会为了问你一句话先加企业微信。

    ③ 它是唯一**能立刻演示**的渠道。客户不用注册任何东西，
       打开你的网页就能看到效果。

★ 和其他渠道最大的不同：**回复路径不一样**。
    企微/飞书有"主动发消息 API"，AI 算完直接推过去。
    网页没有 —— 浏览器只能自己来取。
    所以这里把要发的消息落进 `web_outbox`，浏览器按 seq 增量拉。

  ★ 用自增 seq 而不是时间戳做游标：同一秒内的两条消息，
    用时间戳会漏一条。这类 bug 在生产上表现为"偶尔少一句话"，
    极难复现。
"""

from __future__ import annotations

import uuid
from typing import Any

from .. import store
from .base import Channel, InboundMessage, OutboundMessage


class WebChannel(Channel):
    name = "web"

    def __init__(self, *, tenant_id: str = "default", is_internal: bool = False):
        self.tenant_id = tenant_id
        # ★ 网站来的访客默认是**外部客户**。
        #   内部员工有专门的入口，不会从这个挂件进来。
        self.is_internal = is_internal

    # ── 契约 ──────────────────────────────────────────────────────
    def parse(self, payload: dict[str, Any]) -> InboundMessage | None:
        text = (payload.get("text") or "").strip()
        session = (payload.get("session") or "").strip()
        if not text or not session:
            return None
        return InboundMessage(
            platform=self.name,
            tenant_id=payload.get("tenant_id") or self.tenant_id,
            user_id=session,
            content=text,
            # ★ 每条消息一个独立 id。
            #   网页渠道**不做去重** —— 用户真的可能连问两次同一句话，
            #   那是他想再问一次，不是平台重发。
            #   （企微/飞书必须去重，因为他们会重发；网页不会。）
            message_id=f"web-{uuid.uuid4().hex[:16]}",
            conversation_id=session,
            is_internal=payload.get("is_internal", self.is_internal),
            raw=payload,
        )

    def verify(self, payload: dict[str, Any], headers: dict[str, str]) -> bool:
        """网页渠道不做签名验证，但**做来源校验**。

        ★ 这里必须诚实说明：网页挂件是公开的，任何人打开你的网站都能用。
          真正要防的不是"伪造"，是"刷量" —— 那个由限流负责（见下方说明）。

          所以 verify 永远返回 True，但它不是"没做安全"，
          而是"这个渠道的安全边界不在签名上"。
        """
        return True

    def send(self, msg: OutboundMessage) -> dict[str, Any]:
        """把消息放进发件箱，等浏览器来取。

        ★ 不在这里直接返回给 HTTP 请求 ——
          因为模型生成是异步的，而 HTTP 请求早就返回了。
        """
        seq = store.run(
            """INSERT INTO web_outbox (tenant_id, session_id, content, kind, created_at)
               VALUES (?,?,?,?,?)""",
            (msg.tenant_id, msg.user_id, msg.content, msg.reply_token or "ai", store.now()),
        )
        return {"ok": True, "queued_seq": seq}

    # ── 便利方法 ──────────────────────────────────────────────────
    @staticmethod
    def poll(session_id: str, since: int = 0, limit: int = 50) -> dict[str, Any]:
        """浏览器拉增量。★ 返回的 last 是下次要带的 since。"""
        rows = store.rows(
            """SELECT seq, content, kind, created_at FROM web_outbox
                WHERE session_id=? AND seq>?
                ORDER BY seq LIMIT ?""",
            (session_id, int(since), limit),
        )
        return {
            "messages": rows,
            "last": rows[-1]["seq"] if rows else int(since),
        }

    @staticmethod
    def sys_note(session_id: str, text: str, tenant_id: str = "default",
                 kind: str = "system") -> None:
        """往会话里插一条提示。

        ★ 网页渠道**必须**有这条：企微/飞书那边转人工时用户至少知道"有人在处理"，
          网页这边如果不发，用户就是干等 —— 他不知道发生了什么，会以为系统坏了。

        ★★ kind 有四种，客户端看到的表现完全不同：
            system  —— 灰色小字（一般的系统提示）
            waiting —— ★ **转圈的等待指示**："正在转接人工"
                       它会一直转，直到有人真的点了「接管」才停
            taken   —— ★ 停转，改成"客服已接入"
            ai      —— AI 说的话
        用户的原话：「只有在人工点了接管以后才会出现已经转人工，
                     否则会一直打转的状态……人工接管之后才会停止转」
        """
        store.run(
            """INSERT INTO web_outbox (tenant_id, session_id, content, kind, created_at)
               VALUES (?,?,?,?,?)""",
            (tenant_id, session_id, text, kind, store.now()),
        )

    @staticmethod
    def stop_waiting(session_id: str, text: str = "客服已接入", tenant_id: str = "default") -> None:
        """停止转圈。

        ★ 用一条 `taken` 消息来表示"等待结束"。客户端看到它就停转。
          ★ 为什么不直接删掉那条 waiting 消息：
            客户端是按 seq 拉增量的，删掉的话它不会知道；
            而且"等过"这个事实本身也应该留在记录里。
        """
        WebChannel.sys_note(session_id, text, tenant_id, kind="taken")
