"""市场研究页的接口契约（TASK-011）。

═══════════════════════════════════════════════════════════════════════
【这个页面能回答什么，不能回答什么（如实声明，不编造）】
═══════════════════════════════════════════════════════════════════════
能回答（全部来自**库内真实数据**）：
    · 目标客户长什么样：行业词频（从公司名里数出来的字面词，不是 AI 归类）、
      地区分布、获客渠道
    · 客户处在什么阶段：生命周期分布
    · 哪些地区/渠道/公司值得优先投入（客户多 + 有活跃事件）
    · 客户在关心什么（按类型统计**真实沟通记录**，不逐条外发隐私）
    · 需求集中度（命中策略闸门的类别分布）

**不能回答**（系统里没有这些数据，明确拒绝而不是编）：
    · 竞争对手、市场份额、行业规模、外部趋势
    · 任何来自互联网但未经授权采集的数据（§十）

★ 行业/地区/渠道的数字都是**字面统计**（分组计数），
  样本不足时返回 NO_DATA（界面显示「—」），绝不给"看起来合理"的百分比。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.evidence import EvidenceValue


class CountSlice(BaseModel):
    """一条分组计数（地区 / 渠道 / 生命周期 / 行业词 / 消息类型 / 风险类别）。"""

    key: str
    label: str
    count: int
    share: float | None = Field(
        default=None,
        description="占比（0~1）。★ 仅当样本量达到阈值才给，样本不足时为 null",
    )


class ResearchBoundary(BaseModel):
    """能力边界声明。★ 让页面能如实告诉用户"这些我答不了"。"""

    available: list[str]
    unavailable: list[str]
    reason: str


class ResearchResponse(BaseModel):
    """市场研究概览。"""

    generated_at: datetime
    sample_size: EvidenceValue = Field(description="参与统计的客户数（样本量）")
    sample_sufficient: bool = Field(
        description="样本量是否够做分布解读（< MIN_SAMPLE 时界面只给计数、不给占比结论）"
    )
    min_sample: int

    region_distribution: list[CountSlice] = Field(default_factory=list)
    channel_distribution: list[CountSlice] = Field(default_factory=list)
    lifecycle_distribution: list[CountSlice] = Field(default_factory=list)
    industry_keywords: list[CountSlice] = Field(
        default_factory=list, description="公司名字面词频（不是 AI 行业归类）"
    )

    top_regions: list[CountSlice] = Field(
        default_factory=list, description="值得优先投入的地区（客户多 + 有活跃事件）"
    )
    activity_by_region: list[CountSlice] = Field(
        default_factory=list, description="各地区的事件活跃度（访问/沟通/接管）"
    )

    message_mix: list[CountSlice] = Field(
        default_factory=list, description="沟通记录按类型分布（不含消息正文）"
    )
    message_total: EvidenceValue = Field(description="沟通记录总数")
    demand_signals: list[CountSlice] = Field(
        default_factory=list, description="命中策略闸门的类别分布 = 需求集中在哪里"
    )

    boundary: ResearchBoundary
    note: str
