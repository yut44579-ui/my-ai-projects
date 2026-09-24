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
    """清掉画像缓存（测试用：换了数据集要能重算）。

    一并清掉 TASK-006 的"全数据集逐客户活动表"缓存 —— 两张表都来自 `loader.load_raw()`，
    换了数据集只清一张就会出现"画像变了、首购日期还是旧的"这种半新半旧状态。
    """
    global _profile_cache, _activity_cache
    _profile_cache = None
    _activity_cache = None


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


# ── 公开包装（STEP A 的表格查询层用；下划线实现仍只有一处）────────────────
# 为什么要这几行：业务表（客户/产品/销售/原始）必须与问答工具**同一套口径**，
# 但那些实现是下划线私有的。这里给一层有名字的公开入口，表格层不必去摸私有名，
# 也不会有人误以为"表格另有一套算法"。行为 = 直接调用下面这些下划线函数。
def raw_window(start: _dt.date, end: _dt.date) -> pd.DataFrame:
    """区间内的**原始行**（只按时间筛选，不套 D16 排除规则）。"""
    window, _valid = _valid_window(start, end)
    return window


def valid_rows(start: _dt.date, end: _dt.date) -> pd.DataFrame:
    """区间内的**有效行**（D16 三条排除规则全开），带一列算好的 `_amount`。"""
    return _valid_rows(start, end)


def customer_table(rows: pd.DataFrame) -> pd.DataFrame:
    """按客户号汇总（购买次数 = distinct InvoiceNo）—— 与客户分析工具同一处实现。"""
    return _customer_table(rows)


def product_table(rows: pd.DataFrame) -> pd.DataFrame:
    """按 StockCode 汇总 —— 与产品排行 / 商品分析同一处实现。"""
    return _product_table(rows)


def customer_activity() -> dict[str, Any]:
    """整个数据集的逐客户首购/末购（新客与沉睡客户用）—— 带进程级缓存。"""
    return _dataset_customer_activity()


def bucket_of(stamps: pd.Series, granularity: str) -> tuple[pd.Series, str]:
    """时间戳 → 桶起点（**全项目唯一**的分桶规则：日=当天；周=周一起算）。"""
    return _bucket_of(stamps, granularity)


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
# 时间分桶：**全项目唯一**的一处（销售额趋势 / 商品趋势共用）
#
# 为什么必须只有一处：`partial`（桶不完整）与 `covered_start/covered_end`（桶实际覆盖了
# 问句区间里的哪几天）是"别把半截桶当整桶比高低"这条纪律的落点。两处各写一遍，
# 早晚会有一处忘了标 partial —— 那时用户看到的就是一个骗人的标签。
# ════════════════════════════════════════════════════════════════════════
def _bucket_of(stamps: pd.Series, granularity: str) -> tuple[pd.Series, str]:
    """时间戳 → 桶起点序列 + 人话标签（day=当天 00:00；week=**周一**为起点）。"""
    if granularity == "day":
        return stamps.dt.normalize(), "天"
    # 周一为一周起点：先归零到当天 00:00，再减掉"今天是本周第几天"
    offset = (stamps.dt.weekday - WEEK_START_WEEKDAY) % 7
    return stamps.dt.normalize() - pd.to_timedelta(offset, unit="D"), "周（周一为起点）"


def _series_points(
    rows: pd.DataFrame,
    stamps: pd.Series,
    start: _dt.date,
    end: _dt.date,
    granularity: str,
) -> tuple[list[dict[str, Any]], str]:
    """把有效行分桶 → 逐桶点（`partial` / `covered_*` 规则**只有这一处**）。

    桶是"自然"的（整周/整日），但用户问的区间**不一定对齐桶边界**。
    例：问 2011-11-01~11-30 按周看，第一个桶从周一 2011-10-31 开始 ——
    它其实只装了 11-01~11-06 那几天。这里把"桶的完整跨度"和"落在问句区间内的跨度"
    都写出来并标 `partial`，免得一个标签把人引到"10月31日也有这笔钱"的误读上。
    """
    bucket, bucket_label = _bucket_of(stamps, granularity)
    grouped = rows.assign(_bucket=bucket).groupby("_bucket", sort=True)
    points: list[dict[str, Any]] = []
    for key, block in grouped:
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
    return points, bucket_label


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

    # 分桶与"逐桶点"的规则**只有一处实现**（`_series_points`）——
    # TASK-006 的商品趋势复用同一段代码，所以 partial/covered_* 的语义不可能漂移。
    points, bucket_label = _series_points(rows, stamps, start, end, granularity)

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


def _product_table(rows: pd.DataFrame) -> pd.DataFrame:
    """按 StockCode 分组的汇总表（**全项目唯一**的商品分组口径）。

    为什么按 StockCode 而不是 Description 分组：Description 在数据集里有空值、有同码多名，
    按它分组会把同一个商品拆成几行、还会多出一个 "NaN 商品"。StockCode 才是商品身份，
    Description 只作为**展示名**附上（取该码下出现次数最多的那个）。

    TASK-006 的 `product_analysis` 与既有的 `top_products` **共用这一处** ——
    两条路径的分组/求和/展示名取值因此不可能对不上（AC-08 的兼容性由代码结构保证，
    不是靠测试事后对齐）。
    """
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
    return table


def top_products(start: _dt.date, end: _dt.date, top_n: int = 5) -> dict[str, Any]:
    """产品销售排行 TOP N（按 StockCode 分组，销售额降序）。"""
    rows = _valid_rows(start, end)

    table = _product_table(rows)
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
# 工具 ⑥：customer_analysis（TASK-006）
#
# 【一句话】客户维度只做「数据里真有的那几列」能算出来的确定性描述：
#   客户号、成交次数（distinct InvoiceNo）、金额、购买频次、首次/最后一次购买日期。
#
# 【明确不做】（写在这里，免得以后有人顺手加）：
#   ❌ RFM 分群（能算，但分箱口径复杂，收益不足）；❌ 产品关联 support/confidence/lift；
#   ❌ churn 预测/任何 ML；❌ VIP / 客户等级 / 大客户 / 客户行业 / 客户地区 / 客户渠道 /
#      客户生命周期阶段 —— 数据里**根本没有**这些字段，一律 unsupported（见 intent 的硬闸门），
#      **绝不用「销售额 TOP」顶替「VIP TOP」**。
#
# 【CustomerID 空值是一等公民口径（本 TASK 的核心）】
#   · 客户维度指标**只覆盖 CustomerID 非空**的成交行；
#   · 销售额仍按既有口径（D16-6）：CustomerID 为空的行**不排除**、仍计入销售额，
#     只是计不进客户数、也不进任何客户维度指标；
#   · 所以每个客户分析结果都带 `customer_scope`：空/非空行数、客户范围内的金额、
#     区间总金额、**覆盖率（代码算，不是 LLM 算）**。
#     回答里因此能说清「本次客户分析覆盖有 CustomerID 的成交，占全部销售额 XX%」——
#     覆盖范围由系统自己声明，不留给用户猜。
# ════════════════════════════════════════════════════════════════════════
CUSTOMER_OPERATIONS: tuple[str, ...] = (
    "top",
    "purchase_frequency",
    "repeat_rate",
    "new_customers",
    "inactive_customers",
)
# 工具名写成常量（报告/收口闸门/兼容性说明都要引用，散落的字面量迟早拼错一个）
CUSTOMER_ANALYSIS_TOOL = "customer_analysis"
PRODUCT_ANALYSIS_TOOL = "product_analysis"
TOP_PRODUCTS_TOOL = "top_products"

