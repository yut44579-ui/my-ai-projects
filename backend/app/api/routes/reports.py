"""汇报接口（TASK-007 §二）：生成快照 / 历史列表 / 报告详情 / 下钻。

只有这四个接口。路由层只做 HTTP 转换，口径与取数全在 services/reports.py。
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.report import Report
from app.schemas.customers import CustomerItem
from app.schemas.evidence import EvidenceValue, SourceType, build_evidence
from app.schemas.reports import (
    ReportDetail,
    ReportDrilldownResponse,
    ReportGenerateRequest,
    ReportListResponse,
    ReportSummary,
)
from app.services.reports import (
    METRIC_KEYS,
    METRICS,
    UnknownMetricError,
    build_metric_evidence,
    drilldown_metric,
    generate_report,
)

router = APIRouter(tags=["reports"])

MAX_PAGE_SIZE = 100


def _detail(report: Report) -> ReportDetail:
    """从快照 content_json 还原指标 —— 只读，绝不重算（D6）。"""
    return ReportDetail(
        id=report.id,
        report_type=report.report_type,
        period_start=report.period_start,
        period_end=report.period_end,
        timezone=report.timezone,
        generated_at=report.generated_at,
        source_type=report.source_type,
        generated_by=report.generated_by,
        excluded_test_count=report.excluded_test_count,
        metrics=(report.content_json or {}).get("metrics", {}),
    )


@router.post(
    "/reports/generate",
    response_model=ReportDetail,
    status_code=status.HTTP_201_CREATED,
    summary="生成汇报快照",
)
def generate(payload: ReportGenerateRequest, db: Session = Depends(get_db)) -> ReportDetail:
    """生成快照：默认只统计 source_type='REAL'；TEST 条数记进 excluded_test_count。

    ★ 没有 REAL 数据时，指标是 {"value": null, "state": "NO_DATA"} —— 不是 0。
    """
    report = generate_report(
        db,
        report_type=payload.report_type,
        period_start=payload.period_start,
        period_end=payload.period_end,
    )
    return _detail(report)


@router.get("/reports", response_model=ReportListResponse, summary="历史报告列表（分页）")
def list_reports(
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=MAX_PAGE_SIZE),
) -> ReportListResponse:
    total = db.execute(select(func.count()).select_from(Report)).scalar_one()
    reports = (
        db.execute(
            select(Report)
            .order_by(Report.generated_at.desc(), Report.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return ReportListResponse(
        items=[
            ReportSummary(
                id=r.id,
                report_type=r.report_type,
                period_start=r.period_start,
                period_end=r.period_end,
                timezone=r.timezone,
                generated_at=r.generated_at,
                source_type=r.source_type,
                generated_by=r.generated_by,
                excluded_test_count=r.excluded_test_count,
            )
            for r in reports
        ],
        page=page,
        page_size=page_size,
        total=build_evidence(
            total, source_type=SourceType.SYSTEM, evidence_ref="reports:count"
        ),
    )


@router.get("/reports/drilldown", response_model=ReportDrilldownResponse, summary="指标下钻（客户明细）")
def drilldown(
    db: Session = Depends(get_db),
    metric: str = Query(..., description=f"指标 key，白名单：{' / '.join(METRIC_KEYS)}"),
    period_start: date = Query(..., description="与生成报告时同一个期间起点"),
    period_end: date = Query(..., description="与生成报告时同一个期间终点"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=MAX_PAGE_SIZE),
) -> ReportDrilldownResponse:
    """★ 筛选条件与生成报告时完全一致（共用 metric_conditions），

    因此"下钻条数 == 报告里的数字"。metric 不在白名单 → 400。
    """
    # ★ 本路由必须声明在 /reports/{report_id} 之前，否则 "drilldown" 会被当成 id 解析。
    if metric not in METRICS:
        raise HTTPException(
            status_code=400,
            detail=f"metric 只能是 {' / '.join(METRIC_KEYS)}，收到：{metric!r}",
        )
    if period_start > period_end:
        raise HTTPException(status_code=400, detail="period_start 不能晚于 period_end")

    try:
        rows, _ = drilldown_metric(
            db,
            metric_key=metric,
            period_start=period_start,
            period_end=period_end,
            page=page,
            page_size=page_size,
        )
    except UnknownMetricError as exc:  # pragma: no cover - 上面已挡住
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    total_evidence: EvidenceValue = build_metric_evidence(db, metric, period_start, period_end)

    return ReportDrilldownResponse(
        metric=metric,
        period_start=period_start,
        period_end=period_end,
        page=page,
        page_size=page_size,
        total=total_evidence,
        evidence_ref=total_evidence.evidence_ref or "",
        items=[CustomerItem.model_validate(c) for c in rows],
    )


@router.get("/reports/{report_id}", response_model=ReportDetail, summary="报告详情（全部指标）")
def get_report(report_id: int, db: Session = Depends(get_db)) -> ReportDetail:
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"报告 {report_id} 不存在")
    return _detail(report)
