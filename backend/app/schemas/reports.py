"""汇报接口的 Pydantic 契约（TASK-007 AC1~AC6）。

★ 报告里的每个指标都是 EvidenceValue（D4）—— value/state/source_type/evidence_ref，
  前端只管显示，不得自己算业务数字。
★ page / page_size 是分页元数据，不是业务数字，正常返回裸值。
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator

from app.models.report import ReportSourceType, ReportType
from app.schemas.customers import CustomerItem
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
    metrics: dict[str, EvidenceValue] = Field(
        description="指标 key -> EvidenceValue（含各自的 evidence_ref）"
    )


class ReportListResponse(BaseModel):
    items: list[ReportSummary]
    page: int
    page_size: int
    total: EvidenceValue = Field(description="历史报告总条数（业务数字）")


class ReportDrilldownResponse(BaseModel):
    """下钻明细：构成指标的真实客户行，含 EvidenceValue 形态的总数。"""

    metric: str
    period_start: date
    period_end: date
    page: int
    page_size: int
    total: EvidenceValue = Field(description="★ 与报告里的数字同一个口径，必须相等")
    evidence_ref: str
    items: list[CustomerItem]
