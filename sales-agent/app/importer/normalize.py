"""normalize.py · FR-003C 的**类型规范化**：导入阶段只做类型识别与规范化，不做业务计算。

════════════════════════════════════════════════════════════════════════
【为什么必须有这一层（评审 §8③ 原文）】
════════════════════════════════════════════════════════════════════════
同一份数据从不同格式进来，值的长相是不一样的：

    Excel 单元格里的 100        → int 100
    CSV 里的 "100"              → str "100"
    PPTX 表格里的 "100.00"      → str "100.00"
    Markdown 表格里的 "£100.00" → str "£100.00"

如果不规范就入库，**同一个数会以四种形态躺进数据库**，后面按它分组、求和、判等就会
给出不同结果 —— 而用户以为自己导入的是同一份数据。这一层把四者收敛成同一个值：

    "100" / "100.00" / "£100.00" / "¥100" / "100元"  →  100      （整数列 → int）
    "1,234.50"                                       →  1234.5   （小数列 → float）
    "2011/1/1" / "2011-01-01" / "2011年1月1日"        →  "2011-01-01"（日期列 → ISO 串）

★ 边界（写死在这里，别处不要再判一遍）：**只做类型识别与规范化，不做任何业务计算**
  —— 不求和、不换算汇率、不补全省市区、不做单位换算。那些属于计算层（engine/metrics）。

════════════════════════════════════════════════════════════════════════
【几条刻意的"不猜"规则（每一条都有理由，不是随手写的）】
════════════════════════════════════════════════════════════════════════
1. **前导零的数字保持文本**：`"007"` 不是整数 7 —— 商品编码/客户号常带前导零，
   变成 7 就再也回不去了。`"0.5"` 不受影响（小数点前只有一位 0）。
2. **超过 15 位的数字保持文本**：float 只有 15~17 位有效数字，`"8512345678901234567"`
   转成数字会被悄悄改值——宁可它是文本，也不能是"看着像、其实不是"的数。
3. **数字永远不当日期**：Excel 里的 `40875` 可以是日期序号，也可以是数量。
   猜错就是把"数量"变成"2011 年某天"，用户拿到的是另一个问题。只有**明确的日期形态**
   （带 `-`/`/`/`.`/年月日 分隔的字符串）或 pandas 给的 datetime 单元格才认日期。
4. **布尔只认明确写法**：true/false/yes/no/是/否/y/n（大小写不敏感），不含 "1"/"0"。
   "1"/"0" 在业务表里绝大多数是数字，不是真假。
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Iterable

# 列类型（进 dataset_columns.inferred_type）
TYPE_TEXT = "text"
TYPE_INTEGER = "integer"
TYPE_NUMBER = "number"
TYPE_DATE = "date"
TYPE_BOOL = "bool"

#: 展示用的类型名（接口给前端看；不出现 integer/number 这种词）
TYPE_LABELS: dict[str, str] = {
    TYPE_TEXT: "文本",
    TYPE_INTEGER: "整数",
    TYPE_NUMBER: "小数",
    TYPE_DATE: "日期",
    TYPE_BOOL: "是否",
}

# 数字里允许出现的"噪声"：货币符号、千分位、全角空格、单位后缀
# ★ 只剥这些**确定不是数字一部分**的符号；剥不掉就老老实实当文本（不猜）。
_CURRENCY_CHARS = "¥£$€￥"
_NUMBER_CLEAN = re.compile(r"[,\s 　]")
_UNIT_SUFFIXES = ("元", "人民币", "万元", "块")

_SIGNED_INT = re.compile(r"^[+-]?\d+$")
_DECIMAL = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")

# 明确的日期形态（**必须带分隔符**，光秃秃一串数字不算 —— 见文件头规则 3）
_DATE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?$"), "ymd"),
    (re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?$"), "ymd"),
    (re.compile(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})$"), "ymd"),
    (re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日$"), "ymd"),
)

_BOOL_TRUE = {"true", "yes", "y", "是", "真"}
_BOOL_FALSE = {"false", "no", "n", "否", "假"}

#: 最多留几个样例值进数据字典（给人看的，不是给人算的）
SAMPLE_VALUES = 5


# ════════════════════════════════════════════════════════════════════════
# 单个值的规范化
# ════════════════════════════════════════════════════════════════════════
def _to_scalar(value: Any) -> Any:
    """把 pandas / numpy 的标量转成普通 Python 值（NaN / NaT → None）。"""
    if value is None:
        return None
    if isinstance(value, float) and value != value:              # NaN
        return None
    if isinstance(value, (bool,)):
        return bool(value)
    if isinstance(value, (int,)):
        return int(value)
    if isinstance(value, float):
        return float(value)
    # ★ 顺序要紧：`pandas.Timestamp` 是 `datetime` 的子类，所以这一条必须放在 date 之前
    if isinstance(value, _dt.datetime):
        return _as_datetime(value)
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day)
    # numpy 标量：有 item() 就取它的 Python 值
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _to_scalar(item())
        except (TypeError, ValueError):                          # pragma: no cover —— 兜底
            return str(value)
    text = str(value).strip()
    return text or None


def _as_datetime(value: Any) -> _dt.datetime:
    if isinstance(value, _dt.datetime):
        return value
    return _dt.datetime(value.year, value.month, value.day)


def _strip_numeric_noise(text: str) -> str:
    """剥掉货币符号 / 千分位 / 单位后缀，留下纯数字串（剥不干净就返回原串）。"""
    cleaned = text.strip()
    if not cleaned:
        return cleaned
    for _ in range(4):                                            # 至多剥 4 轮（"¥ 1,234 元"）
        before = cleaned
        cleaned = cleaned.strip()
        if cleaned[:1] in _CURRENCY_CHARS:
            cleaned = cleaned[1:]
        if cleaned[-1:] == "%":                                   # 百分号：只去掉符号，数值原样保留
            cleaned = cleaned[:-1]
        for suffix in _UNIT_SUFFIXES:
            if cleaned.endswith(suffix) and len(cleaned) > len(suffix):
                cleaned = cleaned[: -len(suffix)]
        cleaned = _NUMBER_CLEAN.sub("", cleaned)
        if cleaned == before:
            break
    return cleaned


def parse_number(value: Any) -> int | float | None:
    """把一个值解析成 int / float；解析不了（或**不该**当数字）返回 None。

    返回 None 的两种情形必须分开理解：① 本来就不是数字；② 是数字但**不该**降级
    （前导零 / 超长数字 / 除了符号什么都没有）。两种都退回文本，谁也不吃亏。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if -9_007_199_254_740_991 <= value <= 9_007_199_254_740_991 else None
    if isinstance(value, float):
        return None if value != value else _collapse(value)

    if not isinstance(value, str):
        return None
    text = _strip_numeric_noise(value)
    if not text:
        return None
    if _SIGNED_INT.match(text):
        digits = text.lstrip("+-")
        if len(digits) > 15:                                      # 规则 2：超长 → 保持文本
            return None
        if len(digits) > 1 and digits[0] == "0":                  # 规则 1：前导零 → 保持文本
            return None
        return int(text)
    if _DECIMAL.match(text):
        integer_part = text.lstrip("+-").split(".")[0]
        if len(integer_part) > 15:
            return None
        if len(integer_part) > 1 and integer_part[0] == "0" and "." not in text:
            return None
        try:
            return _collapse(float(text))
        except ValueError:                                        # pragma: no cover
            return None
    return None


