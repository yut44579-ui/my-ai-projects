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

export type TimelineEventKind = 'CUSTOMER_CREATED' | 'SOURCE_IMPORT' | 'DEDUPE_PENDING_REVIEW'

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
