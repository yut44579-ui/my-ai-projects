"""AI 辅助填写接口（TASK-042）。

    GET  /api/ai/draft-fields     哪些字段可以 AI 生成（前端据此决定显示按钮）
    POST /api/ai/draft            生成一段可填入表单的文字

★ 为什么要有 `draft-fields` 这个"白名单"接口：
  让**后端**决定哪些字段可以生成，前端不自己判断。
  这样"不能生成的字段"（原文、金额、需求）在前端**根本不会出现按钮** ——
  用户看不到按钮，就不会问"为什么这里没有 AI"。
  比"显示了按钮再弹窗拒绝"体验好，也避免了前端各页各写一套判断。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.knowledge import KnowledgeDocType
from app.services.ai_draft import DraftError, generate_draft, list_fields
from app.services.errors import ApiErrorCode, ApiFailure

router = APIRouter(tags=["ai-draft"])


class DraftRequest(BaseModel):
    purpose: str = Field(description="字段类型；取值见 GET /api/ai/draft-fields")
    context: dict = Field(
        default_factory=dict,
        description="已知信息（如 name/title/customer）；★ 只用这里给的信息，缺的一律不提",
    )
    hint: str | None = Field(default=None, max_length=500, description="用户补充说明")
    use_knowledge: bool = Field(default=False, description="是否参考知识库资料")
    doc_type: str | None = None
    top_k: int = Field(default=3, ge=1, le=10)


class DraftResponse(BaseModel):
    text: str
    purpose: str
    label: str
    note: str
    sources: list[dict] = Field(default_factory=list)


@router.get("/ai/draft-fields", summary="可 AI 生成的字段白名单")
def get_draft_fields() -> dict:
    return {
        "items": list_fields(),
        "note": (
            "只有这些描述性文字字段可以 AI 生成。"
            "事实性字段（公开资料原文、金额、进度、客户需求、联系方式）"
            "没有 AI 按钮，因为它们必须是真实的 —— 编了就失去意义。"
        ),
        # ★ 明确列出"刻意不提供 AI"的字段及原因，便于界面统一解释
        "excluded": [
            {"label": "公开资料原文", "why": "它是事实来源；AI 只从原文抽事实并逐字回验，原文若由 AI 写，验证就没意义了"},
            {"label": "客户需求 / 背景", "why": "必须是客户真实说过的话，编了等于伪造需求"},
            {"label": "金额 / 折扣 / 账期", "why": "是事实数字，必须人工填（系统里所有数字都要有真实来源）"},
            {"label": "进度 / 概率 / 优先级", "why": "是人的判断，不是文字组织"},
            {"label": "联系方式", "why": "是事实，不许生成"},
        ],
    }


@router.post("/ai/draft", response_model=DraftResponse, summary="AI 生成可填入表单的文字")
def post_draft(payload: DraftRequest, db: Session = Depends(get_db)) -> DraftResponse:
    """生成一段可填入表单的文字。

    ★ 生成结果**只是草稿**：前端填进输入框，用户可改，改完再保存。
      AI 不直接写库 —— 否则"人确认过"这件事就没有了。
    """
    doc_type = KnowledgeDocType(payload.doc_type) if payload.doc_type else None
    try:
        outcome = generate_draft(
            db,
            purpose=payload.purpose,
            context=payload.context,
            hint=payload.hint,
            use_knowledge=payload.use_knowledge,
            doc_type=doc_type,
            top_k=payload.top_k,
        )
    except DraftError as exc:
        raise ApiFailure(ApiErrorCode.DRAFT_INVALID, str(exc))

    if outcome.llm_error:
        raise ApiFailure(
            ApiErrorCode.LLM_UNAVAILABLE,
            f"AI 暂时不可用，无法生成（原因：{outcome.llm_error}）",
            http_status=502,
        )

    from app.services.ai_draft import FIELDS

    label = FIELDS[payload.purpose].label
    return DraftResponse(
        text=outcome.text,
        purpose=payload.purpose,
        label=label,
        note=(
            f"已生成{label}草稿（{len(outcome.text)} 字）"
            + (f"，参考了 {len(outcome.hits)} 条知识库资料" if outcome.hits else "")
            + "。★ 这只是草稿：请核对后自行修改，确认没问题再保存。"
        ),
        sources=[
            {
                "evidence_ref": h.evidence_ref,
                "document_title": h.document_title,
                "seq": h.seq,
            }
            for h in outcome.hits
        ],
    )
