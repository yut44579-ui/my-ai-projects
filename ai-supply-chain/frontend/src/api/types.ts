/**
 * 后端契约类型（与 backend/app/schemas 一一对应）。
 *
 * ★ 业务数字一律是 EvidenceValue（D4）：前端只负责显示，禁止自己算业务数字。
 */

export type ValueState = 'VALID' | 'NO_DATA' | 'ERROR'

/** D4 数据契约：带状态与证据引用的业务数字 */
export type EvidenceValue = {
  value: number | null
  state: ValueState
  source_type: string
  evidence_ref: string | null
  reason?: string | null
}

export type CustomerSourceType = 'REAL' | 'TEST' | 'MANUAL'
export type DedupeState = 'CLEAN' | 'PENDING_REVIEW' | 'MERGED'
export type BatchStatus = 'SUCCESS' | 'PARTIAL' | 'FAILED'

export type CustomerItem = {
  id: number
  name: string
  company_name: string | null
  phone: string | null
  email: string | null
  region: string | null
  note: string | null
  batch_id: number | null
  source_type: CustomerSourceType
  evidence_ref: string | null
  dedupe_state: DedupeState
  first_seen_at: string
  last_seen_at: string
  created_at: string
  updated_at: string
}

export type CustomerListResponse = {
  items: CustomerItem[]
  /** 分页元数据，不是业务数字 */
  page: number
  page_size: number
  /** 业务数字：当前筛选下的客户总数 */
  total: EvidenceValue
  /** 业务数字：全库 TEST 来源条数（顶部黄色 banner 用） */
  test_count: EvidenceValue
}

// ─────────────── TASK-002：客户详情 / 事件流 / 指标卡 ───────────────

/** 来源批次的可追溯信息（详情页「来源与可追溯」卡） */
export type BatchRef = {
  batch_id: number
  filename: string
  status: BatchStatus
  source_type: CustomerSourceType
  imported_at: string
  evidence_ref: string
}

export type SourceTrace = {
  source_type: CustomerSourceType
  evidence_ref: string | null
  batch_id: number | null
  batch: BatchRef | null
}

/** ★ V1 没有客户生命周期状态（customers 表已砍掉 status），这是显式占位 */
export type FollowUpStatus = {
  available: boolean
  label: string
  state: string
  note: string
}

export type CustomerDetail = CustomerItem & {
  source: SourceTrace
  status: FollowUpStatus
  /** 三个空区块：本 TASK 恒为 NO_DATA，前端只显示明确空态 */
  conversations: EvidenceValue
  risks: EvidenceValue
  batch_details: EvidenceValue
}

export type TimelineEventKind =
  | 'CUSTOMER_CREATED'
  | 'SOURCE_IMPORT'
  | 'DEDUPE_PENDING_REVIEW'
  | 'VISIT'
  /** ★ TASK-021：系统记录的事件（如"客户已读消息"回执） */
  | 'NOTE'

export type TimelineEvent = {
  kind: TimelineEventKind
  title: string
  occurred_at: string
  evidence_ref: string
  detail: string | null
}

/** 尚未接入的事件类型：显式 NO_DATA，绝不用空数组冒充「已接入但为空」 */
export type TimelineUnavailable = {
  kind: string
  label: string
  state: ValueState
  reason: string
}

export type CustomerTimelineResponse = {
  customer_id: number
  events: TimelineEvent[]
  events_count: EvidenceValue
  unavailable: TimelineUnavailable[]
}

export type CustomerStatsResponse = {
  /** 口径说明（与列表页同一个 q / source_type） */
  scope: string
  total: EvidenceValue
  test_count: EvidenceValue
  pending_review: EvidenceValue
  new_this_week: EvidenceValue
}

// ─────────────── TASK-003：沟通记录（customer_messages） ───────────────

/** 消息发送方。★ CUSTOMER 只可能来自将来的真实客户渠道，V1 前端永远不发送它（D9）。 */
export type SenderType = 'CUSTOMER' | 'HUMAN' | 'AI' | 'SYSTEM'
export type MessageType = 'CHAT' | 'NOTE' | 'IMPORT'
export type MessageSourceType = 'MANUAL' | 'IMPORT' | 'SYSTEM'

/** AI 回复状态：NONE=与 AI 无关；REQUESTED=已发起；REPLIED=已回复；FAILED=调用失败；HUMAN_REQUIRED=命中敏感策略转人工 */
export type AiStatus = 'NONE' | 'REQUESTED' | 'REPLIED' | 'FAILED' | 'HUMAN_REQUIRED'

export type MessageItem = {
  id: number
  customer_id: number
  sender_type: SenderType
  message_type: MessageType
  content: string
  source_type: MessageSourceType
  /** 由后端生成：customer_message:{id} */
  evidence_ref: string
  ai_status: AiStatus
  created_at: string

  // ── TASK-021：需求 §十二 的三个追溯维度 ──
  // ★ 三态：null = 未记录（界面显示「—」），true/false = 明确记录过。
  //   "还没确认"与"确认了是否"是两件事，不能都显示成"否"。
  human_confirmed: boolean | null
  auto_sent: boolean | null
  /** 本条消息触发的业务事件锚点；null = 未触发 */
  triggered_event_ref: string | null

  // ── TASK-021：已读状态（§九，钉钉语义）──
  /** 客户已读时间；null = 未读或渠道不支持回执 */
  read_at: string | null
  /** 回执来源渠道；null = 没有回执 */
  read_source: string | null
}

export type MessageTimelineResponse = {
  customer_id: number
  /** 按 (created_at, id) 正序 */
  items: MessageItem[]
  /** 分页元数据，不是业务数字 */
  page: number
  page_size: number
  /** 业务数字：该客户的消息条数；一条都没有时 state=NO_DATA、value=null（界面不显示数字） */
  total: EvidenceValue
}

export const SENDER_TYPE_LABELS: Record<SenderType, string> = {
  CUSTOMER: '客户',
  HUMAN: '人工',
  AI: 'AI',
  SYSTEM: '系统',
}

export const AI_STATUS_LABELS: Record<AiStatus, string> = {
  NONE: '',
  REQUESTED: 'AI 回复中',
  REPLIED: 'AI 已回复',
  FAILED: 'AI 回复失败',
  HUMAN_REQUIRED: '需人工处理',
}

export const MESSAGE_TYPE_LABELS: Record<MessageType, string> = {
  CHAT: '对话',
  NOTE: '备注',
  IMPORT: '随导入带入',
}

// ─────────────── TASK-004：AI 回复 ───────────────

/** 代码层策略闸门的结论（后端给，界面上只显示不判断） */
export type PolicyOutcome = {
  allowed: boolean
  reason: string
  category: string | null
  label: string | null
  matched: string | null
  tone: string
}

export type AiReplyResponse = {
  customer_id: number
  question: string
  /** 命中敏感策略时恒为 false（★ 不调 LLM 是硬规则，不是优化） */
  llm_called: boolean
  policy: PolicyOutcome
  ai_status: AiStatus
  /** 已落库的那一行（AI 回复或转人工说明） */
  message: MessageItem
  failure_reason: string | null
}

