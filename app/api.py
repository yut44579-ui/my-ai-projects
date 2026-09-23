"""api.py · HTTP 接口层（TASK-002C）—— 把 engine 的能力暴露成**真实可用**的接口。

分层原则（CLAUDE.md 的分层要求，本文件守死这一条）：
    本文件**只做**：HTTP 入参校验 → 调 engine/renderer → 落执行记录（state.py）→ 出响应。
    本文件**不做**：取数、算数、渲染、Excel 读写 —— 数字一律来自 `app/engine/executor.py`（D4），
                  Excel 一律由 `app/engine/renderer.py` 用 openpyxl 原地改（D5）。
    → 这就是"API 层很薄"的含义：换个前端/加个定时任务，算数逻辑一行都不用动。

端点清单（与 002C 指令逐条对应）：
    POST /api/upload                     上传 xlsx/xlsm，落盘 data/uploads/，返回行数列数
    GET  /api/schema                     数据字典（列名/类型/非空率/样例值）
    POST /api/execute                    算数 + 渲染（写 outputs/*.xlsx），返回金额与产出路径
    GET  /api/download/{execution_id}    下载**真实** xlsx（FileResponse）
    GET  /api/executions                 执行记录列表（含 spec / 数据哈希 / 代码版本 / 校验结果）
    GET  /api/health                     健康检查（含数据快照指纹）

⚠️ 一个必须说清楚的边界（本 TASK 范围内无法绕开，已作为 Scope 外问题上报）：
    `/api/execute` 会先核对**上传文件的 SHA256** 与冻结数据快照（`loader.EXPECTED_SHA256`）是否一致。
        · 一致 → 直接委托给 002A 的 executor 计算。因为字节完全相同，这**就是**在算上传的那份数据；
        · 不一致 → 返回 422 并说明原因，**不出数字、不出文件**（D10）。
    为什么不做成"任意文件都能算"：002A 的 `executor.compute_sales_amount()` 内部写死调用
    `loader.load_raw()`（读 data/Online Retail.xlsx），它**不接受**外部传入的 DataFrame 或路径。
    要支持任意上传文件，必须把 executor 改成"路径/DataFrame 驱动"，属于**改 002A 的计算逻辑** ——
    被 002C 指令禁止。所以这里选择"宁可明确拒绝，也不偷偷用别的数据算"，
    并把该改造记为 Scope 外问题（见交付报告），留给后续 TASK 处理。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import math
import re
import time
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app import state
from app.engine import executor, loader, renderer
from app.spec.models import DataSourceRef, MetricDSL, MetricSpec, OutputSpec, ReportSpec, TimeRange

# app/api.py → 向上 1 层 = 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[1]

SERVICE_NAME = "sales-report-agent"
SERVICE_VERSION = "0.1.0"

# 上传落盘 + 读文件的分片大小（1MB；23MB 文件不至于一次性全进内存）
_CHUNK_SIZE = 1024 * 1024

# 允许上传的后缀（002C 指令：xlsx/csv）。
# 注意：**上传**支持 csv，但**执行**只支持 xlsx —— 002A 的 loader 用 openpyxl 读 xlsx，
# 没有 csv 取数路径（改它=改 002A）。所以 csv 上传会在 /api/execute 里被明确拒绝，不猜。
ALLOWED_UPLOAD_SUFFIXES = (".xlsx", ".xlsm", ".csv")
EXECUTABLE_SUFFIXES = (".xlsx", ".xlsm")

# csv 编码回退顺序：utf-8-sig（Excel 导出的带 BOM 的 utf-8）→ gbk（中文 Excel 默认）
_CSV_ENCODINGS = ("utf-8-sig", "gbk")

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# 默认输出模板（002B 用代码生成的那份；相对项目根，写进 Spec 便于审计）
TEMPLATE_REL_PATH = "templates/weekly_sales_template.xlsx"

# 进程内缓存：上传文件解析结果（file_id → DataFrame）。
# 为什么缓存：读一次 23MB xlsx 约 100 秒，用户"上传→看字段→执行"会连着调两次。
# 为什么容量只有 3：单用户场景，不需要把几十份上传都留在内存里。
_UPLOAD_CACHE: dict[str, pd.DataFrame] = {}
_UPLOAD_CACHE_LIMIT = 3

# ════════════════════════════════════════════════════════════════════════
# 指标目录（受限 DSL 白名单）：能通过 API 请求的指标就这 6 个
# ════════════════════════════════════════════════════════════════════════
# 为什么是这 6 个：它们正好是模板 `weekly_sales_template.xlsx` 预留的数据格（B4:B9），
# 也是 executor 能算出来、renderer 能写进去的那批（口径 -> 计算 -> 单元格，三段都通）。
# cells / 名称从 renderer 的常量取（**不复制一份**，避免"两处布局漂移"）；
# op 写在这里，因为"这个指标该用什么算子"是 API 层对外的口径声明，必须一眼可见、可核对。
_METRIC_OPS: dict[str, str] = {
    "sales_amount": "sum",
    "rows_in_range": "count",
    "rows_valid": "count",
    "rows_excluded": "count",
    "excluded_amount": "sum",
    "valid_qty_sum": "sum",
}
SUPPORTED_METRICS: tuple[str, ...] = tuple(name for name, *_ in renderer.DEFAULT_METRIC_ROWS)


def _check_metric_catalog() -> None:
    """导入时自检：指标名 / 算子 / 单元格三份清单必须**逐一对齐**。

    对不上说明 renderer 的模板行改了而 api 的算子没跟上 —— 那会让 API 静默产出口径错的报表，
    所以宁可在**启动时**就炸掉（uvicorn 起不来，一眼可见），也不留到运行时。
    """
    problems = []
    if set(_METRIC_OPS) != set(SUPPORTED_METRICS):
        problems.append(f"算子表 {sorted(_METRIC_OPS)} ≠ 模板指标行 {sorted(SUPPORTED_METRICS)}")
    if set(renderer.DEFAULT_CELL_MAP) != set(SUPPORTED_METRICS):
        problems.append(
            f"单元格映射 {sorted(renderer.DEFAULT_CELL_MAP)} ≠ 模板指标行 {sorted(SUPPORTED_METRICS)}"
        )
    if problems:
        raise RuntimeError(
            "API 指标目录与渲染层不一致（口径/模板对不上，拒绝启动）：\n  " + "\n  ".join(problems)
        )


_check_metric_catalog()


# ════════════════════════════════════════════════════════════════════════
# 请求体模型
# ════════════════════════════════════════════════════════════════════════
class ExecuteRequest(BaseModel):
    """/api/execute 的请求体。

    extra="forbid"：多传字段直接 422，不静默忽略 —— 免得前端以为传了 `template` 之类就生效了，
    实际被无声丢弃（那种"看起来对但其实没生效"的坑，本项目一律当场报错）。
    """

    model_config = ConfigDict(extra="forbid")

    file_id: str = Field(..., min_length=1, description="POST /api/upload 返回的 file_id")
    start: _dt.date = Field(..., description="起始日期（含当天 00:00:00，D16-2）")
    end: _dt.date = Field(..., description="结束日期（含当天 23:59:59.999，D16-2）")
    metrics: list[str] = Field(
        ...,
        min_length=1,
        description=f"要计算的指标名，可选：{list(SUPPORTED_METRICS)}（口径先定义再算，D12 —— 故选必填）",
    )


# ════════════════════════════════════════════════════════════════════════
# FastAPI 应用
# ════════════════════════════════════════════════════════════════════════
app = FastAPI(
    title="sales-report-agent API",
    version=SERVICE_VERSION,
    description=(
        "销售报表自动化的最小闭环接口：上传 → 看字段 → 执行（算数+渲染）→ 下载真实 xlsx。\n\n"
        "数字一律由 app/engine/executor.py 按 D16 口径用 pandas 计算（D4）；"
        "Excel 一律由 app/engine/renderer.py 用 openpyxl 在模板副本上原地写入（D5）。"
    ),
)


# ── 1. 健康检查 ─────────────────────────────────────────────────────────
@app.get("/api/health", summary="健康检查")
def health() -> dict:
    """服务是否可用 + 数据快照指纹是否对得上 + 模板/目录是否就位。

    `status` 取值：
        "ok"        一切就绪
        "degraded"  服务活着，但**执行会失败**（数据快照缺失/哈希不符、模板缺失）
    刻意不用非 200 表示 degraded：进程本身是健康的，运维一眼看 body 里的 status 就行。
    """
    snapshot = loader.verify_data_source()          # 含 23MB 文件哈希，实测约几十毫秒
    template_path = PROJECT_ROOT / TEMPLATE_REL_PATH
    problems: list[str] = []
    if not snapshot["exists"]:
        problems.append(f"数据快照缺失：{snapshot['path']}")
    elif not snapshot["match"]:
        problems.append(
            f"数据快照 SHA256 不符：实际 {snapshot['sha256']}，期望 {snapshot['expected_sha256']}"
        )
    if not template_path.exists():
        problems.append(f"模板缺失：{template_path}")

    return {
        "status": "ok" if not problems else "degraded",
        "service": SERVICE_NAME,
        "api_version": SERVICE_VERSION,
        "task": "TASK-002C",
        "time": state.now_iso(),
        "code_version": state.code_version(),
        "data_snapshot": {
            "path": snapshot["path"],
            "exists": snapshot["exists"],
            "size_bytes": snapshot["size_bytes"],
            "sha256": snapshot["sha256"],
            "expected_sha256": snapshot["expected_sha256"],
            "match": snapshot["match"],
        },
        "template": {"path": str(template_path), "exists": template_path.exists()},
        "state": state.state_summary(),
        "problems": problems,
    }


# ── 2. 上传 ─────────────────────────────────────────────────────────────
@app.post("/api/upload", status_code=201, summary="上传 xlsx/xlsm/csv")
def upload(file: UploadFile = File(..., description="要分析的表格文件（.xlsx/.xlsm/.csv）")) -> dict:
    """把文件流式落盘到 data/uploads/，返回 {file_id, filename, rows, columns}。

    为什么要**流式**写盘 + 边写边算哈希：文件有 23MB，没必要为了算哈希再整份读进内存；
    而且哈希是**落盘的那份字节**的哈希（不是上传前的），后面用它做数据溯源才算数。

    上传成功 = 文件能真的被 pandas 读出来（行/列数是真的），解析失败会删掉半截文件并返回 400。
    """
    original_name = (file.filename or "").strip()
    safe_name = _safe_filename(original_name)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"不支持的文件类型：{safe_name!r}（后缀 {suffix or '(无)'}）。"
                f"只接受 {list(ALLOWED_UPLOAD_SUFFIXES)}；.xls 老格式请先在 Excel 里另存为 .xlsx。"
            ),
        )

    file_id = state.new_id("f")
    target = state.upload_dir() / f"{file_id}_{safe_name}"
    digest = hashlib.sha256()
    size_bytes = 0
    with open(target, "wb") as handle:
        while chunk := file.file.read(_CHUNK_SIZE):
            digest.update(chunk)
            size_bytes += len(chunk)
            handle.write(chunk)

    try:
        frame = _read_table(target, suffix)
    except HTTPException:
        raise
    except Exception as exc:                       # noqa: BLE001 —— 解析错误种类多，统一转成可读 400
        target.unlink(missing_ok=True)             # 解析不了的文件不留在盘上（D10 的精神）
        raise HTTPException(
            status_code=400,
            detail=f"文件解析失败（{type(exc).__name__}）：{exc}",
        ) from exc

    if frame.shape[1] == 0 or frame.shape[0] == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail=f"文件里没有可用数据：解析出 {frame.shape[0]} 行 × {frame.shape[1]} 列",
        )

    columns = [str(column) for column in frame.columns]
    _cache_upload(file_id, frame)

    record = state.record_upload(
        file_id=file_id,
        filename=original_name or safe_name,
        stored_path=str(target),
        size_bytes=size_bytes,
        sha256=digest.hexdigest(),
        rows=int(frame.shape[0]),
        column_names=columns,
    )

    return {
        "file_id": record["file_id"],
        "filename": record["filename"],
        "rows": record["rows"],
        "columns": record["columns"],            # 列数（int）
        "column_names": record["column_names"],  # 列名清单（前端/数据字典要用的在这里）
        "size_bytes": record["size_bytes"],
        "sha256": record["sha256"],
        "stored_path": record["stored_path"],
        "created_at": record["created_at"],
    }


# ── 3. 数据字典 ─────────────────────────────────────────────────────────
@app.get("/api/schema", summary="数据字典（列名/类型/非空率/样例值）")
def schema(
    file_id: str | None = Query(
        default=None,
        description="不传 = 看冻结数据快照 data/Online Retail.xlsx；传 = 看该次上传的文件",
    )
) -> dict:
    """列名 / 类型 / 非空率 / 样例值 + 总行数列数。"""
    started = time.perf_counter()
    if file_id:
        record = state.get_upload(file_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"未知的 file_id：{file_id}（请先 POST /api/upload）")
        frame = _upload_frame(record)
        source = {
            "kind": "upload",
            "file_id": record["file_id"],
            "filename": record["filename"],
            "path": record["stored_path"],
            "sha256": record["sha256"],
        }
    else:
        try:
            frame = loader.load_raw()               # 默认快照，带 SHA256 校验（D10）
        except loader.DataSourceError as exc:
            raise HTTPException(status_code=503, detail=f"冻结数据快照不可用，无法给出数据字典：{exc}") from exc
        source = {
            "kind": "snapshot",
            "path": str(loader.DEFAULT_DATA_PATH),
            "sha256": loader.sha256_of_file(loader.DEFAULT_DATA_PATH),
        }

    fields = [_field_profile(frame[column], str(column)) for column in frame.columns]
    return {
        "source": source,
        "rows": int(frame.shape[0]),
        "column_count": len(fields),
        "columns": [field["name"] for field in fields],
        "fields": fields,
        "seconds": round(time.perf_counter() - started, 3),
    }


# ── 4. 执行（算数 + 渲染）────────────────────────────────────────────────
@app.post("/api/execute", status_code=200, summary="执行：算数 + 渲染成 Excel")
def execute(request: ExecuteRequest) -> dict:
    """按 Spec 口径算数并渲染报表，返回 {execution_id, amount, rows_in_range, excel_path, seconds}。

    执行链（每一步失败都**不产出文件**，D10；同时落一条 status=failed 的执行记录）：
        ① 校验 file_id / 指标名 / 日期 → ② 核对上传文件与冻结快照同源（SHA256）
        → ③ executor 按 D16 口径算（全精度）→ ④ renderer 写进模板副本并读回自检
        → ⑤ 落执行记录（execution_id / spec / 数据哈希 / 代码版本 / 校验结果 / 耗时）
    """
    started = time.perf_counter()

    # ① 入参校验（file_id 先查，保证"不存在的 file_id"稳定返回 404）
    upload_record = state.get_upload(request.file_id)
    if upload_record is None:
        raise HTTPException(
            status_code=404,
            detail=f"未知的 file_id：{request.file_id}（file_id 由 POST /api/upload 返回；服务重启后仍有效）",
        )

    unknown_metrics = [name for name in request.metrics if name not in SUPPORTED_METRICS]
    if unknown_metrics:
        raise HTTPException(
            status_code=422,
            detail=(
                f"不支持的指标：{unknown_metrics}。当前支持：{list(SUPPORTED_METRICS)}"
                f"（受限于模板预留单元格 B4:B9 与 executor 的口径，见 app/api.py 的指标目录检查）"
            ),
        )
    if request.start > request.end:
        raise HTTPException(
            status_code=422,
            detail=f"起始日期晚于结束日期：{request.start} > {request.end}",
        )

    stored_path = Path(upload_record["stored_path"])
    if stored_path.suffix.lower() not in EXECUTABLE_SUFFIXES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"该文件是 {stored_path.suffix} 格式，当前**执行**只支持 .xlsx/.xlsm："
                f"002A 的 loader 用 openpyxl 读 xlsx，没有 csv 取数路径（改它=改 002A 的计算逻辑，本 TASK 禁止）。"
                f"请上传 .xlsx（或把 csv 另存为 xlsx）。"
            ),
        )

    # ② 数据同源核对（为什么必须核对：executor 只认冻结快照，见文件头说明）
    snapshot_sha = loader.EXPECTED_SHA256
    if upload_record["sha256"] != snapshot_sha:
        raise HTTPException(
            status_code=422,
            detail=(
                f"该文件与冻结数据快照不是同一份数据，拒绝用别的数据出报表（D10）：\n"
                f"  文件 SHA256：{upload_record['sha256']}\n"
                f"  快照 SHA256：{snapshot_sha}\n"
                f"  原因：002A 的 executor 只支持冻结快照（写死 loader.load_raw()），"
                f"支持任意文件取数需要改造 executor（Scope 外，已上报）。"
            ),
        )

    execution_id = state.new_id("x")
    spec = _build_spec(request, upload_record)

    # ③ 算数（全精度；口径由 Spec 固化的排除规则决定 —— 与 002A/002B 同一套）
    try:
        result = executor.compute_sales_amount(
            request.start, request.end, **renderer.executor_kwargs(spec)
        )
        compute_seconds = float(result["seconds"])
    except loader.DataSourceError as exc:
        _record_failure(execution_id, request, upload_record, spec, 1, f"数据源错误：{exc}", started)
        raise HTTPException(status_code=503, detail=f"数据源不可用，未产出文件（D10）：{exc}") from exc
    except ValueError as exc:
        _record_failure(execution_id, request, upload_record, spec, 1, f"入参错误：{exc}", started)
        raise HTTPException(status_code=422, detail=f"入参不合法：{exc}") from exc

    # ④ 渲染（写进模板副本 → 读回自检；自检不过 renderer 会删掉坏文件并抛 RenderError）
    try:
        values = renderer.values_from_result(spec, result)
        output_path = state.output_dir() / f"{execution_id}.xlsx"
        rendered = renderer.render_report(spec, values, output_path=output_path)
    except renderer.RenderError as exc:
        _record_failure(execution_id, request, upload_record, spec, 2, f"渲染失败：{exc}", started)
        raise HTTPException(
            status_code=500,
            detail=f"渲染失败，未产出文件（D10）：{exc}（已记录失败执行 {execution_id}）",
        ) from exc

    # ⑤ 落执行记录（D11：spec / 数据哈希 / 代码版本 / execution_id / 校验结果都要绑定）
    seconds = time.perf_counter() - started
    record = {
        "execution_id": execution_id,
        "status": "success",
        "file_id": upload_record["file_id"],
        "source_filename": upload_record["filename"],
        "data_sha256": upload_record["sha256"],
        "snapshot_sha256": snapshot_sha,
        "data_snapshot_match": True,
        "spec": spec.to_dict(),
        "range": {"start": str(request.start), "end": str(request.end)},
        "metrics": {name: _json_number(value) for name, value in values.items()},
        "amount": result["amount"],                        # 全精度（审计用，D17-2）
        "amount_display": round(float(result["amount"]), 2),   # 展示口径 2 位小数（D17-2）
        "rows_in_range": result["rows_in_range"],
        "rows_valid": result["rows_valid"],
        "rows_excluded": result["rows_excluded"],
        "excluded_amount": result["excluded_amount"],
        "validations": result["validations"],              # 校验结果（含 6 项 checks）
        "excel_path": rendered["output_path"],
        "excel_rel_path": _relative_to_root(rendered["output_path"]),
        "excel_size_bytes": rendered["size_bytes"],
        "render_verification": rendered["verification"],
        "code_version": state.code_version(),
        "compute_seconds": round(compute_seconds, 3),
        "render_seconds": round(float(rendered["seconds"]), 3),
        "seconds": round(seconds, 3),
    }
    state.record_execution(record)

    return {
        "execution_id": execution_id,
        "status": record["status"],
        "amount": record["amount_display"],        # ← 展示口径：2 位小数（D17-2）
        "amount_full": record["amount"],           # ← 全精度原值（复核/审计时用这个比）
        "rows_in_range": record["rows_in_range"],
        "rows_valid": record["rows_valid"],
        "rows_excluded": record["rows_excluded"],
        "range": record["range"],
        "metrics": record["metrics"],              # = 写进报表单元格的原值（全精度）
        "excel_path": record["excel_path"],
        "excel_rel_path": record["excel_rel_path"],
        "excel_size_bytes": record["excel_size_bytes"],
        "download_url": f"/api/download/{execution_id}",
        "template": rendered["template"],
        "sheet": rendered["sheet"],
        "file_id": record["file_id"],
        "data_sha256": record["data_sha256"],
        "code_version": record["code_version"],
        "compute_seconds": record["compute_seconds"],
        "render_seconds": record["render_seconds"],
        "seconds": record["seconds"],
        "verification": record["render_verification"],   # 读回自检（6 项全过才会走到这里）
    }


# ── 5. 下载 ─────────────────────────────────────────────────────────────
@app.get("/api/download/{execution_id}", summary="下载产出报表（真实 xlsx）")
def download(execution_id: str) -> FileResponse:
    """按 execution_id 返回**真实产出**的 xlsx 文件（FileResponse，不是拼出来的响应）。"""
    record = state.get_execution(execution_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"未知的 execution_id：{execution_id}")
    if record.get("status") != "success" or not record.get("excel_path"):
        raise HTTPException(
            status_code=404,
            detail=(
                f"该次执行没有产出文件（status={record.get('status')}，D10 失败不产出）："
                f"{record.get('error') or '无错误信息'}"
            ),
        )

    path = Path(record["excel_path"])
    if not path.exists():
        raise HTTPException(
            status_code=410,
            detail=f"产出文件已不在盘上（记录还在）：{path}（execution_id={execution_id}）",
        )

    start, end = record["range"]["start"], record["range"]["end"]
    return FileResponse(
        path,
        media_type=XLSX_MEDIA_TYPE,
        filename=f"sales_report_{start}_{end}.xlsx",   # ASCII 文件名，避免下载时编码问题
    )


# ── 6. 执行记录 ─────────────────────────────────────────────────────────
@app.get("/api/executions", summary="执行记录列表（新的在前）")
def executions(limit: int = Query(default=50, ge=1, le=500)) -> dict:
    """执行记录（含 spec / 数据哈希 / 代码版本 / 校验结果 / 产出路径）。"""
    records = state.list_executions(limit=limit)
    return {
        "count": len(records),
        "total": state.count_executions(),
        "limit": limit,
        "executions": records,
    }


# ════════════════════════════════════════════════════════════════════════
# 内部工具
# ════════════════════════════════════════════════════════════════════════
def _build_spec(request: ExecuteRequest, upload_record: dict) -> ReportSpec:
    """把一次 API 请求固化成一份 Report Spec（D11：执行必须绑定 Spec）。

    为什么要固化成 Spec 而不是直接调 executor：Spec 是**审计凭据** ——
    它把"这次用了哪些指标、什么口径（exclude）、写哪几个单元格、哪个模板"完整冻结下来，
    随执行记录一起落盘。以后同参数再跑，只要 Spec 一样，结果就必须一样（可重复）。
    """
    return ReportSpec(
        spec_id=f"adhoc-{upload_record['file_id']}",
        name=f"{request.start}~{request.end} 销售汇总",
        version=1,
        data_source=DataSourceRef(kind="excel", path=_relative_to_root(upload_record["stored_path"])),
        time_range=TimeRange(start=request.start, end=request.end),
        metrics=tuple(
            MetricSpec(name=name, dsl=MetricDSL(op=_METRIC_OPS[name], field=name))
            for name in request.metrics
        ),
        output=OutputSpec(
            template=TEMPLATE_REL_PATH,
            sheet=renderer.DEFAULT_SHEET_NAME,
            cells={name: renderer.DEFAULT_CELL_MAP[name] for name in request.metrics},
        ),
        created_at=_dt.datetime.now().astimezone(),
    )


def _record_failure(
    execution_id: str,
    request: ExecuteRequest,
    upload_record: dict,
    spec: ReportSpec,
    stage: int,
    error: str,
    started: float,
) -> None:
    """失败也落一条执行记录（D10 要求"记录错误"；只有记录才能事后说明"当时为什么没出报表"）。"""
    state.record_execution(
        {
            "execution_id": execution_id,
            "status": "failed",
            "stage": stage,                    # 1 = 计算阶段, 2 = 渲染阶段
            "error": error,
            "file_id": upload_record["file_id"],
            "source_filename": upload_record["filename"],
            "data_sha256": upload_record["sha256"],
            "snapshot_sha256": loader.EXPECTED_SHA256,
            "spec": spec.to_dict(),
            "range": {"start": str(request.start), "end": str(request.end)},
            "metrics": {},
            "amount": None,
            "rows_in_range": None,
            "excel_path": None,                # 失败必须没有产出（D10）
            "code_version": state.code_version(),
            "seconds": round(time.perf_counter() - started, 3),
        }
    )


def _read_table(path: Path, suffix: str) -> pd.DataFrame:
    """读 xlsx/csv 成 DataFrame（只为"看清数据长什么样"：列名/类型/非空率/行数）。

    不在这里做任何口径过滤 —— 口径只存在于 metrics.py（D16 单一来源）。
    """
    if suffix in EXECUTABLE_SUFFIXES:
        return pd.read_excel(path, engine="openpyxl")

    last_error: Exception | None = None
    for encoding in _CSV_ENCODINGS:
        try:
            return pd.read_csv(path, encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    raise ValueError(
        f"csv 解码失败（已试 {list(_CSV_ENCODINGS)}）：{last_error}\n  请把文件另存为 UTF-8 或 GBK 编码后重传"
    )


def _upload_frame(record: dict) -> pd.DataFrame:
    """取上传文件对应的 DataFrame（优先用进程内缓存；服务重启后缓存没了就重读盘上的文件）。"""
    file_id = record["file_id"]
    cached = _UPLOAD_CACHE.get(file_id)
    if cached is not None:
        return cached
    path = Path(record["stored_path"])
    if not path.exists():
        raise HTTPException(
            status_code=410,
            detail=f"上传的文件已不在盘上：{path}（file_id={file_id}，请重新上传）",
        )
    frame = _read_table(path, path.suffix.lower())
    _cache_upload(file_id, frame)
    return frame


def _cache_upload(file_id: str, frame: pd.DataFrame) -> None:
    if len(_UPLOAD_CACHE) >= _UPLOAD_CACHE_LIMIT:
        _UPLOAD_CACHE.pop(next(iter(_UPLOAD_CACHE)))   # 淘汰最早的那个（dict 保序）
    _UPLOAD_CACHE[file_id] = frame


def _field_profile(series: pd.Series, name: str) -> dict:
    """一列的画像：类型 / 非空率 / 几个样例值。"""
    total = int(len(series))
    non_null = int(series.notna().sum())
    return {
        "name": name,
        "dtype": str(series.dtype),
        "inferred_type": _inferred_type(series),
        "null_count": total - non_null,
        "non_null_rate": round(non_null / total, 6) if total else 0.0,
        "sample_values": _sample_values(series),
    }


def _inferred_type(series: pd.Series) -> str:
    """给前端/人看的类型名（比 pandas 的 dtype 直白一点）。"""
    kind = series.dtype.kind
    if kind in "iu":
        return "int"
    if kind == "f":
        return "float"
    if kind in "Mm":
        return "datetime"
    if kind == "b":
        return "bool"
    python_types = {type(_json_safe(value)).__name__ for value in series.dropna().head(50)}
    return python_types.pop() if len(python_types) == 1 else "mixed"


def _sample_values(series: pd.Series, count: int = 3) -> list:
    """取前几个**互不相同**的非空样例值（只看前 50 个非空值，不做全表扫描）。"""
    samples: list[Any] = []
    for value in series.dropna().head(50):
        safe = _json_safe(value)
        if safe not in samples:
            samples.append(safe)
        if len(samples) >= count:
            break
    return samples


def _json_safe(value: Any) -> Any:
    """把 pandas/numpy 的标量转成能 json 序列化的 Python 值。"""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (_dt.datetime, _dt.date)):       # pandas.Timestamp 也是 datetime 子类
        return value.isoformat()
    if hasattr(value, "item"):                            # numpy.int64/float64 等标量
        return value.item()
    if isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _json_number(value: Any) -> int | float:
    """数值指标 → 原生 Python 数字（numpy 标量不能直接 json.dumps）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HTTPException(status_code=500, detail=f"指标值不是数字：{value!r}")
    return int(value) if isinstance(value, int) else float(value)


def _safe_filename(name: str) -> str:
    """上传文件名消毒：去掉目录部分（防 ../../ 路径穿越）+ 去掉文件名非法字符。"""
    base = Path(name.strip().replace("\\", "/")).name
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).strip(" .")
    return cleaned or "upload"


def _relative_to_root(path: str | Path) -> str:
    """转成相对项目根的路径（能转就转，不能转就原样返回绝对路径）。"""
    try:
        return Path(path).resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)
