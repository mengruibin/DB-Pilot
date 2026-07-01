/**
 * 健康巡检与诊断报告相关类型定义
 *
 * 依据 api-contract §2.4 DiagnosisResult、§2.5 HealthReport
 * 及 §1.4 健康巡检 SSE 事件、§1.5 故障排查 SSE 事件
 */

// ─── 诊断 ───

export type DiagnosisType =
  | 'slow_query_analysis'
  | 'explain_plan'
  | 'index_suggestion'
  | 'sql_rewrite'
  | 'config_review'

export type FindingSeverity = 'error' | 'warning' | 'info'

export interface Finding {
  severity: FindingSeverity
  category: string
  title: string
  detail: string
  suggestion: string
  is_destructive: boolean
  estimated_improvement: string
  reference: string
}

export interface DiagnosisResult {
  type: DiagnosisType
  target_sql: string
  findings: Finding[]
  generated_at: string
}

// ─── 健康巡检 ───

export type ReportStatus = 'running' | 'completed' | 'cancelled' | 'partial'

export type CheckItemStatus = 'pass' | 'warning' | 'error' | 'skipped'

export interface SeverityCounts {
  error: number
  warning: number
  pass: number
  skipped: number
}

export interface HealthReportItem {
  name: string
  status: CheckItemStatus
  value: string
  threshold: string | null
  suggestion: string | null
  is_destructive: boolean
}

export interface HealthReportCategory {
  name: string
  items: HealthReportItem[]
}

export interface HealthReport {
  id: string
  connection_id: string
  status: ReportStatus
  score: number
  generated_at: string
  duration_sec: number
  severity_counts: SeverityCounts
  categories: HealthReportCategory[]
}

// ─── 故障排查 ───

export type IssueType =
  | 'deadlock'
  | 'connection_flood'
  | 'replication_lag'
  | 'slow_performance'
  | 'disk_full'
  | 'unknown'
  | 'auto'

export type TroubleshootStepStatus =
  | 'pass'
  | 'warning'
  | 'error'
  | 'skip'
  | 'running'

export interface TroubleshootStep {
  order: number
  tool: string
  display: string
  status: TroubleshootStepStatus
  summary: string
  detail: Record<string, unknown> | null
  duration_ms: number
}

export interface TroubleshootResult {
  session_id: string
  issue_type: IssueType
  steps: TroubleshootStep[]
  conclusion: string
  suggestion: string
  suggestion_is_destructive: boolean
  severity: string
}

/** 故障排查请求体 */
export interface TroubleshootRequest {
  issue_type: IssueType
  context?: string
}

// ─── 健康巡检 SSE 事件 ───

/** SSE 事件：检查项进度 */
export interface CheckProgressEvent {
  type: 'check_progress'
  current: number
  total: number
  item: string
  status: string
}

/** SSE 事件：检查项警告 */
export interface CheckWarningEvent {
  type: 'check_warning'
  current: number
  total: number
  item: string
  status: string
  value: string
  threshold: string
  suggestion: string
}

/** SSE 事件：检查项错误 */
export interface CheckErrorEvent {
  type: 'check_error'
  current: number
  total: number
  item: string
  status: string
  value: string
  threshold: string
  suggestion: string
}

/** SSE 事件：巡检结果 */
export interface HealthResultEvent {
  type: 'health_result'
  report_id: string
  score: number
  summary: string
  severity_counts: SeverityCounts
}

/** 健康巡检 SSE 事件联合类型 */
export type HealthCheckSSEEvent =
  | CheckProgressEvent
  | CheckWarningEvent
  | CheckErrorEvent
  | HealthResultEvent

// ─── 故障排查 SSE 事件 ───

/** SSE 事件：诊断结论 */
export interface DiagnosisSSEEvent {
  type: 'diagnosis'
  conclusion: string
  severity: string
  suggestion: string
  suggestion_is_destructive: boolean
}
