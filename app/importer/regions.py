"""regions.py · FR-003D：**地区维度识别**（本 TASK 的核心）。

════════════════════════════════════════════════════════════════════════
【一句话原则】
════════════════════════════════════════════════════════════════════════
**地区字段必须来自数据源本身；没有就明确说没有。**
不猜、不推、更**绝不用 Country 顶替**（用户原话「不要有国家这种」）。

════════════════════════════════════════════════════════════════════════
【为什么不是一个笼统的 region_field（评审 #4）】
════════════════════════════════════════════════════════════════════════
「门店」有时是地区，有时只是销售组织；「渠道」是 线上/线下/经销商，跟地理毫无关系。
把它们一股脑塞进 region_field，UI 上就会出现「按地区」下面顶着一个「渠道」——
那是**分类错误**，不是维度缺失。所以这里拆成维度类别：

    region  /  province  /  city  /  district   → **地区类**（进 region_dimensions）
    store                                        → **组织类**（不进地区）
    channel                                      → **渠道类**（不进地区）

数据源同时有「大区/省份/城市」时**不隐式猜**哪个是"按地区"：全存进
`region_dimensions`，UI 展开成

    按地区 ├─ 大区 ├─ 省份 └─ 城市

════════════════════════════════════════════════════════════════════════
【★ 判定条件：不能只看字段名（评审 #4 原文）】
════════════════════════════════════════════════════════════════════════
「字段名候选」只是**入场券**，四条同时满足才认定：

    ① 字段名候选      —— 命中地区类关键词（下表）
    ② 字段值非空率    —— ≥ 50%（几乎全空的列拆不出分布）
    ③ 唯一值数量合理  —— 2 ≤ 去重个数 ≤ 200（有界词汇表；"每行都不一样"的列不是分类）
    ④ 值类型为离散分类 —— 必须是文本列，且**平均长度 ≤ 20 字**（挡住"地区说明"这种自由文本）

第 ④ 条的两个子条件缺一不可：只卡"去重个数"挡不住 50 条各不相同的长描述；
只卡"平均长度"挡不住"北京/北京/北京…"（那确实该算地区）。两条一起才既挡住自由文本、
又不误杀真实的地区列。

════════════════════════════════════════════════════════════════════════
【单字关键词的处理（这里有个容易踩的坑）】
════════════════════════════════════════════════════════════════════════
「市」「区」「县」「省」是候选词，但**不能按子串匹配** —— 「上**市**时间」「**区**域经理姓名」
都会被误判。所以规则分两档：

    · 多字候选（大区/区域/省份/城市/…）→ 规范化后**子串命中**即可
    · 单字候选（省/市/区/县）        → 只认「完全相等」或「作为后缀且整名 ≤ 4 字」
      （"所在市"/"归属省" 命中；"上市时间"/"区域经理" 不命中）

════════════════════════════════════════════════════════════════════════
【★ 绝不 fallback 到 Country】
════════════════════════════════════════════════════════════════════════
本文件里**没有**任何"找不到地区字段就退回国家"的分支。`_NEVER_REGION` 里显式列出
country/国家/国别，并在 `_name_is_candidate()` 的第一行就返回 False —— 让这条规则
写进代码，而不是靠"我们记得别这么干"。ASSERT 24/25 钉的就是它。
"""

from __future__ import annotations

from typing import Any, Iterable

from app.importer.normalize import TYPE_TEXT

# ════════════════════════════════════════════════════════════════════════
# 维度类别与关键词
# ════════════════════════════════════════════════════════════════════════
DIM_REGION = "region"
DIM_PROVINCE = "province"
DIM_CITY = "city"
DIM_DISTRICT = "district"
DIM_STORE = "store"
DIM_CHANNEL = "channel"

#: 展示名（UI 与错误话术共用，别处不许再写一遍"大区/省份/城市"）
DIMENSION_LABELS: dict[str, str] = {
    DIM_REGION: "大区",
    DIM_PROVINCE: "省份",
    DIM_CITY: "城市",
    DIM_DISTRICT: "区县",
    DIM_STORE: "门店",
    DIM_CHANNEL: "渠道",
}

#: **地区类**维度的展示顺序（也是 region_dimensions 的排序依据：越靠前越宏观）
REGION_DIMENSION_ORDER: tuple[str, ...] = (DIM_REGION, DIM_PROVINCE, DIM_CITY, DIM_DISTRICT)

#: 组织类 / 渠道类（**明确不放进地区** —— 评审 #4 点名）
NON_REGION_DIMENSION_ORDER: tuple[str, ...] = (DIM_STORE, DIM_CHANNEL)

