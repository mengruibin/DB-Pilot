/**
 * 基础 HTTP 客户端
 *
 * 封装 fetch，自动注入 X-Request-ID（UUID v4），
 * 统一处理 4xx/5xx 响应解析 user_message。
 *
 * 依据 api-contract §1.6 错误码体系、T-1 方案 C（直接数据体 + HTTP 状态码）
 */

/** API 错误类 — 统一结构化错误 */
export class ApiError extends Error {
  /** HTTP 状态码 */
  status: number
  /** 后端错误码（如 SQL_AUDIT_BLOCKED） */
  errorCode: string | undefined
  /** 面向用户的错误信息 */
  userMessage: string
  /** 字段级错误定位（INVALID_PARAM 时） */
  field?: string
  /** 重试等待秒数（AGENT_ERROR 时） */
  retryAfterSec?: number

  constructor(
    status: number,
    errorCode: string | undefined,
    userMessage: string,
    field?: string,
    retryAfterSec?: number,
  ) {
    super(userMessage)
    this.name = 'ApiError'
    this.status = status
    this.errorCode = errorCode
    this.userMessage = userMessage
    this.field = field
    this.retryAfterSec = retryAfterSec
  }
}

/** 生成 UUID v4（X-Request-ID） */
function generateRequestId(): string {
  return crypto.randomUUID()
}

/** 请求选项扩展 */
interface RequestOptions extends RequestInit {
  /** 超时毫秒（默认 30000） */
  timeout?: number
}

/**
 * 核心请求方法
 * - 注入 X-Request-ID
 * - 统一超时控制
 * - 结构化错误解析
 */
async function request<T>(url: string, options: RequestOptions = {}): Promise<T> {
  const { timeout = 30000, ...fetchOptions } = options
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), timeout)

  // 合并 signal
  const combinedSignal = fetchOptions.signal
    ? anySignal([fetchOptions.signal, controller.signal])
    : controller.signal

  try {
    const response = await fetch(url, {
      ...fetchOptions,
      signal: combinedSignal,
      headers: {
        'Content-Type': 'application/json',
        'X-Request-ID': generateRequestId(),
        ...(localStorage.getItem('db-pilot:token')
          ? { Authorization: `Bearer ${localStorage.getItem('db-pilot:token')}` }
          : {}),
        ...fetchOptions.headers,
      },
    })

    clearTimeout(timeoutId)

    // 204 No Content（取消/删除成功）
    if (response.status === 204) {
      return undefined as T
    }

    // 成功响应
    if (response.ok) {
      return response.json() as Promise<T>
    }

    // 错误响应：解析结构化错误体
    let errorBody: Record<string, unknown> | undefined
    try {
      errorBody = await response.json()
    } catch {
      // 非 JSON 响应体
    }

    // 401: token 过期或无效 — 清除并跳转登录
    // 必须放在错误体解析之后，优先使用后端的 user_message（如"用户名或密码错误"）
    if (response.status === 401) {
      localStorage.removeItem('db-pilot:token')
      // 兼容平铺 {user_message: "..."} 和嵌套 {detail: {user_message: "..."}} 两种结构
      const detail = errorBody?.detail as Record<string, unknown> | undefined
      const userMsg = (errorBody?.user_message as string) ||
        (detail?.user_message as string) ||
        '登录已过期，请重新登录'
      // 仅非登录页跳转（避免登录失败时页面跳转）
      if (window.location.pathname !== '/login') {
        window.location.href = '/login'
      }
      throw new ApiError(401, 'UNAUTHORIZED', userMsg)
    }

    const errorCode = errorBody?.error_code as string | undefined
    const userMessage = (errorBody?.user_message as string) || `HTTP ${response.status}`
    const field = errorBody?.field as string | undefined
    const retryAfterSec = errorBody?.retry_after_sec as number | undefined

    // 对 422 Pydantic 校验错误做特殊处理
    if (response.status === 422 && Array.isArray(errorBody?.detail)) {
      const detail = (errorBody?.detail as Array<{ loc: string[]; msg: string }>)[0]
      const fieldPath = detail?.loc?.slice(1).join('.') || 'unknown'
      throw new ApiError(422, 'INVALID_PARAM', detail?.msg || '参数校验失败', fieldPath)
    }

    throw new ApiError(response.status, errorCode, userMessage, field, retryAfterSec)
  } catch (error) {
    clearTimeout(timeoutId)

    if (error instanceof ApiError) throw error

    // 超时
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError(408, 'EXECUTION_TIMEOUT', '请求超时，请稍后重试')
    }

    // 网络错误（fetch 仅在网络故障时抛出 TypeError）
    if (error instanceof TypeError) {
      throw new ApiError(502, 'DB_UNREACHABLE', `网络连接失败：${error.message}`)
    }

    throw error
  }
}

/**
 * 合并多个 AbortSignal
 * 任一 signal 触发 abort 时整体中断
 */
function anySignal(signals: AbortSignal[]): AbortSignal {
  const controller = new AbortController()
  for (const signal of signals) {
    if (signal.aborted) {
      controller.abort(signal.reason)
      return controller.signal
    }
    signal.addEventListener('abort', () => controller.abort(signal.reason), { once: true })
  }
  return controller.signal
}

/** HTTP 客户端单例 */
export const http = {
  get<T>(url: string, options?: RequestOptions): Promise<T> {
    return request<T>(url, { ...options, method: 'GET' })
  },
  post<T>(url: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return request<T>(url, {
      ...options,
      method: 'POST',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  },
  put<T>(url: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return request<T>(url, {
      ...options,
      method: 'PUT',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  },
  patch<T>(url: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return request<T>(url, {
      ...options,
      method: 'PATCH',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  },
  delete<T>(url: string, options?: RequestOptions): Promise<T> {
    return request<T>(url, { ...options, method: 'DELETE' })
  },
}
