"""frames.py · 把**物化在 SQLite 里的行**取回来变成一个 DataFrame（计算层的入口）。

════════════════════════════════════════════════════════════════════════
【它在整条链路里的位置】
════════════════════════════════════════════════════════════════════════
    所有导入格式 → 统一 Dataset 抽象 → SQLite 持久化 → **本文件** → 现有确定性计算层 → Answer

本文件只干三件事，**一件都不多**：

    ① 从 `dataset_rows` 把行**原样**取出来（`SELECT payload`，没有聚合）
    ② 把列名对齐到项目的**规范名**（InvoiceDate / Quantity / UnitPrice …），
       这样 `app/engine/metrics.py` 里那套 D16 掩码可以**原样复用**，不必为导入数据再写一套
    ③ 按列的规范化类型把日期列转回 `datetime64`（存的时候是 ISO 串，算的时候要是时间）

★ 它**不算任何数字**：没有 groupby、没有 sum。所有业务数字都在拿到这个 DataFrame 之后
  由确定性计算层算（见 region_query.py）。这条边界是评审"SQLite 不是第二套计算引擎"的落点。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.datasets import registry as dataset_registry
from app.importer import db, store

#: 规范列名（计算层认这些名字：engine/metrics.py 的掩码按字面引用 InvoiceNo/Quantity/UnitPrice）
CANONICAL_FIELDS: tuple[str, ...] = tuple(dataset_registry.REQUIRED_FIELDS) + (
    "sales_amount", "quantity", "unit_price", "date",
)

#: 同一个规范名被映射到多个实际列时，优先级（前者优先）
_CANONICAL_PRIORITY: tuple[str, ...] = (
    "InvoiceDate", "date", "Quantity", "quantity", "UnitPrice", "unit_price",
    "sales_amount", "InvoiceNo", "StockCode", "Description", "CustomerID", "Country",
)

# 帧缓存：按 (dataset_id, table_name, materialized_at) 缓存 —— 物化时间变了自动失效。
# 与 engine/loader.py 的进程级缓存同一个思路：读一次贵（可能几十万行），别在循环里反复读。
_CACHE: dict[tuple[str, str, str], tuple[pd.DataFrame, dict[str, Any]]] = {}
_CACHE_LIMIT = 4


def clear_cache() -> None:
    """清掉帧缓存（测试与"刚重新导入过"的场景用）。"""
    _CACHE.clear()


def canonical_frame(
    dataset_id: str, table_name: str | None = None
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """取一个数据集（某张表）的 DataFrame，返回 `(帧, 说明)`。

    说明里带着"这张表实际用了哪些列 / 日期列是哪个 / 有没有规范名映射"，
    计算层拿它决定"这笔钱怎么算"，也是给用户看的诚实依据。
    """
    with db.readonly() as connection:
        dataset = store.get_dataset(connection, dataset_id)
        if dataset is None:
            raise KeyError(dataset_id)
        tables = store.list_dataset_tables(connection, dataset_id)
        if not tables:
            raise KeyError(dataset_id)
        chosen = table_name or str(tables[0]["table_name"])
        names = {str(table["table_name"]) for table in tables}
        if chosen not in names:
            raise KeyError(f"{dataset_id}/{chosen}")
        key = (dataset_id, chosen, str(dataset.get("materialized_at") or ""))
        cached = _CACHE.get(key)
        if cached is not None:
            frame, meta = cached
            return frame.copy(), dict(meta)

        rows = store.load_rows(connection, dataset_id, chosen)
        columns = store.list_dataset_columns(connection, dataset_id, chosen)

    frame = pd.DataFrame(rows) if rows else pd.DataFrame(columns=[c["name"] for c in columns])
    date_columns = [str(column["name"]) for column in columns
                    if str(column.get("inferred_type")) == "date"]
    for column in date_columns:
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], errors="coerce")

    field_map: dict[str, str] = dict(dataset.get("field_map") or {})
    frame, renamed = _apply_canonical_names(frame, field_map)
    meta = {
        "dataset_id": dataset_id,
        "dataset_name": dataset.get("name"),
        "table_name": chosen,
        "row_count": int(len(frame)),
        "columns": [str(column) for column in frame.columns],
        "original_columns": [str(column["name"]) for column in columns],
        "date_columns": date_columns,
        "canonical": renamed,
        "region_dimensions": list(dataset.get("region_dimensions") or []),
        "source_file_id": dataset.get("source_file_id"),
        "materialized_at": dataset.get("materialized_at"),
        "field_map": field_map,
    }
    if len(_CACHE) >= _CACHE_LIMIT:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = (frame.copy(), dict(meta))
    return frame, meta


def _apply_canonical_names(
    frame: pd.DataFrame, field_map: dict[str, str]
) -> tuple[pd.DataFrame, dict[str, str]]:
    """把实际列名换成规范名（`数量` → `Quantity`），让 D16 掩码能原样复用。

    冲突处理：同一个实际列被映射到多个规范名时，只认优先级最高的那一个
    （`_CANONICAL_PRIORITY`）—— 剩下的那个规范名就等于"没有这一列"，
    计算层会据此走另一条分支或明确报错，而不是拿到一个"看起来对"的假列。
    """
    # ★ 已经**就叫规范名**的列先占住自己的名字（身份映射优先）。
    #   为什么必须有这一步：`+guess_mapping` 会把 `Quantity` 认成规范字段 `Quantity`，
    #   而导入时的度量识别又会补一条 `quantity → Quantity`；没有这一步的话，
    #   第二条会把一个**本来就叫 Quantity 的列**改名成 `quantity`，
    #   于是 `{"Quantity", "UnitPrice"}` 这个判断永远不成立 —— 交易明细被当成"算不出金额"。
    used: dict[str, str] = {
        str(column): str(column) for column in frame.columns if str(column) in CANONICAL_FIELDS
    }
    renamed: dict[str, str] = {}
    for canonical in _CANONICAL_PRIORITY:
        actual = field_map.get(canonical)
        if not actual or actual not in frame.columns or actual in used:
            continue
        if canonical in frame.columns:
            continue
        used[actual] = canonical
        renamed[actual] = canonical
    if not renamed:
        return frame, {}
    return frame.rename(columns=renamed), renamed


def region_frame_column(meta: dict[str, Any], field: str) -> str:
    """地区维度用的是**哪一个列名**（规范名优先，没有就用数据源里的原名）。"""
    canonical = meta.get("canonical") or {}
    for actual, name in canonical.items():
        if actual == field:
            return str(name)
    return field


__all__ = [
    "CANONICAL_FIELDS",
    "canonical_frame",
    "clear_cache",
    "region_frame_column",
]
