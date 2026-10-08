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
    GET  /api/documents                     文档列表（分页）★ 见下：**两个来源的统一视图**
    GET  /api/documents/{doc_id}            单个文档（include_text=true 时带全文）
    GET  /api/documents/{doc_id}/text       全文（text/plain，可直接在浏览器里看）
    POST /api/documents/{doc_id}/summary    结构化摘要 + 关键词 + 关键事实（规则抽取，非 LLM）

════════════════════════════════════════════════════════════════════════
【FR-009-A1：列表/详情/全文读的是**统一视图**，不是只有 JSON 那一份】
════════════════════════════════════════════════════════════════════════
用户实测的缺陷：从「导入资料」导进来的 PDF（FR-003 写 SQLite）在这个列表里**永远看不到** ——
因为这里原来只读 `state/documents.json`（TASK-003 的历史文档）。

现在这一层的读路径全部经 `app/repositories/unified_documents.py`（两个来源的定位与去重规则
写在那份文件顶部，不在这里重复）：本文件只做"转发 + 组装响应体"，**不自己堆 merge 逻辑**。
编号（doc_id）在两个来源里都查，调用方与前端都不需要知道某一条来自哪里。

【FR-009-A3：标题的可信化】
上传时提取到的标题先过 `app/document_title.py::repair_title`：
提出来是乱码（如 `'\\x00A\\x00I…'` 这种 UTF-16 被当单字节读的产物）就用**文件名（去扩展名）**兜底，
绝不让乱码串进库、再显示到用户面前。规则与理由都在那个模块里（新旧两条管道共用同一份实现）。

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
from app.document_title import repair_title
from app.engine import docs
from app.engine.docs import DOC_SUFFIXES
from app.repositories import unified_documents

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
    """取一份文档（**两个来源都找** —— FR-009-A1 的统一视图）。

    旧管道（TASK-003，state/documents.json）与新管道（FR-003，SQLite documents）
    在这里被当成同一件事：调用方与前端都不需要知道它来自哪里。
    """
    record = unified_documents.get_document(doc_id)
    if record is None:
        raise DocumentApiError(404, "document_not_found", f"没有这个文档记录：{doc_id}")
    return record


def _read_text(record: dict) -> str:
    """取全文。

    两个来源的取法由仓储层决定（新导入的正文在库里；历史文档按 stored_path 重新提取，
    原文文件不在 → 410，不假装还能读）。
    """
    try:
        return unified_documents.get_text(record["document_id"])
    except unified_documents.DocumentTextUnavailable as exc:
        status = 404 if exc.code == "document_not_found" else (
            410 if exc.code == "document_file_gone" else 422
        )
        raise DocumentApiError(status, exc.code, exc.message) from exc


def _document_response(record: dict, *, text: str | None = None, preview: str | None = None) -> dict:
    """把统一视图的记录整理成响应体（不含绝对路径、不含全文——除非调用方明确要）。

    字段分两组，**都在**：
      · 统一视图的字段（评审点名的 document_id / source_type / source_label / characters…）
        —— 新代码按这组写；
      · TASK-003 以来的老字段名（doc_id / chars / blocks / suffix…）
        —— 前端与既有测试按这组写，一个都没删（纯增量，老调用方不受影响）。
    """
    body = {
        # ── 统一视图（FR-009-A1）────────────────────────────────────────
        "document_id": record["document_id"],
        "source_type": record["source_type"],
        "source_label": record.get("source_label", ""),
        "char_count": record["char_count"],
        "import_id": record.get("import_id"),
        # ── 兼容既有响应（名字沿用 TASK-003）────────────────────────────
        "doc_id": record["document_id"],
        "filename": record["filename"],
        "suffix": record.get("suffix", ""),
        "size_bytes": record.get("size_bytes"),
        "sha256": record.get("sha256"),
        "chars": record["char_count"],
        "blocks": record["block_count"],
        "block_unit": record.get("block_unit", ""),
        "title": record.get("title", ""),
        "outline": record.get("outline", []),
        "warnings": record.get("warnings", []),
        "extra": record.get("extra", {}),
        "stored_path": _relative_to_root(record["stored_path"]) if record.get("stored_path") else "",
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

    state.record_document(
        doc_id=doc_id,
        filename=original_name or safe_name,
        stored_path=str(target),
        suffix=suffix,
        size_bytes=size_bytes,
        sha256=digest.hexdigest(),
        chars=extracted["chars"],
        blocks=extracted["blocks"],
        block_unit=extracted["block_unit"],
        # 标题一律先过一遍可信化（FR-009-A3）：提出来的是乱码就用文件名兜底，
        # 绝不让 '\x00A\x00I…' 这种串进库、再显示到用户面前（规则在 app/document_title.py）
        title=repair_title(extracted.get("title", ""), original_name or safe_name),
        outline=extracted.get("outline", []),
        warnings=extracted.get("warnings", []),
        extra=extracted.get("extra", {}),
    )
    # 回执走**统一视图**这一条路取（不是把刚落盘的那份原始记录直接拼出去）：
    # 这样上传接口与列表/详情接口的形状由同一处产出，不会出现"刚上传的少一个字段"。
    record = _require_record(doc_id)
    return _document_response(record, preview=extracted["text"][:TEXT_PREVIEW_CHARS])


# ── 2. 列表 ─────────────────────────────────────────────────────────────
@router.get("", summary="文档列表（两个来源统一视图，新的在前，分页）")
def list_documents(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """**统一视图**（FR-009-A1）：FR-003 新导入的文档 + TASK-003 的历史文档，一起返回。

    `source_notes` 是降级说明：某个来源这次读不出来时，另一个照常显示（列表不会整体失败）。
    """
    records, total, notes = unified_documents.list_documents(limit, offset)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "source_notes": notes,
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
        headers={"X-Doc-Chars": str(record["char_count"]), "X-Doc-Sha256": record.get("sha256") or ""},
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
    summary["input_sha256"] = record.get("sha256") or ""   # 摘要针对的是哪一版原文（可追溯）
    if record["source_type"] == unified_documents.SOURCE_LEGACY:
        # 历史文档（JSON 状态文件）仍按老规矩把摘要写回记录：刷新页面还在。
        state.set_document_summary(doc_id, summary)
    # ★ 新导入的文档（FR-003，正文在 SQLite）**本期不为它新增摘要存储**：
    #   返回这次算出来的那份（同样确定性、同样可回原文核对），但明说"没有落库"——
    #   宁可在响应里说实话，也不假装它被记住了（`cached=false` 就是这句话）。
    return {"doc_id": doc_id, "cached": False, "persisted": record["source_type"] == unified_documents.SOURCE_LEGACY, **summary}
