"""registry.py · 数据源登记表（STEP A）：一个数据源 = 一个 **Dataset**。

════════════════════════════════════════════════════════════════════════
【为什么要有它（评审的硬条件）】
════════════════════════════════════════════════════════════════════════
一个数字必须答得出来"它基于哪个数据源、哪套口径、哪次导入"。所以每个 Dataset 记：

    dataset_id / source_file / file_hash / row_count / column_count / columns /
    date_range / imported_at / updated_at / metric_definition / analysis_enabled

**分层（前端展示规范）**：
  · 业务层：名称、行数、时间范围、更新时间、状态（"可分析" / "已登记 · 分析未开通"）
  · 审计层：`file_hash` / `source_file_id` / `stored_path` —— 只在后端与 `audit` 字段里流转，
    **前端一个都不渲染**（页面不出现哈希、文件编号、路径）。

════════════════════════════════════════════════════════════════════════
【当前只有内置数据源可以分析（这条必须说清楚，不许假装）】
════════════════════════════════════════════════════════════════════════
分析引擎（`app/engine/*` 与 `app/ai/tools.py`）当前**绑定在冻结的内置快照**上
（`data/Online Retail.xlsx`，带 SHA256 校验）。把引擎改成"按数据集取数"属于
**数据接入后端深化**（评审把它排在 TASK-011 之后）。所以：

  · 内置快照        → `analysis_enabled=True`，四张业务表都能查；
  · 导入的表格      → **登记为真实 Dataset**（真哈希、真行数、真时间范围、真字段映射），
                      但 `analysis_enabled=False`，状态写「已登记 · 分析未开通」——
                      **绝不让用户以为"导进来就能分析"**，也绝不伪造一次成功的分析。

导入的数据集引用的**仍是上传时落盘的那份字节**（`stored_path` / `file_hash` 指过去），
不重复存一份文件 —— 这与"文档输入 / 数据集输入共用底层文件存储、但业务语义分开"一致。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
from pathlib import Path
from typing import Any

import pandas as pd

from app import state
from app.engine import loader
from app.engine import metrics as engine_metrics
from app.repositories import json_store

# 内置数据源（冻结快照）的 dataset_id：全项目引用"销售数据"时都用它
BUILTIN_DATASET_ID = "ds_sales"
BUILTIN_DATASET_NAME = "销售数据"

# 数据集状态（机器可读 + 人话标签，两处一起给，避免前端自己去翻译内部状态）
STATUS_READY = "ready"
STATUS_REGISTERED = "registered"
STATUS_LABELS: dict[str, str] = {
    STATUS_READY: "可分析",
    STATUS_REGISTERED: "已登记 · 分析未开通",
}

# 口径（业务语言；内部编号只留在代码里，不上接口、不上界面）
METRIC_DEFINITION_BUILTIN: dict[str, Any] = {
    "name": "销售口径",
    "rule_text": "含首尾全天；排除取消单（单号以 C 开头）、数量 ≤ 0、单价 ≤ 0 的行；"
                 "客户号为空的成交仍计入销售额，但不计入客户数。",
    "metrics": [
        {"key": "sales_amount", "label": "销售额"},
        {"key": "order_count", "label": "订单数"},
        {"key": "customer_count", "label": "客户数"},
        {"key": "return_definition", "label": "退货（数量为负 / 取消单两个口径分开）"},
    ],
}
METRIC_DEFINITION_PENDING: dict[str, Any] = {
    "name": "待绑定",
    "rule_text": "导入数据源的分析口径会在「分析未开通」时一并绑定；当前不参与任何计算。",
    "metrics": [],
}

# 分析引擎当前只认内置快照 —— 这句话会由接口原样带给前端（业务语言，不藏）
ANALYSIS_NOTE_IMPORTED = (
    "该数据源已登记。当前分析能力绑定在「销售数据」上，"
    "新数据源的分析将在后续版本开通 —— 现在不会用它的数据算任何数字。"
)

# 导入向导里"哪些字段是分析必需的"（8 个字段对应内置快照的 8 列）
REQUIRED_FIELDS: tuple[str, ...] = (
    "InvoiceNo", "StockCode", "Description", "Quantity",
    "InvoiceDate", "UnitPrice", "CustomerID", "Country",
)
FIELD_LABELS: dict[str, str] = {
    "InvoiceNo": "订单号",
    "StockCode": "商品编码",
    "Description": "商品名称",
    "Quantity": "数量",
    "InvoiceDate": "下单时间",
    "UnitPrice": "单价",
    "CustomerID": "客户号",
    "Country": "国家",
}
# 表头是英文时自动猜一次映射（只做**猜测**，用户在界面上可以改）
_FIELD_GUESSES: dict[str, tuple[str, ...]] = {
    "InvoiceNo": ("invoiceno", "invoice", "订单号", "发票号", "单号"),
    "StockCode": ("stockcode", "stock", "商品编码", "货号", "sku"),
    "Description": ("description", "desc", "商品名称", "品名", "描述"),
    "Quantity": ("quantity", "qty", "数量"),
    "InvoiceDate": ("invoicedate", "date", "下单时间", "日期", "时间"),
    "UnitPrice": ("unitprice", "price", "单价"),
    "CustomerID": ("customerid", "customer", "客户号", "客户"),
    "Country": ("country", "国家"),
}

# 允许导入的后缀（**数据集输入**：结构化表格；文档类走 Document 管道，两边语义分开）
DATASET_SUFFIXES: tuple[str, ...] = (".xlsx", ".xlsm", ".csv")
CSV_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "gbk", "latin-1")
CSV_ENCODING_LABELS: dict[str, str] = {
    "utf-8-sig": "UTF-8",
    "gbk": "GBK",
    "latin-1": "Latin-1（兜底编码，中文可能显示为乱码）",
}

# 预览行数上限（**前 N 行**：导入前绝不把整表读进浏览器）
PREVIEW_ROWS = 20


class DatasetError(RuntimeError):
    """数据集层可预期的错误（带机器可读 code + 给人看的话）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ════════════════════════════════════════════════════════════════════════
