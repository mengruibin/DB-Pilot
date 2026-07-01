/**
 * SQL 编辑器自动补全 composable
 *
 * 基于数据库元数据构建补全候选项。
 * 缓存有效期 5 分钟，连接切换时自动清除。
 *
 * 依据 api-contract §1.1 GET /api/connections/{id}/metadata
 *     frontend AGENTS.md §4 语法辅助边界
 */
import { ref, computed, watch } from 'vue'
import { getConnectionMetadata } from '@/api/connection'
import { useConnectionStore } from '@/stores/connection'
import type { TableMeta, ColumnMeta } from '@/types/connection'

/** 缓存有效期（毫秒） */
const CACHE_TTL = 5 * 60 * 1000

/** 候选条目 */
export interface CompletionItem {
  /** 显示的文本 */
  label: string
  /** 类型标注 */
  detail: string
  /** 插入文本 */
  insertText: string
  /** 类别：table / column */
  kind: 'table' | 'column'
}

/** SQL 关键字（用于上下文判断） */
const FROM_JOIN_KEYWORDS = /\b(FROM|JOIN)\s+$/i
const WHERE_KEYWORDS = /\bWHERE\s+$/i
const DOT_PATTERN = /(\w+)\.$/

export function useSqlCompletion() {
  const store = useConnectionStore()

  // ─── 元数据缓存 ───

  /** 缓存的元数据（表列表） */
  const cachedTables = ref<TableMeta[]>([])
  /** 缓存时间戳 */
  const cacheTimestamp = ref<number>(0)
  /** 是否正在加载 */
  const loading = ref(false)
  /** 加载错误 */
  const error = ref<string | null>(null)

  /** 缓存是否有效 */
  const isCacheValid = computed(() => {
    if (cachedTables.value.length === 0) return false
    return Date.now() - cacheTimestamp.value < CACHE_TTL
  })

  // ─── 展平数据 ───

  /** 所有列（含所属表名） */
  const allColumns = computed(() => {
    const result: Array<{ table: string; column: ColumnMeta }> = []
    for (const table of cachedTables.value) {
      for (const col of table.columns) {
        result.push({ table: table.table_name, column: col })
      }
    }
    return result
  })

  // ─── 获取元数据 ───

  /** 获取并缓存元数据 */
  async function fetchMetadata(connectionId: string): Promise<void> {
    if (loading.value) return

    loading.value = true
    error.value = null
    try {
      const res = await getConnectionMetadata(connectionId)
      cachedTables.value = res.tables
      cacheTimestamp.value = Date.now()
    } catch (err: unknown) {
      error.value = err instanceof Error ? err.message : '获取元数据失败'
    } finally {
      loading.value = false
    }
  }

  /** 确保元数据已加载（优先使用缓存） */
  async function ensureMetadata(connectionId: string): Promise<void> {
    if (isCacheValid.value) return
    await fetchMetadata(connectionId)
  }

  /** 清除缓存 */
  function clearCache(): void {
    cachedTables.value = []
    cacheTimestamp.value = 0
    error.value = null
  }

  // ─── 连接切换时自动清除缓存 ───

  watch(() => store.activeId, (newId, oldId) => {
    if (newId !== oldId) {
      clearCache()
    }
  })

  // ─── 补全候选构建 ───

  /**
   * 根据 SQL 文本和光标位置分析上下文，生成补全候选
   *
   * @param sql   当前 SQL 编辑器中的文本
   * @param cursor 光标位置（字符索引）
   * @returns 补全候选项列表（若无匹配返回空数组）
   */
  function getCompletions(sql: string, cursor: number): CompletionItem[] {
    const beforeCursor = sql.slice(0, cursor)

    // 1. 检测 "表名." → 补全列名
    const dotMatch = beforeCursor.match(DOT_PATTERN)
    if (dotMatch) {
      const tableName = dotMatch[1].toLowerCase()
      return getColumnCompletions(tableName)
    }

    // 2. 提取光标前的最后一个词
    const lastWord = beforeCursor.split(/[\s,();]+/).filter(Boolean).pop() ?? ''

    // 3. 检测 FROM/JOIN 后 → 补全表名
    const fromJoinMatch = beforeCursor.match(FROM_JOIN_KEYWORDS)
    if (fromJoinMatch) {
      return getFilteredTableCompletions(lastWord)
    }

    // 4. 检测 WHERE 后 → 补全列名
    const whereMatch = beforeCursor.match(WHERE_KEYWORDS)
    if (whereMatch) {
      return getFilteredColumnCompletions(lastWord)
    }

    // 5. 有输入文本时，模糊匹配表和列
    if (lastWord.length >= 2) {
      return getFuzzyCompletions(lastWord)
    }

    return []
  }

  /** 获取某表的所有列 */
  function getColumnCompletions(tableName: string): CompletionItem[] {
    const table = cachedTables.value.find(
      (t) => t.table_name.toLowerCase() === tableName
    )
    if (!table) return []

    return table.columns.map((col) => ({
      label: col.name,
      detail: `${col.type}${col.comment ? ` · ${col.comment}` : ''}`,
      insertText: col.name,
      kind: 'column' as const,
    }))
  }

  /** 获取匹配前缀的表名 */
  function getFilteredTableCompletions(prefix: string): CompletionItem[] {
    const lower = prefix.toLowerCase()
    return cachedTables.value
      .filter((t) => !lower || t.table_name.toLowerCase().startsWith(lower))
      .map((t) => ({
        label: t.table_name,
        detail: `${t.columns.length} 列${t.comment ? ` · ${t.comment}` : ''}`,
        insertText: t.table_name,
        kind: 'table' as const,
      }))
  }

  /** 获取匹配前缀的列名 */
  function getFilteredColumnCompletions(prefix: string): CompletionItem[] {
    const lower = prefix.toLowerCase()
    return allColumns.value
      .filter((c) => !lower || c.column.name.toLowerCase().startsWith(lower))
      .map((c) => ({
        label: c.column.name,
        detail: `${c.table}.${c.column.type}${c.column.comment ? ` · ${c.column.comment}` : ''}`,
        insertText: c.column.name,
        kind: 'column' as const,
      }))
      .slice(0, 50)
  }

  /** 模糊匹配表和列 */
  function getFuzzyCompletions(prefix: string): CompletionItem[] {
    const lower = prefix.toLowerCase()
    const results: CompletionItem[] = []

    // 表名匹配
    for (const t of cachedTables.value) {
      if (t.table_name.toLowerCase().includes(lower)) {
        results.push({
          label: t.table_name,
          detail: `${t.columns.length} 列${t.comment ? ` · ${t.comment}` : ''}`,
          insertText: t.table_name,
          kind: 'table',
        })
      }
    }

    // 列名匹配
    for (const c of allColumns.value) {
      if (c.column.name.toLowerCase().includes(lower)) {
        results.push({
          label: c.column.name,
          detail: `${c.table}.${c.column.type}${c.column.comment ? ` · ${c.column.comment}` : ''}`,
          insertText: c.column.name,
          kind: 'column',
        })
      }
    }

    return results.slice(0, 50)
  }

  // ─── SQL 上下文信息（用于编辑器展示） ───

  /** 获取当前连接的数据库类型 */
  const dbType = computed(() => {
    return store.activeConnection?.db_type ?? null
  })

  /** 缓存的表数量 */
  const tableCount = computed(() => cachedTables.value.length)

  /** 缓存剩余有效时间（秒） */
  const cacheRemainingSec = computed(() => {
    if (cacheTimestamp.value === 0) return 0
    const remaining = CACHE_TTL - (Date.now() - cacheTimestamp.value)
    return Math.max(0, Math.ceil(remaining / 1000))
  })

  return {
    // 状态
    loading,
    error,
    cachedTables,
    tableCount,
    cacheRemainingSec,
    dbType,
    isCacheValid,

    // 方法
    ensureMetadata,
    fetchMetadata,
    clearCache,
    getCompletions,
  }
}
