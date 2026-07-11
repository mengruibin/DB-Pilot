<script setup lang="ts">
/**
 * ReportView — 健康巡检页面
 *
 * 集成巡检触发、SSE 实时进度、评分展示、分类检查项、历史记录。
 * 无连接时引导用户先建立连接。
 *
 * 依据 api-contract §1.4 健康巡检 SSE + §2.5 HealthReport
 *     frontend AGENTS.md §1（巡检进度逐项更新）
 */
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useConnectionStore } from '@/stores/connection'
import { useReportStore } from '@/stores/report'
import { useThemeStore } from '@/stores/theme'
import HealthScore from '@/components/report/HealthScore.vue'
import CheckItemList from '@/components/report/CheckItemList.vue'
import { NButton, NSpin } from 'naive-ui'

const router = useRouter()
const connStore = useConnectionStore()
const themeStore = useThemeStore()

/** 浅色主题下 ghost 按钮用浅蓝，深色主题用默认主题色 */
const ghostBtnColor = computed(() => (themeStore.isDark ? undefined : '#3B82F6'))

const reportStore = useReportStore()

// ─── 视图状态 ───

/** 是否展开历史面板 */
const showHistory = ref(false)

/** 评分动画完成标记 */
const scoreAnimated = ref(false)

function onScoreAnimated(): void {
  scoreAnimated.value = true
}

// ─── 辅助函数（模板内使用） ───

function scoreClass(score: number): string {
  if (score >= 80) return 'sc-excellent'
  if (score >= 60) return 'sc-fair'
  return 'sc-poor'
}

function formatHistoryTime(iso: string): string {
  try {
    const d = new Date(iso)
    const month = String(d.getMonth() + 1).padStart(2, '0')
    const day = String(d.getDate()).padStart(2, '0')
    const hour = String(d.getHours()).padStart(2, '0')
    const min = String(d.getMinutes()).padStart(2, '0')
    return `${month}-${day} ${hour}:${min}`
  } catch {
    return iso
  }
}

// ─── 摘要计算 ───

function severitySummary() {
  const r = reportStore.currentReport
  if (!r) return { error: 0, warning: 0, pass: 0, skipped: 0 }
  return {
    error: r.severity_counts?.error ?? 0,
    warning: r.severity_counts?.warning ?? 0,
    pass: r.severity_counts?.pass ?? 0,
    skipped: r.severity_counts?.skipped ?? 0,
  }
}

function formattedTime(): string {
  const r = reportStore.currentReport
  if (!r?.generated_at) return ''
  try {
    const d = new Date(r.generated_at)
    return d.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return r.generated_at
  }
}

// ─── 巡检操作 ───

async function handleStartCheck(): Promise<void> {
  if (!connStore.activeId) return
  scoreAnimated.value = false
  showHistory.value = false
  await reportStore.startHealthCheck(connStore.activeId)
  // 完成后刷新历史
  reportStore.fetchHistory()
}

function handleCancel(): void {
  reportStore.cancelHealthCheck()
}

function handleViewReport(id: string): void {
  showHistory.value = false
  reportStore.fetchReport(id)
}

// ─── 生命周期 ───

onMounted(() => {
  reportStore.fetchHistory()
})
</script>

