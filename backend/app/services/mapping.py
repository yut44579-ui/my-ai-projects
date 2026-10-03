"""列名 → 目标字段的映射（TASK-001 §三、§四）。

★ 评审点名要防的两件事：
  1) "把备注当电话" —— 所以只做**别名精确匹配**，绝不做"包含 电话 二字就当地址/电话"的猜测；
     匹配不上就是 unknown，交给用户在弹窗里手选（未映射的列进 skipped_columns）。
  2) 同一目标字段被两个列同时命中（别名冲突）→ 明确报错，不许任选一个。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.errors import ImportErrorCode, ImportFailure

# 6 个目标字段（与 customers 表可导入列一一对应）
TARGET_FIELDS: tuple[str, ...] = ("name", "company_name", "phone", "email", "region", "note")

FIELD_LABELS: dict[str, str] = {
    "name": "客户姓名",
    "company_name": "公司名称",
    "phone": "联系电话",
    "email": "邮箱",
    "region": "地区",
    "note": "备注",
}

# 别名表：只认这些写法，键是目标字段，值是全部可识别的列名写法
ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("客户姓名", "姓名", "客户名称", "客户", "名称", "联系人", "name", "customer_name"),
    "company_name": (
        "公司", "公司名称", "公司全称", "企业", "企业名称", "单位", "单位名称",
        "客户公司", "company", "company_name",
    ),
    "phone": (
        "电话", "联系电话", "联系电话号码", "电话号", "手机", "手机号", "手机号码",
        "联系方式", "座机", "tel", "phone", "mobile", "phone_number",
    ),
    "email": ("邮箱", "电子邮箱", "电子邮件", "邮件", "邮件地址", "邮箱地址", "email", "e-mail"),
    "region": ("地区", "区域", "省市", "省份", "城市", "所在地", "region", "area", "province", "city"),
    "note": ("备注", "说明", "注释", "附注", "note", "remark", "memo"),
}


def normalize_header(header: str) -> str:
    """表头归一化：去空白、全角空格、下划线、连字符，转小写。"""
    cleaned = header.strip().lower()
    for ch in (" ", "　", "_", "-", "\t"):
        cleaned = cleaned.replace(ch, "")
    return cleaned


_ALIAS_INDEX: dict[str, str] = {
    normalize_header(alias): field for field, aliases in ALIASES.items() for alias in aliases
}


@dataclass
class ColumnMap:
    """一列的自动映射结果。confidence: auto=别名命中；unknown=没认出来，等用户选。"""

    column: str
    target: str | None
    confidence: str  # "auto" | "unknown"


def auto_map_columns(headers: list[str]) -> list[ColumnMap]:
    """按别名表自动预填映射；同一目标字段被两列命中 → 明确报错。"""
    result: list[ColumnMap] = []
    hits: dict[str, list[str]] = {}
    for header in headers:
        target = _ALIAS_INDEX.get(normalize_header(header))
        result.append(ColumnMap(column=header, target=target, confidence="auto" if target else "unknown"))
        if target:
            hits.setdefault(target, []).append(header)

    conflicts = {field: cols for field, cols in hits.items() if len(cols) > 1}
    if conflicts:
        # ★ 不许任选一个：明确报错，让用户改表头或手工取消一列
        detail = "；".join(
            f"{FIELD_LABELS[field]} ← {'、'.join(cols)}" for field, cols in sorted(conflicts.items())
        )
        raise ImportFailure(
            ImportErrorCode.AMBIGUOUS_MAPPING,
            f"多个列同时映射到同一个字段，请只保留一列：{detail}",
            detail={"conflicts": conflicts},
        )

    return result


def resolve_mapping(
    headers: list[str],
    requested: list[dict] | None,
) -> tuple[dict[str, int], list[dict]]:
    """按用户确认后的映射表，算出 {目标字段: 列下标} 与 skipped_columns。

    requested 形如 [{"column": "客户姓名", "target": "name"}, {"column": "备注信息XX", "target": null}]；
    target 为 null 或整列没出现在 requested 里 → 都算"未映射"，进 skipped_columns。
    """
    index_of = {h: i for i, h in enumerate(headers)}

    if requested is None:
        # 没给映射表：退回自动映射（/api/imports/commit 也允许省略，行为与预览一致）
        auto = auto_map_columns(headers)
        requested = [{"column": c.column, "target": c.target} for c in auto]

    by_target: dict[str, int] = {}
    seen_columns: set[str] = set()
    duplicate_targets: dict[str, list[str]] = {}

    for item in requested:
        column = item.get("column")
        target = item.get("target")

        if column not in index_of:
            raise ImportFailure(
                ImportErrorCode.INVALID_MAPPING,
                f"映射表里的列 {column!r} 不在文件表头中（文件已被替换？）",
                detail={"headers": headers},
            )
        if column in seen_columns:
            raise ImportFailure(
                ImportErrorCode.INVALID_MAPPING, f"列 {column!r} 在映射表里出现了多次"
            )
        seen_columns.add(column)

        if target is None or target == "":
            continue  # 用户主动取消该列：不映射，后面统一进 skipped_columns
        if target not in TARGET_FIELDS:
            raise ImportFailure(
                ImportErrorCode.INVALID_MAPPING,
                f"未知目标字段 {target!r}，只能是 {', '.join(TARGET_FIELDS)} 之一或 null",
            )
        if target in by_target:
            duplicate_targets.setdefault(target, [headers[by_target[target]]]).append(column)
            continue
        by_target[target] = index_of[column]

    if duplicate_targets:
        detail = "；".join(
            f"{FIELD_LABELS.get(f, f)} ← {'、'.join(cols)}" for f, cols in sorted(duplicate_targets.items())
        )
        raise ImportFailure(
            ImportErrorCode.AMBIGUOUS_MAPPING,
            f"同一个字段被映射了多次，请只保留一列：{detail}",
            detail={"conflicts": duplicate_targets},
        )

    mapped_columns = {headers[i] for i in by_target.values()}
    skipped_columns = [
        {"column": header, "reason": "unmapped_column"}
        for header in headers
        if header not in mapped_columns
    ]
    return by_target, skipped_columns
