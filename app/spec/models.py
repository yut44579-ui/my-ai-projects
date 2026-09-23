"""models.py · Report Spec（报表规格）—— 项目核心数据结构（D11 ★P0 / D12 / D13）。

一句话：把"用户想要什么报表"固化成一份**可校验、可版本化、可 JSON 往返**的结构，
之后交给确定性执行层照着跑（同一 Spec 每次结果一致 —— 报表必须可重复）。

对应决策：
    D11  Report Spec 为核心：自然语言 → AI 解析 → Spec → 用户确认 → **版本冻结** → 确定性执行
    D12  口径先定义再计算：Spec 里的 time_range / metrics[].dsl.exclude 就是"口径字段"
    D13  受限 DSL 替代"LLM 生成任意代码"：算子只允许白名单里的那几个（见 MetricOp）

字段清单（TASK-002B 指令指定，不得自行增删）：
    spec_id / name / version / data_source / time_range / metrics(受限 DSL)
    / group_by / output(template, sheet, cells) / created_at

设计约束（每条都是"防呆"，不是装饰）：
    1. extra="forbid"   —— 多输出一个字段就报错，不静默吞掉（D13：可审计、可校验）
    2. frozen=True      —— 已确认的 Spec 不允许原地改；改口径只能 bump_version() 出新版本（D11 版本冻结）
    3. 口径单一来源     —— 时间字段 / 区间语义 / 排除规则的**默认值**取自 app/engine/metrics.py（D12），
                           本文件不再抄一份，避免"两处口径漂移"
    4. cells 与 metrics 交叉校验 —— cells 里写了没声明的指标名 = 拼错，直接报错（不猜，见 CLAUDE.md 铁律 4）
    5. 可 JSON 往返     —— to_dict() 的产物能 json.dumps；from_dict() 能还原成**相等**的 Spec（可落库、可审计）

与执行层的边界：
    本文件**不做任何取数/计算/渲染**，也不知道 Excel 怎么读写；它只负责"把报表规格说清楚 + 校验合法"。
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 口径单一来源（D12）：默认时间字段 / 区间语义 / 排除规则键都从 metrics.py 取，不在这里另写一份
from app.engine.metrics import EXCLUSION_RULES, RANGE_SEMANTICS, TIME_FIELD

# ════════════════════════════════════════════════════════════════════════
# 受限 DSL 的算子白名单（D13）
# ════════════════════════════════════════════════════════════════════════
# 允许的算子**只有这些**；LLM 只能挑一个算子 + 填字段名，不能生成任意代码/表达式。
# 分两类（校验规则不同，见 MetricDSL._check_dsl_shape）：
#   聚合类（对某一列做聚合）：sum / count / count_distinct / mean  → 必须给 field
#   跨期类（引用本 Spec 内另一个指标做比较）：yoy（同比）/ previous_period（环比）→ 必须给 of_metric
MetricOp = Literal["sum", "count", "count_distinct", "mean", "yoy", "previous_period"]

_AGGREGATE_OPS: frozenset[str] = frozenset({"sum", "count", "count_distinct", "mean"})
_COMPARISON_OPS: frozenset[str] = frozenset({"yoy", "previous_period"})

# 排除规则键：与 app/engine/metrics.py 的 EXCLUSION_RULES[].key **逐字对应**（D16-3/4/5）。
# 这里写成 Literal 是为了让校验/文档一眼看到合法取值；默认值仍从 metrics.py 取（单一来源）。
ExclusionKey = Literal["cancelled", "negative_qty", "nonpositive_price"]

# D16 默认口径 = 三条排除规则全开（与 executor.compute_sales_amount 的默认开关一致）
DEFAULT_EXCLUSIONS: tuple[str, ...] = tuple(rule.key for rule in EXCLUSION_RULES)

# ════════════════════════════════════════════════════════════════════════
# 单元格地址校验（A1 ~ XFD1048576，即 Excel 的真实边界）
# ════════════════════════════════════════════════════════════════════════
_CELL_PATTERN = re.compile(r"^([A-Z]{1,3})([1-9][0-9]{0,6})$")
_MAX_COLUMN_INDEX = 16384      # XFD
_MAX_ROW_INDEX = 1048576

# 指标名：当作 JSON 键用，限制成标识符形态（防"带空格/带点"的键在模板里对不上）
_METRIC_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def column_index(letters: str) -> int:
    """'A'→1, 'B'→2, ..., 'Z'→26, 'AA'→27（Excel 列名 → 序号）。"""
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index


def validate_cell_address(address: str) -> str:
    """校验单元格地址合法（大写字母列 + 行号，且在 Excel 边界内），返回原值。

    非法直接报错并给出期望格式 —— 不猜测、不自动纠正（CLAUDE.md 铁律 4 的同一条精神）。
    """
    if not isinstance(address, str):
        raise ValueError(f"单元格地址必须是字符串，收到 {type(address).__name__}")
    match = _CELL_PATTERN.match(address)
    if not match:
        raise ValueError(
            f"单元格地址不合法：{address!r}（应形如 'B4'：**大写**列字母 + 行号，如 A1 / B12 / AA100）"
        )
    letters, row_text = match.group(1), match.group(2)
    if column_index(letters) > _MAX_COLUMN_INDEX:
        raise ValueError(f"单元格地址超出 Excel 列范围（最大 XFD）：{address!r}")
    if int(row_text) > _MAX_ROW_INDEX:
        raise ValueError(f"单元格地址超出 Excel 行范围（最大 1048576）：{address!r}")
    return address


# ════════════════════════════════════════════════════════════════════════
# 基类：所有 Spec 子模型共用同一条"严格"策略
# ════════════════════════════════════════════════════════════════════════
class _SpecBase(BaseModel):
    """Spec 内所有模型的公共配置。

    extra="forbid"        多一个字段就报错（D13：LLM 输出必须受控，不能夹带）
    frozen=True           实例不可原地改（D11：确认过的 Spec 版本冻结；改口径走 bump_version()）
    str_strip_whitespace  字符串两端空白自动去掉（路径/名字常见的复制粘贴污染）
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


