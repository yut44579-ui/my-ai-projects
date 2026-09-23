"""api_documents.py · Word/PDF 上传与提取的 HTTP 端点（TASK-003）。

════════════════════════════════════════════════════════════════════════
【为什么是一个独立文件，而不是往 api.py 里塞】
════════════════════════════════════════════════════════════════════════
api.py 里那 11 个端点是被冻结的（Legacy Contract：请求/响应语义不得改动）。
文档输入是**新能力**，所以：

    · 走**新路径** `/api/documents*`（与老表格上传 `/api/upload` 完全平行，互不影响）；
    · 代码放**新文件**，api.py 只为它加两行（import + include_router）；
    · 出错的响应体形状**与老端点完全一致**（同一个 app 级异常处理器产出）——
      所以它的 `ApiError` 不是从 api.py import 的，而是本地定义一个**带 code 属性**的同形类，
      靠 api.py 的处理器里的 `getattr(exc, "code", None)` 鸭子类型接住（避免循环 import）。

老端点会不会被影响：不会。`/api/upload` 只认 .xlsx/.xlsm/.csv；本模块只认 .docx/.pdf ——
两边各管一种输入，谁也没改谁。这也正是"并存"的含义：**Excel 那条路一行没动**。

════════════════════════════════════════════════════════════════════════
【端点清单（全部是新增路径）】
════════════════════════════════════════════════════════════════════════
    POST /api/documents                     上传 .docx/.pdf → 提取文本（chars > 0 才算成功）
    GET  /api/documents                     文档列表（分页）
    GET  /api/documents/{doc_id}            单个文档（include_text=true 时带全文）
    GET  /api/documents/{doc_id}/text       全文（text/plain，可直接在浏览器里看）
    POST /api/documents/{doc_id}/summary    结构化摘要 + 关键词 + 关键事实（规则抽取，非 LLM）

错误码（`error.code`，前端按它分支）：
    document_unsupported  400  后缀不是 .docx/.pdf
    document_broken       422  文件打不开/损坏
    document_no_text      422  **能打开但提不出文字**（扫描件、空文档）→ 不算成功
    document_not_found    404  doc_id 不存在
    document_file_gone    410  记录在但原文文件没了（取全文时发生）
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse

from app import state
from app.engine import docs
from app.engine.docs import DOC_SUFFIXES

router = APIRouter(prefix="/api/documents", tags=["documents"])

# 与 api.py 同一套分片大小（1MB）：文档同样流式落盘、边写边算哈希
_CHUNK_SIZE = 1024 * 1024

# 列表/详情里给的正文预览长度（全文走 /text 或 include_text=true；不把整篇塞进列表响应）
TEXT_PREVIEW_CHARS = 400

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DocumentApiError(HTTPException):
    """带机器可读 `code` 的 HTTPException（与 api.py 的 ApiError 同形，但不 import 它）。

    为什么会话处理器认得它：api.py 的 `_http_error_handler` 用 `getattr(exc, "code", None)`
    取值，所以只要本类带 `code`/`message` 属性，响应体就是同一个统一形状。
    """

    def __init__(self, status_code: int, code: str, message: str, detail: Any = None) -> None:
        super().__init__(status_code=status_code, detail=message if detail is None else detail)
        self.code = code
        self.message = message


# 端点文档里声明的错误响应（沿用 R005 #5 的"失败长什么样要写在 OpenAPI 里"）
_DOC_RESPONSES: dict[int | str, dict] = {
    400: {"description": "后缀不支持（document_unsupported）"},
    404: {"description": "doc_id 不存在（document_not_found）"},
    410: {"description": "记录在但原文文件已不在（document_file_gone）"},
    422: {"description": "文件损坏或提不出文本（document_broken / document_no_text）"},
}


def _safe_filename(name: str) -> str:
    """文件名消毒：去掉目录部分（防 ../../ 路径穿越）+ 去掉非法字符。

    与 api.py 的 `_safe_filename` 语义相同。**刻意重复这 8 行**：api.py 是冻结文件，
    为了一个辅助函数去动它（挪位置/加公共模块）得不偿失 —— 等哪天 api.py 因为别的原因要改，
    再把两者一起提到公共模块里（已记进 TASK-003 报告的遗留项）。
    """
    base = Path(name.strip().replace("\\", "/")).name
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).strip(" .")
    return cleaned or "document"


def _relative_to_root(path: str | Path) -> str:
    """对外只给相对项目根的路径；指到项目外（测试临时目录）时退化成文件名。

    与 api.py 的 `_relative_output_path` 同一原则（R005 #7：不漏服务器绝对路径）。
    """
    resolved = Path(path)
    try:
        return resolved.resolve().relative_to(_PROJECT_ROOT).as_posix()
    except (ValueError, OSError):
        return resolved.name


def _require_record(doc_id: str) -> dict:
    record = state.get_document(doc_id)
    if record is None:
        raise DocumentApiError(404, "document_not_found", f"没有这个文档记录：{doc_id}")
    return record


def _read_text(record: dict) -> str:
    """按记录里的 stored_path 重新提取全文（原文文件不在 → 410，不假装还能读）。"""
    stored = Path(record.get("stored_path") or "")
    if not stored.is_file():
        raise DocumentApiError(
            410, "document_file_gone",
            f"文档原文已不在：{stored}（记录还在，但重新提取文本需要原文文件）",
        )
    try:
        return docs.extract(stored, record.get("suffix"))["text"]
    except docs.DocumentError as exc:
        raise DocumentApiError(422, exc.code, exc.message) from exc


def _document_response(record: dict, *, text: str | None = None, preview: str | None = None) -> dict:
    """把落盘记录整理成响应体（不含绝对路径、不含全文——除非调用方明确要）。"""
    body = {
        "doc_id": record["doc_id"],
        "filename": record["filename"],
        "suffix": record["suffix"],
        "size_bytes": record["size_bytes"],
        "sha256": record["sha256"],
        "chars": record["chars"],
        "blocks": record["blocks"],
        "block_unit": record["block_unit"],
        "title": record.get("title", ""),
        "outline": record.get("outline", []),
        "warnings": record.get("warnings", []),
        "extra": record.get("extra", {}),
        "stored_path": _relative_to_root(record["stored_path"]),
        "summary": record.get("summary"),
        "created_at": record["created_at"],
    }
    if preview is not None:
        body["text_preview"] = preview
    if text is not None:
        body["text"] = text
    return body


# ── 1. 上传 + 提取 ──────────────────────────────────────────────────────
@router.post("", status_code=201, responses=_DOC_RESPONSES, summary="上传 Word/PDF 并提取文本")
def upload_document(
    file: UploadFile = File(..., description="要提取的文档（.docx / .pdf）"),
) -> dict:
    """流式落盘到 `data/documents/` → **真的提取文本** → 登记记录。

    成功的判据是**文本长度 > 0**（指令原文）：提不出文字的文件（扫描件、空文档）返回 422
    `document_no_text`，并且**不留在盘上**（与 /api/upload 解析失败的处理一致，D10 的精神）。
    """
    original_name = (file.filename or "").strip()
    safe_name = _safe_filename(original_name)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in DOC_SUFFIXES:
        raise DocumentApiError(
            400, "document_unsupported",
            f"不支持的文档类型：{safe_name!r}（后缀 {suffix or '(无)'}）。"
            f"本端点只接受 {list(DOC_SUFFIXES)}；表格文件请走 POST /api/upload。",
        )

    doc_id = state.new_id("d")
    target = state.doc_dir() / f"{doc_id}_{safe_name}"
    digest = hashlib.sha256()
    size_bytes = 0
    with open(target, "wb") as handle:
        while chunk := file.file.read(_CHUNK_SIZE):
            digest.update(chunk)
            size_bytes += len(chunk)
            handle.write(chunk)

    try:
        extracted = docs.extract(target, suffix)
    except docs.DocumentError as exc:
        target.unlink(missing_ok=True)             # 提取不了的文件不留盘
        status = 400 if exc.code == "document_unsupported" else 422
        raise DocumentApiError(status, exc.code, exc.message) from exc

    record = state.record_document(
        doc_id=doc_id,
        filename=original_name or safe_name,
        stored_path=str(target),
        suffix=suffix,
        size_bytes=size_bytes,
        sha256=digest.hexdigest(),
        chars=extracted["chars"],
        blocks=extracted["blocks"],
        block_unit=extracted["block_unit"],
        title=extracted.get("title", ""),
        outline=extracted.get("outline", []),
        warnings=extracted.get("warnings", []),
        extra=extracted.get("extra", {}),
    )
    return _document_response(record, preview=extracted["text"][:TEXT_PREVIEW_CHARS])


# ── 2. 列表 ─────────────────────────────────────────────────────────────
@router.get("", summary="文档列表（新的在前，分页）")
def list_documents(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    records, total = state.list_documents(limit, offset)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "documents": [_document_response(record) for record in records],
    }


# ── 3. 单个文档 ─────────────────────────────────────────────────────────
@router.get("/{doc_id}", responses=_DOC_RESPONSES, summary="单个文档（可带全文）")
def get_document(
    doc_id: str,
    include_text: bool = Query(
        default=False,
        description="true = 响应里带重新提取的全文（默认不带：列表/详情不塞整篇正文）",
    ),
) -> dict:
    record = _require_record(doc_id)
    if not include_text:
        return _document_response(record)
    text = _read_text(record)
    return _document_response(record, text=text, preview=text[:TEXT_PREVIEW_CHARS])


# ── 4. 全文（纯文本） ───────────────────────────────────────────────────
@router.get("/{doc_id}/text", response_class=PlainTextResponse, responses=_DOC_RESPONSES,
            summary="文档全文（text/plain，直接可读）")
def get_document_text(doc_id: str) -> PlainTextResponse:
    record = _require_record(doc_id)
    text = _read_text(record)
    return PlainTextResponse(
        content=text,
        headers={"X-Doc-Chars": str(record["chars"]), "X-Doc-Sha256": record["sha256"]},
    )


# ── 5. 结构化摘要 ───────────────────────────────────────────────────────
@router.post("/{doc_id}/summary", responses=_DOC_RESPONSES, summary="结构化摘要（规则抽取，非 LLM）")
def summarize_document(
    doc_id: str,
    force: bool = Query(default=False, description="true = 忽略已有摘要重新生成"),
    max_sentences: int = Query(default=docs.DEFAULT_MAX_SENTENCES, ge=1, le=20),
    max_keywords: int = Query(default=docs.DEFAULT_MAX_KEYWORDS, ge=1, le=50),
    max_facts: int = Query(default=docs.DEFAULT_MAX_FACTS, ge=1, le=100),
) -> dict:
    """从**重新提取的全文**做抽取式摘要 + 关键词 + 关键事实，并把结果写回文档记录。

    为什么是"重新提取"而不是把全文存在 JSON 里：状态文件是给人看的审计凭据，
    塞进整篇正文会让它越来越大；原文文件本身就在盘上，重提取是确定性的、代价很小。

    幂等：默认已生成过就直接返回记录里的那份（`generated_at` 不变）；`force=true` 才重算。
    """
    record = _require_record(doc_id)
    cached = record.get("summary")
    if cached and not force:
        return {"doc_id": doc_id, "cached": True, **cached}

    text = _read_text(record)
    summary = docs.summarize(
        text, max_sentences=max_sentences, max_keywords=max_keywords, max_facts=max_facts
    )
    summary["generated_at"] = state.now_iso()
    summary["input_sha256"] = record["sha256"]      # 摘要针对的是哪一版原文（可追溯）
    state.set_document_summary(doc_id, summary)
    return {"doc_id": doc_id, "cached": False, **summary}
