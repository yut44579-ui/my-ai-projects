"""unified_documents.py · 统一文档仓储层（FR-009-A1）。

════════════════════════════════════════════════════════════════════════
【为什么需要它：现在有两份"文档"，用户只看得到一个列表】
════════════════════════════════════════════════════════════════════════
本项目历史上长出了**两条文档管道**，各自把记录存在自己的地方：

    SQLite `data/app.db` 的 documents 表   ← FR-003 统一导入（Excel/CSV/MD/PPT/Word/PDF）
    `state/documents.json`                ← TASK-003「上传并提取」（Word/PDF）

结果就是用户实测报的那个缺陷：**从「导入资料」导进去的 PDF，在「文档资料」列表里永远看不到**
（那个列表读的是 `state/documents.json`）。两条管道各自都是对的，错在"没有一个地方
把两边的文档当成同一件事来回答"。

════════════════════════════════════════════════════════════════════════
【裁决：读时统一适配，**拒绝写时双写**】
════════════════════════════════════════════════════════════════════════
评审原话：「双写必然制造新的一致性问题」——

    SQLite 写成功 / JSON 写失败 → 这次导入到底算成功还是失败？后续删除、重命名、
    重新导入、恢复备份都会出现两份不一致，最终还得再加一套同步修复机制。

这与本项目"可审计、可复现、少基础设施"的方向相反。所以：

    · **写**：两条管道各写各的，一个字都不改（历史记录不动、新导入不动）；
    · **读**：由本层把两个来源适配成同一份视图 —— 前端永远不关心文档来自哪里。

两个来源的定位（写进代码，也写进文档）：

    SQLite documents                = FR-003 新导入文档的**事实来源**
    state/documents.json            = TASK-003 **历史文档的兼容来源**（只读，不再新增写入点）

★ 这层是过渡形态，不是终局：将来要把历史文档迁进 SQLite，是**一个独立的迁移 TASK**，
  不是在 FR-009-A 里顺手做（铁律：不许顺手重构）。

════════════════════════════════════════════════════════════════════════
【对外只暴露两个方法】
════════════════════════════════════════════════════════════════════════
    list_documents(limit, offset)   → (records, total, notes)
    get_document(document_id)       → record | None
    get_text(document_id)           → str（取不到就抛 DocumentTextUnavailable）

`notes` 是**降级说明**（哪个来源这次读不出来），不是错误：一个来源坏了，另一个照常显示
（评审验收 A-2：不许因为其中一个来源为空/损坏导致整个列表失败）。

════════════════════════════════════════════════════════════════════════
【统一后的字段（评审点名的七个 + 兼容旧响应的那些）】
════════════════════════════════════════════════════════════════════════
    document_id / source_type / filename / title / text / created_at / import_id

  · `source_type` 在这一层是**来源标识**，只有两个取值：
        "import"  = 来自 FR-003 新管道（SQLite）
        "legacy"  = 来自 TASK-003 历史管道（JSON）
    文件的物理格式（pdf/docx/…）另有一列 `file_type` —— **刻意不叫 source_type**：
    同名不同义是这类合并最容易埋的坑（SQLite 那边的 source_type 是 pdf/excel，不是来源）。
  · `text` 只在 `include_text=True` 时带上（整篇正文不该塞进列表响应）。
  · 其余字段（chars/blocks/block_unit/suffix/size_bytes/sha256/outline/warnings/extra/
    summary/stored_path）是**旧响应里已有的名字**，统一视图沿用它们，前端与既有测试都不用改。

════════════════════════════════════════════════════════════════════════
【★ 同一个物理文件不会出现两个逻辑文档】
════════════════════════════════════════════════════════════════════════
去重规则（只跨来源去重，**来源内部一律保留**）：

    若某份历史文档的 sha256 与**任意一份新导入文档**的 sha256 相同
        → 那是同一份物理文件走了两条管道，只显示新的那条（导入进来的那条）

为什么只在"跨来源"这一条线上合并，而不按 sha 全局去重：旧管道自己的语义是
**"同一个文件传两次 = 两条记录"**（TASK-003 的验收就钉着这一条：同 sha 两条记录，
两次上传的 doc_id 要分得清）。全局按 sha 去重会把旧管道的合法记录吃掉 ——
那不是"消重"，那是"改别人的语义"。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app import state
from app.importer import db, store

#: 来源标识（`source_type` 的取值，只有这两个）
SOURCE_IMPORT = "import"
SOURCE_LEGACY = "legacy"

#: 来源的中文说明（给响应里的 `source_label` 用；页面按它说话，不显示"sqlite/json"）
SOURCE_LABELS: dict[str, str] = {
    SOURCE_IMPORT: "导入资料",
    SOURCE_LEGACY: "历史文档",
}

#: 一次最多取多少份文档来合并（单机量级远到不了；到了就如实说明本页只显示最新的这些）
MAX_UNIFIED_DOCUMENTS = 5000

class DocumentTextUnavailable(RuntimeError):
    """取不到正文（不是"文档不存在"）。

    `code` 与 `message` 沿用 `app/engine/docs.py::DocumentError` 的形状，
    API 层据此回 410 / 422 —— 本模块不 import fastapi，也不决定 HTTP 状态码。
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ════════════════════════════════════════════════════════════════════════
# 两个来源各自的读取（每个都在 try 里，坏一个不影响另一个）
# ════════════════════════════════════════════════════════════════════════
def _import_record(record: dict[str, Any], source_file: dict[str, Any] | None = None) -> dict[str, Any]:
    """把 SQLite 的一条 documents 记录适配成统一形状（**唯一**一处定义）。

    列表与详情都走这里：同一个形状有两处定义，改一处忘一处，页面和接口迟早对不上。
    """
    meta = dict(record.get("meta") or {})
    size_bytes = (source_file or {}).get("size_bytes")
    file_type = str(record.get("source_type") or "")
    return {
        "document_id": record["document_id"],
        "source_type": SOURCE_IMPORT,
        "source_label": SOURCE_LABELS[SOURCE_IMPORT],
        "filename": record["filename"],
        "title": record.get("title") or "",
        "text": "",
        "text_available": True,                         # 正文就在库里，原文文件删了也读得到
        "char_count": int(record.get("char_count") or 0),
        "created_at": record.get("imported_at") or "",
        "import_id": record.get("import_id"),
        "file_type": file_type,
        "suffix": f".{file_type}" if file_type else "",
        "size_bytes": size_bytes,
        "sha256": (source_file or {}).get("sha256"),
        "block_count": int(record.get("block_count") or 0),
        "block_unit": meta.get("block_unit") or "",
        "outline": list(meta.get("outline") or []),
        "warnings": list(meta.get("warnings") or []),
        "extra": dict(meta.get("extra") or {}),
        "summary": meta.get("summary"),
        "stored_path": (source_file or {}).get("original_path") or "",
    }


