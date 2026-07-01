<script setup lang="ts">
/**
 * ResultTable — 查询结果表格组件
 *
 * 敏感列自动掩码 + 👁 单行临时查看 + 复制拦截 + 列排序 + 分页。
 *
 * 依据 api-contract §2.3 QueryResult
 *     frontend AGENTS.md §2 敏感数据展示规范
 */
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useSensitiveData, createCopyInterceptor } from '@/composables/useSensitiveData'
import { useExport } from '@/composables/useExport'
import type { QueryColumn } from '@/types/chat'

const props = defineProps<{
  /** 列定义 */
  columns: QueryColumn[]
  /** 行数据 */
  rows: string[][]
  /** 总行数 */
  totalRows: number
  /** 执行耗时（毫秒） */
  executionTimeMs?: number
  /** 每页条数 */
  pageSize?: number
}>()

// ─── 排序 ───

type SortDirection = 'asc' | 'desc' | null
const sortColumn = ref<number | null>(null)
const sortDirection = ref<SortDirection>(null)

/** 切换排序（无 → 升序 → 降序 → 无） */
function toggleSort(colIdx: number): void {
  if (sortColumn.value !== colIdx) {
    sortColumn.value = colIdx
    sortDirection.value = 'asc'
  } else if (sortDirection.value === 'asc') {
    sortDirection.value = 'desc'
  } else if (sortDirection.value === 'desc') {
    sortColumn.value = null
    sortDirection.value = null
  }
}

/** 排序后的行 */
const sortedRows = computed(() => {
  if (sortColumn.value === null || sortDirection.value === null) return props.rows
  return [...props.rows].sort((a, b) => {
    const aVal = a[sortColumn.value!] ?? ''
    const bVal = b[sortColumn.value!] ?? ''
    // 尝试数字排序
    const aNum = parseFloat(aVal)
    const bNum = parseFloat(bVal)
    if (!isNaN(aNum) && !isNaN(bNum)) {
      return sortDirection.value === 'asc' ? aNum - bNum : bNum - aNum
    }
    return sortDirection.value === 'asc'
      ? aVal.localeCompare(bVal)
      : bVal.localeCompare(aVal)
  })
})

// ─── 敏感数据 ───

const { columnSensitive, getDisplayValue, revealValue, isRevealed, clearAllTimers }
  = useSensitiveData(props.columns, sortedRows.value)

// 当 rows 变化时重新初始化敏感数据
watch(() => props.rows, () => {
  clearAllTimers()
})

// ─── 分页 ───

const defaultPageSize = props.pageSize ?? 100
const currentPage = ref(1)

const totalPages = computed(() => Math.max(1, Math.ceil(props.totalRows / defaultPageSize)))

const paginatedRows = computed(() => {
  const start = (currentPage.value - 1) * defaultPageSize
  const end = start + defaultPageSize
  return sortedRows.value.slice(start, end)
})

const showPagination = computed(() => props.totalRows > defaultPageSize)

function goToPage(page: number): void {
  if (page >= 1 && page <= totalPages.value) {
    currentPage.value = page
  }
}

// ─── 复制拦截 ───

const copyInterceptor = createCopyInterceptor(columnSensitive.value)

onMounted(() => {
  document.addEventListener('copy', copyInterceptor)
  document.addEventListener('click', handleClickOutside)
})

onUnmounted(() => {
  document.removeEventListener('copy', copyInterceptor)
  document.removeEventListener('click', handleClickOutside)
  clearAllTimers()
})

/** 点击外部关闭导出菜单 */
function handleClickOutside(): void {
  exportMenuOpen.value = false
}

// ─── 格式化耗时 ───

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

// ─── 排序图标 ───

const SORT_ICONS: Record<string, string> = {
  asc: '▲',
  desc: '▼',
}

// ─── 分页页码 ───

const pageNumbers = computed(() => {
  const total = totalPages.value
  const current = currentPage.value
  const pages: (number | string)[] = []

  if (total <= 7) {
    for (let i = 1; i <= total; i++) pages.push(i)
  } else {
    pages.push(1)
    if (current > 3) pages.push('…')
    for (let i = Math.max(2, current - 1); i <= Math.min(total - 1, current + 1); i++) {
      pages.push(i)
    }
    if (current < total - 2) pages.push('…')
    pages.push(total)
  }
  return pages
})

// ─── 导出 ───