# 整个数据集的逐客户活动表缓存（新客/沉睡都要它；见 `_dataset_customer_activity`）
_activity_cache: dict[str, Any] | None = None

# 排序指标的人话名字（只用在给用户看的说明里；键与 CUSTOMER_TOP_METRICS 一致）
_CUSTOMER_METRIC_LABELS: dict[str, str] = {
    "sales_amount": "销售额",
    "order_count": "订单数（distinct InvoiceNo）",
    "purchase_count": "购买次数（distinct InvoiceNo）",
}
# TOP 榜可用的排序指标。注意：本口径下 order_count 与 purchase_count **是同一件事**
# （都 = 该客户在区间内的 distinct InvoiceNo）—— 两个名字都收，是为了让"问订单数"和
# "问购买次数"得到同一个确定答案，而不是为了造出第二个口径。
CUSTOMER_TOP_METRICS: tuple[str, ...] = ("sales_amount", "order_count", "purchase_count")
DEFAULT_INACTIVE_DAYS = 90

_CUSTOMER_SCOPE_BASIS = (
    "行数按**区间内原始行**统计（与计算引擎 validations 的 customer_id_nulls 同口径）；"
    "金额按**口径有效行**统计（与计算引擎的销售额同口径）。"
)


def _window_and_valid(start: _dt.date, end: _dt.date) -> tuple[pd.DataFrame, pd.DataFrame]:
    """一次取数拿到 `(区间内原始行, 口径有效行)` 两份 —— 客户/退货分析两边都要用。

    为什么退货用**原始行**：退货/取消的行正是被 D16 三条排除规则挡掉的那些行，
    只在有效行上看退货率，看的是一个空集。这个区别写在各自的 notes 里，别混。
    """
    window, valid = _valid_window(start, end)
    rows = window.loc[valid].copy()
    rows["_amount"] = engine_metrics.line_amount(rows)
    return window, rows


def _customer_label(value: Any) -> int | float:
    """客户号的展示形态：整数就按整数写（17850），不是整数才保留小数。

    为什么要转：数据里 CustomerID 是 float64（因为有空值），直接输出会变成 `17850.0` ——
    对着一个整数客户号显示小数，会让人以为客户号有小数部分。
    """
    number = float(value)
    return int(number) if number.is_integer() else number


def _dataset_customer_activity() -> dict[str, Any]:
    """整个数据集的逐客户活动表：首次/最后一次**有效**购买日期 + 空客户号行数。

    「数据集内新客」与「沉睡客户」都需要**全数据集**视角 —— 这是与窗口内指标**两回事**，
    所以单独一处、单独缓存，不跟窗口计算混在一起。

    口径：与 `_valid_window()` 同一套掩码（三条排除规则全开），但**不加时间限制**
    （"整个数据集里第一次买"本来就该看全部数据）。
    """
    global _activity_cache
    if _activity_cache is not None:
        return _activity_cache

    frame = loader.load_raw()
    rules = engine_metrics.resolve_enabled_rules()
    hit_count = pd.concat(
        {rule.key: rule.mask(frame).astype(bool) for rule in rules}, axis=1
    ).sum(axis=1)
    valid = frame.loc[hit_count == 0, ["CustomerID", engine_metrics.TIME_FIELD]]
    stamps = pd.to_datetime(valid[engine_metrics.TIME_FIELD])

    with_customer = valid.loc[valid["CustomerID"].notna()].assign(_stamp=stamps)
    grouped = with_customer.groupby("CustomerID")["_stamp"]
    first = grouped.min()
    last = grouped.max()

    _activity_cache = {
        "first_purchase": {float(key): value.date() for key, value in first.items()},
        "last_purchase": {float(key): value.date() for key, value in last.items()},
        "customer_count": int(len(first)),
        "dataset_rows": int(len(frame)),
        "dataset_customer_id_null_rows": int(frame["CustomerID"].isna().sum()),
        "dataset_customer_id_nonnull_rows": int(frame["CustomerID"].notna().sum()),
    }
    return _activity_cache


def build_customer_scope(
    window: pd.DataFrame, rows: pd.DataFrame, start: _dt.date, end: _dt.date
) -> dict[str, Any]:
    """客户维度的"覆盖范围"声明（**每个**客户分析结果都要带，AC-05）。

    · `customer_id_null_rows` / `customer_id_nonnull_rows`：**区间内原始行**的空/非空客户号行数
      （与 `executor` 的 validations.customer_id_nulls 同口径，可交叉核对）；
    · `valid_customer_id_null_rows` …：**口径有效行**上的同一拆分 —— 金额拆分就是按这一层做的；
    · `customer_scope_sales_amount` / `total_sales_amount` / `customer_scope_sales_share`：
      客户范围内的金额、区间总金额、覆盖率 —— **全部代码算**，不给 LLM 算的机会。
    """
    in_range = int(len(window))
    raw_null = int(window["CustomerID"].isna().sum())
    valid_null = int(rows["CustomerID"].isna().sum())
    valid_nonnull = int(len(rows) - valid_null)
    total_amount = _amount_of(rows)
    scope_amount = float(rows.loc[rows["CustomerID"].notna(), "_amount"].sum())
    null_amount = float(rows.loc[rows["CustomerID"].isna(), "_amount"].sum())
    activity = _dataset_customer_activity()
    return {
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "rows_in_range": in_range,
        "customer_id_null_rows": raw_null,
        "customer_id_nonnull_rows": int(in_range - raw_null),
        "valid_rows": int(len(rows)),
        "valid_customer_id_null_rows": valid_null,
        "valid_customer_id_nonnull_rows": valid_nonnull,
        "customer_scope_sales_amount": scope_amount,
        "null_customer_sales_amount": null_amount,
        "total_sales_amount": total_amount,
        "customer_scope_sales_share": (scope_amount / total_amount) if total_amount else 0.0,
        "dataset_rows": activity["dataset_rows"],
        "dataset_customer_id_null_rows": activity["dataset_customer_id_null_rows"],
        "dataset_customer_id_nonnull_rows": activity["dataset_customer_id_nonnull_rows"],
        "basis": _CUSTOMER_SCOPE_BASIS,
    }


