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
import { useConnectionStore } from '@/stores/connection'
import { getSessions, getSessionMessages, renameSession as apiRenameSession, deleteSession as apiDeleteSession } from '@/api/session'
import type { Session, Message } from '@/types/chat'
import type { FindingSeverity } from '@/types/report'
import type { ThinkingEvent, TokenEvent, ToolCallEvent, ToolResultEvent, SqlEvent, ResultEvent, TextEvent, ErrorEvent, DoneEvent } from '@/types/chat'

// ─── 内部消息类型（Store 展示用） ───

/** 消息展示类型 */
export type StoreMessageType =
  | 'text'        // 用户文字 / 纯文本回复
  | 'thinking'    // Agent 推理过程（可折叠）
  | 'waiting'     // Agent 等待占位（动态点动画 + 计时器）
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

  // thinking
  /** Agent 运行唯一 ID（Agent 架构升级） */
  agentRunId?: string
  /** 当前是 Agent 第几轮 ReAct 迭代 */
  iteration?: number
  /** 推理类型：planning | observing | concluding | error_recovery */
  reasoningType?: string

  // tool_call / tool_result
  tool?: string
  toolArgs?: Record<string, unknown>
  displayText?: string
  durationMs?: number
  stepStatus?: 'running' | 'done' | 'error'
  /** 安全护栏检查结果（Agent 架构升级后新增） */
  safetyChecksPassed?: boolean

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
  /** 决策链路摘要（Agent 架构升级后新增） */
  traceSummary?: {
    tools_called: string[]
    total_duration_ms: number
  }

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

  // ─── 流式 Token 累加状态（优化方案） ───

  /** 当前累积的流式文本 */
  const streamingText = ref('')
  /** 正在流式更新的消息 ID */
  const streamingMessageId = ref<string | null>(null)
  /** 是否已收到首个 SSE 事件（防止重复移除 waiting） */
  const hasReceivedFirstEvent = ref(false)

  // ─── F2: 会话管理状态 ───

  /** 会话列表加载状态 */
  const sessionsLoading = ref(false)
  /** 消息历史加载状态 */
  const messagesLoading = ref(false)
  /** 会话列表加载错误 */
  const sessionFetchError = ref<string | null>(null)

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

  // ─── 优化方案：等待消息 & 流式 Token 辅助方法 ───

  /** 移除 waiting 占位消息（仅首次调用生效） */
  function removeWaitingMessage(): void {
    if (hasReceivedFirstEvent.value) return
    hasReceivedFirstEvent.value = true
    const idx = messages.value.findIndex((m) => m.type === 'waiting')
    if (idx >= 0) messages.value.splice(idx, 1)
  }

  /** 创建或更新流式 text 消息（token 逐字累加，打字机效果） */
  function updateStreamingMessage(content: string): void {
    if (streamingMessageId.value) {
      const msg = messages.value.find((m) => m.id === streamingMessageId.value)
      if (msg && msg.type === 'text') {
        msg.content = content
        // 触发响应式更新
        messages.value = [...messages.value]
      }
    } else {
      const msg: StoreMessage = {
        id: nextMsgId(),
        sessionId: currentSessionId.value ?? '',
        role: 'assistant',
        type: 'text',  // 优化方案：用户可见的流式文本，由 MarkdownRenderer 渲染
        content: content,
        createdAt: new Date().toISOString(),
      }
      messages.value.push(msg)
      streamingMessageId.value = msg.id
    }
  }

  /** 封存当前流式消息（重置 streaming 状态，保留已累积内容） */
  function sealStreamingMessage(): void {
    streamingMessageId.value = null
    streamingText.value = ''
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

    // 2. 重置流式状态 + 插入 waiting 占位消息
    streamingText.value = ''
    streamingMessageId.value = null
    hasReceivedFirstEvent.value = false
    messages.value.push({
      id: nextMsgId(),
      sessionId: currentSessionId.value ?? '',
      role: 'assistant',
      type: 'waiting',
      content: '',
      createdAt: new Date().toISOString(),
    })

    // 3. 设置流式状态
    isStreaming.value = true
    sseError.value = null

    // 4. 获取连接密码并启动 SSE 连接
    const connectionStore = useConnectionStore()
    const password = connectionStore.getPassword(connectionId)

    sse.connect(
      '/api/chat/stream',
      {
        connection_id: connectionId,
        message: text,
        mode,
        session_id: currentSessionId.value,
        password,  // AGENTS.md §安全与合规红线：密码仅存于内存，每次请求传入
      },
      {
        // ── token（优化方案：LLM 逐 token 流式输出） ──
        onToken: (event: TokenEvent) => {
          removeWaitingMessage()
          streamingText.value += event.content
          updateStreamingMessage(streamingText.value)
        },

        // ── thinking（优化方案：不覆盖 token 流式内容，仅更新 metadata） ──
        onThinking: (event: ThinkingEvent) => {
          removeWaitingMessage()
          if (streamingMessageId.value) {
            // token 流已在输出文本 → 仅更新 metadata，不替换 content
            const msg = messages.value.find((m) => m.id === streamingMessageId.value)
            if (msg && msg.type === 'text') {
              msg.durationMs = event.duration_ms
              msg.reasoningType = event.reasoning_type
              msg.agentRunId = event.agent_run_id
              msg.iteration = event.iteration
              messages.value = [...messages.value]
            }
            // 注意：thinking 不再 sealStreamingMessage()，
            // token 流可能继续追加到同一 text 消息
          } else {
            // 快响应降级：token 流未触发时，推一条 text 消息
            const msg: StoreMessage = {
              id: nextMsgId(),
              sessionId: currentSessionId.value ?? '',
              role: 'assistant',
              type: 'text',
              content: event.content,
              agentRunId: event.agent_run_id,
              iteration: event.iteration,
              reasoningType: event.reasoning_type,
              durationMs: event.duration_ms,
              createdAt: new Date().toISOString(),
            }
            messages.value.push(msg)
            sealStreamingMessage()
          }
        },

        // ── tool_call ──
        onToolCall: (event: ToolCallEvent) => {
          removeWaitingMessage()
          sealStreamingMessage()
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
            agentRunId: event.agent_run_id,
            iteration: event.iteration,
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
            toolCall.safetyChecksPassed = event.safety_checks_passed
            toolCall.agentRunId = event.agent_run_id
            toolCall.iteration = event.iteration
          }
        },

        // ── sql ──
        onSql: (event: SqlEvent) => {
          removeWaitingMessage()
          sealStreamingMessage()
          messages.value.push({
            id: nextMsgId(),
            sessionId: currentSessionId.value ?? '',
            role: 'assistant',
            type: 'sql',
            content: event.content,
            sqlContent: event.content,
            auditStatus: event.audit_status,
            isReadonly: event.is_readonly,
            agentRunId: event.agent_run_id,
            iteration: event.iteration,
            createdAt: new Date().toISOString(),
          })
        },

        // ── result（优化方案：不推新消息，token 流已渲染完整文本） ──
        onResult: (_event: ResultEvent) => {
          removeWaitingMessage()
          sealStreamingMessage()
          // result 事件已从后端移除（Task 1），此处仅保留兼容：
          // 不再 push 新消息，用户通过 token 通道看到打字机效果
        },

        // ── text ──
        onText: (event: TextEvent) => {
          removeWaitingMessage()
          sealStreamingMessage()
          messages.value.push({
            id: nextMsgId(),
            sessionId: currentSessionId.value ?? '',
            role: 'assistant',
            type: 'text',
            content: event.content,
            createdAt: new Date().toISOString(),
          })
        },

        // ── error ──
        onError: (event: ErrorEvent) => {
          removeWaitingMessage()
          sealStreamingMessage()
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
          removeWaitingMessage()
          sealStreamingMessage()
          isStreaming.value = false
          currentSessionId.value = event.session_id
          // 更新最后一条助手消息的 token 计数及 Agent 汇总信息
          const last = lastAssistantMessage.value
          if (last) {
            last.tokensUsed = event.tokens_used
            last.agentRunId = last.agentRunId || event.agent_run_id
            last.traceSummary = event.trace_summary
          }
          // 消息发送完成后刷新会话列表（F2）
          fetchSessions(connectionId)
        },

        // ── disconnect ──
        onDisconnect: (reason: string) => {
          removeWaitingMessage()
          sealStreamingMessage()
          isStreaming.value = false
          sseError.value = reason
        },

        // ── timeout ──
        onTimeout: () => {
          removeWaitingMessage()
          sealStreamingMessage()
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

    // 清理 waiting 占位和流式累加状态
    removeWaitingMessage()
    sealStreamingMessage()

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
  //  F2: 会话管理 Actions
  // ═══════════════════════════════════════════════════

  /**
   * 将 API 返回的 MessageResponse 转换为 Store 内部 StoreMessage
   *
   * 历史消息不含 thinking/tool_call/tool_result 等 ReAct 过程细节，
   * 仅展示最终结果（text/sql/result/error/diagnosis）。
   */
  function messageResponseToStoreMessage(msg: Message): StoreMessage {
    const base = {
      id: msg.id,
      sessionId: msg.session_id,
      role: msg.role as 'user' | 'assistant',
      content: msg.content,
      createdAt: msg.created_at,
    }

    // 用户消息始终为 text 类型
    if (msg.role === 'user') {
      return { ...base, type: 'text' as const }
    }

    // 根据消息类型映射
    switch (msg.message_type) {
      case 'sql':
        return {
          ...base,
          type: 'sql' as const,
          sqlContent: msg.sql_generated ?? undefined,
        }
      case 'diagnosis':
        return { ...base, type: 'diagnosis' as const }
      default:
        break
    }

    // 根据内容特征推断
    if (msg.error_info) {
      return {
        ...base,
        type: 'error' as const,
        errorCode: msg.error_info.error_code ?? undefined,
        userMessage: msg.error_info.user_message ?? undefined,
      }
    }

    if (msg.result_preview) {
      const preview = msg.result_preview as {
        columns?: string[]
        rows?: string[][]
        total_rows?: number
      } | null
      return {
        ...base,
        type: 'result' as const,
        summary: msg.content,
        dataPreview: preview
          ? {
              columns: preview.columns ?? [],
              rows: preview.rows ?? [],
              total_rows: preview.total_rows ?? 0,
            }
          : null,
        totalRows: preview?.total_rows ?? 0,
      }
    }

    // 默认视为纯文本
    return { ...base, type: 'text' as const }
  }

  /**
   * 从 API 加载会话列表
   *
   * @param connectionId 可选，按连接 ID 筛选
   */
  async function fetchSessions(connectionId?: string): Promise<void> {
    sessionsLoading.value = true
    sessionFetchError.value = null

    try {
      const response = await getSessions({
        connection_id: connectionId,
        pageSize: 50,
      })
      sessions.value = response.items
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '加载会话列表失败'
      sessionFetchError.value = msg
      console.warn('[chatStore] fetchSessions 失败:', msg)
    } finally {
      sessionsLoading.value = false
    }
  }

  /**
   * 切换到指定会话并加载其消息历史
   *
   * @param sessionId 目标会话 ID
   */
  async function switchToSession(sessionId: string): Promise<void> {
    if (isStreaming.value) {
      await cancelStreaming()
    }

    messagesLoading.value = true

    try {
      const response = await getSessionMessages(sessionId, { pageSize: 200 })
      // API 按 created_at DESC 排序，反转后为 ASC
      const reversed = [...response.items].reverse()
      currentSessionId.value = sessionId
      messages.value = reversed.map(messageResponseToStoreMessage)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '加载消息历史失败'
      console.warn('[chatStore] switchToSession 失败:', msg)
      // 不清空当前消息，保持已有内容可见
    } finally {
      messagesLoading.value = false
    }
  }

  /**
   * 开始新对话
   *
   * 清空消息列表并重置当前会话 ID。
   */
  function startNewSession(): void {
    messages.value = []
    currentSessionId.value = null
  }

  /**
   * 重命名会话标题
   *
   * @param sessionId 会话 ID
   * @param title 新标题
   */
  async function renameSession(sessionId: string, title: string): Promise<void> {
    // 保存旧的标题用于回滚
    const oldSession = sessions.value.find((s) => s.id === sessionId)
    const oldTitle = oldSession?.title

    // 乐观更新
    if (oldSession) {
      oldSession.title = title
    }

    try {
      await apiRenameSession(sessionId, title)
    } catch {
      // 失败时回滚
      if (oldSession && oldTitle !== undefined) {
        oldSession.title = oldTitle
      }
      console.warn('[chatStore] renameSession 失败')
    }
  }

  /**
   * 删除会话
   *
   * 如果删除的是当前会话，自动切换到新对话状态。
   *
   * @param sessionId 会话 ID
   */
  async function deleteSession(sessionId: string): Promise<void> {
    try {
      await apiDeleteSession(sessionId)
      // 从本地列表中移除
      sessions.value = sessions.value.filter((s) => s.id !== sessionId)

      // 如果删除的是当前会话，切换到新对话
      if (currentSessionId.value === sessionId) {
        startNewSession()
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '删除会话失败'
      console.warn('[chatStore] deleteSession 失败:', msg)
    }
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

    // 优化方案：流式 Token 累加状态
    streamingText,
    streamingMessageId,
    hasReceivedFirstEvent,

    // F2: 会话管理状态
    sessionsLoading,
    messagesLoading,
    sessionFetchError,

    // getters
    currentSession,
    lastAssistantMessage,

    // actions
    sendMessage,
    cancelStreaming,
    clearMessages,
    setInputMode,

    // F2: 会话管理 actions
    fetchSessions,
    switchToSession,
    startNewSession,
    renameSession,
    deleteSession,
  }
})
