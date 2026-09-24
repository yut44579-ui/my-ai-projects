"""queries.py · 业务表的**唯一**查询入口（STEP A）。

════════════════════════════════════════════════════════════════════════
【一句话：筛选 → 排序 → 分页 → 行，全在这里，前端只渲染】
════════════════════════════════════════════════════════════════════════
四张表：
    customers  客户表   客户号 / 销售额 / 订单数 / 购买次数 / 平均每单 / 首次购买 / 最后购买
    products   产品表   商品编码 / 名称 / 销售额 / 数量 / 订单数 / 退货数 / 退货数量
    sales      销售表   维度（日 / 周 / 国家）× 指标（销售额 / 订单数 / 客户数 / 客单价）
    raw        原始数据 内置数据集的原始行（分页 / 排序 / 搜索 / 时间筛选），**不套分析口径**

**唯一的取数函数** `fetch_table()`：分页请求传 page，导出传 `export=True`（要全部行）。
筛选、排序、口径、行内容完全同一段代码 —— 页面看到的和导出文件里的因此不可能不一致。

【口径不另起一套】
所有行都来自 `app/ai/tools.py` 的**公开包装**（`valid_rows` / `customer_table` /
`product_table` / `bucket_of` / `customer_activity` / `build_customer_scope`）——
与自然语言问答用的是同一套 D16 口径、同一个 executor。销售表的逐桶销售额/订单数
直接取自既有的销售趋势工具，本文件只**补**它没有的客户数与客单价两列。

【列定义由后端给（重要）】
响应里的 `columns`（key/label/对齐/格式）是后端产出的：前端照着渲染，不自己维护一份表头。
这样"页面上有哪几列、叫什么、怎么对齐"只有一处定义，改口径时不会漏掉前端。
"""

from __future__ import annotations

import datetime as _dt
import math
from typing import Any, Callable

import pandas as pd

from app.ai import tools
from app.datasets import registry
from app.engine import metrics as engine_metrics

# ── 表名（白名单：表外的一律拒绝）────────────────────────────────────────
TABLE_CUSTOMERS = "customers"
TABLE_PRODUCTS = "products"
TABLE_SALES = "sales"
TABLE_RAW = "raw"
TABLES: tuple[str, ...] = (TABLE_CUSTOMERS, TABLE_PRODUCTS, TABLE_SALES, TABLE_RAW)
TABLE_LABELS: dict[str, str] = {
    TABLE_CUSTOMERS: "客户表",
    TABLE_PRODUCTS: "产品表",
    TABLE_SALES: "销售表",
    TABLE_RAW: "原始数据",
}

# ── 分页 ────────────────────────────────────────────────────────────────
PAGE_SIZES: tuple[int, ...] = (10, 20, 50, 100, 200)
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500
# 单次导出上限：541,909 行原始数据一次性写 xlsx 会吃掉几个 GB 内存（openpyxl 正常模式），
# 所以导出到上限就**明确拒绝并告诉用户怎么办**，而不是悄悄截断（截断过的文件最伤人）。
MAX_EXPORT_ROWS = 100_000

# ── 销售表的维度 / 指标白名单 ───────────────────────────────────────────
SALES_DIMENSIONS: tuple[str, ...] = ("day", "week", "country")
SALES_DIMENSION_LABELS: dict[str, str] = {"day": "按日", "week": "按周", "country": "按国家"}
SALES_METRICS: tuple[str, ...] = ("sales_amount", "order_count", "customer_count", "avg_order_amount")
SALES_METRIC_LABELS: dict[str, str] = {
    "sales_amount": "销售额",
    "order_count": "订单数",
    "customer_count": "客户数",
    "avg_order_amount": "客单价",
}

_ORDER_ASC = "asc"
_ORDER_DESC = "desc"