# ════════════════════════════════════════════════════════════════════════
# 数据源（这一份 Spec 从哪取数）
# ════════════════════════════════════════════════════════════════════════
class DataSourceRef(_SpecBase):
    """数据源引用。

    kind="excel"：给 path（xlsx/csv 路径），可选 sheet
    kind="mysql"：给 conn（连接串）—— 本阶段只做声明与校验，取数实现不在本 TASK（D8 存储层）
    """

    kind: Literal["excel", "mysql"] = "excel"
    path: str | None = None
    sheet: str | None = None
    conn: str | None = None

    @field_validator("path")
    @classmethod
    def _check_path_suffix(cls, value: str | None) -> str | None:
        """excel 路径后缀只认 .xlsx / .xlsm（.xls 老格式 openpyxl 读不了，不猜）。"""
        if value is None:
            return value
        lowered = value.lower()
        if not lowered.endswith((".xlsx", ".xlsm")):
            raise ValueError(f"Excel 数据源只支持 .xlsx / .xlsm，收到：{value!r}")
        return value

    @model_validator(mode="after")
    def _check_required_by_kind(self) -> "DataSourceRef":
        if self.kind == "excel":
            if not self.path:
                raise ValueError("kind='excel' 时必须提供 path")
            if self.conn:
                raise ValueError("kind='excel' 时不应同时提供 conn")
        else:  # mysql
            if not self.conn:
                raise ValueError("kind='mysql' 时必须提供 conn（连接串）")
            if self.path:
                raise ValueError("kind='mysql' 时不应同时提供 path")
        return self


# ════════════════════════════════════════════════════════════════════════
# 时间区间（口径的一部分，D16-1 / D16-2）
# ════════════════════════════════════════════════════════════════════════
class TimeRange(_SpecBase):
    """报表的时间区间：**含首尾全天**（D16-2）。

    start / end 传 'YYYY-MM-DD' 字符串即可（pydantic 自动解析成 date）。
    time_field 默认取 metrics.TIME_FIELD（= 'InvoiceDate'），口径单一来源（D12）。
    semantics 是一段**说明文字**，随 Spec 一起冻结，方便审计时一眼看出按什么口径解释区间。
    """

    start: _dt.date
    end: _dt.date
    time_field: str = Field(default=TIME_FIELD, min_length=1)
    semantics: str = Field(default=RANGE_SEMANTICS, min_length=1)

    @model_validator(mode="after")
    def _check_order(self) -> "TimeRange":
        if self.start > self.end:
            raise ValueError(f"起始日期晚于结束日期：{self.start} > {self.end}")
        return self