#: 字段名候选项（评审 #4 给的清单，一字未删）
DIMENSION_KEYWORDS: dict[str, tuple[str, ...]] = {
    DIM_REGION: ("大区", "区域", "地区", "销售区域", "销售大区", "片区", "分区",
                 "经营区域", "行政区", "地域"),
    DIM_PROVINCE: ("省份", "省", "自治区", "直辖市"),
    DIM_CITY: ("城市", "市", "地市"),
    DIM_DISTRICT: ("区县", "区", "县"),
    DIM_STORE: ("门店", "店铺", "网点", "仓库"),
    DIM_CHANNEL: ("渠道", "销售渠道", "通路"),
}

#: 单字候选（**只认完全相等或 ≤4 字的后缀**，不能按子串匹配）
_SINGLE_CHAR_KEYWORDS: frozenset[str] = frozenset(("省", "市", "区", "县"))

#: ★ 永远不会被认成地区维度的名字（**防"拿国家顶替地区"**）
_NEVER_REGION: frozenset[str] = frozenset((
    "country", "countryname", "国家", "国别", "国家地区", "所属国家", "销售国家",
))

#: ★ 人名 / 岗位类后缀：以它们结尾的列是"人"，不是"地方"。
#:   为什么需要这条：`区域经理` 里含「区域」这个候选词，值又短又有界（张三/李四/王五），
#:   凭"字段名 + 值形态"是拦不住的 —— 但它是**人名列**，按它分组得出的是"每个经理的业绩"，
#:   不是地区分布。宁可少认一个地区字段（用户还能指名道姓说要看哪列），也不能把人的名字
#:   当成地区名算给用户。
_PERSON_MARKERS: tuple[str, ...] = (
    "姓名", "名字", "经理", "负责人", "主管", "店长", "专员", "人员", "员工", "业务员", "销售员",
)

# ── 判定阈值（**改这里就改了全局口径**，所以每个数都写清来历）──────────────
#: 非空率下限：一半以上是空的列，分布图会变成"其他 + 一片空白"，没有信息量
REGION_MIN_NON_NULL_RATIO = 0.5
#: 去重个数下限：只有一个值的列不是维度（它是常量）
REGION_MIN_DISTINCT = 2
#: 去重个数上限：超过这个数就不是"地区"了，是自由文本或高基数标识
REGION_MAX_DISTINCT = 200
#: 平均字符长度上限：地区名（"内蒙古自治区" 6 字）不会超过 20 字；"地区说明"会
REGION_MAX_AVG_LENGTH = 20.0


def _normalize_name(name: str) -> str:
    """列名规范化：去空白/下划线/连字符，转小写（"Province_Name" → "provincename"）。

    ★ 与 `registry.guess_mapping` 的规范化方式保持一致（同一项目里两处不同的规范化
      会让"能不能被认出来"取决于走哪条路径）。
    """
    return "".join(str(name).strip().lower().split()).replace("_", "").replace("-", "")


def _name_is_candidate(name: str) -> str | None:
    """列名命中哪个维度类别（不命中返回 None）。**Country 永远返回 None**。"""
    normalized = _normalize_name(name)
    if not normalized or normalized in _NEVER_REGION:
        return None
    if normalized.endswith(_PERSON_MARKERS):          # 「区域经理」是人名列，不是地区（见常量说明）
        return None
    for dimension, keywords in DIMENSION_KEYWORDS.items():
        for keyword in keywords:
            if keyword in _SINGLE_CHAR_KEYWORDS:
                if normalized == keyword:
                    return dimension
                if normalized.endswith(keyword) and len(normalized) <= 4:
                    return dimension
                continue
            if keyword in normalized:
                return dimension
    return None


def _value_shape(profile: dict[str, Any]) -> tuple[bool, str]:
    """判定 ②非空率 / ③唯一值个数 / ④离散分类，返回 `(是否通过, 说明)`。

    说明是给**人**看的（进数据源的判定依据里），所以写的是人话，不是阈值代号。
    """
    non_null = int(profile.get("non_null") or 0)
    total = non_null + int(profile.get("null_count") or 0)
    if total == 0:
        return False, "整列没有数据"
    ratio = non_null / total
    if ratio < REGION_MIN_NON_NULL_RATIO:
        return False, f"非空率只有 {ratio:.0%}（低于 {REGION_MIN_NON_NULL_RATIO:.0%}，拆不出有意义的分布）"

    distinct = int(profile.get("distinct_count") or 0)
    if distinct < REGION_MIN_DISTINCT:
        return False, f"只有一个取值（{distinct} 个），它不是维度"
    if distinct > REGION_MAX_DISTINCT:
        return False, f"取值有 {distinct} 种（超过 {REGION_MAX_DISTINCT} 种，更像自由文本或高基数编号）"

    if str(profile.get("inferred_type")) != TYPE_TEXT:
        return False, f"值类型是「{profile.get('type_label')}」，地区必须是离散分类（文本）"

    samples = [str(value) for value in (profile.get("samples") or [])]
    if samples:
        average_length = sum(len(value) for value in samples) / len(samples)
        if average_length > REGION_MAX_AVG_LENGTH:
            return False, f"取值平均 {average_length:.0f} 字（超过 {REGION_MAX_AVG_LENGTH:.0f} 字，像描述而不是地区名）"
    return True, f"非空率 {ratio:.0%}、{distinct} 种取值、离散文本 —— 符合地区特征"


