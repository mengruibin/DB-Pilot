/**
 * 连接管理 API 封装
 *
 * 依据 api-contract §1.1 连接管理 API（7 个端点）
 */
import { http } from './client'
import type {
  ConnectionConfig,
  ConnectionListResponse,
  CreateConnectionRequest,
  UpdateConnectionRequest,
  TestConnectionResponse,
  MetadataResponse,
} from '@/types/connection'

const BASE = '/api/connections'

/** 获取已保存连接列表 */
export function getConnections() {
  return http.get<ConnectionListResponse>(BASE)
}

/** 创建新连接 */
export function createConnection(data: CreateConnectionRequest) {
  return http.post<ConnectionConfig>(BASE, data)
}

/** 获取连接详情（不含密码） */
export function getConnection(id: string) {
  return http.get<ConnectionConfig>(`${BASE}/${id}`)
}

/** 更新连接配置 */
export function updateConnection(id: string, data: UpdateConnectionRequest) {
  return http.put<ConnectionConfig>(`${BASE}/${id}`, data)
}

/** 删除连接 */
export function deleteConnection(id: string) {
  return http.delete<void>(`${BASE}/${id}`)
}

/** 测试连接可用性 */
export function testConnection(id: string, password?: string) {
  return http.post<TestConnectionResponse>(`${BASE}/${id}/test`, { password })
}

/** 获取连接元数据（库/表/列/索引） */
export function getConnectionMetadata(id: string) {
  return http.get<MetadataResponse>(`${BASE}/${id}/metadata`)
}