export type ColumnPreview = {
  column: string
  target: string | null
  confidence: 'auto' | 'unknown' | string
}

export type TargetField = { key: string; label: string }

export type ImportPreviewResponse = {
  filename: string
  file_sha256: string
  file_kind: string
  encoding: string | null
  encoding_note: string | null
  sheet_names: string[]
  sheet_used: string | null
  sheet_note: string | null
  header_row: number
  ignored_leading_rows: number
  headers: string[]
  rows_total: EvidenceValue
  columns: ColumnPreview[]
  unmapped_columns: string[]
  sample_rows: Record<string, string>[]
  target_fields: TargetField[]
}

export type SkippedRow = { row: number; reason: string; raw: string }
export type SkippedColumn = { column: string; reason: string }
export type DedupeConflict = {
  customer_id: number | null
  conflict_with: number[]
  kind: string
}

export type ImportCommitResponse = {
  batch_id: number
  filename: string
  file_sha256: string
  status: BatchStatus
  source_type: CustomerSourceType
  evidence_ref: string
  rows_total: EvidenceValue
  rows_created: EvidenceValue
  rows_deduplicated: EvidenceValue
  rows_skipped: EvidenceValue
  skipped_reasons: SkippedRow[]
  skipped_columns: SkippedColumn[]
  conflicts: DedupeConflict[]
  imported_at: string
}

export type BatchSummary = Omit<ImportCommitResponse, 'skipped_reasons' | 'skipped_columns' | 'conflicts'>

export type BatchListResponse = { items: BatchSummary[]; total: EvidenceValue }

// ─── 文件资料页（TASK-029，需求 §八 数据中心） ───

/** 批次里带出来的客户（用于"这批文件到底建了哪些客户"的追溯） */
export type BatchCustomerItem = {
  id: number
  name: string
  company_name: string | null
  phone: string | null
  email: string | null
  dedupe_state: string
  source_type: CustomerSourceType
  evidence_ref: string
}

/** 批次详情：导入的完整追溯入口（比列表多跳过明细与客户清单） */
export type BatchDetailResponse = BatchSummary & {
  skipped_reasons: SkippedRow[]
  skipped_columns: SkippedColumn[]
  customer_ids: number[]
  customers: BatchCustomerItem[]
  pending_review_customer_ids: number[]
}

/** 跳过原因 → 中文说明（与后端 SkipReason 枚举一一对应） */
export const SKIP_REASON_LABELS: Record<string, string> = {
  missing_name: '缺客户姓名',
  invalid_phone: '电话格式不正确',
  invalid_email: '邮箱格式不正确',
  empty_row: '整行为空',
  unmapped_column: '该列未映射',
}

/** 来源类型 → Tag 配色（REAL/IMPORT/TEST/MANUAL 一眼可辨） */
export const SOURCE_TAG_COLORS: Record<string, string> = {
  REAL: 'blue',
  TEST: 'orange',
  MANUAL: 'green',
}

export const DEDUPE_STATE_LABELS: Record<DedupeState, string> = {
  CLEAN: '正常',
  PENDING_REVIEW: '待人工裁决',
  MERGED: '已合并',
}

// ─────────────── TASK-008：工作台首页（dashboard） ───────────────

/**
 * 客户生命周期状态。★ 取值必须与后端 app/models/customer.py 的 LifecycleStatus 完全一致。
 * V1 里这个字段只由「状态变更接口 + customer_events 事件」驱动，AI 不得自行改。
 */
export type LifecycleStatus =
  | 'NEW'
  | 'CONTACTED_UNREAD'
  | 'READ_NO_REPLY'
  | 'CONTACTED'
  | 'REPLIED'
  | 'ENGAGED'
  | 'QUOTED'
  | 'SILENT'
  | 'REJECTED'
  | 'WON'
  | 'LOST'

/** ★ TASK-021 起按需求 §九 的 8 种状态语义命名（已联系·未读 / 已读·未回复 / 暂时沉默 / 明确拒绝） */
export const LIFECYCLE_LABELS: Record<string, string> = {
  NEW: '未联系',
  CONTACTED_UNREAD: '已联系·未读',
  READ_NO_REPLY: '已读·未回复',
  CONTACTED: '已联系',
  REPLIED: '已回复',
  ENGAGED: '持续沟通',
  QUOTED: '已报价',
  SILENT: '暂时沉默',
  REJECTED: '明确拒绝',
  WON: '已成交',
  LOST: '已失效',
}

/** 状态配色：越靠后越"冷"或越"热"（成交绿、拒绝红、沉默灰） */
export const LIFECYCLE_COLORS: Record<string, string> = {
  NEW: 'default',
  CONTACTED_UNREAD: 'blue',
  READ_NO_REPLY: 'gold',
  CONTACTED: 'blue',
  REPLIED: 'cyan',
  ENGAGED: 'geekblue',
  QUOTED: 'orange',
  SILENT: 'default',
  REJECTED: 'red',
  WON: 'green',
  LOST: 'default',
}

// ─── 获客渠道（§九 的 10 种）TASK-021 ───
// ★ 与 source_type（REAL/TEST/MANUAL，数据可不可信）是**两个维度**，刻意不合并。
export type AcquisitionChannel =
  | 'WEBSITE_ORGANIC'
  | 'WEBSITE_FORM'
  | 'CONTENT_MARKETING'
  | 'SEARCH_DISCOVERY'
  | 'OUTBOUND'
  | 'REFERRAL'
  | 'EXHIBITION'
  | 'CRM_IMPORT'
  | 'EXCEL_IMPORT'
  | 'OTHER'

export const ACQUISITION_CHANNEL_LABELS: Record<string, string> = {
  WEBSITE_ORGANIC: '官网自然访问',
  WEBSITE_FORM: '官网表单',
  CONTENT_MARKETING: '内容宣传',
  SEARCH_DISCOVERY: '搜索发现',
  OUTBOUND: '主动开发',
  REFERRAL: '老客户转介绍',
  EXHIBITION: '展会',
  CRM_IMPORT: 'CRM 导入',
  EXCEL_IMPORT: 'Excel 导入',
  OTHER: '其他',
}

export const ACQUISITION_CHANNEL_OPTIONS = Object.entries(ACQUISITION_CHANNEL_LABELS).map(
  ([value, label]) => ({ value, label }),
)

/** 接管三态（D8 冻结取值，禁止自由文本） */
export type HandoverState = 'AUTO' | 'HUMAN_REQUIRED' | 'HUMAN_ACTIVE'

export const HANDOVER_LABELS: Record<string, string> = {
  AUTO: 'AI 自动',
  HUMAN_REQUIRED: '需人工处理',
  HUMAN_ACTIVE: '人工接管中',
}

/**
 * 今日任务的触发原因。每个取值都对应**一条可复现的查询条件**，
 * 不做「高意向 / 87% 转化」这类没有依据的推断分类。
 */
