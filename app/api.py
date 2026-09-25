"""api.py · HTTP 接口层（TASK-002C）—— 把 engine 的能力暴露成**真实可用**的接口。

分层原则（CLAUDE.md 的分层要求，本文件守死这一条）：
    本文件**只做**：HTTP 入参校验 → 调 engine/renderer → 落执行记录（state.py）→ 出响应。
    本文件**不做**：取数、算数、渲染、Excel 读写 —— 数字一律来自 `app/engine/executor.py`（D4），
                  Excel 一律由 `app/engine/renderer.py` 用 openpyxl 原地改（D5）。
    → 这就是"API 层很薄"的含义：换个前端/加个定时任务，算数逻辑一行都不用动。

端点清单（与 002C / 002D 指令逐条对应）：
    POST /api/upload                     上传 xlsx/xlsm，落盘 data/uploads/，返回行数列数
    GET  /api/schema                     数据字典（列名/类型/非空率/样例值）
    POST /api/execute                    算数 + 渲染（写 outputs/*.xlsx），返回金额与产出路径
    GET  /api/download/{execution_id}    下载**真实** xlsx（FileResponse）
    GET  /api/executions                 执行记录列表（含 spec / 数据哈希 / 代码版本 / 校验结果）
    GET  /api/health                     健康检查（含数据快照指纹）
    ── 任务层（TASK-002D，R005 评审通过后新增；上面 6 个端点的语义**一个都没改**）──
    POST /api/tasks                      固化任务：便捷参数或完整 Report Spec → task_id + 冻结 Spec
    GET  /api/tasks                      任务列表（分页）
    GET  /api/tasks/{task_id}            单个任务（含完整冻结 Spec；**刷新页面后靠它重读**）
    POST /api/tasks/{task_id}/run        用任务里**冻结的 Spec** 执行一次（复用同一套引擎）
    GET  /api/tasks/{task_id}/runs       该任务的执行历史（成功与失败都在）
    ── 文档输入（TASK-003，新路径；上面 11 个端点一行没改）──
    POST /api/documents                 上传 Word/PDF 并提取文本（实现见 app/api_documents.py）
    GET  /api/documents                 文档列表（分页）
    GET  /api/documents/{doc_id}        单个文档（include_text=true 时带全文）
    GET  /api/documents/{doc_id}/text   全文（text/plain）
    POST /api/documents/{doc_id}/summary 结构化摘要（规则抽取：原句摘录 + 词频关键词 + 正则关键事实）

为什么要任务层（TASK-002D 的存在理由）：
    002C 只有 ad-hoc 路径（file_id → execute → execution_id），**没有"任务"这个实体** ——
    没有 task_id、没有后端返回的 Spec、执行记录里也没有 task_id。前端要做到
    "一句话建任务 → 看 Spec → 执行 → 刷新后记录还在"，就必须有**持久化的任务实体**（D9/D11）。
    这不是前端私有协议：TASK-005（LLM 解析）与 TASK-007（定时调度）都建在这一层上。

统一错误响应体（R005 required_change #5）：
    所有 4xx/5xx 都是 `{"error": {"code", "message", "detail"}, "detail": <FastAPI 原样>}`。
    **顶层 `detail` 原样保留** —— 002C 的 6 个端点与那 18 条测试按 FastAPI 默认契约读 `detail`，
    该契约在 002D 里是冻结的（R005 #10），所以新形状只**追加**，不改旧字段。

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
import json as _json
import math
import re
import time
from pathlib import Path
from typing import Any, Literal, Sequence

import pandas as pd
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import api_auth, api_chat, api_datasets, api_documents, prewarm, state
from app.engine import executor, loader, renderer
from app.engine import metrics as engine_metrics
from app.spec.models import (
    DEFAULT_TIMEZONE,
    DataSourceRef,
    MetricDSL,
    MetricSpec,
    OutputSpec,
    ReportSpec,
    TimeRange,
)

# app/api.py → 向上 1 层 = 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[1]

SERVICE_NAME = "sales-report-agent"
SERVICE_VERSION = "0.2.0"          # 0.1.0 = 002C（6 个端点）；0.2.0 = 002D（+5 个任务端点）

# 任务层用的 ID 前缀（task）与 Spec ID 前缀（task-<task_id>，便于从 Spec 一眼看出属于哪个任务）
_TASK_ID_PREFIX = "t"

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
# 统一错误响应体（R005 required_change #5）
# ════════════════════════════════════════════════════════════════════════
# 为什么要统一：前端（原生 JS，没有类型系统）需要**稳定可判**的错误形状 ——
# 靠 HTTP 状态码硬编码判断"这是业务失败还是参数错"最容易写错。这里给每个错误一个
# 机器可读的 `code`，前端按 code 分支即可。
#
# ⚠️ 兼容性约束（R005 #10）：002C 的 6 个端点 + 18 条测试按 FastAPI 默认契约读顶层 `detail`，
# 因此 `detail` **原样保留**，`error` 是**追加**的新字段。既有端点行为不变。
_ERROR_CODES: dict[int, str] = {
    400: "bad_request",
    404: "not_found",
    409: "conflict",
    410: "gone",
    422: "unprocessable",
    500: "internal_error",
    503: "service_unavailable",
}


class ErrorDetail(BaseModel):
    """错误主体：`code` 给程序判，`message` 给人读，`detail` 放原始信息（可能是对象/数组）。"""

    code: str = Field(..., description="机器可读错误码，如 task_not_found / data_snapshot_mismatch")
    message: str = Field(..., description="给人看的一句话说明")
    detail: Any = Field(default=None, description="原始细节：字符串，或校验错误列表等结构")


class ErrorResponse(BaseModel):
    """统一错误响应体。`detail` 与 `error.message` 同源，为兼容 FastAPI 默认契约而保留。"""

    error: ErrorDetail
    detail: Any = Field(default=None, description="FastAPI 默认字段，原样保留（向后兼容）")


class ApiError(HTTPException):
    """带**机器可读 code** 的 HTTPException。

    既有代码里的 `raise HTTPException(...)` 不用改：没有 code 时按状态码映射一个默认 code
    （见 `_ERROR_CODES`）。新端点用本类给出更精确的 code。
    """

    def __init__(self, status_code: int, code: str, message: str, detail: Any = None) -> None:
        super().__init__(status_code=status_code, detail=message if detail is None else detail)
        self.code = code
        self.message = message


def _error_payload(status_code: int, detail: Any, code: str | None = None, message: str | None = None) -> dict:
    """组装错误响应体（唯一入口，保证所有错误的形状一致）。"""
    if message is None:
        message = detail if isinstance(detail, str) else _json.dumps(detail, ensure_ascii=False)
    return {
        "error": {
            "code": code or _ERROR_CODES.get(status_code, "error"),
            "message": message,
            "detail": detail,
        },
        "detail": detail,          # ← 冻结的旧契约（R005 #10：只增不改）
    }


# 端点文档里声明的错误响应（R005 #5："在 OpenAPI 中体现"）—— 这样 /openapi.json 里
# 每个端点都写明"失败长什么样"，前端不用靠猜。
_ERROR_RESPONSES: dict[int | str, dict] = {
    404: {"model": ErrorResponse, "description": "找不到对象（file_id / task_id / execution_id 不存在）"},
    410: {"model": ErrorResponse, "description": "对象曾经存在但现在没了（如上传记录/产出文件已不在）"},
    422: {"model": ErrorResponse, "description": "入参不合法或数据不匹配（口径/区间/数据哈希）"},
    500: {"model": ErrorResponse, "description": "执行失败（已记录 status=failed 的 run，不产出文件）"},
    503: {"model": ErrorResponse, "description": "数据源不可用（冻结快照缺失/哈希不符）"},
}


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


class CreateTaskRequest(BaseModel):
    """/api/tasks 的请求体（TASK-002D）。

    **两条路径，二选一**（R005 required_change #3）：
        便捷路径  {file_id, start, end, metrics, name?}   —— 前端/手工建任务用，等价于 /api/execute 的入参
        完整 Spec {file_id, spec:{…完整 Report Spec…}, name?}
                  —— TASK-005（LLM 解析）与 TASK-007（调度）把**已经定稿的 Spec** 直接落成任务

    为什么必须支持完整 Spec：若只收 file_id/start/end/metrics，任务层就变成"只有这个前端能用"的
    私有协议 —— LLM 解析出来的口径（排除规则、单元格映射、模板）根本传不进来。
    两条路径产出的都是**同一种 shape 的 Spec 快照**（ReportSpec.to_dict()），落到任务记录里没有区别。

    同时给两条路径 = 含糊（到底按哪个建？）→ **直接 422**，不猜（铁律 4 的同一条精神）。
    """

    model_config = ConfigDict(extra="forbid")

    file_id: str = Field(..., min_length=1, description="POST /api/upload 返回的 file_id")
    name: str | None = Field(default=None, max_length=200, description="任务名；不传则按区间自动生成")

    # ── 便捷路径 ──────────────────────────────────────────────────────
    start: _dt.date | None = Field(default=None, description="便捷路径：起始日期（含当天，D16-2）")
    end: _dt.date | None = Field(default=None, description="便捷路径：结束日期（含当天，D16-2）")
    metrics: list[str] | None = Field(default=None, min_length=1, description="便捷路径：指标名清单")

    # ── 完整 Spec 路径 ────────────────────────────────────────────────
    spec: ReportSpec | None = Field(
        default=None,
        description="完整 Report Spec（与便捷路径二选一）；口径/模板/单元格映射随它一起冻结（D11）",
    )

    @model_validator(mode="after")
    def _check_exactly_one_path(self) -> "CreateTaskRequest":
        convenience_given = [field for field in (self.start, self.end, self.metrics) if field is not None]
        if self.spec is not None and convenience_given:
            raise ValueError(
                "spec 与 start/end/metrics 不能同时提供：二选一"
                "（完整 Spec 路径 = 口径已在 Spec 里；便捷路径 = 用模板默认口径）"
            )
        if self.spec is None:
            if not (self.start and self.end and self.metrics):
                raise ValueError(
                    "必须提供 start + end + metrics（便捷路径），或 spec（完整 Report Spec）"
                )
        return self


# ════════════════════════════════════════════════════════════════════════
# FastAPI 应用
# ════════════════════════════════════════════════════════════════════════
app = FastAPI(
    title="sales-report-agent API",
    version=SERVICE_VERSION,
    # 冷启动预热：启动时**后台**读一遍数据集（不阻塞就绪、不改任何端点形状）。
    # 只做这一件事 —— 详见 app/prewarm.py 的说明。
    lifespan=prewarm.lifespan,
    description=(
        "销售报表自动化的最小闭环接口：上传 → 看字段 → 执行（算数+渲染）→ 下载真实 xlsx。\n\n"
        "任务层（TASK-002D）：固化任务 → 看冻结 Spec → 按任务执行 → 看执行历史。\n\n"
        "数字一律由 app/engine/executor.py 按 D16 口径用 pandas 计算（D4）；"
        "Excel 一律由 app/engine/renderer.py 用 openpyxl 在模板副本上原地写入（D5）。\n\n"
        "**错误响应统一形状**：`{\"error\": {\"code\", \"message\", \"detail\"}, \"detail\": …}`，"
        "前端按 `error.code` 分支即可（顶层 `detail` 为兼容 FastAPI 默认契约而保留）。"
    ),
)


# ════════════════════════════════════════════════════════════════════════
# 异常处理器：把**所有** 4xx/5xx 统一成 {error:{code,message,detail}} + 兼容的 detail
# ════════════════════════════════════════════════════════════════════════
# 处理 Starlette 的 HTTPException（FastAPI 的 HTTPException 是它的子类）—— 这样
# 路由未命中（404）之类的框架级错误也是同一种形状，前端不用分两套代码。
@app.exception_handler(StarletteHTTPException)
async def _http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None)
    headers = getattr(exc, "headers", None)
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_payload(exc.status_code, exc.detail, code=code, message=message),
        headers=headers,
    )


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """请求体/查询参数校验失败（pydantic）→ 同样套上统一形状。

    `detail` 保持 FastAPI 的原始结构（错误列表）—— 旧客户端读到的还是老东西。
        为什么用 exc.errors() 而不自己去解析：FastAPI 默认处理器就是这么做的，行为一致。
    """
    errors = jsonable_encoder(exc.errors())
    return JSONResponse(
        status_code=422,
        content=_error_payload(
            422,
            errors,
            code="validation_error",
            message="请求参数校验失败（字段缺失/类型不对/多传了字段）",
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
        "task": "TASK-002D",
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
@app.post("/api/execute", status_code=200, summary="执行：算数 + 渲染成 Excel（临时路径）")
def execute(request: ExecuteRequest) -> dict:
    """按 Spec 口径算数并渲染报表，返回 {execution_id, amount, rows_in_range, excel_path, seconds}。

    执行链（每一步失败都**不产出文件**，D10；计算/渲染失败会落 status=failed 的执行记录）：
        ① 校验 file_id / 指标名 / 日期 → ② 核对上传文件与冻结快照同源（SHA256）
        → ③ executor 按 D16 口径算（全精度）→ ④ renderer 写进模板副本并读回自检
        → ⑤ 落执行记录（execution_id / spec / 数据哈希 / 代码版本 / 校验结果 / 耗时）

    ③④⑤ 与 `POST /api/tasks/{task_id}/run` **共用** `_execute_and_record()`（R005 #10）——
    本端点是"临时跑一次"的入口，任务端点是"按固化 Spec 跑"的入口，算数与渲染必须只有一套实现。
    """
    started = time.perf_counter()

    # ① 入参校验（file_id 先查，保证"不存在的 file_id"稳定返回 404）
    upload_record = _require_upload(request.file_id)
    _check_metrics(request.metrics)
    _check_date_order(request.start, request.end)
    _check_executable_file(upload_record)
    # ② 数据同源核对（为什么必须核对：executor 只认冻结快照，见文件头说明）
    _check_snapshot_identity(upload_record)

    execution_id = state.new_id("x")
    spec = _build_spec(
        file_id=upload_record["file_id"],
        stored_path=upload_record["stored_path"],
        start=request.start,
        end=request.end,
        metrics=request.metrics,
        spec_id=f"adhoc-{upload_record['file_id']}",     # 002C 既有契约（测试钉死）
        name=f"{request.start}~{request.end} 销售汇总",
    )

    # ③④⑤ 算数 + 渲染 + 落执行记录（与任务端点共用同一段 —— R005 #10）
    record = _execute_and_record(
        upload_record=upload_record,
        spec=spec,
        execution_id=execution_id,
        started=started,
    )
    return _execute_response(record)


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
# 任务层（TASK-002D）—— D9/D11 里"固化成任务"的那个任务
# ════════════════════════════════════════════════════════════════════════
# ── 7. 建任务（固化 Spec）────────────────────────────────────────────────
@app.post(
    "/api/tasks",
    status_code=201,
    summary="建任务：把口径固化成任务（便捷参数 或 完整 Report Spec 二选一）",
    responses=_ERROR_RESPONSES,
)
def create_task(request: CreateTaskRequest) -> dict:
    """固化一个报表任务，返回 {task_id, spec, …} —— 前端区块③"看 Spec"的真数据源。

    两条路径（R005 required_change #3）：
        · 便捷路径  {file_id, start, end, metrics}  → 用模板默认口径（单元格映射 B4:B9）建 Spec
        · 完整 Spec {file_id, spec}                 → 直接用提交的 Spec（TASK-005/007 用这条）

    这里**只固化、不执行**（D9：先确认再固化，之后执行不再调 LLM）。
    建任务时会校验：区间必须是绝对日期（相对时间留给 TASK-007）、指标在目录内、
    数据文件可执行（xlsx）、Spec 的口径与模板对得上。
    **不校验**数据哈希是否等于快照 —— 那是**执行时**的闸门（run 会拒绝并留痕，见 run_task）。
    """
    upload_record = _require_upload(request.file_id)

    # 相对时间：002D 只支持绝对区间 —— **建任务时就拒绝**，别等 run 才炸（早报错，早修）
    if request.spec is not None and request.spec.time_range.mode != "absolute":
        raise ApiError(
            422,
            "relative_time_not_implemented",
            (
                f"时间区间 mode={request.spec.time_range.mode!r} 是相对区间，当前只支持 mode='absolute'：\n"
                f"  相对区间的解析（'上周'是哪一周、按哪个基准日、哪个时区）需要**运行时基准日**，"
                f"这部分设计属 TASK-007（定时调度）—— 本 TASK 只把 mode 字段冻结进 Spec schema，不解析。"
            ),
        )

    task_id = state.new_id(_TASK_ID_PREFIX)
    if request.spec is not None:
        spec = request.spec
        _validate_submitted_spec(spec, upload_record)
    else:
        _check_metrics(request.metrics)
        _check_date_order(request.start, request.end)
        _check_executable_file(upload_record)
        spec = _build_spec(
            file_id=upload_record["file_id"],
            stored_path=upload_record["stored_path"],
            start=request.start,
            end=request.end,
            metrics=request.metrics,
            spec_id=f"task-{task_id}",
            name=request.name or f"{request.start}~{request.end} 销售汇总",
        )

    snapshot_sha = loader.EXPECTED_SHA256
    data_snapshot_match = upload_record["sha256"] == snapshot_sha
    warnings: list[str] = []
    if not data_snapshot_match:
        warnings.append(
            f"该文件与冻结数据快照不是同一份数据（文件 {upload_record['sha256'][:12]}… vs "
            f"快照 {snapshot_sha[:12]}…）：任务能建，但**执行时会被拒绝并记录失败**（D10），"
            f"不会产出报表。请上传 data/Online Retail.xlsx。"
        )

    record = state.record_task(
        task_id=task_id,
        name=request.name or spec.name,         # 显式给的 name 优先；否则用 Spec 自己的名字
        file_id=upload_record["file_id"],
        source_filename=upload_record["filename"],
        data_sha256=upload_record["sha256"],
        spec=spec.to_dict(),
        data_snapshot_match=data_snapshot_match,
    )
    return _task_detail(record, stats={}, warnings=warnings)


# ── 8. 任务列表 ─────────────────────────────────────────────────────────
@app.get("/api/tasks", summary="任务列表（新的在前，分页）", responses=_ERROR_RESPONSES)
def list_tasks(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """任务列表（分页，R005 required_change #8）。

    每条带 `spec_summary`（区间/指标/单元格）与执行统计（run_count / 最近一次的状态与时间）——
    统计是**每次从执行记录现算**的，不在任务里存计数（派生数据会漂移）。
    """
    records, total = state.list_tasks(limit=limit, offset=offset)
    stats = state.run_stats_by_task()
    tasks = [_task_summary(record, stats.get(record["task_id"], {})) for record in records]
    return {
        "count": len(tasks),
        "total": total,
        "limit": limit,
        "offset": offset,
        "tasks": tasks,
    }


# ── 9. 单个任务（刷新页面后靠它重读）────────────────────────────────────
@app.get("/api/tasks/{task_id}", summary="单个任务（含完整冻结 Spec）", responses=_ERROR_RESPONSES)
def get_task(task_id: str) -> dict:
    """一个任务的完整信息（含**完整冻结 Spec**）。

    为什么单列一个端点：前端刷新后不能靠内存里的变量 —— 必须能从后端**重读**出
    task_id 对应的 Spec 与状态（AC 里钉死了这一条，禁止把 Spec 写死在前端）。
    """
    record = state.get_task(task_id)
    if record is None:
        raise ApiError(
            404,
            "task_not_found",
            f"未知的 task_id：{task_id}（现有任务用 GET /api/tasks 查）",
        )
    return _task_detail(record, stats=state.run_stats_by_task().get(task_id, {}))


# ── 10. 按任务执行（用冻结的 Spec）───────────────────────────────────────
@app.post("/api/tasks/{task_id}/run", summary="执行任务一次（用任务里冻结的 Spec）", responses=_ERROR_RESPONSES)
def run_task(task_id: str) -> dict:
    """按任务里**冻结的 Spec** 执行一次：算数 + 渲染 + 落执行记录（task_id 绑上）。

    与 `/api/execute` 的区别：**不再接受任何口径参数** —— 区间/指标/单元格全从任务的 Spec 读，
    所以"同一任务每次结果一致"（D9/D11 的可重复性）。

    数据闸门（沿用 D10）：执行前核对三方哈希 —— 任务绑定的 / 上传的 / 冻结快照的。
    任一不符 → **拒绝执行 + 落一条 status=failed 的 run 记录 + 不产出文件**（R005 #4）。
    这与 `/api/execute` 的既有行为**刻意不同**：那个端点的 422 不落记录（002C 冻结契约），
    任务端点则是"带审计的执行"，拒绝也要留痕。
    """
    started = time.perf_counter()
    task = state.get_task(task_id)
    if task is None:
        raise ApiError(404, "task_not_found", f"未知的 task_id：{task_id}（现有任务用 GET /api/tasks 查）")

    execution_id = state.new_id("x")
    spec_dict = task.get("spec") or {}
    frozen_range = _range_from_spec_dict(spec_dict)

    def reject(status: int, code: str, message: str, stage: int = 0) -> None:
        """拒绝执行：先落 failed run（留痕），再抛统一错误（R005 #4）。"""
        _record_failure(
            execution_id=execution_id,
            stage=stage,
            error=f"{code}：{message}",
            started=started,
            spec_dict=spec_dict,
            range_=frozen_range,
            file_id=task.get("file_id"),
            source_filename=task.get("source_filename"),
            data_sha256=task.get("data_sha256"),
            task_id=task_id,
        )
        raise ApiError(status, code, message)

    # ① 数据绑定核对（三方：任务绑定 / 上传记录 / 冻结快照）
    upload_record = state.get_upload(task.get("file_id") or "")
    if upload_record is None:
        reject(
            410, "upload_record_gone",
            f"任务绑定的上传记录已不存在（file_id={task.get('file_id')}，state/uploads.json 被清过？）："
            f"拒绝执行（没有可绑定的数据源，D11）",
        )
    if upload_record["sha256"] != task.get("data_sha256"):
        reject(
            422, "data_mismatch",
            f"上传的文件与任务建任务时绑定的数据不是同一份：\n"
            f"  任务绑定 SHA256：{task.get('data_sha256')}\n"
            f"  当前文件 SHA256：{upload_record['sha256']}\n"
            f"  拒绝用别的数据出报表（D10）",
        )
    if task.get("data_sha256") != loader.EXPECTED_SHA256:
        reject(
            422, "data_snapshot_mismatch",
            f"任务绑定的数据与**冻结数据快照**不是同一份（快照被换过？）：\n"
            f"  任务绑定 SHA256：{task.get('data_sha256')}\n"
            f"  快照 SHA256：{loader.EXPECTED_SHA256}\n"
            f"  拒绝算出不可信的数字（D10）：请重新上传当前数据快照并重建任务。",
        )

    # ② 还原冻结的 Spec（**不重新解析、不调 LLM** —— D9/D11；读回来必须仍然合法）
    try:
        spec = ReportSpec.from_dict(spec_dict)
    except Exception as exc:                       # noqa: BLE001 —— 落盘内容坏了，统一转成可读错误
        reject(500, "stored_spec_invalid", f"任务里冻结的 Spec 已无法还原（状态文件被改坏？）：{exc}")
    _validate_submitted_spec(spec, upload_record)   # 冻结的 Spec 仍须与指标目录/模板对得上

    # ③ 执行（与 /api/execute 共用同一段：算数 + 渲染 + 落记录 —— R005 #10）
    record = _execute_and_record(
        upload_record=upload_record,
        spec=spec,
        execution_id=execution_id,
        started=started,
        task_id=task_id,
    )
    state.mark_task_has_run(task_id)               # created → has_run（失败不改，见 state.py）
    return _run_response(record)


