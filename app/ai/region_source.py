"""region_source.py · FR-008：地区问答的「用哪个数据源」规则（**显式、可断言、不隐式猜**）。

════════════════════════════════════════════════════════════════════════
【为什么单独一个文件：这是全链路唯一一处"选数据源"的地方】
════════════════════════════════════════════════════════════════════════
内置数据集（`data/Online Retail.xlsx`，8 列）**没有地区字段** —— 地区数据只可能来自
用户导入的数据源（FR-003 的物化库）。于是"问地区"这件事必然多出一个问题：

    **用哪个数据源？**

这个问题不许交给 LLM（它看不到库里有几张表，硬让它猜就是编），也不许散落在
service / tools / guard 各写一遍（三处规则早晚漂移）。所以规则只写在这里一次：

    ① 只把**带地区字段**的数据源当候选（`region_dimensions` 非空，判据复用
       `region_query.region_dimensions_of` —— 不另写一套"什么算地区"）
    ② 候选有多个时 → **最近导入优先**（`store.list_datasets` 本身就按
       `materialized_at DESC` 排，这里不重新排序，直接取第一条）
    ③ 选中哪个数据源，**必须在回答里说出来**（见 `rule_note()`）——
       用户手上有好几个数据源时，"这个数是从哪来的"是答案的一部分
    ④ 一个候选都没有 → 返回 `None`，调用方给出"当前没有带地区字段的数据源 + 怎么获得"
       （`NO_REGION_REASON`）。**绝不回退到 Country**（那是最糟的答法：看着像答案，
       答的却是另一个问题）。

════════════════════════════════════════════════════════════════════════
【这一层只"选"，不算数】
════════════════════════════════════════════════════════════════════════
本文件里没有任何聚合：数字一律由 `app/importer/region_query.py`（pandas groupby）算，
这里只负责把那条确定性实现指向**哪个 dataset_id**。SQLite 依旧只做持久化，
没有 SUM / GROUP BY（见 db.py 开头的定位说明）。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.importer import db, region_query, store

#: 一次最多看多少个数据源（单用户单机；给个上限是为了不把整库拉进内存）
CANDIDATE_LIMIT = 200

#: 没有地区数据源时**唯一的**说明文案（guard / 工具 / 回答三处共用同一句，不各写一份）
NO_REGION_REASON = (
    "当前**没有任何带地区字段的数据源**：内置数据集没有「区域」字段（只有 Country，"
    "国家不能当地区用），也没有导入过带地区列的数据。"
    "导入一份带「大区 / 省份 / 城市」列的数据，就能在对话里直接问「各省份的销售额」。"
)

#: 有地区数据源、但问的不是"销售额分布"时用这句（地区这块只做金额分布，别的指标没有）
REGION_ONLY_SALES_REASON = (
    "地区这块目前只做**按地区看销售额**（某个时间段里各地区的金额分布）—— "
    "退货 / 客户 / 订单数这类指标在数据源里没有对应的地区口径，不会拿销售额去顶替它们。"
)


def list_region_datasets() -> list[dict[str, Any]]:
    """全部「带地区字段」的数据源，**最近导入优先**。

    返回的是 `store.get_dataset()` 那种解码后的记录（`region_dimensions` 已是列表）。
    这里**不重新排序**：库里的顺序就是"最近导入在前"，规则与实现同一处。

    ★ 库**还没建表 / 读不出来**时按"一个地区数据源都没有"处理（返回空列表），而不是抛异常：
      这个判断在**问句进门的第一步**（硬闸门）就被调用，它必须永远能给一个答案 ——
      而"读不出库"与"库里没有带地区字段的数据"在**结论上完全一致**（都没有地区维度可用），
      对应的行为也同一个：明确说没有地区数据源，绝不用国家顶替。
      代价只可能是"少了一次能用的地区问答"，绝不会是"给出一个错的数字"。
    """
    try:
        with db.readonly() as connection:
            records, _total = store.list_datasets(connection, limit=CANDIDATE_LIMIT)
    except (sqlite3.Error, OSError):
        return []
    return [record for record in records if region_query.region_dimensions_of(record)]


def available() -> bool:
    """当前有没有带地区字段的数据源（能力清单 / 硬闸门都问这一句）。"""
    return bool(list_region_datasets())


def choose() -> dict[str, Any] | None:
    """按上面 ①② 的规则挑出这次要用的数据源；一个都没有时返回 `None`。

    返回 `{"record": …, "rule": …}`：`rule` 是**给人看的一句话**，说明"为什么是它"
    （只有一个候选时也要说，否则用户不知道怎么换数据源）。
    """
    candidates = list_region_datasets()
    if not candidates:
        return None
    chosen = candidates[0]
    names = [str(record.get("name") or record.get("dataset_id")) for record in candidates]
    if len(candidates) == 1:
        rule = f"本次用的是唯一带地区字段的数据源「{names[0]}」"
    else:
        rule = (
            f"当前有 {len(candidates)} 个带地区字段的数据源（{'、'.join(names)}），"
            f"按「带地区字段优先、其次最近导入优先」取了最近导入的「{names[0]}」"
        )
    return {"record": chosen, "rule": rule, "candidate_names": names}


def dimension_labels(record: dict[str, Any]) -> list[str]:
    """这个数据源有哪几个地区字段（给"多个维度时要澄清"的话术用）。"""
    return [
        str(item.get("label") or item.get("field"))
        for item in region_query.region_dimensions_of(record)
    ]


__all__ = [
    "CANDIDATE_LIMIT",
    "NO_REGION_REASON",
    "REGION_ONLY_SALES_REASON",
    "available",
    "choose",
    "dimension_labels",
    "list_region_datasets",
]
