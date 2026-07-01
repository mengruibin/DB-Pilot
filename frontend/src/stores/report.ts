/**
 * 健康巡检 Store
 *
 * 管理巡检触发、SSE 流式进度、报告数据、历史列表。
 * 健康巡检 SSE 与聊天 SSE 使用不同事件类型，故独立管理连接。
 *
 * 依据 api-contract §1.4 健康巡检 SSE 事件
 *     §2.5 HealthReport
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { getReports, getReport } from '@/api/report'
import type {
  HealthReport,
  HealthReportCategory,
  CheckItemStatus,
  CheckProgressEvent,
  CheckWarningEvent,
  CheckErrorEvent,
  HealthResultEvent,
} from '@/types/report'

// ─── 检查项进度条目（Store 内部用） ───

export interface CheckProgressItem {
  /** 序号 1-based */
  index: number
  /** 总项数 */
  total: number
  /** 检查项名称 */
  name: string
  /** 状态 */
  status: CheckItemStatus | 'pending' | 'running'
  /** 当前值 */
  value?: string
  /** 阈值 */
  threshold?: string
  /** 建议 */
  suggestion?: string
}

// ─── Store ───

export const useReportStore = defineStore('report', () => {
  // ═══════════════════════════════════════════════════
  //  State
  // ═══════════════════════════════════════════════════

  /** 当前展示的完整报告 */
  const currentReport = ref<HealthReport | null>(null)

  /** 历史报告列表 */
  const historyReports = ref<HealthReport[]>([])

  /** 历史报告总数 */
  const historyTotal = ref(0)

  /** 是否正在加载历史列表 */
  const loadingHistory = ref(false)

  /** 是否正在加载报告详情 */
  const loadingReport = ref(false)

  // ── SSE 状态 ──

  /** 是否正在巡检中 */
  const isRunning = ref(false)

  /** SSE 连接是否正常 */
  const sseConnected = ref(false)

  /** SSE 错误信息 */
  const sseError = ref<string | null>(null)

  // ── 进度跟踪 ──

  /** 巡检进度条目列表 */
  const progressItems = ref<CheckProgressItem[]>([])

  /** 当前完成项数 */
  const progressCurrent = ref(0)

  /** 总检查项数 */
  const progressTotal = ref(0)

  /** 当前正在检查的项目名称 */
  const currentItemName = ref('')

  // ── 内部状态 ──

  let abortController: AbortController | null = null
  let reader: ReadableStreamDefaultReader<Uint8Array> | null = null

  // ═══════════════════════════════════════════════════
  //  Getters
  // ═══════════════════════════════════════════════════

  /** 进度百分比（0-100） */
  const progressPercent = computed(() => {
    if (progressTotal.value === 0) return 0
    return Math.round((progressCurrent.value / progressTotal.value) * 100)
  })

  /** 按 category 分组后的报告数据（直接取自 currentReport） */
  const reportCategories = computed(() => {
    return currentReport.value?.categories ?? []
  })

  /** 历史报告按分数排序（低分在前——危险优先） */
  const sortedHistory = computed(() => {
    return [...historyReports.value].sort((a, b) => a.score - b.score)
  })

  /** 是否有活跃的完整报告 */
  const hasReport = computed(() => currentReport.value !== null)

  // ═══════════════════════════════════════════════════
  //  Actions — SSE 巡检
  // ═══════════════════════════════════════════════════

  /**
   * 开始健康巡检
   *
   * @param connectionId 目标连接 ID
   * @param checkItems   检查项列表（默认 ['all']）
   * @param timeoutSec   超时秒数（默认 30）
   */
  async function startHealthCheck(
    connectionId: string,
    checkItems: string[] = ['all'],
    timeoutSec: number = 30
  ): Promise<void> {
    if (isRunning.value) return

    // 重置状态
    isRunning.value = true
    sseConnected.value = false
    sseError.value = null
    progressCurrent.value = 0
    progressTotal.value = 0
    currentItemName.value = ''
    progressItems.value = []
    currentReport.value = null

    abortController = new AbortController()

    try {
      const response = await fetch(
        `/api/connections/${connectionId}/health-check`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Accept: 'text/event-stream',
            'X-Request-ID': crypto.randomUUID(),
          },
          body: JSON.stringify({
            check_items: checkItems,
            timeout_sec: timeoutSec,
          }),
          signal: abortController.signal,
        }
      )

      if (!response.ok) {
        let userMsg = `HTTP ${response.status}`
        try {
          const errBody = await response.json()
          userMsg = (errBody.user_message as string) ?? userMsg
        } catch {
          // 非 JSON
        }
        throw new Error(userMsg)
      }

      if (!response.body) {
        throw new Error('响应体为空')
      }

      sseConnected.value = true
      reader = response.body.getReader()

      // 启动读取循环
      await readSSEStream()
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        // 用户取消
        isRunning.value = false
        sseConnected.value = false
        return
      }
      const msg = err instanceof Error ? err.message : '巡检连接失败'
      sseError.value = msg
      isRunning.value = false
      sseConnected.value = false
    }
  }

  /** SSE 读取循环 */
  async function readSSEStream(): Promise<void> {
    if (!reader) return

    const decoder = new TextDecoder()
    let buffer = ''

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const parts = buffer.split('\n\n')
        buffer = parts.pop() ?? ''

        for (const part of parts) {
          if (!part.trim()) continue
          dispatchHealthEvent(part)
        }
      }

      // 剩余缓冲
      if (buffer.trim()) {
        dispatchHealthEvent(buffer)
      }
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return
      const reason = err instanceof Error ? err.message : '流读取中断'
      sseError.value = reason
    } finally {
      isRunning.value = false
      sseConnected.value = false
      reader = null
    }
  }

  /** 解析并分发健康巡检 SSE 事件 */
  function dispatchHealthEvent(raw: string): void {
    let dataField = ''

    const lines = raw.split('\n')
    for (const line of lines) {
      const trimmed = line.trim()
      if (trimmed.startsWith('data:')) {
        dataField = trimmed.slice(5).trim()
      }
    }

    if (!dataField) return

    let payload: Record<string, unknown>
    try {
      payload = JSON.parse(dataField)
    } catch {
      return
    }

    const type = payload.type as string

    switch (type) {
      case 'check_progress':
        handleCheckProgress(payload as unknown as CheckProgressEvent)
        break
      case 'check_warning':
        handleCheckWarning(payload as unknown as CheckWarningEvent)
        break
      case 'check_error':
        handleCheckError(payload as unknown as CheckErrorEvent)
        break
      case 'health_result':
        handleHealthResult(payload as unknown as HealthResultEvent)
        break
      default:
        console.warn('[Report SSE] 未知事件类型:', type)
    }
  }

  /** 处理 check_progress 事件 */
  function handleCheckProgress(event: CheckProgressEvent): void {
    progressCurrent.value = event.current
    progressTotal.value = event.total
    currentItemName.value = event.item

    // 追加到进度列表
    progressItems.value.push({
      index: event.current,
      total: event.total,
      name: event.item,
      status: event.status as CheckItemStatus,
    })
  }

  /** 处理 check_warning 事件 */
  function handleCheckWarning(event: CheckWarningEvent): void {
    progressCurrent.value = event.current
    progressTotal.value = event.total
    currentItemName.value = event.item

    progressItems.value.push({
      index: event.current,
      total: event.total,
      name: event.item,
      status: 'warning',
      value: event.value,
      threshold: event.threshold,
      suggestion: event.suggestion,
    })
  }

  /** 处理 check_error 事件 */
  function handleCheckError(event: CheckErrorEvent): void {
    progressCurrent.value = event.current
    progressTotal.value = event.total
    currentItemName.value = event.item

    progressItems.value.push({
      index: event.current,
      total: event.total,
      name: event.item,
      status: 'error',
      value: event.value,
      threshold: event.threshold,
      suggestion: event.suggestion,
    })
  }

  /** 处理 health_result 事件（巡检完成） */
  function handleHealthResult(event: HealthResultEvent): void {
    // 构建一份概览报告
    currentReport.value = {
      id: event.report_id,
      connection_id: '',
      status: 'completed',
      score: event.score,
      generated_at: new Date().toISOString(),
      duration_sec: 0,
      severity_counts: event.severity_counts,
      categories: buildCategoriesFromProgress(),
    }
    isRunning.value = false
  }

  /** 由进度条目构建分类数据 */
  function buildCategoriesFromProgress(): HealthReportCategory[] {
    const map = new Map<string, HealthReportCategory>()

    for (const item of progressItems.value) {
      // 默认归类到"其他"
      const categoryName = '检查项'
      if (!map.has(categoryName)) {
        map.set(categoryName, { name: categoryName, items: [] })
      }
      const cat = map.get(categoryName)!
      cat.items.push({
        name: item.name,
        status: item.status === 'pending' || item.status === 'running'
          ? 'skipped'
          : (item.status as CheckItemStatus),
        value: item.value ?? '',
        threshold: item.threshold ?? null,
        suggestion: item.suggestion ?? null,
        is_destructive: false,
      })
    }

    return Array.from(map.values())
  }

  /** 取消当前巡检 */
  function cancelHealthCheck(): void {
    if (reader) {
      reader.cancel().catch(() => { /* 忽略 */ })
      reader = null
    }
    abortController?.abort()
    abortController = null
    isRunning.value = false
    sseConnected.value = false
  }

  // ═══════════════════════════════════════════════════
  //  Actions — 报告管理
  // ═══════════════════════════════════════════════════

  /** 加载历史报告列表 */
  async function fetchHistory(page: number = 1, pageSize: number = 20): Promise<void> {
    loadingHistory.value = true
    try {
      const data = await getReports({ page, pageSize })
      historyReports.value = data.items
      historyTotal.value = data.total
    } catch (err) {
      console.error('[ReportStore] 加载历史失败:', err)
    } finally {
      loadingHistory.value = false
    }
  }

  /** 加载单份报告详情 */
  async function fetchReport(id: string): Promise<void> {
    loadingReport.value = true
    try {
      const data = await getReport(id)
      currentReport.value = data
    } catch (err) {
      console.error('[ReportStore] 加载报告失败:', err)
    } finally {
      loadingReport.value = false
    }
  }

  /** 清除当前报告 */
  function clearCurrentReport(): void {
    currentReport.value = null
    progressItems.value = []
    progressCurrent.value = 0
    progressTotal.value = 0
    currentItemName.value = ''
  }

  // ═══════════════════════════════════════════════════
  //  Return
  // ═══════════════════════════════════════════════════

  return {
    // state
    currentReport,
    historyReports,
    historyTotal,
    loadingHistory,
    loadingReport,
    isRunning,
    sseConnected,
    sseError,
    progressItems,
    progressCurrent,
    progressTotal,
    progressPercent,
    currentItemName,

    // getters
    reportCategories,
    sortedHistory,
    hasReport,

    // actions
    startHealthCheck,
    cancelHealthCheck,
    fetchHistory,
    fetchReport,
    clearCurrentReport,
  }
})
