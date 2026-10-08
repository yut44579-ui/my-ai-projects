"""api_imports.py · FR-003E：**统一导入入口 + 已物化数据源 + 按地区查询**的 HTTP 端点。

════════════════════════════════════════════════════════════════════════
【端点清单（全部是新增路径，与既有端点零交集）】
════════════════════════════════════════════════════════════════════════
    POST /api/imports/preview                     导入前预览（多文件，**不落库**）
    POST /api/imports                             统一导入（支持多文件；Excel/CSV/MD/PPTX/Word/PDF）
    GET  /api/imports                             导入记录（来源链路，新的在前）
    GET  /api/imports/documents/{document_id}     导入进来的文档正文
    GET  /api/imports/{import_id}                 单条导入记录（含它落了哪些 dataset/document）

    GET  /api/sources                             已物化数据源列表（与"按地区"能力一起给）
    GET  /api/sources/{dataset_id}                单个已物化数据源（含数据字典）
    GET  /api/sources/{dataset_id}/region         地区维度是否可用（前端据此决定显不显示「按地区」）
    GET  /api/sources/{dataset_id}/region/query   **按地区查销售额**（确定性计算）

【为什么是独立文件 + api.py 只加两行】
与 TASK-003/STEP A 同一条理由：api.py 里那批端点是冻结契约。新能力走新文件、新路径。

【★ 为什么 `/api/sources` 与既有 `/api/datasets` 是两份列表（不是重复实现）】
    `/api/datasets`  = **已登记**的数据源：记着文件信息，但数据还在原文件里（引用式）
    `/api/sources`   = **已物化**的数据源：行已经在 SQLite 里，能直接参与确定性计算（FR-003B）
两者语义不同、状态不同（"分析未开通" vs "可分析"），合成一份会让用户以为"导进来就能分析"。
将来真要合并，是**一个独立的重构 TASK**，不在 FR-003 里顺手做（铁律：不许顺手重构）。

【数字只有一个来源】
`/api/sources/{id}/region/query` 的数字全部来自 `app/importer/region_query.py` 的 pandas 计算
（复用 `app/engine/metrics.py` 的 D16 口径），**LLM 全程不参与**，SQLite 也不做任何聚合。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

from app import state
from app.importer import db, frames, models, pipeline, region_query, store

router = APIRouter(prefix="/api", tags=["imports"])

#: 一次请求最多带几个文件（导入前预览也一样）—— 多文件是便利，不是无限并行上传
MAX_FILES_PER_REQUEST = 20

#: 格式名（models 里的 FMT_* 取值）→ 中文标签（"pdf" → "PDF 文档"）。
#: 与 `models.SOURCE_TYPES[*]["label"]` 是同一批话术，**从那里派生**而不是另抄一份
#: （抄一份就有两个地方要改，迟早只改一处）。
_SOURCE_TYPE_LABELS: dict[str, str] = {
    str(spec["source_type"]): str(spec["label"]) for spec in models.SOURCE_TYPES.values()
}

#: 机器可读错误码 → HTTP 状态码（没列出的走 400）
_STATUS_BY_CODE: dict[str, int] = {
    "dataset_not_found": 404,
    "document_not_found": 404,
    "import_not_found": 404,
    "unsupported_type": 415,
    "file_too_large": 413,
    "too_many_rows": 413,
    "markdown_too_large": 413,
    "too_many_slides": 413,
    "too_many_tables": 413,
    "db_capacity_exceeded": 507,
    "zip_bomb": 422,
    "zip_unsafe": 422,
    "encrypted_archive": 422,
    "signature_mismatch": 422,
    "parse_failed": 422,
    "no_content": 422,
    "encoding_unknown": 422,
    # 维度不可用 = "这个请求当前数据源满足不了"（不是参数错、也不是没找到），用 409
    models.DIMENSION_UNAVAILABLE: 409,
    "DIMENSION_AMBIGUOUS": 409,
    "measure_unavailable": 409,
    "window_unavailable": 409,
    "dimension_missing": 409,
    "dimension_unknown": 400,
    "region_column_missing": 409,
    "db_write_failed": 500,
}


class ImportApiError(HTTPException):
    """带机器可读 `code` 的 HTTPException（与 api.py / api_datasets 同形，但不 import 它们）。

    api.py 的统一异常处理器靠 `getattr(exc, "code", None)` 鸭子类型接住 → 同一个错误体形状。
    """

    def __init__(self, status_code: int, code: str, message: str, extra: dict | None = None) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.message = message
        self.extra = dict(extra or {})


def _json_response(exc: ImportApiError) -> JSONResponse:
    """把导入层错误渲染成响应。

    形状**同时**满足两边的契约（纯增量，老的解析方式不受影响）：

        {"error": {"code": …, "message": …, "detail": …}, "detail": …}   ← 项目冻结的统一形状
        {"code": …, "dimension": …, "message": …}                        ← 评审 #5 点名的维度错误

    `DIMENSION_UNAVAILABLE` 那两个字段（code / dimension / message）就摆在顶层 ——
    评审明确要求"直接调用也要拿到这个结构化错误"，所以它们必须在最外层一眼可见。
    """
    body: dict[str, Any] = {
        "error": {"code": exc.code, "message": exc.message, "detail": exc.detail},
        "detail": exc.detail,
        "code": exc.code,
        "message": exc.message,
    }
    body.update(exc.extra)
    return JSONResponse(status_code=exc.status_code, content=body)


def _fail(status_code: int, code: str, message: str, **extra: Any) -> JSONResponse:
    """**返回**（不是 raise）一个错误响应。

    为什么端点里直接 return 而不是抛异常：`DIMENSION_UNAVAILABLE` 要求把 `dimension`
    摆在响应体顶层，而 api.py 的统一异常处理器只产出 `{"error":{code,message,detail}}`。
    改成"显式返回"就不必去动那个被所有端点共用的处理器（它属于冻结的错误契约）。
    """
    return _json_response(ImportApiError(status_code, code, message, extra or None))


def _translate(exc: Exception) -> ImportApiError:
    """把导入层 / 数据访问层的可预期错误翻成 HTTP（业务话术原样带出去）。"""
    if isinstance(exc, ImportApiError):
        return exc
    if isinstance(exc, models.ImporterError):
        return ImportApiError(
            _STATUS_BY_CODE.get(exc.code, 400), exc.code, exc.message, exc.extra
        )
    if isinstance(exc, KeyError):                       # frames.py 找不到数据集/表
        return ImportApiError(404, "dataset_not_found", f"找不到这个数据源或它的表：{exc}")
    raise exc


def _read_uploads(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    """把上传的文件读进内存（带数量与**边读边卡大小**的上限）。

    为什么不用 `item.file.read()` 一把梭：它会把一个 2GB 的文件整个吃进内存才轮到
    "大小超限"的检查 —— 那就把"限制"做成了摆设（评审 §8⑧ 要的正是防这种上传）。
    这里分片读，**超限立刻停**，最多多占一个分片的量。
    """
    if not files:
        raise ImportApiError(400, "no_file", "没有收到文件 —— 请选择要导入的文件。")
    if len(files) > MAX_FILES_PER_REQUEST:
        raise ImportApiError(
            400, "too_many_files",
            f"一次最多导入 {MAX_FILES_PER_REQUEST} 个文件，收到 {len(files)} 个。",
        )
    payloads: list[tuple[str, bytes]] = []
    for item in files:
        name = (item.filename or "").strip()
        if not name:
            raise ImportApiError(400, "no_filename", "有一个文件没有文件名，无法判断格式。")
        chunks: list[bytes] = []
        size = 0
        while chunk := item.file.read(1024 * 1024):
            size += len(chunk)
            if size > models.MAX_FILE_BYTES:
                raise ImportApiError(
                    413, "file_too_large",
                    f"「{name}」超过 {models.MAX_FILE_BYTES // 1048576}MB 上限 —— "
                    f"请先拆分或抽样后再导入。",
                )
            chunks.append(chunk)
        payloads.append((name, b"".join(chunks)))
    return payloads


# ════════════════════════════════════════════════════════════════════════
# ① 导入前预览（不落库）
# ════════════════════════════════════════════════════════════════════════
@router.post("/imports/preview", summary="导入前预览：格式 / 字段 / 落成什么 / 地区字段候选")
def preview_imports(files: list[UploadFile] = File(..., description="要预览的文件（可多选）")) -> dict[str, Any]:
    """**只看不导入**：识别到的格式、字段与类型、会落成文档还是数据集、地区字段候选。

    刻意不写库：用户还没确认，系统就不该产生"一份数据源"这种事实。
    """
    results: list[dict[str, Any]] = []
    for name, data in _read_uploads(files):
        try:
            results.append({"filename": name, "ok": True, **_preview_bytes(name, data)})
        except Exception as exc:                        # noqa: BLE001 —— 单个文件失败不影响其它
            error = _translate(exc)
            results.append({
                "filename": name, "ok": False,
                "error_code": error.code, "message": error.message,
            })
    return {
        "count": len(results),
        "ok_count": sum(1 for item in results if item["ok"]),
        "results": results,
    }


def _preview_bytes(name: str, data: bytes) -> dict[str, Any]:
    """把一份字节预览出来（落临时文件 → 走同一条解析管道 → 删掉临时文件）。

    为什么不在内存里预览：解析器读的是真实路径（pandas / python-pptx / ZIP 安全检查都是）。
    预览**不写库**，但会短暂落一个临时文件 —— 用完立刻删，不留痕。
    """
    staging = state.upload_dir() / f"{state.new_id('pv')}_{pipeline.safe_stem(name)}"
    staging.write_bytes(data)
    try:
        return pipeline.preview_file(staging, filename=name)
    finally:
        staging.unlink(missing_ok=True)


# ════════════════════════════════════════════════════════════════════════
# ② 统一导入（多文件，逐个独立）
# ════════════════════════════════════════════════════════════════════════
@router.post("/imports", status_code=201, summary="统一导入：Excel / CSV / Markdown / PPT / Word / PDF")
def import_files(
    files: list[UploadFile] = File(..., description="要导入的文件（可多选）"),
    name: str | None = Query(default=None, description="数据源名字（只对单文件有意义）"),
) -> dict[str, Any]:
    """把文件真的导入系统（解析 → 物化入库 → 登记来源链路）。

    **每个文件独立成败**：一个失败不影响其它（`results` 里逐条给状态）。
    HTTP 状态码只表示"这次请求处理完了"，真正的成败看每一条的 `ok` ——
    这样前端能把"3 个成功、1 个失败"如实显示出来，而不是整批一起报错或一起报成功。
    """
    results: list[dict[str, Any]] = []
    for filename, data in _read_uploads(files):
        try:
            receipt = pipeline.import_bytes(data, filename, name=name if len(files) == 1 else None)
            results.append({"filename": filename, "ok": True, **receipt})
        except Exception as exc:                        # noqa: BLE001
            error = _translate(exc)
            results.append({
                "filename": filename, "ok": False,
                "error_code": error.code, "message": error.message,
                "rejected": error.code in {
                    "unsupported_type", "signature_mismatch", "zip_unsafe", "zip_bomb",
                    "encrypted_archive",
                },
            })
    return {
        "count": len(results),
        "ok_count": sum(1 for item in results if item["ok"]),
        "failed_count": sum(1 for item in results if not item["ok"]),
        "results": results,
    }


# ════════════════════════════════════════════════════════════════════════
# ③ 导入记录 / 文档
# ════════════════════════════════════════════════════════════════════════
@router.get("/imports", summary="导入记录（来源链路，新的在前）")
def list_imports(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """导入记录列表 —— **失败的导入也在里面**（status=failed + 可读原因，见 A-4）。

    每一条都带"落成了什么"：文档字数与数据集行数（页面那两列不需要再逐条去取详情）。
    """
    with db.readonly() as connection:
        records, total = store.list_imports(connection, limit, offset)
        views = _import_views(connection, records)
    return {"total": total, "limit": limit, "offset": offset, "imports": views}


@router.get("/imports/documents/{document_id}", summary="导入进来的文档正文")
def get_imported_document(
    document_id: str,
    include_text: bool = Query(default=False, description="true = 响应里带全文"),
) -> Any:
    """★ 这个路由必须**声明在** `/imports/{import_id}` 之前 —— FastAPI 按声明顺序匹配。"""
    with db.readonly() as connection:
        record = store.get_document(connection, document_id)
    if record is None:
        return _fail(404, "document_not_found", f"没有这个文档：{document_id}")
    body = {
        "document_id": record["document_id"],
        "source_file_id": record.get("source_file_id"),
        "filename": record["filename"],
        "source_type": record["source_type"],
        "title": record.get("title") or "",
        "char_count": record.get("char_count"),
        "block_count": record.get("block_count"),
        "imported_at": record.get("imported_at"),
        "parser_version": record.get("parser_version"),
        "meta": record.get("meta") or {},
        "text_preview": (record.get("text") or "")[:400],
    }
    if include_text:
        body["text"] = record.get("text") or ""
    return body


@router.get("/imports/{import_id}", summary="单条导入记录（含它落了哪些数据集 / 文档）")
def get_import(import_id: str) -> Any:
    with db.readonly() as connection:
        record = store.get_import(connection, import_id)
        if record is None:
            return _fail(404, "import_not_found", f"没有这条导入记录：{import_id}")
        body = _import_views(connection, [record])[0]
        counts = store.successful_import_count(connection, str(record["source_file_id"]))
        body["source_file"] = _source_file_view(connection, str(record["source_file_id"]))
        body["dataset"] = (
            _dataset_view(connection, str(record["dataset_id"])) if record.get("dataset_id") else None
        )
        body["document_id"] = record.get("document_id")
        body["dataset_count"] = counts["dataset_count"]
        body["document_count"] = counts["document_count"]
    return body


def _import_views(connection: Any, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """一批导入记录的业务视图（FR-009-A2：一次把这页的"落成了什么"全查出来）。

    为什么是"一批"而不是每条自己查：列表一页最多 200 条，逐条查就是 200 次往返。
    页面要的"字数 / 行数"从哪一列取，只在这里定义一次。
    """
    char_counts = store.document_char_counts(
        connection, [str(item["document_id"]) for item in records if item.get("document_id")]
    )
    row_counts = store.dataset_row_counts(
        connection, [str(item["dataset_id"]) for item in records if item.get("dataset_id")]
    )
    return [
        _import_view(
            record,
            char_count=char_counts.get(str(record.get("document_id"))),
            row_count=row_counts.get(str(record.get("dataset_id"))),
        )
        for record in records
    ]


def _import_view(
    record: dict[str, Any], *, char_count: int | None = None, row_count: int | None = None
) -> dict[str, Any]:
    """一条导入记录的业务视图（**不含**任何绝对路径）。

    `char_count` / `row_count` 是"这次导入落成了多少内容"：
    文档资料看字数，数据集看行数 —— 页面那一列（"字数或行数"）直接用它们，不必再点进详情。
    """
    return {
        "import_id": record["import_id"],
        "source_file_id": record["source_file_id"],
        "filename": record["source_filename"],
        "source_type": record["source_type"],
        "source_type_label": _SOURCE_TYPE_LABELS.get(str(record["source_type"]), ""),
        "imported_at": record["imported_at"],
        "status": record["status"],
        "status_label": {
            "success": "已入库", "failed": "导入失败", "skipped": "已导入（未重复入库）",
        }.get(str(record["status"]), str(record["status"])),
        "error_code": record.get("error_code"),
        "dataset_id": record.get("dataset_id"),
        "document_id": record.get("document_id"),
        "document_char_count": char_count,
        "dataset_row_count": row_count,
        "parser_version": record["parser_version"],
        "revision": record.get("revision"),
        "note": record.get("note") or "",
    }


def _source_file_view(connection: Any, source_file_id: str) -> dict[str, Any]:
    """来源文件视图：这份文件一共被导成了什么（评审 #3 那句话的机器可读版本）。"""
    record = store.get_source_file(connection, source_file_id) or {}
    counts = store.successful_import_count(connection, source_file_id)
    imports = store.imports_of_source_file(connection, source_file_id)
    return {
        "source_file_id": source_file_id,
        "filename": record.get("filename"),
        "source_type": record.get("source_type"),
        "size_bytes": record.get("size_bytes"),
        "sha256": record.get("sha256"),
        "created_at": record.get("created_at"),
        "dataset_count": counts["dataset_count"],
        "document_count": counts["document_count"],
        "import_count": len(imports),
        "original_kept": bool(record.get("original_path")),
        "summary_text": pipeline.summary_text(
            str(record.get("filename") or ""), counts["document_count"],
            counts["dataset_count"], 0,
        ) if counts["dataset_count"] or counts["document_count"] else "尚未成功导入任何内容",
    }