# 内置数据源（冻结快照）—— 元数据**从文件与数据里现算**，不写死
# ════════════════════════════════════════════════════════════════════════
def _file_updated_at(path: Path) -> str:
    """文件最后修改时间（业务上就是"这份数据什么时候更新的"）。

    为什么不用哈希当"版本"给用户看：哈希是审计层的东西，业务层只需要"这份数据是什么时候的"。
    """
    stamp = _dt.datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    return stamp.isoformat(timespec="seconds")


def builtin_dataset() -> dict[str, Any]:
    """内置销售数据源的元数据（行数/列数/时间范围/更新时间都从真实文件与数据里算）。"""
    path = Path(loader.DEFAULT_DATA_PATH)
    frame = loader.load_raw()
    stamps = pd.to_datetime(frame[engine_metrics.TIME_FIELD])
    missing = [column for column in REQUIRED_FIELDS if column not in frame.columns]
    return {
        "dataset_id": BUILTIN_DATASET_ID,
        "name": BUILTIN_DATASET_NAME,
        "kind": "builtin",
        "source_file": path.name,
        "row_count": int(len(frame)),
        "column_count": int(frame.shape[1]),
        "columns": [str(column) for column in frame.columns],
        "date_range": {
            "start": stamps.min().date().isoformat(),
            "end": stamps.max().date().isoformat(),
        },
        "imported_at": _file_updated_at(path),
        "updated_at": _file_updated_at(path),
        "status": STATUS_READY,
        "status_label": STATUS_LABELS[STATUS_READY],
        "analysis_enabled": True,
        "analysis_note": "内置销售数据源：四张业务表都基于它计算。",
        "metric_definition": METRIC_DEFINITION_BUILTIN,
        "required_fields_present": len(missing) == 0,
        "missing_fields": missing,
        # 内置快照的字段就是分析要的那 8 列（字段映射是恒等映射，不存在"猜"）
        "field_map": {field: field for field in REQUIRED_FIELDS},
        "required_fields": [
            {"key": field, "label": FIELD_LABELS.get(field, field), "required": True, "mapped_to": field}
            for field in REQUIRED_FIELDS
        ],
        "mapping_complete": not missing,
        "read_note": "内置快照（Excel）",
        # ── 审计层（接口会给，前端不渲染）────────────────────────────────
        "audit": {
            "stored_path": str(path),
            "file_hash": loader.sha256_of_file(path),
            "hash_algo": "sha256",
        },
    }


