"""工作台首页（dashboard）的 Pydantic 契约。

★ 与其它模块同一套硬约束：
  · 每个业务数字都是 EvidenceValue（D4）—— 前端只显示，不得自己算；
  · 「今日重点任务」只由**可复现的查询条件**产生（见 TaskReason），
    不做「高意向 / 87% 转化」这类没有依据的推断分类；
  · 一条都没有时 state=NO_DATA、value=null（界面显示「—」），绝不用 0 冒充「没有数据」；
  · 图表（客户分布 / 增长趋势 / 客户来源）在**零数据时返回空数组**，
    前端画空态，绝不编造比例。

数据结构按首页三栏布局组织：
    greeting / pending   —— 中央顶部欢迎区 + 今日待处理
    kpis                 —— 中央第一层 5 张 KPI
    tasks                —— 中央「今日重点任务」
    region_distribution  —— 中央右侧「客户分布」环形图
    growth_trend         —— 中央右侧「客户增长趋势」折线图
    source_breakdown     —— 中央右侧「客户来源」列表
    recent_activities    —— 中央底部「最近客户动态」时间线
    data_connections     —— 中央底部「数据连接状态」
    today_report         —— 右栏「今日汇报」
    ai_capabilities      —— 右栏「AI 助手」能力清单（静态文案，非业务数字）
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.models.customer import CustomerSourceType, HandoverState, LifecycleStatus
from app.schemas.evidence import EvidenceValue


class TaskReason(str, Enum):
    """今日任务的触发原因。每个取值背后都是一条确定的 SQL 条件，可复现、可追溯。"""

    HUMAN_REQUIRED = "HUMAN_REQUIRED"  # handover_state = HUMAN_REQUIRED
    HUMAN_ACTIVE = "HUMAN_ACTIVE"      # handover_state = HUMAN_ACTIVE
    AI_FAILED = "AI_FAILED"            # 存在 ai_status = FAILED 的消息
    PENDING_REVIEW = "PENDING_REVIEW"  # dedupe_state = PENDING_REVIEW
    NEW = "NEW"                        # 本周新增、尚未联系


TASK_REASON_DETAILS: dict[TaskReason, str] = {
    TaskReason.HUMAN_REQUIRED: "策略闸门判定敏感，AI 不许自动回复，需人工处理",
    TaskReason.HUMAN_ACTIVE: "人工已接管，处理中",
    TaskReason.AI_FAILED: "AI 回复失败，需要人工接手",
    TaskReason.PENDING_REVIEW: "去重命中多条既有客户，需人工裁决",
    TaskReason.NEW: "本周新增，尚未联系",
}

#: TaskReason -> 参考图上的「意向等级 / 沟通状态」标签文案。
#: ★ 这是**展示映射**：真实判定条件就是 TaskReason 本身，不是 AI 打分。
TASK_REASON_LABELS: dict[TaskReason, str] = {
    TaskReason.HUMAN_REQUIRED: "高风险",
    TaskReason.HUMAN_ACTIVE: "需人工处理",
    TaskReason.AI_FAILED: "需人工处理",
    TaskReason.PENDING_REVIEW: "待裁决",
    TaskReason.NEW: "新客户",
}


class GreetingBlock(BaseModel):
    """中央顶部欢迎区。名字没有真实用户表 → 由前端用固定文案，这里只给时段问候语。"""

    salutation: str = Field(description="按时段生成：早上好 / 下午好 / 晚上好")
    subtitle: str
    timezone: str


class PendingBlock(BaseModel):
    """今日待处理摘要（参考图右上角那块）。三个子项各有真实来源。"""

    total: EvidenceValue = Field(description="需要人接手的客户条数")
    human_required: EvidenceValue = Field(description="handover_state=HUMAN_REQUIRED")
    human_active: EvidenceValue = Field(description="handover_state=HUMAN_ACTIVE")
    ai_failed: EvidenceValue = Field(description="存在 AI 回复失败消息的客户数")


class KpiBlock(BaseModel):
    """中央第一层 5 张 KPI 卡。

    ★ 商机数量 / 成交订单的口径（D24 → D32 修正）：
        商机表（opportunities）**有记录时以它为准**：
          商机数量 = 进行中的商机数（不含赢单/丢单）—— 进行中才是"待推进的机会"
          成交订单 = 赢单（WON）的商机数
        商机表**一条都没有**时，回退用 customers.lifecycle_status 近似
          （商机 = 推进到「已报价」及之后的客户；成交 = 「已成交」的客户），
          并在 `opportunity_source` 里如实说明用的是哪种口径。
    ★ 两种情况都**不编数字**：回退口径也是真实状态分布算出来的。
    """

    customer_total: EvidenceValue
    customer_new_this_week: EvidenceValue
    opportunity_count: EvidenceValue
    won_orders: EvidenceValue
    risk_customers: EvidenceValue = Field(description="= 需人工处理（HUMAN_REQUIRED）")
    opportunity_source: str = Field(
        default="OPPORTUNITY_TABLE",
        description="OPPORTUNITY_TABLE = 读商机表；LIFECYCLE_FALLBACK = 商机表为空，用客户生命周期近似",
    )
    opportunity_note: str = Field(
        default="", description="如实说明这两个数字的口径，便于核对"
    )


class TaskItem(BaseModel):
    """一条「今天需要我处理」的客户事项。"""

    id: int
    name: str
    company_name: str | None = None
    source_type: CustomerSourceType
    lifecycle_status: LifecycleStatus
    handover_state: HandoverState
    reason: TaskReason
    reason_label: str = Field(description="参考图上的状态标签文案（由 TASK_REASON_LABELS 给出）")
    reason_detail: str = Field(description="该原因的可读说明")
    occurred_at: datetime = Field(description="触发时间：来自真实列，不是当前时间")
    evidence_ref: str = Field(description="证据锚点，前端可点开核对")


class TaskCount(BaseModel):
    """某一类任务的条数（供参考图上的筛选标签显示真实条数）。"""

    reason: TaskReason
    label: str
    count: int


class TaskSection(BaseModel):
    total: EvidenceValue = Field(description="需要人处理的客户总条数（业务数字）")
    counts: list[TaskCount] = Field(
        default_factory=list, description="按原因分类的真实条数（前端筛选标签用）"
    )
    items: list[TaskItem] = Field(default_factory=list, description="明细（最多 N 条）")


class RegionSlice(BaseModel):
    """客户分布的一片。region 取自 customers.region；为空归入「未填写」。"""

    region: str
    count: int


class RegionDistribution(BaseModel):
    total: EvidenceValue = Field(description="参与分布统计的客户总数")
    slices: list[RegionSlice] = Field(
        default_factory=list, description="★ 零数据时为空数组，前端画空态，不编造比例"
    )


class TrendPoint(BaseModel):
    day: date
    count: int


class GrowthTrend(BaseModel):
    days: int
    total: EvidenceValue = Field(description="区间内新增客户数")
    points: list[TrendPoint] = Field(default_factory=list)


class SourceSlice(BaseModel):
    """客户来源一片。来源取自 customers.source_type（REAL/TEST/MANUAL）。"""

    source: str
    label: str
    count: int


class SourceBreakdown(BaseModel):
    total: EvidenceValue
    slices: list[SourceSlice] = Field(default_factory=list)


class ActivityItem(BaseModel):
    """最近客户动态的一条。★ 只来自 customer_events 真实事件表。"""

    occurred_at: datetime
    customer_id: int
    customer_name: str
    kind: str = Field(description="事件类型：STATUS_CHANGED / MESSAGE_SENT / HANDOVER / NOTE")
    title: str = Field(description="事件标题，如「状态变更」「AI 回复」")
    detail: str | None = None
    actor: str = Field(description="发起方：HUMAN / AI / SYSTEM")
    evidence_ref: str


class RecentActivities(BaseModel):
    total: EvidenceValue
    items: list[ActivityItem] = Field(default_factory=list)


class DataConnection(BaseModel):
    key: str
    label: str
    connected: bool
    last_sync_at: datetime | None = Field(default=None, description="最近一次成功导入时间")
    note: str


class DataConnectionsBlock(BaseModel):
    items: list[DataConnection]
    connected_count: EvidenceValue


class ReportMetric(BaseModel):
    """报告快照里的一个指标（原样来自 reports.content_json，不重算、不编造）。"""

    key: str
    label: str
    evidence: dict = Field(description="EvidenceValue 形态（原样透传）")


class ReportDigest(BaseModel):
    """右栏「今日汇报」摘要。

    ★ V1 的报告只有 customer_total / customer_new 两个指标（见 services/reports.py 的 METRICS）。
      因此这里返回**指标列表**而不是写死四个字段 —— 报告里有什么就显示什么，
      没有的（跟进客户 / 成交 / 风险）不显示，**绝不返回 0 冒充**。
    """

    available: bool
    report_id: int | None = None
    report_type: str | None = None
    generated_at: datetime | None = None
    period_start: date | None = None
    period_end: date | None = None
    excluded_test_count: int = 0
    metrics: list[ReportMetric] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list, description="重点事项（来自报告，不编）")
    note: str


class AiCapability(BaseModel):
    key: str
    label: str


class AiAssistantBlock(BaseModel):
    """右栏 AI 助手。

    ★ capabilities / boundaries 都是静态说明，不是业务数字。
    ★ boundaries 是**如实声明的能力边界**（答不了什么）：
      只写"能做什么"而不写"不能做什么"，用户会误以为它什么都能答。
    """

    title: str
    badge: str
    greeting: str
    capabilities: list[AiCapability]
    boundaries: list[str] = Field(
        default_factory=list, description="明确答不了的范围，与问答口径共用一份来源"
    )
    blocked_note: str = Field(default="", description="硬拦范围（策略闸门在模型之前拦下）")
    input_placeholder: str


class DashboardSummaryResponse(BaseModel):
    generated_at: datetime
    greeting: GreetingBlock
    pending: PendingBlock
    kpis: KpiBlock
    tasks: TaskSection
    region_distribution: RegionDistribution
    growth_trend: GrowthTrend
    source_breakdown: SourceBreakdown
    recent_activities: RecentActivities
    data_connections: DataConnectionsBlock
    today_report: ReportDigest
    ai_assistant: AiAssistantBlock


# ══════════════════════════════════════════════════════════════════════
# AI 助手问答（Copilot 提问，不是给某个客户起草回复）
# ══════════════════════════════════════════════════════════════════════
class AiAskKind(str, Enum):
    """问题归类。

    ★ 三类边界（决定回答方式）：
        能答        PENDING / CUSTOMERS / SOURCES / REPORT / ACTIVITY / DATA / CUSTOMER_DETAIL
        越界礼貌拒  OUT_OF_SCOPE（竞对情报、库外数据、改写客户消息…）
        硬拦        —— 不走这里，由 PolicyGate 拦下（见 basis=policy_blocked）
    """

    CUSTOMER_DETAIL = "CUSTOMER_DETAIL"  # 查某个具体客户（"张伟什么情况"）
    PENDING = "PENDING"                  # 今天要处理什么 / 待处理
    RISK = "RISK"                        # 风险 / 敏感咨询 / 有没有问题
    VISITS = "VISITS"                    # 客户访问了什么
    CUSTOMERS = "CUSTOMERS"              # 客户数量 / 新增 / 分布
    SOURCES = "SOURCES"                  # 客户来源
    REPORT = "REPORT"                    # 汇报 / 老板要看
    # ★ TASK-041：商机与项目此前**问不到**（实测"现在有几个商机？"答成不在范围内，
    #   而数据其实有）。数据本来就在系统里、报告里也在用，所以补上。
    OPPORTUNITY = "OPPORTUNITY"          # 商机 / 项目 / 订单 / 赢单丢单
    ACTIVITY = "ACTIVITY"                # 最近动态 / 发生了什么
    DATA = "DATA"                        # 数据连接 / 同步是否正常
    CAPABILITY = "CAPABILITY"            # 你能干什么
    GREETING = "GREETING"                # 打招呼 / 闲聊开场
    OUT_OF_SCOPE = "OUT_OF_SCOPE"        # 超出系统数据范围
    GENERAL = "GENERAL"                  # 其它


class AiFact(BaseModel):
    """一条**事实**（FACT）。value 原样来自后端计算，前端只显示。

    ★ evidence 声明为 EvidenceValue（强类型）而不是 dict：
      之前声明成 dict，运行时传入 EvidenceValue 对象会直接 500
      （实测：访问类问题全部返回空响应）。强类型能在校验期就抓住这类错误。
    """

    label: str
    evidence: EvidenceValue = Field(description="EvidenceValue 形态，原样透传")
    source_note: str = Field(description="这个数字从哪来，便于核对")


class AiAskResponse(BaseModel):
    """AI 助手的一次回答。

    ★ 事实 / 建议严格分离（总控提示词 §三）：
        facts        —— 确定性计算出来的事实，带 evidence_ref 可追溯
        answer       —— 基于事实的组织语言；没有事实时必须明说没有数据
        basis        —— answer 是怎么来的（facts / llm / 都没用上）
        policy       —— 敏感问题的闸门结论（命中则不调 LLM）
    """

    question: str
    kind: AiAskKind
    answer: str
    basis: str = Field(
        description="llm | llm_no_facts | no_data | policy_blocked | out_of_scope | capability | llm_failed"
    )
    facts: list[AiFact] = Field(default_factory=list)
    policy: dict = Field(description="闸门结论（allowed / label / matched / reason）")
    llm_called: bool = False
    llm_ok: bool = False
    note: str = Field(description="如实说明数据边界 / 能力边界")
    context_customer_id: int | None = Field(
        default=None,
        description="本轮话题客户 id；前端在下一轮原样回传，即可支持“他什么情况”这类追问",
    )
