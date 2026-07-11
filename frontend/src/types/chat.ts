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

// ─── 分页列表响应（F1） ───

export interface SessionListResponse {
  items: Session[]
  total: number
  page: number
  pageSize: number
}

export interface MessageListResponse {
  items: Message[]
  total: number
  page: number
  pageSize: number
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

/** SSE 事件：LLM Token 流式输出块（逐 token/块推送） */
export interface TokenEvent {
  type: 'token'
  /** Token 阶段：thinking（思考过程）/ answer（最终回答） */
  stage: 'thinking' | 'answer'
  /** 增量文本片段 */
  content: string
  /** Agent 运行唯一 ID */
  agent_run_id?: string
  /** 当前是 Agent 第几轮 ReAct 迭代 */
  iteration?: number
}

/** SSE 事件：Agent 推理过程（已废弃，由 reasoning 事件替代） */
export interface ThinkingEvent {
  type: 'thinking'
  content: string
  /** Agent 运行唯一 ID（Agent 架构升级后新增） */
  agent_run_id?: string
  /** 当前是 Agent 第几轮 ReAct 迭代（Agent 架构升级后新增） */
  iteration?: number
  /** 推理类型：planning | observing | concluding | error_recovery */
  reasoning_type?: string
  /** 本次 LLM 推理耗时（ms），后端计算，解决前端计时器从 0 开始的问题 */
  duration_ms?: number
}

/** SSE 事件：模型深度推理内容（reasoning_content → 思考面板） */
export interface ReasoningEvent {
  type: 'reasoning'
  /** 模型内部推理文本片段（流式） */
  content: string
  /** Agent 运行唯一 ID */
  agent_run_id?: string
}

/** SSE 事件：Agent 调用工具 */
export interface ToolCallEvent {
  type: 'tool_call'
  tool: string
  args: Record<string, unknown>
  display: string
  /** LLM 分配的 tool_call 唯一 ID，用于并行执行时前后端关联 */
  tool_call_id?: string
  /** Agent 运行唯一 ID（Agent 架构升级后新增） */
  agent_run_id?: string
  /** 当前是 Agent 第几轮 ReAct 迭代（Agent 架构升级后新增） */
  iteration?: number
}

/** SSE 事件：工具返回 */
export interface ToolResultEvent {
  type: 'tool_result'
  tool: string
  summary: string
  duration_ms: number
  /** 对应的 tool_call 唯一 ID，用于并行执行时精确匹配 */
  tool_call_id?: string
  /** Agent 运行唯一 ID（Agent 架构升级后新增） */
  agent_run_id?: string
  /** 当前是 Agent 第几轮 ReAct 迭代（Agent 架构升级后新增） */
  iteration?: number
  /** 安全护栏检查结果（Agent 架构升级后新增） */
  safety_checks_passed?: boolean
}

/** SSE 事件：SQL 生成 */
export interface SqlEvent {
  type: 'sql'
  content: string
  audit_status: string
  is_readonly: boolean
  /** Agent 运行唯一 ID（Agent 架构升级后新增） */
  agent_run_id?: string
  /** 当前是 Agent 第几轮 ReAct 迭代（Agent 架构升级后新增） */
  iteration?: number
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

/** SSE 事件：纯文本回复（通用对话回显） */
export interface TextEvent {
  type: 'text'
  content: string
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
  /** Agent 运行唯一 ID（Agent 架构升级后新增） */
  agent_run_id?: string
  /** Agent 总共推理了几轮（Agent 架构升级后新增） */
  total_iterations?: number
  /** 决策链路摘要（Agent 架构升级后新增，用于调试面板） */
  trace_summary?: {
    tools_called: string[]
    total_duration_ms: number
  }
  /** 整个 SSE 流的总耗时（毫秒），从请求开始到 done 事件的时间差 */
  total_duration_ms?: number
}

/** SSE 事件：阶段切换（thinking ↔ answer 双向切换） */
export interface StageChangeEvent {
  type: 'stage_change'
  /** 切换到的阶段：thinking | answer */
  stage: 'thinking' | 'answer'
  /** Agent 运行唯一 ID */
  agent_run_id?: string
}

/** SSE 事件联合类型 */
export type SSEEvent =
  | ThinkingEvent
  | ReasoningEvent
  | TokenEvent
  | ToolCallEvent
  | ToolResultEvent
  | SqlEvent
  | ResultEvent
  | TextEvent
  | ErrorEvent
  | DoneEvent
  | StageChangeEvent

/** SSE 事件回调映射 */
export interface SSEEventCallbacks {
  onThinking?: (event: ThinkingEvent) => void
  onReasoning?: (event: ReasoningEvent) => void
  onToken?: (event: TokenEvent) => void
  onToolCall?: (event: ToolCallEvent) => void
  onToolResult?: (event: ToolResultEvent) => void
  onSql?: (event: SqlEvent) => void
  onResult?: (event: ResultEvent) => void
  onText?: (event: TextEvent) => void
  onError?: (event: ErrorEvent) => void
  onStageChange?: (event: StageChangeEvent) => void
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
  password?: string  // 数据库密码，仅存于内存，不落盘（AGENTS.md §安全红线）
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
