/**
 * 连接管理 Store
 *
 * 依据 api-contract §三 Store 划分（connectionStore）
 * - connections 从 GET /api/connections 加载，写入 Pinia state
 * - activeId 变更时自动触发连接测试
 * - connections 持久化到 localStorage（不含 password）
 * - activeId 不持久化（页面刷新需重新测试）
 * - status 映射四种状态
 */
import { defineStore } from 'pinia'
import { ref, watch, computed } from 'vue'
import {
  getConnections,
  createConnection as apiCreate,
  updateConnection as apiUpdate,
  deleteConnection as apiDelete,
  testConnection as apiTest,
} from '@/api/connection'
import type {
  ConnectionConfig,
  ConnectionStatusValue,
  CreateConnectionRequest,
  UpdateConnectionRequest,
  TestConnectionResponse,
} from '@/types/connection'

/** localStorage 键名 */
const STORAGE_KEY = 'db-pilot:connections'

/** 运行时连接状态（扩展了中间态 'connecting'） */
export type RuntimeStatus = ConnectionStatusValue | 'connecting'

export const useConnectionStore = defineStore('connection', () => {
  // ─── State ───

  /** 已保存连接列表（不含 password） */
  const connections = ref<ConnectionConfig[]>([])

  /** 当前活跃连接 ID（页面刷新后为 null） */
  const activeId = ref<string | null>(null)

  /** 连接运行时状态（区分于 ConnectionConfig.status） */
  const status = ref<RuntimeStatus>('unknown')

  /** 是否正在测试连接 */
  const isTesting = ref(false)

  /** 最近一次测试延迟（毫秒） */
  const testLatencyMs = ref<number | null>(null)

  /** 最近一次测试响应（含 capabilities） */
  const testResult = ref<TestConnectionResponse | null>(null)

  /** 最近一次测试时间 */
  const lastTestedAt = ref<string | null>(null)

  /** 连接测试失败信息 */
  const testError = ref<string | null>(null)

  // ─── Getters ───

  /** 当前活跃的连接配置 */
  const activeConnection = computed(() => {
    if (!activeId.value) return null
    return connections.value.find((c) => c.id === activeId.value) ?? null
  })

  /** 连接是否就绪（可用且已测试通过） */
  const isReady = computed(() => status.value === 'healthy')

  // ─── 从 localStorage 恢复缓存数据 ───

  function loadFromStorage() {
    try {
      const saved = localStorage.getItem(STORAGE_KEY)
      if (saved) {
        const parsed = JSON.parse(saved) as ConnectionConfig[]
        // 确保每个连接有 password 占位符
        connections.value = parsed.map((c) => ({ ...c, password: '***' }))
      }
    } catch {
      // 存储数据损坏时静默忽略
      localStorage.removeItem(STORAGE_KEY)
    }
  }

  /** 写入 localStorage（过滤 password） */
  function persistToStorage() {
    try {
      const toSave = connections.value.map((c) => ({
        ...c,
        password: '***' as const,
      }))
      localStorage.setItem(STORAGE_KEY, JSON.stringify(toSave))
    } catch {
      // localStorage 满等异常静默忽略
    }
  }

  // ─── Actions ───

  /** 加载连接列表（刷新 API 后合并到本地缓存） */
  async function loadConnections() {
    try {
      const res = await getConnections()
      // 合并 API 数据，保留本地已有但未保存的字段（仅 password 占位）
      const merged = res.items.map((apiItem) => {
        const local = connections.value.find((c) => c.id === apiItem.id)
        return {
          ...apiItem,
          password: local?.password ?? '***',
        }
      })
      connections.value = merged
      persistToStorage()
    } catch {
      // API 不可用时使用 localStorage 缓存数据
      // 已在 loadFromStorage 中恢复
    }
  }

  /** 创建新连接 */
  async function addConnection(data: CreateConnectionRequest): Promise<ConnectionConfig> {
    const created = await apiCreate(data)
    // 后端返回不含 password，添加占位符
    const entry: ConnectionConfig = { ...created, password: '***' }
    connections.value.push(entry)
    persistToStorage()
    return entry
  }

  /** 更新连接 */
  async function updateConnection(id: string, data: UpdateConnectionRequest): Promise<ConnectionConfig> {
    const updated = await apiUpdate(id, data)
    const entry: ConnectionConfig = { ...updated, password: '***' }
    const idx = connections.value.findIndex((c) => c.id === id)
    if (idx !== -1) {
      connections.value[idx] = entry
    }
    persistToStorage()
    return entry
  }

  /** 删除连接（如果已激活则重置 activeId） */
  async function removeConnection(id: string): Promise<void> {
    await apiDelete(id)
    connections.value = connections.value.filter((c) => c.id !== id)
    if (activeId.value === id) {
      activeId.value = null
      status.value = 'unknown'
    }
    persistToStorage()
  }

  /** 设置活跃连接（自动触发连接测试） */
  function setActiveConnection(id: string | null) {
    if (activeId.value === id) return
    activeId.value = id
  }

  /** 测试连接可用性 */
  async function testConnection(id: string): Promise<void> {
    if (isTesting.value) return

    const targetId = id || activeId.value
    if (!targetId) return

    isTesting.value = true
    status.value = 'connecting'
    testError.value = null

    try {
      const result = await apiTest(targetId)
      testResult.value = result
      testLatencyMs.value = result.latency_ms
      lastTestedAt.value = new Date().toISOString()

      // 根据测试结果更新状态
      if (result.success) {
        status.value = 'healthy'

        // 同步更新 connections 列表中的状态
        const idx = connections.value.findIndex((c) => c.id === targetId)
        if (idx !== -1) {
          connections.value[idx] = {
            ...connections.value[idx],
            status: 'healthy',
            last_tested_at: lastTestedAt.value,
          }
          persistToStorage()
        }
      } else {
        status.value = 'degraded'
      }
    } catch (err: unknown) {
      status.value = 'unreachable'
      const msg = err instanceof Error ? err.message : '连接测试失败'
      testError.value = msg
    } finally {
      isTesting.value = false
    }
  }

  // ─── Watchers ───

  // activeId 变更时自动测试连接
  watch(activeId, (newId) => {
    if (newId) {
      testConnection(newId)
    } else {
      status.value = 'unknown'
      testLatencyMs.value = null
      testResult.value = null
      testError.value = null
    }
  })

  // connections 变更时自动持久化
  watch(connections, persistToStorage, { deep: true })

  // ─── 初始化：从 localStorage 恢复 ───

  loadFromStorage()

  return {
    // state
    connections,
    activeId,
    status,
    isTesting,
    testLatencyMs,
    testResult,
    lastTestedAt,
    testError,
    // getters
    activeConnection,
    isReady,
    // actions
    loadConnections,
    addConnection,
    updateConnection,
    removeConnection,
    setActiveConnection,
    testConnection,
  }
})
