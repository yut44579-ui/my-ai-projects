"""商业洞察服务（TASK-031，需求 §八 AI 能力 → 商业洞察）。

═══════════════════════════════════════════════════════════════════════
【这个页面到底能做什么、不能做什么 —— 先说清楚】
═══════════════════════════════════════════════════════════════════════
    "商业洞察"通常意味着**市场/竞对/行业**层面的判断。但那需要外部数据
    （市场规模、竞对报价、行业增速…），**本系统里没有**，也不联网抓取。

    所以本模块**只做两件诚实的事**：
      ① 用系统里**真实存在的业务数据**做跨模块洞察
         （客户结构 / 商机漏斗 / 项目健康 / 风险敞口 / 内容产出）
         —— 每条洞察都带 evidence_ref，可点开核对
      ② **明确列出缺什么**：哪些洞察因为缺外部数据而做不了。
         缺数据时返回 NO_DATA 并说明原因，**不猜、不估算、不给"行业经验值"**。

    ★ 宁可页面看起来"能说的不多"，也不编一段像模像样的市场判断。
      这正是 §二（禁止制造业务假数据）与 §二十六（不知道就明说不知道）。

═══════════════════════════════════════════════════════════════════════
【洞察的判定规则：全部可解释】
═══════════════════════════════════════════════════════════════════════
    不用模型打分，用**明确的阈值规则**，这样人能看懂"为什么给出这条洞察"。
    每条洞察带 `rule` 字段说明判定规则。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.content import MarketingContent
from app.models.customer import Customer, CustomerSourceType
from app.models.opportunity import CLOSED_STAGES, Opportunity
from app.models.project import Project, ProjectStatus
from app.models.risk_event import RiskEvent, RiskLevel, RiskStatus

#: 商机停滞判定：超过这么多天没关闭且仍开着 → 提示关注
STALE_OPPORTUNITY_DAYS = 30


@dataclass
class InsightFinding:
    """一条洞察。"""

    key: str
    title: str
    finding: str          # 结论（人能读懂的话）
    value: int | None     # 支撑数字；None 表示取不到
    unit: str             # 数字单位或口径说明
    severity: str         # info / attention / warning
    rule: str             # ★ 判定规则（为什么给出这条）
    evidence_ref: str     # 证据锚点


@dataclass
class MissingCapability:
    """做不了、需要外部数据的洞察。★ 如实列出，不糊弄。"""

    key: str
    title: str
    why_missing: str
    would_need: str


def _count(db: Session, stmt) -> int:
    return int(db.scalar(stmt) or 0)


def collect_findings(db: Session) -> list[InsightFinding]:
    """基于**真实业务数据**产出洞察。取不到的项不产出（而不是填 0 假装正常）。"""
    findings: list[InsightFinding] = []

    # ── ① 客户构成：真实客户占比 ──
    total_customers = _count(db, select(func.count(Customer.id)))
    real_customers = _count(
        db,
        select(func.count(Customer.id)).where(Customer.source_type == CustomerSourceType.REAL),
    )
    if total_customers:
        if real_customers == 0:
            findings.append(
                InsightFinding(
                    key="customer_real_share",
                    title="客户构成",
                    finding=(
                        f"系统里有 {total_customers} 个客户，但「没有一个」是真实来源"
                        "（全部为测试/导入数据）。当前任何基于客户的结论都不能当经营依据。"
                    ),
                    value=0,
                    unit=f"真实客户 / 共 {total_customers} 个",
                    severity="warning",
                    rule="统计 customers 表中 source_type=REAL 的数量；为 0 即提示",
                    evidence_ref="customers:count",
                )
            )
        else:
            share = round(real_customers * 100 / total_customers, 1)
            findings.append(
                InsightFinding(
                    key="customer_real_share",
                    title="客户构成",
                    finding=f"真实来源客户 {real_customers} 个，占全部 {total_customers} 个的 {share}%。",
                    value=real_customers,
                    unit="个真实客户",
                    severity="info" if share >= 50 else "attention",
                    rule="统计 source_type=REAL 占比；低于 50% 提示关注",
                    evidence_ref="customers:real_count",
                )
            )

    # ── ② 获客渠道覆盖度 ──
    with_channel = _count(
        db, select(func.count(Customer.id)).where(Customer.acquisition_channel.is_not(None))
    )
    if total_customers:
        rate = round(with_channel * 100 / total_customers, 1)
        findings.append(
            InsightFinding(
                key="channel_coverage",
                title="获客渠道完整度",
                finding=(
                    f"{with_channel}/{total_customers} 个客户填了获客渠道（{rate}%）。"
                    + (
                        "渠道信息缺失会让「哪个渠道有效」无法判断。"
                        if rate < 80
                        else "渠道信息较完整，可用于分析渠道效果。"
                    )
                ),
                value=with_channel,
                unit="个客户有渠道",
                severity="info" if rate >= 80 else "attention",
                rule="统计 acquisition_channel 非空的客户占比；低于 80% 提示补齐",
                evidence_ref="customers:channel_coverage",
            )
        )

    # ── ③ 商机停滞 ──
    cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_OPPORTUNITY_DAYS)
    open_opps = _count(
        db,
        select(func.count(Opportunity.id)).where(
            Opportunity.is_active.is_(True), Opportunity.stage.notin_(list(CLOSED_STAGES))
        ),
    )
    stale_opps = _count(
        db,
        select(func.count(Opportunity.id)).where(
            Opportunity.is_active.is_(True),
            Opportunity.stage.notin_(list(CLOSED_STAGES)),
            Opportunity.updated_at < cutoff,
        ),
    )
    if open_opps:
        findings.append(
            InsightFinding(
                key="stale_opportunities",
                title="商机推进节奏",
                finding=(
                    f"进行中的商机 {open_opps} 个，其中 {stale_opps} 个超过 {STALE_OPPORTUNITY_DAYS} 天没有更新。"
                    + ("建议逐个确认是否还在推进。" if stale_opps else "推进节奏正常。")
                ),
                value=stale_opps,
                unit=f"个停滞商机 / 共 {open_opps} 个进行中",
                severity="attention" if stale_opps else "info",
                rule=f"找出未关闭且 updated_at 早于 {STALE_OPPORTUNITY_DAYS} 天前的商机",
                evidence_ref="opportunities:stale",
            )
        )

    # ── ④ 项目逾期 ──
    # ★ 字段名以 models/project.py 为准：due_date（计划完成）/ delivered_at（实际交付）
    active_projects = _count(
        db,
        select(func.count(Project.id)).where(
            Project.is_active.is_(True),
            Project.status.in_([ProjectStatus.PLANNING, ProjectStatus.IN_PROGRESS, ProjectStatus.ON_HOLD]),
        ),
    )
    overdue_projects = _count(
        db,
        select(func.count(Project.id)).where(
            Project.is_active.is_(True),
            Project.status.in_([ProjectStatus.PLANNING, ProjectStatus.IN_PROGRESS, ProjectStatus.ON_HOLD]),
            Project.due_date.is_not(None),
            Project.due_date < datetime.now(timezone.utc).date(),
            Project.delivered_at.is_(None),
        ),
    )
    if active_projects:
        findings.append(
            InsightFinding(
                key="overdue_projects",
                title="项目交付风险",
                finding=(
                    f"进行中的项目 {active_projects} 个，其中 {overdue_projects} 个已超过计划完成时间但未交付。"
                    if overdue_projects
                    else f"进行中的项目 {active_projects} 个，没有逾期项目。"
                ),
                value=overdue_projects,
                unit=f"个逾期项目 / 共 {active_projects} 个",
                severity="warning" if overdue_projects else "info",
                rule="找出计划完成时间(due_date)已过、且没有实际交付时间(delivered_at)的未结项项目",
                evidence_ref="projects:overdue",
            )
        )

    # ── ⑤ 风险敞口 ──
    open_risks = _count(
        db, select(func.count(RiskEvent.id)).where(RiskEvent.status == RiskStatus.OPEN)
    )
    high_risks = _count(
        db,
        select(func.count(RiskEvent.id)).where(
            RiskEvent.status == RiskStatus.OPEN, RiskEvent.level == RiskLevel.HIGH
        )
    )
    findings.append(
        InsightFinding(
            key="risk_exposure",
            title="风险敞口",
            finding=(
                f"当前待处理风险 {open_risks} 条" + (f"，其中高风险 {high_risks} 条。" if high_risks else "。")
                if open_risks
                else "当前没有待处理风险。"
            ),
            value=open_risks,
            unit="条待处理风险",
            severity="warning" if high_risks else ("attention" if open_risks else "info"),
            rule="统计 risk_events 中 status=OPEN 的数量，并单列 level=HIGH",
            evidence_ref="risk_events:open",
        )
    )

    # ── ⑥ 内容产出 ──
    published = _count(
        db,
        select(func.count(MarketingContent.id)).where(MarketingContent.is_active.is_(True)),
    )
    findings.append(
        InsightFinding(
            key="content_output",
            title="内容产出",
            finding=(
                f"系统里有 {published} 条营销内容。"
                "★ 系统无法统计这些内容的实际获客/转化效果 —— 缺少渠道侧的回流数据。"
                if published
                else "还没有营销内容。"
            ),
            value=published,
            unit="条内容",
            severity="info",
            rule="统计 marketing_contents 的有效条数；效果数据因缺回流而不可得",
            evidence_ref="marketing_contents:count",
        )
    )

    return findings


def missing_capabilities() -> list[MissingCapability]:
    """★ 如实列出"做不了"的洞察，以及需要什么才能做。

    这一段是本页存在的主要理由之一：与其编一段市场判断，
    不如明确告诉使用者「要得到市场洞察，你需要先接入这些数据」。
    """
    return [
        MissingCapability(
            key="market_size",
            title="市场规模与增速",
            why_missing="系统内没有行业数据源，也不联网抓取公开报告",
            would_need="接入行业协会数据 / 购买市场报告 / 人工录入市场规模数据",
        ),
        MissingCapability(
            key="competitor_pricing",
            title="竞对报价与打法",
            why_missing="系统内没有竞对数据（只有知识库里可能存在的竞品资料片段）",
            would_need="人工录入竞对情报，或接入第三方竞对监测服务",
        ),
        MissingCapability(
            key="content_conversion",
            title="内容实际转化效果",
            why_missing="缺少渠道侧回流数据（阅读量、点击、留资），只有发布条数",
            would_need="官网/公众号等渠道的数据回调（对应 §二十 的连接器，V1 未接入）",
        ),
        MissingCapability(
            key="customer_profitability",
            title="客户利润贡献",
            why_missing="系统没有成本与回款数据，只有商机金额",
            would_need="接入财务/ERP 数据（科目、成本、回款）",
        ),
    ]


def overview(db: Session) -> dict:
    findings = collect_findings(db)
    return {
        "findings": findings,
        "missing": missing_capabilities(),
        "data_coverage": {
            "customers": _count(db, select(func.count(Customer.id))),
            "opportunities_open": _count(
                db,
                select(func.count(Opportunity.id)).where(
                    Opportunity.is_active.is_(True), Opportunity.stage.notin_(list(CLOSED_STAGES))
                ),
            ),
            "projects_active": _count(
                db, select(func.count(Project.id)).where(Project.is_active.is_(True))
            ),
            "risks_open": _count(
                db, select(func.count(RiskEvent.id)).where(RiskEvent.status == RiskStatus.OPEN)
            ),
            "contents": _count(
                db, select(func.count(MarketingContent.id)).where(MarketingContent.is_active.is_(True))
            ),
        },
    }


__all__ = [
    "STALE_OPPORTUNITY_DAYS",
    "InsightFinding",
    "MissingCapability",
    "collect_findings",
    "missing_capabilities",
    "overview",
]
