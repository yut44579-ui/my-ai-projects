"""api_datasets.py · 数据源 / 业务表 / 导入 / 导出的 HTTP 端点（STEP A）。

════════════════════════════════════════════════════════════════════════
【端点清单（全部是新增路径，与既有 12 个端点零交集）】
════════════════════════════════════════════════════════════════════════
    GET  /api/datasets                    数据源列表（业务字段；哈希等在 audit 里，前端不渲染）
    GET  /api/datasets/{dataset_id}       单个数据源（含字段映射与口径绑定）
    POST /api/datasets/inspect            导入向导第 1 步：上传 + 只读检查（类型/工作表/前 N 行/字段猜测）
    POST /api/datasets/import             导入向导最后一步：把上传的文件**登记成数据集**
    GET  /api/tables/{table}              业务表分页查询（customers/products/sales/raw）
    GET  /api/tables/{table}/export       导出 xlsx / csv（**与页面同一查询条件、同一取数函数**）

【为什么又是独立文件 + 只往 api.py 加两行】
同 TASK-003/004 的理由：api.py 里那 12 个端点是**冻结的 Legacy Contract**，响应形状一个字不许动。
新能力走新文件、新路径，api.py 只多一行 import + 一行 include_router（位置在 `mount("/")` 之前）。

【/api/upload 一个字没动】
数据集输入（Excel/CSV）与文档输入（Word/PDF）在业务语义上是两条管道：本模块的
`/api/datasets/inspect` 只收 `.xlsx/.xlsm/.csv`，文档类文件由既有文档管道负责 ——
**不把 /api/upload 升级成万能上传**（评审明确要求）。

【导入的诚实边界】
`POST /api/datasets/import` 会**真的**登记一个数据集（真哈希、真行数、真时间范围、真字段映射），
但它的 `analysis_enabled=false`、状态写「已登记 · 分析未开通」：分析引擎当前绑定在内置快照上，
按数据集取数属于后续 TASK。接口把这句话一并返回，前端照实显示 —— **不假装导入即可分析**。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response

from app import state
from app.datasets import export as export_module
from app.datasets import queries, registry

router = APIRouter(prefix="/api", tags=["datasets"])

# 数据集输入允许的后缀（与 registry.DATASET_SUFFIXES 同一处定义）
_ALLOWED_SUFFIXES = registry.DATASET_SUFFIXES
_CHUNK_SIZE = 1024 * 1024
_MAX_UPLOAD_BYTES = 80 * 1024 * 1024          # 80MB：够放常规销售表，也挡得住误传大文件


class DatasetApiError(HTTPException):
    """带机器可读 `code` 的 HTTPException（与 api.py 的 ApiError 同形，但不 import 它）。

    api.py 的统一错误处理器靠鸭子类型接住带 `code`/`message` 的异常 → 产出同一个错误体。
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.message = message


def _error_from(exc: Exception) -> DatasetApiError:
    """把数据集层的可预期错误翻成 HTTP（业务话术原样带出去，不翻译成"内部错误"）。"""
    if isinstance(exc, registry.DatasetError):
        status = {
            "dataset_not_found": 404,
            "upload_not_found": 404,
            "dataset_analysis_not_ready": 409,
            "dataset_unsupported_type": 400,
            "dataset_parse_failed": 400,
            "dataset_encoding_unknown": 400,
            "dataset_sheet_not_found": 400,
            "dataset_unknown_field": 400,
            "dataset_unknown_column": 400,
        }.get(exc.code, 400)
        return DatasetApiError(status, exc.code, exc.message)
    if isinstance(exc, queries.QueryError):
        status = {
            "unknown_table": 404,
            "export_too_large": 413,
        }.get(exc.code, 400)
        return DatasetApiError(status, exc.code, exc.message)
    raise exc