def _customer_table(rows: pd.DataFrame) -> pd.DataFrame:
    """按 CustomerID 汇总的客户表（**全项目唯一**的客户聚合口径）。

        sales_amount  区间内该客户的有效行金额合计
        purchase_count **distinct InvoiceNo**（不是行数！一个订单 20 个商品行仍是 1 次购买）
        order_count    同上 —— 本口径下与 purchase_count 是同一件事，两个名字都留着
        rows           有效行数（只能说明"买了多少种/多少行"，**不代表购买次数**）
    """
    scoped = rows.dropna(subset=["CustomerID"])
    grouped = scoped.groupby("CustomerID", sort=True)
    table = pd.DataFrame(
        {
            "sales_amount": grouped["_amount"].sum(),
            "purchase_count": grouped["InvoiceNo"].nunique(),
            "rows": grouped.size(),
        }
    )
    table["order_count"] = table["purchase_count"]
    table["avg_order_amount"] = table["sales_amount"] / table["purchase_count"]
    table.index.name = "CustomerID"
    return table.reset_index()


def _rank_customers(table: pd.DataFrame, metric: str, top_n: int) -> pd.DataFrame:
    """按指标降序 + **客户号升序**并列打破（确定性的排序，问多少次结果都一样）。"""
    return table.sort_values(
        [metric, "CustomerID"], ascending=[False, True], kind="mergesort"
    ).head(top_n)