export type TaskReason =
  | 'HUMAN_REQUIRED'   // handover_state = HUMAN_REQUIRED（闸门判定敏感）
  | 'HUMAN_ACTIVE'     // handover_state = HUMAN_ACTIVE（人工已接管、处理中）
  | 'AI_FAILED'        // 存在 ai_status = FAILED 的消息
  | 'PENDING_REVIEW'   // dedupe_state = PENDING_REVIEW（去重冲突待裁决）
  | 'NEW'              // 本周新增、尚未联系

/** 任务原因 → 中文标签（后端给原因码，前端只做展示映射） */
export const TASK_REASON_LABELS: Record<string, string> = {
  HUMAN_REQUIRED: '需人工处理',
  HUMAN_ACTIVE: '人工接管中',
  AI_FAILED: 'AI 回复失败',
  PENDING_REVIEW: '待人工裁决',
  NEW: '新客户·未联系',
}

export type TaskItem = {
  id: number
  name: string
  company_name: string | null
  source_type: CustomerSourceType
  lifecycle_status: LifecycleStatus
  handover_state: HandoverState
  reason: TaskReason
  /** 该原因的可读说明（后端生成，如「闸门判定敏感，需人工处理」） */
  reason_detail: string
  /** 触发时间：来自真实列（first_seen_at / created_at） */
  occurred_at: string
  /** 证据锚点，可点开核对 */
  evidence_ref: string
}

export type DataConnection = {
  key: string
  label: string
  connected: boolean
  /** 最近一次同步/导入时间；没接入或从未导入时为 null */
  last_sync_at: string | null
  note: string
}

// ─────────────── 工作台首页聚合（与 backend/app/schemas/dashboard.py 一一对应） ───────────────

export type GreetingBlock = {
  salutation: string
  subtitle: string
  timezone: string
}

export type PendingBlock = {
  total: EvidenceValue
  human_required: EvidenceValue
  human_active: EvidenceValue
  ai_failed: EvidenceValue
}

export type KpiBlock = {
  customer_total: EvidenceValue
  customer_new_this_week: EvidenceValue
  /** 进行中的商机数（或回退口径：推进到「已报价」及之后的客户） */
  opportunity_count: EvidenceValue
  /** 赢单商机数（或回退口径：生命周期为「已成交」的客户） */
  won_orders: EvidenceValue
  risk_customers: EvidenceValue
  /** OPPORTUNITY_TABLE = 读商机表；LIFECYCLE_FALLBACK = 商机表为空，用客户生命周期近似 */
  opportunity_source: 'OPPORTUNITY_TABLE' | 'LIFECYCLE_FALLBACK'
  /** 如实说明这两个数字的口径，便于核对 */
  opportunity_note: string
}

export type TaskCount = { reason: TaskReason; label: string; count: number }

export type TaskSection = {
  total: EvidenceValue
  counts: TaskCount[]
  items: TaskItem[]
}

export type RegionSlice = { region: string; count: number }
export type RegionDistribution = { total: EvidenceValue; slices: RegionSlice[] }

export type TrendPoint = { day: string; count: number }
export type GrowthTrend = { days: number; total: EvidenceValue; points: TrendPoint[] }

export type SourceSlice = { source: string; label: string; count: number }
export type SourceBreakdown = { total: EvidenceValue; slices: SourceSlice[] }

export type ActivityItem = {
  occurred_at: string
  customer_id: number
  customer_name: string
  kind: string
  title: string
  detail: string | null
  actor: string
  evidence_ref: string
}
export type RecentActivities = { total: EvidenceValue; items: ActivityItem[] }

export type DataConnectionsBlock = { items: DataConnection[]; connected_count: EvidenceValue }

export type ReportMetric = { key: string; label: string; evidence: EvidenceValue }
export type ReportDigest = {
  available: boolean
  report_id: number | null
  report_type: string | null
  generated_at: string | null
  period_start: string | null
  period_end: string | null
  excluded_test_count: number
  /** ★ 报告里有什么指标就显示什么；V1 只有 customer_total / customer_new */
  metrics: ReportMetric[]
  highlights: string[]
  note: string
}

export type AiCapability = { key: string; label: string }
export type AiAssistantBlock = {
  title: string
  badge: string
  greeting: string
  capabilities: AiCapability[]
  /** 如实声明的能力边界（答不了什么），与问答口径共用一份来源 */
  boundaries: string[]
  /** 硬拦范围（策略闸门在模型之前拦下） */
  blocked_note: string
  input_placeholder: string
}

export type DashboardSummaryResponse = {
  generated_at: string
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
}

// ─── AI 助手问答（Copilot，与「客户详情页起草回复」是两件事） ───

export type AiAskKind =
  | 'CUSTOMER_DETAIL'
  | 'PENDING'
  | 'RISK'
  | 'VISITS'
  | 'CUSTOMERS'
  | 'SOURCES'
  | 'REPORT'
  | 'ACTIVITY'
  | 'DATA'
  | 'CAPABILITY'
  | 'GREETING'
  | 'OUT_OF_SCOPE'
  | 'GENERAL'

/** 回答的依据：facts=有事实；no_data=没有事实（且未调模型）；policy_blocked=闸门拦下 */
export type AiAskBasis =
  | 'llm'
  | 'llm_no_facts'
  | 'no_data'
  | 'policy_blocked'
  | 'out_of_scope'
  | 'capability'
  | 'llm_failed'

export type AiFact = {
  label: string
  /** EvidenceValue 形态（原样来自后端） */
  evidence: EvidenceValue
  source_note: string
}

export type AiAskPolicy = {
  allowed: boolean
  label: string | null
  matched: string | null
  reason: string
  tone: string
}

export type AiAskResponse = {
  question: string
  kind: AiAskKind
  answer: string
  basis: AiAskBasis
  facts: AiFact[]
  policy: AiAskPolicy
  llm_called: boolean
  llm_ok: boolean
  note: string
  /** 本轮话题客户 id；下一轮原样回传即可支持「他什么情况」这类追问 */
  context_customer_id: number | null
}

// ─── 客户工作台（客户详情页的状态 / 下一步 / 风险） ───

export type NextActionCode =
  | 'TAKE_OVER'
  | 'REPLY'
  | 'WAIT_CUSTOMER'
  | 'FOLLOW_UP'
  | 'VERIFY_DUPLICATE'
  | 'RECORD_NOTE'
  | 'NONE'

export type NextAction = {
  code: NextActionCode
  label: string
  /** 为什么是这一步（引用真实字段值） */
  reason: string
  evidence_ref: string
}

export type RiskAlertKind = 'POLICY_BLOCKED' | 'AI_FAILED'
export type RiskLevel = 'NONE' | 'ATTENTION' | 'HIGH'

export type RiskAlert = {
  kind: RiskAlertKind
  level: RiskLevel
  title: string
  detail: string
  occurred_at: string
  evidence_ref: string
}