def classify_column(profile: dict[str, Any]) -> dict[str, Any] | None:
    """**单列判定**：这一列算不算某个维度。不算返回 None。

    返回 `{"key": "province", "field": "省份", "label": "省份", "reason": "…"}`。
    这条判定是全 TASK 唯一的"什么算地区字段"的实现处（数据字典、维度列表、UI 都用它）。
    """
    name = str(profile.get("name") or "")
    dimension = _name_is_candidate(name)
    if dimension is None:
        return None
    passed, reason = _value_shape(profile)
    if not passed:
        return None
    return {
        "key": dimension,
        "field": name,
        "label": str(profile.get("name") or DIMENSION_LABELS.get(dimension, dimension)),
        "dimension_label": DIMENSION_LABELS.get(dimension, dimension),
        "category": "region" if dimension in REGION_DIMENSION_ORDER else "other",
        # 多表数据集里"这一列在哪张表"是必需信息（否则 UI 拿不到该查哪张表）
        "table_name": profile.get("table_name"),
        "reason": reason,
    }


def detect_dimensions(profiles: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """把一个数据集的所有列跑一遍判定，返回命中列表（按维度类别排序）。"""
    hits = [hit for hit in (classify_column(profile) for profile in profiles) if hit]
    order = {key: index for index, key in enumerate((*REGION_DIMENSION_ORDER, *NON_REGION_DIMENSION_ORDER))}
    return sorted(hits, key=lambda hit: (order.get(hit["key"], 99), hit["field"]))


def region_dimensions(profiles: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """**只取地区类**维度（region/province/city/district）—— store 与 channel 不在这里。

    数据源同时有「大区/省份/城市」时，**三个都返回**（不隐式挑一个当"按地区"）：
    评审 #4 的原话是「不要隐式猜」，UI 再展开成 按地区 ├─ 大区 ├─ 省份 └─ 城市。
    """
    return [hit for hit in detect_dimensions(profiles) if hit["category"] == "region"]


def region_field_names(profiles: Iterable[dict[str, Any]]) -> list[str]:
    """地区维度的**列名**列表（数据源记录里就存这个，UI 直接展开）。"""
    return [hit["field"] for hit in region_dimensions(profiles)]


def has_region(profiles: Iterable[dict[str, Any]]) -> bool:
    """这个数据源有没有地区维度（**唯一的判据**，别处不许自己看列名猜）。"""
    return bool(region_dimensions(profiles))


def explain(name: str, profile: dict[str, Any]) -> dict[str, Any]:
    """给数据字典用的"为什么认/不认"（把判定过程摊开，用户能复核）。"""
    dimension = _name_is_candidate(name)
    if dimension is None:
        return {"field": name, "matched": False, "reason": "字段名不在地区候选清单里"}
    passed, reason = _value_shape(profile)
    return {
        "field": name,
        "matched": passed,
        "dimension": dimension,
        "dimension_label": DIMENSION_LABELS.get(dimension, dimension),
        "reason": reason,
    }


__all__ = [
    "DIMENSION_KEYWORDS",
    "DIMENSION_LABELS",
    "DIM_CHANNEL",
    "DIM_CITY",
    "DIM_DISTRICT",
    "DIM_PROVINCE",
    "DIM_REGION",
    "DIM_STORE",
    "NON_REGION_DIMENSION_ORDER",
    "REGION_DIMENSION_ORDER",
    "REGION_MAX_AVG_LENGTH",
    "REGION_MAX_DISTINCT",
    "REGION_MIN_DISTINCT",
    "REGION_MIN_NON_NULL_RATIO",
    "classify_column",
    "detect_dimensions",
    "explain",
    "has_region",
    "region_dimensions",
    "region_field_names",
]
