"""营销与内容接口（TASK-018 / TASK-038）。

    GET    /api/contents                     列表（可按状态 / 类型 / 渠道筛选）
    GET    /api/contents/summary             概览（类型/渠道分布、浏览量合计、Top5）
    GET    /api/contents/length-presets      可选的篇幅档位（供 AI 生成用）
    POST   /api/contents                     新建
    POST   /api/contents/generate            ★ AI 生成正文（可参考知识库）
    GET    /api/contents/{id}                详情
    PATCH  /api/contents/{id}                更新（不含状态）
    POST   /api/contents/{id}/status         流转发布状态（校验合法性）

★ 路由顺序：静态段（/summary、/length-presets、/generate）必须注册在 /{content_id} 之前。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.content import ContentChannel, ContentStatus, ContentType
from app.models.knowledge import KnowledgeDocType
from app.schemas.contents import (
    ContentCreateRequest,
    ContentGenerateRequest,
    ContentItem,
    ContentListResponse,
    ContentStatusRequest,
    ContentStatusResponse,
    ContentSummaryResponse,
    ContentUpdateRequest,
)
from app.schemas.evidence import SourceType, build_evidence, no_data_evidence
from app.services.content import (
    ContentStatusError,
    build_summary,
    change_status,
    create_content,
    get_content,
    list_contents,
    to_item,
    update_content,
)
from app.services.content_gen import LENGTH_PRESETS, ContentGenError, generate_content
from app.services.errors import ApiErrorCode, ApiFailure

router = APIRouter(tags=["contents"])


def _require(db: Session, content_id: int):
    row = get_content(db, content_id)
    if row is None:
        raise ApiFailure(
            ApiErrorCode.CONTENT_NOT_FOUND, f"内容 {content_id} 不存在", http_status=404
        )
    return row


@router.get("/contents/summary", response_model=ContentSummaryResponse, summary="内容概览")
def contents_summary(db: Session = Depends(get_db)) -> ContentSummaryResponse:
    return build_summary(db)


@router.get("/contents/length-presets", summary="AI 生成可选的篇幅档位")
def content_length_presets() -> dict:
    """可选的篇幅档位。

    ★ 为什么给"档位"而不是任意字数输入：
      模型无法稳定满足"写 473 字"这种要求，给区间（如 500~700 字）才能稳定落点。
      用户看到的这几个选项就是实际会传给模型的区间。
    ★ 这只是**写作目标**，不是存储上限 —— 正文字段用 Text，不受此约束。
    """
    return {
        "items": [
            {"value": k, "label": v["label"], "range": v["range"]}
            for k, v in LENGTH_PRESETS.items()
        ],
        "note": (
            "篇幅是「写作目标区间」，不是字数上限 —— "
            "正文用 Text 存储，你想写多长都可以；这几档只是让 AI 有个落点。"
        ),
    }


@router.post("/contents/generate", summary="AI 生成正文（可参考知识库）")
def contents_generate(payload: ContentGenerateRequest, db: Session = Depends(get_db)) -> dict:
    """AI 生成一篇内容。

    ★ 两种模式（对应用户说的两种情况）：
      · `use_knowledge=True`：先把知识库里相关的资料检索出来作为**事实依据**；
      · `use_knowledge=False`：不检索，纯按通用场景写作。
    ★ 无论哪种，提示词都明确禁止编造产品参数/案例/客户名/数字；
      知识库没命中时**不假装有依据**。
    ★ 生成结果**不直接入库** —— 先返回给前端预览，人看过再决定是否保存。
      这样避免"AI 写的东西悄悄变成正式内容"。
    """
    doc_type = KnowledgeDocType(payload.doc_type) if payload.doc_type else None
    try:
        outcome = generate_content(
            db,
            topic=payload.topic,
            content_type=payload.content_type,
            channel=payload.channel.value,
            target_length=payload.target_length,
            doc_type=doc_type,
            top_k=payload.top_k,
            use_knowledge=payload.use_knowledge,
        )
    except ContentGenError as exc:
        raise ApiFailure(ApiErrorCode.CONTENT_GEN_INVALID, str(exc))

    if outcome.llm_error:
        raise ApiFailure(
            ApiErrorCode.LLM_UNAVAILABLE,
            f"AI 暂时不可用，无法生成内容（原因：{outcome.llm_error}）",
            http_status=502,
        )

    return {
        "title": outcome.title,
        "summary": outcome.summary,
        "body": outcome.body,
        "char_count": outcome.char_count,
        "target_range": (LENGTH_PRESETS.get(payload.target_length) or LENGTH_PRESETS["MEDIUM"])[
            "range"
        ],
        "used_knowledge": bool(outcome.hits),
        "sources": [
            {
                "evidence_ref": h.evidence_ref,
                "document_title": h.document_title,
                "seq": h.seq,
                "score": h.score,
            }
            for h in outcome.hits
        ],
        "note": (
            (
                f"已生成约 {outcome.char_count} 字，引用了 {len(outcome.hits)} 条知识库资料。"
                if outcome.hits
                else (
                    f"已生成约 {outcome.char_count} 字。"
                    + (
                        "★ 知识库里没有命中相关资料，本次按通用场景写作，"
                        "内容不含具体数字与客户名 —— 请务必人工核对后再用。"
                        if payload.use_knowledge
                        else "本次按通用场景写作（未使用知识库），内容不含具体数字与客户名。"
                    )
                )
            )
            + " ★ 生成结果尚未保存，确认后再点「创建」或「保存正文」。"
        ),
    }


@router.get("/contents", response_model=ContentListResponse, summary="内容列表")
def contents_list(
    status: ContentStatus | None = Query(default=None),
    content_type: ContentType | None = Query(default=None),
    channel: ContentChannel | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> ContentListResponse:
    items, total = list_contents(
        db,
        status=status,
        content_type=content_type,
        channel=channel,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    total_ev = (
        build_evidence(total, source_type=SourceType.SYSTEM, evidence_ref="contents:list:total")
        if total
        else no_data_evidence(
            "当前筛选下没有内容", source_type=SourceType.SYSTEM, evidence_ref="contents:list:total"
        )
    )
    return ContentListResponse(items=items, page=page, page_size=page_size, total=total_ev)


@router.post("/contents", response_model=ContentItem, status_code=201, summary="新建内容")
def contents_create(payload: ContentCreateRequest, db: Session = Depends(get_db)) -> ContentItem:
    return to_item(create_content(db, **payload.model_dump()))


@router.get("/contents/{content_id}", response_model=ContentItem, summary="内容详情")
def contents_detail(content_id: int, db: Session = Depends(get_db)) -> ContentItem:
    return to_item(_require(db, content_id))


@router.patch("/contents/{content_id}", response_model=ContentItem, summary="更新内容")
def contents_update(
    content_id: int, payload: ContentUpdateRequest, db: Session = Depends(get_db)
) -> ContentItem:
    """更新内容字段。★ 状态请走 /status。"""
    row = _require(db, content_id)
    return to_item(update_content(db, row, **payload.model_dump(exclude_unset=True)))


@router.post(
    "/contents/{content_id}/status",
    response_model=ContentStatusResponse,
    summary="流转发布状态",
)
def contents_status(
    content_id: int, payload: ContentStatusRequest, db: Session = Depends(get_db)
) -> ContentStatusResponse:
    """状态流转。★ 非法流转 → 400；同状态 → changed=false（不报错）。

    允许「待审核 → 草稿」退回修改（内容审核的真实流程），其余向后回退一律拒绝。
    """
    row = _require(db, content_id)
    try:
        updated, changed, msg = change_status(
            db, row, target=payload.status, published_on=payload.published_on
        )
    except ContentStatusError as exc:
        raise ApiFailure(ApiErrorCode.INVALID_CONTENT_STATUS, exc.reason, http_status=400)
    return ContentStatusResponse(content=to_item(updated), changed=changed, message=msg)
