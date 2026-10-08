"""市场研究接口（TASK-011）。

═══════════════════════════════════════════════════════════════════════
【设计原则：只做"字面统计"，不做"AI 洞察"】
═══════════════════════════════════════════════════════════════════════
本模块**不调用 LLM**。原因：
    · 市场结论属总控提示词 §三 的 INFERENCE / BUSINESS_CONCLUSION，
      没有真实市场数据时，模型只能编；
    · 宁可给"11 个客户里 10 个还是新客户"这种可核对的字面事实，
      也不给"该行业正处于高速增长期"这种听起来专业但无依据的结论。

行业词频：从 customers.company_name 里**数词**，不是 AI 归类。
    ★ 只统计出现 ≥2 次、长度 ≥2 的词，样本不足就不给结论。这是刻意的低智设计：
      它不会把"深圳前海科技"归到"金融科技"，但也绝不会编出一个行业。

占比（share）只在样本量 ≥ MIN_SAMPLE 时给出，否则为 null：
    3 个客户算出"33.3%"会让人误以为是市场结论。

═══════════════════════════════════════════════════════════════════════
【明确不做的】
═══════════════════════════════════════════════════════════════════════
    · 竞争对手 / 市场份额 / 行业规模 / 外部趋势（系统里没有这些数据）
    · 未经授权采集的互联网数据（§十）
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.customer import Customer, CustomerSourceType, LifecycleStatus
from app.models.customer_event import CustomerEvent
from app.models.customer_message import CustomerMessage, MessageType, SenderType
from app.models.risk_event import RiskEvent
from app.schemas.evidence import SourceType, build_evidence, no_data_evidence
from app.schemas.research import CountSlice, ResearchBoundary, ResearchResponse
from app.schemas.visits import VISIT_CHANNELS

router = APIRouter(prefix="/research", tags=["research"])

#: 样本量阈值：低于它只给计数、不给占比（避免"3 个客户算出 33%"被当成市场结论）
MIN_SAMPLE = 10

#: 公司名里要忽略的通用词（否则"有限公司"会排第一，毫无信息量）
STOPWORDS = {
    "有限公司", "股份有限公司", "有限责任公司", "公司", "集团", "科技", "技术",
    "网络", "信息", "有限", "责任", "分公司", "中国", "国际",
}

LIFECYCLE_LABELS: dict[LifecycleStatus, str] = {
    LifecycleStatus.NEW: "未联系",
    LifecycleStatus.CONTACTED: "已联系",
    LifecycleStatus.REPLIED: "已回复",
    LifecycleStatus.ENGAGED: "持续沟通",
    LifecycleStatus.QUOTED: "已报价",
    LifecycleStatus.WON: "已成交",
    LifecycleStatus.LOST: "已失效",
}

MESSAGE_TYPE_LABELS: dict[MessageType, str] = {
    MessageType.CHAT: "对话",
    MessageType.NOTE: "备注",
    MessageType.IMPORT: "随导入带入",
}

SOURCE_LABELS: dict[CustomerSourceType, str] = {
    CustomerSourceType.REAL: "真实业务数据",
    CustomerSourceType.TEST: "测试数据",
    CustomerSourceType.MANUAL: "人工录入",
}


def _slices(counter: Counter, labels: dict | None = None, sample: int = 0) -> list[CountSlice]:
    """把 Counter 转成 CountSlice 列表（按数量倒序）。

    ★ share 只在 sample >= MIN_SAMPLE 时给：样本不足时给占比会误导。
    """
    total = sum(counter.values())
    out: list[CountSlice] = []
    for key, n in counter.most_common():
        out.append(
            CountSlice(
                key=str(key),
                label=(labels or {}).get(key, str(key)) if labels else str(key),
                count=int(n),
                share=(n / total) if (total and sample >= MIN_SAMPLE) else None,
            )
        )
    return out


def _industry_keywords(db: Session) -> list[CountSlice]:
    """从公司名里数字面词（不是 AI 行业归类）。

    规则（刻意保守）：
      · 只取长度 ≥2 的中文/字母词；
      · 去掉"有限公司"这类通用词；
      · 只保留出现 ≥2 次的词 —— 出现一次的词不构成"行业信号"。
    """
    names = [n for (n,) in db.execute(select(Customer.company_name)).all() if n]
    counter: Counter = Counter()
    for name in names:
        for token in re.findall(r"[\u4e00-\u9fa5A-Za-z]{2,}", name):
            if token in STOPWORDS:
                continue
            counter[token] += 1
    frequent = Counter({k: v for k, v in counter.items() if v >= 2})
    return _slices(frequent, sample=len(names))


@router.get("/overview", response_model=ResearchResponse, summary="市场研究概览（字面统计，不编结论）")
def research_overview(db: Session = Depends(get_db)) -> ResearchResponse:
    """市场研究概览。★ 不调 LLM：所有数字都是可核对的分组计数。"""
    now = datetime.now(timezone.utc)

    total = db.scalar(select(func.count(Customer.id))) or 0
    sample_ev = (
        build_evidence(total, source_type=SourceType.SYSTEM, evidence_ref="research:sample_size")
        if total
        else no_data_evidence("还没有客户数据", source_type=SourceType.SYSTEM,
                              evidence_ref="research:sample_size")
    )
    sufficient = total >= MIN_SAMPLE

    # ── 地区分布 ──
    region_counter = Counter()
    for region, n in db.execute(
        select(Customer.region, func.count(Customer.id)).group_by(Customer.region)
    ).all():
        region_counter[region or "未填写"] += int(n)

    # ── 获客渠道（复用客户来源口径）──
    source_counter = Counter()
    for st, n in db.execute(
        select(Customer.source_type, func.count(Customer.id)).group_by(Customer.source_type)
    ).all():
        source_counter[st] = int(n)

    # ── 生命周期分布 ──
    lifecycle_counter = Counter()
    for st, n in db.execute(
        select(Customer.lifecycle_status, func.count(Customer.id)).group_by(Customer.lifecycle_status)
    ).all():
        lifecycle_counter[st] = int(n)

    # ── 活跃度：各地区事件数（访问/沟通/接管都算）──
    activity_counter = Counter()
    rows = db.execute(
        select(Customer.region, func.count(CustomerEvent.id))
        .join(CustomerEvent, CustomerEvent.customer_id == Customer.id)
        .group_by(Customer.region)
    ).all()
    for region, n in rows:
        activity_counter[region or "未填写"] += int(n)

    # 值得优先投入 = 客户数前几名（附事件活跃度，供页面并排展示）
    top_regions = [
        CountSlice(key=k, label=k, count=int(v), share=(v / total) if sufficient and total else None)
        for k, v in region_counter.most_common(5)
    ]
    activity_by_region = [
        CountSlice(key=k, label=k, count=int(v), share=None)
        for k, v in activity_counter.most_common(5)
    ]

    # ── 沟通记录按类型（不取正文，避免把客户原话摊到研究页）──
    msg_counter = Counter()
    for mt, n in db.execute(
        select(CustomerMessage.message_type, func.count(CustomerMessage.id)).group_by(
            CustomerMessage.message_type
        )
    ).all():
        msg_counter[mt] = int(n)
    msg_total = db.scalar(select(func.count(CustomerMessage.id))) or 0

    # ── 需求信号：命中策略闸门的类别分布 ──
    demand_counter = Counter()
    for reason, n in db.execute(
        select(RiskEvent.reason, func.count(RiskEvent.id))
        .where(RiskEvent.event_type == "POLICY_BLOCKED")
        .group_by(RiskEvent.reason)
    ).all():
        # reason 形如「命中敏感策略：报价（闸门 D7 判定，未调用模型）」→ 取类别名
        label = "未分类"
        if reason and "：" in reason:
            label = reason.split("：", 1)[1].split("（", 1)[0] or label
        demand_counter[label] += int(n)

    boundary = ResearchBoundary(
        available=[
            "目标客户的地区 / 渠道 / 阶段构成（库内真实数据）",
            "公司名里的字面词频（不是 AI 行业归类）",
            "各地区的事件活跃度（访问 / 沟通 / 接管）",
            "客户沟通记录的类型分布（不含正文）",
            "需求集中度：命中策略闸门的类别分布",
        ],
        unavailable=[
            "竞争对手与市场份额",
            "行业规模、增长率、外部趋势",
            "未经授权采集的互联网数据",
        ],
        reason=(
            "本页面只做库内数据的字面统计，不调用模型生成市场结论："
            "没有真实市场数据时，任何「行业洞察」都只能是编的。"
        ),
    )

    note = (
        f"样本量 {total} 个客户"
        + ("" if sufficient else f"（少于 {MIN_SAMPLE} 个，因此不给出占比，只给计数）")
        + "；行业词频为字面统计，出现 ≥2 次才列出。"
    )

    return ResearchResponse(
        generated_at=now,
        sample_size=sample_ev,
        sample_sufficient=sufficient,
        min_sample=MIN_SAMPLE,
        region_distribution=_slices(region_counter, sample=total),
        channel_distribution=_slices(source_counter, SOURCE_LABELS, sample=total),
        lifecycle_distribution=_slices(lifecycle_counter, LIFECYCLE_LABELS, sample=total),
        industry_keywords=_industry_keywords(db),
        top_regions=top_regions,
        activity_by_region=activity_by_region,
        message_mix=_slices(msg_counter, MESSAGE_TYPE_LABELS, sample=msg_total),
        message_total=(
            build_evidence(msg_total, source_type=SourceType.SYSTEM,
                           evidence_ref="research:messages")
            if msg_total
            else no_data_evidence("还没有沟通记录", source_type=SourceType.SYSTEM,
                                  evidence_ref="research:messages")
        ),
        demand_signals=_slices(demand_counter, sample=len(demand_counter)),
        boundary=boundary,
        note=note,
    )
