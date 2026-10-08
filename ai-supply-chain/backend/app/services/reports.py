"""汇报服务：指标口径、快照生成、下钻取数（TASK-007 建立，TASK-023 扩展）。

三条铁律，本文件是唯一执行点：

1. **口径唯一**：每个指标的 where 条件只在 `METRICS` 定义一次；生成报告与下钻
   都走同一份定义 —— 两边若各写一份，迟早漂移（下钻条数 != 报告数字）。
2. **快照语义**（D6）：报告数字只在生成那一刻算一次，写进 content_json；
   读取接口永不重算，客户被删/改也不影响已生成的报告。
3. **NO_DATA 不是 0**：该口径下没有任何真实数据时返回 NO_DATA，绝不用 0 冒充。

═══════════════════════════════════════════════════════════════════════
【TASK-023：为什么从 2 个指标扩到 15 个】
═══════════════════════════════════════════════════════════════════════
需求文档 §十八「老板汇报助手」写明这是**核心功能之一**，要求支持 11 类汇报内容：
    总体进展 / 新增客户 / 有效线索 / 重点客户 / 客户沟通 / 商机变化 / 成交情况 /
    风险 / 异常 / 当前问题 / 下一步计划
原先只有 customer_total / customer_new 两个指标，名不副实。

★ 扩展的前提是**数据源已经就绪**：商机、项目、风险、营销内容四张表都已落地
  （TASK-016~018），所以这些数字都是真实可查的，不是凑数。
★ 仍然**不编**：没有数据源的口径（如"有效线索"需要线索定义、"下一步计划"需要人工填写）
  一律不做，宁可少一个指标也不编一个数字。

═══════════════════════════════════════════════════════════════════════
【下钻要跨实体（§十九 要求"所有数字必须可追溯"）】
═══════════════════════════════════════════════════════════════════════
    客户类指标 → 下钻出客户行；商机类 → 商机行；风险类 → 风险行……
    所以下钻返回**统一的 DrilldownRow**（id/title/subtitle/occurred_at/ref），
    而不是死板地只返回 Customer —— 否则商机指标点开是空的，追溯就断了。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from app.models.content import MarketingContent
from app.models.customer import Customer, CustomerSourceType
from app.models.customer_message import CustomerMessage
from app.models.opportunity import CLOSED_STAGES, WON_STAGE, Opportunity
from app.models.project import ACTIVE_STATUSES, CLOSED_STATUSES, Project
from app.models.report import REPORT_TIMEZONE, Report, ReportSourceType, ReportType
from app.models.risk_event import RiskEvent, RiskStatus
from app.schemas.evidence import EvidenceValue, SourceType, ValueState

#: 下钻行的实体类型（前端据此决定跳哪个详情页）
#: ★ 注意 message_* 指标的实体是 CustomerMessage，不是 Customer ——
#:   row_kind 必须与 metric_query() 实际返回的模型一致，否则 _row_for 会取错属性。
RowKind = Literal["customer", "message", "opportunity", "project", "risk", "content"]


class UnknownMetricError(ValueError):
    """下钻/取数遇到了白名单之外的指标 key（路由层翻译成 400）。"""


@dataclass(frozen=True)
class MetricDef:
    """指标定义。

    key            —— 白名单键
    label          —— 展示名
    period_bounded —— 口径是否按期间过滤（"当前总数"类为 False）
    row_kind       —— 下钻出来的实体类型
    group          —— 归到 §十八 的哪一类汇报内容（前端分组展示）
    """

    key: str
    label: str
    period_bounded: bool
    row_kind: RowKind
    group: str


@dataclass
class DrilldownRow:
    """下钻的一行（跨实体统一形态）。"""

    id: int
    kind: RowKind
    title: str
    subtitle: str | None = None
    occurred_at: datetime | None = None
    evidence_ref: str | None = None
    extra: dict = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════════════
# 指标白名单（§十八 的 11 类汇报内容，按数据源分组）
# ══════════════════════════════════════════════════════════════════════
METRICS: dict[str, MetricDef] = {
    # ── 客户（总体进展 / 新增客户）──
    "customer_total": MetricDef(
        "customer_total", "客户总数（REAL）", False, "customer", "客户"
    ),
    "customer_new": MetricDef(
        "customer_new", "期间新增客户（REAL）", True, "customer", "客户"
    ),
    "customer_with_channel": MetricDef(
        "customer_with_channel", "已填获客渠道的客户", False, "customer", "客户"
    ),
    # ── 客户沟通 ──
    "message_sent": MetricDef(
        "message_sent", "期间沟通记录条数", True, "message", "客户沟通"
    ),
    "message_read": MetricDef(
        "message_read", "期间收到已读回执的消息", True, "message", "客户沟通"
    ),
    # ── 商机变化 / 成交情况 ──
    "opportunity_open": MetricDef(
        "opportunity_open", "进行中的商机", False, "opportunity", "商机与成交"
    ),
    "opportunity_new": MetricDef(
        "opportunity_new", "期间新建商机", True, "opportunity", "商机与成交"
    ),
    "opportunity_won": MetricDef(
        "opportunity_won", "期间赢单商机", True, "opportunity", "商机与成交"
    ),
    "opportunity_lost": MetricDef(
        "opportunity_lost", "期间丢单商机", True, "opportunity", "商机与成交"
    ),
    "opportunity_created_total": MetricDef(
        "opportunity_created_total", "已录入商机总数", False, "opportunity", "商机与成交"
    ),
    # ── 项目进展 ──
    "project_active": MetricDef(
        "project_active", "进行中的项目", False, "project", "项目进展"
    ),
    "project_overdue": MetricDef(
        "project_overdue", "已逾期的项目", False, "project", "项目进展"
    ),
    # ── 风险 / 异常 ──
    "risk_open": MetricDef("risk_open", "待处理风险", False, "risk", "风险与异常"),
    "risk_high_open": MetricDef(
        "risk_high_open", "待处理高风险", False, "risk", "风险与异常"
    ),
    # ── 营销效果 ──
    "content_published": MetricDef(
        "content_published", "已发布内容", False, "content", "营销效果"
    ),
}

METRIC_KEYS: tuple[str, ...] = tuple(METRICS)

#: 按 §十八 归类，前端按组展示（不返回 NULL 组）
METRIC_GROUPS: tuple[str, ...] = (
    "客户",
    "客户沟通",
    "商机与成交",
    "项目进展",
    "风险与异常",
    "营销效果",
)


def _period_bounds(period_start: date, period_end: date) -> tuple[datetime, datetime]:
    """期间 -> [start, end) 半开区间（Asia/Shanghai 本地时间，含 period_end 当天）。"""
    start = datetime.combine(period_start, time.min)
    end_exclusive = datetime.combine(period_end + timedelta(days=1), time.min)
    return start, end_exclusive


# ══════════════════════════════════════════════════════════════════════
# 每个指标的「口径」与「取数」——两处定义，但都只在这一个文件里
# ══════════════════════════════════════════════════════════════════════

def _real_customers() -> ColumnElement[bool]:
    """业务数字只认 REAL 来源客户（TEST/DEMO 不进业务汇报）。"""
    return Customer.source_type == CustomerSourceType.REAL


def metric_query(metric_key: str, period_start: date, period_end: date):
    """★ 统计口径的唯一来源：返回 (模型, 条件列表)。

    生成报告与下钻都调它 —— 保证"下钻条数 == 报告里的数字"。
    """
    if metric_key not in METRICS:
        raise UnknownMetricError(f"未知指标：{metric_key}")
    d = METRICS[metric_key]
    start, end_ex = _period_bounds(period_start, period_end)

    if metric_key == "customer_total":
        return Customer, [_real_customers()]
    if metric_key == "customer_new":
        return Customer, [
            _real_customers(),
            Customer.first_seen_at >= start,
            Customer.first_seen_at < end_ex,
        ]
    if metric_key == "customer_with_channel":
        return Customer, [_real_customers(), Customer.acquisition_channel.isnot(None)]
    if metric_key == "message_sent":
        return CustomerMessage, [CustomerMessage.created_at >= start, CustomerMessage.created_at < end_ex]
    if metric_key == "message_read":
        return CustomerMessage, [
            CustomerMessage.read_at.isnot(None),
            CustomerMessage.read_at >= start,
            CustomerMessage.read_at < end_ex,
        ]
    if metric_key == "opportunity_open":
        return Opportunity, [
            Opportunity.is_active.is_(True),
            Opportunity.stage.notin_(list(CLOSED_STAGES)),
        ]
    if metric_key == "opportunity_new":
        return Opportunity, [
            Opportunity.is_active.is_(True),
            Opportunity.created_at >= start,
            Opportunity.created_at < end_ex,
        ]
    if metric_key == "opportunity_won":
        return Opportunity, [
            Opportunity.is_active.is_(True),
            Opportunity.stage == WON_STAGE,
            Opportunity.closed_at.isnot(None),
            Opportunity.closed_at >= start,
            Opportunity.closed_at < end_ex,
        ]
    if metric_key == "opportunity_lost":
        return Opportunity, [
            Opportunity.is_active.is_(True),
            Opportunity.stage.notin_([WON_STAGE]),
            Opportunity.stage.in_(list(CLOSED_STAGES)),
            Opportunity.closed_at.isnot(None),
            Opportunity.closed_at >= start,
            Opportunity.closed_at < end_ex,
        ]
    if metric_key == "opportunity_created_total":
        return Opportunity, [Opportunity.is_active.is_(True)]
    if metric_key == "project_active":
        return Project, [
            Project.is_active.is_(True),
            Project.status.in_(list(ACTIVE_STATUSES)),
        ]
    if metric_key == "project_overdue":
        # 逾期是日期比较（不是延期预测）：有 due_date、未结束、且已过计划完成日
        # ★ REPORT_TIMEZONE 是**字符串**常量（写进快照自解释用），
        #   不能直接传给 datetime.now()——它要 tzinfo 对象（实测踩过 TypeError）。
        today = datetime.now(ZoneInfo(REPORT_TIMEZONE)).date()
        return Project, [
            Project.is_active.is_(True),
            Project.status.notin_(list(CLOSED_STATUSES)),
            Project.due_date.isnot(None),
            Project.due_date < today,
        ]
    if metric_key == "risk_open":
        return RiskEvent, [RiskEvent.status == RiskStatus.OPEN]
    if metric_key == "risk_high_open":
        return RiskEvent, [
            RiskEvent.status == RiskStatus.OPEN,
            RiskEvent.level == "HIGH",
        ]
    if metric_key == "content_published":
        return MarketingContent, [
            MarketingContent.is_active.is_(True),
            MarketingContent.published_on.isnot(None),
        ]
    raise UnknownMetricError(f"未知指标：{metric_key}")  # pragma: no cover


def evidence_ref_for(metric_key: str, period_start: date, period_end: date) -> str:
    """指标的证据锚点：可反查、可复现。"""
    if metric_key not in METRICS:
        raise UnknownMetricError(f"未知指标：{metric_key}")
    ref = f"report:metric={metric_key}"
    if METRICS[metric_key].period_bounded:
        ref += f"|period={period_start.isoformat()}..{period_end.isoformat()}"
    return ref[:128]


def count_metric(db: Session, metric_key: str, period_start: date, period_end: date) -> int:
    """按统一口径数一行。★ 下钻的 total 也调它，保证两边同一个数。"""
    model, conditions = metric_query(metric_key, period_start, period_end)
    return int(db.execute(select(func.count()).select_from(model).where(*conditions)).scalar_one())


#: 空态原因文案：说清"为什么没有数据"，而不是干巴巴一个 NO_DATA
EMPTY_REASONS: dict[str, str] = {
    "customer_total": "库里没有 REAL 来源客户（只有测试/演示数据时不计入业务数字）",
    "customer_new": "期间内没有新增的 REAL 来源客户",
    "customer_with_channel": "还没有客户填写获客渠道",
    "message_sent": "期间内没有沟通记录",
    "message_read": "期间内没有收到客户已读回执（需真实渠道回传）",
    "opportunity_open": "没有进行中的商机",
    "opportunity_new": "期间内没有新建商机",
    "opportunity_won": "期间内没有赢单",
    "opportunity_lost": "期间内没有丢单",
    "opportunity_created_total": "还没有录入任何商机",
    "project_active": "没有进行中的项目",
    "project_overdue": "没有逾期项目",
    "risk_open": "没有待处理风险",
    "risk_high_open": "没有待处理的高风险",
    "content_published": "还没有已发布的内容",
}


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
            source_type=SourceType.SYSTEM,
            evidence_ref=ref,
            reason=EMPTY_REASONS.get(metric_key, "该口径下没有数据"),
        )
    return EvidenceValue(
        value=count,
        state=ValueState.VALID,
        source_type=SourceType.SYSTEM,
        evidence_ref=ref,
    )


def count_excluded_test(db: Session, period_start: date, period_end: date) -> int:
    """本期被排除在业务数字之外的 TEST 客户条数。"""
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


#: 叙述里"值得单独点出"的指标及其措辞（TASK-039）。
#: ★ 只给**确实需要关注**的指标写句子（逾期、风险、赢单…），
#:   不给每个指标都凑一句 —— 那会变成流水账，反而不直观。
NARRATIVE_RULES: list[tuple[str, str, str]] = [
    # (metric_key, 情态, 模板)
    ("project_overdue", "warning", "已逾期项目 {v} 个，需要尽快确认交付计划。"),
    ("risk_high_open", "warning", "存在 {v} 条待处理的高风险，建议优先处理。"),
    ("risk_open", "attention", "待处理风险共 {v} 条。"),
    ("opportunity_won", "good", "本期赢单 {v} 个。"),
    ("opportunity_lost", "attention", "本期丢单 {v} 个，建议复盘原因。"),
    ("opportunity_new", "info", "本期新建商机 {v} 个。"),
    ("customer_new", "info", "本期新增真实客户 {v} 个。"),
]


def build_narrative(metrics: dict[str, dict]) -> list[dict]:
    """根据**真实指标值**生成文字描述（TASK-039）。

    ★★ 关键取舍：**这段文字由代码按规则拼，不调用 AI**。
      理由：报告是给老板看的，数字必须与指标卡完全一致。
      让模型"根据数字写一段话"存在改写/加戏的风险
      （把 NO_DATA 说成 0、把 3 个说成"约 3 个"），
      而这类错误在汇报场景里代价很高。
      所以用模板拼装 —— 数字原样嵌入，**不可能与指标卡不一致**。

    ★ 每条都带 `state`：NO_DATA 的指标**不出句子**（不写"新增客户 0 个"），
      而是在"说明"里单独列出"本期无真实数据"。
    """
    lines: list[dict] = []
    for key, tone, template in NARRATIVE_RULES:
        m = metrics.get(key)
        if not isinstance(m, dict):
            continue
        # ★ 只有 VALID 才出句子；NO_DATA / ERROR 一律不写死数字
        if m.get("state") != "VALID" or m.get("value") is None:
            continue
        value = m["value"]
        # 0 值只在"坏消息类"指标上值得点出（如逾期 0 个是好消息，不用写）
        if value == 0 and tone == "info":
            continue
        lines.append(
            {
                "tone": tone,
                "text": template.format(v=value),
                "metric_key": key,
                "evidence_ref": m.get("evidence_ref"),
            }
        )
    return lines


def build_no_data_note(metrics: dict[str, dict]) -> list[str]:
    """列出**本期取不到数据**的指标名。

    ★ 这一段是"文本描述"里最容易被忽略、但最重要的一半：
      只写"做了什么"而不写"哪些没数据"，读者会把缺失当成 0（§二 禁止）。
    """
    gaps: list[str] = []
    for key, m in metrics.items():
        if not isinstance(m, dict):
            continue
        if m.get("state") == "VALID":
            continue
        label = METRICS[key].label if key in METRICS else key
        gaps.append(f"{label}（{m.get('reason') or '本期无数据'}）")
    return gaps


def build_trend(
    db: Session, *, period_start: date, period_end: date, max_days: int = 31
) -> list[dict]:
    """按天汇总的走势序列，供报告里的折线/柱状图使用（TASK-039）。

    ★ 只统计 `source_type=REAL` 的客户与消息，与指标卡同口径（否则图上和卡上对不上）。
    ★ 天数超过 max_days 时**按周聚合**，避免 90 天画成 90 个点看不清。
    """
    from app.models.customer import Customer as _Customer
    from app.models.customer_message import CustomerMessage as _Msg

    span = (period_end - period_start).days + 1
    if span <= 0:
        return []

    # 客户按 first_seen_at、消息按 created_at 落在期间内
    cust_rows = db.execute(
        select(func.date(_Customer.first_seen_at), func.count(_Customer.id))
        .where(
            _Customer.source_type == CustomerSourceType.REAL,
            func.date(_Customer.first_seen_at).between(period_start, period_end),
        )
        .group_by(func.date(_Customer.first_seen_at))
    ).all()
    msg_rows = db.execute(
        select(func.date(_Msg.created_at), func.count(_Msg.id))
        .where(func.date(_Msg.created_at).between(period_start, period_end))
        .group_by(func.date(_Msg.created_at))
    ).all()

    cust_map = {str(d): int(n) for d, n in cust_rows}
    msg_map = {str(d): int(n) for d, n in msg_rows}

    points: list[dict] = []
    day = period_start
    while day <= period_end:
        key = day.isoformat()
        points.append(
            {"day": key, "customers": cust_map.get(key, 0), "messages": msg_map.get(key, 0)}
        )
        day += timedelta(days=1)

    # ★ 超过 31 天按周聚合：图表上"一天一个点"在长周期下没有可读性
    if len(points) > max_days:
        weekly: list[dict] = []
        for i in range(0, len(points), 7):
            chunk = points[i : i + 7]
            weekly.append(
                {
                    "day": chunk[0]["day"],
                    "customers": sum(p["customers"] for p in chunk),
                    "messages": sum(p["messages"] for p in chunk),
                    "aggregated_days": len(chunk),
                }
            )
        return weekly
    return points


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
        metrics[key] = evidence.model_dump(mode="json", exclude_none=True)

    report = Report(
        report_type=report_type,
        period_start=period_start,
        period_end=period_end,
        timezone=REPORT_TIMEZONE,
        # ★ content_json 里除了 metrics，还冻结三样东西（TASK-039）：
        #   narrative  —— 文字描述（代码按真实数字拼，不调 AI）
        #   no_data    —— 本期取不到的指标（避免把缺失当 0）
        #   trend      —— 按天/按周的走势序列（供图表）
        #   全部在生成时冻结，与指标卡同一份来源，事后不会不一致（D6）。
        content_json={
            "metrics": metrics,
            "narrative": build_narrative(metrics),
            "no_data": build_no_data_note(metrics),
            "trend": build_trend(db, period_start=period_start, period_end=period_end),
        },
        source_type=ReportSourceType.SYSTEM,
        generated_by="system",
        excluded_test_count=count_excluded_test(db, period_start, period_end),
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def _row_for(db: Session, metric_key: str, obj) -> DrilldownRow:
    """把一个实体对象转成统一的下钻行。"""
    kind = METRICS[metric_key].row_kind

    if kind == "customer":
        return DrilldownRow(
            id=obj.id,
            kind="customer",
            title=obj.name,
            subtitle=obj.company_name or obj.region,
            occurred_at=obj.first_seen_at,
            evidence_ref=f"customer:{obj.id}",
        )
    if kind == "message":
        # ★ CustomerMessage 没有 name：用正文摘要作标题，并带上所属客户 id 便于跳转
        summary = (obj.content or "").strip()
        return DrilldownRow(
            id=obj.id,
            kind="message",
            title=summary[:60] + ("…" if len(summary) > 60 else ""),
            subtitle=f"{obj.sender_type.value}｜已读 {'是' if obj.read_at else '否'}"
            + (f"（{obj.read_source}）" if obj.read_source else ""),
            occurred_at=obj.created_at,
            evidence_ref=obj.evidence_ref or f"customer_message:{obj.id}",
            extra={"customer_id": obj.customer_id},
        )
    if kind == "opportunity":
        return DrilldownRow(
            id=obj.id,
            kind="opportunity",
            title=obj.title,
            subtitle=f"{obj.stage.value}｜{obj.currency} {obj.amount if obj.amount is not None else '未估金额'}",
            occurred_at=obj.created_at,
            evidence_ref=obj.evidence_ref or f"opportunity:{obj.id}",
            extra={"customer_id": obj.customer_id},
        )
    if kind == "project":
        return DrilldownRow(
            id=obj.id,
            kind="project",
            title=obj.name,
            subtitle=f"{obj.status.value}｜完成日 {obj.due_date or '未定'}",
            occurred_at=obj.created_at,
            evidence_ref=obj.evidence_ref or f"project:{obj.id}",
        )
    if kind == "risk":
        return DrilldownRow(
            id=obj.id,
            kind="risk",
            title=obj.reason or obj.event_type.value,
            subtitle=f"{obj.level.value}｜{obj.status.value}",
            occurred_at=obj.created_at,
            evidence_ref=obj.evidence_ref,
            extra={"customer_id": obj.customer_id},
        )
    # content
    return DrilldownRow(
        id=obj.id,
        kind="content",
        title=obj.title,
        subtitle=f"{obj.content_type.value}｜{obj.channel.value}",
        occurred_at=obj.created_at,
        evidence_ref=obj.evidence_ref or f"content:{obj.id}",
    )


def drilldown_metric(
    db: Session,
    *,
    metric_key: str,
    period_start: date,
    period_end: date,
    page: int,
    page_size: int,
) -> tuple[list[DrilldownRow], int]:
    """下钻：返回构成该指标的真实明细行（分页）+ 总条数。

    ★ 条件来自 metric_query()，与生成报告时完全一致 ——
      因此"下钻条数 == 报告里的数字"是可断言的（§十九）。
    ★ 返回**统一形态**的行，跨实体（客户/商机/项目/风险/内容）都能追溯。
    """
    model, conditions = metric_query(metric_key, period_start, period_end)
    total = count_metric(db, metric_key, period_start, period_end)
    rows = (
        db.execute(
            select(model)
            .where(*conditions)
            .order_by(model.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return [_row_for(db, metric_key, r) for r in rows], total


def actual_value(metric_key: str, obj) -> float | None:
    """把指标口径下的实体折算成"金额/数值"（用于金额类汇总）。

    ★ 只有商机指标有金额语义；其它返回 None，由调用方决定是否给金额合计。
    """
    if metric_key in ("opportunity_new", "opportunity_won", "opportunity_open") and isinstance(
        obj, Opportunity
    ):
        value = obj.won_amount if metric_key == "opportunity_won" else obj.amount
        return float(value) if value is not None else None
    return None


__all__ = [
    "DrilldownRow",
    "EMPTY_REASONS",
    "METRICS",
    "METRIC_GROUPS",
    "METRIC_KEYS",
    "MetricDef",
    "UnknownMetricError",
    "build_metric_evidence",
    "count_excluded_test",
    "count_metric",
    "drilldown_metric",
    "evidence_ref_for",
    "generate_report",
    "metric_query",
]
