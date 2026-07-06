/**
 * 对话与查询 API 封装
 *
 * 依据 api-contract §1.2 对话（stream/cancel）、§1.3 查询与诊断
 */
import { http } from './client'
import type {
  DirectQueryRequest,
  ExplainRequest,
  ExplainResponse,
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

/** 获取 SQL 执行计划 */
export function explainQuery(connectionId: string, data: ExplainRequest) {
  return http.post<ExplainResponse>(`${CONN_BASE}/${connectionId}/explain`, data)
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