# ════════════════════════════════════════════════════════════════════════
# 数据集列表 / 取用
# ════════════════════════════════════════════════════════════════════════
def list_datasets(limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    """全部数据源：内置的排最前，其余按导入时间新的在前（分页）。"""
    imported, total = state.list_datasets(limit=200, offset=0)
    everything = [builtin_dataset()] + list(imported)
    return everything[offset:offset + limit], total + 1


def get_dataset(dataset_id: str) -> dict[str, Any]:
    """按 id 取数据集；不存在 → DatasetError（由接口转 404）。"""
    if dataset_id == BUILTIN_DATASET_ID:
        return builtin_dataset()
    record = state.get_dataset(dataset_id)
    if record is None:
        raise DatasetError("dataset_not_found", f"没有这个数据源：{dataset_id}")
    return record


def require_analysis_dataset(dataset_id: str) -> dict[str, Any]:
    """取一个**可以分析**的数据集；导入的（尚未开通分析）在这里被明确拦下。

    为什么拦得这么硬：如果放过去，四张业务表就会拿内置快照的数字冒充"你的数据源"的结果 ——
    那是最坏的一种骗法（数字是真的，但答的是另一个数据源的问题）。
    """
    dataset = get_dataset(dataset_id)
    if not dataset.get("analysis_enabled"):
        raise DatasetError(
            "dataset_analysis_not_ready",
            f"「{dataset.get('name') or dataset_id}」已登记为数据源，但它的**分析能力尚未开通** —— "
            f"当前业务表格只基于「{BUILTIN_DATASET_NAME}」计算。"
            + (f"（{dataset.get('analysis_note')}）" if dataset.get("analysis_note") else ""),
        )
    return dataset


def dataset_summary(dataset: dict[str, Any]) -> dict[str, Any]:
    """给业务表响应里带的那一小块"这是哪份数据"（业务字段，无审计信息）。"""
    return {
        "dataset_id": dataset["dataset_id"],
        "name": dataset["name"],
        "updated_at": dataset["updated_at"],
        "date_range": dataset["date_range"],
        "row_count": dataset["row_count"],
        "status_label": dataset.get("status_label"),
        "analysis_enabled": bool(dataset.get("analysis_enabled")),
        "metric_definition": dataset.get("metric_definition") or {},
    }


# ════════════════════════════════════════════════════════════════════════
# 导入：把一份上传的表格**登记成数据集**（真哈希 / 真行数 / 真时间范围）
# ════════════════════════════════════════════════════════════════════════
def read_uploaded_table(
    stored_path: str | Path, *, sheet: str | None = None, nrows: int | None = None
) -> tuple[pd.DataFrame, str]:
    """读上传落盘的那份文件（xlsx/xlsm/csv）→ `(DataFrame, 说明)`。

    · Excel：可指定 sheet；不指定读第一个并**说明读的是哪个**（不静默）；
    · CSV：按 UTF-8 → GBK → Latin-1 依次尝试，并把**实际用的编码**报出来
      （编码猜错会读出乱码，用户有权知道我们是按哪种编码读的）。
    """
    path = Path(stored_path)
    suffix = path.suffix.lower()
    if suffix not in DATASET_SUFFIXES:
        raise DatasetError(
            "dataset_unsupported_type",
            f"数据源只接受 {list(DATASET_SUFFIXES)}，收到 {suffix or '(无后缀)'}；"
            f"Word/PDF/PPT 属于文档输入，请走文档入口。",
        )
    if suffix == ".csv":
        last_error: Exception | None = None
        for encoding in CSV_ENCODINGS:
            try:
                frame = pd.read_csv(path, encoding=encoding, nrows=nrows)
                return frame, f"CSV · 编码 {CSV_ENCODING_LABELS.get(encoding, encoding)}"
            except UnicodeDecodeError as exc:           # 编码不对 → 换下一个
                last_error = exc
                continue
            except Exception as exc:                    # noqa: BLE001 —— 解析失败要说人话
                raise DatasetError("dataset_parse_failed", f"CSV 解析失败：{exc}") from exc
        raise DatasetError(
            "dataset_encoding_unknown",
            f"这个 CSV 的编码认不出来（试过 {[CSV_ENCODING_LABELS.get(item, item) for item in CSV_ENCODINGS]}）。"
            f"请另存为 UTF-8 后重试。原始错误：{last_error}",
        )

    excel = pd.ExcelFile(path, engine="openpyxl")
    sheet_names = [str(name) for name in excel.sheet_names]
    chosen = str(sheet) if sheet else sheet_names[0]
    if chosen not in sheet_names:
        raise DatasetError(
            "dataset_sheet_not_found",
            f"工作簿里没有工作表 {chosen!r}（可选：{sheet_names}）",
        )
    frame = excel.parse(chosen, nrows=nrows)
    return frame, f"Excel · 工作表「{chosen}」（共 {len(sheet_names)} 个）"


def list_excel_sheets(stored_path: str | Path) -> list[str]:
    """列出工作簿里的工作表（非 Excel 返回空列表：CSV 没有 sheet 概念）。"""
    path = Path(stored_path)
    if path.suffix.lower() == ".csv":
        return []
    return [str(name) for name in pd.ExcelFile(path, engine="openpyxl").sheet_names]


def guess_mapping(columns: list[str]) -> dict[str, str]:
    """按表头猜一次字段映射（**只是猜**，用户在界面上可以改；猜不出来就留空）。"""
    mapping: dict[str, str] = {}
    normalized = {str(column).strip().lower().replace(" ", "").replace("_", ""): str(column)
                  for column in columns}
    for field, guesses in _FIELD_GUESSES.items():
        for guess in guesses:
            hit = normalized.get(guess)
            if hit is not None:
                mapping[field] = hit
                break
    return mapping


def inspect_upload(file_id: str) -> dict[str, Any]:
    """导入向导第 1 步：**只看不导入** —— 文件类型 / 工作表 / 前 N 行预览 / 字段映射猜测。

    刻意不落任何 Dataset：用户还没确认，系统就不该产生"一个新数据源"这种事实。
    """
    record = state.get_upload(file_id)
    if record is None:
        raise DatasetError("upload_not_found", f"没有这个上传记录：{file_id}（请先上传文件）")

    sheets = list_excel_sheets(record["stored_path"])
    sheet = sheets[0] if sheets else None
    frame, read_note = read_uploaded_table(record["stored_path"], sheet=sheet, nrows=PREVIEW_ROWS)
    columns = [str(column) for column in frame.columns]
    mapping = guess_mapping(columns)
    missing = [field for field in REQUIRED_FIELDS if field not in mapping]
    return {
        "file_id": record["file_id"],
        "filename": record["filename"],
        "size_bytes": record["size_bytes"],
        "rows": int(record["rows"]),
        "column_count": len(columns),
        "columns": columns,
        "sheets": sheets,
        "sheet": sheet,
        "read_note": read_note,
        "preview_rows": PREVIEW_ROWS,
        "preview": [
            {column: _cell(frame.iloc[index][column]) for column in columns}
            for index in range(min(len(frame), PREVIEW_ROWS))
        ],
        "field_map": mapping,
        "required_fields": [
            {"key": field, "label": FIELD_LABELS.get(field, field),
             "required": True, "mapped_to": mapping.get(field)}
            for field in REQUIRED_FIELDS
        ],
        "missing_fields": missing,
        "mapping_complete": not missing,
        "import_supported": True,          # 登记这一步是真支持的（见 register_import）
        "analysis_supported": False,       # 分析这一步**还没开通** —— 如实说
        "analysis_note": ANALYSIS_NOTE_IMPORTED,
    }


def _cell(value: Any) -> Any:
    """预览单元格：转成 JSON 能表达的形态（时间 → ISO 字符串，NaN → None）。"""
    if value is None or (isinstance(value, float) and value != value):
        return None
    if isinstance(value, (pd.Timestamp, _dt.datetime, _dt.date)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def register_import(
    file_id: str,
    *,
    name: str | None = None,
    sheet: str | None = None,
    field_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """导入向导最后一步：把上传的文件登记成一个 **Dataset**（真哈希 / 真行数 / 真时间范围）。

    它**不假装**分析已经可用：登记出来的数据源 `analysis_enabled=False`、状态写
    「已登记 · 分析未开通」，并带上说明。分析引擎按数据集取数属于后续 TASK。
    """
    record = state.get_upload(file_id)
    if record is None:
        raise DatasetError("upload_not_found", f"没有这个上传记录：{file_id}（请先上传文件）")

    sheets = list_excel_sheets(record["stored_path"])
    chosen_sheet = str(sheet) if sheet else (sheets[0] if sheets else None)
    frame, read_note = read_uploaded_table(record["stored_path"], sheet=chosen_sheet)
    columns = [str(column) for column in frame.columns]
    mapping = dict(field_map) if field_map else guess_mapping(columns)

    unknown = [field for field in mapping if field not in REQUIRED_FIELDS]
    if unknown:
        raise DatasetError(
            "dataset_unknown_field",
            f"字段映射里有不认识的目标字段：{sorted(unknown)}（可选：{list(REQUIRED_FIELDS)}）",
        )
    wrong_source = [source for source in mapping.values() if source not in columns]
    if wrong_source:
        raise DatasetError(
            "dataset_unknown_column",
            f"字段映射里引用了文件里没有的列：{sorted(wrong_source)}（文件列：{columns}）",
        )

    missing = [field for field in REQUIRED_FIELDS if field not in mapping]
    date_range = None
    date_column = mapping.get("InvoiceDate")
    if date_column:
        stamps = pd.to_datetime(frame[date_column], errors="coerce")
        if stamps.notna().any():
            date_range = {
                "start": stamps.min().date().isoformat(),
                "end": stamps.max().date().isoformat(),
            }

    dataset_id = state.new_id("ds")
    dataset: dict[str, Any] = {
        "dataset_id": dataset_id,
        "name": (name or "").strip() or Path(record["filename"]).stem or "未命名数据源",
        "kind": "imported",
        "source_file": record["filename"],
        "row_count": int(len(frame)),
        "column_count": len(columns),
        "columns": columns,
        "sheet": chosen_sheet,
        "read_note": read_note,
        "date_range": date_range,
        "imported_at": state.now_iso(),
        "updated_at": state.now_iso(),
        "status": STATUS_REGISTERED,
        "status_label": STATUS_LABELS[STATUS_REGISTERED],
        "analysis_enabled": False,
        "analysis_note": ANALYSIS_NOTE_IMPORTED,
        "metric_definition": (
            {**METRIC_DEFINITION_BUILTIN, "name": "销售口径（待开通后生效）"}
            if not missing else METRIC_DEFINITION_PENDING
        ),
        "field_map": mapping,
        "required_fields": [
            {"key": field, "label": FIELD_LABELS.get(field, field),
             "required": True, "mapped_to": mapping.get(field)}
            for field in REQUIRED_FIELDS
        ],
        "mapping_complete": not missing,
        "missing_fields": missing,
        # ── 审计层（接口会给，前端不渲染）────────────────────────────────
        "audit": {
            "source_file_id": record["file_id"],
            "stored_path": record["stored_path"],
            "file_hash": record["sha256"],
            "schema_hash": _schema_hash(columns, mapping),
            "hash_algo": "sha256",
        },
    }
    return state.record_dataset(dataset)


def _schema_hash(columns: list[str], mapping: dict[str, str]) -> str:
    """结构指纹（列名 + 字段映射）—— 审计用：同一个文件换一种映射就是另一套结构。"""
    payload = "|".join([*columns, "::", *[f"{key}={value}" for key, value in sorted(mapping.items())]])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "ANALYSIS_NOTE_IMPORTED",
    "BUILTIN_DATASET_ID",
    "BUILTIN_DATASET_NAME",
    "CSV_ENCODINGS",
    "DATASET_SUFFIXES",
    "DatasetError",
    "FIELD_LABELS",
    "METRIC_DEFINITION_BUILTIN",
    "PREVIEW_ROWS",
    "REQUIRED_FIELDS",
    "STATUS_LABELS",
    "STATUS_READY",
    "STATUS_REGISTERED",
    "builtin_dataset",
    "dataset_summary",
    "get_dataset",
    "guess_mapping",
    "inspect_upload",
    "list_datasets",
    "list_excel_sheets",
    "read_uploaded_table",
    "register_import",
    "require_analysis_dataset",
]