export type HandoverHistoryItem = {
  id: number
  event_type: string
  from_state: string | null
  to_state: string
  actor_type: string
  reason: string | null
  evidence_ref: string
  created_at: string
}

export type CustomerWorkspaceResponse = {
  customer_id: number
  lifecycle_status: LifecycleStatus
  lifecyle_label: string
  handover_state: HandoverState
  handover_label: string
  /** ★ 统一判据：只有 AUTO 为 true */
  ai_auto_reply_allowed: boolean
  next_action: NextAction
  risks: RiskAlert[]
  risk_total: EvidenceValue
  handover_history: HandoverHistoryItem[]
  note: string
}

// ─── 风险中心（risk_events，D11 冻结表之一） ───

export type RiskEventType = 'POLICY_BLOCKED' | 'AI_FAILED' | 'MANUAL_FLAG'
export type RiskEventLevel = 'LOW' | 'ATTENTION' | 'HIGH'
export type RiskEventStatus = 'OPEN' | 'RESOLVED' | 'DISMISSED'

export type RiskEventItem = {
  id: number
  customer_id: number
  customer_name: string
  event_type: RiskEventType
  level: RiskEventLevel
  status: RiskEventStatus
  source_type: string
  /** 来源唯一标识，如 customer_message:346（幂等键） */
  source_ref: string
  /** 触发摘要（D5：只作摘要，证据锚点用 message_id） */
  trigger_text: string | null
  reason: string | null
  /** 证据锚点（D5：= customer_message:{id}） */
  evidence_ref: string
  resolved_at: string | null
  resolved_note: string | null
  created_at: string
}

export type RiskListResponse = {
  items: RiskEventItem[]
  page: number
  page_size: number
  total: EvidenceValue
}

export type RiskSummaryResponse = {
  total: EvidenceValue
  open_total: EvidenceValue
  high_open: EvidenceValue
  attention_open: EvidenceValue
  resolved_total: EvidenceValue
  by_type: { event_type: string; label: string; count: number }[]
  note: string
}

export type RiskScanResponse = {
  scanned_messages: number
  created: number
  skipped_existing: number
  note: string
}

export const RISK_LEVEL_LABELS: Record<string, string> = {
  LOW: '低',
  ATTENTION: '需关注',
  HIGH: '高风险',
}

export const RISK_STATUS_LABELS: Record<string, string> = {
  OPEN: '待处理',
  RESOLVED: '已处理',
  DISMISSED: '已忽略',
}

export const RISK_TYPE_LABELS: Record<string, string> = {
  POLICY_BLOCKED: '命中敏感策略',
  AI_FAILED: 'AI 回复失败',
  MANUAL_FLAG: '人工标记',
}

// ─── 客户访问追踪（TASK-010，记为 customer_events 的 VISIT 事件） ───

export type VisitItem = {
  event_id: number
  customer_id: number
  customer_name: string
  page: string
  channel: string
  channel_label: string
  referrer: string | null
  note: string | null
  occurred_at: string
  evidence_ref: string
}

export type VisitListResponse = {
  items: VisitItem[]
  page: number
  page_size: number
  total: EvidenceValue
}

export type VisitPageSlice = { page: string; count: number }
export type VisitChannelSlice = { channel: string; label: string; count: number }

export type VisitSummaryResponse = {
  total: EvidenceValue
  today: EvidenceValue
  visited_customers: EvidenceValue
  top_pages: VisitPageSlice[]
  by_channel: VisitChannelSlice[]
  daily: { day: string; count: number }[]
  note: string
}

// ─── 市场研究（TASK-011，字面统计，不调模型） ───

export type CountSlice = {
  key: string
  label: string
  count: number
  /** 占比 0~1；★ 样本量不足时为 null（界面显示「—」，不自己算） */
  share: number | null
}

export type ResearchBoundary = {
  available: string[]
  unavailable: string[]
  reason: string
}

export type ResearchResponse = {
  generated_at: string
  sample_size: EvidenceValue
  sample_sufficient: boolean
  min_sample: number
  region_distribution: CountSlice[]
  channel_distribution: CountSlice[]
  lifecycle_distribution: CountSlice[]
  industry_keywords: CountSlice[]
  top_regions: CountSlice[]
  activity_by_region: CountSlice[]
  message_mix: CountSlice[]
  message_total: EvidenceValue
  demand_signals: CountSlice[]
  boundary: ResearchBoundary
  note: string
}

// ─── 数据分析（TASK-012，趋势 / 漏斗 / 质量，不调模型） ───

/** ★ 与首页 GrowthTrend 的 TrendPoint 区分：那个是客户增长趋势，这个是数据分析页的多指标趋势 */
export type AnalyticsTrendPoint = {
  day: string
  new_customers: number
  events: number
  visits: number
}

export type FunnelStage = {
  status: string
  label: string
  /** 累计口径：达到或超过该阶段的客户数 */
  reached: number
  /** 相对上一级的转化率；★ 分母为 0 或样本不足时为 null（不是 0） */
  conversion_from_prev: number | null
  conversion_from_total: number | null
}

export type QualityIssue = {
  key: string
  label: string
  count: number
  severity: 'HIGH' | 'MEDIUM' | 'LOW'
  hint: string
  evidence_ref: string
}

export type SourceQuality = {
  source_type: string
  label: string
  count: number
  share: number | null
}

export type AnalyticsResponse = {
  generated_at: string
  total: EvidenceValue
  new_this_week: EvidenceValue
  active_customers: EvidenceValue
  trend_days: number
  trend: AnalyticsTrendPoint[]
  funnel: FunnelStage[]
  quality: QualityIssue[]
  quality_ok_total: EvidenceValue
  source_quality: SourceQuality[]
  sample_sufficient: boolean
  note: string
}

// ─── 报告（TASK-007 已有接口，本次补前端页） ───

export type ReportType = 'DAILY' | 'WEEKLY' | 'MONTHLY'

export type ReportSummary = {
  id: number
  report_type: ReportType
  period_start: string
  period_end: string
  timezone: string
  generated_at: string
  source_type: string
  generated_by: string
  /** 本期被排除的 TEST 来源客户条数 */
  excluded_test_count: number
}

export type ReportNarrativeLine = {
  tone: string
  text: string
  metric_key: string
  evidence_ref: string | null
}

export type ReportTrendPoint = {
  day: string
  customers: number
  messages: number
  /** 有值表示这一点是按周聚合的，值是聚合天数 */
  aggregated_days?: number
}

export type ReportDetail = ReportSummary & {
  /** 指标 key -> EvidenceValue（快照，读 content_json，永不重算） */
  metrics: Record<string, EvidenceValue>
  /** 文字描述（TASK-039）。★ 后端按真实数字拼装，与 metrics 同一份来源 */
  narrative: ReportNarrativeLine[]
  /** 本期取不到数据的指标（避免把缺失当成 0） */
  no_data: string[]
  /** 走势序列；超过 31 天按周聚合 */
  trend: ReportTrendPoint[]
}

