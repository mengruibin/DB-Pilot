/**
 * 对话与查询 API 封装
 *
 * 依据 api-contract §1.2 对话（stream/cancel）、§1.3 查询与诊断
 */
import { http, ApiError } from './client'
import type {
  DirectQueryRequest,
  QueryResult,
  SlowQueryListResponse,
  CancelRequest,
} from '@/types/chat'

const BASE = '/api/chat'

/** 取消当前正在执行的操作 */
export function cancelChat(data: CancelRequest) {
  return http.post<void>(`${BASE}/cancel`, data)
}

// ─── 直接查询与诊断（§1.3）───

const CONN_BASE = '/api/connections'

/** 执行 SQL 查询（直接模式，由后端审计控制读写） */
export function directQuery(connectionId: string, data: DirectQueryRequest) {
  return http.post<QueryResult>(`${CONN_BASE}/${connectionId}/query`, data)
}

/** 导出查询结果为 CSV（流式下载，返回 Blob） */
export async function exportCsv(
  connectionId: string,
  data: { sql: string; password?: string; max_rows?: number }
): Promise<Blob> {
  const resp = await fetch(`${CONN_BASE}/${connectionId}/export`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Request-ID': crypto.randomUUID(),
      ...(localStorage.getItem('db-pilot:token')
        ? { Authorization: `Bearer ${localStorage.getItem('db-pilot:token')}` }
        : {}),
    },
    body: JSON.stringify(data),
  })

  if (!resp.ok) {
    let userMessage = `HTTP ${resp.status}`
    try {
      const body = await resp.json()
      userMessage =
        (body.user_message as string) ||
        (body.detail?.user_message as string) ||
        userMessage
    } catch {
      // 非 JSON 错误体
    }
    throw new ApiError(resp.status, undefined, userMessage)
  }
  return resp.blob()
}

/** 获取慢查询列表 */
export function getSlowQueries(
  connectionId: string,
  params?: {
    time_range?: string
    limit?: number
    sort_by?: string
    page?: number
    pageSize?: number
  }
) {
  const searchParams = new URLSearchParams()
  if (params?.time_range) searchParams.set('time_range', params.time_range)
  if (params?.limit) searchParams.set('limit', String(params.limit))
  if (params?.sort_by) searchParams.set('sort_by', params.sort_by)
  if (params?.page) searchParams.set('page', String(params.page))
  if (params?.pageSize) searchParams.set('pageSize', String(params.pageSize))
  const qs = searchParams.toString()
  return http.get<SlowQueryListResponse>(
    `${CONN_BASE}/${connectionId}/slow-queries${qs ? `?${qs}` : ''}`
  )
}

/** 分页获取查询结果 */
export function getQueryResultPage(sessionId: string, page: number) {
  return http.get<QueryResult>(`${BASE}/result/${sessionId}?page=${page}`)
}
