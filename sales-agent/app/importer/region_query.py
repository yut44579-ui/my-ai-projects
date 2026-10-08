"""region_query.py · FR-003D：**按地区的确定性计算**（本 TASK 核心的落点）。

════════════════════════════════════════════════════════════════════════
【数字只有一个来源（铁律）】
════════════════════════════════════════════════════════════════════════
这里的每一个数都由 pandas 算出（`groupby(...).sum()`），**没有一个来自 LLM**，
也没有一个来自 SQL 的 SUM/GROUP BY（SQLite 只负责把行取回来，见 frames.py）。

金额的两种来源，**同一段代码里两条明确分支**（不许出现第三套）：

  ① 数据源自带金额列（如「销售额」）→ 直接按地区求和；
     这是**已经算好的业务金额**，不再套 D16 的三条排除规则 —— 那些规则是"从交易明细
     反推金额"时才成立的口径（取消单/负数量/非正单价），套到一张汇总表上就是乱扣。
  ② 数据源是**交易明细**（有 数量 + 单价）→ 逐行 `Quantity × UnitPrice` 求和，
     并且**严格复用** `app/engine/metrics.py` 的 D16 掩码（取消单 / 负数量 / 非正单价）
     与 `inclusive_day_window` 的区间语义 —— 与问答链路用的是同一份口径代码。

两条分支都必须能算出数，算不出来（既没有金额列、也没有数量+单价）→ **明确报错**，
绝不"拿别的列凑一个金额"。

════════════════════════════════════════════════════════════════════════
【★ 没有地区字段时：结构化报错，绝不 fallback 到 Country】
════════════════════════════════════════════════════════════════════════
`region_dimensions_of()` 是唯一判据：数据源的 `region_dimensions` 为空 → `sales_by_region()`
直接抛 `DIMENSION_UNAVAILABLE`（评审 #5 指定的响应体形状）。

本文件里**没有**任何"那就用 Country 吧"的分支，也没有任何按列名兜底去猜地区的代码 ——
地区字段只可能在导入时由 `regions.py` 判定出来，并写进数据源记录（ASSERT 24/25/26）。
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

import pandas as pd

from app.engine import metrics as engine_metrics
from app.importer import db, frames, store
from app.importer.models import ImporterError, dimension_unavailable

#: 最多返回几行地区（超出不再截断成"其他"，而是**如实说明还有多少行没展示**）
DEFAULT_TOP_N = 50


def region_dimensions_of(record: dict[str, Any]) -> list[dict[str, Any]]:
    """一个数据源记录里的地区维度（**唯一判据**）。

    `record` 是 `store.get_dataset()` 那一行的解码结果（`region_dimensions` 已是列表）。
    没有就返回空列表 —— 调用方据此报 DIMENSION_UNAVAILABLE / 前端据此决定显不显示「按地区」。
    """
    return [
        dict(item) for item in (record.get("region_dimensions") or [])
        if isinstance(item, dict) and item.get("field")
    ]


def resolve_dimension(
    dimensions: list[dict[str, Any]], requested: str | None
) -> dict[str, Any]:
    """挑出这次要按哪个地区字段分组。

    ★ **不隐式猜**（评审 #4）：数据源同时有「大区/省份/城市」而用户没指明时，
      不默认挑一个（挑错了就是答另一个问题），而是抛 `DIMENSION_AMBIGUOUS` 把候选列出来，
      让上层去问用户（UI 已经把这三个展开成 按地区 ├─ 大区 ├─ 省份 └─ 城市）。
    """
    if not dimensions:
        raise ImporterError("dimension_missing", "这个数据源没有地区维度。")
    if requested:
        wanted = str(requested).strip()
        for item in dimensions:
            if wanted in (str(item.get("key")), str(item.get("field")), str(item.get("label"))):
                return item
        raise ImporterError(
            "dimension_unknown",
            f"这个数据源里没有地区字段 {wanted!r}（可选："
            f"{[item.get('label') or item.get('field') for item in dimensions]}）",
        )
    if len(dimensions) == 1:
        return dimensions[0]
    labels = [str(item.get("label") or item.get("field")) for item in dimensions]
    raise ImporterError(
        "DIMENSION_AMBIGUOUS",
        f"这个数据源有多个地区字段（{labels}）—— 请说明按哪一个看（不替你猜）。",
        extra={"dimension": "region", "candidates": dimensions},
    )


def _measure_of(frame: pd.DataFrame, meta: dict[str, Any]) -> dict[str, Any]:
    """决定"这笔钱怎么算"，返回 `{"kind":…, "mask": bool, "note":…}`。

    分支见文件头：有金额列就用金额列（不再套交易口径掩码）；否则用 数量 × 单价（套 D16 掩码）。
    """
    canonical = set(meta.get("canonical", {}).values())
    if "sales_amount" in frame.columns:
        return {
            "kind": "amount_column",
            "column": "sales_amount",
            "field": next((actual for actual, name in (meta.get("canonical") or {}).items()
                           if name == "sales_amount"), "sales_amount"),
            "apply_d16_mask": False,
            "note": "数据源自带金额列，直接按地区求和（不再套交易明细口径的排除规则）",
        }
    if {"Quantity", "UnitPrice"} <= set(frame.columns):
        return {
            "kind": "quantity_x_unitprice",
            "column": "Quantity * UnitPrice",
            "field": "Quantity * UnitPrice",
            "apply_d16_mask": True,
            "note": "数据源是交易明细：逐行 数量 × 单价，并套用与问答链路相同的 D16 口径"
                    "（排除取消单 / 负数量 / 非正单价）",
        }
    raise ImporterError(
        "measure_unavailable",
        "这个数据源里没有金额列，也没有「数量 + 单价」两列 —— 算不出销售额。"
        f"现有列：{meta.get('columns')}。",
    )


def _date_column(frame: pd.DataFrame, meta: dict[str, Any]) -> str | None:
    """日期列：优先规范名，其次"导入时被判定为日期类型"的列。"""
    if "InvoiceDate" in frame.columns:
        return "InvoiceDate"
    if "date" in frame.columns:
        return "date"
    for column in meta.get("date_columns") or []:
        if column in frame.columns:
            return str(column)
    return None


def _filter_window(
    frame: pd.DataFrame, meta: dict[str, Any],
    start: _dt.date | None, end: _dt.date | None,
) -> tuple[pd.DataFrame, dict[str, Any] | None]:
    """按时间窗筛选（区间语义**复用** `metrics.inclusive_day_window`：含首尾全天）。"""
    if start is None and end is None:
        return frame, None
    column = _date_column(frame, meta)
    if column is None:
        raise ImporterError(
            "window_unavailable",
            "这个数据源里没有日期列，无法按时间段筛选 —— 请去掉时间条件，或换一个带日期的数据源。",
        )
    actual_start = start or end
    actual_end = end or start
    assert actual_start is not None and actual_end is not None       # 上面已保证
    low, high = engine_metrics.inclusive_day_window(actual_start, actual_end)
    stamps = pd.to_datetime(frame[column], errors="coerce")
    kept = frame.loc[(stamps >= low) & (stamps < high)]
    return kept, {
        "column": column,
        "start": actual_start.isoformat(),
        "end": actual_end.isoformat(),
        "semantics": engine_metrics.RANGE_SEMANTICS,
        "rows_in_window": int(len(kept)),
    }


def sales_by_region(
    dataset_id: str,
    *,
    start: _dt.date | str | None = None,
    end: _dt.date | str | None = None,
    dimension: str | None = None,
    table_name: str | None = None,
    top_n: int | None = DEFAULT_TOP_N,
) -> dict[str, Any]:
    """按地区看销售额（**确定性计算**，见文件头）。

    返回的结构里每一个数字都能追到"哪份数据源的哪张表、用哪一列算的、算之前剩几行"。
    """
    with db.readonly() as connection:
        record = store.get_dataset(connection, dataset_id)
    if record is None:
        raise ImporterError("dataset_not_found", f"没有这个数据源：{dataset_id}")

    dimensions = region_dimensions_of(record)
    if not dimensions:                                   # ★ 绝不 fallback 到 Country
        raise dimension_unavailable("region", str(record.get("name") or dataset_id))
    chosen = resolve_dimension(dimensions, dimension)

    frame, meta = frames.canonical_frame(dataset_id, table_name)
    region_column = frames.region_frame_column(meta, str(chosen["field"]))
    if region_column not in frame.columns:
        raise ImporterError(
            "region_column_missing",
            f"数据源记录里的地区字段「{chosen['field']}」在当前表里找不到 —— "
            f"数据字典可能已过期，请重新导入这份文件。",
        )

    start_date = engine_metrics.to_date(start) if start is not None else None
    end_date = engine_metrics.to_date(end) if end is not None else None
    frame, window = _filter_window(frame, meta, start_date, end_date)

    measure = _measure_of(frame, meta)
    working = frame.copy()
    if measure["apply_d16_mask"]:
        rules = engine_metrics.resolve_enabled_rules()
        hits = pd.concat(
            {rule.key: rule.mask(working).astype(bool) for rule in rules}, axis=1
        ).sum(axis=1)
        working = working.loc[hits == 0]
        amount = engine_metrics.line_amount(working)
    else:
        amount = pd.to_numeric(working[measure["column"]], errors="coerce")
    working = working.assign(_amount=amount)

    raw_regions = working[region_column]
    # 地区值为空的行走不进任何一行分组 —— 但要**如实报数列数**，不能悄悄吞掉
    labels = raw_regions.astype("string").fillna("").str.strip()
    usable = labels != ""
    skipped = int((~usable).sum())
    grouped = (working.loc[usable].assign(_region=labels.loc[usable])
               .groupby("_region", sort=False)["_amount"].sum())

    total = float(grouped.sum())
    ordered = grouped.sort_values(ascending=False, kind="stable")
    shown = ordered if top_n is None else ordered.head(int(top_n))
    rows = [
        {
            "region": str(region),
            "amount": float(value),
            "share": (float(value) / total) if total else None,
        }
        for region, value in shown.items()
    ]
    notes = [measure["note"]]
    if skipped:
        notes.append(f"有 {skipped:,} 行没有地区值，未计入任何地区（也没有被算成「其他」）")
    if top_n is not None and len(ordered) > len(rows):
        notes.append(f"共 {len(ordered)} 个地区，这里按金额降序展示前 {len(rows)} 个")

    return {
        "dataset_id": dataset_id,
        "dataset_name": record.get("name"),
        "table_name": meta.get("table_name"),
        "dimension": {"key": chosen.get("key"), "field": chosen.get("field"),
                      "label": chosen.get("label") or chosen.get("field")},
        "measure": measure,
        "window": window,
        "rows": rows,
        "total_amount": total,
        "region_count": int(len(ordered)),
        "rows_used": int(len(working)),
        "rows_skipped_no_region": skipped,
        "notes": notes,
    }


def region_view(dataset_id: str) -> dict[str, Any]:
    """给 UI 用的"这个数据源有没有地区维度"（前端据此决定显不显示「按地区」）。

    ★ 前端**不显示**是体验，后端这道门才是防线：真正调用 `sales_by_region()` 时
      照样会抛 DIMENSION_UNAVAILABLE（评审 #5：不能只靠前端隐藏）。
    """
    with db.readonly() as connection:
        record = store.get_dataset(connection, dataset_id)
    if record is None:
        raise ImporterError("dataset_not_found", f"没有这个数据源：{dataset_id}")
    dimensions = region_dimensions_of(record)
    return {
        "dataset_id": dataset_id,
        "dataset_name": record.get("name"),
        "available": bool(dimensions),
        "dimension": "region",
        "dimensions": dimensions,
        "message": "" if dimensions else "当前数据源没有地区字段",
    }


__all__ = [
    "DEFAULT_TOP_N",
    "region_dimensions_of",
    "region_view",
    "resolve_dimension",
    "sales_by_region",
]
