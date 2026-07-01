/**
 * 健康巡检报告 API 封装
 *
 * 依据 api-contract §1.4 健康巡检 API 端点
 *     §2.5 HealthReport
 */
import { http } from './client'
import type { HealthReport } from '@/types/report'

const BASE = '/api/reports'
const CONN_BASE = '/api/connections'

/** 触发健康巡检（返回 SSE session_id） */
export function triggerHealthCheck(
  connectionId: string,
  data?: { check_items?: string[]; timeout_sec?: number }
) {
  return http.post<string>(
    `${CONN_BASE}/${connectionId}/health-check`,
    data ?? { check_items: ['all'], timeout_sec: 30 }
  )
}

/** 获取历史巡检报告列表 */
export function getReports(params?: { page?: number; pageSize?: number }) {
  const searchParams = new URLSearchParams()
  if (params?.page) searchParams.set('page', String(params.page))
  if (params?.pageSize) searchParams.set('pageSize', String(params.pageSize))
  const qs = searchParams.toString()
  return http.get<{ items: HealthReport[]; total: number }>(
    `${BASE}${qs ? `?${qs}` : ''}`
  )
}

/** 获取单份巡检报告详情 */
export function getReport(id: string) {
  return http.get<HealthReport>(`${BASE}/${id}`)
}
