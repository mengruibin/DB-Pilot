/**
 * 对话 Store
 *
 * 管理会话列表、消息数组、流式 SSE 状态、输入模式。
 * SSE 事件实时更新消息列表：thinking/tool_call/sql/result/error/done。
 *
 * 依据 api-contract §三 Store 划分（chatStore）
 *     §2.2 Session/Message 实体
 *     §1.2 SSE 事件类型契约
 */
import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { useSSE } from '@/composables/useSSE'
import { cancelChat as apiCancelChat } from '@/api/chat'
import type { Session } from '@/types/chat'
import type { FindingSeverity } from '@/types/report'
import type {
  ThinkingEvent,
  ToolCallEvent,
  ToolResultEvent,
  SqlEvent,
  ResultEvent,
  ErrorEvent,
  DoneEvent,
} from '@/types/chat'

// ─── 内部消息类型（Store 展示用） ───

/** 消息展示类型 */
export type StoreMessageType =
  | 'text'        // 用户文字 / 纯文本回复
  | 'thinking'    // Agent 推理过程（可折叠）
  | 'tool_call'    // 工具调用步骤卡片
  | 'tool_result'  // 工具返回结果
  | 'sql'         // SQL 代码块
  | 'result'      // 查询结果表格
  | 'error'       // 错误卡片
  | 'diagnosis'   // 诊断结果卡片

/** Store 内部消息结构 */
export interface StoreMessage {
  /** 消息唯一 ID */
  id: string
  /** 会话 ID */
  sessionId: string
  /** 角色 */
  role: 'user' | 'assistant'
  /** 显示类型（决定 MessageBubble 渲染组件） */
  type: StoreMessageType
  /** 主显示文本 */
  content: string
  /** 创建时间 ISO */
  createdAt: string

  // ─── 按类型扩展字段 ───

  // thinking: 无额外字段

  // tool_call / tool_result
  tool?: string
  toolArgs?: Record<string, unknown>
  displayText?: string
  durationMs?: number
  stepStatus?: 'running' | 'done' | 'error'

  // sql
  sqlContent?: string
  auditStatus?: string
  isReadonly?: boolean

  // result
  summary?: string
  dataPreview?: {
    columns: string[]
    rows: string[][]
    total_rows: number
  } | null
  totalRows?: number
  executionTimeMs?: number

  // error
  errorCode?: string
  userMessage?: string
  severity?: string

  // done
  tokensUsed?: number

  // diagnosis
  findings?: Array<{
    severity: FindingSeverity
    category: string
    title: string
    detail: string
    suggestion: string
    is_destructive: boolean
    estimated_improvement: string
    reference: string
  }>
  targetSql?: string
}

// ─── 输入模式 ───

export type InputMode = 'natural_language' | 'sql_editor'

const INPUT_MODE_KEY = 'db-pilot:input-mode'

// ─── Store ───