# ════════════════════════════════════════════════════════════════════════
# ④ 已物化数据源 + 地区维度
# ════════════════════════════════════════════════════════════════════════
@router.get("/sources", summary="已物化数据源列表（含地区维度可用性）")
def list_sources(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    with db.readonly() as connection:
        records, total = store.list_datasets(connection, limit, offset)
        views = [_dataset_view(connection, str(record["dataset_id"])) for record in records]
    return {"total": total, "limit": limit, "offset": offset, "sources": views}


@router.get("/sources/{dataset_id}", summary="单个已物化数据源（含数据字典）")
def get_source(dataset_id: str) -> Any:
    with db.readonly() as connection:
        view = _dataset_view(connection, dataset_id)
    if view is None:
        return _fail(404, "dataset_not_found", f"没有这个数据源：{dataset_id}")
    return view


@router.get("/sources/{dataset_id}/region", summary="地区维度是否可用（前端据此决定显不显示「按地区」）")
def region_availability(dataset_id: str) -> Any:
    """★ 数据源**没有**地区字段时，这里返回 `available=false`。

    前端据此**不显示**「按地区」（评审 #5：禁止"假能力入口"）；
    但真正调用下面的 `/region/query` 时后端**照样**会拦（不能只靠前端隐藏）。
    """
    try:
        return region_query.region_view(dataset_id)
    except Exception as exc:                            # noqa: BLE001
        error = _translate(exc)
        return _fail(error.status_code, error.code, error.message, **error.extra)


@router.get("/sources/{dataset_id}/region/query", summary="按地区查销售额（确定性计算）")
def query_region(
    dataset_id: str,
    start: str | None = Query(default=None, description="YYYY-MM-DD（含首尾全天）"),
    end: str | None = Query(default=None, description="YYYY-MM-DD（含首尾全天）"),
    dimension: str | None = Query(default=None, description="按哪个地区字段：大区 / 省份 / 城市"),
    table_name: str | None = Query(default=None, description="多表数据源里指定哪张表"),
    top_n: int | None = Query(default=region_query.DEFAULT_TOP_N, ge=1, le=500),
) -> Any:
    """按地区拆销售额。数字全部由 `region_query.py` 用 pandas 算出（复用 D16 口径）。

    **没有地区字段时返回 409 + `{"code":"DIMENSION_UNAVAILABLE","dimension":"region",…}`**
    —— 绝不 fallback 到 Country（ASSERT 24/25/26）。
    """
    try:
        return region_query.sales_by_region(
            dataset_id, start=start, end=end, dimension=dimension,
            table_name=table_name, top_n=top_n,
        )
    except Exception as exc:                            # noqa: BLE001
        error = _translate(exc)
        return _fail(error.status_code, error.code, error.message, **error.extra)


def _dataset_view(connection: Any, dataset_id: str) -> dict[str, Any] | None:
    """已物化数据源的业务视图（**不含绝对路径**；审计字段进 `audit`）。"""
    record = store.get_dataset(connection, dataset_id)
    if record is None:
        return None
    meta = record.get("meta") or {}
    tables = store.list_dataset_tables(connection, dataset_id)
    columns = store.list_dataset_columns(connection, dataset_id)
    return {
        "dataset_id": record["dataset_id"],
        "name": record["name"],
        "source_file_id": record.get("source_file_id"),
        "source_type": record.get("source_type"),
        "source_filename": meta.get("source_filename"),
        "row_count": record.get("row_count"),
        "column_count": record.get("column_count"),
        "table_count": record.get("table_count"),
        "date_range": {"start": record.get("date_start"), "end": record.get("date_end")},
        "status": record.get("status"),
        "status_label": pipeline.STATUS_LABEL_MATERIALIZED,
        "materialized_at": record.get("materialized_at"),
        "region_dimensions": record.get("region_dimensions") or [],
        "has_region": bool(record.get("region_dimensions")),
        "other_dimensions": [
            item for item in (meta.get("detected_dimensions") or [])
            if item.get("category") != "region"
        ],
        "field_map": record.get("field_map") or {},
        "original_kept": bool(meta.get("original_path")),
        "tables": [
            {
                "table_name": table["table_name"],
                "display_name": table["display_name"],
                "row_count": table["row_count"],
                "column_count": table["column_count"],
            }
            for table in tables
        ],
        "columns": [
            {
                "table_name": column["table_name"],
                "name": column["name"],
                "type": column["inferred_type"],
                "non_null": column["non_null"],
                "null_count": column["null_count"],
                "distinct": column["distinct_count"],
                "samples": column["samples"][:3],
                "region_key": column.get("region_key"),
            }
            for column in columns
        ],
        "notes": meta.get("notes") or [],
        # ── 审计层（接口会给，前端不渲染）────────────────────────────────
        "audit": {
            "original_path": meta.get("original_path"),
            "original_sha256": meta.get("original_sha256"),
            "parser_version": record.get("parser_version"),
        },
    }


__all__ = ["router"]