def _legacy_record(record: dict[str, Any]) -> dict[str, Any]:
    """把 state/documents.json 的一条记录适配成统一形状（**唯一**一处定义）。"""
    suffix = str(record.get("suffix") or "")
    return {
        "document_id": record["doc_id"],
        "source_type": SOURCE_LEGACY,
        "source_label": SOURCE_LABELS[SOURCE_LEGACY],
        "filename": record.get("filename") or "",
        "title": record.get("title") or "",
        "text": "",
        "text_available": True,                         # 正文按 stored_path 重新提取
        "char_count": int(record.get("chars") or 0),
        "created_at": record.get("created_at") or "",
        "import_id": None,                              # 旧管道没有导入记录这个概念
        "file_type": suffix.lstrip("."),
        "suffix": suffix,
        "size_bytes": record.get("size_bytes"),
        "sha256": record.get("sha256"),
        "block_count": int(record.get("blocks") or 0),
        "block_unit": record.get("block_unit") or "",
        "outline": list(record.get("outline") or []),
        "warnings": list(record.get("warnings") or []),
        "extra": dict(record.get("extra") or {}),
        "summary": record.get("summary"),
        "stored_path": record.get("stored_path") or "",
    }


def _import_records(limit: int, notes: list[str]) -> list[dict[str, Any]]:
    """FR-003 来源：SQLite documents（**不含正文**，正文按需再取）。"""
    try:
        with db.readonly() as connection:
            records, _total = store.list_document_meta(connection, limit)
            files = {
                record.get("source_file_id"): store.get_source_file(
                    connection, str(record.get("source_file_id"))
                )
                for record in records
                if record.get("source_file_id")
            }
    except Exception:                                   # noqa: BLE001 —— 降级，不炸整页
        notes.append("新导入的文档这次读不出来（本机存储暂时不可用），下面显示的是历史文档。")
        return []
    return [_import_record(record, files.get(record.get("source_file_id"))) for record in records]


def _legacy_records(limit: int, notes: list[str]) -> list[dict[str, Any]]:
    """TASK-003 来源：state/documents.json（历史文档，只读）。"""
    try:
        records, _total = state.list_documents(limit, 0)
    except Exception:                                   # noqa: BLE001 —— 同上
        notes.append("历史文档这次读不出来（状态文件暂时不可用），下面显示的是新导入的文档。")
        return []
    return [_legacy_record(record) for record in records]


# ════════════════════════════════════════════════════════════════════════
# 合并 / 去重 / 分页
# ════════════════════════════════════════════════════════════════════════
def _drop_cross_source_duplicates(records: list[dict[str, Any]]) -> tuple[list[dict], int]:
    """跨来源去重：历史文档若与新导入文档是同 sha，只留新导入的那条。

    见模块顶部"同一个物理文件不会出现两个逻辑文档"——**只在跨来源这一条线上合并**，
    来源内部的重复一律保留（那是各自管道的既有语义）。
    """
    imported_hashes = {
        record["sha256"] for record in records
        if record["source_type"] == SOURCE_IMPORT and record.get("sha256")
    }
    kept = [
        record for record in records
        if not (record["source_type"] == SOURCE_LEGACY and record.get("sha256") in imported_hashes)
    ]
    return kept, len(records) - len(kept)


