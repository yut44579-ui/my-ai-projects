"""metrics.py · 销售额口径清单（项目决策 D16）逐条实现。

⚠️ 本文件是"口径唯一来源"（D12：口径先定义再计算）。集成方/测试若另写一套口径，
    必须能逐条对照回本文件；本 TASK 的独立 Oracle 就是按同一份 D16 清单**另写一条代码路径**。

════════════════════════════════════════════════════════════════════════
D16 原文（来源：docs/DECISIONS.md · 严格照抄，不得自行解释）
════════════════════════════════════════════════════════════════════════
| 项       | 定义 |
| 时间字段 | `InvoiceDate`（下单时间） |
| 区间语义 | **含首尾全天**：start 00:00:00 ~ end 23:59:59.999 |
| 取消单   | 排除 `InvoiceNo` 以 `'C'` 开头的行 |
| 退货/负数量 | 排除 `Quantity <= 0` 的行 |
| 负/零单价 | 排除 `UnitPrice <= 0` 的行 |
| 缺失客户 | `CustomerID` 为空 **不排除**（只影响客户维度分析），但在 `validations` 里报告空值行数 |
| 重复行   | **不去重**（原始数据可能含同单据重复录入；去重策略留待 002B 决定，本 TASK 报告重复行数） |
| 金额公式 | `sales_amount = sum(Quantity * UnitPrice)` |
════════════════════════════════════════════════════════════════════════

逐条落地对照表（D16-1 .. D16-8，每条都有对应常量/函数，可枚举可核对）：

| 编号   | D16 条目     | 本文件对应实现 |
| D16-1  | 时间字段     | const TIME_FIELD = "InvoiceDate" |
| D16-2  | 区间语义     | func inclusive_day_window(start, end) → 半开区间 [start 00:00:00, end+1天 00:00:00)（与含首尾全天等价） |
| D16-3  | 取消单       | const CANCELLED_PREFIX = "C" + func mask_cancelled(df) |
| D16-4  | 退货/负数量  | const NEGATIVE_QTY_THRESHOLD = 0 + func mask_negative_qty(df) |
| D16-5  | 负/零单价    | const NONPOSITIVE_PRICE_THRESHOLD = 0 + func mask_nonpositive_price(df) |
| D16-6  | 缺失客户     | func count_customer_id_nulls(df)（**不排除**，只在 validations 报告） |
| D16-7  | 重复行       | func count_duplicate_rows(df)（**不去重**，只报告） |
| D16-8  | 金额公式     | const LINE_AMOUNT_FORMULA = "Quantity * UnitPrice" + func line_amount(df) |

金额公式为 `sum(Quantity * UnitPrice)`：按行相乘再求和（不是 sum(Quantity) * sum(UnitPrice)）。
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Callable

import pandas as pd

# ════════════════════════════════════════════════════════════════════════
# D16-1 · 时间字段 = InvoiceDate（下单时间）
# ════════════════════════════════════════════════════════════════════════
TIME_FIELD = "InvoiceDate"
TIME_FIELD_D16_ID = "D16-1"

# ════════════════════════════════════════════════════════════════════════
# D16-2 · 区间语义 = 含首尾全天（start 00:00:00 ~ end 23:59:59.999）
# ════════════════════════════════════════════════════════════════════════
# 实现选择（TASK-002A 第 5 节评审建议 3）：用**等价的半开区间**做筛选：
#     InvoiceDate >= start 00:00:00  AND  InvoiceDate < (end + 1 天) 00:00:00
# 与"闭区间到 23:59:59.999"业务语义完全等价，且不受浮点/毫秒精度影响
# （pandas 的 datetime64 精度高于毫秒，用 < 次日零点可以精确纳入 23:59:59.999999）。
# 两种写法不得混用出第三种口径。
RANGE_SEMANTICS = "含首尾全天（闭区间 start 00:00:00 ~ end 23:59:59.999）"
RANGE_IMPLEMENTATION = "半开区间 [start 00:00:00, end+1天 00:00:00)"

# 一天的时间偏移：end 日 +1 天 得到半开区间的开界
_ONE_DAY = pd.Timedelta(days=1)

# ════════════════════════════════════════════════════════════════════════
# D16-3 / D16-4 / D16-5 · 三条排除规则
# ════════════════════════════════════════════════════════════════════════
CANCELLED_PREFIX = "C"          # D16-3：InvoiceNo 以 'C' 开头 = 取消单
NEGATIVE_QTY_THRESHOLD = 0      # D16-4：Quantity <= 0 = 退货/负数量
NONPOSITIVE_PRICE_THRESHOLD = 0 # D16-5：UnitPrice <= 0 = 负/零单价


def mask_cancelled(df: pd.DataFrame) -> pd.Series:
    """D16-3 · 命中"取消单"的行掩码：InvoiceNo 以 'C' 开头（字面匹配，不做大小写转换）。"""
    return (
        df["InvoiceNo"]
        .astype("string")
        .str.startswith(CANCELLED_PREFIX)
        .fillna(False)
        .astype(bool)
    )


def mask_negative_qty(df: pd.DataFrame) -> pd.Series:
    """D16-4 · 命中"退货/负数量"的行掩码：Quantity <= 0。"""
    return df["Quantity"] <= NEGATIVE_QTY_THRESHOLD


def mask_nonpositive_price(df: pd.DataFrame) -> pd.Series:
    """D16-5 · 命中"负/零单价"的行掩码：UnitPrice <= 0。"""
    return df["UnitPrice"] <= NONPOSITIVE_PRICE_THRESHOLD


@dataclass(frozen=True)
class ExclusionRule:
    """一条排除规则（口径 + 开关），三项合起来就是 D16 的全部排除项。

    字段：
        key         统计明细 excluded_detail 里的键名（**固定**，测试会断言）
        param       compute_sales_amount(...) 上对应的开关参数名
        d16_id      对应 D16 条目编号（可对照回上面清单）
        description 人话说明
        mask        输入 DataFrame，输出布尔掩码（True = 命中该规则）
    """

    key: str
    param: str
    d16_id: str
    description: str
    mask: Callable[[pd.DataFrame], pd.Series]


# 顺序 = excluded_detail 输出顺序（与 TASK-002A 指令一致），不要随意调整
EXCLUSION_RULES: tuple[ExclusionRule, ...] = (
    ExclusionRule(
        key="cancelled",
        param="exclude_cancelled",
        d16_id="D16-3",
        description="排除取消单（InvoiceNo 以 'C' 开头）",
        mask=mask_cancelled,
    ),
    ExclusionRule(
        key="negative_qty",
        param="exclude_negative_qty",
        d16_id="D16-4",
        description="排除退货/负数量（Quantity <= 0）",
        mask=mask_negative_qty,
    ),
    ExclusionRule(
        key="nonpositive_price",
        param="exclude_nonpositive_price",
        d16_id="D16-5",
        description="排除负/零单价（UnitPrice <= 0）",
        mask=mask_nonpositive_price,
    ),
)

# 键名 → 规则，便于按 key 取用
EXCLUSION_RULE_BY_KEY = {rule.key: rule for rule in EXCLUSION_RULES}


def resolve_enabled_rules(
    exclude_cancelled: bool = True,
    exclude_negative_qty: bool = True,
    exclude_nonpositive_price: bool = True,
) -> tuple[ExclusionRule, ...]:
    """按三个开关挑出**当前启用**的排除规则（统计口径只认这个结果，见 AC-05）。"""
    flags = {
        "exclude_cancelled": bool(exclude_cancelled),
        "exclude_negative_qty": bool(exclude_negative_qty),
        "exclude_nonpositive_price": bool(exclude_nonpositive_price),
    }
    return tuple(rule for rule in EXCLUSION_RULES if flags[rule.param])


# ════════════════════════════════════════════════════════════════════════
# D16-6 · 缺失客户：CustomerID 为空 **不排除**，只在 validations 里报告行数
# ════════════════════════════════════════════════════════════════════════
def count_customer_id_nulls(df: pd.DataFrame) -> int:
    """D16-6 · CustomerID 为空（NaN）的行数。**不参与任何排除**，只作报告。"""
    return int(df["CustomerID"].isna().sum())


# ════════════════════════════════════════════════════════════════════════
# D16-7 · 重复行：**不去重**，只报告重复行数
# ════════════════════════════════════════════════════════════════════════
def count_duplicate_rows(df: pd.DataFrame) -> int:
    """D16-7 · 完全重复行的行数。

    语义（TASK-002A 指令 AC-05 指定，必须与 pandas 一致）：
        df.duplicated(keep="first").sum()
        = 每个完全重复组保留**首次出现**的行，其余"重复出现"的行计数。
    本 TASK 只报告不删除（是否去重留待 002B 决定）。
    """
    return int(df.duplicated(keep="first").sum())


# ════════════════════════════════════════════════════════════════════════
# D16-8 · 金额公式 = sum(Quantity * UnitPrice)
# ════════════════════════════════════════════════════════════════════════
LINE_AMOUNT_FORMULA = "Quantity * UnitPrice"


def line_amount(df: pd.DataFrame) -> pd.Series:
    """D16-8 · 逐行金额 = Quantity * UnitPrice（先按行相乘，再由调用方求和）。"""
    return df["Quantity"] * df["UnitPrice"]


# ════════════════════════════════════════════════════════════════════════
# 时间区间：把用户输入的起止日期规范成"含首尾全天"的筛选边界
# ════════════════════════════════════════════════════════════════════════
def to_date(value: str | _dt.date | _dt.datetime) -> _dt.date:
    """把 start/end 输入统一成 datetime.date（只取日期部分）。

    接受：'YYYY-MM-DD' / ISO 时间字符串 / datetime.date / datetime.datetime。
    含首尾全天的语义下，时间部分忽略——即使传 '2011-11-21 08:30' 也按整天 2011-11-21 算。
    """
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("时间参数不能为空字符串")
        try:
            # pd.Timestamp 能吃 'YYYY-MM-DD' 与 'YYYY-MM-DDTHH:MM:SS' 及带时区形式
            return pd.Timestamp(text).date()
        except (ValueError, TypeError) as exc:  # 输入格式错误 → 明确报错，不猜
            raise ValueError(f"无法解析时间参数：{value!r}（应形如 'YYYY-MM-DD'）") from exc
    raise TypeError(f"时间参数类型不支持：{type(value).__name__}（只接受 str/date/datetime）")


def normalize_day_range(
    start: str | _dt.date | _dt.datetime,
    end: str | _dt.date | _dt.datetime,
) -> tuple[_dt.date, _dt.date]:
    """规范化区间为 (start_date, end_date)；start > end 直接报错（不静默返回空）。"""
    start_date, end_date = to_date(start), to_date(end)
    if start_date > end_date:
        raise ValueError(f"起始日期晚于结束日期：{start_date} > {end_date}")
    return start_date, end_date


def inclusive_day_window(
    start: str | _dt.date | _dt.datetime,
    end: str | _dt.date | _dt.datetime,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """D16-2 · 含首尾全天 → 等价半开区间 [start 00:00:00, end+1天 00:00:00)。

    返回 (low_inclusive, high_exclusive)，调用方这样写筛选：
        (df[TIME_FIELD] >= low_inclusive) & (df[TIME_FIELD] < high_exclusive)
    """
    start_date, end_date = normalize_day_range(start, end)
    low = pd.Timestamp(start_date)                       # 当日 00:00:00
    high = pd.Timestamp(end_date) + _ONE_DAY             # 次日 00:00:00（不含）
    return low, high


# ════════════════════════════════════════════════════════════════════════
# D16 清单覆盖自检：8 条 → 本文件里的实现符号（AC-07 要求"逐条可对照"）
# ════════════════════════════════════════════════════════════════════════
D16_IMPLEMENTED_IDS: tuple[str, ...] = (
    "D16-1",  # 时间字段       → TIME_FIELD
    "D16-2",  # 区间语义       → inclusive_day_window
    "D16-3",  # 取消单         → mask_cancelled / EXCLUSION_RULES[0]
    "D16-4",  # 退货/负数量    → mask_negative_qty / EXCLUSION_RULES[1]
    "D16-5",  # 负/零单价      → mask_nonpositive_price / EXCLUSION_RULES[2]
    "D16-6",  # 缺失客户       → count_customer_id_nulls
    "D16-7",  # 重复行         → count_duplicate_rows
    "D16-8",  # 金额公式       → line_amount / LINE_AMOUNT_FORMULA
)
