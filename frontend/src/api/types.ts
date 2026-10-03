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