export type ReportListResponse = {
  items: ReportSummary[]
  page: number
  page_size: number
  total: EvidenceValue
}

export type ReportDrilldownRow = {
  id: number
  /** customer / message / opportunity / project / risk / content */
  kind: string
  title: string
  subtitle: string | null
  occurred_at: string | null
  evidence_ref: string | null
  extra: Record<string, unknown>
}

export type ReportMetricDef = {
  key: string
  label: string
  /** 归属的汇报内容分类（§十八） */
  group: string
  row_kind: string
  period_bounded: boolean
}

export type ReportMetricCatalogResponse = {
  groups: string[]
  metrics: ReportMetricDef[]
  note: string
}

export type ReportDrilldownResponse = {
  metric: string
  metric_label: string
  period_start: string
  period_end: string
  page: number
  page_size: number
  /** ★ 与报告里的数字同一个口径，必须相等 */
  total: EvidenceValue
  evidence_ref: string
  items: ReportDrilldownRow[]
}

/** 指标 key → 展示名。
 * ★ TASK-023 起后端有 15 个指标，前端不再硬编码清单 ——
 *   从 GET /api/reports/metric-catalog 取（本表只作为兜底与旧快照兼容）。 */
export const REPORT_METRIC_LABELS: Record<string, string> = {
  customer_total: '客户总数（REAL）',
  customer_new: '期间新增客户（REAL）',
  customer_with_channel: '已填获客渠道的客户',
  message_sent: '期间沟通记录条数',
  message_read: '期间收到已读回执的消息',
  opportunity_open: '进行中的商机',
  opportunity_new: '期间新建商机',
  opportunity_won: '期间赢单商机',
  opportunity_lost: '期间丢单商机',
  opportunity_created_total: '已录入商机总数',
  project_active: '进行中的项目',
  project_overdue: '已逾期的项目',
  risk_open: '待处理风险',
  risk_high_open: '待处理高风险',
  content_published: '已发布内容',
}

export const REPORT_TYPE_LABELS: Record<string, string> = {
  DAILY: '日报',
  WEEKLY: '周报',
  MONTHLY: '月报',
}

// ─── 商机（TASK-016，D28 解冻新增表） ───

export type OpportunityStage =
  | 'NEW'
  | 'CONTACTED'
  | 'QUALIFIED'
  | 'VALIDATING'
  | 'QUOTED'
  | 'NEGOTIATING'
  | 'WON'
  | 'CLOSED_LOST'

export type OpportunityPriority = 'LOW' | 'MEDIUM' | 'HIGH'

export type OpportunityItem = {
  id: number
  customer_id: number
  customer_name: string
  title: string
  stage: OpportunityStage
  stage_label: string
  priority: OpportunityPriority
  /** 预计金额（业务数字）；未估算 = NO_DATA，不是 0 */
  amount: EvidenceValue
  currency: string
  /** 实际成交金额；未赢单 = NO_DATA */
  won_amount: EvidenceValue
  /** 赢单概率（人工填写，不是模型预测） */
  probability: number | null
  expected_close_date: string | null
  closed_at: string | null
  owner: string | null
  note: string | null
  is_active: boolean
  evidence_ref: string
}

export type OpportunityStageColumn = {
  stage: OpportunityStage
  label: string
  count: number
  amount_total: EvidenceValue
  items: OpportunityItem[]
}

export type OpportunityBoardResponse = {
  columns: OpportunityStageColumn[]
  total: EvidenceValue
  open_total: EvidenceValue
  won_total: EvidenceValue
  pipeline_amount: EvidenceValue
  won_amount_total: EvidenceValue
  note: string
}

export type OpportunityListResponse = {
  items: OpportunityItem[]
  page: number
  page_size: number
  total: EvidenceValue
}

export type OpportunityStageResponse = {
  opportunity: OpportunityItem
  /** false = 阶段本来就等于目标值（no-op，未写任何变更） */
  changed: boolean
  message: string
}

/** 阶段配色：终态用绿/灰，进行中用蓝系 */
export const STAGE_COLORS: Record<string, string> = {
  NEW: '#94A3B8',
  CONTACTED: '#60A5FA',
  QUALIFIED: '#3B82F6',
  VALIDATING: '#8B5CF6',
  QUOTED: '#F59E0B',
  NEGOTIATING: '#EA580C',
  WON: '#16A34A',
  CLOSED_LOST: '#9CA3AF',
}

export const PRIORITY_LABELS: Record<string, string> = {
  LOW: '低',
  MEDIUM: '中',
  HIGH: '高',
}

export const PRIORITY_COLORS: Record<string, string> = {
  LOW: 'default',
  MEDIUM: 'blue',
  HIGH: 'red',
}

// ─── 项目（TASK-017，D28 解冻新增表） ───

export type ProjectStatus =
  | 'PLANNING'
  | 'IN_PROGRESS'
  | 'ON_HOLD'
  | 'DELIVERED'
  | 'CLOSED'
  | 'CANCELLED'

export type ProjectCustomerLink = {
  customer_id: number
  customer_name: string
  role: string | null
  evidence_ref: string
}

export type ProjectItem = {
  id: number
  name: string
  code: string | null
  status: ProjectStatus
  status_label: string
  priority: OpportunityPriority
  /** 进度 0~100；★ 人工填写，null = 未评估（与 0% 不同） */
  progress: number | null
  start_date: string | null
  due_date: string | null
  delivered_at: string | null
  owner: string | null
  description: string | null
  customer_count: number
  customers: ProjectCustomerLink[]
  is_overdue: boolean
  is_active: boolean
  evidence_ref: string
}

export type ProjectStatusColumn = {
  status: ProjectStatus
  label: string
  count: number
  items: ProjectItem[]
}

export type ProjectBoardResponse = {
  columns: ProjectStatusColumn[]
  total: EvidenceValue
  active_total: EvidenceValue
  overdue_total: EvidenceValue
  avg_progress: EvidenceValue
  note: string
}

export type ProjectListResponse = {
  items: ProjectItem[]
  page: number
  page_size: number
  total: EvidenceValue
}

export type ProjectStatusResponse = {
  project: ProjectItem
  changed: boolean
  message: string
}

export const PROJECT_STATUS_COLORS: Record<string, string> = {
  PLANNING: '#94A3B8',
  IN_PROGRESS: '#3B82F6',
  ON_HOLD: '#F59E0B',
  DELIVERED: '#16A34A',
  CLOSED: '#6B7280',
  CANCELLED: '#9CA3AF',
}

export const PROJECT_STATUS_LABELS: Record<string, string> = {
  PLANNING: '筹备中',
  IN_PROGRESS: '进行中',
  ON_HOLD: '已暂停',
  DELIVERED: '已交付',
  CLOSED: '已结项',
  CANCELLED: '已取消',
}

// ─── 营销与内容（TASK-018，D28 解冻新增表） ───