# ════════════════════════════════════════════════════════════════════════
# ① 数据源
# ════════════════════════════════════════════════════════════════════════
@router.get("/datasets", summary="数据源列表（业务字段）")
def list_datasets(limit: int = Query(default=50, ge=1, le=200),
                  offset: int = Query(default=0, ge=0)) -> dict[str, Any]:
    records, total = registry.list_datasets(limit=limit, offset=offset)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "datasets": [_business_view(record) for record in records],
    }


@router.get("/datasets/{dataset_id}", summary="单个数据源（含字段映射与口径绑定）")
def get_dataset(dataset_id: str) -> dict[str, Any]:
    try:
        record = registry.get_dataset(dataset_id)
    except Exception as exc:                            # noqa: BLE001 —— 领域错误 → 404
        raise _error_from(exc) from exc
    return _management_view(record)


def _business_view(record: dict[str, Any]) -> dict[str, Any]:
    """业务层视图：名称 / 行数 / 时间范围 / 更新时间 / 状态 / 口径名 —— **不含任何审计字段**。

    口径（"这个数据源的数字按什么规则算"）属于管理层要知道的事：数据源页与业务表下方
    都会显示它的**人话**说明；内部编号不上接口、不上界面。
    """
    return {
        "dataset_id": record["dataset_id"],
        "name": record["name"],
        "kind": record["kind"],
        "source_file": record["source_file"],
        "row_count": record["row_count"],
        "column_count": record["column_count"],
        "date_range": record["date_range"],
        "updated_at": record["updated_at"],
        "status": record["status"],
        "status_label": record["status_label"],
        "analysis_enabled": record["analysis_enabled"],
        "metric_definition": record.get("metric_definition") or {},
    }


def _management_view(record: dict[str, Any]) -> dict[str, Any]:
    """管理层视图：业务字段 + 口径绑定 + 字段映射 + 数据范围 + 导入状态。

    审计字段（哈希 / 文件编号 / 落盘路径）放在 `audit` 下单独一块：接口给得到，
    但普通业务页面不渲染它们（前端展示规范）。
    """
    body = _business_view(record)
    body.update({
        "analysis_note": record.get("analysis_note") or "",
        "metric_definition": record.get("metric_definition") or {},
        "field_map": record.get("field_map") or {},
        "required_fields": record.get("required_fields") or [],
        "mapping_complete": record.get("mapping_complete"),
        "missing_fields": record.get("missing_fields") or [],
        "imported_at": record.get("imported_at"),
        "sheet": record.get("sheet"),
        "read_note": record.get("read_note"),
        "columns": record.get("columns") or [],
        "audit": record.get("audit") or {},
    })
    return body


# ════════════════════════════════════════════════════════════════════════
# ② 导入向导：① 检查（只读）→ ② 登记（真数据集）
# ════════════════════════════════════════════════════════════════════════
@router.post("/datasets/inspect", status_code=201,
             summary="导入向导第 1 步：上传 + 只读检查（类型/工作表/前 N 行/字段猜测）")
