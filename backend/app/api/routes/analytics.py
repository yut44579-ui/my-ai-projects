"""数据分析接口（TASK-012）。

═══════════════════════════════════════════════════════════════════════
【漏斗为什么必须用累计口径（本模块最容易做错的地方）】
═══════════════════════════════════════════════════════════════════════
lifecycle_status 是**离散状态**，不是阶段进度。若直接按当前状态计数：
    未联系 10 / 已联系 1 / 已回复 0 / 已成交 0
会得出"漏斗第一级 10、第二级 1"，但也会有"已成交 0 → 已报价 2"这种
**越往后越大**的错结论（因为"已报价"的人不会被计入"已联系"）。

正确做法：给每个状态一个 RANK，累计"达到或超过"该 rank 的人数：
    未联系 ≥1、已联系 ≥2、已回复 ≥3、持续沟通 ≥4、已报价 ≥5、已成交 ≥6
    已失效（LOST）是**旁支**终态，不进漏斗主线，单独统计。

═══════════════════════════════════════════════════════════════════════
【转化率】
═══════════════════════════════════════════════════════════════════════
分母为 0 → null（不是 0%）；样本量 < MIN_SAMPLE → null。
"0% 转化"和"没有数据可算"是两件事，绝不能混（D4 的三态原则）。

★ 本模块不调用 LLM。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.customer import Customer, CustomerSourceType, DedupeState, LifecycleStatus
from app.models.customer_event import CustomerEvent, CustomerEventType
from app.schemas.analytics import (
    AnalyticsResponse,
    FunnelStage,
    QualityIssue,
    SourceQuality,
    TrendPoint,
)
from app.schemas.evidence import SourceType, build_evidence, no_data_evidence

router = APIRouter(prefix="/analytics", tags=["analytics"])

#: D6：时区固定 Asia/Shanghai（所有"今天/本周"的边界都按它算）
TZ = ZoneInfo("Asia/Shanghai")

#: 样本量阈值：低于它不给比率结论
MIN_SAMPLE = 10

TREND_DAYS = 14

LIFECYCLE_LABELS: dict[LifecycleStatus, str] = {
    LifecycleStatus.NEW: "未联系",
    LifecycleStatus.CONTACTED: "已联系",
    LifecycleStatus.REPLIED: "已回复",
    LifecycleStatus.ENGAGED: "持续沟通",
    LifecycleStatus.QUOTED: "已报价",
    LifecycleStatus.WON: "已成交",
    LifecycleStatus.LOST: "已失效",
}

#: 漏斗主线（LOST 是旁支终态，不进主线）
FUNNEL_ORDER: tuple[LifecycleStatus, ...] = (
    LifecycleStatus.NEW,
    LifecycleStatus.CONTACTED,
    LifecycleStatus.REPLIED,
    LifecycleStatus.ENGAGED,
    LifecycleStatus.QUOTED,
    LifecycleStatus.WON,
)

#: 每个状态的 rank：越大越靠后。★ 累计口径的依据
RANK: dict[LifecycleStatus, int] = {s: i + 1 for i, s in enumerate(FUNNEL_ORDER)}

SOURCE_LABELS: dict[CustomerSourceType, str] = {
    CustomerSourceType.REAL: "真实业务数据",
    CustomerSourceType.TEST: "测试数据",
    CustomerSourceType.MANUAL: "人工录入",
}


def _ratio(numerator: int, denominator: int, sample: int) -> float | None:
    """转化率。★ 分母为 0 或样本不足 → null（不是 0）。"""
    if not denominator or sample < MIN_SAMPLE:
        return None
    return numerator / denominator


def _week_start(now_utc: datetime) -> datetime:
    """本地周一 00:00（与 customers.py 的 _week_start 同口径）。"""
    local = now_utc.astimezone(TZ)
    monday = local - timedelta(days=local.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


@router.get("/overview", response_model=AnalyticsResponse, summary="数据分析概览（趋势/漏斗/质量）")
def analytics_overview(
    days: int = TREND_DAYS, db: Session = Depends(get_db)
) -> AnalyticsResponse:
    """数据分析概览。★ 不调 LLM：全部是分组计数与累计判定。"""
    now = datetime.now(timezone.utc)
    week_start = _week_start(now)

    total = db.scalar(select(func.count(Customer.id))) or 0
    new_this_week = (
        db.scalar(select(func.count(Customer.id)).where(Customer.first_seen_at >= week_start)) or 0
    )

    def ev(count: int, ref: str, empty: str):
        if count:
            return build_evidence(count, source_type=SourceType.SYSTEM, evidence_ref=ref)
        return no_data_evidence(empty, source_type=SourceType.SYSTEM, evidence_ref=ref)

    # ── ① 趋势：近 N 天，每天新增客户 / 事件 / 访问 ──
    today_local = now.astimezone(TZ).date()
    start_local = today_local - timedelta(days=days - 1)

    new_by_day: dict[str, int] = {}
    for (ts,) in db.execute(select(Customer.first_seen_at)).all():
        if ts is None:
            continue
        d = ts.astimezone(TZ).date() if ts.tzinfo else ts.date()
        if d >= start_local:
            new_by_day[d.isoformat()] = new_by_day.get(d.isoformat(), 0) + 1

    events_by_day: dict[str, int] = {}
    visits_by_day: dict[str, int] = {}
    for ts, et in db.execute(select(CustomerEvent.created_at, CustomerEvent.event_type)).all():
        if ts is None:
            continue
        d = ts.astimezone(TZ).date() if ts.tzinfo else ts.date()
        if d < start_local:
            continue
        events_by_day[d.isoformat()] = events_by_day.get(d.isoformat(), 0) + 1
        if et == CustomerEventType.VISIT:
            visits_by_day[d.isoformat()] = visits_by_day.get(d.isoformat(), 0) + 1

    trend: list[TrendPoint] = []
    for i in range(days):
        d = (start_local + timedelta(days=i)).isoformat()
        trend.append(
            TrendPoint(
                day=d,
                new_customers=new_by_day.get(d, 0),
                events=events_by_day.get(d, 0),
                visits=visits_by_day.get(d, 0),
            )
        )

    # ── ② 漏斗：累计口径 ──
    status_rows = db.execute(
        select(Customer.lifecycle_status, func.count(Customer.id)).group_by(
            Customer.lifecycle_status
        )
    ).all()
    status_count = {st: int(n) for st, n in status_rows}

    funnel: list[FunnelStage] = []
    prev_reached: int | None = None
    for st in FUNNEL_ORDER:
        rank = RANK[st]
        # 累计：所有 rank >= 当前 rank 的状态人数之和（排除 LOST 旁支）
        reached = sum(n for s, n in status_count.items() if s in RANK and RANK[s] >= rank)
        funnel.append(
            FunnelStage(
                status=st.value,
                label=LIFECYCLE_LABELS[st],
                reached=reached,
                conversion_from_prev=(
                    None
                    if prev_reached is None
                    else _ratio(reached, prev_reached, total)
                ),
                conversion_from_total=_ratio(reached, total, total),
            )
        )
        prev_reached = reached

    # ── ③ 数据质量 ──
    no_phone = db.scalar(select(func.count(Customer.id)).where(Customer.phone.is_(None))) or 0
    no_email = db.scalar(select(func.count(Customer.id)).where(Customer.email.is_(None))) or 0
    no_company = (
        db.scalar(
            select(func.count(Customer.id)).where(
                or_(Customer.company_name.is_(None), Customer.company_name == "")
            )
        )
        or 0
    )
    no_region = (
        db.scalar(
            select(func.count(Customer.id)).where(
                or_(Customer.region.is_(None), Customer.region == "")
            )
        )
        or 0
    )
    pending_review = (
        db.scalar(
            select(func.count(Customer.id)).where(
                Customer.dedupe_state == DedupeState.PENDING_REVIEW
            )
        )
        or 0
    )

    quality: list[QualityIssue] = []
    if no_phone:
        quality.append(
            QualityIssue(
                key="no_phone", label="缺少电话", count=no_phone, severity="HIGH",
                hint="无法外呼联系，建议补齐或标记为不可用",
                evidence_ref="analytics:quality:no_phone",
            )
        )
    if no_email:
        quality.append(
            QualityIssue(
                key="no_email", label="缺少邮箱", count=no_email, severity="MEDIUM",
                hint="不影响外呼，但无法走邮件触达",
                evidence_ref="analytics:quality:no_email",
            )
        )
    if no_company:
        quality.append(
            QualityIssue(
                key="no_company", label="缺少公司名", count=no_company, severity="MEDIUM",
                hint="无法判断企业背景与规模，影响分级",
                evidence_ref="analytics:quality:no_company",
            )
        )
    if no_region:
        quality.append(
            QualityIssue(
                key="no_region", label="缺少地区", count=no_region, severity="LOW",
                hint="地区统计会把这些人归到「未填写」",
                evidence_ref="analytics:quality:no_region",
            )
        )
    if pending_review:
        quality.append(
            QualityIssue(
                key="pending_review", label="去重待裁决", count=pending_review, severity="HIGH",
                hint="可能是同一客户的重复记录，数字会被重复计算",
                evidence_ref="analytics:quality:pending_review",
            )
        )

    # 没有任何质量问题的客户数（各问题集合可能重叠，这里按"都不命中"算）
    problem_ids: set[int] = set()
    for col_is_null in (
        Customer.phone.is_(None),
        Customer.email.is_(None),
        or_(Customer.company_name.is_(None), Customer.company_name == ""),
        or_(Customer.region.is_(None), Customer.region == ""),
    ):
        problem_ids.update(db.scalars(select(Customer.id).where(col_is_null)).all())
    problem_ids.update(
        db.scalars(
            select(Customer.id).where(Customer.dedupe_state == DedupeState.PENDING_REVIEW)
        ).all()
    )
    quality_ok = max(total - len(problem_ids), 0)

    # ── ④ 来源质量 ──
    source_rows = db.execute(
        select(Customer.source_type, func.count(Customer.id)).group_by(Customer.source_type)
    ).all()
    source_quality = [
        SourceQuality(
            source_type=st.value,
            label=SOURCE_LABELS.get(st, st.value),
            count=int(n),
            share=(int(n) / total) if total >= MIN_SAMPLE else None,
        )
        for st, n in source_rows
    ]

    # ── ⑤ 活跃客户（有事件留痕）──
    active = (
        db.scalar(select(func.count(func.distinct(CustomerEvent.customer_id)))) or 0
    )

    sufficient = total >= MIN_SAMPLE

    return AnalyticsResponse(
        generated_at=now,
        total=ev(total, "analytics:total", "还没有客户数据"),
        new_this_week=ev(new_this_week, "analytics:new_this_week", "本周没有新增客户"),
        active_customers=ev(active, "analytics:active_customers", "还没有客户产生事件"),
        trend_days=days,
        trend=trend,
        funnel=funnel,
        quality=quality,
        quality_ok_total=ev(quality_ok, "analytics:quality_ok", "没有客户数据"),
        source_quality=source_quality,
        sample_sufficient=sufficient,
        note=(
            f"趋势按 Asia/Shanghai 归日，近 {days} 天（含 0 值，便于看断档）；"
            "漏斗为累计口径（达到或超过该阶段）；"
            + ("" if sufficient else f"样本量少于 {MIN_SAMPLE}，因此不给转化率。")
        ),
    )
