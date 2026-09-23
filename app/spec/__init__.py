"""spec 包：Report Spec（报表规格）—— 项目核心数据结构（D11）。

分层约定（见 CLAUDE.md）：
    spec/ 只描述"要什么报表"（口径 + 输出位置），**不做任何取数与计算**
    engine/ 照着 Spec 做确定性取数、计算、渲染（数字一律由代码算 —— D4）

本 TASK（002B）只落地 Spec 模型本身；执行入口在 app/engine/executor.py（002A）与
app/engine/renderer.py（002B）。
"""

from app.spec.models import (
    DEFAULT_EXCLUSIONS,
    DataSourceRef,
    MetricDSL,
    MetricSpec,
    OutputSpec,
    ReportSpec,
    TimeRange,
)

__all__ = [
    "DEFAULT_EXCLUSIONS",
    "DataSourceRef",
    "MetricDSL",
    "MetricSpec",
    "OutputSpec",
    "ReportSpec",
    "TimeRange",
]