export type ContentType =
  | 'ARTICLE'
  | 'CASE_STUDY'
  | 'WHITEPAPER'
  | 'VIDEO'
  | 'EVENT'
  | 'LANDING_PAGE'
  | 'OTHER'

export type ContentStatus = 'DRAFT' | 'REVIEW' | 'PUBLISHED' | 'UNPUBLISHED' | 'ARCHIVED'
export type ContentChannel = 'WEBSITE' | 'WECHAT' | 'ZHIHU' | 'VIDEO_CHANNEL' | 'OFFLINE' | 'OTHER'

export type ContentItem = {
  id: number
  title: string
  content_type: ContentType
  type_label: string
  status: ContentStatus
  status_label: string
  channel: ContentChannel
  channel_label: string
  published_on: string | null
  /** 浏览量（匿名聚合计数）；未统计 = NO_DATA，不是 0 */
  view_count: EvidenceValue
  author: string | null
  url: string | null
  summary: string | null
  is_active: boolean
  evidence_ref: string
}

export type ContentListResponse = {
  items: ContentItem[]
  page: number
  page_size: number
  total: EvidenceValue
}

export type ContentSummaryResponse = {
  total: EvidenceValue
  published_total: EvidenceValue
  draft_total: EvidenceValue
  by_type: { key: string; label: string; count: number }[]
  by_channel: { key: string; label: string; count: number }[]
  total_views: EvidenceValue
  top_contents: ContentItem[]
  note: string
}

export type ContentStatusResponse = {
  content: ContentItem
  changed: boolean
  message: string
}

export const CONTENT_STATUS_COLORS: Record<string, string> = {
  DRAFT: '#94A3B8',
  REVIEW: '#F59E0B',
  PUBLISHED: '#16A34A',
  UNPUBLISHED: '#9CA3AF',
  ARCHIVED: '#6B7280',
}

export const CONTENT_TYPE_OPTIONS: { value: ContentType; label: string }[] = [
  { value: 'ARTICLE', label: '文章' },
  { value: 'CASE_STUDY', label: '客户案例' },
  { value: 'WHITEPAPER', label: '白皮书' },
  { value: 'VIDEO', label: '视频' },
  { value: 'EVENT', label: '活动' },
  { value: 'LANDING_PAGE', label: '落地页' },
  { value: 'OTHER', label: '其他' },
]

export const CONTENT_CHANNEL_OPTIONS: { value: ContentChannel; label: string }[] = [
  { value: 'WEBSITE', label: '官网' },
  { value: 'WECHAT', label: '公众号' },
  { value: 'ZHIHU', label: '知乎' },
  { value: 'VIDEO_CHANNEL', label: '视频号' },
  { value: 'OFFLINE', label: '线下活动' },
  { value: 'OTHER', label: '其他' },
]

// ─── 认证（TASK-019，规格见 docs/AUTH_SPEC.md） ───

export type UserRole = 'ADMIN' | 'MEMBER'

/** 账号公开信息。★ 后端契约里没有 password_hash，前端也拿不到。 */
export type UserItem = {
  id: number
  username: string
  display_name: string
  role: UserRole
  is_active: boolean
  last_login_at: string | null
  created_at: string
  /** ── TASK-040 实时在线状态（按心跳与操作自动推算）── */
  avatar_url?: string | null
  presence?: PresenceStatus
  presence_label?: string
  /** 状态依据；只说观测到的事实 */
  presence_reason?: string
  last_active_at?: string | null
}

export type LoginResponse = {
  access_token: string
  token_type: string
  /** 令牌过期时间；到期需重新登录（不做 refresh token） */
  expires_at: string
  user: UserItem
}

export type UserListResponse = {
  items: UserItem[]
  total: EvidenceValue
}

// ─── 数据连接（TASK-024，需求 §二十） ───

export type ConnectorKind =
  | 'EXCEL_CSV'
  | 'MYSQL'
  | 'POSTGRESQL'
  | 'CRM'
  | 'ERP'
  | 'WECHAT_WORK'
  | 'WEBSITE'
  | 'API'
  | 'WEBHOOK'

export type ConnectionStatus = 'UNCONFIGURED' | 'CONFIGURED' | 'OK' | 'ERROR' | 'DISABLED'

export type DataConnectionItem = {
  id: number
  kind: ConnectorKind
  kind_label: string
  name: string
  status: ConnectionStatus
  status_label: string
  /** 说明这个连接器能做什么、V1 做到哪一步 */
  note: string
  /** 非敏感配置回显（host/port/database/user…），**不含密码** */
  config: Record<string, unknown>
  /** ★ 只有"是否已配置凭据"，永远不返回凭据内容 */
  has_credential: boolean
  scope_note: string | null
  permission_note: string | null
  last_sync_at: string | null
  last_test_at: string | null
  /** 最近一次真实失败原因 */
  last_error: string | null
  /** V1 是否真的实现了这个连接器 */
  supported: boolean
}

export type DataConnectionListResponse = {
  items: DataConnectionItem[]
  total: EvidenceValue
}

export type ConnectionTestResponse = {
  connection: DataConnectionItem
  ok: boolean
  message: string
  elapsed_ms: number
}

export type SyncRecordItem = {
  id: number
  connection_id: number
  connection_name: string
  trigger: string
  trigger_label: string
  result: string
  result_label: string
  rows_total: number
  rows_created: number
  rows_skipped: number
  import_batch_id: number | null
  message: string | null
  started_at: string
  finished_at: string | null
}

export type SyncRecordListResponse = {
  items: SyncRecordItem[]
  total: EvidenceValue
}

export type ConnectionSummaryResponse = {
  total: EvidenceValue
  ok_total: EvidenceValue
  unconfigured_total: EvidenceValue
  error_total: EvidenceValue
  supported_kinds: string[]
  framework_only_kinds: string[]
  note: string
}

export const CONNECTION_STATUS_COLORS: Record<string, string> = {
  UNCONFIGURED: 'default',
  CONFIGURED: 'blue',
  OK: 'green',
  ERROR: 'red',
  DISABLED: 'default',
}

export const CONNECTOR_KIND_OPTIONS: { value: ConnectorKind; label: string }[] = [
  { value: 'EXCEL_CSV', label: 'Excel / CSV' },
  { value: 'MYSQL', label: 'MySQL' },
  { value: 'POSTGRESQL', label: 'PostgreSQL' },
  { value: 'CRM', label: 'CRM' },
  { value: 'ERP', label: 'ERP' },
  { value: 'WECHAT_WORK', label: '企业微信' },
  { value: 'WEBSITE', label: '官网' },
  { value: 'API', label: 'API 接口' },
  { value: 'WEBHOOK', label: 'Webhook' },
]

// ─── 线索研究 / 获客（TASK-025，需求 §十七） ───

export type ResearchTargetType = 'PROSPECT' | 'CUSTOMER'
export type DraftStatus = 'NONE' | 'GENERATED' | 'CONFIRMED' | 'REJECTED'
export type ResearchSourceType =
  | 'COMPANY_SITE'
  | 'NEWS'
  | 'JOB_POSTING'
  | 'INDUSTRY_SITE'
  | 'PRODUCT_INFO'
  | 'OTHER'

