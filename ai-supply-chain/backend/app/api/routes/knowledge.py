"""知识库接口（TASK-026，需求 §二十二）。

    GET    /api/knowledge/summary              概览
    GET    /api/knowledge/documents            资料列表
    POST   /api/knowledge/documents            导入资料（原文 + 切片）
    GET    /api/knowledge/documents/{id}       资料详情（含原文与全部切片）
    DELETE /api/knowledge/documents/{id}       移除资料（软删除）
    GET    /api/knowledge/search               关键词检索（★ 命中原因可解释）
    POST   /api/knowledge/analyze              结合资料 + 真实业务数据做分析

★ 路由顺序：/summary、/search 必须在 /documents/{id} 之前（多处已踩过同一个坑）。
★ §二十二：资料是背景知识，**不是本公司业务事实** ——
  /analyze 的响应把两类内容分成两个字段，前端也分两块展示。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocType,
    KnowledgeDocument,
)
from app.schemas.evidence import SourceType, build_evidence, no_data_evidence
from app.schemas.knowledge import (
    BusinessDataItem,
    KnowledgeAnalyzeRequest,
    KnowledgeAnalyzeResponse,
    KnowledgeChunkItem,
    KnowledgeCreateRequest,
    KnowledgeDocumentDetail,
    KnowledgeDocumentItem,
    KnowledgeListResponse,
    KnowledgeSearchHit,
    KnowledgeSearchResponse,
    KnowledgeSummaryResponse,
)
from app.services.errors import ApiErrorCode, ApiFailure
from app.services.knowledge import (
    KnowledgeError,
    analyze,
    extract_terms,
    get_document,
    import_document,
    list_chunks,
    list_documents,
    search,
)

router = APIRouter(tags=["knowledge"])

DOC_TYPE_LABELS: dict[KnowledgeDocType, str] = {
    KnowledgeDocType.PRODUCT: "产品资料",
    KnowledgeDocType.MARKET: "市场资料",
    KnowledgeDocType.USER: "用户资料",
    KnowledgeDocType.COMPETITOR: "竞品资料",
    KnowledgeDocType.BUSINESS: "商业资料",
    KnowledgeDocType.OTHER: "其他",
}

SOURCE_TYPE_LABELS: dict[str, str] = {
    "PASTED": "人工粘贴",
    "UPLOAD": "文件上传",
    "MANUAL": "人工录入",
}

#: 业务数据项的展示名（与 services/knowledge.collect_business_data 的 key 对齐）
BUSINESS_LABELS: dict[str, str] = {
    "customers_total": "客户总数",
    "customers_real": "其中真实来源（REAL）客户数",
    "opportunities_open": "进行中的商机数",
}


def _is_expired(doc: KnowledgeDocument) -> bool:
    if doc.effective_to is None:
        return False
    return doc.effective_to < datetime.now(timezone.utc).date()


def _to_item(doc: KnowledgeDocument) -> KnowledgeDocumentItem:
    return KnowledgeDocumentItem(
        id=doc.id,
        title=doc.title,
        doc_type=doc.doc_type,
        doc_type_label=DOC_TYPE_LABELS.get(doc.doc_type, doc.doc_type.value),
        source_type=doc.source_type,
        source_type_label=SOURCE_TYPE_LABELS.get(doc.source_type.value, doc.source_type.value),
        source_note=doc.source_note,
        tags=list(doc.tags_json or []),
        effective_from=doc.effective_from,
        effective_to=doc.effective_to,
        is_expired=_is_expired(doc),
        source_length=len(doc.source_text or ""),
        chunk_count=doc.chunk_count,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


def _to_detail(db: Session, doc: KnowledgeDocument) -> KnowledgeDocumentDetail:
    base = _to_item(doc)
    return KnowledgeDocumentDetail(
        **base.model_dump(),
        source_text=doc.source_text,
        chunks=[
            KnowledgeChunkItem(
                id=c.id,
                seq=c.seq,
                content=c.content,
                char_start=c.char_start,
                char_end=c.char_end,
                evidence_ref=c.evidence_ref or f"knowledge_chunk:{c.id}",
            )
            for c in list_chunks(db, doc.id)
        ],
    )


def _require(db: Session, document_id: int) -> KnowledgeDocument:
    doc = get_document(db, document_id)
    if doc is None:
        raise ApiFailure(
            ApiErrorCode.KNOWLEDGE_NOT_FOUND, f"资料 {document_id} 不存在", http_status=404
        )
    return doc


def _hit(h) -> KnowledgeSearchHit:
    return KnowledgeSearchHit(
        chunk_id=h.chunk_id,
        document_id=h.document_id,
        document_title=h.document_title,
        doc_type=h.doc_type,
        doc_type_label=DOC_TYPE_LABELS.get(h.doc_type, h.doc_type.value),
        seq=h.seq,
        content=h.content,
        score=h.score,
        matched_terms=h.matched_terms,
        evidence_ref=h.evidence_ref,
    )


@router.get("/knowledge/summary", response_model=KnowledgeSummaryResponse, summary="知识库概览")
def knowledge_summary(db: Session = Depends(get_db)) -> KnowledgeSummaryResponse:
    rows = list(
        db.scalars(
            select(KnowledgeDocument).where(KnowledgeDocument.is_active.is_(True))
        ).all()
    )
    total = len(rows)
    chunk_total = sum(r.chunk_count for r in rows)
    expired = sum(1 for r in rows if _is_expired(r))

    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r.doc_type.value] = by_type.get(r.doc_type.value, 0) + 1

    def ev_or_nodata(n: int, ref: str, empty: str):
        if total:
            return build_evidence(n, source_type=SourceType.MANUAL, evidence_ref=ref)
        return no_data_evidence(empty, source_type=SourceType.MANUAL, evidence_ref=ref)

    return KnowledgeSummaryResponse(
        total=ev_or_nodata(total, "knowledge:count", "知识库还是空的"),
        chunk_total=ev_or_nodata(chunk_total, "knowledge:chunks", "还没有任何切片"),
        expired_total=ev_or_nodata(expired, "knowledge:expired", "没有过期资料"),
        by_type=[
            {"key": k, "label": DOC_TYPE_LABELS.get(KnowledgeDocType(k), k), "count": v}
            for k, v in sorted(by_type.items(), key=lambda kv: kv[1], reverse=True)
        ],
        note=(
            "资料由人工提供原文，系统不联网抓取；检索用关键词匹配（没有向量库，"
            "因为总控提示词 §五 明确禁止向量数据库 / RAG Pipeline）。"
            "★ 知识库内容属于「背景知识」，不是本公司的业务事实 —— 分析结果里两者分开呈现。"
        ),
    )


@router.get("/knowledge/search", response_model=KnowledgeSearchResponse, summary="关键词检索")
def knowledge_search(
    q: str = Query(..., min_length=1, description="检索词（中文按 2-gram 切）"),
    doc_type: KnowledgeDocType | None = Query(default=None),
    tag: str | None = Query(default=None),
    top_k: int = Query(default=6, ge=1, le=20),
    include_expired: bool = Query(default=False, description="是否包含已过失效期的资料"),
    db: Session = Depends(get_db),
) -> KnowledgeSearchResponse:
    """检索资料片段。

    ★ 打分完全可解释：返回 `matched_terms`（命中了哪些词）与 `score`，
      让人能看懂"为什么这段被选中"，比向量相似度更便于人工核对。
    """
    hits = search(
        db, q, doc_type=doc_type, tag=tag, top_k=top_k, include_expired=include_expired
    )
    total_ev = (
        build_evidence(len(hits), source_type=SourceType.MANUAL, evidence_ref="knowledge:search")
        if hits
        else no_data_evidence(
            "没有检索到相关资料（换个说法或补充分资料）",
            source_type=SourceType.MANUAL,
            evidence_ref="knowledge:search",
        )
    )
    return KnowledgeSearchResponse(
        query=q,
        terms=extract_terms(q),
        hits=[_hit(h) for h in hits],
        total=total_ev,
        note=(
            "命中原因是「切片里包含这些检索词」，不是语义相似度。"
            "每段都能点开看它来自哪份资料的哪一段（evidence_ref 指向原文位置）。"
        ),
    )


@router.get("/knowledge/documents", response_model=KnowledgeListResponse, summary="资料列表")
def knowledge_list(
    doc_type: KnowledgeDocType | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> KnowledgeListResponse:
    rows, total = list_documents(
        db, doc_type=doc_type, limit=page_size, offset=(page - 1) * page_size
    )
    total_ev = (
        build_evidence(total, source_type=SourceType.MANUAL, evidence_ref="knowledge:list:total")
        if total
        else no_data_evidence(
            "当前筛选下没有资料", source_type=SourceType.MANUAL, evidence_ref="knowledge:list:total"
        )
    )
    return KnowledgeListResponse(
        items=[_to_item(r) for r in rows], page=page, page_size=page_size, total=total_ev
    )


@router.post(
    "/knowledge/documents",
    response_model=KnowledgeDocumentDetail,
    status_code=201,
    summary="导入资料（保存原文 + 切片）",
)
def knowledge_create(
    payload: KnowledgeCreateRequest, db: Session = Depends(get_db)
) -> KnowledgeDocumentDetail:
    """导入一份资料。★ 原文会完整留存，切片通过字符区间指回原文。"""
    try:
        doc = import_document(
            db,
            title=payload.title,
            doc_type=payload.doc_type,
            source_text=payload.source_text,
            source_type=payload.source_type,
            source_note=payload.source_note,
            tags=payload.tags,
            effective_from=payload.effective_from,
            effective_to=payload.effective_to,
        )
    except KnowledgeError as exc:
        raise ApiFailure(ApiErrorCode.KNOWLEDGE_INVALID, str(exc))
    return _to_detail(db, doc)


@router.get(
    "/knowledge/documents/{document_id}",
    response_model=KnowledgeDocumentDetail,
    summary="资料详情（含原文与全部切片）",
)
def knowledge_detail(document_id: int, db: Session = Depends(get_db)) -> KnowledgeDocumentDetail:
    return _to_detail(db, _require(db, document_id))


@router.delete(
    "/knowledge/documents/{document_id}",
    response_model=KnowledgeDocumentItem,
    summary="移除资料（软删除）",
)
def knowledge_delete(document_id: int, db: Session = Depends(get_db)) -> KnowledgeDocumentItem:
    """移除资料。

    ★ 软删除：切片一并停用（不再参与检索），但数据保留 ——
      否则之前引用过这份资料的分析就无法回溯了。
    """
    doc = _require(db, document_id)
    doc.is_active = False
    db.commit()
    db.refresh(doc)
    return _to_item(doc)


@router.post(
    "/knowledge/analyze",
    response_model=KnowledgeAnalyzeResponse,
    summary="结合资料 + 真实业务数据做分析",
)
def knowledge_analyze(
    payload: KnowledgeAnalyzeRequest, db: Session = Depends(get_db)
) -> KnowledgeAnalyzeResponse:
    """用检索到的资料片段 + 系统真实业务数据，生成分析。

    ★ 两类输入分别标注、分别返回：
        knowledge_used —— 背景知识（**不是本公司业务事实**）
        business_data  —— 系统查库所得的真实数字
      §二十二 明确要求"不能把知识库内容伪装成真实业务事实"，
      所以这里不是靠提示词，而是**结构上分开**。
    """
    outcome = analyze(
        db,
        question=payload.question,
        doc_type=payload.doc_type,
        tag=payload.tag,
        top_k=payload.top_k,
    )

    business_items: list[BusinessDataItem] = []
    for key, value in outcome.business_data.items():
        if value is None:
            continue
        business_items.append(
            BusinessDataItem(
                key=key,
                label=BUSINESS_LABELS.get(key, key),
                evidence=build_evidence(
                    value, source_type=SourceType.SYSTEM, evidence_ref=f"knowledge:biz:{key}"
                ),
            )
        )

    return KnowledgeAnalyzeResponse(
        question=payload.question,
        analysis=outcome.analysis,
        knowledge_used=[_hit(h) for h in outcome.hits],
        business_data=business_items,
        from_knowledge=outcome.from_knowledge,
        from_business_data=outcome.from_business_data,
        gaps=outcome.gaps,
        llm_called=outcome.llm_called,
        llm_error=outcome.llm_error,
        note=(
            "「参考资料」是知识库里的背景知识，不是本公司的业务事实；"
            "「真实业务数据」是系统查库所得。两者分开呈现，请勿混用。"
            + ("（AI 不可用，以上为检索原始结果，未经模型分析）" if outcome.llm_error else "")
        ),
    )