# ════════════════════════════════════════════════════════════════════════
# 受限 DSL（D13）：指标怎么算，用**结构**描述，不用代码
# ════════════════════════════════════════════════════════════════════════
class MetricDSL(_SpecBase):
    """一个指标的受限算子描述。

    合法形态（只有这两种，其余一律报错）：
        聚合类：{"op": "sum", "field": "sales_amount"}                 ← 对某列求和
                {"op": "count", "field": "rows_in_range"}              ← 计数
        跨期类：{"op": "yoy", "of_metric": "sales_amount"}             ← 同比（引用本 Spec 内别的指标）

    exclude：本指标适用的排除规则（口径，D16-3/4/5），取值只能从 metrics.py 的规则键里挑。
             默认 = 三条全开（与 executor 默认一致）。
             注意：**同一 Spec 内所有指标的 exclude 必须一致**（当前引擎一次计算只认一套口径），
             这条约束在 ReportSpec 里统一校验。
    """

    op: MetricOp
    field: str | None = None
    of_metric: str | None = None
    exclude: tuple[ExclusionKey, ...] = DEFAULT_EXCLUSIONS

    @field_validator("exclude")
    @classmethod
    def _check_exclude_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """排除规则键必须来自 metrics.py（防 LLM 编一个 'whatever' 规则名）。"""
        known = {rule.key for rule in EXCLUSION_RULES}
        unknown = sorted(set(value) - known)
        if unknown:
            raise ValueError(
                f"未知的排除规则：{unknown}（合法取值：{sorted(known)}，定义见 app/engine/metrics.py）"
            )
        if len(set(value)) != len(value):
            raise ValueError(f"exclude 里出现重复规则：{value}")
        return value

    @model_validator(mode="after")
    def _check_dsl_shape(self) -> "MetricDSL":
        """算子与参数必须配套：聚合类要 field，跨期类要 of_metric，且不能乱给。"""
        if self.op in _AGGREGATE_OPS:
            if not self.field:
                raise ValueError(f"op={self.op!r} 必须提供 field（要对哪一列做聚合）")
            if self.of_metric:
                raise ValueError(f"op={self.op!r} 不应提供 of_metric（of_metric 只用于 {sorted(_COMPARISON_OPS)}）")
        elif self.op in _COMPARISON_OPS:
            if not self.of_metric:
                raise ValueError(f"op={self.op!r} 必须提供 of_metric（引用本 Spec 内用于比较的指标名）")
            if self.field:
                raise ValueError(f"op={self.op!r} 不应提供 field")
        return self


class MetricSpec(_SpecBase):
    """一个指标：名字（= cells 映射的键）+ 受限 DSL 定义。"""

    name: str = Field(..., min_length=1, max_length=64, pattern=_METRIC_NAME_PATTERN.pattern)
    dsl: MetricDSL


# ════════════════════════════════════════════════════════════════════════
# 输出（写哪个模板的哪个 sheet 的哪些单元格）
# ════════════════════════════════════════════════════════════════════════
class OutputSpec(_SpecBase):
    """输出位置：模板 + sheet + **指标名 → 单元格地址** 的映射。

    cells 例：{"sales_amount": "B4", "rows_in_range": "B5"}
        · 键必须是本 Spec 已声明的指标名（拼错就报错，见 ReportSpec 的交叉校验）
        · 值是**模板里预留好的数据单元格**（渲染时只改这些格，其余样式/合并/列宽全部原样保留 —— D5）
    """

    template: str = Field(..., min_length=1)
    sheet: str = Field(..., min_length=1)
    cells: dict[str, str] = Field(default_factory=dict)

    @field_validator("template")
    @classmethod
    def _check_template_suffix(cls, value: str) -> str:
        if not value.lower().endswith((".xlsx", ".xlsm")):
            raise ValueError(f"模板只支持 .xlsx / .xlsm，收到：{value!r}")
        return value

    @field_validator("cells")
    @classmethod
    def _check_cell_addresses(cls, value: dict[str, str]) -> dict[str, str]:
        for metric_name, address in value.items():
            validate_cell_address(address)
        return value