const exportTool = useExport()

/** 导出下拉菜单是否展开 */
const exportMenuOpen = ref(false)

function toggleExportMenu(): void {
  exportMenuOpen.value = !exportMenuOpen.value
}

function handleExport(format: 'csv' | 'excel'): void {
  exportMenuOpen.value = false
  exportTool.openExport(format, {
    columns: props.columns,
    rows: sortedRows.value,
    fileName: 'query_result',
  })
}
</script>

<template>
  <div class="result-table">
    <!-- 表头信息 -->
    <div class="table-info">
      <span class="info-summary">查询结果 {{ totalRows }} 行</span>
      <div class="info-actions">
        <span v-if="executionTimeMs !== undefined" class="info-duration">
          {{ formatDuration(executionTimeMs) }}
        </span>
        <div class="export-wrapper">
          <button
            class="export-btn"
            title="导出结果"
            @click="toggleExportMenu"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
              <polyline points="7 10 12 15 17 10"/>
              <line x1="12" y1="15" x2="12" y2="3"/>
            </svg>
            导出
          </button>
          <!-- 下拉菜单 -->
          <Transition name="fade">
            <div v-if="exportMenuOpen" class="export-dropdown" @click.stop>
              <button class="dropdown-item" @click="handleExport('csv')">
                📄 CSV
              </button>
              <button class="dropdown-item" @click="handleExport('excel')">
                📊 Excel
              </button>
            </div>
          </Transition>
        </div>
      </div>
    </div>

    <!-- 表格 -->
    <div class="table-wrapper">
      <table class="data-table">
        <thead>
          <tr>
            <th
              v-for="(col, ci) in columns"
              :key="ci"
              class="col-header"
              :class="{ sortable: true, sorted: sortColumn === ci }"
              @click="toggleSort(ci)"
            >
              <span class="header-content">
                <span class="col-name">{{ col.name }}</span>
                <span v-if="columnSensitive[ci]" class="sensitive-icon" title="敏感字段">🔒</span>
                <span v-if="sortColumn === ci" class="sort-arrow">{{ SORT_ICONS[sortDirection!] }}</span>
              </span>
              <span class="col-type">{{ col.type }}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, ri) in paginatedRows" :key="ri" class="data-row">
            <td v-for="(cell, ci) in row" :key="ci" class="data-cell" :class="{ sensitive: columnSensitive[ci] }">
              <template v-if="columnSensitive[ci]">
                <span class="masked-value">{{ getDisplayValue((currentPage - 1) * defaultPageSize + ri, ci) }}</span>
                <button
                  v-if="!isRevealed((currentPage - 1) * defaultPageSize + ri, ci)"
                  class="eye-btn"
                  title="临时查看明文（5 秒后自动隐藏）"
                  @click="revealValue((currentPage - 1) * defaultPageSize + ri, ci)"
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
                    <circle cx="12" cy="12" r="3"/>
                  </svg>
                </button>
                <span v-else class="reveal-badge">👁</span>
              </template>
              <template v-else>
                {{ cell }}
              </template>
            </td>
          </tr>
          <!-- 空行占位 -->
          <tr v-if="paginatedRows.length === 0" class="empty-row">
            <td :colspan="columns.length" class="empty-cell">无数据</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 分页 -->
    <div v-if="showPagination" class="pagination">
      <button
        class="page-btn"
        :disabled="currentPage <= 1"
        @click="goToPage(currentPage - 1)"
      >
        ‹
      </button>
      <template v-for="(p, pi) in pageNumbers" :key="pi">
        <span v-if="p === '…'" class="page-ellipsis">…</span>
        <button
          v-else
          class="page-btn"
          :class="{ active: p === currentPage }"
          @click="goToPage(p as number)"
        >
          {{ p }}
        </button>
      </template>
      <button
        class="page-btn"
        :disabled="currentPage >= totalPages"
        @click="goToPage(currentPage + 1)"
      >
        ›
      </button>
      <span class="page-info">{{ currentPage }} / {{ totalPages }} 页</span>
    </div>

    <!-- ═══ 导出对话框 ═══ -->
    <Transition name="modal-fade">
      <div v-if="exportTool.showDialog.value" class="export-overlay" @click.self="exportTool.cancelExport()">
        <div class="export-dialog">
          <div class="dialog-header">
            <h3 class="dialog-title">
              导出 {{ exportTool.exportFormat.value === 'csv' ? 'CSV' : 'Excel' }}
            </h3>
            <button class="dialog-close" @click="exportTool.cancelExport()">✕</button>
          </div>

          <div class="dialog-body">
            <p class="dialog-desc">选择要导出的列：</p>

            <div class="column-list">
              <div
                v-for="(col, ci) in exportTool.columns.value"
                :key="ci"
                class="column-item"
                :class="{ sensitive: exportTool.sensitiveFlags.value[ci] }"
              >
                <label class="col-label">
                  <input
                    type="checkbox"
                    :checked="exportTool.selectedColumns.has(ci)"
                    @change="exportTool.toggleColumn(ci)"
                  />
                  <span class="col-check-name">{{ col.name }}</span>
                  <span v-if="exportTool.sensitiveFlags.value[ci]" class="col-lock" title="敏感字段">🔒</span>
                  <span v-if="exportTool.sensitiveFlags.value[ci]" class="sensitive-tag">敏感</span>
                </label>
              </div>
            </div>

            <div v-if="exportTool.hasSensitiveSelected.value" class="sensitive-warning">
              ⚠️ 将导出包含敏感字段的数据
            </div>
          </div>

          <div class="dialog-footer">
            <button class="footer-btn cancel" @click="exportTool.cancelExport()">
              取消
            </button>
            <button
              class="footer-btn confirm"
              :disabled="exportTool.selectedColumns.size === 0 || exportTool.exporting.value"
              @click="exportTool.confirmExport()"
            >
              {{ exportTool.exporting.value ? '导出中...' : `下载 ${exportTool.exportFormat.value === 'csv' ? 'CSV' : 'Excel'}` }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.result-table {
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  overflow: hidden;
}

/* ─── 表头信息 ─── */
.table-info {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border-color);
}