/** 〔公开事实〕★ quote 是原文逐字片段，服务端已回验它在原文里存在 */
export type ResearchFact = {
  statement: string
  quote: string
}

/** 〔AI 推断〕★ 与事实分开返回，且必须给出依据 */
export type ResearchInference = {
  statement: string
  basis: string
}

export type ResearchDraft = {
  channel: string
  content: string
  note: string
}

export type ProspectResearchItem = {
  id: number
  target_type: ResearchTargetType
  target_name: string
  customer_id: number | null
  customer_name: string | null
  source_type: ResearchSourceType
  source_type_label: string
  source_url: string | null
  /** ★ 研究要点（要查证的问题）；可由 AI 起草，与原文（证据）分开存 */
  focus: string | null
  source_length: number
  facts: ResearchFact[]
  inferences: ResearchInference[]
  summary: string | null
  draft_status: DraftStatus
  draft_status_label: string
  drafts: ResearchDraft[]
  confirmed_by: string | null
  confirmed_at: string | null
  reject_reason: string | null
  llm_called: boolean
  llm_error: string | null
  created_at: string
  updated_at: string
}

export type ProspectResearchDetail = ProspectResearchItem & {
  /** 详情才有原文（列表不返回，避免接口体积过大） */
  source_text: string
}

/** POST /api/researches 返回的是详情 */
export type ResearchCreateResponse = ProspectResearchDetail

export type ResearchExtractResponse = {
  research: ProspectResearchDetail
  facts_count: number
  inferences_count: number
  /** ★ 因「引用在原文里找不到」被剔除的条目数（防幻觉机制生效次数） */
  rejected_count: number
  note: string
}

export type ResearchListResponse = {
  items: ProspectResearchItem[]
  page: number
  page_size: number
  total: EvidenceValue
}

export type ResearchSummaryResponse = {
  total: EvidenceValue
  pending_confirm_total: EvidenceValue
  confirmed_total: EvidenceValue
  rejected_total: EvidenceValue
  note: string
}

// ─── 知识库（TASK-026，需求 §二十二；不用向量库，见 DECISIONS D43） ───

export type KnowledgeDocType =
  | 'PRODUCT'
  | 'MARKET'
  | 'USER'
  | 'COMPETITOR'
  | 'BUSINESS'
  | 'OTHER'

export type KnowledgeSourceType = 'PASTED' | 'UPLOAD' | 'MANUAL'

export type KnowledgeChunkItem = {
  id: number
  seq: number
  content: string
  /** ★ content 必须精确等于原文的 [char_start:char_end]（可回溯，已断言） */
  char_start: number
  char_end: number
  /** = knowledge_chunk:{id} */
  evidence_ref: string
}

export type KnowledgeDocumentItem = {
  id: number
  title: string
  doc_type: KnowledgeDocType
  doc_type_label: string
  source_type: KnowledgeSourceType
  source_type_label: string
  /** 出处说明（仅记录，系统不会访问它） */
  source_note: string | null
  tags: string[]
  effective_from: string | null
  /** 失效日；null = 长期有效 */
  effective_to: string | null
  /** 是否已过失效日（过期资料默认不参与检索） */
  is_expired: boolean
  /** ★ 研究要点（要查证的问题）；可由 AI 起草，与原文（证据）分开存 */
  focus: string | null
  source_length: number
  chunk_count: number
  created_at: string
  updated_at: string
}

export type KnowledgeDocumentDetail = KnowledgeDocumentItem & {
  source_text: string
  chunks: KnowledgeChunkItem[]
}

export type KnowledgeListResponse = {
  items: KnowledgeDocumentItem[]
  page: number
  page_size: number
  total: EvidenceValue
}

export type KnowledgeSearchHit = {
  chunk_id: number
  document_id: number
  document_title: string
  doc_type: KnowledgeDocType
  doc_type_label: string
  seq: number
  content: string
  /** 相关度分值（命中词数 + 整串命中加权） */
  score: number
  /** ★ 命中了哪些词 —— 让"为什么选它"可解释 */
  matched_terms: string[]
  evidence_ref: string
}

export type KnowledgeSearchResponse = {
  query: string
  /** 查询被切成的检索词（中文取 2-gram） */
  terms: string[]
  hits: KnowledgeSearchHit[]
  total: EvidenceValue
  note: string
}

export type KnowledgeAnalyzeResponse = {
  question: string
  analysis: string
  /** 〔参考资料〕背景知识，★ 不是本公司业务事实 */
  knowledge_used: KnowledgeSearchHit[]
  /** 〔真实业务数据〕系统查库所得 */
  business_data: { key: string; label: string; evidence: EvidenceValue }[]
  from_knowledge: string[]
  from_business_data: string[]
  gaps: string[]
  llm_called: boolean
  llm_error: string | null
  note: string
}

export type KnowledgeSummaryResponse = {
  total: EvidenceValue
  chunk_total: EvidenceValue
  expired_total: EvidenceValue
  by_type: { key: string; label: string; count: number }[]
  note: string
}

export const KNOWLEDGE_DOC_TYPE_OPTIONS: { value: KnowledgeDocType; label: string }[] = [
  { value: 'PRODUCT', label: '产品资料' },
  { value: 'MARKET', label: '市场资料' },
  { value: 'USER', label: '用户资料' },
  { value: 'COMPETITOR', label: '竞品资料' },
  { value: 'BUSINESS', label: '商业资料' },
  { value: 'OTHER', label: '其他' },
]

// ─── 方案（TASK-030，需求 §八 输出中心） ───

export type ProposalKind =
  | 'SOLUTION'
  | 'QUOTATION_NOTE'
  | 'SERVICE_PLAN'
  | 'COOPERATION'
  | 'OTHER'

export type ProposalStatus = 'NONE' | 'GENERATED' | 'CONFIRMED' | 'REJECTED'

/** 方案的一节。★ basis 说明依据什么，便于核对是否编造 */
export type ProposalSection = {
  heading: string
  body: string
  basis: string
}

/** 生成时冻结的知识片段引用（§十九 可追溯） */
export type FrozenKnowledgeHit = {
  evidence_ref: string
  document_title: string
  seq: number
  content: string
  score: number
}

/** 生成时冻结的输入快照 */
export type ProposalInputs = {
  frozen_at: string | null
  knowledge_hits: FrozenKnowledgeHit[]
  business_data: Record<string, string>
  requirement: string | null
  missing_info: string[]
}

export type ProposalItem = {
  id: number
  title: string
  kind: ProposalKind
  kind_label: string
  status: ProposalStatus
  status_label: string
  customer_id: number | null
  customer_name: string | null
  opportunity_id: number | null
  opportunity_title: string | null
  target_name: string | null
  requirement: string | null
  section_count: number
  confirmed_by: string | null
  confirmed_at: string | null
  reject_reason: string | null
  llm_called: boolean
  llm_error: string | null
  created_at: string
  updated_at: string
}

