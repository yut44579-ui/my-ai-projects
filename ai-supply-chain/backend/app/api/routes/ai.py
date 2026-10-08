"""AI 回复接口（TASK-004）：POST /api/customers/{id}/ai-reply

顺序固定：① 代码层闸门 → ② LLM → ③ 回复落库（详见 services/ai_reply.py）。

HTTP 语义：
  · 200 —— 已落库并返回那一行（REPLIED 的 AI 回复 / HUMAN_REQUIRED 的转人工说明）
  · 502 —— LLM 不可用：**已经落了一行 FAILED**（人工兜底文案），响应体带明确错误码
           （D10：如实告知"AI 暂时无法回复，请人工处理"，严禁编造回复顶上）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.ai import AiReplyRequest, AiReplyResponse, PolicyOutcome
from app.schemas.messages import MessageItem
from app.services.ai_reply import LLM_FAILURE_TEXT, resolve_question, run_ai_reply
from app.services.errors import ApiErrorCode, ApiFailure
from app.services.messaging import require_customer

router = APIRouter(tags=["customer-ai"])


@router.post(
    "/customers/{customer_id}/ai-reply",
    response_model=AiReplyResponse,
    summary="对客户问题生成 AI 回复（敏感先转人工，不调 LLM）",
)
def ai_reply(
    customer_id: int,
    payload: AiReplyRequest,
    db: Session = Depends(get_db),
) -> AiReplyResponse:
    customer = require_customer(db, customer_id)
    question = resolve_question(
        db, customer_id, content=payload.content, in_reply_to=payload.in_reply_to
    )

    outcome = run_ai_reply(db, customer, question)
    body = AiReplyResponse(
        customer_id=outcome.customer_id,
        question=outcome.question,
        llm_called=outcome.llm_called,
        policy=PolicyOutcome(
            allowed=outcome.decision.allowed,
            reason=outcome.decision.reason,
            category=outcome.decision.category.value if outcome.decision.category else None,
            label=outcome.decision.label,
            matched=outcome.decision.matched,
            tone=outcome.decision.tone,
        ),
        ai_status=outcome.message.ai_status,
        message=MessageItem.model_validate(outcome.message),
        failure_reason=outcome.failure_reason,
    )

    if outcome.failed:
        # ★ 兜底那一行已经落库了，这里只是把它如实告诉前端（502 + 明确错误码）
        raise ApiFailure(
            ApiErrorCode.LLM_UNAVAILABLE,
            LLM_FAILURE_TEXT,
            http_status=502,
            detail={
                "customer_id": outcome.customer_id,
                "message_id": outcome.message.id,
                "ai_status": outcome.message.ai_status.value,
                "reason": outcome.failure_reason,
                # ★ 不要用 "message" 这个键名：它会盖掉顶层的人工兜底文案
                "result": body.model_dump(mode="json"),
            },
        )
    return body