class QueryError(ValueError):
    """查询层可预期的错误（机器可读 code + 给人看的一句话）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ════════════════════════════════════════════════════════════════════════
# 列定义（后端唯一出处）
# ════════════════════════════════════════════════════════════════════════
def _column(key: str, label: str, fmt: str, align: str = "left", note: str = "") -> dict[str, Any]:
    return {"key": key, "label": label, "format": fmt, "align": align, "note": note}


CUSTOMER_COLUMNS: tuple[dict[str, Any], ...] = (
    _column("customer_id", "客户号", "text"),
    _column("sales_amount", "销售额", "money", "right"),
    _column("order_count", "订单数", "int", "right",
            "与购买次数同义：本口径下都按**去重后的订单号**计（一个订单买 20 种商品算 1 单）"),
    _column("purchase_count", "购买次数", "int", "right", "去重订单号个数（不是数据行数）"),
    _column("avg_order_amount", "平均每单", "money", "right", "销售额 ÷ 订单数"),
    _column("first_purchase", "首次购买", "date"),
    _column("last_purchase", "最后购买", "date"),
)

PRODUCT_COLUMNS: tuple[dict[str, Any], ...] = (
    _column("stock_code", "商品编码", "text"),
    _column("description", "商品名称", "text"),
    _column("sales_amount", "销售额", "money", "right"),
    _column("quantity", "数量", "qty", "right"),
    _column("order_count", "订单数", "int", "right"),
    _column("return_rows", "退货数", "int", "right",
            "区间内「数量为负」或「取消单（单号以 C 开头）」的行数（去重后）"),
    _column("return_quantity", "退货数量", "qty", "right"),
)

SALES_COLUMNS: tuple[dict[str, Any], ...] = (
    _column("dimension_value", "期间", "text"),
    _column("sales_amount", "销售额", "money", "right"),
    _column("order_count", "订单数", "int", "right"),
    _column("customer_count", "客户数", "int", "right"),
    _column("avg_order_amount", "客单价", "money", "right"),
    _column("metric_value", "当前指标", "money", "right", "随上面选的指标变化"),
    _column("metric_share", "占比", "pct", "right", "该行在所选指标合计中的占比"),
    _column("period_note", "说明", "text", "left",
            "不完整的期间会写明「只统计了哪几天」—— 拿半截期间和整期比高低会得出错的结论"),
)

RAW_COLUMNS: tuple[dict[str, Any], ...] = (
    _column("row_no", "序号", "int", "right", "本次筛选结果内的行号（1 起）"),
    _column("InvoiceNo", "订单号", "text"),
    _column("StockCode", "商品编码", "text"),
    _column("Description", "商品名称", "text"),
    _column("Quantity", "数量", "int", "right"),
    _column("InvoiceDate", "下单时间", "datetime"),
    _column("UnitPrice", "单价", "money", "right"),
    _column("CustomerID", "客户号", "text"),
    _column("Country", "国家", "text"),
)

_TABLE_COLUMNS: dict[str, tuple[dict[str, Any], ...]] = {
    TABLE_CUSTOMERS: CUSTOMER_COLUMNS,
    TABLE_PRODUCTS: PRODUCT_COLUMNS,
    TABLE_SALES: SALES_COLUMNS,
    TABLE_RAW: RAW_COLUMNS,
}

# 可排序键（**只认这些**：别的键直接报错，不猜用户想排哪一列）
_SORT_KEYS: dict[str, tuple[str, ...]] = {
    TABLE_CUSTOMERS: ("customer_id", "sales_amount", "order_count", "purchase_count",
                      "rows", "avg_order_amount", "first_purchase", "last_purchase"),
    TABLE_PRODUCTS: ("stock_code", "description", "sales_amount", "quantity",
                     "order_count", "return_rows", "return_quantity"),
    TABLE_SALES: ("dimension_value", "sales_amount", "order_count", "customer_count",
                  "avg_order_amount", "metric_value", "metric_share"),
    TABLE_RAW: ("row_no", "InvoiceNo", "StockCode", "Description", "Quantity",
                "InvoiceDate", "UnitPrice", "CustomerID", "Country"),
}
_DEFAULT_SORT: dict[str, str] = {
    TABLE_CUSTOMERS: "sales_amount",
    TABLE_PRODUCTS: "sales_amount",
    TABLE_SALES: "metric_value",
    TABLE_RAW: "row_no",
}

# 搜索作用的列（业务表按"能认出来的业务标识"搜；原始数据搜几个文本列）
_SEARCH_COLUMNS: dict[str, tuple[str, ...]] = {
    TABLE_CUSTOMERS: ("customer_id",),
    TABLE_PRODUCTS: ("stock_code", "description"),
    TABLE_SALES: ("dimension_value",),
    TABLE_RAW: ("InvoiceNo", "StockCode", "Description", "CustomerID", "Country"),
}


# ════════════════════════════════════════════════════════════════════════
# 派生表缓存：结构性数据在进程内只算一次
#
# 为什么可以缓存：数据源是**冻结快照**（文件哈希校验过，运行期不会变），
# 同一区间算出来的客户表/产品表在进程内是确定的。缓存只影响"算几次"，不影响"算出什么"。
# 键里带 dataset_id 与区间，换了区间自动重算；容量很小（4 个槽位），不担心内存。
# ════════════════════════════════════════════════════════════════════════
_TABLE_CACHE: dict[tuple[str, str, str], Any] = {}
_CACHE_SLOTS = 4


def reset_cache() -> None:
    """清掉派生表缓存（测试/换数据集时用）。"""
    _TABLE_CACHE.clear()


def _cached(key: tuple[str, str, str], build: Callable[[], Any]) -> Any:
    if key in _TABLE_CACHE:
        return _TABLE_CACHE[key]
    value = build()
    if len(_TABLE_CACHE) >= _CACHE_SLOTS:
        _TABLE_CACHE.clear()
    _TABLE_CACHE[key] = value
    return value


# ════════════════════════════════════════════════════════════════════════
# 查询参数的规范化与校验
# ════════════════════════════════════════════════════════════════════════
def resolve_window(
    dataset: dict[str, Any], start: str | None, end: str | None
) -> tuple[_dt.date, _dt.date, list[str]]:
    """把 start/end 规范化成闭区间（含首尾全天），并给出**如实**的说明。

    没给区间就用数据集自身的覆盖范围（不是"今天"，数据集是历史数据）。
    给了区间但整个落在数据之外 → **不缩成数据边界**（那答的是另一个问题），
    原样返回 + 一条"这个区间里没有记录"的说明（与问答链路同一条约定）。
    """
    data_start = _dt.date.fromisoformat(dataset["date_range"]["start"])
    data_end = _dt.date.fromisoformat(dataset["date_range"]["end"])
    if start is None and end is None:
        return data_start, data_end, [f"未指定区间，按数据源覆盖范围 {data_start} ~ {data_end} 统计。"]
    try:
        low, high = engine_metrics.normalize_day_range(start or data_start, end or data_end)
    except (ValueError, TypeError) as exc:
        raise QueryError("invalid_range", f"时间区间不合法：{exc}") from exc
    notes: list[str] = []
    if high < data_start or low > data_end:
        notes.append(f"所问区间（{low} ~ {high}）不在数据源范围内（{data_start} ~ {data_end}），因此没有记录。")
    else:
        if low < data_start:
            notes.append(f"区间起点早于数据源起点，按 {data_start} 起算。")
            low = data_start
        if high > data_end:
            notes.append(f"区间终点晚于数据源终点，按 {data_end} 止算。")
            high = data_end
    return low, high, notes


def _clean_search(search: str | None) -> str:
    return (search or "").strip()


def _page_args(page: int | None, page_size: int | None) -> tuple[int, int]:
    size = int(page_size or DEFAULT_PAGE_SIZE)
    if not (1 <= size <= MAX_PAGE_SIZE):
        raise QueryError(
            "invalid_page_size",
            f"每页条数必须在 1~{MAX_PAGE_SIZE} 之间，收到 {size}（常用档位：{list(PAGE_SIZES)}）",
        )
    number = max(1, int(page or 1))
    return number, size


def _paginate(frame: pd.DataFrame, page: int, page_size: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    """切页。页码越界**夹到最后一页**并把 `clamped` 标出来（不返回一个莫名其妙的空页）。"""
    total = int(len(frame))
    page_count = max(1, math.ceil(total / page_size)) if total else 1
    clamped = page > page_count
    page = min(page, page_count)
    start = (page - 1) * page_size
    return frame.iloc[start:start + page_size], {
        "page": page, "page_size": page_size, "total": total,
        "page_count": page_count, "clamped": clamped,
    }


def _check_sort(table: str, sort: str | None, order: str | None) -> tuple[str, str]:
    key = str(sort or _DEFAULT_SORT[table])
    if key not in _SORT_KEYS[table]:
        raise QueryError(
            "unknown_sort",
            f"{TABLE_LABELS[table]}不支持按 {key!r} 排序（可选：{list(_SORT_KEYS[table])}）",
        )
    direction = str(order or _ORDER_DESC).lower()
    if direction not in (_ORDER_ASC, _ORDER_DESC):
        raise QueryError("unknown_order", f"排序方向只支持 asc / desc，收到 {order!r}")
    return key, direction


def _json_cell(value: Any) -> Any:
    """把 pandas/numpy 的值转成 JSON 能表达的形态（日期 → ISO，缺失 → None）。"""
    if value is None or value is pd.NA:
        return None
    if isinstance(value, float) and value != value:      # NaN
        return None
    if isinstance(value, (pd.Timestamp, _dt.datetime, _dt.date)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, _dt.timedelta):
        return str(value)
    return str(value)


def _rows_from_frame(frame: pd.DataFrame, columns: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    keys = [column["key"] for column in columns]
    return [
        {key: _json_cell(row[key]) for key in keys}
        for _, row in frame.iterrows()
    ]


# ════════════════════════════════════════════════════════════════════════
# ① 客户表
# ════════════════════════════════════════════════════════════════════════
def _customer_frame(dataset_id: str, start: _dt.date, end: _dt.date) -> dict[str, Any]:
    def build() -> pd.DataFrame:
        window = tools.raw_window(start, end)
        rows = tools.valid_rows(start, end)
        table = tools.customer_table(rows).rename(columns={"CustomerID": "customer_id"})
        activity = tools.customer_activity()
        table["first_purchase"] = table["customer_id"].map(
            lambda value: activity["first_purchase"].get(float(value))
        )
        table["last_purchase"] = table["customer_id"].map(
            lambda value: activity["last_purchase"].get(float(value))
        )
        # 客户号的展示形态：整数就按整数写（17850），不留 float 的小数尾巴
        table["customer_id"] = table["customer_id"].map(
            lambda value: int(value) if float(value).is_integer() else float(value)
        )
        scope = tools.build_customer_scope(window, rows, start, end)
        return {"table": table, "scope": scope}

    bundle = _cached((TABLE_CUSTOMERS, dataset_id, f"{start}|{end}"), build)
    return bundle


def _customer_query(bundle: dict[str, Any], query: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    table = bundle["table"].copy()
    search = _clean_search(query.get("search"))
    if search:
        needle = search.lower()
        table = table[table["customer_id"].astype(str).str.lower().str.contains(needle, regex=False)]

    # 默认按销售额降序，并列按客户号升序 —— 排序完全由这里决定（前端只显示）
    sort, order = _check_sort(TABLE_CUSTOMERS, query.get("sort"), query.get("order"))
    if sort == "order_count":
        sort = "purchase_count"                      # 两者同义（都按去重订单号）
    table = table.sort_values(
        [sort, "customer_id"], ascending=[order == _ORDER_ASC, True], kind="mergesort"
    )

    scope = bundle["scope"]
    summary = {
        "count": int(len(table)),
        "sales_amount": float(table["sales_amount"].sum()) if len(table) else 0.0,
        "purchase_count": int(table["purchase_count"].sum()) if len(table) else 0,
        "customer_id_null_rows": scope["customer_id_null_rows"],
        "customer_scope_sales_share": scope["customer_scope_sales_share"],
        "note": (
            f"客户表只覆盖**有客户号**的成交（{scope['valid_customer_id_nonnull_rows']} 行有效成交），"
            f"占全部销售额 {scope['customer_scope_sales_share'] * 100:.2f}%；"
            f"客户号为空的 {scope['valid_customer_id_null_rows']} 行仍计入销售额，但不进客户维度。"
        ),
    }
    return table, {"scope": scope, "summary": summary}


# ════════════════════════════════════════════════════════════════════════
# ② 产品表
# ════════════════════════════════════════════════════════════════════════
def _product_frame(dataset_id: str, start: _dt.date, end: _dt.date) -> dict[str, Any]:
    def build() -> dict[str, Any]:
        rows = tools.valid_rows(start, end)
        table = tools.product_table(rows).reset_index().rename(columns={
            "StockCode": "stock_code",
            "amount": "sales_amount",
            "qty": "quantity",
            "orders": "order_count",
            "rows": "row_count",
        })
        # 商品编码统一成**字符串**：数据里纯数字编码是 int、带字母的是 str（如 85123A），
        # 但它们是同一种东西（商品标识）。既有产品排行工具也是 str(index)，两边必须一致；
        # 而且**必须在合并退货列之前就统一**，否则 int 编码与 str 编码对不上，全是 0。
        table["stock_code"] = table["stock_code"].map(str)

        # 退货列：**与商品退货分析同一套判定**（数量为负 或 单号以 C 开头，并集去重）
        window = tools.raw_window(start, end)
        negative = window["Quantity"] < 0
        cancelled = engine_metrics.mask_cancelled(window)
        blocked = window.loc[negative | cancelled].copy()
        if len(blocked):
            blocked["_return_amount"] = engine_metrics.line_amount(blocked)
            blocked["_stock_code"] = blocked["StockCode"].map(str)
            grouped = blocked.groupby("_stock_code", dropna=False)
            returns = pd.DataFrame({
                "stock_code": list(grouped.groups.keys()),
                "return_rows": grouped["InvoiceNo"].size().to_numpy(),
                "return_quantity": grouped["Quantity"].sum().to_numpy(),
            })
        else:
            returns = pd.DataFrame(columns=["stock_code", "return_rows", "return_quantity"])
        table = table.merge(returns, on="stock_code", how="left")
        table["return_rows"] = table["return_rows"].fillna(0).astype(int)
        table["return_quantity"] = table["return_quantity"].fillna(0).astype(float)
        table["description"] = table["description"].fillna("")
        return {
            "table": table,
            "raw_rows": int(len(window)),
            "valid_rows": int(len(rows)),
            "total_amount": float(table["sales_amount"].sum()) if len(table) else 0.0,
        }

    return _cached((TABLE_PRODUCTS, dataset_id, f"{start}|{end}"), build)


def _product_query(bundle: dict[str, Any], query: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    table = bundle["table"].copy()
    search = _clean_search(query.get("search"))
    if search:
        needle = search.lower()
        hit = (
            table["stock_code"].astype(str).str.lower().str.contains(needle, regex=False)
            | table["description"].astype(str).str.lower().str.contains(needle, regex=False)
        )
        table = table[hit]

    sort, order = _check_sort(TABLE_PRODUCTS, query.get("sort"), query.get("order"))
    table = table.sort_values(
        [sort, "stock_code"], ascending=[order == _ORDER_ASC, True], kind="mergesort"
    )
    summary = {
        "count": int(len(table)),
        "sales_amount": float(table["sales_amount"].sum()) if len(table) else 0.0,
        "quantity": float(table["quantity"].sum()) if len(table) else 0.0,
        "return_rows": int(table["return_rows"].sum()) if len(table) else 0,
        "note": (
            f"口径：含首尾全天；排除取消单、数量≤0、单价≤0 的行（{bundle['valid_rows']} 行有效成交）；"
            f"退货列按**原始行**统计（退货/取消正是被排除掉的那些行）。"
        ),
    }
    return table, {"summary": summary}


# ════════════════════════════════════════════════════════════════════════
# ③ 销售表（维度 × 指标）
# ════════════════════════════════════════════════════════════════════════
def _sales_frame(dataset_id: str, start: _dt.date, end: _dt.date, dimension: str, metric: str) -> dict[str, Any]:
    def build() -> dict[str, Any]:
        rows = tools.valid_rows(start, end)
        if dimension == "country":
            grouped = rows.groupby("Country", dropna=False)
            table = pd.DataFrame({
                "dimension_value": [str(key) for key in grouped.groups],
                "sales_amount": grouped["_amount"].sum().to_numpy(),
                "order_count": grouped["InvoiceNo"].nunique().to_numpy(),
                "customer_count": grouped["CustomerID"].nunique().to_numpy(),
                "rows": grouped.size().to_numpy(),
            })
            table["partial"] = False
            table["period_note"] = ""
        else:
            # 逐桶的销售额/订单数**直接取自既有的销售趋势工具**（同一段分桶代码 + 同一套口径），
            # 本函数只补它没有的两列：客户数、客单价。
            trend = tools.sales_trend(start, end, granularity=dimension)
            points = trend["series"]["points"]
            stamps = pd.to_datetime(rows[engine_metrics.TIME_FIELD])
            bucket, _label = tools.bucket_of(stamps, dimension)
            bucket_key = bucket.dt.date.astype(str)
            by_bucket = rows.assign(_bucket=bucket_key).groupby("_bucket")
            customers = by_bucket["CustomerID"].nunique()
            table = pd.DataFrame([
                {
                    "dimension_value": point["period_start"],
                    "sales_amount": float(point["amount"]),
                    "order_count": int(point["orders"]),
                    "customer_count": int(customers.get(point["period_start"], 0)),
                    "rows": int(point["rows"]),
                    "partial": bool(point["partial"]),
                    "period_note": (
                        f"本期不完整：只统计 {point['covered_start']} ~ {point['covered_end']}"
                        if point["partial"] else ""
                    ),
                }
                for point in points
            ])
            if not len(table):
                table = pd.DataFrame(columns=[
                    "dimension_value", "sales_amount", "order_count",
                    "customer_count", "rows", "partial", "period_note",
                ])
        table["avg_order_amount"] = [
            (float(amount) / int(orders)) if int(orders) else 0.0
            for amount, orders in zip(table["sales_amount"], table["order_count"])
        ]
        return {
            "table": table,
            "rows_in_range": int(len(rows)),
            "total_amount": float(table["sales_amount"].sum()) if len(table) else 0.0,
            "partial_buckets": int(table["partial"].sum()) if len(table) else 0,
        }

    return _cached((TABLE_SALES, dataset_id, f"{start}|{end}|{dimension}|{metric}"), build)


def _sales_query(bundle: dict[str, Any], query: dict[str, Any], dimension: str, metric: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    table = bundle["table"].copy()
    search = _clean_search(query.get("search"))
    if search:
        needle = search.lower()
        table = table[table["dimension_value"].astype(str).str.lower().str.contains(needle, regex=False)]

    table["metric_value"] = table[metric]
    if metric == "avg_order_amount":
        # 客单价是**平均值**：平均值的"占比"没有意义 → 明确给 None（前端显示"—"），不硬算一个数
        table["metric_share"] = [None] * len(table)
        total_orders = int(table["order_count"].sum()) if len(table) else 0
        metric_total = (float(table["sales_amount"].sum()) / total_orders) if total_orders else 0.0
    else:
        metric_total = float(table[metric].sum()) if len(table) else 0.0
        table["metric_share"] = [
            (float(value) / metric_total * 100) if metric_total else 0.0 for value in table[metric]
        ]

    default_sort = "dimension_value" if dimension in ("day", "week") else metric
    sort, order = _check_sort(TABLE_SALES, query.get("sort") or default_sort, query.get("order"))
    if sort in ("metric_value", "metric_share"):
        sort = metric
    ascending = order == _ORDER_ASC
    if sort == "dimension_value":
        table = table.sort_values([sort], ascending=ascending, kind="mergesort")
    else:
        table = table.sort_values(
            [sort, "dimension_value"], ascending=[ascending, True], kind="mergesort"
        )

    summary = {
        "count": int(len(table)),
        "metric": metric,
        "metric_label": SALES_METRIC_LABELS[metric],
        "metric_total": metric_total,
        "sales_amount": float(table["sales_amount"].sum()) if len(table) else 0.0,
        "order_count": int(table["order_count"].sum()) if len(table) else 0,
        "partial_buckets": int(table["partial"].sum()) if len(table) else 0,
        "note": (
            f"维度：{SALES_DIMENSION_LABELS[dimension]}；指标：{SALES_METRIC_LABELS[metric]}。"
            f"口径：含首尾全天；排除取消单、数量≤0、单价≤0 的行（{bundle['rows_in_range']} 行有效成交）。"
            + (f"有 {bundle['partial_buckets']} 个期间**不完整**（区间没对齐自然周），明细列已标注。"
               if bundle["partial_buckets"] else "")
            + ("客单价是平均值，不计算占比。" if metric == "avg_order_amount" else "")
        ),
    }
    return table, {"summary": summary}


# ════════════════════════════════════════════════════════════════════════
# ④ 原始数据（**不套分析口径**：如实呈现数据源里的原始行）
# ════════════════════════════════════════════════════════════════════════
def _raw_frame(start: _dt.date, end: _dt.date) -> dict[str, Any]:
    window = tools.raw_window(start, end).copy()
    window = window.reset_index(drop=True)
    window["row_no"] = range(1, len(window) + 1)
    window["CustomerID"] = window["CustomerID"].astype("Int64")
    # 订单号（含 C 开头的取消单）与商品编码是**标识**：统一成字符串，避免一列里混着 581587 与 "C536379"
    for column in ("InvoiceNo", "StockCode"):
        window[column] = window[column].map(lambda value: None if value is None or value != value else str(value))
    return {"table": window, "rows": int(len(window))}


def _raw_query(bundle: dict[str, Any], query: dict[str, Any], start: _dt.date, end: _dt.date) -> tuple[pd.DataFrame, dict[str, Any]]:
    table = bundle["table"]
    search = _clean_search(query.get("search"))
    if search:
        needle = search.lower()
        hit = pd.Series(False, index=table.index)
        for column in _SEARCH_COLUMNS[TABLE_RAW]:
            hit = hit | table[column].astype(str).str.lower().str.contains(needle, regex=False)
        table = table[hit]

    sort, order = _check_sort(TABLE_RAW, query.get("sort"), query.get("order"))
    ascending = order == _ORDER_ASC
    if sort == "row_no":
        table = table.sort_values("row_no", ascending=ascending, kind="mergesort")
    else:
        table = table.sort_values([sort, "row_no"], ascending=[ascending, True], kind="mergesort")

    summary = {
        "count": int(len(table)),
        "note": (
            f"原始数据是数据源里的**原样行**（未套用分析口径：含取消单、负数量等）；"
            f"业务口径请用「销售分析 / 客户分析 / 产品分析」三张表。"
        ),
    }
    return table, {"summary": summary}


# ════════════════════════════════════════════════════════════════════════
# 唯一入口：页面与导出都走这里
# ════════════════════════════════════════════════════════════════════════
def fetch_table(
    table: str,
    *,
    dataset_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
    search: str | None = None,
    sort: str | None = None,
    order: str | None = None,
    dimension: str | None = None,
    metric: str | None = None,
    page: int | None = 1,
    page_size: int | None = DEFAULT_PAGE_SIZE,
    export: bool = False,
) -> dict[str, Any]:
    """查一张表。`export=True` 时不分页（要全部行），其余参数与分页查询**完全一致**。"""
    if table not in TABLES:
        raise QueryError("unknown_table", f"没有这张表：{table!r}（可选：{list(TABLES)}）")

    dataset = registry.require_analysis_dataset(dataset_id or registry.BUILTIN_DATASET_ID)
    low, high, notes = resolve_window(dataset, start, end)
    key = (dataset["dataset_id"], low.isoformat(), high.isoformat())

    # 排序**只在这里解析一次**：下面各表的查询函数只按解析好的键排。
    # （踩过的坑：各表函数自己再取一次默认值 → 实际排序用 desc、回显却写 asc，两边打架。）
    sort_key, sort_order = _check_sort(
        table,
        sort or _default_sort_for(table, dimension, metric),
        order or _default_order_for(table, dimension, metric),
    )
    query = {"search": search, "sort": sort_key, "order": sort_order}

    if table == TABLE_CUSTOMERS:
        frame, extra = _customer_query(_customer_frame(key[0], low, high), query)
    elif table == TABLE_PRODUCTS:
        frame, extra = _product_query(_product_frame(key[0], low, high), query)
    elif table == TABLE_SALES:
        chosen_dimension = str(dimension or "day")
        if chosen_dimension not in SALES_DIMENSIONS:
            raise QueryError("unknown_dimension", f"维度只支持 {list(SALES_DIMENSIONS)}，收到 {dimension!r}")
        chosen_metric = str(metric or "sales_amount")
        if chosen_metric not in SALES_METRICS:
            raise QueryError("unknown_metric", f"指标只支持 {list(SALES_METRICS)}，收到 {metric!r}")
        frame, extra = _sales_query(
            _sales_frame(key[0], low, high, chosen_dimension, chosen_metric), query,
            chosen_dimension, chosen_metric,
        )
    else:
        frame, extra = _raw_query(_raw_frame(low, high), query, low, high)

    columns = _TABLE_COLUMNS[table]
    total_rows = int(len(frame))
    if export:
        if total_rows > MAX_EXPORT_ROWS:
            raise QueryError(
                "export_too_large",
                f"当前条件下共 {total_rows:,} 行，超过单次导出上限 {MAX_EXPORT_ROWS:,} 行 ——"
                f"请先缩小范围（例如按时间或关键字筛选）再导出。",
            )
        page_frame, paging = frame, {
            "page": 1, "page_size": total_rows, "total": total_rows,
            "page_count": 1, "clamped": False, "exported": True,
        }
    else:
        page_number, size = _page_args(page, page_size)
        page_frame, paging = _paginate(frame, page_number, size)
        paging["exported"] = False

    return {
        "table": table,
        "table_label": TABLE_LABELS[table],
        "dataset": registry.dataset_summary(dataset),
        "query": {
            "dataset_id": dataset["dataset_id"],
            "start": low.isoformat(),
            "end": high.isoformat(),
            "search": _clean_search(search),
            "sort": sort_key,
            "order": sort_order,
            "dimension": dimension if table == TABLE_SALES else None,
            "metric": metric if table == TABLE_SALES else None,
        },
        "columns": list(columns),
        # sort / order 在**顶层也放一份**：页面表头高亮与脚本核对都直接从这两个字段读，
        # 不必再去 query 块里翻（两处同源，同一个值）。
        "sort": sort_key,
        "order": sort_order,
        "items": _rows_from_frame(page_frame, columns),
        **paging,
        "notes": notes,
        **_clean_extra(extra),
    }


def _clean_extra(extra: dict[str, Any]) -> dict[str, Any]:
    """只把能 JSON 出去的字段带出去（scope 里的 pandas 类型要转掉）。"""
    cleaned: dict[str, Any] = {}
    for key, value in extra.items():
        if key == "scope" and isinstance(value, dict):
            cleaned["scope"] = {item_key: _json_cell(item) for item_key, item in value.items()}
        else:
            cleaned[key] = value
    return cleaned


def _default_sort_for(table: str, dimension: str | None, metric: str | None) -> str:
    if table == TABLE_SALES:
        return "dimension_value" if str(dimension or "day") in ("day", "week") else str(metric or "sales_amount")
    return _DEFAULT_SORT[table]


def _default_order_for(table: str, dimension: str | None, metric: str | None) -> str:
    if table == TABLE_SALES:
        return _ORDER_ASC if str(dimension or "day") in ("day", "week") else _ORDER_DESC
    if table == TABLE_RAW:
        return _ORDER_ASC                 # 原始数据默认从第 1 行开始看
    return _ORDER_DESC                    # 业务表默认"大的在前"


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_EXPORT_ROWS",
    "PAGE_SIZES",
    "QueryError",
    "SALES_DIMENSIONS",
    "SALES_DIMENSION_LABELS",
    "SALES_METRICS",
    "SALES_METRIC_LABELS",
    "TABLES",
    "TABLE_LABELS",
    "fetch_table",
    "reset_cache",
    "resolve_window",
]
