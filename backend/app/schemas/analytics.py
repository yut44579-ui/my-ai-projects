"""数据分析页的接口契约（TASK-012）。

═══════════════════════════════════════════════════════════════════════
【与「市场研究」页的分工（刻意不重复）】
═══════════════════════════════════════════════════════════════════════
    市场研究（/research）  = 客户**长什么样**：地区/渠道/阶段的构成（截面分布）
    数据分析（本页）       = 客户**怎么流动**：时序趋势 / 生命周期漏斗 / 数据质量

所以本页三块内容与市场研究零重叠：
    ① 趋势     —— 按时间聚合（新增客户、活跃事件），回答"在变好还是变差"
    ② 漏斗     —— 生命周期转化（累计口径），回答"卡在哪一步"
    ③ 数据质量 —— 缺字段 / 待裁决，回答"这份数据能不能信"

═══════════════════════════════════════════════════════════════════════
【两条硬约束】
═══════════════════════════════════════════════════════════════════════
· 漏斗用**累计口径**：一个人不可能"已联系"却"未联系"。
  枚举是离散状态而非阶段，直接按当前状态计数会得出"漏斗越往后越大"这种错结论。
  因此用 RANK 判定"达到或超过某一阶段"。

· 转化率只在**分母有意义**时给：分母为 0 → null（界面显示「—」），不是 0%。
  样本量同样受阈值约束（< MIN_SAMPLE 不给比率结论）。

★ 所有数字是 EvidenceValue（D4）；本页不调用模型。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.evidence import EvidenceValue


class TrendPoint(BaseModel):
    """趋势上的一个点。"""

    day: str = Field(description="本地日期 YYYY-MM-DD（Asia/Shanghai）")
    new_customers: int = Field(description="当天新增客户数")
    events: int = Field(description="当天发生的客户事件数")
    visits: int = Field(description="当天客户访问数")


class FunnelStage(BaseModel):
    """漏斗的一级（累计口径）。"""

    status: str = Field(description="生命周期状态枚举值")
    label: str
    reached: int = Field(description="达到或超过该阶段的客户数（累计）")
    conversion_from_prev: float | None = Field(
        default=None,
        description="相对上一级的转化率；分母为 0 或样本不足时为 null（界面显示「—」）",
    )
    conversion_from_total: float | None = Field(
        default=None, description="相对客户总数的转化率；分母为 0 或样本不足时为 null"
    )


class QualityIssue(BaseModel):
    """一条数据质量问题。"""

    key: str
    label: str
    count: int = Field(description="受影响的客户数")
    severity: str = Field(description="HIGH 阻塞跟进 / MEDIUM 影响判断 / LOW 仅影响统计")
    hint: str = Field(description="怎么处理")
    evidence_ref: str


class SourceQuality(BaseModel):
    """按来源看数据质量（例如测试数据占比）。"""

    source_type: str
    label: str
    count: int
    share: float | None = Field(default=None, description="样本不足时为 null")


class AnalyticsResponse(BaseModel):
    """数据分析概览。"""

    generated_at: datetime
    total: EvidenceValue = Field(description="客户总数")
    new_this_week: EvidenceValue = Field(description="本周新增")
    active_customers: EvidenceValue = Field(description="有事件留痕的客户数")
    trend_days: int

    trend: list[TrendPoint] = Field(default_factory=list, description="近 N 天趋势（含 0 值，便于看断档）")
    funnel: list[FunnelStage] = Field(default_factory=list)
    quality: list[QualityIssue] = Field(default_factory=list)
    quality_ok_total: EvidenceValue = Field(description="没有任何质量问题的客户数")
    source_quality: list[SourceQuality] = Field(default_factory=list)

    sample_sufficient: bool
    note: str
