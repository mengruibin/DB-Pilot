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
import type { ThinkingEvent, ReasoningEvent, TokenEvent, ToolCallEvent, ToolResultEvent, SqlEvent, ResultEvent, TextEvent, ErrorEvent, DoneEvent, StageChangeEvent, ConfirmRequiredEvent } from '@/types/chat'

// ─── 内部消息类型（Store 展示用） ───

/** 消息展示类型 */
export type StoreMessageType =
  | 'text'        // 用户文字 / 纯文本回复
  | 'thinking'    // Agent 推理过程（可折叠，已废弃）
  | 'reasoning'   // 模型深度推理内容（reasoning_content → 思考面板）
  | 'waiting'     // Agent 等待占位（动态点动画）
  | 'tool_call'    // 工具调用步骤卡片
  | 'tool_result'  // 工具返回结果
  | 'sql'         // SQL 代码块
  | 'result'      // 查询结果表格
  | 'error'       // 错误卡片
  | 'diagnosis'   // 诊断结果卡片
  | 'thinking_group'  // 思考过程折叠分组

/** 思考过程分组数据 */
export interface ThinkingGroupData {
  /** 分组内的步骤消息（thinking text + tool_call + tool_result + sql） */
  steps: StoreMessage[]
  /** 工具调用步骤数量 */
  stepCount: number
  /** 整个 SSE 流总耗时（毫秒） */
  totalDurationMs: number
}

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

  // 通用
  /** Token 阶段：thinking（思考过程）/ answer（最终回答），用于 SSE 分组判断 */
  stage?: 'thinking' | 'answer'

  // thinking
  /** Agent 运行唯一 ID（Agent 架构升级） */
  agentRunId?: string
  /** 当前是 Agent 第几轮 ReAct 迭代 */
  iteration?: number
  /** 推理类型：planning | observing | concluding | error_recovery */
  reasoningType?: string

  // tool_call / tool_result
  tool?: string
  toolCallId?: string
  toolArgs?: Record<string, unknown>
  displayText?: string
  durationMs?: number
  stepStatus?: 'running' | 'done' | 'error' | 'waiting_approval'
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

  // thinking_group
  /** 思考过程分组数据（仅 type='thinking_group' 时有效） */
  thinkingGroup?: ThinkingGroupData
}

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

  /** SSE 连接状态 */
  const sseConnected = ref(false)

  /** SSE 错误信息 */
  const sseError = ref<string | null>(null)

  // ─── 流式 Token 累加状态（优化方案） ───

  /** 当前累积的流式文本 */
  const streamingText = ref('')
  /** 正在流式更新的消息 ID */
  const streamingMessageId = ref<string | null>(null)
  /** 当前累积的推理文本 */
  const streamingReasoningText = ref('')
  /** 正在流式更新的推理消息 ID */
  const streamingReasoningMessageId = ref<string | null>(null)
  /** 是否已收到首个 SSE 事件（防止重复移除 waiting） */
  const hasReceivedFirstEvent = ref(false)

  /** 当前 SSE 流阶段：thinking（思考过程）/ answer（最终回答） */
  const currentStage = ref<'thinking' | 'answer'>('thinking')
  /** 当前 turn 的起始索引（最后一条 user 消息的位置），供 postProcessTurn 分组使用 */
  const turnStartIndex = ref(-1)

  // ─── F2: 会话管理状态 ───

  /** 会话列表加载状态 */
  const sessionsLoading = ref(false)
  /** 消息历史加载状态 */
  const messagesLoading = ref(false)
  /** 会话列表加载错误 */
  const sessionFetchError = ref<string | null>(null)

  // 内部：消息计数器（生成 ID）
  let msgCounter = 0
  // 内部：tool_call_id → tool_call message 快速查找表（并行安全匹配用）
  const toolCallMap = new Map<string, StoreMessage>()
  /** 当前待用户确认的危险操作（非空时显示确认对话框） */
  const pendingConfirm = ref<{
    writes: Array<{
      tool_call_id: string
      tool: string
      category: import('@/types/chat').ConfirmCategory
      description: string
      details: Record<string, unknown>
    }>
  } | null>(null)

  /** SSE 流是否因写操作确认而中断（等待用户决策） */
  const isAwaitingConfirmation = computed(() => pendingConfirm.value !== null)

  // 内部：写操作确认超时计时器
  let confirmTimeoutId: ReturnType<typeof setTimeout> | null = null
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

  /** 按 tool_call_id 查找 tool_call 消息（优先 Map O(1)，回退数组扫描） */
  function findToolCallById(toolCallId: string): StoreMessage | undefined {
    const fromMap = toolCallMap.get(toolCallId)
    if (fromMap) return fromMap
    return messages.value.find(
      (m) => m.type === 'tool_call' && m.toolCallId === toolCallId
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
      const idx = messages.value.findIndex((m) => m.id === streamingMessageId.value)
      if (idx >= 0 && messages.value[idx].type === 'text') {
        // 创建新对象触发完整的响应式链路（确保 MarkdownRenderer computed 正确重算）
        messages.value = [
          ...messages.value.slice(0, idx),
          { ...messages.value[idx], content },
          ...messages.value.slice(idx + 1),
        ]
      }
    } else {
      const msg: StoreMessage = {
        id: nextMsgId(),
        sessionId: currentSessionId.value ?? '',
        role: 'assistant',
        type: 'text',  // 优化方案：用户可见的流式文本，由 MarkdownRenderer 渲染
        stage: currentStage.value,  // 标记当前阶段，供 postProcessTurn 分组使用
        content: content,
        createdAt: new Date().toISOString(),
      }
      messages.value.push(msg)
      streamingMessageId.value = msg.id
    }
  }

  /** 创建或更新流式 reasoning 消息（推理文本逐 chunk 累加） */
  function updateStreamingReasoningMessage(content: string): void {
    if (streamingReasoningMessageId.value) {
      const idx = messages.value.findIndex((m) => m.id === streamingReasoningMessageId.value)
      if (idx >= 0 && messages.value[idx].type === 'reasoning') {
        messages.value = [
          ...messages.value.slice(0, idx),
          { ...messages.value[idx], content },
          ...messages.value.slice(idx + 1),
        ]
      }
    } else {
      const msg: StoreMessage = {
        id: nextMsgId(),
        sessionId: currentSessionId.value ?? '',
        role: 'assistant',
        type: 'reasoning',
        content: content,
        createdAt: new Date().toISOString(),
      }
      messages.value.push(msg)
      streamingReasoningMessageId.value = msg.id
    }
  }

  /** 封存当前流式消息（重置 streaming 状态，保留已累积内容） */
  function sealStreamingMessage(): void {
    streamingMessageId.value = null
    streamingText.value = ''
  }

  /** 封存当前推理流式消息 */
  function sealStreamingReasoningMessage(): void {
    streamingReasoningMessageId.value = null
    streamingReasoningText.value = ''
  }

  /**
   * 后处理当前 turn：将思考过程消息折叠为 thinking_group
   *
   * 分组启发式规则：
   * 1. 从 turnStartIndex（最后一条 user 消息）之后开始扫描
   * 2. 查找 stage === 'answer' 的 text 消息 → 视为 final answer，保留在顶层
   * 3. final answer 之前的 text/tool_call/tool_result/sql → 归入 thinking_group
   * 4. error/diagnosis 等异常类型保留在顶层（不隐藏）
   * 5. 无工具调用的纯文本回答跳过分组
   *
   * @param totalDurationMs 后端返回的 SSE 流总耗时
   */
  function postProcessTurn(totalDurationMs: number): void {
    if (turnStartIndex.value < 0) return

    const turnMsgs = messages.value.slice(turnStartIndex.value + 1)
    if (turnMsgs.length === 0) { turnStartIndex.value = -1; return }

    // 检查是否有工具调用 — 纯文本回答无需分组
    const hasTools = turnMsgs.some(
      (m) => m.type === 'tool_call' || m.type === 'tool_result',
    )
    if (!hasTools) { turnStartIndex.value = -1; return }

    // 找到最后一条工具交互（tool_call / tool_result）的位置作为分界点。
    // 工具交互及之前的思考内容 → thinking_group；之后的 text(answer) → 最终回答。
    let lastToolIdx = -1
    for (let i = turnMsgs.length - 1; i >= 0; i--) {
      if (turnMsgs[i].type === 'tool_call' || turnMsgs[i].type === 'tool_result') {
        lastToolIdx = i
        break
      }
    }

    // 没有工具交互消息（不太可能，但做防御）
    if (lastToolIdx < 0) { turnStartIndex.value = -1; return }

    const groupingTypes = new Set(['text', 'tool_call', 'tool_result', 'sql', 'reasoning'])
    // 检查分界点及之前是否有可分组的内容
    const hasGroupable = turnMsgs.slice(0, lastToolIdx + 1).some((m) => groupingTypes.has(m.type))
    if (!hasGroupable) { turnStartIndex.value = -1; return }

    // 分离：thinking steps（lastToolIdx 及之前）vs keep（lastToolIdx 之后）
    const thinkingSteps: StoreMessage[] = []
    const keepMessages: StoreMessage[] = []

    for (let i = 0; i < turnMsgs.length; i++) {
      if (i > lastToolIdx) {
        // 工具交互之后的消息 — 保留在顶层（最终回答）
        keepMessages.push(turnMsgs[i])
      } else if (groupingTypes.has(turnMsgs[i].type)) {
        // thinking 内容 — 归入分组
        thinkingSteps.push(turnMsgs[i])
      } else {
        // error / diagnosis 等 — 保留在顶层（不隐藏）
        keepMessages.push(turnMsgs[i])
      }
    }

    if (thinkingSteps.length === 0) { turnStartIndex.value = -1; return }

    // 统计工具调用步数
    const toolCallCount = thinkingSteps.filter((m) => m.type === 'tool_call').length

    // 构建 thinking_group 消息
    const groupMsg: StoreMessage = {
      id: `thinking_group_${Date.now()}`,
      sessionId: currentSessionId.value ?? '',
      role: 'assistant',
      type: 'thinking_group',
      content: '',
      createdAt: new Date().toISOString(),
      thinkingGroup: {
        steps: thinkingSteps,
        stepCount: toolCallCount,
        totalDurationMs,
      },
    }

    // 重建 messages 数组：保留 turn 之前的消息 + user 消息 + thinking_group + 保留消息
    const before = messages.value.slice(0, turnStartIndex.value + 1)
    messages.value = [...before, groupMsg, ...keepMessages]

    // 重置状态
    turnStartIndex.value = -1
    currentStage.value = 'thinking'
  }

  // ═══════════════════════════════════════════════════
  //  Actions — SSE 流式连接
  // ═══════════════════════════════════════════════════

  /**
   * 发送消息并启动 SSE 流。
   *
   * @param connectionId 目标连接 ID
   * @param text         用户消息文本
   * @param resume       中断恢复请求（可选，非空时 text 可为空）
   */
  function sendMessage(connectionId: string, text: string, resume?: { approved_tool_call_ids: string[]; denied_tool_call_ids: string[] }): void {
    if (isStreaming.value) return
    if (!connectionId) return

    // 恢复请求时跳过消息管理（不添加用户消息、不重置状态、不加 waiting 占位）
    if (resume) {
      isStreaming.value = true
      sseError.value = null
    } else {
      // 1. 添加用户消息
      addUserMessage(text)

      // 重置双区渲染状态，记录 turn 起始索引供 postProcessTurn 分组使用
      currentStage.value = 'thinking'
      turnStartIndex.value = messages.value.length - 1
      // 清空上一轮的 tool_call 快速查找表
      toolCallMap.clear()

      // 2. 重置流式状态 + 插入 waiting 占位消息
      streamingText.value = ''
      streamingMessageId.value = null
      streamingReasoningText.value = ''
      streamingReasoningMessageId.value = null
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
    }

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
        mode: 'natural_language',
        session_id: currentSessionId.value,
        password,  // AGENTS.md §安全与合规红线：密码仅存于内存，每次请求传入
        ...(resume ? { resume } : {}),
      },
      {
        // ── token（优化方案：LLM 逐 token 流式输出） ──
        onToken: (event: TokenEvent) => {
          removeWaitingMessage()
          sealStreamingReasoningMessage()  // reasoning 结束，切换到 token 流
          streamingText.value += event.content
          updateStreamingMessage(streamingText.value)
        },

        // ── reasoning（模型深度推理 → 思考面板，逐 chunk 累加到同一条消息） ──
        onReasoning: (event: ReasoningEvent) => {
          removeWaitingMessage()
          sealStreamingMessage()  // 推理与 token 文本互斥：token 流存在时先封存
          streamingReasoningText.value += event.content
          updateStreamingReasoningMessage(streamingReasoningText.value)
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
          sealStreamingReasoningMessage()
          messages.value.push({
            id: nextMsgId(),
            sessionId: currentSessionId.value ?? '',
            role: 'assistant',
            type: 'tool_call',
            content: event.display || event.tool,
            tool: event.tool,
            toolCallId: event.tool_call_id,
            toolArgs: event.args,
            displayText: event.display,
            stepStatus: 'running',
            agentRunId: event.agent_run_id,
            iteration: event.iteration,
            createdAt: new Date().toISOString(),
          })
          // 注册到快速查找表（Vue 响应式代理）
          if (event.tool_call_id) {
            toolCallMap.set(event.tool_call_id, messages.value[messages.value.length - 1])
          }
        },

        // ── tool_result ──
        onToolResult: (event: ToolResultEvent) => {
          // 1. 找到对应的 tool_call 消息，标记为 done
          // 优先按 tool_call_id 精确匹配（并行安全），回退到按 tool 名匹配（向后兼容）
          let toolCall: StoreMessage | undefined
          if (event.tool_call_id) {
            toolCall = findToolCallById(event.tool_call_id)
          }
          if (!toolCall && event.tool) {
            toolCall = messages.value.find(
              (m) => m.type === 'tool_call' && m.tool === event.tool && m.stepStatus === 'running'
            )
          }
          if (toolCall) {
            toolCall.stepStatus = 'done'
            toolCall.durationMs = event.duration_ms
            toolCall.agentRunId = event.agent_run_id
            toolCall.iteration = event.iteration
          }

          // 2. 推送独立的 tool_result 消息（「工具返回结果」折叠块）
          removeWaitingMessage()
          sealStreamingMessage()
          sealStreamingReasoningMessage()
          messages.value.push({
            id: nextMsgId(),
            sessionId: currentSessionId.value ?? '',
            role: 'assistant',
            type: 'tool_result',
            content: event.summary,
            tool: event.tool,
            toolCallId: event.tool_call_id,
            durationMs: event.duration_ms,
            safetyChecksPassed: event.safety_checks_passed,
            agentRunId: event.agent_run_id,
            iteration: event.iteration,
            createdAt: new Date().toISOString(),
          })
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

        // ── stage_change（乐观渲染收编：thinking → answer 双向切换） ──
        onStageChange: (_event: StageChangeEvent) => {
          const newStage = (_event as any).stage as 'thinking' | 'answer'
          if (newStage === currentStage.value) return  // 幂等保护

          if (newStage === 'answer') {
            // 收编：只将「最后一条 tool_call/tool_result 之后」的 text(stage=thinking)
            // 改为 answer。工具调用之前的思考文本保持 thinking 阶段。
            // 向前扫描，遇到 tool_call/tool_result 时停止收编（之前的文本保持 thinking）。
            let hitToolInteraction = false
            for (let i = messages.value.length - 1; i >= 0; i--) {
              const m = messages.value[i]
              if (m.role !== 'assistant') break
              if (m.type === 'tool_call' || m.type === 'tool_result') {
                hitToolInteraction = true
                continue  // 工具交互本身不改变 stage，但之后的文本不再收编
              }
              if (hitToolInteraction) continue  // 工具交互之前的文本保持 thinking
              if (m.type === 'text' && m.stage === 'thinking') {
                m.stage = 'answer'
              }
            }
            messages.value = [...messages.value]  // 触发响应式
            sealStreamingMessage()
            currentStage.value = 'answer'
          } else {
            // 从 answer → thinking（降级：兼容极少数纠偏场景）
            sealStreamingMessage()
            currentStage.value = 'thinking'
          }
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
          sealStreamingReasoningMessage()
          isStreaming.value = false
          currentSessionId.value = event.session_id

          // 将思考过程折叠为 thinking_group（需在更新 lastAssistantMessage 之前）
          postProcessTurn(event.total_duration_ms ?? 0)

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

        // ── confirm_required（写操作确认） ──
        onConfirmRequired: (event: ConfirmRequiredEvent) => {
          pendingConfirm.value = { writes: event.writes }
          // 保存会话 ID 供恢复请求使用（中断时不触发 onDone，session_id 无法更新）
          currentSessionId.value = event.session_id

          // 60s 超时自动拒绝
          confirmTimeoutId = setTimeout(() => {
            if (pendingConfirm.value) {
              const allDenied = pendingConfirm.value.writes.map(w => w.tool_call_id)
              respondToConfirm({
                approved_tool_call_ids: [],
                denied_tool_call_ids: allDenied,
              })
            }
          }, 60_000)
        },
      }
    )
  }

  /**
   * 响应用户对写操作的确认/拒绝决策，发起恢复请求
   */
  async function respondToConfirm(decision: {
    approved_tool_call_ids: string[]
    denied_tool_call_ids: string[]
  }): Promise<void> {
    if (!pendingConfirm.value) return
    pendingConfirm.value = null
    if (confirmTimeoutId) {
      clearTimeout(confirmTimeoutId)
      confirmTimeoutId = null
    }

    // 临时放行 isStreaming 检查，通过 sendMessage 发起恢复 SSE 流
    const connectionStore = useConnectionStore()
    isStreaming.value = false
    sendMessage(connectionStore.activeId ?? '', '', decision)
  }

  /**
   * 取消当前正在执行的 Agent 操作
   * 发送 POST /api/chat/cancel 并关闭 SSE 连接
   */
  async function cancelStreaming(): Promise<void> {
    // 如果正在等待用户确认写操作，先自动拒绝
    if (pendingConfirm.value) {
      const allDenied = pendingConfirm.value.writes.map(w => w.tool_call_id)
      await respondToConfirm({
        approved_tool_call_ids: [],
        denied_tool_call_ids: allDenied,
      })
      return
    }

    if (!isStreaming.value) return

    // 清理 waiting 占位和流式累加状态
    removeWaitingMessage()
    sealStreamingMessage()
    sealStreamingReasoningMessage()

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
    turnStartIndex.value = -1
    currentStage.value = 'thinking'
  }

  // ═══════════════════════════════════════════════════
  //  F2: 会话管理 Actions
  // ═══════════════════════════════════════════════════

  /**
   * 将 API 返回的 MessageResponse 转换为 Store 内部 StoreMessage 列表
   *
   * 当 reasoning_content 存在时，返回 [reasoning_msg, main_msg] 两条消息，
   * 确保历史消息也能展示思考面板中的推理内容。
   */
  function messageResponseToStoreMessage(msg: Message): StoreMessage[] {
    const base = {
      id: msg.id,
      sessionId: msg.session_id,
      role: msg.role as 'user' | 'assistant',
      content: msg.content,
      createdAt: msg.created_at,
    }

    // 用户消息始终为 text 类型
    if (msg.role === 'user') {
      return [{ ...base, type: 'text' as const }]
    }

    // 1) 按原有逻辑构建主体消息
    let main: StoreMessage
    switch (msg.message_type) {
      case 'sql':
        main = {
          ...base,
          type: 'sql' as const,
          sqlContent: msg.sql_generated ?? undefined,
        }
        break
      case 'diagnosis':
        // 历史数据不含 findings 结构化字段，降级为纯文本渲染，
        // 确保诊断结论内容（msg.content）正常展示
        main = { ...base, type: 'text' as const }
        break
      default: {
        if (msg.error_info) {
          main = {
            ...base,
            type: 'error' as const,
            errorCode: msg.error_info.error_code ?? undefined,
            userMessage: msg.error_info.user_message ?? undefined,
          }
        } else if (msg.result_preview) {
          const preview = msg.result_preview as {
            columns?: string[]
            rows?: string[][]
            total_rows?: number
          } | null
          main = {
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
        } else {
          // 默认视为纯文本
          main = { ...base, type: 'text' as const }
        }
      }
    }

    const result: StoreMessage[] = []

    // 2) 有推理内容时在主体消息前插入一条 reasoning 消息
    // 当 thinking_steps 中已包含 reasoning 条目时，跳过整体消息，
    // 由 thinking_steps 中的独立 reasoning 步骤按正确顺序渲染
    const hasReasoningSteps = msg.thinking_steps?.some(
      (s) => s.type === 'reasoning'
    )
    if (msg.reasoning_content && !hasReasoningSteps) {
      result.push({
        ...base,
        type: 'reasoning' as const,
        content: msg.reasoning_content,
      })
    }

    // 3) 重建思考步骤（reasoning / tool_call / tool_result / sql）
    // 使用 `${msg.id}-step-${i}` 唯一 ID，防止 pairToolSteps 的 Set 误去重
    if (msg.thinking_steps?.length) {
      msg.thinking_steps.forEach((step, i) => {
        const stepId = `${msg.id}-step-${i}`
        if (step.type === 'tool_call') {
          result.push({
            ...base,
            id: stepId,
            type: 'tool_call' as const,
            content: step.display || step.tool,
            tool: step.tool,
            toolCallId: step.tool_call_id,
            toolArgs: step.args,
            displayText: step.display,
            stepStatus: 'done' as const,
            agentRunId: step.agent_run_id,
            iteration: step.iteration,
          })
        } else if (step.type === 'tool_result') {
          result.push({
            ...base,
            id: stepId,
            type: 'tool_result' as const,
            content: step.summary,
            tool: step.tool,
            toolCallId: step.tool_call_id,
            durationMs: step.duration_ms,
            safetyChecksPassed: step.safety_checks_passed,
            agentRunId: step.agent_run_id,
            iteration: step.iteration,
          })
        } else if (step.type === 'reasoning') {
          result.push({
            ...base,
            id: stepId,
            type: 'reasoning' as const,
            content: step.content,
            agentRunId: step.agent_run_id,
            iteration: step.iteration,
          })
        } else if (step.type === 'sql') {
          result.push({
            ...base,
            id: stepId,
            type: 'sql' as const,
            content: step.content,
            sqlContent: step.content,
            auditStatus: step.audit_status,
            isReadonly: step.is_readonly,
          })
        }
      })
    }

    // 4) 主体消息（text/sql/result/error/diagnosis）
    result.push(main)

    return result
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
      messages.value = reversed.flatMap(messageResponseToStoreMessage)
      // 切换会话时重置双区渲染状态，防止影响后续流式
      turnStartIndex.value = -1
      currentStage.value = 'thinking'
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
    turnStartIndex.value = -1
    currentStage.value = 'thinking'
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
    sseConnected,
    sseError,

    // 优化方案：流式 Token 累加状态
    streamingText,
    streamingMessageId,
    streamingReasoningText,
    streamingReasoningMessageId,
    hasReceivedFirstEvent,

    // F2: 会话管理状态
    sessionsLoading,
    messagesLoading,
    sessionFetchError,

    // 写操作确认状态
    pendingConfirm,
    isAwaitingConfirmation,

    // getters
    currentSession,
    lastAssistantMessage,

    // actions
    sendMessage,
    respondToConfirm,
    cancelStreaming,
    clearMessages,

    // F2: 会话管理 actions
    fetchSessions,
    switchToSession,
    startNewSession,
    renameSession,
    deleteSession,
  }
})
