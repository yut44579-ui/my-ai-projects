"""tools.py · **白名单工具**（TASK-004 链路的第三步：确定性计算）。

════════════════════════════════════════════════════════════════════════
【这个文件是"LLM 绝不碰数字"这条铁律的落点】
════════════════════════════════════════════════════════════════════════
链路：`Intent JSON → **这里** → 事实结果 → LLM 组织语言`。
回答里出现的每一个数字，都必须能在本文件某次调用的返回值里找到出处。
所以本文件的规矩只有一条：**所有数字都由 pandas/executor 算，没有一个是写死的。**

五个工具（白名单**恰好这五个**，表外的一律调不动）：

    sales_summary   调**既有冻结资产** `executor.compute_sales_amount()` —— 口径就是 D16，
                    一行不改、一位不差。订单数/客户数这类 metrics 目录里没有的，
                    在这里用**同一套掩码**（`_valid_window`）现算，并与 executor 对账。
    sales_trend     同一套掩码 → groupby 日/周求和（确定性）
    top_products    同一套掩码 → 按 StockCode 分组排序取 TOP N（确定性）
    sales_compare   两个区间比大小（custom/wow/mom/yoy）+ 可选的**受控归因**
                    （attribution_dimension ∈ country/stock_code）—— TASK-005
    sales_breakdown_by_country  某区间各国家的销售额分布 TOP N + 占比 —— TASK-005

【TASK-005 加的这两条，把"判断权"也钉在了代码里】
    · 「主要是谁造成的」= 排序问题。排序由 `_rank_contributors()` 做，
      LLM 只拿到排好的名单（连"主要"这个词都是代码写的，见 answer.render_contribution_text）。
    · 归因有**硬校验**：Σ(各维度 delta) 必须等于总 delta（总 delta 走 executor，
      分组求和走 groupby —— 两条独立路径）。不等 → 整段归因不得进入回答。
    · previous == 0 → 一律 `not_available`，**绝不放 Infinity / NaN 出去**。

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
import math
import time
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
# ════════════════════════════════════════════════════════════════════════
# 货币单位：**显式声明**，不是从数据里推出来的（全项目唯一出处）
# ════════════════════════════════════════════════════════════════════════
# 数据集 8 列里**没有货币字段** —— "金额是什么币种"数据自己没说，只能由我们声明，
# 所以这里把**出处**（source）一起写出来：它不是"从数据读到的"，是我们声明的口径。
#
# 口径由用户定（2026-09-24）：**按人民币「元」显示**，数值**不做任何换算**（就用数据里的数）。
# 事实段（answer.py）、display 表、能力端点、前端**都只从这一处取值**，
# 别处不许再出现「元」/「¥」/「英镑」/「£」的硬编码 —— 将来换数据集只改这一个常量。
DATASET_CURRENCY: dict[str, str] = {
    "code": "CNY",
    "symbol": "¥",
    "name": "元",
    "source": "declared",
    "note": "金额按人民币「元」显示（口径声明）；文件里没有货币列，"
            "币种不是从数据里读出来的，数值不做任何换算。",
}


def currency_unit() -> str:
    """金额在文字/表格里跟的单位（`1,509,496.33元` 的那个「元」）。

    与其它单位（`行`/`件`/`个`/`%`）一样紧贴数字不加空格 —— 保持全表同一种排版。
    """
    return DATASET_CURRENCY["name"]


# ════════════════════════════════════════════════════════════════════════
# 数据集画像（rows/边界/去重计数都**从数据里算**；`currency` 是声明，见上）
# ════════════════════════════════════════════════════════════════════════
_profile_cache: dict[str, Any] | None = None


def dataset_profile() -> dict[str, Any]:
    """数据集的真实边界与规模（忠实说明"数据支持什么、不支持什么"要用到）。

    为什么不在代码里写 `DATA_LAST_DAY = 2011-12-09` 这种常量：数据集是外部资产，
    写死的常量一旦与文件脱钩（换了数据忘了改常量），所有"数据边界"的话就都成了假话。
    这里每次从 `loader.load_raw()` 现算（loader 自己有进程级缓存，不重复读盘）。

    两处例外，都是**声明**而不是"算出来的"，如实摆在画像里（`has_region_field`
    与 `currency`）：数据里没有的信息不能假装是从数据里读的。
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
        "currency": dict(DATASET_CURRENCY),
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