def _customer_items(ranked: pd.DataFrame, extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    extra = extra or {}
    return [
        {
            "rank": rank,
            "customer_id": _customer_label(row["CustomerID"]),
            "sales_amount": float(row["sales_amount"]),
            "purchase_count": int(row["purchase_count"]),
            "order_count": int(row["order_count"]),
            "rows": int(row["rows"]),
            "avg_order_amount": float(row["avg_order_amount"]),
            **{key: row[key] for key in extra},
        }
        for rank, (_, row) in enumerate(ranked.iterrows(), start=1)
    ]


def _scope_display(scope: dict[str, Any]) -> list[dict[str, Any]]:
    """每个客户分析都带的那三行 —— 覆盖范围**由代码说清楚**，不让用户猜。"""
    return [
        {"label": "客户分析覆盖的销售额", "value": scope["customer_scope_sales_amount"],
         "unit": currency_unit(), "format": "money"},
        {"label": "区间总销售额（含无客户号行）", "value": scope["total_sales_amount"],
         "unit": currency_unit(), "format": "money"},
        {"label": "客户分析覆盖率", "value": scope["customer_scope_sales_share"] * 100,
         "unit": "%", "format": "pct", "derived": True,
         "note": "有客户号的成交金额 ÷ 区间总销售额"},
    ]


def _scope_notes(scope: dict[str, Any]) -> list[str]:
    return [
        f"**客户范围**：本次客户分析只覆盖**有 CustomerID** 的成交"
        f"（有效行 {scope['valid_customer_id_nonnull_rows']} 行 / "
        f"{scope['customer_scope_sales_amount']:.2f}{currency_unit()}），"
        f"占该区间全部销售额 {scope['customer_scope_sales_share'] * 100:.2f}%；"
        f"客户号为空的 {scope['valid_customer_id_null_rows']} 行有效成交"
        f"（{scope['null_customer_sales_amount']:.2f}{currency_unit()}）"
        f"仍计入销售额，但不计入客户数、也不进任何客户维度指标（D16-6）。",
        f"**购买次数 = 客户号 + distinct InvoiceNo**（不是数据行数）："
        f"同一个订单买了 20 种商品，算 **1 次**购买。{_CUSTOMER_SCOPE_BASIS}",
    ]


def customer_analysis(
    start: _dt.date,
    end: _dt.date,
    operation: str = "top",
    metric: str = "sales_amount",
    top_n: int = 5,
    inactive_days: int = DEFAULT_INACTIVE_DAYS,
    reference_date: _dt.date | None = None,
) -> dict[str, Any]:
    """客户维度的确定性描述性分析（`operation` 决定做哪一种）。

        top                 客户 TOP N（按 sales_amount / order_count / purchase_count 排）
        purchase_frequency  购买频次分布 + 频次最高的客户
        repeat_rate         复购率（复购客户 = 购买次数 ≥ 2）
        new_customers       **数据集内新客**（全数据集首次有效购买落在目标区间）
        inactive_customers  规则型沉睡客户（参考日 − 最后购买日 ≥ 阈值天数）

    所有分支都只覆盖 CustomerID 非空的行，并带同一份 `customer_scope`（AC-05/AC-06）。
    """
    if operation not in CUSTOMER_OPERATIONS:
        raise ValueError(f"customer_analysis 只支持 {list(CUSTOMER_OPERATIONS)}，收到 {operation!r}")
    if metric not in CUSTOMER_TOP_METRICS:
        raise ValueError(f"metric 只支持 {list(CUSTOMER_TOP_METRICS)}，收到 {metric!r}")

    window, rows = _window_and_valid(start, end)
    table = _customer_table(rows)
    scope = build_customer_scope(window, rows, start, end)
    scope_total = float(table["sales_amount"].sum())

    base_params = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "operation": operation,
        "metric": metric,
        "top_n": int(top_n),
    }
    common_facts: dict[str, Any] = {
        "operation": operation,
        "metric_requested": metric,
        "top_n": int(top_n),
        "customers_with_purchase": int(len(table)),
        "customer_scope": scope,
    }
    # 分组求和 = 客户范围金额（对账：客户表没漏行、没重复计）
    selfcheck: dict[str, Any] = {
        "scope_source": "build_customer_scope（同一掩码下的行列拆分）",
        "customer_table_total": scope_total,
        "customer_scope_sales_amount": scope["customer_scope_sales_amount"],
        "table_matches_scope": abs(scope_total - scope["customer_scope_sales_amount"])
        <= max(_FLOAT_TOL, abs(scope_total) * _FLOAT_TOL),
        "delta": abs(scope_total - scope["customer_scope_sales_amount"]),
    }

    items: list[dict[str, Any]] = []
    display: list[dict[str, Any]] = []
    notes: list[str] = _scope_notes(scope)

    if operation == "top":
        ranked = _rank_customers(table, metric, top_n)
        top_amount = float(ranked["sales_amount"].sum())
        facts = {
            **common_facts,
            "metric_used": metric,
            "customer_count": int(len(table)),
            "top_amount": top_amount,
            "all_customers_amount": scope_total,
            "top_share_of_customer_scope": (top_amount / scope_total) if scope_total else 0.0,
            "best_customer_id": _customer_label(ranked["CustomerID"].iloc[0]) if len(ranked) else None,
            "best_amount": float(ranked["sales_amount"].iloc[0]) if len(ranked) else 0.0,
        }
        items = _customer_items(ranked)
        display = [
            {"label": "区间内有成交的客户数", "value": len(table), "unit": "位", "format": "int"},
            {"label": f"TOP{len(items)} 客户合计销售额", "value": top_amount, "unit": currency_unit(),
             "format": "money"},
            {"label": "占客户范围销售额", "value": facts["top_share_of_customer_scope"] * 100,
             "unit": "%", "format": "pct", "derived": True},
            *_scope_display(scope),
        ]
        notes.append(
            f"排序：按{_CUSTOMER_METRIC_LABELS.get(metric, metric)}降序，并列时按客户号升序"
            f"（**排序由代码做**，不是模型挑的）。"
        )
    elif operation == "purchase_frequency":
        counts = table["purchase_count"]
        distribution = (
            table.groupby("purchase_count")
            .agg(customers=("purchase_count", "size"), sales_amount=("sales_amount", "sum"))
            .sort_index()
        )
        repeat_customers = int((counts >= 2).sum())
        distribution_rows = [
            {
                "purchase_count": int(index),
                "customers": int(row["customers"]),
                "customer_share": (float(row["customers"]) / len(table)) if len(table) else 0.0,
                "sales_amount": float(row["sales_amount"]),
                "sales_share": (float(row["sales_amount"]) / scope_total) if scope_total else 0.0,
            }
            for index, row in distribution.iterrows()
        ]
        facts = {
            **common_facts,
            "metric_used": "purchase_count",
            "customer_count": int(len(table)),
            "max_purchase_count": int(counts.max()) if len(counts) else 0,
            "avg_purchase_count": float(counts.mean()) if len(counts) else 0.0,
            "repeat_customers": repeat_customers,
            "repeat_rate": (repeat_customers / len(table)) if len(table) else 0.0,
            "frequency_distribution": distribution_rows,
        }
        ranked = _rank_customers(table, "purchase_count", top_n)
        items = _customer_items(ranked)
        display = [
            {"label": "区间内有成交的客户数", "value": len(table), "unit": "位", "format": "int"},
            {"label": f"购买次数最多的 {len(items)} 位客户", "value": items[0]["purchase_count"] if items else 0,
             "unit": "次", "format": "int", "note": "这是榜首的购买次数"},
            {"label": "最高购买次数", "value": facts["max_purchase_count"], "unit": "次", "format": "int"},
            {"label": "平均购买次数", "value": facts["avg_purchase_count"], "unit": "次", "format": "qty",
             "derived": True},
            {"label": "复购客户数（≥2 次）", "value": repeat_customers, "unit": "位", "format": "int"},
            *_scope_display(scope),
        ]
        notes.append(
            f"频次分布覆盖 {len(distribution_rows)} 种购买次数（1 次 … {facts['max_purchase_count']} 次），"
            f"明细全在 facts.frequency_distribution 里，**没有截断**。"
        )
        notes.append("榜单固定按购买次数（distinct InvoiceNo）降序 —— 频次榜的排序指标不由参数决定。")
    elif operation == "repeat_rate":
        counts = table["purchase_count"]
        repeat = table[counts >= 2]
        repeat_customers = int(len(repeat))
        repeat_amount = float(repeat["sales_amount"].sum())
        facts = {
            **common_facts,
            "metric_used": "purchase_count",
            "total_customers": int(len(table)),
            "repeat_customers": repeat_customers,
            "repeat_rate": (repeat_customers / len(table)) if len(table) else 0.0,
            "single_purchase_customers": int(len(table) - repeat_customers),
            "repeat_sales_amount": repeat_amount,
            "repeat_sales_share": (repeat_amount / scope_total) if scope_total else 0.0,
            "target_definition": "目标区间内至少有一次有效购买、且 CustomerID 非空的客户",
        }
        ranked = _rank_customers(repeat, "purchase_count", top_n)
        items = _customer_items(ranked)
        display = [
            {"label": "客户数（目标区间内有成交）", "value": facts["total_customers"], "unit": "位", "format": "int"},
            {"label": "复购客户数（购买次数 ≥ 2）", "value": repeat_customers, "unit": "位", "format": "int"},
            {"label": "复购率", "value": facts["repeat_rate"] * 100, "unit": "%", "format": "pct", "derived": True,
             "note": "复购客户数 ÷ 客户数"},
            {"label": "只买过一次的客户数", "value": facts["single_purchase_customers"], "unit": "位", "format": "int"},
            {"label": "复购客户贡献的销售额占比", "value": facts["repeat_sales_share"] * 100, "unit": "%",
             "format": "pct", "derived": True},
            *_scope_display(scope),
        ]
        selfcheck["repeat_rate_consistent"] = (
            abs(facts["repeat_rate"] - (repeat_customers / len(table))) < 1e-12 if len(table) else True
        )
        selfcheck["repeat_plus_single_equals_total"] = (
            facts["repeat_customers"] + facts["single_purchase_customers"] == facts["total_customers"]
        )
        notes.append(
            "**复购的定义锁死**：购买次数 = 客户号 + distinct InvoiceNo（不是行数）；"
            "复购客户 = 购买次数 ≥ 2；复购率 = 复购客户数 ÷ 目标区间内有成交的客户数。"
        )
    elif operation == "new_customers":
        activity = _dataset_customer_activity()
        first_map = activity["first_purchase"]
        table = table.assign(first_purchase=table["CustomerID"].map(first_map))
        unmapped = int(table["first_purchase"].isna().sum())
        is_new = (table["first_purchase"] >= start) & (table["first_purchase"] <= end)
        new_table, existing_table = table[is_new], table[~is_new]
        new_amount = float(new_table["sales_amount"].sum())
        facts = {
            **common_facts,
            "metric_used": "sales_amount",
            "total_customers": int(len(table)),
            "new_customers": int(len(new_table)),
            "existing_customers": int(len(existing_table)),
            "new_customer_share": (len(new_table) / len(table)) if len(table) else 0.0,
            "new_sales_amount": new_amount,
            "new_sales_share": (new_amount / scope_total) if scope_total else 0.0,
            "definition": "CustomerID 非空，且该客户在**整个数据集**内的首次有效购买日期落在目标区间内",
            "definition_label": "数据集内新客",
            "existing_definition": "同一批客户里，首次有效购买日期早于目标区间开始的那些（数据集内老客）",
            "first_purchase_basis": "整个数据集内首次有效购买日期（口径同 D16：排除取消单/数量≤0/单价≤0）",
            "dataset_first_day": dataset_bounds()[0].isoformat(),
        }
        ranked = _rank_customers(new_table, "sales_amount", top_n) if len(new_table) else new_table
        items = _customer_items(
            ranked.assign(first_purchase=ranked["first_purchase"].astype(str)), extra=["first_purchase"]
        )
        display = [
            {"label": "数据集内新客", "value": facts["new_customers"], "unit": "位", "format": "int"},
            {"label": "数据集内老客", "value": facts["existing_customers"], "unit": "位", "format": "int"},
            {"label": "新客占比", "value": facts["new_customer_share"] * 100, "unit": "%", "format": "pct",
             "derived": True},
            {"label": "新客带来的销售额", "value": new_amount, "unit": currency_unit(), "format": "money"},
            *_scope_display(scope),
        ]
        selfcheck["first_purchase_all_mapped"] = unmapped == 0
        selfcheck["new_plus_existing_equals_total"] = (
            facts["new_customers"] + facts["existing_customers"] == len(table)
        )
        notes.append(
            f"**「新客」一律指「数据集内新客」**：该客户在**整个数据集**里的首次有效购买落在目标区间内 —— "
            f"**不是**「人生第一次购买」。数据集从 {facts['dataset_first_day']} 才开始，"
            f"在此之前有没有买过，数据里没有、也推不出来。"
        )
    else:  # inactive_customers
        activity = _dataset_customer_activity()
        last_map = activity["last_purchase"]
        reference = reference_date or dataset_bounds()[1]
        table = table.assign(last_purchase=table["CustomerID"].map(last_map))
        unmapped = int(table["last_purchase"].isna().sum())
        table = table.assign(days_since_last_purchase=table["last_purchase"].map(
            lambda day: (reference - day).days
        ))
        dormant = table[table["days_since_last_purchase"] >= inactive_days]
        facts = {
            **common_facts,
            "metric_used": "days_since_last_purchase",
            "inactive_days": int(inactive_days),
            "reference_date": reference.isoformat(),
            "reference_basis": "用户指定" if reference_date else "缺省 = 数据集最后一天（数据的「今天」）",
            "rule": "reference_date − last_purchase_date ≥ inactive_days",
            "total_customers": int(len(table)),
            "inactive_customers": int(len(dormant)),
            "inactive_rate": (len(dormant) / len(table)) if len(table) else 0.0,
            "active_customers": int(len(table) - len(dormant)),
            "max_days_since_purchase": int(table["days_since_last_purchase"].max()) if len(table) else 0,
            "avg_days_since_purchase": float(table["days_since_last_purchase"].mean()) if len(table) else 0.0,
            "last_purchase_basis": "该客户在**整个数据集**内最后一次有效购买日期",
            "definition": "目标区间内有过有效购买、且距参考日已 ≥ inactivity_days 未再购买的客户（规则型判定）",
        }
        ranked = dormant.sort_values(
            ["days_since_last_purchase", "CustomerID"], ascending=[False, True], kind="mergesort"
        ).head(top_n)
        items = _customer_items(
            ranked.assign(last_purchase=ranked["last_purchase"].astype(str)),
            extra=["last_purchase", "days_since_last_purchase"],
        )
        display = [
            {"label": f"沉睡客户数（≥ {inactive_days} 天未购买）", "value": facts["inactive_customers"],
             "unit": "位", "format": "int"},
            {"label": "目标客户数", "value": facts["total_customers"], "unit": "位", "format": "int"},
            {"label": "沉睡占比", "value": facts["inactive_rate"] * 100, "unit": "%", "format": "pct",
             "derived": True},
            {"label": "阈值天数", "value": int(inactive_days), "unit": "天", "format": "int"},
            {"label": "参考日期", "value": reference.isoformat(), "unit": "", "format": "auto"},
            *_scope_display(scope),
        ]
        selfcheck["last_purchase_all_mapped"] = unmapped == 0
        selfcheck["inactive_plus_active_equals_total"] = (
            facts["inactive_customers"] + facts["active_customers"] == facts["total_customers"]
        )
        notes.append(
            f"**沉睡客户是规则型判定，不是预测**：参考日（{reference.isoformat()}）减去该客户"
            f"最后一次有效购买日期 ≥ {inactive_days} 天。没有任何概率、也没有「接下来会不会买」的推断 —— "
            f"本版不做预测/机器学习。"
        )
        notes.append(
            "「最后一次购买」取该客户在**整个数据集**里的最后一次有效购买（不限目标区间）——"
            "目标区间只用来圈定「要观察哪些客户」。"
        )

    notes += _empty_window_note(int(len(rows)), start, end)

    return {
        "tool": "customer_analysis",
        "params": {**base_params, "inactive_days": int(inactive_days),
                   "reference_date": reference_date.isoformat() if reference_date else None},
        "status": "ok",
        "facts": facts,
        "items": items,
        "notes": notes,
        "display": display,
        "selfcheck": selfcheck,
    }