def _sort_key(record: dict[str, Any]) -> tuple[str, str]:
    """排序键：时间倒序（新的在前），同一时刻按编号倒序 —— 确定性的，不依赖读盘顺序。"""
    return (str(record.get("created_at") or ""), str(record.get("document_id") or ""))


def list_documents(
    limit: int = 50, offset: int = 0, *, include_text: bool = False
) -> tuple[list[dict[str, Any]], int, list[str]]:
    """统一文档列表：返回 `(当页记录, 总数, 降级说明)`。

    分页在**合并之后**做（先各自取全量 → 合并 → 去重 → 排序 → 切片），
    否则"第 2 页"会出现同一个来源被切两半、合并后顺序错乱的经典 bug。
    """
    notes: list[str] = []
    records = _import_records(MAX_UNIFIED_DOCUMENTS, notes)
    records += _legacy_records(MAX_UNIFIED_DOCUMENTS, notes)
    records, dropped = _drop_cross_source_duplicates(records)
    records.sort(key=_sort_key, reverse=True)

    if dropped:
        notes.append(f"有 {dropped} 份历史文档与新导入的是同一个文件，已按一份显示。")
    if len(records) > MAX_UNIFIED_DOCUMENTS:
        notes.append(f"文档较多，本页只列出最新的 {MAX_UNIFIED_DOCUMENTS} 份。")
        records = records[:MAX_UNIFIED_DOCUMENTS]

    total = len(records)
    page = records[offset:offset + limit]
    if include_text:
        for record in page:
            record["text"] = get_text(record["document_id"]) or ""
    return page, total, notes


def get_document(document_id: str, *, include_text: bool = False) -> dict[str, Any] | None:
    """按编号取一份文档（两个来源都找；找不到返回 None，由调用方回 404）。

    编号本身带来源指纹（`doc_…` = FR-003 / `d_…` = TASK-003），但**先按编号查库、
    再按编号查 JSON**，不靠前缀猜 —— 前缀是生成规则，不是契约。
    """
    record = _find_import_record(document_id) or _find_legacy_record(document_id)
    if record is None:
        return None
    if include_text:
        record["text"] = get_text(document_id)
    return record


def _find_import_record(document_id: str) -> dict[str, Any] | None:
    """在 SQLite 里按编号找一份文档（含正文行，详情要给全文时用得上）。"""
    try:
        with db.readonly() as connection:
            found = store.get_document(connection, document_id)
            if found is None:
                return None
            source_file = store.get_source_file(connection, str(found.get("source_file_id"))) \
                if found.get("source_file_id") else {}
    except Exception:                                   # noqa: BLE001 —— 读不出来 = 当作没有
        return None
    return _import_record(found, source_file)


def _find_legacy_record(document_id: str) -> dict[str, Any] | None:
    """在 state/documents.json 里按编号找一份文档。"""
    try:
        found = state.get_document(document_id)
    except Exception:                                   # noqa: BLE001
        return None
    return _legacy_record(found) if found else None


# ════════════════════════════════════════════════════════════════════════
# 正文
# ════════════════════════════════════════════════════════════════════════
def get_text(document_id: str) -> str:
    """取全文。两个来源的取法**故意不同**，因为它们的事实来源不同：

        FR-003（SQLite）    正文就在库里 —— 原文文件删了照样读得到
        TASK-003（JSON）    库里只存元信息，正文得按 stored_path **重新提取**
                            （原文没了就 410，绝不假装还能读 —— 这是 TASK-003 的既有约定）

    找不到这份文档 → `DocumentTextUnavailable("document_not_found")`。
    """
    from app.engine import docs                          # 只读复用，engine 一行没改

    try:
        with db.readonly() as connection:
            found = store.get_document(connection, document_id)
    except Exception:                                   # noqa: BLE001 —— 库读不出来就去看旧管道
        found = None

    if found is not None:
        return str(found.get("text") or "")

    record = _find_legacy_record(document_id)
    if record is None:
        raise DocumentTextUnavailable("document_not_found", f"没有这个文档记录：{document_id}")

    stored = record.get("stored_path") or ""
    if not Path(stored).is_file():
        raise DocumentTextUnavailable(
            "document_file_gone",
            f"文档原文已不在：{stored}（记录还在，但重新提取文本需要原文文件）",
        )
    try:
        return str(docs.extract(stored, record.get("suffix"))["text"])
    except docs.DocumentError as exc:
        raise DocumentTextUnavailable(exc.code, exc.message) from exc


__all__ = [
    "MAX_UNIFIED_DOCUMENTS",
    "SOURCE_IMPORT",
    "SOURCE_LABELS",
    "SOURCE_LEGACY",
    "DocumentTextUnavailable",
    "get_document",
    "get_text",
    "list_documents",
]