def warm_up() -> dict[str, Any]:
    """把数据集读进**进程级缓存**（冷启动预热；服务启动时在后台线程里调一次）。

    为什么需要它：`loader.load_raw()` 第一次要读 22MB Excel（本机约 100 秒），
    冷启动后的**第一问**全卡在这一步上（热态只要 7~10 秒，因为缓存已建好）。
    预热只是**把这一次读盘提前到启动时**，读表/口径/计算的代码路径一个字没改：
    确定性与"第一次提问时现读"完全一致（同一个 `load_raw()`，同一个 `_CACHE`）。

    返回一份人看的简报（行数/边界/币种/耗时），供启动日志与测试断言用。
    """
    started = time.perf_counter()
    rows = int(len(loader.load_raw()))          # ① 最贵的一步：22MB Excel 读盘 + 形状校验
    profile = dataset_profile()                 # ② 画像（三列 nunique 扫描），顺带确认边界算得出来
    return {
        "rows": rows,
        "column_count": profile["column_count"],
        "first_day": profile["first_day"],
        "last_day": profile["last_day"],
        "currency": profile["currency"]["code"],
        "seconds": round(time.perf_counter() - started, 1),
    }


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
        "口径：含首尾全天；排除取消单（单号以 C 开头）、数量≤0、单价≤0 的行。",
        f"客户数只统计 CustomerID 非空的有效行；有效行里有 {customers_null_rows} 行没有客户号，"
        f"它们计入销售额但计不进客户数（客户号为空的行不排除）。",
    ] + _empty_window_note(int(base["rows_in_range"]), start, end)

    return {
        "tool": "sales_summary",
        "params": {"start": start.isoformat(), "end": end.isoformat()},
        "facts": facts,
        "notes": notes,
        "display": [
            {"label": "销售额", "value": amount, "unit": currency_unit(), "format": "money"},
            {"label": "订单数（去重发票号）", "value": order_count, "unit": "单", "format": "int"},
            {"label": "客户数（去重客户号）", "value": customer_count, "unit": "位", "format": "int"},
            {"label": "客单价（销售额 ÷ 订单数）", "value": avg_order, "unit": currency_unit(), "format": "money",
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
        # 「共 N 个点」而不是「共 N 个天/周」—— 后者读出来是病句（TASK-010 露出）
        f"按{bucket_label}聚合，共 {len(points)} 个点；空档（没有销售的日子/周）**不补零**，"
        "所以点数可能少于自然天数/周数。",
        "口径：含首尾全天；排除取消单、数量≤0、单价≤0 的行。",
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
            {"label": "区间销售额合计", "value": total, "unit": currency_unit(), "format": "money"},
            {"label": f"{bucket_label}数", "value": len(points), "unit": "个", "format": "int"},
            {"label": f"最高一个{bucket_label}", "value": facts["max_bucket_amount"], "unit": currency_unit(),
             "format": "money",
             "note": f"起始 {facts['max_bucket_start']}" if facts["max_bucket_start"] else ""},
            {"label": f"最低一个{bucket_label}", "value": facts["min_bucket_amount"], "unit": currency_unit(),
             "format": "money",
             "note": f"起始 {facts['min_bucket_start']}" if facts["min_bucket_start"] else ""},
            {"label": f"{bucket_label}均额", "value": facts["avg_bucket_amount"], "unit": currency_unit(),
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
        "口径：含首尾全天；排除取消单、数量≤0、单价≤0 的行。",
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
            {"label": f"TOP{len(items)} 合计销售额", "value": top_amount, "unit": currency_unit(), "format": "money"},
            {"label": "占区间总销售额", "value": facts["top_share"] * 100, "unit": "%", "format": "pct",
             "derived": True},
            {"label": "区间总销售额", "value": detail_total, "unit": currency_unit(), "format": "money"},
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
# 工具 ④：sales_compare（两区间比较 + 受控归因）—— TASK-005
# ════════════════════════════════════════════════════════════════════════
COMPARISON_TYPES: tuple[str, ...] = ("custom", "wow", "mom", "yoy")

# 归因维度白名单：**只认数据集里真实存在的列**。
# 区域/省份/城市/门店/渠道/销售员不在这张表里 —— 所以「拿 Country 顶替区域」
# 在**参数层**就走不通（intent 层的关键词硬闸门已经先拦了一道，这是第二道）。
ATTRIBUTION_DIMENSIONS: dict[str, str] = {"country": "Country", "stock_code": "StockCode"}

# 正/负贡献者各取前几名 —— **常量只放这一处**，不散落到调用点
ATTRIBUTION_TOP_N = 3

# 贡献率的分母是**总变化额**，不是总销售额 —— 这句话必须跟着数字一起出现，
# 否则用户会把 150% 读成"占了全部销售额的 150%"（评审点名的误读风险）。
CONTRIBUTION_DENOMINATOR_NOTE = (
    "贡献率 = 该维度的变化额 ÷ **总变化额**（不是总销售额）；"
    "大于 100% 或为负在数学上是正常的（某个维度涨得比总体多、或有维度在反向拉）。"
)


def _month_window(day: _dt.date) -> tuple[_dt.date, _dt.date]:
    """day 所在的自然月（1 日 ~ 月末）。"""
    start = day.replace(day=1)
    end = (start + _dt.timedelta(days=32)).replace(day=1) - _dt.timedelta(days=1)
    return start, end


def _week_window(day: _dt.date) -> tuple[_dt.date, _dt.date]:
    """day 所在的自然周（**周一起算**，与 WEEK_START_WEEKDAY 一致）。"""
    start = day - _dt.timedelta(days=day.weekday())
    return start, start + _dt.timedelta(days=6)


def _prev_month_window(day: _dt.date) -> tuple[_dt.date, _dt.date]:
    return _month_window(day.replace(day=1) - _dt.timedelta(days=1))


def _prev_week_window(day: _dt.date) -> tuple[_dt.date, _dt.date]:
    return _week_window(day - _dt.timedelta(days=7))


def _same_day_last_year(day: _dt.date) -> _dt.date | None:
    """去年同一天。2 月 29 日在去年不存在 → 返回 None（**不猜、不补成 28 日**）。"""
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return None


def _insufficient(reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": "insufficient_data", "reason": reason, **extra}


def resolve_compare_windows(
    *,
    comparison_type: str,
    current_start: _dt.date | None,
    current_end: _dt.date | None,
    previous_start: _dt.date | None,
    previous_end: _dt.date | None,
    first: _dt.date,
    last: _dt.date,
) -> dict[str, Any]:
    """把「比较类型 + 问题里的日期」解析成两个窗口，并**判断到底能不能比**。

    这是**纯函数**（只吃日期、要 first/last 边界，不碰数据），所以能被单元测试
    用构造的边界喂进去，把每一种"数据不足"的情形钉死。

    三条规则：
      ① 两个区间都给了 → 用用户给的，**一个字节都不动**（不截断、不挪动）。
         只要有一个没被数据集完全覆盖 → `insufficient_data` + 明确原因。
      ② 只给了本期（或什么都没给）→ 由 comparison_type 推：
         wow = 含数据最后一天的自然周；mom/yoy = 含最后一天的自然月。
         · wow/mom 的本期若被数据边界切断 → 退到**最近一个完整周期**并写明
           （拿 9 天比 30 天得出的"环比 -51%"是假数字，宁可换个基准也不能给）。
         · yoy **不退**：用户问的是哪一年哪个月就是哪个月，不完整就如实说数据不足。
      ③ 上一期：
         · 本期是"按日历推出来的" → 用紧邻的上一个自然周/月（等长）
         · 本期是"用户给的" → 按同长度往前挪（周挪 7 天、月挪 1 个月、年挪 1 年）
    """
    if comparison_type not in COMPARISON_TYPES:
        return _insufficient(f"不认识的比较类型：{comparison_type!r}（只支持 {list(COMPARISON_TYPES)}）")

    notes: list[str] = []
    adjusted = False
    current_explicit = bool(current_start and current_end)
    previous_explicit = bool(previous_start and previous_end)

    # ── 本期 ──────────────────────────────────────────────────────────
    if current_explicit:
        current = (current_start, current_end)
        notes.append(f"本期区间 `{current_start} ~ {current_end}` 来自问题里的明确日期（程序没有改它）。")
    else:
        if comparison_type == "custom":
            return _insufficient("自定义比较必须给出两个区间（`custom` 缺日期）。")
        if comparison_type == "wow":
            current = _week_window(last)
        else:
            current = _month_window(last)
        if comparison_type in ("wow", "mom"):
            derived = current
            moved, move_notes = _walk_back_to_complete(current, comparison_type, first, last)
            current, notes = moved, notes + move_notes
            adjusted = moved != derived
        else:
            notes.append(
                f"本期取数据最后一天（{last}）所在的自然月 `{current[0]} ~ {current[1]}`。"
            )

    # ── 上一期 ────────────────────────────────────────────────────────
    if previous_explicit:
        previous = (previous_start, previous_end)
        notes.append(f"上一期区间 `{previous_start} ~ {previous_end}` 来自问题里的明确日期（程序没有改它）。")
    elif comparison_type == "custom":
        return _insufficient("自定义比较必须给出两个区间（`custom` 缺上一期）。")
    elif comparison_type == "wow":
        previous = (
            (current[0] - _dt.timedelta(days=7), current[1] - _dt.timedelta(days=7))
            if current_explicit
            else _prev_week_window(current[0])
        )
    elif comparison_type == "mom":
        previous = (
            _shift_month_span(current[0], current[1], -1) if current_explicit else _prev_month_window(current[0])
        )
    else:                                    # yoy
        shifted_start = _same_day_last_year(current[0])
        shifted_end = _same_day_last_year(current[1])
        if shifted_start is None or shifted_end is None:
            return _insufficient(
                f"无法做同比：本期区间 `{current[0]} ~ {current[1]}` 里有 2 月 29 日，"
                f"去年没有这一天，强行对齐就是编数据。"
            )
        previous = (shifted_start, shifted_end)

    # ── 能不能比：两个窗口都必须被数据集**完全覆盖** ──────────────────
    covered, problem = _coverage_problem(
        comparison_type, current, previous, first, last, current_explicit
    )
    if not covered:
        return _insufficient(problem, current=_span(current), previous=_span(previous))

    same_length = (current[1] - current[0]) == (previous[1] - previous[0])
    if same_length:
        notes.append(
            f"两个区间等长（各 {(current[1] - current[0]).days + 1} 天），是**可比的等长窗口**。"
        )
    return {
        "status": "ok",
        "reason": "",
        "current": _span(current, adjusted=adjusted),
        "previous": _span(previous),
        "same_length": same_length,
        "notes": notes,
    }


def _span(window: tuple[_dt.date, _dt.date], *, adjusted: bool = False) -> dict[str, Any]:
    """窗口的机器可读形态。`adjusted=True` 表示本期被程序挪到过（原因在 notes 里）。"""
    start, end = window
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": (end - start).days + 1,
        "adjusted": adjusted,
    }


def _walk_back_to_complete(
    window: tuple[_dt.date, _dt.date], kind: str, first: _dt.date, last: _dt.date
) -> tuple[tuple[_dt.date, _dt.date], list[str]]:
    """本期被数据边界切断时，退到**最近一个完整周期**（并如实写明为什么）。"""
    start, end = window
    if start >= first and end <= last:
        return window, [f"本期取数据最后一天所在的自然{'周' if kind == 'wow' else '月'} `{start} ~ {end}`。"]
    original = f"{start} ~ {end}"
    steps = 0
    while (end > last or start < first) and steps < 400:
        if kind == "wow":
            start, end = start - _dt.timedelta(days=7), end - _dt.timedelta(days=7)
        else:
            (start, end) = _prev_month_window(start)
        steps += 1
    return (start, end), [
        f"**本期基准被改过**：数据最后一天 {last} 落在不完整的 `{original}` 里"
        f"（该{'周' if kind == 'wow' else '月'}没走完），拿它跟完整的上期比会得出失真的{'环比' if kind == 'mom' else '周环比'}，"
        f"因此本期退到**最近一个完整{'周' if kind == 'wow' else '月'}**：`{start} ~ {end}`。"
    ]


def _shift_month_span(start: _dt.date, end: _dt.date, months: int) -> tuple[_dt.date, _dt.date]:
    """把区间整体往前挪 N 个月（日号越界时夹到当月最后一天）。"""

    def shift(day: _dt.date) -> _dt.date:
        total = day.year * 12 + (day.month - 1) + months
        year, month = divmod(total, 12)
        month += 1
        last_day = (_dt.date(year, month, 1) + _dt.timedelta(days=32)).replace(day=1) - _dt.timedelta(days=1)
        return day.replace(year=year, month=month, day=min(day.day, last_day.day))

    return shift(start), shift(end)


def _coverage_problem(
    comparison_type: str,
    current: tuple[_dt.date, _dt.date],
    previous: tuple[_dt.date, _dt.date],
    first: _dt.date,
    last: _dt.date,
    current_explicit: bool,
) -> tuple[bool, str]:
    """两个窗口是否都被数据集完全覆盖？没有 → 返回**能读懂的原因**（不猜不补不偷偷截断）。"""
    for label, window in (("本期", current), ("上一期", previous)):
        start, end = window
        if start >= first and end <= last:
            continue
        if comparison_type == "yoy" and label == "本期" and start.day == 1 and start.year == last.year:
            # 指令点名要的那句话：整月同比但该月没走完
            return False, (
                f"无法进行完整月度同比：{start.year} 年 {start.month} 月数据仅覆盖至 "
                f"{last.month} 月 {last.day} 日（数据集范围 {first} ~ {last}）。"
                f"若要看等长窗口，可以比 `{start} ~ {last}` 与去年同期的 "
                f"`{start.replace(year=start.year - 1)} ~ {last.replace(year=last.year - 1)}`。"
            )
        if start < first:
            return False, (
                f"无法比较：{label}区间 `{start} ~ {end}` 早于数据集起点 {first}，"
                f"数据集里没有这段数据（不猜、不补、不偷偷截断）。"
            )
        extra = "" if current_explicit else "（该周期在数据集里没走完）"
        return False, (
            f"无法比较：{label}区间 `{start} ~ {end}` 没有被数据集完全覆盖{extra}，"
            f"数据集只到 {last}（不猜、不补、不偷偷截断）。"
        )
    return True, ""


# ── 归因：分组求和 → 代码排序 → 硬校验 ───────────────────────────────────
def _group_amounts(rows: pd.DataFrame, field: str) -> dict[str, float]:
    """按某个维度列分组求金额（NaN 归到「(缺失)」，不让它变出一个 "nan" 名字）。"""
    if rows.empty:
        return {}
    grouped = rows.groupby(field, dropna=False)["_amount"].sum()
    return {_dimension_name(key): float(value) for key, value in grouped.items()}


def _group_labels(rows: pd.DataFrame, field: str, keys: list[str]) -> dict[str, str]:
    """给每个维度值配一个**展示名**（StockCode 用出现次数最多的 Description）。"""
    if field != "StockCode" or rows.empty:
        return {}
    labels: dict[str, str] = {}
    for key, group in rows.groupby(field, dropna=False):
        name = _dimension_name(key)
        if name in keys:
            labels[name] = _dominant_description(group["Description"].dropna())
    return labels


def _dimension_name(key: Any) -> str:
    return "(缺失)" if pd.isna(key) else str(key)


def _rank_contributors(records: list[dict[str, Any]], top_n: int) -> tuple[list, list]:
    """**代码排序**：按 |变化额| 降序取正/负贡献者各 top_n 个。

    tie-break 用维度值字典序 —— 不然金额相同的两个维度谁排前面取决于 pandas
    的内部顺序，"同一个问题问两次给出不同名单"就可能发生。
    """
    ordered = sorted(records, key=lambda item: (-abs(item["delta"]), item["name"]))
    positive = [item for item in ordered if item["delta"] > _FLOAT_TOL][:top_n]
    negative = [item for item in ordered if item["delta"] < -_FLOAT_TOL][:top_n]
    return positive, negative


def build_attribution(
    dimension: str,
    current_rows: pd.DataFrame,
    previous_rows: pd.DataFrame,
    total_delta: float,
    *,
    current_total: float | None = None,
    previous_total: float | None = None,
    top_n: int = ATTRIBUTION_TOP_N,
) -> dict[str, Any]:
    """归因的**全部数学**都在这里：分组 → delta → 排序 → 贡献率 → 一致性硬校验。

    一致性校验为什么是硬闸门：`total_delta` 来自 executor（独立路径），
    `Σ delta` 来自这里的 groupby。两条路径对不上，说明其中一条错了 ——
    那时候**任何一个数字都不能给用户**，所以 `passed=False` 时调用方必须整段丢掉。
    """
    if dimension not in ATTRIBUTION_DIMENSIONS:
        raise ValueError(f"不支持的归因维度：{dimension!r}（白名单：{sorted(ATTRIBUTION_DIMENSIONS)}）")
    field = ATTRIBUTION_DIMENSIONS[dimension]

    current = _group_amounts(current_rows, field)
    previous = _group_amounts(previous_rows, field)
    names = sorted(set(current) | set(previous))
    labels = _group_labels(pd.concat([current_rows, previous_rows], ignore_index=True), field, names)

    records: list[dict[str, Any]] = []
    for name in names:
        now, before = current.get(name, 0.0), previous.get(name, 0.0)
        records.append({
            "dimension": dimension,
            "name": name,
            "label": labels.get(name, ""),
            "current": now,
            "previous": before,
            "delta": now - before,
        })

    sum_delta = math.fsum(item["delta"] for item in records)
    tolerance = max(_FLOAT_TOL, abs(total_delta) * _FLOAT_TOL)
    consistency: dict[str, Any] = {
        "policy": "Σ(各维度 delta) == total_delta（total_delta 由 executor 独立算出，不是这里加的）",
        "dimension_count": len(records),
        "sum_of_dimension_deltas": sum_delta,
        "total_delta": total_delta,
        "delta": abs(sum_delta - total_delta),
        "tolerance": tolerance,
        "passed": abs(sum_delta - total_delta) <= tolerance,
    }
    if current_total is not None:
        consistency["sum_of_current"] = math.fsum(item["current"] for item in records)
        consistency["current_total"] = current_total
        consistency["current_matches"] = abs(consistency["sum_of_current"] - current_total) <= tolerance
        consistency["passed"] = consistency["passed"] and consistency["current_matches"]
    if previous_total is not None:
        consistency["sum_of_previous"] = math.fsum(item["previous"] for item in records)
        consistency["previous_total"] = previous_total
        consistency["previous_matches"] = abs(consistency["sum_of_previous"] - previous_total) <= tolerance
        consistency["passed"] = consistency["passed"] and consistency["previous_matches"]

    positive, negative = _rank_contributors(records, top_n)
    for item in records:
        # previous==0 / 总变化为 0 → 一律 not_available，**不放 Infinity/NaN 出去**
        share, status = _share_of_change(item["delta"], total_delta)
        item["contribution_share_of_change"] = share
        item["contribution_share_status"] = status

    return {
        "dimension": dimension,
        "dimension_field": field,
        "top_n": int(top_n),
        "total_delta": total_delta,
        "contributor_count": len(records),
        "positive_contributors": positive,
        "negative_contributors": negative,
        "consistency": consistency,
        "passed": bool(consistency["passed"]),
        "notes": [CONTRIBUTION_DENOMINATOR_NOTE],
    }


def _share_of_change(delta: float, total_delta: float) -> tuple[float | None, str]:
    """贡献率 = delta / 总变化额。总变化为 0 → `(None, "not_available")`，绝不出 Infinity。"""
    if total_delta == 0:
        return None, "not_available"
    return delta / total_delta, "ok"


def _change_of(current: float, previous: float) -> dict[str, Any]:
    """变化额 + 变化率（previous == 0 → 变化率 `not_available`）。"""
    change = current - previous
    rate, status = (None, "not_available") if previous == 0 else (change / previous, "ok")
    return {"change": change, "rate": rate, "rate_status": status}


def sales_compare(
    *,
    comparison_type: str = "custom",
    current_start: _dt.date | None = None,
    current_end: _dt.date | None = None,
    previous_start: _dt.date | None = None,
    previous_end: _dt.date | None = None,
    attribution_dimension: str | None = None,
) -> dict[str, Any]:
    """两个区间比大小（本期 vs 上一期），可选按某个维度做**代码归因**。

    金额一律来自 `sales_summary`（→ 冻结资产 executor），所以天然满足 AC-10 的位级相等。
    """
    first, last = dataset_bounds()
    resolution = resolve_compare_windows(
        comparison_type=comparison_type,
        current_start=current_start,
        current_end=current_end,
        previous_start=previous_start,
        previous_end=previous_end,
        first=first,
        last=last,
    )
    params = {
        "comparison_type": comparison_type,
        "current_start": current_start.isoformat() if current_start else None,
        "current_end": current_end.isoformat() if current_end else None,
        "previous_start": previous_start.isoformat() if previous_start else None,
        "previous_end": previous_end.isoformat() if previous_end else None,
        "attribution_dimension": attribution_dimension,
    }

    if resolution["status"] != "ok":
        # 数据不足 → 明确拒绝，**一个变化额都不给**（不给用户半个答案）
        return {
            "tool": "sales_compare",
            "params": params,
            "status": "insufficient_data",
            "facts": {
                "comparison_type": comparison_type,
                "comparison_status": "insufficient_data",
                "attribution_dimension": attribution_dimension,
            },
            "notes": [resolution["reason"], "数据不足时不给变化额与变化率 —— 不猜、不补、不偷偷截断。"],
            "display": [
                {"label": "比较类型", "value": comparison_type, "unit": "", "format": "text"},
                {"label": "比较结果", "value": "数据不足，未做比较", "unit": "", "format": "text",
                 "note": resolution["reason"]},
            ],
            "selfcheck": {"windows_resolved": False, "reason": resolution["reason"]},
        }

    current, previous = resolution["current"], resolution["previous"]
    current_window = (_dt.date.fromisoformat(current["start"]), _dt.date.fromisoformat(current["end"]))
    previous_window = (_dt.date.fromisoformat(previous["start"]), _dt.date.fromisoformat(previous["end"]))
    now = sales_summary(*current_window)
    before = sales_summary(*previous_window)
    cur, prev = now["facts"], before["facts"]

    amount_change = _change_of(cur["sales_amount"], prev["sales_amount"])
    changes = {
        "order_count": _change_of(cur["order_count"], prev["order_count"]),
        "customer_count": _change_of(cur["customer_count"], prev["customer_count"]),
        "avg_order_amount": _change_of(cur["avg_order_amount"], prev["avg_order_amount"]),
    }

    # ── 归因（可选）：算 → 排序 → **硬校验**，过不了就整段丢弃 ──────────
    attribution: dict[str, Any] | None = None
    rejected: dict[str, Any] | None = None
    if attribution_dimension:
        rows_now = _valid_rows(*current_window)
        rows_before = _valid_rows(*previous_window)
        candidate = build_attribution(
            attribution_dimension,
            rows_now,
            rows_before,
            amount_change["change"],
            current_total=cur["sales_amount"],
            previous_total=prev["sales_amount"],
            top_n=ATTRIBUTION_TOP_N,
        )
        if candidate["passed"]:
            attribution = candidate
        else:
            rejected = {
                "reason": "归因一致性校验未通过（Σ各维度变化额 ≠ 总变化额），按规则**整段归因不进入回答**。",
                "consistency": candidate["consistency"],
            }

    facts: dict[str, Any] = {
        "comparison_type": comparison_type,
        "comparison_status": "ok",
        "current_period": current,
        "previous_period": previous,
        "same_length": resolution["same_length"],
        "current": {"sales_amount": cur["sales_amount"], "order_count": cur["order_count"],
                    "customer_count": cur["customer_count"], "avg_order_value": cur["avg_order_amount"]},
        "previous": {"sales_amount": prev["sales_amount"], "order_count": prev["order_count"],
                     "customer_count": prev["customer_count"], "avg_order_value": prev["avg_order_amount"]},
        "change_amount": amount_change["change"],
        "change_rate": amount_change["rate"],
        "change_rate_status": amount_change["rate_status"],
        # 别名：AC 里同时出现过 delta/rate 与 change_amount/change_rate 两种叫法，都给
        "delta": amount_change["change"],
        "rate": amount_change["rate"],
        "changes": {key: {"change": value["change"], "rate": value["rate"],
                          "rate_status": value["rate_status"]} for key, value in changes.items()},
        "attribution_dimension": attribution_dimension,
        "attribution_status": ("ok" if attribution else ("rejected_inconsistent" if rejected else None)),
        "total_delta": amount_change["change"],
        "positive_contributors": attribution["positive_contributors"] if attribution else [],
        "negative_contributors": attribution["negative_contributors"] if attribution else [],
    }

    notes = list(resolution["notes"]) + [
        "口径：含首尾全天；排除取消单（单号以 C 开头）、数量≤0、单价≤0 的行。",
        "变化率 = 变化额 ÷ 上一期销售额；上一期为 0 时输出 `not_available`（不做除零）。",
    ]
    if attribution:
        notes.append(CONTRIBUTION_DENOMINATOR_NOTE)
    if rejected:
        notes.append(rejected["reason"] + "（细节见 attribution_rejected.consistency）")
    if not int(cur["rows_in_range"]) or not int(prev["rows_in_range"]):
        empty = "本期" if not int(cur["rows_in_range"]) else "上一期"
        window = current_window if empty == "本期" else previous_window
        notes.append(
            f"{empty}区间（{window[0]} ~ {window[1]}）在数据集里**一行交易都没有**，"
            f"它的金额是 0 —— 这是数据事实，不是计算失败。"
        )

    display = [
        {"label": "本期区间", "value": f"{current['start']} ~ {current['end']}"
                                        f"（{current['days']} 天）", "unit": "", "format": "text",
         "note": "本期基准被程序挪到最近一个完整周期（原因见下方口径说明）" if current.get("adjusted") else ""},
        {"label": "上一期区间", "value": f"{previous['start']} ~ {previous['end']}"
                                          f"（{previous['days']} 天）", "unit": "", "format": "text"},
        {"label": "本期销售额", "value": cur["sales_amount"], "unit": currency_unit(), "format": "money"},
        {"label": "上一期销售额", "value": prev["sales_amount"], "unit": currency_unit(), "format": "money"},
        {"label": "销售额变化额（本期 − 上期）", "value": amount_change["change"], "unit": currency_unit(),
         "format": "money_signed", "derived": True},
    ]
    display.append(_rate_display("销售额变化率（变化额 ÷ 上期）", amount_change))
    display += [
        _count_display("本期订单数", cur["order_count"], "单"),
        _count_display("上一期订单数", prev["order_count"], "单"),
        _count_display("本期客户数", cur["customer_count"], "位"),
        _count_display("上一期客户数", prev["customer_count"], "位"),
        {"label": "本期客单价", "value": cur["avg_order_amount"], "unit": currency_unit(), "format": "money",
         "derived": True},
        {"label": "上一期客单价", "value": prev["avg_order_amount"], "unit": currency_unit(), "format": "money",
         "derived": True},
    ]
    if attribution_dimension:
        display.append({
            "label": "归因维度",
            "value": f"{ATTRIBUTION_DIMENSIONS[attribution_dimension]}"
                     f"（正/负贡献者各取前 {ATTRIBUTION_TOP_N} 名，由程序排序选出）"
                     if attribution else "归因结果未通过一致性校验，已整段丢弃",
            "unit": "", "format": "text",
        })

    return {
        "tool": "sales_compare",
        "params": params,
        "status": "ok",
        "facts": facts,
        "attribution": attribution,
        "attribution_rejected": rejected,
        "notes": notes,
        "display": display,
        "selfcheck": {
            "window_source": "代码解析（见 resolve_compare_windows），日期不由 LLM 解释",
            "amount_source": "executor.compute_sales_amount（冻结资产），两个区间各调一次",
            "current_amount": cur["sales_amount"],
            "previous_amount": prev["sales_amount"],
            "change_amount": amount_change["change"],
            "rate_not_available": amount_change["rate_status"] == "not_available",
            "attribution_consistency": attribution["consistency"] if attribution else
                                       (rejected["consistency"] if rejected else None),
            "attribution_passed": bool(attribution),
        },
    }


def _rate_display(label: str, change: dict[str, Any]) -> dict[str, Any]:
    """变化率那一行：`not_available` 时**不显示数字**，显示一句人能读的原因。"""
    if change["rate_status"] == "not_available":
        return {"label": label, "value": "不适用（上一期为 0，未做除零）", "unit": "", "format": "text",
                "note": "not_available"}
    return {"label": label, "value": change["rate"] * 100, "unit": "%", "format": "pct_signed", "derived": True}


def _count_display(label: str, value: Any, unit: str) -> dict[str, Any]:
    return {"label": label, "value": value, "unit": unit, "format": "int"}


# ════════════════════════════════════════════════════════════════════════
# 工具 ⑤：sales_breakdown_by_country（只做分布，**不承担变化归因**）
#
# 「某时段谁卖得多」和「两个时段之间谁造成了变化」是两个问题，别混：
# 前者归这里，后者归 sales_compare 的 attribution（评审明确要求分开）。
# ════════════════════════════════════════════════════════════════════════
def sales_breakdown_by_country(start: _dt.date, end: _dt.date, top_n: int = 5) -> dict[str, Any]:
    """某时间段各国家的销售额分布：TOP N + 占比 + **总额一致性**。"""
    rows = _valid_rows(start, end)
    total = _amount_of(rows)

    if rows.empty:
        grouped_total, items = 0.0, []
    else:
        table = rows.groupby("Country", dropna=False).agg(
            amount=("_amount", "sum"),
            orders=("InvoiceNo", "nunique"),
            customers=("CustomerID", "nunique"),
            rows=("_amount", "size"),
        )
        table = table.sort_values(["amount", "Country"], ascending=[False, True], kind="mergesort")
        grouped_total = float(table["amount"].sum())
        items = [
            {
                "rank": rank,
                "country": _dimension_name(index),
                "amount": float(row["amount"]),
                "share": (float(row["amount"]) / grouped_total) if grouped_total else 0.0,
                "orders": int(row["orders"]),
                "customers": int(row["customers"]),
                "rows": int(row["rows"]),
            }
            for rank, (index, row) in enumerate(table.head(top_n).iterrows(), start=1)
        ]

    top_amount = math.fsum(item["amount"] for item in items)
    facts: dict[str, Any] = {
        "total_amount": total,
        "grouped_total": grouped_total,
        "country_count": int(len(items)) if rows.empty else int(rows["Country"].nunique(dropna=False)),
        "top_n": int(top_n),
        "top_amount": top_amount,
        "top_share": (top_amount / grouped_total) if grouped_total else 0.0,
        "top_country": items[0]["country"] if items else "",
        "top_country_amount": items[0]["amount"] if items else 0.0,
    }

    notes = [
        "口径：含首尾全天；排除取消单、数量≤0、单价≤0 的行。",
        "按 Country 分组（数据集里真实存在的国家字段）。**只描述这个时间段内的分布**，"
        "不承担「两个时间段之间谁造成变化」的归因（那个问题归两区间比较的归因能力）。",
        "**国家不是区域**：数据集没有区域/省份/城市/门店/渠道字段，本工具也不会拿国家顶着用。",
    ] + _empty_window_note(len(rows), start, end)

    return {
        "tool": "sales_breakdown_by_country",
        "params": {"start": start.isoformat(), "end": end.isoformat(), "top_n": int(top_n)},
        "status": "ok",
        "facts": facts,
        "items": items,
        "notes": notes,
        "display": [
            {"label": "区间总销售额", "value": total, "unit": currency_unit(), "format": "money"},
            {"label": "上榜国家数", "value": len(items), "unit": "个", "format": "int"},
            {"label": f"TOP{len(items)} 合计销售额", "value": top_amount, "unit": currency_unit(),
             "format": "money"},
            {"label": "占区间总销售额", "value": facts["top_share"] * 100, "unit": "%", "format": "pct",
             "derived": True},
            {"label": "区间内出现过的国家数", "value": facts["country_count"], "unit": "个", "format": "int"},
        ],
        "selfcheck": {
            "detail_total_source": "同一掩码下 line_amount(rows).sum()",
            "detail_total": total,
            "grouped_total": grouped_total,
            "grouped_matches_detail": abs(grouped_total - total) <= max(_FLOAT_TOL, abs(total) * _FLOAT_TOL),
            "delta": abs(grouped_total - total),
            "top_amount_le_total": top_amount <= total + _FLOAT_TOL,
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
        description="某时间段的销售额 / 订单数 / 客户数（销售额由既有计算引擎算出，口径见 metrics）",
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
    "sales_compare": ToolSpec(
        name="sales_compare",
        title="两区间比较",
        description="两个时间段比大小（custom/wow/mom/yoy）：销售额/订单数/客户数/客单价 + "
                    "变化额 + 变化率；可选按 country 或 stock_code 做**代码归因**",
        run=sales_compare,
    ),
    "sales_breakdown_by_country": ToolSpec(
        name="sales_breakdown_by_country",
        title="国家分布",
        description="某时间段各国家销售额分布 TOP N + 占比（**不做变化归因**）",
        run=sales_breakdown_by_country,
    ),
}


def run_tool(name: str, params: dict[str, Any]) -> dict[str, Any]:
    """按白名单执行工具。名字不在表里 → 明确报错（**绝不"找个相近的凑合"**）。"""
    spec = TOOLS.get(name)
    if spec is None:
        raise KeyError(f"没有这个工具：{name!r}（白名单：{sorted(TOOLS)}）")
    return spec.run(**params)


__all__ = [
    "ATTRIBUTION_DIMENSIONS",
    "ATTRIBUTION_TOP_N",
    "COMPARISON_TYPES",
    "CONTRIBUTION_DENOMINATOR_NOTE",
    "DATASET_CURRENCY",
    "TOOLS",
    "ToolSpec",
    "WEEK_START_WEEKDAY",
    "build_attribution",
    "currency_unit",
    "dataset_bounds",
    "dataset_profile",
    "reset_cache",
    "resolve_compare_windows",
    "run_tool",
    "sales_breakdown_by_country",
    "sales_compare",
    "sales_summary",
    "sales_trend",
    "top_products",
    "warm_up",
]
