/**
 * 连接管理相关类型定义
 *
 * 依据 api-contract §2.1 ConnectionConfig / ConnectionStatus
 * 字段名与后端 Pydantic schema 完全一致
 */

/** 数据库类型枚举 */
export type DbType = 'mysql' | 'postgresql' | 'oracle'

/** 连接状态枚举 */
export type ConnectionStatusValue = 'unknown' | 'healthy' | 'unreachable' | 'degraded'

/** 连接配置（后端响应中不含 password） */
export interface ConnectionConfig {
  id: string
  name: string
  db_type: DbType
  host: string
  port: number
  database: string
  user: string
  /** 后端响应中永远为 "***"，仅在创建/更新请求中可传入 */
  password: string
  ssl_enabled: boolean
  ssl_ca_cert: string | null
  extra_params: Record<string, string> | null
  created_at: string
  updated_at: string
  last_tested_at: string | null
  status: ConnectionStatusValue
}

/** 创建连接请求体 */
export interface CreateConnectionRequest {
  name: string
  db_type: DbType
  host: string
  port?: number
  database: string
  user: string
  password: string
  ssl_enabled?: boolean
  ssl_ca_cert?: string
}

/** 更新连接请求体 */
export type UpdateConnectionRequest = Partial<CreateConnectionRequest>

/** 连接测试响应 */
export interface TestConnectionResponse {
  success: boolean
  latency_ms: number
  version: string
  capabilities: {
    supports_explain: boolean
    supports_slow_query_log: boolean
    supports_replication: boolean
    supports_table_spaces: boolean
  }
}

/** 连接列表响应（分页） */
export interface ConnectionListResponse {
  items: ConnectionConfig[]
  total: number
  page: number
  pageSize: number
}

/** 连接状态（数据库连接池状态） */
export interface ConnectionStatus {
  total_connections: number
  active_connections: number
  idle_connections: number
  waiting_connections: number
  usage_percent: number
  aborted_connections_rate: number
  sampled_at: string
}

/** 元数据 - 列 */
export interface ColumnMeta {
  name: string
  type: string
  nullable: boolean
  is_primary: boolean
  comment: string
}

/** 元数据 - 索引 */
export interface IndexMeta {
  name: string
  columns: string[]
  is_unique: boolean
  type: string
}

/** 元数据 - 表 */
export interface TableMeta {
  database: string
  table_name: string
  comment: string
  row_count_estimate: number
  columns: ColumnMeta[]
  indexes: IndexMeta[]
}

/** 元数据响应 */
export interface MetadataResponse {
  databases: string[]
  tables: TableMeta[]
}
