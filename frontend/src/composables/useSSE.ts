/**
 * SSE 流式客户端 composable
 *
 * POST + fetch ReadableStream 实现，手动解析 SSE 协议，
 * 7 种事件类型独立回调分发。
 *
 * 依据 api-contract §1.2 POST /api/chat/stream SSE 事件类型契约
 *     frontend AGENTS.md §1 连接中断处理
 *     api-contract T-2 方案 A（POST + fetch ReadableStream）
 */
import { ref, readonly } from 'vue'
import type { SSEEventCallbacks } from '@/types/chat'

/** SSE 无消息超时（毫秒） */
const TIMEOUT_MS = 120_000

export function useSSE() {
  // ─── 响应式状态 ───

  /** 是否已连接 */
  const isConnected = ref(false)

  /** 是否发生错误 */
  const isError = ref(false)

  /** 错误描述 */
  const errorMessage = ref<string | null>(null)

  // ─── 内部状态 ───

  let abortController: AbortController | null = null
  let reader: ReadableStreamDefaultReader<Uint8Array> | null = null
  let timeoutId: ReturnType<typeof setTimeout> | null = null
  let userCallbacks: SSEEventCallbacks | null = null
  let errorSeen = false // 收到 error 事件后忽略后续事件

  // ─── 超时定时器 ───

  /** 刷新超时定时器（每次收到消息时调用） */
  function resetTimeout(): void {
    clearTimeout(timeoutId!)
    timeoutId = setTimeout(() => {
      userCallbacks?.onTimeout?.()
      disconnectInternal()
    }, TIMEOUT_MS)
  }

  /** 清除超时定时器 */
  function clearTimeoutTimer(): void {
    if (timeoutId !== null) {
      clearTimeout(timeoutId)
      timeoutId = null
    }
  }

  // ─── SSE 消息解析 ───

  /**
   * 解析单条 SSE 消息并分发到对应回调
   *
   * SSE 原始格式：
   *   event: message
   *   data: {"type":"thinking","content":"..."}
   *
   * 实际 type 在 JSON data 内，event 行固定为 "message"
   */
  function dispatchSSEEvent(msg: string): void {
    // 收到 error 后忽略后续
    if (errorSeen) return

    let eventField = ''
    let dataField = ''

    // 逐行解析
    const lines = msg.split('\n')
    for (const line of lines) {
      const trimmed = line.trim()
      if (trimmed.startsWith('event:')) {
        eventField = trimmed.slice(6).trim()
      } else if (trimmed.startsWith('data:')) {
        dataField = trimmed.slice(5).trim()
      }
    }

    // 仅处理 event: message（SSE 协议层事件名）
    if (eventField !== 'message' || !dataField) return

    // 解析 JSON payload
    let payload: Record<string, unknown>
    try {
      payload = JSON.parse(dataField)
    } catch {
      console.warn('[SSE] JSON 解析失败:', dataField.slice(0, 120))
      return
    }

    const type = payload.type as string

    // 按 type 分发到对应回调
    switch (type) {
      case 'thinking':
        userCallbacks?.onThinking?.(payload as any)
        break
      case 'tool_call':
        userCallbacks?.onToolCall?.(payload as any)
        break
      case 'tool_result':
        userCallbacks?.onToolResult?.(payload as any)
        break
      case 'sql':
        userCallbacks?.onSql?.(payload as any)
        break
      case 'text':
        userCallbacks?.onText?.(payload as any)
        break
      case 'result':
        userCallbacks?.onResult?.(payload as any)
        break
      case 'error':
        errorSeen = true // 标记：忽略后续事件
        userCallbacks?.onError?.(payload as any)
        break
      case 'done':
        userCallbacks?.onDone?.(payload as any)
        break
      default:
        console.warn('[SSE] 未注册的事件类型:', type)
    }
  }

  // ─── 读取循环 ───

  /**
   * 异步读取 ReadableStream，按 \n\n 边界切分完整 SSE 消息
   */
  async function readLoop(): Promise<void> {
    if (!reader) return

    const decoder = new TextDecoder()
    let buffer = ''
    resetTimeout()

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break // 服务端正常关闭

        // 解码并追加到缓冲区
        buffer += decoder.decode(value, { stream: true })

        // 按 SSE 消息分隔符 \n\n 拆分
        const parts = buffer.split('\n\n')
        // 最后一个可能是未完成的消息块，留在 buffer 中
        buffer = parts.pop() ?? ''

        for (const part of parts) {
          if (!part.trim()) continue
          dispatchSSEEvent(part)
          // 收到消息后刷新超时
          resetTimeout()
        }
      }

      // 处理剩余缓冲
      if (buffer.trim()) {
        dispatchSSEEvent(buffer)
      }
    } catch (err: unknown) {
      // 手动取消连接不是错误
      if (err instanceof DOMException && err.name === 'AbortError') {
        return
      }
      const reason = err instanceof Error ? err.message : '流读取中断'
      userCallbacks?.onDisconnect?.(reason)
    } finally {
      disconnectInternal()
    }
  }

  // ─── 内部清理 ───

  function disconnectInternal(): void {
    isConnected.value = false
    clearTimeoutTimer()
    reader = null
  }

  // ─── 公开方法 ───

  /**
   * 建立 SSE 连接
   *
   * @param url  目标 URL（如 /api/chat/stream）
   * @param body POST 请求体
   * @param cb   事件回调（onThinking / onToolCall / onToolResult / onSql / onResult / onError / onDone / onDisconnect / onTimeout）
   */
  async function connect(url: string, body: unknown, cb: SSEEventCallbacks): Promise<void> {
    // 断开已有连接
    disconnect()

    userCallbacks = cb
    errorSeen = false
    isError.value = false
    errorMessage.value = null

    abortController = new AbortController()

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
          'X-Request-ID': crypto.randomUUID(),
        },
        body: JSON.stringify(body),
        signal: abortController.signal,
      })

      // HTTP 错误处理
      if (!response.ok) {
        let userMsg = `HTTP ${response.status}`
        try {
          const errBody = await response.json()
          userMsg = (errBody.user_message as string) ?? userMsg
        } catch {
          // 非 JSON 响应体
        }
        userCallbacks?.onDisconnect?.(userMsg)
        isError.value = true
        errorMessage.value = userMsg
        return
      }

      if (!response.body) {
        userCallbacks?.onDisconnect?.('响应体为空')
        return
      }

      isConnected.value = true
      reader = response.body.getReader()

      // 启动读取循环（异步，不阻塞）
      readLoop()
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        // 手动断开，不触发回调
        return
      }
      const msg = err instanceof Error ? err.message : '连接失败'
      userCallbacks?.onDisconnect?.(msg)
      isConnected.value = false
      isError.value = true
      errorMessage.value = msg
    }
  }

  /**
   * 手动断开 SSE 连接
   * - 取消 reader（服务端可感知）
   * - 中止 fetch（AbortController）
   * - 不触发 onDisconnect 回调
   */
  function disconnect(): void {
    if (reader) {
      reader.cancel().catch(() => {
        /* 忽略取消错误 */
      })
      reader = null
    }
    abortController?.abort()
    abortController = null
    disconnectInternal()
  }

  return {
    /** 是否已连接 */
    isConnected: readonly(isConnected),
    /** 是否发生错误 */
    isError: readonly(isError),
    /** 错误描述 */
    errorMessage: readonly(errorMessage),
    /** 建立 SSE 连接 */
    connect,
    /** 手动断开 SSE 连接 */
    disconnect,
  }
}