.info-summary {
  font-size: 12px;
  color: var(--text-secondary);
}

.info-duration {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-tertiary);
}

/* ─── 表格容器 ─── */
.table-wrapper {
  overflow-x: auto;
}

/* ─── 表格 ─── */
.data-table {
  width: 100%;
  border-collapse: collapse;
  font-family: var(--font-mono);
  font-size: 12px;
}

/* 列头 */
.col-header {
  padding: 6px 10px;
  text-align: left;
  background: var(--bg-surface);
  color: var(--text-secondary);
  border-bottom: 1px solid var(--border-color);
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
  transition: background var(--transition-fast);
  position: relative;
}

.col-header:hover {
  background: var(--bg-hover);
}

.col-header.sorted {
  color: var(--accent-teal);
}

.header-content {
  display: flex;
  align-items: center;
  gap: 4px;
}

.col-name {
  font-weight: 500;
}

.col-type {
  display: block;
  font-size: 10px;
  color: var(--text-tertiary);
  font-weight: 400;
  margin-top: 1px;
}

.sensitive-icon {
  font-size: 11px;
  line-height: 1;
}

.sort-arrow {
  font-size: 10px;
  color: var(--accent-teal);
  margin-left: 2px;
}

/* 数据行 */
.data-row {
  transition: background var(--transition-fast);
}

.data-row:hover {
  background: var(--bg-hover);
}

.data-row:nth-child(even) {
  background: rgba(255, 255, 255, 0.015);
}

.data-row:nth-child(even):hover {
  background: var(--bg-hover);
}

/* 数据单元格 */
.data-cell {
  padding: 3px 10px;
  border-bottom: 1px solid rgba(30, 41, 59, 0.5);
  color: var(--text-primary);
  white-space: nowrap;
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.data-cell.sensitive {
  display: flex;
  align-items: center;
  gap: 4px;
}

.masked-value {
  color: var(--text-tertiary);
  font-weight: 500;
}

/* 👁 眼睛按钮 */
.eye-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  border-radius: 3px;
  padding: 0;
  flex-shrink: 0;
  transition: all var(--transition-fast);
}

.eye-btn:hover {
  background: rgba(45, 212, 191, 0.1);
  color: var(--accent-teal);
}

.reveal-badge {
  font-size: 13px;
  opacity: 0.7;
}

/* 空行 */
.empty-row:hover {
  background: transparent;
}

.empty-cell {
  padding: 24px;
  text-align: center;
  color: var(--text-tertiary);
  font-size: 13px;
  font-family: var(--font-body);
}