# ════════════════════════════════════════════════════════════════════════
# 工具 ⑦：product_analysis（TASK-006）
#
# operation:
#   top     商品 TOP N —— metric=sales_amount 时**直接复用 top_products 的结果**
#           （同参数逐字一致，兼容性由"同一段代码"保证，不是靠事后对齐）
#   trend   指定商品的逐日/逐周趋势（分桶规则与 sales_trend **同一处实现**）
#   return  退货/取消分析 —— 数量为负 与 C 开头的取消单**两个口径分开**，
#           统一指标必须去重（并集），绝不相加
#
# 明确不做：产品关联（support/confidence/lift，组合爆炸）。
# ════════════════════════════════════════════════════════════════════════
PRODUCT_OPERATIONS: tuple[str, ...] = ("top", "trend", "return")
PRODUCT_TOP_METRICS: tuple[str, ...] = ("sales_amount", "quantity", "order_count")
PRODUCT_TREND_MAX_CODES = 10

_PRODUCT_METRIC_COLUMN = {
    "sales_amount": "amount",
    "quantity": "qty",
    "order_count": "orders",
}


def _product_top(start: _dt.date, end: _dt.date, metric: str, top_n: int) -> dict[str, Any]:
    """商品排行。metric=sales_amount 走既有 `top_products`（保证 AC-08 一致）。"""
    if metric == "sales_amount":
        result = top_products(start, end, top_n)
        # 只改"这次是谁算的"这几个字段，facts/items/display 逐字保留 ——
        # 兼容性因此不是"测试对齐出来的"，而是**同一段代码**算出来的。
        result["tool"] = PRODUCT_ANALYSIS_TOOL
        result["params"] = {**result["params"], "operation": "top", "metric": metric}
        result["facts"] = {**result["facts"], "operation": "top", "metric_used": metric}
        result["notes"] = list(result["notes"]) + [
            f"本结果与「{TOP_PRODUCTS_TOOL}（产品排行）」**同参数完全一致** —— "
            f"两者调的是同一段分组/排序代码，不存在两套商品口径。"
        ]
        result["selfcheck"] = {**result["selfcheck"], "compat_with_top_products": {
            "same_params": True,
            "delegated_to": TOP_PRODUCTS_TOOL,
            "note": "sales_amount 榜直接复用既有产品的分组排序结果（同一个函数调用）",
        }}
        return result

    rows = _valid_rows(start, end)
    table = _product_table(rows)
    column = _PRODUCT_METRIC_COLUMN[metric]
    top = _rank_products(table, metric, top_n)
    total_amount = _amount_of(rows)
    table_total = float(table["amount"].sum())

    items = [
        {
            "rank": rank,
            "stock_code": str(index),
            "description": str(row["description"]),
            "amount": float(row["amount"]),
            "qty": float(row["qty"]),
            "rows": int(row["rows"]),
            "orders": int(row["orders"]),
            "share": (float(row["amount"]) / table_total) if table_total else 0.0,
        }
        for rank, (index, row) in enumerate(top.iterrows(), start=1)
    ]
    lead = items[0] if items else None
    facts: dict[str, Any] = {
        "operation": "top",
        "metric_used": metric,
        "top_n": int(top_n),
        "top_amount": float(top["amount"].sum()) if len(top) else 0.0,
        "all_products_amount": table_total,
        "total_amount": total_amount,
        "product_count": int(len(table)),
        "best_stock_code": lead["stock_code"] if lead else "",
        "best_amount": lead["amount"] if lead else 0.0,
        "best_description": lead["description"] if lead else "",
    }
    notes = [
        "口径：含首尾全天；排除取消单、数量≤0、单价≤0 的行。",
        f"按 StockCode 分组（Description 只作展示名，取该编码下出现次数最多者）；"
        f"本次排序指标是 **{metric}**（不是销售额）—— 排序由代码做，不是模型挑的。",
        _NONPRODUCT_NOTE,
    ] + _empty_window_note(len(rows), start, end)
    return {
        "tool": PRODUCT_ANALYSIS_TOOL,
        "params": {"start": start.isoformat(), "end": end.isoformat(),
                   "operation": "top", "metric": metric, "top_n": int(top_n)},
        "status": "ok",
        "facts": facts,
        "items": items,
        "notes": notes,
        "display": [
            {"label": "上榜商品数", "value": len(items), "unit": "个", "format": "int"},
            {"label": f"TOP{len(items)} 合计销售额", "value": facts["top_amount"], "unit": currency_unit(),
             "format": "money"},
            {"label": "区间总销售额", "value": total_amount, "unit": currency_unit(), "format": "money"},
            {"label": "区间内出现过的商品编码数", "value": facts["product_count"], "unit": "个", "format": "int"},
        ],
        "selfcheck": {
            "detail_total_source": "同一掩码下 line_amount(rows).sum()",
            "detail_total": total_amount,
            "grouped_total": table_total,
            "grouped_matches_detail": abs(table_total - total_amount)
            <= max(_FLOAT_TOL, abs(total_amount) * _FLOAT_TOL),
            "delta": abs(table_total - total_amount),
        },
    }


