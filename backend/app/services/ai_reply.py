"""AI 回复编排（TASK-004）。

顺序**不许变**（D7 + D10）：

    客户消息
      → ① 代码层 PolicyGate
           命中敏感 → 落 HUMAN_REQUIRED，★ 不调 LLM（llm_called=False）
      → ② 调 LLM（DeepSeek；失败 → 落 FAILED + 人工兜底文案，绝不编造）
      → ③ 回复再过一次闸门（防止模型自己写出"已经给你 8 折"这类承诺）
           通过 → 落 sender_type=AI, ai_status=REPLIED
           命中 → 拦下，改落 HUMAN_REQUIRED（★ 草稿不落库）

★ 回复必须落库后才返回：返回值里的 message 就是库里那一行。
★ V1 站内没有真实客户渠道，"客户消息"由人工录入（sender_type=HUMAN 的那条，
  或用请求里的 content 直接给），本服务**永远不会写 sender_type=CUSTOMER**（D9）。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.customer_message import AiStatus, CustomerMessage, MessageType, SenderType
from app.services.errors import ApiErrorCode, ApiFailure
from app.services.llm import LLMUnavailable, generate_reply
from app.services.messaging import write_message
from app.services.policy_gate import PolicyDecision, evaluate_policy

# D10 的兜底文案：LLM 失败时如实告知，界面直接显示这句
LLM_FAILURE_TEXT = "AI 暂时无法回复，请人工处理"


@dataclass
class AiReplyOutcome:
    """一次 AI 回复的全部结果（接口层据此组装响应）。"""

    customer_id: int
    question: str
    decision: PolicyDecision
    llm_called: bool
    message: CustomerMessage
    failure_reason: str | None = None
    reply_blocked_reason: str | None = None

    @property
    def failed(self) -> bool:
        return self.failure_reason is not None


def resolve_question(db: Session, customer_id: int, *, content: str | None, in_reply_to: int | None) -> str:
    """取"客户问题"原文：优先用 in_reply_to 指向的那条已录消息，其次用请求里的 content。

    ★ 两种情况都不写 sender_type=CUSTOMER —— 站内没有真实客户渠道（D9）。
    """
    if in_reply_to is not None:
        message = db.get(CustomerMessage, in_reply_to)
        if message is None or message.customer_id != customer_id:
            raise ApiFailure(
                ApiErrorCode.MESSAGE_NOT_FOUND,
                f"客户 {customer_id} 下没有消息 {in_reply_to}",
                http_status=404,
            )
        return message.content

    text = (content or "").strip()
    if not text:
        raise ApiFailure(
            ApiErrorCode.EMPTY_CONTENT,
            "需要给出客户问题：content 或 in_reply_to 二选一",
        )
    return text


def _write_handover(
    db: Session,
    customer_id: int,
    *,
    content: str,
    ai_status: AiStatus,
) -> CustomerMessage:
    """转人工 / 失败的落库：sender_type=SYSTEM（**不是 AI**，因为这一轮没有 AI 回复）。"""
    return write_message(
        db,
        customer_id=customer_id,
        content=content,
        sender_type=SenderType.SYSTEM,
        message_type=MessageType.NOTE,
        ai_status=ai_status,
    )


#: 传给模型的最近对话轮数上限（一问一答算 2 条）。
#: ★ 为什么要裁：§十三 要求"有上下文""不重复"，但把全部历史塞进去
#:   会撑爆上下文、拖慢响应、也更容易让模型跑偏。取最近 6 轮足够覆盖
#:   一段连续对话，同时保持请求体小。
HISTORY_MAX_MESSAGES = 12


def _load_history(db: Session, customer_id: int, *, limit: int = HISTORY_MAX_MESSAGES) -> list[dict]:
    """取该客户最近的对话，供模型理解上下文（§十三"有上下文""不重复"）。

    ★ 只取 **AI 与人工发出的** 与 **客户发来的** 消息：
      系统消息（sender_type=SYSTEM，如"已转人工"这类状态说明）不是对话内容，
      塞进去会干扰模型。
    ★ 按时间**正序**返回（取最近的 N 条后要反转），否则模型读到的对话是倒着的。
    ★ 不包含本轮正在处理的那条 question —— 它由调用方单独传入。
    """
    rows = list(
        db.scalars(
            select(CustomerMessage)
            .where(
                CustomerMessage.customer_id == customer_id,
                CustomerMessage.sender_type.in_([SenderType.CUSTOMER, SenderType.AI, SenderType.HUMAN]),
            )
            .order_by(CustomerMessage.created_at.desc(), CustomerMessage.id.desc())
            .limit(limit)
        ).all()
    )
    history: list[dict] = []
    for m in reversed(rows):  # 转成正序
        role = "user" if m.sender_type == SenderType.CUSTOMER else "assistant"
        content = (m.content or "").strip()
        if not content:
            continue
        history.append({"role": role, "content": content})
    return history


def run_ai_reply(db: Session, customer: Customer, question: str) -> AiReplyOutcome:
    """按固定顺序跑完一轮 AI 回复，返回已落库的结果。"""
    # ① 代码层闸门（在此之前不许有任何 LLM 调用）
    decision = evaluate_policy(question)
    if decision.human_required:
        message = _write_handover(
            db,
            customer.id,
            ai_status=AiStatus.HUMAN_REQUIRED,
            content=(
                f"命中敏感策略「{decision.label}」（命中词：{decision.matched}），"
                "已转人工处理；本轮未调用 AI。"
            ),
        )
        return AiReplyOutcome(
            customer_id=customer.id,
            question=question,
            decision=decision,
            llm_called=False,
            message=message,
        )

    # ② 调 LLM（失败 → FAILED + 兜底文案，绝不编造回复）
    # ★ TASK-033：带上最近对话，让回复"有上下文、不重复"（§十三）
    try:
        reply = generate_reply(
            question,
            customer_name=customer.name,
            history=_load_history(db, customer.id),
        )
    except LLMUnavailable as exc:
        message = _write_handover(
            db,
            customer.id,
            ai_status=AiStatus.FAILED,
            content=f"{LLM_FAILURE_TEXT}（原因：{exc.reason}）",
        )
        return AiReplyOutcome(
            customer_id=customer.id,
            question=question,
            decision=decision,
            llm_called=True,
            message=message,
            failure_reason=exc.reason,
        )

    # ③ 草稿再过一次闸门：模型自己写出的"对外承诺"同样不许落库
    draft_decision = evaluate_policy(reply)
    if draft_decision.human_required:
        message = _write_handover(
            db,
            customer.id,
            ai_status=AiStatus.HUMAN_REQUIRED,
            content=(
                f"AI 草稿命中敏感策略「{draft_decision.label}」"
                f"（命中词：{draft_decision.matched}），草稿已拦截、未对外发出，请人工处理。"
            ),
        )
        return AiReplyOutcome(
            customer_id=customer.id,
            question=question,
            decision=decision,
            llm_called=True,
            message=message,
            reply_blocked_reason=draft_decision.reason,
        )

    # 通过 → 落库（sender_type=AI / ai_status=REPLIED）
    message = write_message(
        db,
        customer_id=customer.id,
        content=reply,
        sender_type=SenderType.AI,
        message_type=MessageType.CHAT,
        ai_status=AiStatus.REPLIED,
    )
    return AiReplyOutcome(
        customer_id=customer.id,
        question=question,
        decision=decision,
        llm_called=True,
        message=message,
    )


__all__ = ["LLM_FAILURE_TEXT", "AiReplyOutcome", "resolve_question", "run_ai_reply"]
