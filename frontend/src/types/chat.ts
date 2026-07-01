/**
 * 对话与 Agent 交互相关类型定义
 *
 * 依据 api-contract §2.2 Session/Message、§2.3 QueryResult
 * 及 §1.2 SSE 事件类型契约
 */

// ─── 会话 ───

export type SessionStatus = 'active' | 'idle' | 'closed'

export interface Session {
  id: string
  connection_id: string
  title: string
  created_at: string
  last_active_at: string
  status: SessionStatus
  message_count: number
  tokens_used_total: number
}

// ─── 消息 ───

export type MessageRole = 'user' | 'assistant' | 'system'

export type MessageType =
  | 'natural_language'
  | 'sql'
  | 'diagnosis'
  | 'troubleshoot'
  | 'health_check'

export interface MessageErrorInfo {
  error_code: string
  user_message: string
}

export interface Message {
  id: string
  session_id: string
  role: MessageRole
  content: string
  message_type: MessageType
  sql_generated: string | null
  sql_executed: string | null
  result_preview: object | null
  error_info: MessageErrorInfo | null
  created_at: string
  tokens_used: number
}

// ─── 查询结果 ───

export interface QueryColumn {
  name: string
  type: string
  is_sensitive: boolean
}

export interface QueryResult {
  columns: QueryColumn[]
  rows: string[][]
  total_rows: number
  returned_rows: number
  execution_time_ms: number
  audit_status: string
  is_readonly: boolean
}

export interface DirectQueryRequest {
  sql: string
  params?: Record<string, unknown>
  max_execution_ms?: number
}

export interface ExplainRequest {
  sql: string
  format?: 'tree' | 'json' | 'traditional'
}

export interface ExplainResponse {
  explain_output: string
  parsed: {
    total_cost_estimate: string
    bottleneck: string
    suggestion: string
    estimated_improvement: string
  }
  format: string
}

// ─── SSE 事件类型 ───

/** SSE 事件：Agent 推理过程 */
export interface ThinkingEvent {
  type: 'thinking'
  content: string
}

/** SSE 事件：Agent 调用工具 */
export interface ToolCallEvent {
  type: 'tool_call'
  tool: string
  args: Record<string, unknown>
  display: string
}

/** SSE 事件：工具返回 */
export interface ToolResultEvent {
  type: 'tool_result'
  tool: string
  summary: string
  duration_ms: number
}

/** SSE 事件：SQL 生成 */
export interface SqlEvent {
  type: 'sql'
  content: string
  audit_status: string
  is_readonly: boolean
}

/** SSE 事件：查询结果 */
export interface ResultEvent {
  type: 'result'
  summary: string
  data_preview: {
    columns: string[]
    rows: string[][]
    total_rows: number
  }
  duration_ms: number
}

/** SSE 事件：错误 */
export interface ErrorEvent {
  type: 'error'
  error_code: string
  user_message: string
  severity: string
}

/** SSE 事件：流结束 */
export interface DoneEvent {
  type: 'done'
  session_id: string
  tokens_used: number
}

/** SSE 事件联合类型 */
export type SSEEvent =
  | ThinkingEvent
  | ToolCallEvent
  | ToolResultEvent
  | SqlEvent
  | ResultEvent
  | ErrorEvent
  | DoneEvent

/** SSE 事件回调映射 */
export interface SSEEventCallbacks {
  onThinking?: (event: ThinkingEvent) => void
  onToolCall?: (event: ToolCallEvent) => void
  onToolResult?: (event: ToolResultEvent) => void
  onSql?: (event: SqlEvent) => void
  onResult?: (event: ResultEvent) => void
  onError?: (event: ErrorEvent) => void
  onDone?: (event: DoneEvent) => void
  onDisconnect?: (reason: string) => void
  onTimeout?: () => void
}

/** SSE 流请求体（POST /api/chat/stream） */
export interface StreamChatRequest {
  connection_id: string
  message: string
  mode: 'natural_language' | 'sql_editor'
  session_id: string | null
  context?: {
    selected_table?: string
    user_role?: string
  }
}

/** 取消请求体（POST /api/chat/cancel） */
export interface CancelRequest {
  session_id: string
}

/** 慢查询记录 */
export interface SlowQuery {
  id: string
  sql_text: string
  query_time_sec: number
  lock_time_sec: number
  rows_examined: number
  rows_sent: number
  executed_at: string
  user: string | null
  host: string | null
}

/** 慢查询列表响应 */
export interface SlowQueryListResponse {
  items: SlowQuery[]
  total: number
  page: number
  pageSize: number
  warning?: string
}
