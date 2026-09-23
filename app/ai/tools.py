"""tools.py · **白名单工具**（TASK-004 链路的第三步：确定性计算）。

════════════════════════════════════════════════════════════════════════
【这个文件是"LLM 绝不碰数字"这条铁律的落点】
════════════════════════════════════════════════════════════════════════
链路：`Intent JSON → **这里** → 事实结果 → LLM 组织语言`。
回答里出现的每一个数字，都必须能在本文件某次调用的返回值里找到出处。
所以本文件的规矩只有一条：**所有数字都由 pandas/executor 算，没有一个是写死的。**

三个工具（第一版就三个，多一个都不加）：

    sales_summary   调**既有冻结资产** `executor.compute_sales_amount()` —— 口径就是 D16，
                    一行不改、一位不差。订单数/客户数这类 metrics 目录里没有的，
                    在这里用**同一套掩码**（`_valid_window`）现算，并与 executor 对账。
    sales_trend     同一套掩码 → groupby 日/周求和（确定性）
    top_products    同一套掩码 → 按 StockCode 分组排序取 TOP N（确定性）

════════════════════════════════════════════════════════════════════════
【为什么必须"同一套掩码"（而不是各自写一遍筛选）】
════════════════════════════════════════════════════════════════════════
D16 口径有三条排除规则（取消单 / 负数量 / 非正价格）与"含首尾全天"的区间语义（D16-2）。
如果 trend 自己写一遍 `Quantity > 0` 之类的筛选，它跟 summary 的数字**迟早对不上** ——
而用户会同时看到这两个数字。所以这里只留**一个**取数函数 `_valid_window()`，
它的掩码严格照抄 `executor.compute_sales_amount()` 的做法，
并且每个工具都做一次**对账自检**（`selfcheck`）写进返回值：算出来跟 executor 不一致，
就是有 bug，藏不住。

冻结点：本文件**只读** engine（`metrics` / `executor` / `loader`），一个字节都不改它们。
把口径搬过来的部分，逐行对着 executor.py 抄，并用 `tests/test_chat.py` 的
逐位一致性测试钉死。
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

from app.engine import executor, loader
from app.engine import metrics as engine_metrics

# 浮点对账容差（与 executor.py 内部同一量级；位级相等时它是 0 差异）
_FLOAT_TOL = 1e-6

# 按周聚合时，一周从**周一**开始（与 DECISIONS.md:127「那一周 2011-11-21~11-27」一致）
WEEK_START_WEEKDAY = 0

# StockCode 不是商品的"编码"（邮费/手续费/调整）在数据集里同样以 StockCode 形式存在，
# 这里**不过滤**它们 —— 过滤需要一张写死的黑名单（那是"硬编码业务知识"），
# 而"数据集里有什么就报什么"才是诚实的。所以只在 notes 里如实说明。
_NONPRODUCT_NOTE = (
    "排行按 StockCode 分组，**未过滤**邮费/手续费/人工调整类编码"
    "（数据集里它们也是 StockCode，如 POST / DOT / BANK CHARGES / Manual）——"
    "命中时看 description 就能认出来。"
)


def _empty_window_note(rows_in_range: int, start: _dt.date, end: _dt.date) -> list[str]:
    """区间内一行都没有 → 必须**说出来**，不能让用户对着一个 0 自己猜。

    为什么可能为空：问的区间落在数据集之外（数据只到 2011-12-09），
    或者月份本身没有交易（比如 2011-11-26 在数据集里整天没有记录）。
    """
    if rows_in_range:
        return []
    first, last = dataset_bounds()
    return [
        f"**区间 {start} ~ {end} 内没有任何交易记录**（销售额为 0 是「确实没有」，不是算错）。"
        f"数据集覆盖范围是 {first} ~ {last}。"
    ]


# ════════════════════════════════════════════════════════════════════════
# 数据集画像（全部**从数据里算**，没有一个是写死的常量）
# ════════════════════════════════════════════════════════════════════════
_profile_cache: dict[str, Any] | None = None


def dataset_profile() -> dict[str, Any]:
    """数据集的真实边界与规模（忠实说明"数据支持什么、不支持什么"要用到）。

    为什么不在代码里写 `DATA_LAST_DAY = 2011-12-09` 这种常量：数据集是外部资产，
    写死的常量一旦与文件脱钩（换了数据忘了改常量），所有"数据边界"的话就都成了假话。
    这里每次从 `loader.load_raw()` 现算（loader 自己有进程级缓存，不重复读盘）。
    """
    global _profile_cache
    if _profile_cache is not None:
        return dict(_profile_cache)
    frame = loader.load_raw()
    time_column = engine_metrics.TIME_FIELD
    stamps = pd.to_datetime(frame[time_column])
    profile = {
        "rows": int(len(frame)),
        "columns": list(map(str, frame.columns)),
        "column_count": int(frame.shape[1]),
        "first_day": str(stamps.min().date()),
        "last_day": str(stamps.max().date()),
        "country_count": int(frame["Country"].nunique(dropna=True)),
        "customer_count": int(frame["CustomerID"].nunique(dropna=True)),
        "stock_code_count": int(frame["StockCode"].nunique(dropna=True)),
        "has_region_field": False,
    }
    _profile_cache = profile
    return dict(profile)


def dataset_bounds() -> tuple[_dt.date, _dt.date]:
    """`(第一天, 最后一天)` —— 相对时间说法（"上个月"）以最后一天为基准换算。"""
    profile = dataset_profile()
    return (
        _dt.date.fromisoformat(profile["first_day"]),
        _dt.date.fromisoformat(profile["last_day"]),
    )


def reset_cache() -> None:
    """清掉画像缓存（测试用：换了数据集要能重算）。"""
    global _profile_cache
    _profile_cache = None


# ════════════════════════════════════════════════════════════════════════
# 取数：**唯一**的窗口 + 有效行掩码（D16 口径，严格照抄 executor）
# ════════════════════════════════════════════════════════════════════════
def _valid_window(start: _dt.date, end: _dt.date) -> tuple[pd.DataFrame, pd.Series]:
    """返回 `(区间内的行, 有效行掩码)`。

    逐行对应 `app/engine/executor.py::compute_sales_amount` 的做法（**不改它，只是照做**）：

        low, high = metrics.inclusive_day_window(start, end)              # D16-2 含首尾全天
        window = df.loc[(df[InvoiceDate] >= low) & (df[InvoiceDate] < high)]
        enabled = metrics.resolve_enabled_rules()                          # 三条排除规则全开
        hit = 每行的命中规则数（bool 相加 ⇒ **命中多条只算一次**）
        valid = hit == 0
    """
    frame = loader.load_raw()
    low, high = engine_metrics.inclusive_day_window(start, end)
    time_column = engine_metrics.TIME_FIELD
    in_range = (frame[time_column] >= low) & (frame[time_column] < high)
    window = frame.loc[in_range]

    rules = engine_metrics.resolve_enabled_rules()
    hit_count = pd.concat(
        {rule.key: rule.mask(window).astype(bool) for rule in rules}, axis=1
    ).sum(axis=1)
    valid = hit_count == 0
    return window, valid


def _valid_rows(start: _dt.date, end: _dt.date) -> pd.DataFrame:
    """有效行（= 真正参与口径计算的那些行），带一列算好的 `_amount`。"""
    window, valid = _valid_window(start, end)
    rows = window.loc[valid].copy()
    rows["_amount"] = engine_metrics.line_amount(rows)
    return rows


def _amount_of(rows: pd.DataFrame) -> float:
    """有效行金额合计。调用方式与 executor 完全一致（`line_amount(rows).sum()`）。"""
    return float(engine_metrics.line_amount(rows).sum())


# ════════════════════════════════════════════════════════════════════════
# 工具 ①：sales_summary
# ════════════════════════════════════════════════════════════════════════
def sales_summary(start: _dt.date, end: _dt.date) -> dict[str, Any]:
    """某时间段的销售额 / 订单数 / 客户数。

    销售额那部分**直接调既有 executor**（口径由它定义，这里不重算一遍当"第二实现"）；
    订单数/客户数 metrics 目录里没有，用**同一个掩码**现算，然后与 executor 的金额对账。
    """
    base = executor.compute_sales_amount(start, end)
    rows = _valid_rows(start, end)

    order_count = int(rows["InvoiceNo"].nunique(dropna=True))
    # CustomerID 为空的行按 D16-6 **不排除**，所以"客户数"只能数非空的那些（如实说明口径）
    customers = rows["CustomerID"].dropna()
    customer_count = int(customers.nunique())
    customers_null_rows = int(len(rows) - len(customers))

    recomputed = _amount_of(rows)
    amount = float(base["amount"])
    avg_order = (amount / order_count) if order_count else 0.0

    facts: dict[str, Any] = {
        "sales_amount": amount,
        "order_count": order_count,
        "customer_count": customer_count,
        "avg_order_amount": avg_order,
        "rows_in_range": int(base["rows_in_range"]),
        "rows_valid": int(base["rows_valid"]),
        "rows_excluded": int(base["rows_excluded"]),
        "excluded_amount": float(base["excluded_amount"]),
        "valid_qty_sum": float(base["valid_qty_sum"]),
        "customer_id_null_rows": int(base["validations"]["customer_id_nulls"]),
        "duplicate_rows": int(base["validations"]["duplicate_rows"]),
        "customers_null_rows_valid": customers_null_rows,
    }

    notes = [
        "口径 D16：含首尾全天；排除取消单（InvoiceNo 以 C 开头）、数量≤0、单价≤0 的行。",
        f"客户数只统计 CustomerID 非空的有效行；有效行里有 {customers_null_rows} 行没有客户号，"
        f"它们计入销售额但计不进客户数（D16-6：CustomerID 为空不排除）。",
    ] + _empty_window_note(int(base["rows_in_range"]), start, end)

    return {
        "tool": "sales_summary",
        "params": {"start": start.isoformat(), "end": end.isoformat()},
        "facts": facts,
        "notes": notes,
        "display": [
            {"label": "销售额", "value": amount, "unit": "元", "format": "money"},
            {"label": "订单数（去重发票号）", "value": order_count, "unit": "单", "format": "int"},
            {"label": "客户数（去重客户号）", "value": customer_count, "unit": "位", "format": "int"},
            {"label": "客单价（销售额 ÷ 订单数）", "value": avg_order, "unit": "元", "format": "money",
             "derived": True},
            {"label": "有效行数", "value": facts["rows_valid"], "unit": "行", "format": "int"},
            {"label": "被排除行数", "value": facts["rows_excluded"], "unit": "行", "format": "int"},
            {"label": "有效商品件数", "value": facts["valid_qty_sum"], "unit": "件", "format": "qty"},
        ],
        "selfcheck": {
            "amount_source": "executor.compute_sales_amount（冻结资产）",
            "amount_from_executor": amount,
            "amount_recomputed_from_mask": recomputed,
            "bit_identical": amount == recomputed,
            "delta": abs(amount - recomputed),
            "executor_checks_all_passed": all(
                bool(item.get("passed")) for item in base["validations"]["checks"]
            ),
        },
    }


# ════════════════════════════════════════════════════════════════════════
# 工具 ②：sales_trend
# ════════════════════════════════════════════════════════════════════════
def sales_trend(start: _dt.date, end: _dt.date, granularity: str = "day") -> dict[str, Any]:
    """按日 / 按周聚合的销售额序列（确定性 groupby，同一套掩码）。"""
    if granularity not in ("day", "week"):
        raise ValueError(f"granularity 只支持 day/week，收到 {granularity!r}")

    rows = _valid_rows(start, end)
    time_column = engine_metrics.TIME_FIELD
    stamps = pd.to_datetime(rows[time_column])

    if granularity == "day":
        bucket = stamps.dt.normalize()
        bucket_label = "天"
    else:
        # 周一为一周起点：先归零到当天 00:00，再减掉"今天是本周第几天"
        offset = (stamps.dt.weekday - WEEK_START_WEEKDAY) % 7
        bucket = stamps.dt.normalize() - pd.to_timedelta(offset, unit="D")
        bucket_label = "周（周一为起点）"

    grouped = rows.assign(_bucket=bucket).groupby("_bucket", sort=True)
    points: list[dict[str, Any]] = []
    for key, block in grouped:
        # 桶是"自然"的（整周/整日），但用户问的区间**不一定对齐桶边界**。
        # 例：问 2011-11-01~11-30 按周看，第一个桶从周一 2011-10-31 开始 ——
        # 它其实只装了 11-01~11-06 那几天。这里把"桶的完整跨度"和"落在问句区间内的跨度"
        # 都写出来并标 `partial`，免得一个标签把人引到"10月31日也有这笔钱"的误读上。
        period_start = pd.Timestamp(key).date()
        period_end = period_start + _dt.timedelta(days=0 if granularity == "day" else 6)
        covered_start = max(period_start, start)
        covered_end = min(period_end, end)
        points.append(
            {
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "covered_start": covered_start.isoformat(),
                "covered_end": covered_end.isoformat(),
                "covered_days": (covered_end - covered_start).days + 1,
                "partial": (period_start < start) or (period_end > end),
                "amount": float(block["_amount"].sum()),
                "rows": int(len(block)),
                "qty": float(block["Quantity"].sum()),
                "orders": int(block["InvoiceNo"].nunique(dropna=True)),
            }
        )

    amounts = [point["amount"] for point in points]
    bucket_sum = float(sum(amounts))
    total = _amount_of(rows)
    peak_index = max(range(len(points)), key=lambda i: amounts[i]) if points else None
    trough_index = min(range(len(points)), key=lambda i: amounts[i]) if points else None

    facts: dict[str, Any] = {
        "total_amount": total,
        "bucket_count": len(points),
        "bucket_sum": bucket_sum,
        "max_bucket_amount": amounts[peak_index] if peak_index is not None else 0.0,
        "max_bucket_start": points[peak_index]["period_start"] if peak_index is not None else None,
        "min_bucket_amount": amounts[trough_index] if trough_index is not None else 0.0,
        "min_bucket_start": points[trough_index]["period_start"] if trough_index is not None else None,
        "avg_bucket_amount": (bucket_sum / len(points)) if points else 0.0,
        "granularity": granularity,
    }

    partial_points = [point for point in points if point["partial"]]
    notes = [
        f"按{bucket_label}聚合，共 {len(points)} 个{bucket_label}；空档（没有销售的日子/周）**不补零**，"
        "所以点数可能少于自然天数/周数。",
        "口径 D16：含首尾全天；排除取消单、数量≤0、单价≤0 的行。",
    ]
    if partial_points:
        notes.append(
            "**首尾桶不完整**：问句的区间不是按"
            f"{bucket_label}对齐的，有 {len(partial_points)} 个桶只装了区间内的部分天数"
            "（明细里 `covered_start`~`covered_end` 是它实际覆盖的范围，`partial` 已标 true）——"
            "拿它们和整桶比高低会得出错的结论。"
        )
    notes += _empty_window_note(len(rows), start, end)

    return {
        "tool": "sales_trend",
        "params": {"start": start.isoformat(), "end": end.isoformat(), "granularity": granularity},
        "facts": facts,
        "series": {"value_key": "amount", "points": points},
        "notes": notes,
        "display": [
            {"label": "区间销售额合计", "value": total, "unit": "元", "format": "money"},
            {"label": f"{bucket_label}数", "value": len(points), "unit": "个", "format": "int"},
            {"label": f"最高一个{bucket_label}", "value": facts["max_bucket_amount"], "unit": "元",
             "format": "money",
             "note": f"起始 {facts['max_bucket_start']}" if facts["max_bucket_start"] else ""},
            {"label": f"最低一个{bucket_label}", "value": facts["min_bucket_amount"], "unit": "元",
             "format": "money",
             "note": f"起始 {facts['min_bucket_start']}" if facts["min_bucket_start"] else ""},
            {"label": f"{bucket_label}均额", "value": facts["avg_bucket_amount"], "unit": "元",
             "format": "money", "derived": True},
        ],
        "selfcheck": {
            "detail_total_source": "同一掩码下 line_amount(rows).sum()（应与逐桶求和一致）",
            "detail_total": total,
            "bucket_sum": bucket_sum,
            "bucket_sum_matches_total": abs(bucket_sum - total) <= max(_FLOAT_TOL, abs(total) * _FLOAT_TOL),
            "delta": abs(bucket_sum - total),
        },
    }


# ════════════════════════════════════════════════════════════════════════
# 工具 ③：top_products
# ════════════════════════════════════════════════════════════════════════
def _dominant_description(series: pd.Series) -> str:
    """一个 StockCode 对应多个 Description 时，取**出现次数最多**的那个。

    同票数时按**字典序**取第一个 —— 纯粹为了可复现：`value_counts()` 在票数相同时
    的顺序依赖内部实现，不加这个 tie-break，"同一个问题问两次给出不同商品名"就可能发生。
    """
    counts = series.value_counts()
    if counts.empty:
        return ""
    winners = sorted(str(name) for name in counts[counts == counts.max()].index)
    return winners[0]


def top_products(start: _dt.date, end: _dt.date, top_n: int = 5) -> dict[str, Any]:
    """产品销售排行 TOP N（按 StockCode 分组，销售额降序）。

    为什么按 StockCode 而不是 Description 分组：Description 在数据集里有空值、有同码多名，
    按它分组会把同一个商品拆成几行、还会多出一个 "NaN 商品"。StockCode 才是商品身份，
    Description 只作为**展示名**附上（取该码下出现次数最多的那个）。
    """
    rows = _valid_rows(start, end)

    grouped = rows.groupby("StockCode", dropna=False)
    table = grouped.agg(
        amount=("_amount", "sum"),
        qty=("Quantity", "sum"),
        rows=("_amount", "size"),
        orders=("InvoiceNo", "nunique"),
    )
    table["description"] = grouped["Description"].agg(
        lambda series: _dominant_description(series.dropna())
    )
    table = table.sort_values(
        ["amount", "StockCode"], ascending=[False, True], kind="mergesort"
    )
    top = table.head(top_n)

    items = [
        {
            "rank": rank,
            "stock_code": str(index),
            "description": str(row["description"]),
            "amount": float(row["amount"]),
            "qty": float(row["qty"]),
            "rows": int(row["rows"]),
            "orders": int(row["orders"]),
            "share": (float(row["amount"]) / float(table["amount"].sum()))
            if float(table["amount"].sum())
            else 0.0,
        }
        for rank, (index, row) in enumerate(top.iterrows(), start=1)
    ]

    # 全量分组求和 == 同一掩码下的行级求和（对账：分组没漏行、没重复计）
    table_total = float(table["amount"].sum())
    detail_total = _amount_of(rows)
    top_amount = float(top["amount"].sum()) if len(top) else 0.0

    facts: dict[str, Any] = {
        "top_amount": top_amount,
        "all_products_amount": table_total,
        "total_amount": detail_total,
        "product_count": int(len(table)),
        "top_n": int(top_n),
        "top_share": (top_amount / table_total) if table_total else 0.0,
        "best_stock_code": str(top.index[0]) if len(top) else "",
        "best_amount": float(top["amount"].iloc[0]) if len(top) else 0.0,
        "best_description": str(top["description"].iloc[0]) if len(top) else "",
    }

    notes = [
        "口径 D16：含首尾全天；排除取消单、数量≤0、单价≤0 的行。",
        "按 StockCode 分组（Description 只作展示名，取该编码下出现次数最多者）。",
        _NONPRODUCT_NOTE,
    ] + _empty_window_note(len(rows), start, end)

    return {
        "tool": "top_products",
        "params": {"start": start.isoformat(), "end": end.isoformat(), "top_n": int(top_n)},
        "facts": facts,
        "items": items,
        "notes": notes,
        "display": [
            {"label": "上榜商品数", "value": len(items), "unit": "个", "format": "int"},
            {"label": f"TOP{len(items)} 合计销售额", "value": top_amount, "unit": "元", "format": "money"},
            {"label": "占区间总销售额", "value": facts["top_share"] * 100, "unit": "%", "format": "pct",
             "derived": True},
            {"label": "区间总销售额", "value": detail_total, "unit": "元", "format": "money"},
            {"label": "区间内出现过的商品编码数", "value": facts["product_count"], "unit": "个", "format": "int"},
        ],
        "selfcheck": {
            "detail_total_source": "同一掩码下 line_amount(rows).sum()",
            "detail_total": detail_total,
            "grouped_total": table_total,
            "grouped_matches_detail": abs(table_total - detail_total) <= max(_FLOAT_TOL, abs(detail_total) * _FLOAT_TOL),
            "delta": abs(table_total - detail_total),
            "top_amount_le_detail": top_amount <= detail_total + _FLOAT_TOL,
        },
    }


# ════════════════════════════════════════════════════════════════════════
# 白名单注册表（service.py 只认这张表 —— 表外的东西调不动）
# ════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class ToolSpec:
    name: str
    title: str
    description: str
    run: Callable[..., dict[str, Any]]


TOOLS: dict[str, ToolSpec] = {
    "sales_summary": ToolSpec(
        name="sales_summary",
        title="销售汇总",
        description="某时间段的销售额 / 订单数 / 客户数（销售额调既有 executor，口径 D16）",
        run=sales_summary,
    ),
    "sales_trend": ToolSpec(
        name="sales_trend",
        title="销售趋势",
        description="某时间段按日或按周的销售额序列（同一套 D16 掩码下 groupby）",
        run=sales_trend,
    ),
    "top_products": ToolSpec(
        name="top_products",
        title="产品排行",
        description="某时间段销售额最高的 N 个产品（按 StockCode 分组降序）",
        run=top_products,
    ),
}


def run_tool(name: str, params: dict[str, Any]) -> dict[str, Any]:
    """按白名单执行工具。名字不在表里 → 明确报错（**绝不"找个相近的凑合"**）。"""
    spec = TOOLS.get(name)
    if spec is None:
        raise KeyError(f"没有这个工具：{name!r}（白名单：{sorted(TOOLS)}）")
    return spec.run(**params)


__all__ = [
    "TOOLS",
    "ToolSpec",
    "WEEK_START_WEEKDAY",
    "dataset_bounds",
    "dataset_profile",
    "reset_cache",
    "run_tool",
    "sales_summary",
    "sales_trend",
    "top_products",
]