def inspect_dataset(file: UploadFile = File(..., description="数据源文件（.xlsx/.xlsm/.csv）")) -> dict[str, Any]:
    """把文件落盘到上传目录（与其它上传共用**同一份存储**），然后**只看不导入**。

    为什么不直接写进 `/api/upload`：那条是既有报表链路的冻结端点。数据集输入是另一条业务管道，
    这里只共用底层文件存储，语义分开（评审明确要求）。
    """
    original = (file.filename or "").strip()
    suffix = Path(original).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise DatasetApiError(
            400, "dataset_unsupported_type",
            f"数据源只接受 {list(_ALLOWED_SUFFIXES)}，收到 {suffix or '(无后缀)'}。"
            f"Word / PDF / PPT 属于文档资料，请在「文档资料」里上传。",
        )

    file_id = state.new_id("f")
    stored = state.upload_dir() / f"{file_id}_{_safe_name(original)}"
    digest = hashlib.sha256()
    size = 0
    with open(stored, "wb") as handle:
        while chunk := file.file.read(_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
            if size > _MAX_UPLOAD_BYTES:
                handle.close()
                stored.unlink(missing_ok=True)
                raise DatasetApiError(
                    413, "upload_too_large",
                    f"文件超过 {_MAX_UPLOAD_BYTES // (1024 * 1024)}MB 上限 —— 请先拆分或抽样后再导入。",
                )
            handle.write(chunk)

    try:
        record = _register_upload(
            file_id=file_id, filename=original or stored.name, stored=stored,
            size=size, digest=digest.hexdigest(),
        )
        result = registry.inspect_upload(record["file_id"])
    except Exception as exc:                        # noqa: BLE001 —— 统一翻成人话错误
        stored.unlink(missing_ok=True)
        raise _error_from(exc) from exc
    result["upload_id"] = record["file_id"]
    return result


@router.post("/datasets/import", status_code=201, summary="导入向导最后一步：登记成数据集")
def import_dataset(payload: dict[str, Any]) -> dict[str, Any]:
    """把检查过的上传文件**登记成数据集**（真哈希 / 真行数 / 真时间范围 / 真字段映射）。

    请求体（JSON）：
        {"upload_id": "f_xxx", "name": "2026 年销售数据", "sheet": "Sheet1",
         "field_map": {"InvoiceNo": "订单号", ...}}
    字段映射可以只给一部分 —— 缺哪个必需字段会在返回里点名列出来（`missing_fields`），
    但**不会因此假装导入成功**：数据集照样登记（它是一份真实的数据源），
    只是状态写「已登记 · 分析未开通」。
    """
    upload_id = str(payload.get("upload_id") or "").strip()
    if not upload_id:
        raise DatasetApiError(400, "upload_id_required", "缺少 upload_id（请先调用导入检查那一步）")
    sheet = payload.get("sheet")
    field_map = payload.get("field_map") or {}
    if not isinstance(field_map, dict):
        raise DatasetApiError(400, "invalid_field_map", "field_map 必须是对象（目标字段 → 文件列名）")
    name = payload.get("name")
    try:
        record = registry.register_import(
            upload_id,
            name=str(name) if name else None,
            sheet=str(sheet) if sheet else None,
            field_map={str(key): str(value) for key, value in field_map.items()},
        )
    except Exception as exc:                        # noqa: BLE001
        raise _error_from(exc) from exc
    body = _management_view(record)
    body["imported"] = True
    body["message"] = (
        f"「{record['name']}」已登记为数据源（{record['row_count']:,} 行 × "
        f"{record['column_count']} 列）。{record.get('analysis_note') or ''}"
    )
    return body


def _safe_name(name: str) -> str:
    """只留文件名本身（去掉任何路径成分），并挡掉空名。"""
    cleaned = Path(str(name or "upload.xlsx")).name.strip() or "upload.xlsx"
    return "".join(char for char in cleaned if char not in '\\/:*?"<>|')


def _register_upload(*, file_id: str, filename: str, stored: Path,
                     size: int, digest: str) -> dict[str, Any]:
    """把"落盘 + 解析成功"这件事记进上传仓库（与 /api/upload 同一套记录形状）。

    解析失败会抛错（由调用方删掉半截文件）：解析不了的文件不进 UploadRepository ——
    与既有上传同一条约定（D10：失败不留残件、不产出）。
    """
    from app.api import _read_table                     # 复用既有读取实现（同一条代码路径）

    frame = _read_table(stored, stored.suffix.lower())
    if frame.shape[0] == 0 or frame.shape[1] == 0:
        raise registry.DatasetError(
            "dataset_parse_failed",
            f"文件里没有可用数据：解析出 {frame.shape[0]} 行 × {frame.shape[1]} 列",
        )
    return state.record_upload(
        file_id=file_id,
        filename=filename,
        stored_path=str(stored),
        size_bytes=size,
        sha256=digest,
        rows=int(frame.shape[0]),
        column_names=[str(column) for column in frame.columns],
    )


# ════════════════════════════════════════════════════════════════════════
# ③ 业务表查询（分页 / 排序 / 筛选全部在后端）
# ════════════════════════════════════════════════════════════════════════
def _query_params(
    start: str | None, end: str | None, search: str | None,
    sort: str | None, order: str | None, dimension: str | None, metric: str | None,
    dataset_id: str | None,
) -> dict[str, Any]:
    return {
        "dataset_id": dataset_id, "start": start, "end": end, "search": search,
        "sort": sort, "order": order, "dimension": dimension, "metric": metric,
    }


@router.get("/tables/{table}", summary="业务表分页查询（后端排序 / 分页 / 筛选）")
def read_table(
    table: str,
    dataset_id: str | None = Query(default=None, description="默认内置销售数据源"),
    start: str | None = Query(default=None, description="YYYY-MM-DD"),
    end: str | None = Query(default=None, description="YYYY-MM-DD"),
    search: str | None = Query(default=None, description="按业务标识搜索（客户号 / 商品 / 期间）"),
    sort: str | None = Query(default=None, description="排序键（各表可选键见响应 columns）"),
    order: str | None = Query(default=None, description="asc / desc"),
    dimension: str | None = Query(default=None, description="销售表维度：day / week / country"),
    metric: str | None = Query(default=None,
                               description="销售表指标：sales_amount / order_count / customer_count / avg_order_amount"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=queries.DEFAULT_PAGE_SIZE, ge=1, le=queries.MAX_PAGE_SIZE),
) -> dict[str, Any]:
    try:
        return queries.fetch_table(
            table,
            page=page,
            page_size=page_size,
            **_query_params(start, end, search, sort, order, dimension, metric, dataset_id),
        )
    except Exception as exc:                            # noqa: BLE001
        raise _error_from(exc) from exc


@router.get("/tables/{table}/export", summary="导出 xlsx / csv（与页面同源）")
def export_table(
    table: str,
    format: str = Query(default="xlsx", description="xlsx / csv"),
    dataset_id: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    search: str | None = Query(default=None),
    sort: str | None = Query(default=None),
    order: str | None = Query(default=None),
    dimension: str | None = Query(default=None),
    metric: str | None = Query(default=None),
) -> Response:
    """同一个查询条件 → 同一个取数函数 → 完整结果。

    导出**不分页**（要全部行）；超过单次导出上限时明确拒绝（413 + 怎么缩小范围），
    绝不悄悄截断一个文件。
    """
    try:
        filename, content, mime = export_module.build_export(
            table, format,
            **_query_params(start, end, search, sort, order, dimension, metric, dataset_id),
        )
    except Exception as exc:                            # noqa: BLE001
        raise _error_from(exc) from exc
    # 文件名给两份：`filename*` 是中文业务名（浏览器/现代客户端优先用这个）；
    # `filename` 是 ASCII 兜底（老客户端用），用"表名 + 区间"拼，不用一串短横线凑数。
    ascii_fallback = f"{table}-{start or 'all'}_{end or 'all'}.{export_module.EXPORT_SUFFIX[format]}"
    return Response(
        content=content,
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="{ascii_fallback}"'
                                    f"; filename*=UTF-8''{_quote(filename)}",
            "X-Export-Rows": str(_row_count(content, format)),
            "X-Export-Table": table,
        },
    )


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def _row_count(content: bytes, fmt: str) -> int:
    """导出文件里的数据行数（写进响应头，方便脚本核对；不解析整份文件——xlsx 用 zip 目录里的行数不靠谱，
    这里对 csv 直接数行，xlsx 交给测试用 openpyxl 读回核对）。"""
    if fmt == "csv":
        return max(0, len(content.decode("utf-8-sig").splitlines()) - 1)
    return -1


__all__ = ["router"]