<template>
  <div class="report-view">
    <!-- ─── 顶栏 ─── -->
    <header class="report-header">
      <div class="header-left">
        <h1 class="page-title">健康巡检</h1>
        <span v-if="connStore.activeId && connStore.activeConnection" class="header-connection">
          {{ connStore.activeConnection.name }}
        </span>
      </div>
      <div class="header-actions">
        <n-button
          v-if="connStore.activeId"
          :disabled="reportStore.isRunning"
          :color="ghostBtnColor"
          size="small"
          ghost
          class="check-btn"
          @click="handleStartCheck"
        >
          <template #icon>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <polygon points="5 3 19 12 5 21 5 3"/>
            </svg>
          </template>
          {{ reportStore.isRunning ? '巡检中...' : '开始巡检' }}
        </n-button>
        <n-button
          v-if="reportStore.historyReports.length > 0"
          size="small"
          quaternary
          class="history-toggle"
          @click="showHistory = !showHistory"
        >
          <template #icon>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="10"/>
              <polyline points="12 6 12 12 16 14"/>
            </svg>
          </template>
          历史记录 ({{ reportStore.historyTotal }})
        </n-button>
      </div>
    </header>

    <!-- ─── 主内容区 ─── -->
    <div class="report-content">
      <!-- 未连接引导 -->
      <div v-if="!connStore.activeId" class="welcome-state">
        <div class="welcome-content">
          <div class="welcome-icon">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <path d="M22 12h-4l-3 9L9 3l-3 9H2"/>
            </svg>
          </div>
          <h2 class="welcome-title">连接数据库以开始巡检</h2>
          <p class="welcome-desc">
            健康巡检帮你全面评估数据库运行状态，识别潜在风险。
          </p>
          <n-button type="primary" ghost @click="router.push('/connections')">
            前往连接管理
          </n-button>
        </div>
      </div>

      <!-- 已连接 -->
      <template v-else>
        <!-- 加载中 -->
        <div v-if="reportStore.loadingReport" class="loading-state">
          <n-spin size="small" />
          <span class="loading-text">加载报告中...</span>
        </div>

        <!-- ═══ SSE 巡检进行中 ═══ -->
        <div v-else-if="reportStore.isRunning" class="progress-panel">
          <div class="progress-header">
            <span class="progress-title">🔍 健康巡检进行中</span>
            <n-button size="tiny" quaternary class="cancel-btn" @click="handleCancel">
              取消
            </n-button>
          </div>

          <!-- 进度条 -->
          <div class="progress-track">
            <div
              class="progress-fill"
              :style="{ width: `${reportStore.progressPercent}%` }"
            ></div>
          </div>
          <div class="progress-info">
            <span class="progress-count">
              第 {{ reportStore.progressCurrent }}/{{ reportStore.progressTotal }} 项
            </span>
            <span class="progress-pct">{{ reportStore.progressPercent }}%</span>
          </div>
          <p class="progress-current">
            正在检查：<strong>{{ reportStore.currentItemName }}</strong>
          </p>

          <!-- 进度条目列表 -->
          <div class="progress-items">
            <div
              v-for="(item, pi) in reportStore.progressItems"
              :key="pi"
              class="progress-item"
              :class="`pstatus-${item.status}`"
            >
              <span class="pitem-icon">
                <template v-if="item.status === 'pass'">✅</template>
                <template v-else-if="item.status === 'warning'">⚠️</template>
                <template v-else-if="item.status === 'error'">❌</template>
                <template v-else>⏳</template>
              </span>
              <span class="pitem-name">{{ item.name }}</span>
            </div>
          </div>

          <!-- 待检查项占位 -->
          <div v-if="reportStore.progressTotal > 0" class="progress-placeholder">
            <div
              v-for="n in Math.max(0, reportStore.progressTotal - reportStore.progressItems.length)"
              :key="'ph-' + n"
              class="placeholder-item"
            >
              <span class="placeholder-dot"></span>
              <span class="placeholder-text">等待检查...</span>
            </div>
          </div>
        </div>

        <!-- ═══ 报告就绪 ═══ -->
        <div v-else-if="reportStore.hasReport" class="report-ready">
          <!-- 评分 + 摘要两列布局 -->
          <div class="report-overview">
            <div class="overview-score">
              <HealthScore
                :score="reportStore.currentReport!.score"
                :size="160"
                :stroke-width="10"
                @animated="onScoreAnimated"
              />
            </div>
            <div class="overview-summary">
              <h3 class="summary-title">检查摘要</h3>
              <div class="summary-stats">
                <div class="stat-block pass">
                  <span class="stat-count">{{ severitySummary().pass }}</span>
                  <span class="stat-label">通过</span>
                </div>
                <div class="stat-block warn">
                  <span class="stat-count">{{ severitySummary().warning }}</span>
                  <span class="stat-label">警告</span>
                </div>
                <div class="stat-block error">
                  <span class="stat-count">{{ severitySummary().error }}</span>
                  <span class="stat-label">错误</span>
                </div>
                <div class="stat-block skip">
                  <span class="stat-count">{{ severitySummary().skipped }}</span>
                  <span class="stat-label">跳过</span>
                </div>
              </div>
              <div class="summary-meta">
                <span class="meta-time">{{ formattedTime() }}</span>
                <span v-if="reportStore.currentReport?.duration_sec" class="meta-duration">
                  耗时 {{ reportStore.currentReport.duration_sec }}s
                </span>
              </div>
            </div>
          </div>

          <!-- 分类检查项 -->
          <div class="report-categories">
            <CheckItemList :categories="reportStore.reportCategories" />
          </div>
        </div>

        <!-- ═══ 空闲状态（无报告，未在巡检） ═══ -->
        <div v-else class="idle-state">
          <div class="idle-content">
            <div class="idle-icon">
              <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                <path d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
              </svg>
            </div>
            <h3 class="idle-title">数据库状态检查</h3>
            <p class="idle-desc">
              点击「开始巡检」对当前数据库进行全面的健康评估。
            </p>
            <n-button type="primary" ghost :color="ghostBtnColor" class="start-btn" @click="handleStartCheck">
              <template #icon>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                  <polygon points="5 3 19 12 5 21 5 3"/>
                </svg>
              </template>
              开始巡检
            </n-button>
          </div>
        </div>

        <!-- ═══ SSE 错误 ═══ -->
        <div v-if="reportStore.sseError" class="sse-error">
          <span class="error-icon">❌</span>
          <span class="error-text">{{ reportStore.sseError }}</span>
        </div>

        <!-- ═══ 历史记录面板 ═══ -->
        <Transition name="slide-up">
          <div v-if="showHistory && reportStore.historyReports.length > 0" class="history-panel">
            <div class="history-header">
              <h3 class="history-title">📜 历史报告 ({{ reportStore.historyTotal }})</h3>
              <n-button size="tiny" quaternary @click="showHistory = false">
                收起
              </n-button>
            </div>
            <div class="history-list">
              <div
                v-for="h in reportStore.sortedHistory"
                :key="h.id"
                class="history-row"
                :class="{ 'score-low': h.score < 60 }"
                @click="handleViewReport(h.id)"
              >
                <span class="h-score" :class="scoreClass(h.score)">
                  {{ h.score }}
                </span>
                <span class="h-time">{{ formatHistoryTime(h.generated_at) }}</span>
                <span class="h-stats">
                  {{ h.severity_counts?.pass ?? 0 }}✅ {{ h.severity_counts?.warning ?? 0 }}⚠️ {{ h.severity_counts?.error ?? 0 }}❌
                </span>
                <span class="h-arrow">→</span>
              </div>
            </div>
            <div v-if="reportStore.loadingHistory" class="history-loading">
              <n-spin size="small" />
            </div>
          </div>
        </Transition>
      </template>
    </div>
  </div>
