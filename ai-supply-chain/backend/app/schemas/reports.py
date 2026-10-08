"""汇报接口的 Pydantic 契约（TASK-007 AC1~AC6）。

★ 报告里的每个指标都是 EvidenceValue（D4）—— value/state/source_type/evidence_ref，
  前端只管显示，不得自己算业务数字。
★ page / page_size 是分页元数据，不是业务数字，正常返回裸值。
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator

from app.models.report import ReportSourceType, ReportType
from app.schemas.evidence import EvidenceValue


class ReportGenerateRequest(BaseModel):
    """POST /api/reports/generate 的入参。"""

    report_type: ReportType
    period_start: date = Field(description="统计期间起点（含）")
    period_end: date = Field(description="统计期间终点（含）")

    @model_validator(mode="after")
    def _check_period(self) -> "ReportGenerateRequest":
        if self.period_start > self.period_end:
            raise ValueError("period_start 不能晚于 period_end")
        return self


class ReportSummary(BaseModel):
    """历史报告列表项（不含指标明细，前端点进去看详情）。"""

    id: int
    report_type: ReportType
    period_start: date
    period_end: date
    timezone: str
    generated_at: datetime
    source_type: ReportSourceType
    generated_by: str
    excluded_test_count: int = Field(description="本期被排除的 TEST 来源客户条数")


class ReportDetail(BaseModel):
    """单份报告的全部指标（快照，读的是 content_json，永不重算）。"""

    id: int
    report_type: ReportType
    period_start: date
    period_end: date
    timezone: str
    generated_at: datetime
    source_type: ReportSourceType
    generated_by: str
    excluded_test_count: int
    # ── TASK-039 文字描述与走势 ──
    narrative: list[dict] = Field(
        default_factory=list,
        description="文字描述（按真实数字由代码拼装，与指标卡同源，不调 AI）",
    )
    no_data: list[str] = Field(
        default_factory=list, description="本期取不到数据的指标（避免把缺失当成 0）"
    )
    trend: list[dict] = Field(
        default_factory=list, description="走势序列；超过 31 天按周聚合"
    )
    metrics: dict[str, EvidenceValue] = Field(
        description="指标 key -> EvidenceValue（含各自的 evidence_ref）"
    )


class ReportListResponse(BaseModel):
    items: list[ReportSummary]
    page: int
    page_size: int
    total: EvidenceValue = Field(description="历史报告总条数（业务数字）")


class ReportDrilldownRow(BaseModel):
    """下钻的一行。★ TASK-023 起跨实体统一形态。

    为什么不再直接返回 CustomerItem：指标已经扩到商机/项目/风险/内容，
    如果下钻只返回客户，商机类指标点开就是空的，§十九 的"可追溯"就断了。
    """

    id: int
    kind: str = Field(description="customer / opportunity / project / risk / content")
    title: str
    subtitle: str | None = None
    occurred_at: datetime | None = None
    evidence_ref: str | None = None
    extra: dict = Field(default_factory=dict, description="如 customer_id，便于前端跳转")


class ReportMetricDef(BaseModel):
    """指标定义（前端据此分组展示，不自己硬编码指标清单）。"""

    key: str
    label: str
    group: str = Field(description="归属的汇报内容分类（§十八）")
    row_kind: str
    period_bounded: bool


class ReportDrilldownResponse(BaseModel):
    """下钻明细：构成指标的真实行，含 EvidenceValue 形态的总数。"""

    metric: str
    metric_label: str
    period_start: date
    period_end: date
    page: int
    page_size: int
    total: EvidenceValue = Field(description="★ 与报告里的数字同一个口径，必须相等")
    evidence_ref: str
    items: list[ReportDrilldownRow]


class ReportMetricCatalogResponse(BaseModel):
    """指标目录：前端用它渲染"这份报告有哪些指标、按什么分组"。"""

    groups: list[str]
    metrics: list[ReportMetricDef]
    note: str
