"""AI 回复接口的 Pydantic 契约（TASK-004）。"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.models.customer_message import AiStatus
from app.schemas.messages import MessageItem


class AiReplyRequest(BaseModel):
    """POST /api/customers/{id}/ai-reply 的入参。

    ★ 站内没有真实客户渠道（D9）：客户问题用两种方式给 ——
      · content：人工把客户原话贴进来（不落 CUSTOMER 行）
      · in_reply_to：指向该客户下**已录入**的一条消息（人工录入的那条）
    两者必须给一个。
    """

    content: str | None = Field(default=None, max_length=4000, description="客户问题原文")
    in_reply_to: int | None = Field(default=None, description="该客户下已录入消息的 id")

    @model_validator(mode="after")
    def _need_question(self) -> "AiReplyRequest":
        has_content = bool((self.content or "").strip())
        if not has_content and self.in_reply_to is None:
            raise ValueError("content 与 in_reply_to 至少要给一个")
        return self


class PolicyOutcome(BaseModel):
    """闸门结论（原样回给界面，便于显示"为什么转人工"）。"""

    allowed: bool
    reason: str
    category: str | None = None
    label: str | None = None
    matched: str | None = None
    tone: str = "NONE"


class AiReplyResponse(BaseModel):
    """一次 AI 回复的结果。★ message 一定是**已落库**的那一行（先落库后返回）。"""

    customer_id: int
    question: str = Field(description="用于判定与生成的问题原文（人工录入/请求里给的）")
    llm_called: bool = Field(description="本轮是否真的调用了 LLM（命中敏感时恒为 false）")
    policy: PolicyOutcome = Field(description="入口闸门的结论")
    ai_status: AiStatus = Field(description="落库那行的 AI 状态")
    message: MessageItem = Field(description="已落库的消息行（AI 回复或转人工说明）")
    failure_reason: str | None = Field(default=None, description="LLM 失败原因（成功时为 null）")
