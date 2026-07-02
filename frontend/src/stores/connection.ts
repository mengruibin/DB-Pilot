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

/** sessionStorage 键名（密码缓存，关闭标签页自动清除） */
const PASSWORD_STORAGE_KEY = 'db-pilot:passwords'

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

  /**
   * 连接密码缓存（持久化到 sessionStorage，关闭标签页自动清除）。
   * 创建/更新连接时存入，页面刷新后自动恢复。
   * 测试连接时从中取密码发送给后端。
   */
  const passwords = new Map<string, string>()

  /** 从 sessionStorage 恢复密码缓存 */
  function loadPasswords() {
    try {
      const saved = sessionStorage.getItem(PASSWORD_STORAGE_KEY)
      if (saved) {
        const parsed = JSON.parse(saved) as [string, string][]
        parsed.forEach(([id, pwd]) => passwords.set(id, pwd))
      }
    } catch {
      sessionStorage.removeItem(PASSWORD_STORAGE_KEY)
    }
  }

  /** 持久化密码缓存到 sessionStorage */
  function persistPasswords() {
    try {
      sessionStorage.setItem(
        PASSWORD_STORAGE_KEY,
        JSON.stringify(Array.from(passwords.entries())),
      )
    } catch {
      // sessionStorage 满等异常静默忽略
    }
  }

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
    // 缓存密码到 sessionStorage（用于后续连接测试）
    if (data.password) {
      passwords.set(created.id, data.password)
      persistPasswords()
    }
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
    // 更新密码缓存（如果提供了新密码）
    if (data.password) {
      passwords.set(id, data.password)
      persistPasswords()
    }
    persistToStorage()
    return entry
  }

  /** 删除连接（如果已激活则重置 activeId） */
  async function removeConnection(id: string): Promise<void> {
    await apiDelete(id)
    connections.value = connections.value.filter((c) => c.id !== id)
    // 清理密码缓存
    passwords.delete(id)
    persistPasswords()
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

    // 从内存缓存获取密码；无密码则跳过自动测试（保留 API 已有状态）
    const password = passwords.get(targetId)
    if (!password) {
      status.value = 'unknown'
      testError.value = '密码未缓存，请打开编辑表单输入密码后重试'
      return
    }

    isTesting.value = true
    status.value = 'connecting'
    testError.value = null

    try {
      const result = await apiTest(targetId, password)
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
        // 后端连接失败（认证错误、网络不可达等）→ 标记为 unreachable
        status.value = 'unreachable'
        testError.value = '连接测试失败，请检查网络、用户名和密码'
      }
    } catch (err: unknown) {
      status.value = 'unreachable'
      const msg = err instanceof Error ? err.message : '连接测试失败'
      testError.value = msg
    } finally {
      isTesting.value = false
    }
  }

  /**
   * 缓存连接密码到 sessionStorage（用于后续测试）。
   * 由 ConnectionForm 在创建/更新后及测试前调用。
   */
  function setPassword(id: string, password: string) {
    if (password) {
      passwords.set(id, password)
      persistPasswords()
    }
  }

  /**
   * 获取指定连接的缓存密码。
   * 由聊天 SSE 请求等场景调用，取不到时返回 undefined。
   */
  function getPassword(id: string): string | undefined {
    return passwords.get(id)
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

  // ─── 初始化：从 localStorage 恢复连接列表 + sessionStorage 恢复密码缓存 ───

  loadFromStorage()
  loadPasswords()

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
    setPassword,
    getPassword,
  }
})