def _rank_products(table: pd.DataFrame, metric: str, top_n: int) -> pd.DataFrame:
    """按指标降序 + **StockCode 升序**并列打破（与既有产品排行同一套排序规则）。"""
    column = _PRODUCT_METRIC_COLUMN[metric]
    return table.sort_values(
        [column, "StockCode"], ascending=[False, True], kind="mergesort"
    ).head(top_n)


def _product_trend(
    start: _dt.date, end: _dt.date, product_codes: tuple[str, ...], granularity: str
) -> dict[str, Any]:
    """指定商品的逐日/逐周趋势：**每个商品分别聚合**，分桶规则复用 `_series_points`。"""
    if not product_codes:
        raise ValueError("product_analysis(operation='trend') 必须给 product_codes（要按商品分别聚合）")
    if granularity not in ("day", "week"):
        raise ValueError(f"granularity 只支持 day/week，收到 {granularity!r}")

    rows = _valid_rows(start, end)
    time_column = engine_metrics.TIME_FIELD
    codes = [str(code) for code in product_codes]
    scoped = rows.loc[rows["StockCode"].astype(str).isin(codes)]
    window_total = _amount_of(rows)

    products: list[dict[str, Any]] = []
    series_products: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    missing: list[str] = []
    bucket_label = "天" if granularity == "day" else "周（周一为起点）"

    for code in codes:
        block = scoped.loc[scoped["StockCode"].astype(str) == code]
        if block.empty:
            missing.append(code)
            products.append(
                {"stock_code": code, "description": "", "total_amount": 0.0, "qty": 0.0,
                 "orders": 0, "rows": 0, "bucket_count": 0, "partial_buckets": 0,
                 "point_sum": 0.0}
            )
            series_products.append({"stock_code": code, "description": "", "points": []})
            continue
        stamps = pd.to_datetime(block[time_column])
        points, bucket_label = _series_points(block, stamps, start, end, granularity)
        description = _dominant_description(block["Description"].dropna())
        products.append(
            {
                "stock_code": code,
                "description": description,
                "total_amount": float(block["_amount"].sum()),
                "qty": float(block["Quantity"].sum()),
                "orders": int(block["InvoiceNo"].nunique(dropna=True)),
                "rows": int(len(block)),
                "bucket_count": len(points),
                "partial_buckets": int(sum(1 for point in points if point["partial"])),
                "point_sum": float(math.fsum(point["amount"] for point in points)),
            }
        )
        series_products.append(
            {"stock_code": code, "description": description, "points": points}
        )

    products.sort(key=lambda item: (-item["total_amount"], str(item["stock_code"])))
    items = [
        {
            "rank": rank,
            "stock_code": item["stock_code"],
            "description": item["description"],
            "amount": item["total_amount"],
            "qty": item["qty"],
            "orders": item["orders"],
            "rows": item["rows"],
            "bucket_count": item["bucket_count"],
            "partial_buckets": item["partial_buckets"],
            "share": (item["total_amount"] / window_total) if window_total else 0.0,
        }
        for rank, item in enumerate(products, start=1)
    ]

    scoped_total = float(scoped["_amount"].sum())
    bucket_count = max((item["bucket_count"] for item in products), default=0)
    partial_total = sum(item["partial_buckets"] for item in products)
    facts: dict[str, Any] = {
        "operation": "trend",
        "granularity": granularity,
        "product_codes": codes,
        "product_count": len(codes),
        "bucket_count": bucket_count,
        "partial_buckets": partial_total,
        "products": products,
        "codes_missing": missing,
        "selected_amount": scoped_total,
        "total_amount": window_total,
        "selected_share": (scoped_total / window_total) if window_total else 0.0,
    }
    notes = [
        f"按{bucket_label}聚合，**每个商品分别聚合**（不做跨商品的合并序列）；"
        "空档（没有销售的日子/周）**不补零**，所以点数可能少于自然天数/周数。",
        "口径：含首尾全天；排除取消单、数量≤0、单价≤0 的行。",
        "**桶不完整**的标记规则与销售额趋势**同一处代码**：区间没对齐桶边界时，"
        "首尾桶只装了区间内的部分天数，点里的 `partial=true`、`covered_start~covered_end` "
        "是它实际覆盖的范围 —— 拿它和整桶比高低会得出错的结论。",
    ]
    if missing:
        notes.append(
            f"**有 {len(missing)} 个商品编码在区间内没有任何有效成交**：{'、'.join(missing)} —— "
            "它们的序列是空的（不是「卖得少」，是这段时间确实没有）。"
        )
    notes += _empty_window_note(int(len(rows)), start, end)
    return {
        "tool": PRODUCT_ANALYSIS_TOOL,
        "params": {"start": start.isoformat(), "end": end.isoformat(), "operation": "trend",
                   "product_codes": codes, "granularity": granularity},
        "status": "ok",
        "facts": facts,
        "items": items,
        "series": {"value_key": "amount", "granularity": granularity, "products": series_products},
        "notes": notes,
        "display": [
            {"label": "统计商品数", "value": len(codes), "unit": "个", "format": "int"},
            {"label": "时间桶数", "value": bucket_count, "unit": "个", "format": "int",
             "note": f"按{bucket_label}"},
            {"label": "桶不完整的点数", "value": partial_total, "unit": "个", "format": "int"},
            {"label": "所选商品销售额合计", "value": scoped_total, "unit": currency_unit(), "format": "money"},
            {"label": "区间总销售额（全部商品）", "value": window_total, "unit": currency_unit(), "format": "money"},
            {"label": "所选商品占区间销售额", "value": facts["selected_share"] * 100, "unit": "%",
             "format": "pct", "derived": True},
        ],
        "selfcheck": {
            "per_product_point_sum_matches_total": all(
                abs(item["point_sum"] - item["total_amount"]) <= max(_FLOAT_TOL, abs(item["total_amount"]) * _FLOAT_TOL)
                for item in products
            ),
            "selected_le_total": scoped_total <= window_total + _FLOAT_TOL,
            "missing_codes": missing,
        },
    }