def _collapse(number: float) -> int | float:
    """整数形态的小数收成 int：`100.0` → `100`。

    为什么必须收：`"100"` 进来是 int、`100.0` 进来是 float，不收的话同一份数据
    用不同格式导入会得到 100 与 100.0 两种值 —— 正是评审 §8③ 要防的那件事。
    """
    if number.is_integer() and abs(number) <= 9_007_199_254_740_991:
        return int(number)
    return number


def parse_date(value: Any) -> _dt.datetime | None:
    """把一个值解析成 datetime；**不猜**（规则 3：纯数字永远不当日期）。"""
    if isinstance(value, _dt.datetime):
        return value
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day)
    if not isinstance(value, str):
        return None
    text = value.strip()
    for pattern, _ in _DATE_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        parts = list(match.groups())
        year, month, day = (int(parts[0]), int(parts[1]), int(parts[2]))
        hour = int(parts[3]) if len(parts) > 3 and parts[3] is not None else 0
        minute = int(parts[4]) if len(parts) > 4 and parts[4] is not None else 0
        second = int(parts[5]) if len(parts) > 5 and parts[5] is not None else 0
        try:
            return _dt.datetime(year, month, day, hour, minute, second)
        except ValueError:                                        # 2011-13-45 这种
            return None
    return None


def parse_bool(value: Any) -> bool | None:
    """布尔只认明确写法（规则 4）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _BOOL_TRUE:
            return True
        if lowered in _BOOL_FALSE:
            return False
    return None


# ════════════════════════════════════════════════════════════════════════
# 列的规范化
# ════════════════════════════════════════════════════════════════════════
def _jsonify(value: Any) -> Any:
    """规范化的值 → JSON 能安全往返的形态（日期 → ISO 串）。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, _dt.datetime):
        return value.isoformat(sep=" ") if (value.hour or value.minute or value.second) \
            else value.date().isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    return str(value)