export type ProposalDetail = ProposalItem & {
  content: ProposalSection[]
  /** 生成时冻结的输入（资料引用 + 业务数据） */
  inputs: ProposalInputs | null
}

export type ProposalListResponse = {
  items: ProposalItem[]
  page: number
  page_size: number
  total: EvidenceValue
}

export type ProposalSummaryResponse = {
  total: EvidenceValue
  pending_confirm_total: EvidenceValue
  confirmed_total: EvidenceValue
  rejected_total: EvidenceValue
  note: string
}

export type ProposalGenerateResponse = {
  proposal: ProposalDetail
  section_count: number
  knowledge_hit_count: number
  missing_info: string[]
  note: string
}

export type ProposalCandidatesResponse = {
  customers: { id: number; name: string; company_name: string | null }[]
  opportunities: { id: number; title: string; customer_id: number | null; stage: string }[]
}

export const PROPOSAL_KIND_OPTIONS: { value: ProposalKind; label: string }[] = [
  { value: 'SOLUTION', label: '解决方案' },
  { value: 'QUOTATION_NOTE', label: '报价说明' },
  { value: 'SERVICE_PLAN', label: '服务方案' },
  { value: 'COOPERATION', label: '合作建议' },
  { value: 'OTHER', label: '其他' },
]

// ─── 商业洞察（TASK-031，需求 §八 AI 能力） ───

export type InsightFinding = {
  key: string
  title: string
  finding: string
  value: number | null
  unit: string
  severity: 'info' | 'attention' | 'warning' | string
  /** ★ 判定规则 —— 让人看懂为什么给出这条洞察 */
  rule: string
  evidence_ref: string
}

export type MissingCapability = {
  key: string
  title: string
  why_missing: string
  would_need: string
}

export type BusinessInsightResponse = {
  findings: InsightFinding[]
  missing: MissingCapability[]
  coverage: {
    customers: number
    opportunities_open: number
    projects_active: number
    risks_open: number
    contents: number
  }
  findings_total: EvidenceValue
  note: string
}

// ─── 系统与安全设置（TASK-032，需求 §八 系统管理） ───

export type SecurityPolicyItem = {
  key: string
  label: string
  value: string
  /** ★ 这个值来自哪里（便于核对） */
  source: string
  note: string | null
  level: string
}

export type SecurityCheckItem = {
  key: string
  label: string
  passed: boolean
  detail: string
}

export type CurrentSessionInfo = {
  username: string
  display_name: string
  role: string
  token_expires_at: string | null
  remaining_hours: number | null
  last_login_at: string | null
}

export type SecuritySettingsResponse = {
  policies: SecurityPolicyItem[]
  checks: SecurityCheckItem[]
  session: CurrentSessionInfo
  note: string
}

export type DependencyStatus = {
  key: string
  label: string
  configured: boolean
  detail: string
  impact: string
}

export type SystemSettingsResponse = {
  app: {
    name: string
    version: string
    env: string
    host: string
    port: number
    api_prefix: string
    timezone: string
  }
  database: {
    dialect: string | null
    version: string | null
    connected: boolean
    error: string | null
    host: string
    port: number
    database: string
    user: string
    /** ★ 只报是否配置，不回显口令 */
    password_configured: boolean
    echo: boolean
  }
  dependencies: DependencyStatus[]
  table_counts: { table: string; label: string; count: number | null }[]
  note: string
}

// ─── 个人中心（TASK-037） ───

export type PresenceStatus = 'ONLINE' | 'BUSY' | 'OFFLINE'
export type ThemePreference = 'SYSTEM' | 'LIGHT' | 'DARK'

export type ProfileMe = {
  id: number
  username: string
  /** ★ 个人中心不允许改登录名（改登录凭据要走管理员） */
  display_name: string
  role: string
  role_label: string
  is_active: boolean
  avatar_url: string | null
  presence: PresenceStatus
  presence_label: string
  /** ★ 状态依据；如「页面开着，但已 7 分钟没有操作」——只说观测到的事实 */
  presence_reason: string
  /** 是否来自手动覆盖（覆盖有到期时间，过期自动恢复推算） */
  presence_is_override: boolean
  presence_override_until: string | null
  /** 最近一次真实操作时间 */
  last_active_at: string | null
  theme: ThemePreference
  email: string | null
  phone: string | null
  bio: string | null
  last_login_at: string | null
  created_at: string
}

export type AvatarLimits = {
  max_bytes: number
  max_mb: number
  allowed_types: string[]
  note: string
}

export type AiProviderItem = {
  kind: string
  label: string
  /** 请求协议：OPENAI_COMPATIBLE / ANTHROPIC / GEMINI */
  protocol: string
  docs: string
  base_url: string
  model: string
  is_active: boolean
  /** ★ 是否已配置 Key；永不返回 key 内容 */
  has_key: boolean
  last_test_at: string | null
  last_test_ok: boolean | null
  last_error: string | null
}

export type AiProviderListResponse = {
  items: AiProviderItem[]
  active_kind: string | null
  /** DATABASE = 界面里配的；ENV = 回退 .env；NONE = 都没有 */
  active_source: string
  current_model: string | null
  note: string
}

export type AiProviderTestResponse = {
  provider: AiProviderItem
  ok: boolean
  message: string
  elapsed_ms: number
}

export const PRESENCE_OPTIONS: { value: PresenceStatus; label: string; color: string }[] = [
  { value: 'ONLINE', label: '在线', color: '#16A34A' },
  { value: 'BUSY', label: '忙碌', color: '#F59E0B' },
  { value: 'OFFLINE', label: '离线', color: '#9CA3AF' },
]

export const PROTOCOL_LABELS: Record<string, string> = {
  OPENAI_COMPATIBLE: 'OpenAI 兼容',
  ANTHROPIC: 'Anthropic',
  GEMINI: 'Google Gemini',
}

/** AI 生成正文的响应（TASK-038）。★ 生成结果不直接入库，先预览 */
export type ContentGenerateResponse = {
  title: string
  summary: string
  body: string
  char_count: number
  /** 目标区间描述，如 "500~700 字"。★ 是写作目标，不是上限 */
  target_range: string
  used_knowledge: boolean
  sources: { evidence_ref: string; document_title: string; seq: number; score: number }[]
  note: string
}

export type ContentLengthPreset = { value: string; label: string; range: string }

// ─── 实时在线状态（TASK-040） ───

export type PresenceRule = { status: PresenceStatus; label: string; rule: string }

export type PresenceRulesResponse = {
  heartbeat_interval_seconds: number
  offline_after_seconds: number
  idle_after_seconds: number
  override_choices_minutes: number[]
  rules: PresenceRule[]
  note: string
}

export type HeartbeatResponse = {
  status: PresenceStatus
  label: string
  reason: string
  is_override: boolean
  seconds_since_active: number | null
  server_time: string
}
