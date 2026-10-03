"""汇报服务：指标口径、快照生成、下钻取数（TASK-007）。

三条铁律，本文件是唯一执行点：

1. **口径唯一**：`metric_conditions()` 是统计口径的唯一定义处。生成报告与下钻
   都必须调它 —— 两边若各写一份 where 条件，迟早会漂移（下钻条数 != 报告数字）。
2. **快照语义**（D6）：报告数字只在生成那一刻算一次，写进 content_json；
   读取接口永不重算，客户被删/改也不影响已生成的报告。
3. **NO_DATA 不是 0**：本期该口径下没有任何 REAL 来源客户（count == 0）时，
   指标返回 {"value": null, "state": "NO_DATA"}，绝不用 0 冒充"没有数据"。
   本模块这两个指标都是"REAL 客户行计数"，数出 0 就意味着库里根本没有可支撑
   这个数字的真实数据 —— 所以 0 一律落 NO_DATA（与 D4 的 VALID-0 严格区分：
   我们不会伪造一个 VALID-0）。

默认只统计 source_type='REAL'（docs/IMPORT_RULES.md §六）：TEST 数据可以进库
方便跑通链路，但绝不能进业务数字；被排除的 TEST 条数记进 excluded_test_count，
让"排除了多少"在报告里可见。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.models.customer import Customer, CustomerSourceType
from app.models.report import REPORT_TIMEZONE, Report, ReportSourceType, ReportType
from app.schemas.evidence import EvidenceValue, SourceType, ValueState


class UnknownMetricError(ValueError):
    """下钻/取数遇到了白名单之外的指标 key（路由层翻译成 400）。"""


@dataclass(frozen=True)
class MetricDef:
    """指标定义。period_bounded=True 表示口径要按期间过滤。"""

    key: str
    label: str
    period_bounded: bool


# ★ 指标白名单。本期只做这两个（其余指标依赖尚未落地的表，不做）。
METRICS: dict[str, MetricDef] = {
    "customer_total": MetricDef(
        key="customer_total", label="客户总数（REAL）", period_bounded=False
    ),
    "customer_new": MetricDef(
        key="customer_new", label="期间新增客户（REAL）", period_bounded=True
    ),
}

METRIC_KEYS: tuple[str, ...] = tuple(METRICS)


def _period_bounds(period_start: date, period_end: date) -> tuple[datetime, datetime]:
    """期间 -> [start, end) 半开区间（Asia/Shanghai 本地时间，含 period_end 当天）。

    库里 first_seen_at 存的就是服务端本地时间（server now()），与 D6 的
    Asia/Shanghai 一致，因此这里用朴素 datetime 直接比较即可。
    """
    start = datetime.combine(period_start, time.min)
    end_exclusive = datetime.combine(period_end + timedelta(days=1), time.min)
    return start, end_exclusive


def metric_conditions(
    metric_key: str, period_start: date, period_end: date
) -> list[ColumnElement[bool]]:
    """★ 统计口径的唯一来源：生成报告与下钻都调这个函数。"""
    if metric_key not in METRICS:
        raise UnknownMetricError(f"未知指标：{metric_key}")
    definition = METRICS[metric_key]

    conditions: list[ColumnElement[bool]] = [Customer.source_type == CustomerSourceType.REAL]
    if definition.period_bounded:
        start, end_exclusive = _period_bounds(period_start, period_end)
        conditions.append(Customer.first_seen_at >= start)
        conditions.append(Customer.first_seen_at < end_exclusive)
    return conditions


def evidence_ref_for(metric_key: str, period_start: date, period_end: date) -> str:
    """指标的证据锚点：可反查、可复现（下钻用同一份 ref 语义）。"""
    definition = METRICS.get(metric_key)
    if definition is None:
        raise UnknownMetricError(f"未知指标：{metric_key}")
    ref = f"customers:source_type=REAL|metric={metric_key}"
    if definition.period_bounded:
        ref += f"|period={period_start.isoformat()}..{period_end.isoformat()}"
    return ref[:128]


def count_metric(db: Session, metric_key: str, period_start: date, period_end: date) -> int:
    """按统一下口径数一行。★ 下钻的 total 也调它，保证两边同一个数。"""
    conditions = metric_conditions(metric_key, period_start, period_end)
    return int(
        db.execute(select(func.count()).select_from(Customer).where(*conditions)).scalar_one()
    )


def count_excluded_test(db: Session, period_start: date, period_end: date) -> int:
    """本期被排除在业务数字之外的 TEST 客户条数（按 first_seen_at 落期间内）。

    与 business 口径对称：TEST 行同样用期间过滤，"排除了多少本期的测试数据"
    才对得上报告期间的含义。分母口径仍然只认 REAL，不受影响。
    """
    start, end_exclusive = _period_bounds(period_start, period_end)
    return int(
        db.execute(
            select(func.count())
            .select_from(Customer)
            .where(
                Customer.source_type == CustomerSourceType.TEST,
                Customer.first_seen_at >= start,
                Customer.first_seen_at < end_exclusive,
            )
        ).scalar_one()
    )


def build_metric_evidence(
    db: Session, metric_key: str, period_start: date, period_end: date
) -> EvidenceValue:
    """取数并包成 EvidenceValue。count == 0 -> NO_DATA（见模块头 §3）。"""
    count = count_metric(db, metric_key, period_start, period_end)
    ref = evidence_ref_for(metric_key, period_start, period_end)
    if count == 0:
        return EvidenceValue(
            value=None,
            state=ValueState.NO_DATA,
            source_type=SourceType.IMPORT,
            evidence_ref=ref,
            reason=f"期间内无 REAL 来源客户数据（{METRICS[metric_key].label}）",
        )
    return EvidenceValue(
        value=count,
        state=ValueState.VALID,
        source_type=SourceType.IMPORT,
        evidence_ref=ref,
    )


def generate_report(
    db: Session,
    *,
    report_type: ReportType,
    period_start: date,
    period_end: date,
) -> Report:
    """生成快照：数字与 evidence_ref 就地写进 content_json，之后不再变。"""
    if period_start > period_end:
        raise ValueError("period_start 不能晚于 period_end")

    metrics: dict[str, dict] = {}
    for key in METRIC_KEYS:
        evidence = build_metric_evidence(db, key, period_start, period_end)
        # exclude_none：VALID 指标就恰好是契约要求的 4 个键；NO_DATA 才带 reason。
        metrics[key] = evidence.model_dump(mode="json", exclude_none=True)

    report = Report(
        report_type=report_type,
        period_start=period_start,
        period_end=period_end,
        timezone=REPORT_TIMEZONE,
        content_json={"metrics": metrics},
        source_type=ReportSourceType.IMPORT,
        generated_by="system",
        excluded_test_count=count_excluded_test(db, period_start, period_end),
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def drilldown_metric(
    db: Session,
    *,
    metric_key: str,
    period_start: date,
    period_end: date,
    page: int,
    page_size: int,
) -> tuple[list[Customer], int]:
    """下钻：返回构成该指标的真实客户明细行（分页）+ 总条数。

    ★ 筛选条件来自 metric_conditions()，与生成报告时完全一致 ——
      因此"下钻条数 == 报告里的数字"是可断言的。
    """
    conditions = metric_conditions(metric_key, period_start, period_end)
    total = count_metric(db, metric_key, period_start, period_end)
    rows = (
        db.execute(
            select(Customer)
            .where(*conditions)
            .order_by(Customer.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(rows), total
