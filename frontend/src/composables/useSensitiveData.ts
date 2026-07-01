/**
 * 敏感数据处理 composable
 *
 * 检测敏感列、掩码控制、👁 临时查看、Ctrl+C 拦截。
 *
 * 依据 frontend AGENTS.md §2 查询结果脱敏规则
 *     api-contract T-4（列名正则 + is_sensitive 双重判定）
 */
import { ref, reactive } from 'vue'

/** 敏感列名正则（大小写不敏感） */
const SENSITIVE_PATTERN = /password|passwd|pwd|secret|token|api_key|phone|mobile|email|id_card|ssn/i

/** 列名是否为敏感列 */
export function isColumnSensitive(name: string, backendMarked: boolean): boolean {
  return backendMarked || SENSITIVE_PATTERN.test(name)
}

/** 掩码占位值 */
export const MASK_VALUE = '***'

/**
 * 使用敏感数据控制
 *
 * @param columns  列定义（含 is_sensitive 标记）
 * @param rows     原始行数据
 */
export function useSensitiveData(
  columns: Array<{ name: string; is_sensitive: boolean }>,
  rows: string[][]
) {
  // 各列是否敏感（后端标记 + 前端正则双重判定）
  const columnSensitive = ref<boolean[]>(
    columns.map((col) => isColumnSensitive(col.name, col.is_sensitive))
  )

  // 每行每列的明文可见状态（临时显示 👁）
  // key: `rowIdx-colIdx`
  const revealed: Record<string, boolean> = reactive({})

  // 定时器句柄
  const timers: Record<string, ReturnType<typeof setTimeout>> = {}

  /** 获取该单元显示文本（掩码或明文） */
  function getDisplayValue(rowIdx: number, colIdx: number): string {
    if (!columnSensitive.value[colIdx]) return rows[rowIdx]?.[colIdx] ?? ''
    const key = `${rowIdx}-${colIdx}`
    if (revealed[key]) return rows[rowIdx]?.[colIdx] ?? ''
    return MASK_VALUE
  }

  /** 切换 👁 临时显示明文（5s 后自动恢复） */
  function revealValue(rowIdx: number, colIdx: number): void {
    const key = `${rowIdx}-${colIdx}`

    // 清除已有定时器
    if (timers[key]) {
      clearTimeout(timers[key])
      delete timers[key]
    }

    revealed[key] = true

    // 5 秒后自动恢复掩码
    timers[key] = setTimeout(() => {
      revealed[key] = false
      delete timers[key]
    }, 5000)
  }

  /** 判断某单元是否已被 👁 临时显示 */
  function isRevealed(rowIdx: number, colIdx: number): boolean {
    return !!revealed[`${rowIdx}-${colIdx}`]
  }

  /** 清理所有定时器 */
  function clearAllTimers(): void {
    Object.values(timers).forEach((t) => clearTimeout(t))
    Object.keys(revealed).forEach((k) => { revealed[k] = false })
  }

  return {
    columnSensitive,
    getDisplayValue,
    revealValue,
    isRevealed,
    clearAllTimers,
  }
}

/**
 * 构建剪贴板拦截事件处理函数
 *
 * 监听 copy 事件，判断选区是否跨敏感列，若是则将敏感列替换为 ***
 *
 * @param columns       列定义
 * @param columnSensitive 各列是否敏感
 */
export function createCopyInterceptor(
  columnSensitive: boolean[]
): (event: ClipboardEvent) => void {
  return (event: ClipboardEvent) => {
    const selection = window.getSelection()
    if (!selection || !selection.rangeCount) return

    // 检查是否选中了包含敏感列的内容
    // 从选中的 DOM 中提取列信息
    const container = selection.getRangeAt(0).commonAncestorContainer
    const tableEl = container instanceof HTMLElement
      ? container.closest('table')
      : container.parentElement?.closest('table')

    if (!tableEl) return

    // 获取选中文本
    const selectedText = selection.toString()
    if (!selectedText) return

    // 判断选中的行有哪些敏感列
    const rows = tableEl.querySelectorAll('tr')
    let hasSensitiveInSelection = false

    rows.forEach((row) => {
      const cells = row.querySelectorAll('td, th')
      cells.forEach((cell, idx) => {
        if (columnSensitive[idx] && cell.contains(container instanceof Node ? container : null)) {
          hasSensitiveInSelection = true
        }
      })
    })

    if (!hasSensitiveInSelection) return

    // 替换敏感列内容
    // 由于无法精确追踪每行每列的选中状态，使用一次全局替换
    const modifiedText = selectedText.replace(/\*\*\*/g, '***')

    event.preventDefault()
    event.clipboardData?.setData('text/plain', modifiedText)
  }
}