def _product_return(start: _dt.date, end: _dt.date, top_n: int) -> dict[str, Any]:
    """退货 / 取消分析：**两个口径分开**，统一指标必须去重（并集）。

    为什么不能用有效行算：退货/取消的行**正是**被 D16 三条排除规则挡掉的那些行
    （数量≤0、单号以 C 开头），所以这里刻意用**区间内原始行**做分母，并写明这件事。

    两个口径为什么不能相加：
        Quantity < 0        = "数量是负的"（退货/冲销）
        InvoiceNo 以 C 开头 = "这是一张取消单"
    两者**可能重叠**（本数据集里 C 单一定同时是负数量行）。相加会把同一行算两遍 ——
    所以要么分开看，要么看**去重后的并集** `return_or_cancel_*`。这里两样都给，且并集是
    `neg | cancel` 真算出来的，不是 a+b。
    """
    window, _valid = _valid_window(start, end)
    return_items: list[dict[str, Any]] = []
    window_rows = int(len(window))
    window_invoices = int(window["InvoiceNo"].nunique(dropna=True)) if window_rows else 0
    negative_rows = cancel_rows = overlap_rows = union_rows = 0
    union_invoices = 0
    negative_amount = cancel_amount = union_amount = 0.0

    if window_rows:
        quantity = window["Quantity"]
        negative = quantity < 0
        cancel = engine_metrics.mask_cancelled(window)
        union = negative | cancel
        overlap = negative & cancel
        amount = engine_metrics.line_amount(window)
        negative_rows = int(negative.sum())
        cancel_rows = int(cancel.sum())
        overlap_rows = int(overlap.sum())
        union_rows = int(union.sum())
        negative_amount = float(amount[negative].sum())
        cancel_amount = float(amount[cancel].sum())
        union_amount = float(amount[union].sum())
        union_invoices = int(window.loc[union, "InvoiceNo"].nunique(dropna=True))

        # 商品维度：只在**并集行**上分组（同一行只算一次）—— 这是"去重"在商品层的落点
        blocked = window.loc[union].copy()
        blocked["_amount"] = amount[union]
        blocked["_is_negative"] = negative[union].astype(bool)
        blocked["_is_cancel"] = cancel[union].astype(bool)
        grouped = blocked.groupby("StockCode", dropna=False)
        union_table = grouped.agg(
            return_rows=("InvoiceNo", "size"),
            negative_rows=("_is_negative", "sum"),
            cancel_rows=("_is_cancel", "sum"),
            return_qty=("Quantity", "sum"),
            return_amount=("_amount", "sum"),
            orders=("InvoiceNo", "nunique"),
        )
        union_table["description"] = grouped["Description"].agg(
            lambda series: _dominant_description(series.dropna())
        )
        union_table = union_table.sort_values(
            ["return_rows", "StockCode"], ascending=[False, True], kind="mergesort"
        )
        return_items = [
            {
                "rank": rank,
                "stock_code": str(index),
                "description": str(row["description"]),
                "return_rows": int(row["return_rows"]),
                "negative_rows": int(row["negative_rows"]),
                "cancel_rows": int(row["cancel_rows"]),
                "return_qty": float(row["return_qty"]),
                "return_amount": float(row["return_amount"]),
                "orders": int(row["orders"]),
                "share": (float(row["return_rows"]) / union_rows) if union_rows else 0.0,
            }
            for rank, (index, row) in enumerate(union_table.head(top_n).iterrows(), start=1)
        ]

    rate = (lambda part: (part / window_rows) if window_rows else 0.0)
    facts: dict[str, Any] = {
        "operation": "return",
        "top_n": int(top_n),
        "window_rows": window_rows,
        "window_invoices": window_invoices,
        # ── 口径一：数量为负 ───────────────────────────────────────────
        "negative_quantity_rows": negative_rows,
        "negative_quantity_rate": rate(negative_rows),
        "negative_quantity_amount": negative_amount,
        # ── 口径二：C 取消单 ──────────────────────────────────────────
        "cancel_invoice_rows": cancel_rows,
        "cancel_invoice_rate": rate(cancel_rows),
        "cancel_invoice_amount": cancel_amount,
        # ── 两者的重叠与去重后的并集（不许相加）──────────────────────
        "overlap_rows": overlap_rows,
        "return_or_cancel_rows": union_rows,
        "return_or_cancel_row_rate": rate(union_rows),
        "return_or_cancel_invoices": union_invoices,
        "return_or_cancel_invoice_rate": (union_invoices / window_invoices) if window_invoices else 0.0,
        "return_or_cancel_amount": union_amount,
        "definition": {
            "negative_quantity_rate": "Quantity < 0 的行 ÷ 区间内原始行数",
            "cancel_invoice_rate": "InvoiceNo 以 C 开头的行 ÷ 区间内原始行数",
            "return_or_cancel_row_rate": "(Quantity < 0 或 InvoiceNo 以 C 开头) 的行 ÷ 区间内原始行数（**去重**）",
            "denominator": "区间内**原始行**（不套 D16 排除规则 —— 退货/取消正是被那些规则挡掉的行）",
        },
    }
    notes = [
        f"**两个口径分开看，不许相加**：数量为负（Quantity < 0）与取消单（单号以 C 开头）"
        f"是两种不同的业务信号，在本区间里前者 {negative_rows} 行、后者 {cancel_rows} 行，"
        f"而**两者重叠**（同时满足）的有 {overlap_rows} 行 —— 直接相加会把这 {overlap_rows} 行算两遍。",
        f"统一指标 `return_or_cancel_*` 是**去重后的并集**（真算的并集，不是 a+b）："
        f"{union_rows} 行 / 占区间行数 {facts['return_or_cancel_row_rate'] * 100:.2f}%。",
        f"分母是**区间内原始行**（{window_rows} 行），没有套 D16 的排除规则 ——"
        f"退货/取消恰恰就是被那三条规则排除掉的行，套上去分母就空了。",
        _NONPRODUCT_NOTE,
    ]
    notes += _empty_window_note(window_rows, start, end)
    return {
        "tool": PRODUCT_ANALYSIS_TOOL,
        "params": {"start": start.isoformat(), "end": end.isoformat(), "operation": "return",
                   "top_n": int(top_n)},
        "status": "ok",
        "facts": facts,
        "items": return_items,
        "notes": notes,
        "display": [
            {"label": "区间内原始行数", "value": window_rows, "unit": "行", "format": "int"},
            {"label": "数量为负的行占比", "value": facts["negative_quantity_rate"] * 100, "unit": "%",
             "format": "pct"},
            {"label": "取消单（C 开头）行占比", "value": facts["cancel_invoice_rate"] * 100, "unit": "%",
             "format": "pct"},
            {"label": "退货或取消的行占比（去重）", "value": facts["return_or_cancel_row_rate"] * 100,
             "unit": "%", "format": "pct", "derived": True},
            {"label": "两个口径重叠的行数", "value": overlap_rows, "unit": "行", "format": "int"},
            {"label": "退货/取消涉及金额（负值）", "value": union_amount, "unit": currency_unit(),
             "format": "money"},
        ],
        "selfcheck": {
            "union_equals_dedup": union_rows == negative_rows + cancel_rows - overlap_rows,
            "union_le_window": union_rows <= window_rows,
            "overlap_le_each": overlap_rows <= min(negative_rows, cancel_rows),
            "negative_plus_cancel_if_summed": negative_rows + cancel_rows,
            "double_count_if_summed": overlap_rows,
        },
    }