# ════════════════════════════════════════════════════════════════════════
# Report Spec（顶层）
# ════════════════════════════════════════════════════════════════════════
class ReportSpec(_SpecBase):
    """一份报表规格（顶层对象）。

    用法：
        spec = ReportSpec.from_dict({...})           # 从 JSON（AI 解析结果）还原 + 校验
        spec.to_dict()                              # 序列化（可直接 json.dumps 落库）
        spec.bump_version()                         # 改口径后升版本 → 新对象（原版本冻结，D11）
    """

    spec_id: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(..., min_length=1, max_length=200)
    version: int = Field(default=1, ge=1)
    data_source: DataSourceRef
    time_range: TimeRange
    metrics: tuple[MetricSpec, ...] = Field(..., min_length=1)
    group_by: tuple[str, ...] = ()
    output: OutputSpec
    created_at: _dt.datetime

    # ── 跨字段校验（口径一致性 / 命名唯一 / cells 与 metrics 对得上）──────
    @model_validator(mode="after")
    def _check_consistency(self) -> "ReportSpec":
        # ① 指标名唯一（否则 cells 映射会歧义）
        names = [metric.name for metric in self.metrics]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"指标名重复：{duplicates}")

        # ② 跨期算子引用的指标必须真实存在
        for metric in self.metrics:
            target = metric.dsl.of_metric
            if target and target not in names:
                raise ValueError(
                    f"指标 {metric.name!r} 的 of_metric={target!r} 未在本 Spec 中声明（可选：{names}）"
                )

        # ③ 同一 Spec 内 exclude 口径必须一致（当前引擎一次计算只认一套口径）
        distinct = {tuple(metric.dsl.exclude) for metric in self.metrics}
        if len(distinct) > 1:
            raise ValueError(
                f"同一 Spec 内各指标的 exclude 口径必须一致，实际出现 {len(distinct)} 种："
                f"{sorted(distinct)}（如需不同口径，请拆成多个 Spec）"
            )

        # ④ cells 的键必须是已声明的指标名（拼错直接报错，不静默忽略）
        unknown_cells = sorted(set(self.output.cells) - set(names))
        if unknown_cells:
            raise ValueError(
                f"output.cells 里出现未声明的指标名：{unknown_cells}（已声明：{names}）"
            )
        return self

    # ── 序列化 / 还原（D11：Spec 要能落库、能审计、能版本对比）────────────
    def to_dict(self) -> dict[str, Any]:
        """转成**纯 JSON 友好**的 dict（date/datetime → ISO 字符串），可直接 json.dumps。

        用 pydantic 的 mode="json"，不手写转换 —— 少一处可能漂移的代码。
        """
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReportSpec":
        """从 to_dict() 的产物还原成 Spec（**带完整校验**）。

        字段缺失/多余/非法一律抛 pydantic 的 ValidationError —— 不静默补默认值、不猜。
        """
        return cls.model_validate(data)

    # ── 版本管理（D11 版本冻结：改动 = 新版本，不是原地修改）─────────────
    def bump_version(self) -> "ReportSpec":
        """版本递增：返回一个新 Spec（version + 1），其余字段逐字保持不变。

        created_at 保持原值 —— 它记录的是"这条报表链路"的创建时间，不是每次改动的修改时间。
        """
        return self.model_copy(update={"version": self.version + 1})

    # ── 便利方法（上层/renderer 用，避免到处写魔法字符串）────────────────
    def metric(self, name: str) -> MetricSpec:
        """按名取指标；不存在直接 KeyError（列出可选项，不猜）。"""
        for metric in self.metrics:
            if metric.name == name:
                return metric
        raise KeyError(f"Spec {self.spec_id!r} 中没有指标 {name!r}（已声明：{[m.name for m in self.metrics]}）")

    def exclusion_keys(self) -> tuple[str, ...]:
        """本 Spec 固化的排除规则口径（三者一致，已在 _check_consistency 校验）。"""
        return tuple(self.metrics[0].dsl.exclude)

    def cell_map(self) -> dict[str, str]:
        """指标名 → 单元格地址（原样返回 output.cells）。"""
        return dict(self.output.cells)