/* ─── 分页 ─── */
.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 2px;
  padding: 8px 12px;
  border-top: 1px solid var(--border-color);
  background: var(--bg-surface);
}

.page-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 26px;
  height: 26px;
  padding: 0 6px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-secondary);
  font-family: var(--font-mono);
  font-size: 12px;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.page-btn:hover:not(:disabled):not(.active) {
  border-color: var(--accent-teal);
  color: var(--accent-teal);
}

.page-btn.active {
  background: var(--accent-teal);
  border-color: var(--accent-teal);
  color: #0B0E14;
  font-weight: 600;
}

.page-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.page-ellipsis {
  padding: 0 4px;
  color: var(--text-tertiary);
  font-size: 12px;
}

.page-info {
  margin-left: 8px;
  font-size: 11px;
  color: var(--text-tertiary);
  font-family: var(--font-mono);
}

/* ─── 表头操作区 ─── */
.info-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 导出按钮 */
.export-wrapper {
  position: relative;
}

.export-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-tertiary);
  font-family: var(--font-body);
  font-size: 11px;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.export-btn:hover {
  border-color: var(--accent-teal);
  color: var(--accent-teal);
  background: rgba(45, 212, 191, 0.06);
}

/* 下拉菜单 */
.export-dropdown {
  position: absolute;
  top: 100%;
  right: 0;
  margin-top: 4px;
  background: var(--bg-elevated);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  overflow: hidden;
  z-index: 50;
  min-width: 120px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
}

.dropdown-item {
  display: block;
  width: 100%;
  padding: 6px 14px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-family: var(--font-body);
  font-size: 12px;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s;
}

.dropdown-item:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

/* ═══ 导出对话框 ═══ */
.export-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.export-dialog {
  background: var(--bg-elevated);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  width: 400px;
  max-width: 90vw;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4);
}

.dialog-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border-color);
}

.dialog-title {
  font-family: var(--font-display);
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.dialog-close {
  width: 24px;
  height: 24px;
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  transition: all 0.15s;
}

.dialog-close:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.dialog-body {
  padding: 12px 16px;
  flex: 1;
  overflow-y: auto;
}

.dialog-desc {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-bottom: 10px;
}

.column-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.column-item {
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  transition: background 0.15s;
}

.column-item:hover {
  background: var(--bg-hover);
}

.column-item.sensitive {
  background: rgba(248, 113, 113, 0.04);
}

.col-label {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  font-size: 13px;
  color: var(--text-primary);
}

.col-label input[type="checkbox"] {
  accent-color: var(--accent-teal);
}

.col-check-name {
  font-family: var(--font-mono);
  font-size: 12px;
}

.col-lock {
  font-size: 11px;
  line-height: 1;
}

.sensitive-tag {
  font-size: 10px;
  color: var(--color-error);
  padding: 0 4px;
  border: 1px solid rgba(248, 113, 113, 0.25);
  border-radius: 3px;
}

.sensitive-warning {
  margin-top: 8px;
  padding: 6px 10px;
  background: rgba(251, 191, 36, 0.08);
  border: 1px solid rgba(251, 191, 36, 0.2);
  border-radius: var(--radius-sm);
  font-size: 11px;
  color: var(--color-warning);
}

/* 对话框底部 */
.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px 16px;
  border-top: 1px solid var(--border-color);
}

.footer-btn {
  padding: 6px 16px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  font-family: var(--font-body);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}

.footer-btn.cancel {
  background: transparent;
  color: var(--text-tertiary);
}

.footer-btn.cancel:hover {
  background: var(--bg-hover);
  color: var(--text-secondary);
}

.footer-btn.confirm {
  background: var(--accent-teal);
  border-color: var(--accent-teal);
  color: #0B0E14;
  font-weight: 600;
}

.footer-btn.confirm:hover:not(:disabled) {
  opacity: 0.9;
}

.footer-btn.confirm:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* 过渡动画 */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.15s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.modal-fade-enter-active,
.modal-fade-leave-active {
  transition: opacity 0.2s ease;
}
.modal-fade-enter-active .export-dialog,
.modal-fade-leave-active .export-dialog {
  transition: transform 0.2s ease;
}
.modal-fade-enter-from,
.modal-fade-leave-to {
  opacity: 0;
}
.modal-fade-enter-from .export-dialog,
.modal-fade-leave-to .export-dialog {
  transform: scale(0.95);
}
</style>