def product_analysis(
    start: _dt.date,
    end: _dt.date,
    operation: str = "top",
    metric: str = "sales_amount",
    top_n: int = 5,
    product_codes: tuple[str, ...] = (),
    granularity: str = "day",
) -> dict[str, Any]:
    """商品维度的三种确定性分析（`operation` 决定做哪一种）：top / trend / return。"""
    if operation not in PRODUCT_OPERATIONS:
        raise ValueError(f"product_analysis 只支持 {list(PRODUCT_OPERATIONS)}，收到 {operation!r}")
    if metric not in PRODUCT_TOP_METRICS:
        raise ValueError(f"metric 只支持 {list(PRODUCT_TOP_METRICS)}，收到 {metric!r}")
    if len(product_codes) > PRODUCT_TREND_MAX_CODES:
        raise ValueError(
            f"product_codes 最多 {PRODUCT_TREND_MAX_CODES} 个（收到 {len(product_codes)} 个）——"
            "一次比太多商品的趋势，图与表都读不了"
        )
    if operation == "top":
        return _product_top(start, end, metric, top_n)
    if operation == "trend":
        return _product_trend(start, end, tuple(product_codes), granularity)
    return _product_return(start, end, top_n)


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
    # ── TASK-006：客户 / 商品（**各一个 Intent，用 operation 收口**）───────────
    CUSTOMER_ANALYSIS_TOOL: ToolSpec(
        name=CUSTOMER_ANALYSIS_TOOL,
        title="客户分析",
        description="客户维度（operation=top 客户排行 / purchase_frequency 购买频次 / "
                    "repeat_rate 复购率 / new_customers 数据集内新客 / inactive_customers 沉睡客户）；"
                    "**只覆盖有 CustomerID 的成交**，结果自带覆盖率说明；不含 VIP/等级/行业/地区",
        run=customer_analysis,
    ),
    PRODUCT_ANALYSIS_TOOL: ToolSpec(
        name=PRODUCT_ANALYSIS_TOOL,
        title="商品分析",
        description="商品维度（operation=top 商品排行 / trend 指定商品趋势 / return 退货与取消分析）；"
                    "top 与既有产品排行同源，退货按「数量为负」「取消单」两个口径分开给",
        run=product_analysis,
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
    "CUSTOMER_ANALYSIS_TOOL",
    "CUSTOMER_OPERATIONS",
    "CUSTOMER_TOP_METRICS",
    "DATASET_CURRENCY",
    "DEFAULT_INACTIVE_DAYS",
    "PRODUCT_ANALYSIS_TOOL",
    "PRODUCT_OPERATIONS",
    "PRODUCT_TOP_METRICS",
    "PRODUCT_TREND_MAX_CODES",
    "TOP_PRODUCTS_TOOL",
    "TOOLS",
    "ToolSpec",
    "WEEK_START_WEEKDAY",
    "build_attribution",
    "build_customer_scope",
    "bucket_of",
    "currency_unit",
    "customer_activity",
    "customer_analysis",
    "customer_table",
    "product_table",
    "raw_window",
    "valid_rows",
    "dataset_bounds",
    "dataset_profile",
    "product_analysis",
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
