"""沟通记录接口（TASK-003）：人工发送 + 时间线读取。

路由层只做 HTTP 出入参转换，判定与落库全在 services/messaging.py（可被单测直接调用）。

★ 硬边界：
  · 只允许写 HUMAN / SYSTEM / IMPORT；sender_type=CUSTOMER → 400 customer_sender_forbidden（D9 / AC3）
  · 写库必带 source_type 与 evidence_ref（evidence_ref 由代码生成，不接受入参传入）
  · 业务数字（消息条数）走 EvidenceValue（AC5）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.evidence import SourceType, build_evidence, no_data_evidence
from app.services.errors import ApiErrorCode, ApiFailure
from app.schemas.messages import (
    MessageCreateRequest,
    MessageItem,
    MessageReadRequest,
    MessageTimelineResponse,
)
from app.services.messaging import (
    READ_SOURCES,
    assert_api_writable,
    list_messages,
    mark_read,
    require_customer,
    write_message,
)

router = APIRouter(tags=["customer-messages"])

MAX_PAGE_SIZE = 100


@router.post(
    "/customers/{customer_id}/messages",
    response_model=MessageItem,
    summary="人工发送一条沟通记录（真落库）",
)
def create_message(
    customer_id: int,
    payload: MessageCreateRequest,
    db: Session = Depends(get_db),
) -> MessageItem:
    """客户详情页底部输入框走这里。

    ★ 先过发送方闸门再看客户是否存在：伪造客户消息是**硬红线**，必须稳定地拿到
      400 + customer_sender_forbidden，而不是被 404 之类的判断盖过去（AC3）。
    """
    assert_api_writable(payload.sender_type)
    require_customer(db, customer_id)

    message = write_message(
        db,
        customer_id=customer_id,
        content=payload.content,
        sender_type=payload.sender_type,
        message_type=payload.message_type,
    )
    return MessageItem.model_validate(message)


@router.get(
    "/customers/{customer_id}/messages",
    response_model=MessageTimelineResponse,
    summary="该客户的沟通时间线（分页，时间正序）",
)
def list_customer_messages(
    customer_id: int,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=MAX_PAGE_SIZE),
) -> MessageTimelineResponse:
    """一条都没有 → total 是显式 NO_DATA 空态（AC4），界面显示「暂无沟通记录」而不是 0。"""
    require_customer(db, customer_id)
    rows, total = list_messages(db, customer_id, page=page, page_size=page_size)

    ref = f"customer_messages:count|customer_id={customer_id}"
    if total == 0:
        total_evidence = no_data_evidence(
            "暂无沟通记录",  # 界面按 AC4 显示这个空态；不显示任何数字
            source_type=SourceType.SYSTEM,
            evidence_ref=ref,
        )
    else:
        total_evidence = build_evidence(total, source_type=SourceType.SYSTEM, evidence_ref=ref)

    return MessageTimelineResponse(
        customer_id=customer_id,
        items=[MessageItem.model_validate(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total_evidence,
    )


@router.post(
    "/customers/{customer_id}/messages/{message_id}/read",
    response_model=MessageItem,
    summary="标记消息为客户已读（须给出真实回执来源）",
)
def mark_message_read(
    customer_id: int,
    message_id: int,
    payload: MessageReadRequest,
    db: Session = Depends(get_db),
) -> MessageItem:
    """客户已读回执（TASK-021，需求 §九 钉钉语义）。

    ★ 必须传 source（白名单：WECHAT / WEBSITE / EMAIL / CRM / MANUAL）：
      站内没有真实客户渠道（D9 禁止伪造客户行为），所以"已读"只能由真实渠道回传。
      没有来源的请求一律 400，不允许任何路径凭空造出"客户已读"。
    ★ 幂等：已读过的消息不会覆盖首次已读时间，也不会重复写事件。
    ★ 标记成功会**同时写一条 customer_events**，使"客户何时读的"出现在客户时间线上。
    """
    require_customer(db, customer_id)
    # ★ 空字符串/纯空白要返回 400 + empty_content（与项目其它空值错误码一致），
    #   而不是让 Pydantic 的 min_length 抛 422 —— 422 是"结构不对"，
    #   这里是"给了但内容为空"，语义不同，也不便于前端按错误码分支处理。
    if not (payload.source or "").strip():
        raise ApiFailure(ApiErrorCode.EMPTY_CONTENT, "必须给出已读回执来源（source）")
    message = mark_read(
        db, customer_id, message_id, source=payload.source, read_at=payload.read_at
    )
    return MessageItem.model_validate(message)