</template>

<style scoped>
.report-view {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* ─── 顶栏 ─── */
.report-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.page-title {
  font-family: var(--font-display);
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.header-connection {
  font-size: 12px;
  color: var(--interactive-color);
  padding: 2px 8px;
  border: 1px solid var(--accent-soft-border);
  border-radius: 4px;
  font-family: var(--font-mono);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.history-toggle {
  --n-text-color: var(--text-tertiary);
  --n-text-color-hover: var(--text-secondary);
}

/* ─── 内容区 ─── */
.report-content {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px;
}

/* ─── 未连接引导 ─── */
.welcome-state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
}

.welcome-content {
  text-align: center;
  max-width: 380px;
}

.welcome-icon {
  color: var(--text-tertiary);
  margin-bottom: 16px;
  opacity: 0.5;
}

.welcome-title {
  font-family: var(--font-display);
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 8px;
}

.welcome-desc {
  font-size: 14px;
  color: var(--text-tertiary);
  margin-bottom: 20px;
  line-height: 1.5;
}

/* ─── 加载中 ─── */
.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 60px 20px;
}

.loading-text {
  font-size: 13px;
  color: var(--text-tertiary);
}

/* ═══ 进度面板 ═══ */
.progress-panel {
  background: var(--bg-elevated);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  padding: 16px 20px;
  max-width: 560px;
  margin: 20px auto;
}

.progress-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.progress-title {
  font-family: var(--font-display);
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.cancel-btn {
  --n-text-color: var(--text-tertiary);
  --n-text-color-hover: var(--color-error);
}

/* 进度条 */
.progress-track {
  width: 100%;
  height: 4px;
  background: rgba(255, 255, 255, 0.06);
  border-radius: 4px;
  overflow: hidden;
  margin-bottom: 6px;
}

.progress-fill {
  height: 100%;
  background: var(--interactive-color);
  border-radius: 4px;
  transition: width 0.3s ease;
}

.progress-info {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--text-tertiary);
  margin-bottom: 4px;
}

.progress-count {
  font-family: var(--font-mono);
}

.progress-pct {
  font-family: var(--font-mono);
  color: var(--interactive-color);
}

.progress-current {
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 12px;
}

.progress-current strong {
  color: var(--text-primary);
}

/* 进度条目 */
.progress-items {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.progress-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  animation: pi-enter 0.3s ease both;
}

@keyframes pi-enter {
  from { opacity: 0; transform: translateX(-8px); }
  to { opacity: 1; transform: translateX(0); }
}

.pitem-icon {
  font-size: 12px;
  width: 18px;
  text-align: center;
}

.pitem-name {
  font-size: 12px;
  color: var(--text-secondary);
}

.pstatus-pass .pitem-name { color: var(--color-success); }
.pstatus-warning .pitem-name { color: var(--color-warning); }
.pstatus-error .pitem-name { color: var(--color-error); }

/* 待检查项占位 */
.progress-placeholder {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 4px;
}

.placeholder-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  opacity: 0.3;
}

