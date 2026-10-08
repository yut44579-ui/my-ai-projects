"""工作台首页聚合接口（TASK-008）。

═══════════════════════════════════════════════════════════════════════
【这个接口只回答六个问题】（对齐首页三栏布局）
═══════════════════════════════════════════════════════════════════════
    今天要做什么        → tasks / pending
    客户发生了什么      → recent_activities
    哪些事情需要人工    → pending / kpis.risk_customers
    数据是否正常        → data_connections
    AI 可以帮我什么     → ai_assistant（静态能力说明）
    今天汇报在哪里      → today_report

**不返回**架构图、技术栈、Agent 流程 —— 那些属于开发内部资料（总控提示词 §七）。

═══════════════════════════════════════════════════════════════════════
【真实数据原则（最高优先级）】
═══════════════════════════════════════════════════════════════════════
  · 商机数量 / 成交订单：V1 **没有对应数据模型** → 恒返回 NO_DATA（界面显示「—」）。
    绝不为了凑满 5 张 KPI 卡而编一个数字。
  · 图表在零数据时返回**空数组**，前端画空态；绝不编造比例。
  · 分类计数与明细来自同一批数据，因此两者**必然同口径**。

═══════════════════════════════════════════════════════════════════════
【实现要点】
═══════════════════════════════════════════════════════════════════════
  · 一次并集查询取候选客户，再在 Python 里按优先级归类（避免 4 次往返 + 4 套口径）；
  · 「哪些客户有 AI 失败消息」用**一次 IN 查询**，不做 N+1；
  · 时间口径与 /api/customers/stats 保持一致（本周一 00:00 起），否则首页与客户页数字会打架。
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.customer import (
    Customer,
    CustomerSourceType,
    DedupeState,
    HandoverState,
    LifecycleStatus,
)
from app.models.customer_event import CustomerEvent
from app.models.customer_message import AiStatus, CustomerMessage
# ★ 必须用别名：本模块还有一个 **Pydantic schema** 也叫 DataConnection
#   （schemas/dashboard.py 的 DataConnectionsBlock 用的），同名会互相覆盖 ——
#   实测把 ORM 模型直接导成 DataConnection 后，构造响应的那行报了
#   "Column expression ... expected, got <class 'app.schemas.dashboard.DataConnection'>"。
from app.models.data_connection import (
    ConnectionStatus,
    ConnectorKind,
    DataConnection as DataConnectionRow,
)

#: 连接器类型的中文名。★ 首页只读展示用；与数据连接页的 KIND_LABELS 保持一致，
#: 避免同一类型在两处显示不同的名字（同 D46 的教训）。
CONNECTOR_KIND_LABELS: dict[ConnectorKind, str] = {
    ConnectorKind.EXCEL_CSV: "Excel / CSV",
    ConnectorKind.MYSQL: "MySQL",
    ConnectorKind.POSTGRESQL: "PostgreSQL",
    ConnectorKind.CRM: "CRM",
    ConnectorKind.ERP: "ERP",
    ConnectorKind.WECHAT_WORK: "企业微信",
    ConnectorKind.WEBSITE: "官网",
    ConnectorKind.API: "API 接口",
    ConnectorKind.WEBHOOK: "Webhook",
}
from app.models.import_batch import ImportBatch, ImportBatchStatus
from app.models.opportunity import CLOSED_STAGES, WON_STAGE, Opportunity
from app.models.report import Report
from app.schemas.dashboard import (
    TASK_REASON_DETAILS,
    TASK_REASON_LABELS,
    ActivityItem,
    AiAssistantBlock,
    AiCapability,
    DashboardSummaryResponse,
    DataConnection,
    DataConnectionsBlock,
    GreetingBlock,
    GrowthTrend,
    KpiBlock,
    PendingBlock,
    RecentActivities,
    RegionDistribution,
    RegionSlice,
    ReportDigest,
    ReportMetric,
    SourceBreakdown,
    SourceSlice,
    TaskCount,
    TaskItem,
    TaskReason,
    TaskSection,
    TrendPoint,
)
from app.schemas.evidence import SourceType, build_evidence, no_data_evidence
from app.schemas.visits import VISIT_CHANNELS
from app.services.capabilities import BLOCKED, CAPABILITIES, UNAVAILABLE
# ★ 指标展示名的**单一事实来源**：不在这里另维护一份标签表（见 _metric_label 的注释）
from app.services.reports import METRICS

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

#: 首页「今日重点任务」最多显示多少条。
#: ★ 刻意收短（参考图里任务是「先看什么」，下面还要露出客户分布/增长趋势/最近动态）。
#:   完整列表在「客户管理」页；这里排太多会把下方图表挤出首屏。
TASK_PREVIEW_LIMIT = 5
#: 「最近客户动态」最多显示多少条
ACTIVITY_LIMIT = 8
#: 增长趋势默认看多少天
TREND_DAYS = 7

REPORT_TZ = ZoneInfo("Asia/Shanghai")

#: customers.region 为空时的归并标签（如实标注，不猜是哪个区）
REGION_UNKNOWN = "未填写"


# ══════════════════════════════════════════════════════════════════════
# 时间口径（必须与 customers.py 的「本周新增」一致）
# ══════════════════════════════════════════════════════════════════════
def _week_start(now: datetime) -> datetime:
    monday = now - timedelta(days=now.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def _salutation(now: datetime) -> str:
    hour = now.astimezone(REPORT_TZ).hour
    if hour < 6:
        return "凌晨好"
    if hour < 12:
        return "早上好"
    if hour < 18:
        return "下午好"
    return "晚上好"


# ══════════════════════════════════════════════════════════════════════
# 候选客户归类（tasks 与 pending 共用同一次查询结果，保证口径一致）
# ══════════════════════════════════════════════════════════════════════
def _failed_ai_exists():
    return (
        select(CustomerMessage.id)
        .where(
            CustomerMessage.customer_id == Customer.id,
            CustomerMessage.ai_status == AiStatus.FAILED,
        )
        .exists()
    )


def _categorize(db: Session, week_start: datetime) -> dict[TaskReason, list[Customer]]:
    """取候选客户并按原因归类。一条客户只进一类（顺序即优先级），不重复出现在多张卡里。"""
    candidates = db.scalars(
        select(Customer)
        .where(
            (Customer.handover_state != HandoverState.AUTO)
            | (Customer.dedupe_state == DedupeState.PENDING_REVIEW)
            | (Customer.first_seen_at >= week_start)
            | _failed_ai_exists()
        )
        .order_by(Customer.updated_at.desc(), Customer.id.desc())
        .limit(500)
    ).all()

    buckets: dict[TaskReason, list[Customer]] = {reason: [] for reason in TaskReason}
    if not candidates:
        return buckets

    # 一次查出「哪些客户有 AI 失败消息」，避免 N+1
    ids = [c.id for c in candidates]
    failed_ids = set(
        db.scalars(
            select(CustomerMessage.customer_id)
            .where(
                CustomerMessage.customer_id.in_(ids),
                CustomerMessage.ai_status == AiStatus.FAILED,
            )
            .distinct()
        ).all()
    )

    for customer in candidates:
        if customer.handover_state == HandoverState.HUMAN_REQUIRED:
            reason = TaskReason.HUMAN_REQUIRED
        elif customer.handover_state == HandoverState.HUMAN_ACTIVE:
            reason = TaskReason.HUMAN_ACTIVE
        elif customer.id in failed_ids:
            reason = TaskReason.AI_FAILED
        elif customer.dedupe_state == DedupeState.PENDING_REVIEW:
            reason = TaskReason.PENDING_REVIEW
        elif customer.lifecycle_status == LifecycleStatus.NEW:
            reason = TaskReason.NEW
        else:
            continue
        buckets[reason].append(customer)
    return buckets


def _task_item(customer: Customer, reason: TaskReason) -> TaskItem:
    occurred = customer.first_seen_at if reason == TaskReason.NEW else customer.updated_at
    return TaskItem(
        id=customer.id,
        name=customer.name,
        company_name=customer.company_name,
        source_type=customer.source_type,
        lifecycle_status=customer.lifecycle_status,
        handover_state=customer.handover_state,
        reason=reason,
        reason_label=TASK_REASON_LABELS[reason],
        reason_detail=TASK_REASON_DETAILS[reason],
        occurred_at=occurred,
        evidence_ref=customer.evidence_ref
        or (f"import_batch:{customer.batch_id}" if customer.batch_id else f"customer:{customer.id}"),
    )


# ══════════════════════════════════════════════════════════════════════
# 各区块
# ══════════════════════════════════════════════════════════════════════
def _kpis(db: Session, week_start: datetime, buckets: dict[TaskReason, list[Customer]]) -> KpiBlock:
    total = db.scalar(select(func.count(Customer.id))) or 0
    new_week = (
        db.scalar(select(func.count(Customer.id)).where(Customer.first_seen_at >= week_start)) or 0
    )
    risk = len(buckets[TaskReason.HUMAN_REQUIRED])

    # ★ 商机 / 成交口径（D24 → D32）：
    #   商机表**有记录**时以它为准（进行中 = 待推进的机会；赢单 = WON）；
    #   商机表为空时回退用客户生命周期近似（并在 opportunity_note 里说明）。
    #   之前固定回退，导致首页显示"商机 0"而商机看板显示"3" —— 同一个产品两个数字矛盾。
    op_total = db.scalar(
        select(func.count(Opportunity.id)).where(Opportunity.is_active.is_(True))
    ) or 0

    if op_total > 0:
        opportunity = db.scalar(
            select(func.count(Opportunity.id)).where(
                Opportunity.is_active.is_(True),
                Opportunity.stage.notin_(list(CLOSED_STAGES)),
            )
        ) or 0
        won = db.scalar(
            select(func.count(Opportunity.id)).where(
                Opportunity.is_active.is_(True), Opportunity.stage == WON_STAGE
            )
        ) or 0
        opp_source = "OPPORTUNITY_TABLE"
        opp_note = (
            f"读商机表：商机数量 = 进行中的商机（不含赢单/丢单），成交订单 = 赢单商机数；"
            f"商机表共 {op_total} 条记录。"
        )
        opp_ref = "opportunities:count|stage not in (WON, CLOSED_LOST)"
        won_ref = "opportunities:count|stage=WON"
    else:
        reach = _lifecycle_reach(db)
        opportunity = sum(
            reach.get(s, 0) for s in (LifecycleStatus.QUOTED, LifecycleStatus.WON)
        )
        won = reach.get(LifecycleStatus.WON, 0)
        opp_source = "LIFECYCLE_FALLBACK"
        opp_note = (
            "商机表还没有记录，暂用客户生命周期近似："
            "商机数量 = 推进到「已报价」及之后的客户；成交订单 = 「已成交」的客户。"
            "录入商机后本卡自动改读商机表。"
        )
        opp_ref = "customers:count|lifecycle_status in (QUOTED, WON)"
        won_ref = "customers:count|lifecycle_status=WON"

    return KpiBlock(
        customer_total=build_evidence(
            total, source_type=SourceType.SYSTEM, evidence_ref="customers:count"
        ),
        customer_new_this_week=build_evidence(
            new_week, source_type=SourceType.SYSTEM, evidence_ref="customers:count|first_seen_at>=week_start"
        ),
        opportunity_count=build_evidence(
            opportunity, source_type=SourceType.SYSTEM, evidence_ref=opp_ref
        ),
        won_orders=build_evidence(won, source_type=SourceType.SYSTEM, evidence_ref=won_ref),
        risk_customers=build_evidence(
            risk,
            source_type=SourceType.SYSTEM,
            evidence_ref="customers:count|handover_state=HUMAN_REQUIRED",
        ),
        opportunity_source=opp_source,
        opportunity_note=opp_note,
    )


def _lifecycle_reach(db: Session) -> dict[LifecycleStatus, int]:
    """各生命周期状态的**当前**客户数。

    累计口径由调用方按需聚合（quoted 及之后 = 商机）。
    为什么不在这里直接做累计：不同指标要的累计区间不同，
    把"当前分布"和"怎么累计"分开，调用点各自表达意图更清楚。
    """
    rows = db.execute(
        select(Customer.lifecycle_status, func.count(Customer.id)).group_by(
            Customer.lifecycle_status
        )
    ).all()
    return {st: int(n) for st, n in rows}


def _pending(buckets: dict[TaskReason, list[Customer]]) -> PendingBlock:
    total = sum(len(v) for v in buckets.values())

    def ev(n: int, ref: str):
        return build_evidence(n, source_type=SourceType.SYSTEM, evidence_ref=ref)

    return PendingBlock(
        total=ev(total, "dashboard:pending:total"),
        human_required=ev(len(buckets[TaskReason.HUMAN_REQUIRED]), "dashboard:pending:human_required"),
        human_active=ev(len(buckets[TaskReason.HUMAN_ACTIVE]), "dashboard:pending:human_active"),
        ai_failed=ev(len(buckets[TaskReason.AI_FAILED]), "dashboard:pending:ai_failed"),
    )


def _tasks(buckets: dict[TaskReason, list[Customer]]) -> TaskSection:
    total = sum(len(v) for v in buckets.values())
    if total == 0:
        total_ev = no_data_evidence(
            "当前没有需要人工处理的客户（无待接管、无 AI 失败、无待裁决、无本周新增未联系）",
            source_type=SourceType.SYSTEM,
            evidence_ref="dashboard:tasks",
        )
    else:
        total_ev = build_evidence(total, source_type=SourceType.SYSTEM, evidence_ref="dashboard:tasks")

    counts = [
        TaskCount(reason=reason, label=TASK_REASON_LABELS[reason], count=len(buckets[reason]))
        for reason in TaskReason
    ]

    items: list[TaskItem] = []
    for reason in TaskReason:  # 枚举顺序 = 展示优先级（人工相关在前）
        for customer in buckets[reason][:TASK_PREVIEW_LIMIT]:
            items.append(_task_item(customer, reason))
        if len(items) >= TASK_PREVIEW_LIMIT:
            break
    return TaskSection(total=total_ev, counts=counts, items=items[:TASK_PREVIEW_LIMIT])


def _region_distribution(db: Session) -> RegionDistribution:
    """客户分布：按 customers.region 分组。

    ★ region 为空归入「未填写」并如实显示；全库为空时 slices 为空数组（前端画空态）。
    """
    rows = db.execute(
        select(Customer.region, func.count(Customer.id)).group_by(Customer.region)
    ).all()
    total = sum(int(n) for _, n in rows)
    slices = [
        RegionSlice(region=(region or REGION_UNKNOWN), count=int(n)) for region, n in rows if n
    ]
    slices.sort(key=lambda s: s.count, reverse=True)

    return RegionDistribution(
        total=build_evidence(
            total, source_type=SourceType.SYSTEM, evidence_ref="customers:region:distribution"
        )
        if total
        else no_data_evidence(
            "还没有客户数据",
            source_type=SourceType.SYSTEM,
            evidence_ref="customers:region:distribution",
        ),
        slices=slices,
    )


def _growth_trend(db: Session, now: datetime, days: int = TREND_DAYS) -> GrowthTrend:
    """客户增长趋势：按 first_seen_at 的日期聚合最近 N 天（含今天）。"""
    today = now.astimezone(REPORT_TZ).date()
    start_day = today - timedelta(days=days - 1)

    rows = db.execute(
        select(func.date(Customer.first_seen_at), func.count(Customer.id))
        .where(Customer.first_seen_at >= datetime.combine(start_day, datetime.min.time(), tzinfo=timezone.utc))
        .group_by(func.date(Customer.first_seen_at))
    ).all()
    by_day: Counter[str] = Counter()
    for day, n in rows:
        key = day.isoformat() if isinstance(day, date) else str(day)[:10]
        by_day[key] += int(n)

    points: list[TrendPoint] = []
    for i in range(days):
        d = start_day + timedelta(days=i)
        points.append(TrendPoint(day=d, count=by_day.get(d.isoformat(), 0)))

    total = sum(p.count for p in points)
    return GrowthTrend(
        days=days,
        total=build_evidence(
            total, source_type=SourceType.SYSTEM, evidence_ref=f"customers:trend:{days}d"
        ),
        points=points,
    )


def _source_breakdown(db: Session) -> SourceBreakdown:
    """客户来源：按 customers.source_type 分组（REAL / TEST / MANUAL）。"""
    labels = {
        CustomerSourceType.REAL: "真实业务数据",
        CustomerSourceType.TEST: "测试数据",
        CustomerSourceType.MANUAL: "人工录入",
    }
    rows = db.execute(
        select(Customer.source_type, func.count(Customer.id)).group_by(Customer.source_type)
    ).all()
    total = sum(int(n) for _, n in rows)
    slices = [
        SourceSlice(source=st.value, label=labels.get(st, st.value), count=int(n))
        for st, n in rows
        if n
    ]
    slices.sort(key=lambda s: s.count, reverse=True)
    return SourceBreakdown(
        total=build_evidence(
            total, source_type=SourceType.SYSTEM, evidence_ref="customers:source:breakdown"
        )
        if total
        else no_data_evidence(
            "还没有客户数据",
            source_type=SourceType.SYSTEM,
            evidence_ref="customers:source:breakdown",
        ),
        slices=slices,
    )


#: 事件类型 -> 参考图上的动态标题
EVENT_TITLES = {
    "STATUS_CHANGED": "状态变更",
    "MESSAGE_SENT": "沟通记录",
    "HANDOVER": "人工接管",
    "NOTE": "备注",
    "VISIT": "客户访问",  # TASK-010
}


def _recent_activities(db: Session) -> RecentActivities:
    """最近客户动态。★ **只来自 customer_events 真实事件表**，不编造访问/表单事件。"""
    total = db.scalar(select(func.count(CustomerEvent.id))) or 0
    rows = db.execute(
        select(CustomerEvent, Customer.name)
        .join(Customer, Customer.id == CustomerEvent.customer_id)
        .order_by(CustomerEvent.created_at.desc(), CustomerEvent.id.desc())
        .limit(ACTIVITY_LIMIT)
    ).all()

    items: list[ActivityItem] = []
    for event, customer_name in rows:
        kind = event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)
        meta = event.metadata_json or {}
        detail = meta.get("note") or meta.get("summary")
        if event.from_status is not None and event.to_status is not None:
            detail = f"{event.from_status.value} → {event.to_status.value}"
        elif kind == "VISIT":
            # TASK-010：访问动态要说清"访问了哪个页面、从哪来"
            page = meta.get("page") or "—"
            channel = str(meta.get("channel") or "").upper()
            detail = f"访问了 {page}"
            if channel:
                detail += f"（来源 {VISIT_CHANNELS.get(channel, channel)}）"
        items.append(
            ActivityItem(
                occurred_at=event.created_at,
                customer_id=event.customer_id,
                customer_name=customer_name,
                kind=kind,
                title=EVENT_TITLES.get(kind, kind),
                detail=detail,
                actor=event.actor_type.value
                if hasattr(event.actor_type, "value")
                else str(event.actor_type),
                evidence_ref=event.evidence_ref or f"customer_event:{event.id}",
            )
        )

    return RecentActivities(
        total=build_evidence(
            total, source_type=SourceType.SYSTEM, evidence_ref="customer_events:count"
        )
        if total
        else no_data_evidence(
            "还没有客户事件（状态变更 / 沟通 / 接管都会记录在这里）",
            source_type=SourceType.SYSTEM,
            evidence_ref="customer_events:count",
        ),
        items=items,
    )


def _connections(db: Session) -> DataConnectionsBlock:
    """数据连接状态。

    ★ TASK-024：改为**读真实的 data_connections 表**。
      此前 CRM/官网/MySQL/API 的 `connected=False` 与 note="未连接" 是**硬编码**，
      正是需求 §二十 禁止的那种"假连接页"——不管用户有没有配置，显示永远一样。
      现在：连接列表来自真实表，状态由真实测试/同步结果驱动。
    ★ 表里一个连接都没有时，退回展示"可接入但未配置"的清单（如实说未配置）。
    """
    last_import = db.scalar(
        select(func.max(ImportBatch.imported_at)).where(
            ImportBatch.status == ImportBatchStatus.SUCCESS
        )
    )
    batch_count = db.scalar(select(func.count(ImportBatch.id))) or 0

    rows = list(
        db.scalars(
            select(DataConnectionRow)
            .where(DataConnectionRow.is_active.is_(True))
            .order_by(DataConnectionRow.id.asc())
        ).all()
    )

    if rows:
        items = [
            DataConnection(
                key=f"conn_{r.id}",
                # ★ 显示可读的类型名（Excel / CSV、MySQL…），不显示 EXCEL_CSV 这种枚举名 ——
                #   中文界面里混英文枚举，业务人员看不懂（§七 UI 原则）。
                label=f"{r.name}（{CONNECTOR_KIND_LABELS.get(r.kind, r.kind.value)}）",
                # ★ 只有真实测试/同步通过才算 connected
                connected=r.status == ConnectionStatus.OK,
                last_sync_at=r.last_sync_at,
                note=r.last_error
                or (f"状态：{r.status.value}" + ("｜已配置凭据" if r.secret_encrypted else "｜未配置凭据")),
            )
            for r in rows
        ]
    else:
        # 没有配置任何连接：如实列出"可接入但未配置"，不假装已连接
        items = [
            DataConnection(
                key="file_import",
                label="Excel 文件",
                connected=batch_count > 0,
                last_sync_at=last_import,
                note=f"已有 {batch_count} 个导入批次" if batch_count else "还没有导入过文件",
            ),
            DataConnection(
                key="unconfigured",
                label="CRM / 官网 / MySQL / API",
                connected=False,
                note="尚未配置任何数据连接（到「数据连接」页新建并测试）",
            ),
        ]

    connected = sum(1 for i in items if i.connected)
    return DataConnectionsBlock(
        items=items,
        connected_count=build_evidence(
            connected, source_type=SourceType.SYSTEM, evidence_ref="dashboard:data_connections"
        ),
    )


def _metric_label(key: str) -> str:
    """取指标的展示名。

    ★ 从 `services.reports.METRICS` **读取**，不再自己维护一份标签表。
      之前这里有一份只写了 2 条（customer_total / customer_new）的
      `REPORT_METRIC_LABELS`，其余 13 个指标 fallback 成原始 key ——
      首页「今日汇报」因此显示出 `risk_open` / `message_read` / `project_active`
      这种英文键名（中文界面里混英文键，用户看不懂）。
    ★ 教训同 D46：**同一个东西有两份名字表，迟早对不上**。
      这里改为单一事实来源，报告页与首页永远一致。
      查不到的 key 才回退成 key 本身（不假装认识它）。
    """
    metric = METRICS.get(key)
    return metric.label if metric is not None else key


def _today_report(db: Session) -> ReportDigest:
    """右栏「今日汇报」。★ 数字来自 reports 快照的 content_json，原样透传，不重算、不编造。"""
    report = db.scalars(
        select(Report).order_by(Report.generated_at.desc(), Report.id.desc()).limit(1)
    ).first()
    if report is None:
        return ReportDigest(
            available=False,
            note="还没有生成过汇报；在汇报页生成后，这里会显示最新一份的摘要",
        )

    content = report.content_json if isinstance(report.content_json, dict) else {}
    raw_metrics = content.get("metrics") or {}

    metrics: list[ReportMetric] = []
    for key, value in raw_metrics.items():
        if not isinstance(value, dict):
            continue
        metrics.append(
            ReportMetric(
                key=key,
                label=_metric_label(key),
                evidence=value,  # 已经是 EvidenceValue 形态，原样透传
            )
        )

    highlights: list[str] = []
    raw_highlights = content.get("highlights")
    if isinstance(raw_highlights, list):
        highlights = [str(h) for h in raw_highlights][:5]

    excluded = report.excluded_test_count or 0
    note = f"数据快照：{report.period_start} ~ {report.period_end}"
    if excluded:
        note += f"（已排除 {excluded} 条 TEST 数据，不计入业务数字）"

    return ReportDigest(
        available=True,
        report_id=report.id,
        report_type=report.report_type.value
        if hasattr(report.report_type, "value")
        else str(report.report_type),
        generated_at=report.generated_at,
        period_start=report.period_start,
        period_end=report.period_end,
        excluded_test_count=excluded,
        metrics=metrics,
        highlights=highlights,
        note=note,
    )


def _ai_assistant() -> AiAssistantBlock:
    """右栏 AI 助手。

    ★ capabilities 与问答能力说明**共用 services/capabilities.py 一份来源**：
      原先两处手写，风险/访问上线后问答更新了、面板没更新（CDP 断言直接抓到）。
    ★ input_placeholder 也已修正：AI 问答**是可用的**，
      之前写「V1 暂未接入对话」是不实说明。
    """
    return AiAssistantBlock(
        title="AI 助手",
        badge="企业版",
        greeting="你好！我是你的 AI 商业项目助理，我可以帮你：",
        capabilities=list(CAPABILITIES),
        boundaries=list(UNAVAILABLE),
        blocked_note=BLOCKED,
        input_placeholder="有什么问题，尽管问我…（点右箭头或回车发送）",
    )


# ══════════════════════════════════════════════════════════════════════
# 端点
# ══════════════════════════════════════════════════════════════════════
@router.get(
    "/summary",
    response_model=DashboardSummaryResponse,
    summary="工作台首页聚合（三栏布局所需的全部真实数据）",
)
def dashboard_summary(db: Session = Depends(get_db)) -> DashboardSummaryResponse:
    """首页聚合。**一次请求拿齐首页所有区块**，避免前端并发打 8 个接口。"""
    now = datetime.now(timezone.utc)
    week_start = _week_start(now)
    buckets = _categorize(db, week_start)

    return DashboardSummaryResponse(
        generated_at=now,
        greeting=GreetingBlock(
            salutation=_salutation(now),
            subtitle="这是你今天的商业项目助理工作台。",
            timezone=str(REPORT_TZ),
        ),
        pending=_pending(buckets),
        kpis=_kpis(db, week_start, buckets),
        tasks=_tasks(buckets),
        region_distribution=_region_distribution(db),
        growth_trend=_growth_trend(db, now),
        source_breakdown=_source_breakdown(db),
        recent_activities=_recent_activities(db),
        data_connections=_connections(db),
        today_report=_today_report(db),
        ai_assistant=_ai_assistant(),
    )
