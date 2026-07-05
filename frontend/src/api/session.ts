/**
 * 会话管理 API 封装（F1）
 *
 * 会话 CRUD：列表、消息、重命名、删除。
 *
 * 依据 api-contract §2.2 Session/Message 实体定义
 * 及 conversation-history-plan.md F1 任务说明。
 */
import { http } from './client'
import type { Session, SessionListResponse, MessageListResponse } from '@/types/chat'

const SESSIONS_BASE = '/api/sessions'

/**
 * 获取会话列表（分页）
 *
 * @param params 筛选和分页参数
 * @returns 分页会话列表
 */
export function getSessions(params?: {
  connection_id?: string
  status?: string
  page?: number
  pageSize?: number
}): Promise<SessionListResponse> {
  const searchParams = new URLSearchParams()
  if (params?.connection_id) searchParams.set('connection_id', params.connection_id)
  if (params?.status) searchParams.set('status', params.status)
  if (params?.page) searchParams.set('page', String(params.page))
  if (params?.pageSize) searchParams.set('pageSize', String(params.pageSize))
  const qs = searchParams.toString()
  return http.get<SessionListResponse>(`${SESSIONS_BASE}${qs ? `?${qs}` : ''}`)
}

/**
 * 获取指定会话的消息历史（分页）
 *
 * @param sessionId 会话 ID
 * @param params 分页参数
 * @returns 分页消息列表
 */
export function getSessionMessages(
  sessionId: string,
  params?: { page?: number; pageSize?: number }
): Promise<MessageListResponse> {
  const searchParams = new URLSearchParams()
  if (params?.page) searchParams.set('page', String(params.page))
  if (params?.pageSize) searchParams.set('pageSize', String(params.pageSize))
  const qs = searchParams.toString()
  return http.get<MessageListResponse>(
    `${SESSIONS_BASE}/${sessionId}/messages${qs ? `?${qs}` : ''}`
  )
}

/**
 * 重命名会话标题（B1）
 *
 * @param sessionId 会话 ID
 * @param title 新标题（1-128 字符）
 * @returns 更新后的会话对象
 */
export function renameSession(sessionId: string, title: string): Promise<Session> {
  return http.patch<Session>(`${SESSIONS_BASE}/${sessionId}`, { title })
}

/**
 * 删除会话（B2）
 *
 * 会级联删除该会话的所有关联消息。
 *
 * @param sessionId 会话 ID
 */
export function deleteSession(sessionId: string): Promise<void> {
  return http.delete<void>(`${SESSIONS_BASE}/${sessionId}`)
}