.placeholder-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-tertiary);
  margin-left: 6px;
}

.placeholder-text {
  font-size: 12px;
  color: var(--text-tertiary);
}

/* ═══ 报告就绪 ═══ */
.report-ready {
  max-width: 960px;
  margin: 0 auto;
}

/* 评分 + 摘要 */
.report-overview {
  display: flex;
  gap: 24px;
  align-items: center;
  padding: 20px 24px;
  background: var(--bg-elevated);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  margin-bottom: 16px;
}

.overview-score {
  flex-shrink: 0;
}

.overview-summary {
  flex: 1;
  min-width: 0;
}

.summary-title {
  font-family: var(--font-display);
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 12px;
}

.summary-stats {
  display: flex;
  gap: 12px;
  margin-bottom: 10px;
}

.stat-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 8px 16px;
  border-radius: var(--radius-md);
  min-width: 64px;
}

.stat-block.pass  { background: rgba(52, 211, 153, 0.08); }
.stat-block.warn  { background: rgba(251, 191, 36, 0.08); }
.stat-block.error { background: rgba(248, 113, 113, 0.08); }
.stat-block.skip  { background: rgba(107, 114, 128, 0.08); }

.stat-count {
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 700;
  line-height: 1.2;
}

.stat-block.pass  .stat-count { color: #10B981; }
.stat-block.warn  .stat-count { color: var(--color-warning); }
.stat-block.error .stat-count { color: var(--color-error); }
.stat-block.skip  .stat-count { color: var(--text-tertiary); }

.stat-label {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 2px;
}

.summary-meta {
  display: flex;
  gap: 12px;
  font-size: 11px;
  color: var(--text-tertiary);
  font-family: var(--font-mono);
}

/* 分类检查项 */
.report-categories {
  margin-top: 8px;
}

/* ═══ 空闲状态 ═══ */
.idle-state {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 300px;
}

.idle-content {
  text-align: center;
}

.idle-icon {
  color: var(--text-tertiary);
  opacity: 0.4;
  margin-bottom: 12px;
}

.idle-title {
  font-family: var(--font-display);
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 6px;
}

.idle-desc {
  font-size: 13px;
  color: var(--text-tertiary);
  margin-bottom: 16px;
}

/* ═══ SSE 错误 ═══ */
.sse-error {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  margin-top: 12px;
  padding: 8px 14px;
  background: rgba(248, 113, 113, 0.08);
  border: 1px solid rgba(248, 113, 113, 0.2);
  border-radius: var(--radius-md);
  color: var(--color-error);
  font-size: 13px;
}

/* ═══ 历史面板 ═══ */
.history-panel {
  margin-top: 20px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  overflow: hidden;
  background: var(--bg-elevated);
}

.history-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border-color);
}

.history-title {
  font-family: var(--font-display);
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.history-list {
  display: flex;
  flex-direction: column;
}

.history-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--border-color);
  cursor: pointer;
  transition: background 0.15s;
}

.history-row:last-child {
  border-bottom: none;
}

.history-row:hover {
  background: var(--bg-hover);
}

.history-row.score-low {
  background: rgba(248, 113, 113, 0.04);
}

.history-row.score-low:hover {
  background: rgba(248, 113, 113, 0.08);
}

.h-score {
  font-family: var(--font-display);
  font-size: 16px;
  font-weight: 700;
  width: 36px;
  text-align: center;
}

.sc-excellent { color: #10B981; }
.sc-fair { color: var(--color-warning); }
.sc-poor { color: var(--color-error); }

.h-time {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-tertiary);
  flex-shrink: 0;
}

.h-stats {
  font-size: 12px;
  color: var(--text-secondary);
  flex: 1;
}

.h-arrow {
  font-size: 12px;
  color: var(--text-tertiary);
  opacity: 0;
  transition: opacity 0.15s;
}

.history-row:hover .h-arrow {
  opacity: 1;
}

.history-loading {
  display: flex;
  justify-content: center;
  padding: 12px;
}

/* ─── 滑入动画 ─── */
.slide-up-enter-active,
.slide-up-leave-active {
  transition: all 0.25s ease;
}

.slide-up-enter-from,
.slide-up-leave-to {
  opacity: 0;
  transform: translateY(10px);
}
</style>