export const useChatStore = defineStore('chat', () => {
  // ═══════════════════════════════════════════════════
  //  State
  // ═══════════════════════════════════════════════════

  /** 当前会话的消息列表 */
  const messages = ref<StoreMessage[]>([])

  /** 会话历史 */
  const sessions = ref<Session[]>([])

  /** 当前会话 ID */
  const currentSessionId = ref<string | null>(null)

  /** 是否正在 SSE 流式接收 */
  const isStreaming = ref(false)

  /** 输入模式（持久化到 localStorage） */
  const inputMode = ref<InputMode>(
    (localStorage.getItem(INPUT_MODE_KEY) as InputMode) || 'natural_language'
  )

  /** SSE 连接状态 */
  const sseConnected = ref(false)

  /** SSE 错误信息 */
  const sseError = ref<string | null>(null)

  // 内部：消息计数器（生成 ID）
  let msgCounter = 0
  // 内部：SSE composable 实例
  const sse = useSSE()

  // ═══════════════════════════════════════════════════
  //  Getters
  // ═══════════════════════════════════════════════════

  /** 当前会话对象 */
  const currentSession = computed(() => {
    return sessions.value.find((s) => s.id === currentSessionId.value) ?? null
  })

  /** 最近一条助手消息（用于 SSE 流式更新） */
  const lastAssistantMessage = computed(() => {
    const filtered = messages.value.filter((m) => m.role === 'assistant')
    return filtered.length > 0 ? filtered[filtered.length - 1] : null
  })

  // ═══════════════════════════════════════════════════
  //  Watchers
  // ═══════════════════════════════════════════════════

  // 同步 SSE 响应式状态
  watch(() => sse.isConnected.value, (v) => { sseConnected.value = v })
  watch(() => sse.isError.value, (v) => { sseError.value = v ? sse.errorMessage.value : null })

  // 持久化 inputMode
  watch(inputMode, (val) => {
    localStorage.setItem(INPUT_MODE_KEY, val)
  })

  // ═══════════════════════════════════════════════════
  //  Actions — 消息管理
  // ═══════════════════════════════════════════════════

  /** 生成唯一消息 ID */
  function nextMsgId(): string {
    return `msg_${Date.now()}_${++msgCounter}`
  }

  /** 添加用户消息 */
  function addUserMessage(text: string): StoreMessage {
    const msg: StoreMessage = {
      id: nextMsgId(),
      sessionId: currentSessionId.value ?? '',
      role: 'user',
      type: 'text',
      content: text,
      createdAt: new Date().toISOString(),
    }
    messages.value.push(msg)
    return msg
  }

  /** 查找最后一条 tool_call（running 状态） */
  function findLastRunningToolCall(): StoreMessage | undefined {
    const reversed = [...messages.value].reverse()
    return reversed.find(
      (m) => m.role === 'assistant' && m.type === 'tool_call' && m.stepStatus === 'running'
    )
  }

  // ═══════════════════════════════════════════════════
  //  Actions — SSE 流式连接
  // ═══════════════════════════════════════════════════

  /**
   * 发送消息并启动 SSE 流
   *
   * @param connectionId 目标连接 ID
   * @param text         用户消息文本
   * @param mode         输入模式
   */
  function sendMessage(connectionId: string, text: string, mode: InputMode): void {
    if (isStreaming.value) return
    if (!connectionId) return

    // 1. 添加用户消息
    addUserMessage(text)

    // 2. 设置流式状态
    isStreaming.value = true
    sseError.value = null

    // 3. 启动 SSE 连接
    sse.connect(
      '/api/chat/stream',
      {
        connection_id: connectionId,
        message: text,
        mode,
        session_id: currentSessionId.value,
      },
      {
        // ── thinking ──
        onThinking: (event: ThinkingEvent) => {
          messages.value.push({
            id: nextMsgId(),
            sessionId: currentSessionId.value ?? '',
            role: 'assistant',
            type: 'thinking',
            content: event.content,
            createdAt: new Date().toISOString(),
          })
        },

        // ── tool_call ──
        onToolCall: (event: ToolCallEvent) => {
          messages.value.push({
            id: nextMsgId(),
            sessionId: currentSessionId.value ?? '',
            role: 'assistant',
            type: 'tool_call',
            content: event.display || event.tool,
            tool: event.tool,
            toolArgs: event.args,
            displayText: event.display,
            stepStatus: 'running',
            createdAt: new Date().toISOString(),
          })
        },

        // ── tool_result ──
        onToolResult: (event: ToolResultEvent) => {
          const toolCall = findLastRunningToolCall()
          if (toolCall && toolCall.tool === event.tool) {
            toolCall.stepStatus = 'done'
            toolCall.durationMs = event.duration_ms
            toolCall.content = event.summary
            toolCall.type = 'tool_result'
          }
        },

        // ── sql ──
        onSql: (event: SqlEvent) => {
          messages.value.push({
            id: nextMsgId(),
            sessionId: currentSessionId.value ?? '',
            role: 'assistant',
            type: 'sql',
            content: event.content,
            sqlContent: event.content,
            auditStatus: event.audit_status,
            isReadonly: event.is_readonly,
            createdAt: new Date().toISOString(),
          })
        },

        // ── result ──
        onResult: (event: ResultEvent) => {
          messages.value.push({
            id: nextMsgId(),
            sessionId: currentSessionId.value ?? '',
            role: 'assistant',
            type: 'result',
            content: event.summary,
            summary: event.summary,
            dataPreview: event.data_preview,
            totalRows: event.data_preview.total_rows,
            executionTimeMs: event.duration_ms,
            createdAt: new Date().toISOString(),
          })
        },

        // ── error ──
        onError: (event: ErrorEvent) => {
          messages.value.push({
            id: nextMsgId(),
            sessionId: currentSessionId.value ?? '',
            role: 'assistant',
            type: 'error',
            content: event.user_message,
            errorCode: event.error_code,
            userMessage: event.user_message,
            severity: event.severity,
            createdAt: new Date().toISOString(),
          })
        },

        // ── done ──
        onDone: (event: DoneEvent) => {
          isStreaming.value = false
          currentSessionId.value = event.session_id
          // 更新最后一条助手消息的 token 计数
          const last = lastAssistantMessage.value
          if (last) {
            last.tokensUsed = event.tokens_used
          }
        },

        // ── disconnect ──
        onDisconnect: (reason: string) => {
          isStreaming.value = false
          sseError.value = reason
        },

        // ── timeout ──
        onTimeout: () => {
          isStreaming.value = false
          sseError.value = '响应超时（120s 无消息）'
        },
      }
    )
  }

  /**
   * 取消当前正在执行的 Agent 操作
   * 发送 POST /api/chat/cancel 并关闭 SSE 连接
   */
  async function cancelStreaming(): Promise<void> {
    if (!isStreaming.value) return

    const sessionId = currentSessionId.value
    try {
      if (sessionId) {
        await apiCancelChat({ session_id: sessionId })
      }
    } catch {
      // 取消失败不影响前端状态重置
    }

    sse.disconnect()
    isStreaming.value = false
  }

  /** 清除当前会话消息 */
  function clearMessages(): void {
    messages.value = []
    currentSessionId.value = null
  }

  /** 设置输入模式 */
  function setInputMode(mode: InputMode): void {
    inputMode.value = mode
  }

  // ═══════════════════════════════════════════════════
  //  Return
  // ═══════════════════════════════════════════════════

  return {
    // state
    messages,
    sessions,
    currentSessionId,
    isStreaming,
    inputMode,
    sseConnected,
    sseError,

    // getters
    currentSession,
    lastAssistantMessage,

    // actions
    sendMessage,
    cancelStreaming,
    clearMessages,
    setInputMode,
  }
})