# ── 11. 该任务的执行历史 ────────────────────────────────────────────────
@app.get("/api/tasks/{task_id}/runs", summary="该任务的执行历史（成功与失败都在）", responses=_ERROR_RESPONSES)
def list_task_runs(
    task_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """该任务的全部执行记录（新的在前，含**失败**的那些 —— 失败也是审计信息，D10）。

    字段清单按 R005 required_change #6 钉死，且**成功/失败两种记录都有这些键**
    （失败的那几个值为 null，不隐藏字段 —— 前端不用写两套判空逻辑）。
    """
    if state.get_task(task_id) is None:
        raise ApiError(404, "task_not_found", f"未知的 task_id：{task_id}（现有任务用 GET /api/tasks 查）")
    records, total = state.list_task_runs(task_id, limit=limit, offset=offset)
    return {
        "count": len(records),
        "total": total,
        "limit": limit,
        "offset": offset,
        "task_id": task_id,
        "runs": [_shape_run(record) for record in records],
    }


# ════════════════════════════════════════════════════════════════════════
# 内部工具
# ════════════════════════════════════════════════════════════════════════
def _require_upload(file_id: str) -> dict:
    """file_id → 上传记录；不存在抛 404（各端点共用，保证同一句话术）。"""
    record = state.get_upload(file_id)
    if record is None:
        raise ApiError(
            404,
            "file_id_not_found",
            f"未知的 file_id：{file_id}（file_id 由 POST /api/upload 返回；服务重启后仍有效）",
        )
    return record


def _check_metrics(metrics: Sequence[str]) -> None:
    """指标名必须在指标目录里（口径先定义再算，D12 —— 不认识的口径宁可不做）。"""
    unknown = [name for name in metrics if name not in SUPPORTED_METRICS]
    if unknown:
        raise ApiError(
            422,
            "unknown_metric",
            (
                f"不支持的指标：{unknown}。当前支持：{list(SUPPORTED_METRICS)}"
                f"（受限于模板预留单元格 B4:B9 与 executor 的口径，见 app/api.py 的指标目录检查）"
            ),
        )


def _check_date_order(start: _dt.date, end: _dt.date) -> None:
    if start > end:
        raise ApiError(422, "reversed_range", f"起始日期晚于结束日期：{start} > {end}")


def _check_executable_file(upload_record: dict) -> None:
    """执行只支持 .xlsx/.xlsm（csv 能上传但不能执行 —— 002A 的 loader 没有 csv 取数路径）。"""
    suffix = Path(upload_record["stored_path"]).suffix.lower()
    if suffix not in EXECUTABLE_SUFFIXES:
        raise ApiError(
            422,
            "not_executable",
            (
                f"该文件是 {suffix} 格式，当前**执行**只支持 .xlsx/.xlsm："
                f"002A 的 loader 用 openpyxl 读 xlsx，没有 csv 取数路径（改它=改 002A 的计算逻辑，本 TASK 禁止）。"
                f"请上传 .xlsx（或把 csv 另存为 xlsx）。"
            ),
        )


def _check_snapshot_identity(upload_record: dict) -> None:
    """上传文件必须与冻结数据快照**字节相同**（理由见文件头）。

    这是 `/api/execute` 的既有行为（002C 冻结契约）：拒绝发生在计算之前，
    因此**不落执行记录、不产出文件**（tests/test_api.py 钉死了这一条）。
    任务端点的同一道闸门不在这里 —— 见 run_task 的 reject()（那边要留痕，R005 #4）。
    """
    snapshot_sha = loader.EXPECTED_SHA256
    if upload_record["sha256"] != snapshot_sha:
        raise ApiError(
            422,
            "data_snapshot_mismatch",
            (
                f"该文件与冻结数据快照不是同一份数据，拒绝用别的数据出报表（D10）：\n"
                f"  文件 SHA256：{upload_record['sha256']}\n"
                f"  快照 SHA256：{snapshot_sha}\n"
                f"  原因：002A 的 executor 只支持冻结快照（写死 loader.load_raw()），"
                f"支持任意文件取数需要改造 executor（Scope 外，已上报）。"
            ),
        )


def _build_spec(
    *,
    file_id: str,
    stored_path: str,
    start: _dt.date,
    end: _dt.date,
    metrics: Sequence[str],
    spec_id: str,
    name: str,
) -> ReportSpec:
    """把入参固化成一份 Report Spec（D11：执行必须绑定 Spec）。

    为什么要固化成 Spec 而不是直接调 executor：Spec 是**审计凭据** ——
    它把"这次用了哪些指标、什么口径（exclude）、写哪几个单元格、哪个模板"完整冻结下来，
    随执行记录一起落盘。以后同参数再跑，只要 Spec 一样，结果就必须一样（可重复）。

    spec_id：`/api/execute` 用 `adhoc-<file_id>`（002C 既有契约，测试钉死）；
    任务用 `task-<task_id>`（从 Spec 一眼看出它属于哪个任务）。
    """
    return ReportSpec(
        spec_id=spec_id,
        name=name,
        version=1,
        data_source=DataSourceRef(kind="excel", path=_relative_to_root(stored_path)),
        time_range=TimeRange(mode="absolute", start=start, end=end),
        metrics=tuple(
            MetricSpec(name=metric, dsl=MetricDSL(op=_METRIC_OPS[metric], field=metric))
            for metric in metrics
        ),
        output=OutputSpec(
            template=TEMPLATE_REL_PATH,
            sheet=renderer.DEFAULT_SHEET_NAME,
            cells={metric: renderer.DEFAULT_CELL_MAP[metric] for metric in metrics},
        ),
        created_at=_dt.datetime.now().astimezone(),
    )


def _validate_submitted_spec(spec: ReportSpec, upload_record: dict) -> None:
    """校验"提交上来的 / 任务里冻结的" Spec 在本 TASK 的能力范围内。

    为什么必须校验（而不能"照 Spec 跑就行"）：
        ① Spec 是 LLM/调度会写的东西（TASK-005/007），不能假设它对；
        ② 校验不过的执行会算出**口径不对劲**的报表 —— 那比报错危险得多（D10/D12）；
        ③ 早报错（建任务时）比晚报错（执行到一半）好。

    校验项与本 TASK 的边界一一对应：
        区间：只支持 mode='absolute'（相对时间留给 TASK-007）
        口径字段：time_field / semantics 必须与 app/engine/metrics.py 一致（D12 单一来源）
        指标：名字与算子必须在指标目录里（D13 受限 DSL）
        单元格：必须正好是模板预留的数据格（本 TASK 不支持任意模板/任意单元格 → 否则会丢样式）
        数据源：必须指向本次 file_id 对应的那个上传文件（防止"Spec 说一套、file_id 说另一套"）
    """
    if spec.time_range.mode != "absolute":
        raise ApiError(
            422,
            "relative_time_not_implemented",
            f"Spec 的 time_range.mode={spec.time_range.mode!r} 是相对区间，当前只支持 mode='absolute'"
            f"（相对区间的解析属 TASK-007）",
        )
    if spec.time_range.time_field != engine_metrics.TIME_FIELD:
        raise ApiError(
            422,
            "time_field_mismatch",
            f"Spec 的时间字段是 {spec.time_range.time_field!r}，但引擎的口径字段是 "
            f"{engine_metrics.TIME_FIELD!r}（口径单一来源，D12：见 app/engine/metrics.py）",
        )
    if spec.time_range.semantics != engine_metrics.RANGE_SEMANTICS:
        raise ApiError(
            422,
            "range_semantics_mismatch",
            f"Spec 的区间语义说明与引擎口径不一致（D12 单一来源）：\n"
            f"  Spec：{spec.time_range.semantics!r}\n"
            f"  引擎：{engine_metrics.RANGE_SEMANTICS!r}",
        )

    names = [metric.name for metric in spec.metrics]
    unknown = [name for name in names if name not in SUPPORTED_METRICS]
    if unknown:
        raise ApiError(
            422,
            "unknown_metric",
            f"Spec 里出现本引擎产不出的指标：{unknown}（当前支持：{list(SUPPORTED_METRICS)}）",
        )
    wrong_op = [
        f"{metric.name}(op={metric.dsl.op}, field={metric.dsl.field})"
        for metric in spec.metrics
        if _METRIC_OPS[metric.name] != metric.dsl.op or metric.dsl.field != metric.name
    ]
    if wrong_op:
        raise ApiError(
            422,
            "metric_dsl_mismatch",
            f"Spec 里指标的算子/字段与目录不一致：{wrong_op}\n"
            f"  约定：每个指标都是 op={_METRIC_OPS}、field=指标名（口径先定义再算，D12/D13）",
        )

    expected_cells = {name: renderer.DEFAULT_CELL_MAP[name] for name in names}
    if spec.output.cells != expected_cells:
        raise ApiError(
            422,
            "cells_mismatch",
            f"Spec 的单元格映射必须正好是模板预留的数据格：\n"
            f"  Spec：{spec.output.cells}\n"
            f"  期望：{expected_cells}\n"
            f"  原因：写到模板预留格之外会丢样式（D5 用 openpyxl 原地改的意义就在这里）；"
            f"任意模板/任意单元格不在本 TASK 范围。",
        )
    if spec.output.template != TEMPLATE_REL_PATH:
        raise ApiError(
            422,
            "template_not_supported",
            f"Spec 的模板是 {spec.output.template!r}，本 TASK 只支持 {TEMPLATE_REL_PATH!r}"
            f"（任意模板不在范围内）",
        )
    if spec.output.sheet != renderer.DEFAULT_SHEET_NAME:
        raise ApiError(
            422,
            "sheet_mismatch",
            f"Spec 的 sheet 是 {spec.output.sheet!r}，模板里的报表 sheet 是 {renderer.DEFAULT_SHEET_NAME!r}",
        )

    submitted_path = Path(spec.data_source.path or "")
    if not submitted_path.is_absolute():
        submitted_path = PROJECT_ROOT / submitted_path
    try:
        same_file = submitted_path.resolve() == Path(upload_record["stored_path"]).resolve()
    except OSError:
        same_file = False
    if not same_file:
        raise ApiError(
            422,
            "data_source_mismatch",
            f"Spec 的数据源指向 {spec.data_source.path!r}，但 file_id 指向的是 "
            f"{_relative_to_root(upload_record['stored_path'])!r} —— 一个任务只能绑定一份数据（D11）",
        )


def _resolve_time_range(spec: ReportSpec) -> tuple[dict, str | None]:
    """Spec 的时间区间 → (resolved_range, base_date)（R005 required_change #2）。

    绝对模式：resolved_range = {start, end}；base_date = None（没有"基准日"这回事）
    相对模式：002D **不解析** → 明确报错（不知道"上周"是哪一周就不算，D10）
    为什么把 base_date 也记下来：将来 TASK-007 支持相对时间后，
    "当时按哪个基准日解析成这个绝对区间"必须可复现 —— 否则同一份 Spec 会算出不同结果。
    """
    if spec.time_range.mode != "absolute":
        raise ApiError(
            422,
            "relative_time_not_implemented",
            f"时间区间 mode={spec.time_range.mode!r} 的相对解析属 TASK-007，当前只支持 mode='absolute'",
        )
    start, end = spec.time_range.require_absolute()
    return {"start": str(start), "end": str(end)}, None


def _range_from_spec_dict(spec_dict: dict) -> dict:
    """从**落盘的** Spec dict 里取区间快照（失败记录用 —— 此时不保证 Spec 还能还原成对象）。"""
    time_range = spec_dict.get("time_range") or {}
    return {"start": time_range.get("start"), "end": time_range.get("end")}


def _execute_and_record(
    *,
    upload_record: dict,
    spec: ReportSpec,
    execution_id: str,
    started: float,
    task_id: str | None = None,
) -> dict:
    """③ 算数 → ④ 渲染 → ⑤ 落执行记录（成功）。

    **`/api/execute` 与 `POST /api/tasks/{task_id}/run` 共用这一段**（R005 required_change #10）：
    两处各写一遍的话，将来只有一处会被改到，另一处静默漂移 —— 而"口径漂移"正是本项目最怕的事
    （D12 的由来就是评审指出"语义正确但口径错误"才是最大风险）。

    失败语义（与 002C 一致，未变）：任何一步失败都**不产出文件**（D10），
    并落一条 status=failed 的执行记录，然后把错误抛成统一的 ApiError。
    """
    resolved_range, base_date = _resolve_time_range(spec)
    start_text, end_text = resolved_range["start"], resolved_range["end"]
    range_ = {"start": start_text, "end": end_text}
    spec_dict = spec.to_dict()

    def failed(stage: int, error: str) -> None:
        _record_failure(
            execution_id=execution_id,
            stage=stage,
            error=error,
            started=started,
            spec_dict=spec_dict,
            range_=range_,
            file_id=upload_record["file_id"],
            source_filename=upload_record["filename"],
            data_sha256=upload_record["sha256"],
            task_id=task_id,
            resolved_range=resolved_range,
            base_date=base_date,
        )

    # ③ 算数（全精度；口径由 Spec 固化的排除规则决定 —— 与 002A/002B 同一套）
    start_date, end_date = spec.time_range.require_absolute()
    try:
        result = executor.compute_sales_amount(start_date, end_date, **renderer.executor_kwargs(spec))
        compute_seconds = float(result["seconds"])
    except loader.DataSourceError as exc:
        failed(1, f"数据源错误：{exc}")
        raise ApiError(503, "data_source_unavailable", f"数据源不可用，未产出文件（D10）：{exc}") from exc
    except ValueError as exc:
        failed(1, f"入参错误：{exc}")
        raise ApiError(422, "invalid_input", f"入参不合法：{exc}") from exc

    # ④ 渲染（写进模板副本 → 读回自检；自检不过 renderer 会删掉坏文件并抛 RenderError）
    try:
        values = renderer.values_from_result(spec, result)
        output_path = state.output_dir() / f"{execution_id}.xlsx"
        rendered = renderer.render_report(spec, values, output_path=output_path)
    except renderer.RenderError as exc:
        failed(2, f"渲染失败：{exc}")
        raise ApiError(
            500,
            "render_failed",
            f"渲染失败，未产出文件（D10）：{exc}（已记录失败执行 {execution_id}）",
        ) from exc

    # ⑤ 落执行记录（D11：spec / 数据哈希 / 代码版本 / execution_id / 校验结果都要绑定）
    seconds = time.perf_counter() - started
    record = {
        "execution_id": execution_id,
        "status": "success",
        "task_id": task_id,                                # 任务执行才有；ad-hoc 为 None（追加字段）
        "file_id": upload_record["file_id"],
        "source_filename": upload_record["filename"],
        "data_sha256": upload_record["sha256"],
        "snapshot_sha256": loader.EXPECTED_SHA256,
        "data_snapshot_match": True,
        "spec": spec_dict,
        "spec_id": spec.spec_id,
        "spec_version": spec.version,
        "range": range_,
        "resolved_range": resolved_range,                  # 实际算的绝对区间（R005 #2）
        "base_date": base_date,                            # 相对模式的基准日；绝对模式 None（R005 #2）
        "metrics": {name: _json_number(value) for name, value in values.items()},
        "amount": result["amount"],                        # 全精度（审计用，D17-2）
        "amount_display": round(float(result["amount"]), 2),   # 展示口径 2 位小数（D17-2）
        "rows_in_range": result["rows_in_range"],
        "rows_valid": result["rows_valid"],
        "rows_excluded": result["rows_excluded"],
        "excluded_amount": result["excluded_amount"],
        "validations": result["validations"],              # 校验结果（含 6 项 checks）
        "excel_path": rendered["output_path"],
        "excel_rel_path": _relative_output_path(rendered["output_path"]),
        "excel_size_bytes": rendered["size_bytes"],
        "template": rendered["template"],              # ← 绝对路径（002C 起的字段，仅内部/旧端点用）
        "template_rel": spec.output.template,          # ← Spec 里冻结的相对路径（对外用，R005 #7）
        "sheet": rendered["sheet"],
        "render_verification": rendered["verification"],   # 002C 起的字段名（冻结）
        "verification": rendered["verification"],          # R005 #6 钉的字段名（同一个值）
        "code_version": state.code_version(),
        "compute_seconds": round(compute_seconds, 3),
        "render_seconds": round(float(rendered["seconds"]), 3),
        "seconds": round(seconds, 3),
    }
    # 用落盘函数的返回值（它会 setdefault("created_at") —— 响应里要带这个时间）
    return state.record_execution(record)


def _record_failure(
    *,
    execution_id: str,
    stage: int,
    error: str,
    started: float,
    spec_dict: dict,
    range_: dict,
    file_id: str | None = None,
    source_filename: str | None = None,
    data_sha256: str | None = None,
    task_id: str | None = None,
    resolved_range: dict | None = None,
    base_date: str | None = None,
) -> None:
    """失败也落一条执行记录（D10 要求"记录错误"；只有记录才能事后说明"当时为什么没出报表"）。

    stage 取值：0 = 执行前的数据/绑定核对就拒绝了，1 = 计算阶段，2 = 渲染阶段。
    字段与成功记录**对齐**（同样的键，失败的那几个是 None）—— 前端/审计读同一条路径。
    """
    state.record_execution(
        {
            "execution_id": execution_id,
            "status": "failed",
            "stage": stage,
            "error": error,
            "task_id": task_id,
            "file_id": file_id,
            "source_filename": source_filename,
            "data_sha256": data_sha256,
            "snapshot_sha256": loader.EXPECTED_SHA256,
            "spec": spec_dict,
            "spec_id": spec_dict.get("spec_id"),
            "spec_version": spec_dict.get("version"),
            "range": range_,
            "resolved_range": resolved_range if resolved_range is not None else range_,
            "base_date": base_date,
            "metrics": {},
            "amount": None,
            "rows_in_range": None,
            "rows_valid": None,
            "rows_excluded": None,
            "excel_path": None,                # 失败必须没有产出（D10）
            "excel_rel_path": None,
            "excel_size_bytes": None,
            "verification": None,
            "render_verification": None,
            "code_version": state.code_version(),
            "seconds": round(time.perf_counter() - started, 3),
        }
    )


def _execute_response(record: dict) -> dict:
    """`/api/execute` 的响应体（**002C 冻结契约**：字段名/含义一个都没改）。

    注意这里仍然给 `excel_path`（服务器绝对路径）—— 那是 002C 的既有契约，R005 #10 要求不得改动。
    新端点（任务 run）按 R005 #7 只给相对路径 + download_url。
    """
    return {
        "execution_id": record["execution_id"],
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
        "download_url": f"/api/download/{record['execution_id']}",
        "template": record["template"],
        "sheet": record["sheet"],
        "file_id": record["file_id"],
        "data_sha256": record["data_sha256"],
        "code_version": record["code_version"],
        "compute_seconds": record["compute_seconds"],
        "render_seconds": record["render_seconds"],
        "seconds": record["seconds"],
        "verification": record["verification"],    # 读回自检（全过才会走到这里）
    }


def _run_response(record: dict) -> dict:
    """`POST /api/tasks/{task_id}/run` 的响应体。

    R005 required_change #7：**不给服务器绝对路径**（excel_path 只留在 state/*.json 与日志里），
    对外只给 `excel_rel_path` + `download_url`（相对 URL，由 `GET /api/download/{execution_id}` 承载
    —— FileResponse，返回真实文件）。
    """
    return {
        "task_id": record["task_id"],
        "execution_id": record["execution_id"],
        "status": record["status"],
        "amount": record["amount_display"],
        "amount_full": record["amount"],
        "rows_in_range": record["rows_in_range"],
        "rows_valid": record["rows_valid"],
        "rows_excluded": record["rows_excluded"],
        "range": record["range"],
        "resolved_range": record["resolved_range"],
        "metrics": record["metrics"],
        "spec_id": record["spec_id"],
        "spec_version": record["spec_version"],
        "file_id": record["file_id"],
        "data_sha256": record["data_sha256"],
        "excel_rel_path": record["excel_rel_path"],
        "excel_size_bytes": record["excel_size_bytes"],
        "download_url": f"/api/download/{record['execution_id']}",   # ← 相对 URL（见 docstring）
        "template": record["template_rel"],           # ← 相对路径（不是服务器绝对路径，R005 #7）
        "sheet": record["sheet"],
        "code_version": record["code_version"],
        "compute_seconds": record["compute_seconds"],
        "render_seconds": record["render_seconds"],
        "seconds": record["seconds"],
        "verification": record["verification"],
        "created_at": record["created_at"],
    }


def _task_spec_summary(spec_dict: dict) -> dict:
    """任务的 Spec 摘要（列表用；详情端点给完整 Spec）。"""
    time_range = spec_dict.get("time_range") or {}
    output = spec_dict.get("output") or {}
    return {
        "mode": time_range.get("mode"),
        "start": time_range.get("start"),
        "end": time_range.get("end"),
        "tz": time_range.get("tz"),
        "metrics": [metric.get("name") for metric in spec_dict.get("metrics") or []],
        "cells": output.get("cells"),
        "template": output.get("template"),
        "sheet": output.get("sheet"),
    }


def _task_summary(record: dict, stats: dict) -> dict:
    """任务列表里的一条（不含完整 Spec —— 列表别塞大对象）。"""
    return {
        "task_id": record["task_id"],
        "name": record["name"],
        "status": record["status"],
        "file_id": record["file_id"],
        "source_filename": record["source_filename"],
        "data_sha256": record["data_sha256"],
        "data_snapshot_match": record.get("data_snapshot_match"),
        "spec_id": (record.get("spec") or {}).get("spec_id"),
        "spec_version": (record.get("spec") or {}).get("version"),
        "spec_summary": _task_spec_summary(record.get("spec") or {}),
        "run_count": stats.get("run_count", 0),
        "last_run_at": stats.get("last_run_at"),
        "last_run_status": stats.get("last_run_status"),
        "created_at": record["created_at"],
        "updated_at": record.get("updated_at"),
    }


def _task_detail(record: dict, stats: dict, warnings: list[str] | None = None) -> dict:
    """单个任务（列表字段 + **完整冻结 Spec**）—— 前端区块③的数据源。"""
    detail = _task_summary(record, stats)
    detail.update(
        {
            "spec": record.get("spec"),                       # ← 完整 Report Spec（后端返回的，不是前端写死的）
            "data_snapshot_match": record.get("data_snapshot_match"),
            "runs_url": f"/api/tasks/{record['task_id']}/runs",
            "run_url": f"/api/tasks/{record['task_id']}/run",
            "schema_version": state.SCHEMA_VERSION,
        }
    )
    if warnings:
        detail["warnings"] = warnings
    return detail


def _shape_run(record: dict) -> dict:
    """一条 run 记录 → 对外形状（R005 required_change #6 钉死的字段清单）。

    成功与失败都走这里，**所有键恒定存在**（失败的那些值为 null）——
    前端不用为"失败记录少几个字段"写两套取值逻辑。
    `output_rel_path` 对应落盘记录里的 `excel_rel_path`（002C 起的字段名，冻结不改）。
    """
    success = record.get("status") == "success"
    return {
        "run_id": record["execution_id"],
        "execution_id": record["execution_id"],
        "task_id": record.get("task_id"),
        "status": record.get("status"),
        "created_at": record.get("created_at"),
        "spec_id": record.get("spec_id"),
        "spec_version": record.get("spec_version"),
        "time_range": (record.get("spec") or {}).get("time_range"),   # 冻结的区间声明
        "resolved_range": record.get("resolved_range"),               # 实际计算的绝对区间
        "base_date": record.get("base_date"),
        "data_sha256": record.get("data_sha256"),
        "code_version": record.get("code_version"),
        "verification": record.get("verification"),
        "seconds": record.get("seconds"),
        "error": record.get("error"),
        "stage": record.get("stage"),
        "output_rel_path": record.get("excel_rel_path"),              # 失败恒为 null
        "download_url": f"/api/download/{record['execution_id']}" if success else None,
        "amount": record.get("amount_display"),
        "amount_full": record.get("amount"),
        "rows_in_range": record.get("rows_in_range"),
        "rows_valid": record.get("rows_valid"),
        "rows_excluded": record.get("rows_excluded"),
    }


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
    """转成相对项目根的路径（能转就转，不能转就原样返回绝对路径）。

    ⚠️ 只用于**内部**（Spec 的数据源路径等审计信息）。**对外响应**用 `_relative_output_path()` ——
    绝对路径不该出给客户端（R005 required_change #7）。
    """
    try:
        return Path(path).resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def _relative_output_path(path: str | Path) -> str:
    """产出文件的相对路径（**对外**用；保证永远不是绝对路径 —— R005 required_change #7）。

    能相对项目根就相对项目根（默认产出目录 `outputs/` 就是这种情况 → "outputs/x_xxx.xlsx"）；
    产出目录被指到项目外（测试用临时目录、或自定义 SRA_OUTPUT_DIR）时**退化成文件名** ——
    宁可少给一层目录信息，也不把服务器绝对路径漏给客户端。
    客户端真正该用的是 `download_url`（由 GET /api/download/{execution_id} 承载，返回真实文件）。
    """
    resolved = Path(path)
    try:
        return resolved.resolve().relative_to(PROJECT_ROOT).as_posix()
    except (ValueError, OSError):
        return resolved.name


# ════════════════════════════════════════════════════════════════════════
# 文档输入（TASK-003：Word/PDF）—— 走**新路径** `/api/documents*`
# ════════════════════════════════════════════════════════════════════════
# 上面 11 个端点的语义一个字没改（Legacy Contract）。文档能力整个装在
# app/api_documents.py 里，这里只把它挂上来 —— 位置必须在 mount("/") **之前**，
# 否则 /api/documents* 会被静态目录吃掉（原因见下面那段注释）。
app.include_router(api_documents.router)


# ════════════════════════════════════════════════════════════════════════
# 自然语言问答（TASK-004）—— 走**新路径** `/api/chat*` `/api/conversations*`
# ════════════════════════════════════════════════════════════════════════
# 同样一个字没改既有端点：LLM 只做"解析意图 + 组织语言"，数字全部由 app/ai/tools.py
# 走既有 metrics/executor 确定性算出，`answer.py` 的数字核对闸门负责挡住 LLM 自己造的数。
# 位置同样必须在 mount("/") **之前**。
app.include_router(api_chat.router)


# ════════════════════════════════════════════════════════════════════════
# 数据源与业务表（STEP A）—— 走**新路径** `/api/datasets*` `/api/tables*`
# ════════════════════════════════════════════════════════════════════════
# 既有端点（含冻结的 /api/upload）一个字没改：数据源登记、业务表的分页/排序/筛选、
# 导出（xlsx/csv）整个装在 app/api_datasets.py 里。位置同样必须在 mount("/") **之前**。
app.include_router(api_datasets.router)


# ════════════════════════════════════════════════════════════════════════
# 本地账号（注册 / 登录）—— 走**新路径** `/api/auth/*`
# ════════════════════════════════════════════════════════════════════════
# 同样一个字没改既有端点：注册、登录校验、注册页查重整个装在 app/api_auth.py 里，
# 密码只以"不可还原的校验值"落 state/accounts.json（实现与边界见 app/accounts.py）。
# 位置同样必须在 mount("/") **之前**。
app.include_router(api_auth.router)


# ════════════════════════════════════════════════════════════════════════
# 静态前端挂载（TASK-004A）—— 本文件**唯一**为前端加的东西
# ════════════════════════════════════════════════════════════════════════
# 位置说明（很重要）：这段**必须在文件最末尾**。Starlette 按注册顺序匹配路由，
# `mount("/")` 一旦写在前面，后面的 /api/* 就全被静态目录吃掉了 —— 所以它只能放最后。
# 它**不改任何既有 /api/* 路由**（004A 指令：仅加静态挂载），只把 web/ 目录按 URL 暴露出去：
#     GET /             → web/index.html（html=True）
#     GET /app.js 等    → web/ 下的静态文件
#     GET /api/*        → 仍然命中上面那些真实路由（它们在 mount 之前注册，优先级更高）
# web/ 不存在时**跳过挂载**（后端要能脱离前端单独跑；测试里也可只测 API）。
_FRONTEND_DIR = PROJECT_ROOT / "web"
if _FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