def _detect_type(values: list[Any]) -> str:
    """整列一起判类型（**不是逐格判** —— 一列只能有一个类型，否则分组会分裂）。"""
    present = [value for value in values if value is not None]
    if not present:
        return TYPE_TEXT
    if all(parse_bool(value) is not None for value in present):
        return TYPE_BOOL
    if all(parse_number(value) is not None for value in present):
        numbers = [parse_number(value) for value in present]
        return TYPE_INTEGER if all(isinstance(item, int) for item in numbers) else TYPE_NUMBER
    if all(parse_date(value) is not None for value in present):
        return TYPE_DATE
    return TYPE_TEXT


def _coerce(value: Any, column_type: str) -> Any:
    """按列类型把值收敛成规范形态（类型判错了也不炸：落到文本分支）。"""
    if value is None:
        return None
    if column_type == TYPE_BOOL:
        parsed = parse_bool(value)
        return parsed if parsed is not None else str(value)
    if column_type in (TYPE_INTEGER, TYPE_NUMBER):
        parsed_number = parse_number(value)
        return parsed_number if parsed_number is not None else str(value)
    if column_type == TYPE_DATE:
        parsed_date = parse_date(value)
        return _jsonify(parsed_date) if parsed_date is not None else str(value)
    if isinstance(value, (_dt.datetime, _dt.date)):
        # 文本列里混进真日期单元格（Excel 常见）→ 落成 ISO 串，不落成 "2011-01-01 00:00:00"
        return _jsonify(value)
    return str(value) if not isinstance(value, str) else value


class ColumnProfile(dict):
    """一列的画像（dict 子类，直接能进 dataset_columns 那一行）。

    字段与 `dataset_columns` 表一一对应：name / inferred_type / non_null / null_count /
    distinct_count / samples / region_key（后者由 regions.py 回填）。
    """

    def __init__(self, name: str, column_type: str, rows: list[Any]) -> None:
        present = [value for value in rows if value is not None]
        samples: list[Any] = []
        for value in present:
            if value not in samples:
                samples.append(value)
            if len(samples) >= SAMPLE_VALUES:
                break
        super().__init__(
            name=name,
            inferred_type=column_type,
            type_label=TYPE_LABELS.get(column_type, TYPE_TEXT),
            non_null=len(present),
            null_count=len(rows) - len(present),
            distinct_count=len({_hashable(value) for value in present}),
            samples=samples,
        )


def _hashable(value: Any) -> Any:
    """去重用：列表/字典这类不可哈希的值退化成 JSON 串。"""
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    return repr(value)


def normalize_table(
    columns: Iterable[str], rows: Iterable[Iterable[Any]]
) -> tuple[list[str], list[dict[str, Any]], list[ColumnProfile]]:
    """把一个表**整列**规范化，返回 `(列名, 规范化后的行字典, 列画像)`。

    参数里 rows 是"行的列表"，每行是"与 columns 等长的值列表" —— 解析器负责把
    Excel/CSV/PPTX/Markdown 四种来源都整理成这个形状，所以下面这段逻辑只有一份。

    ★ 超过行长度的多余单元格会被丢掉、缺的补 None：PPTX / Markdown 的表格经常出现
      某行多一格或少一格，这不是"数据错误"，是那两种格式本身就没强约束。
    """
    names = [str(column).strip() or f"列{index + 1}" for index, column in enumerate(columns)]
    # 列名去重（Excel 允许两个同名列；入库后必须有唯一名字，否则行字典会互相覆盖）
    seen: dict[str, int] = {}
    unique_names: list[str] = []
    for name in names:
        if name in seen:
            seen[name] += 1
            unique_names.append(f"{name}_{seen[name]}")
        else:
            seen[name] = 0
            unique_names.append(name)

    materialized = [
        [_to_scalar(cell) for cell in list(row)[: len(unique_names)]] for row in rows
    ]
    for row in materialized:
        if len(row) < len(unique_names):
            row.extend([None] * (len(unique_names) - len(row)))

    columns_values: list[list[Any]] = [
        [row[index] for row in materialized] for index in range(len(unique_names))
    ]
    types = [_detect_type(values) for values in columns_values]
    normalized_values = [
        [_coerce(value, types[index]) for value in columns_values[index]]
        for index in range(len(unique_names))
    ]
    normalized_rows = [
        {unique_names[index]: normalized_values[index][row_index]
         for index in range(len(unique_names))}
        for row_index in range(len(materialized))
    ]
    profiles = [
        ColumnProfile(unique_names[index], types[index], normalized_values[index])
        for index in range(len(unique_names))
    ]
    return unique_names, normalized_rows, profiles


__all__ = [
    "SAMPLE_VALUES",
    "TYPE_BOOL",
    "TYPE_DATE",
    "TYPE_INTEGER",
    "TYPE_LABELS",
    "TYPE_NUMBER",
    "TYPE_TEXT",
    "ColumnProfile",
    "normalize_table",
    "parse_bool",
    "parse_date",
    "parse_number",
]
