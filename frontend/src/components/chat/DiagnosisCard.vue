<script setup lang="ts">
/**
 * DiagnosisCard — 诊断结果卡片组件
 *
 * 渲染 findings 列表，severity=error 优先排序，
 * 破坏性建议带确认执行按钮。
 *
 * 依据 api-contract §2.4 DiagnosisResult
 *     frontend AGENTS.md §1（建议卡片带执行按钮）
 */
import { ref, computed } from 'vue'
import { NButton } from 'naive-ui'
import SqlBlock from '@/components/sql/SqlBlock.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import type { Finding } from '@/types/report'

const props = defineProps<{
  /** 诊断发现列表 */
  findings: Finding[]
  /** 被分析的 SQL（可选） */
  targetSql?: string
}>()

/** severity 排序权重：error=0, warning=1, info=2 */
const sortedFindings = computed(() => {
  const weights: Record<string, number> = { error: 0, warning: 1, info: 2 }
  return [...props.findings].sort(
    (a, b) => (weights[a.severity] ?? 3) - (weights[b.severity] ?? 3)
  )
})

/** 破坏性建议 → 确认对话框状态 */
const destructiveDialog = ref<{ visible: boolean; sql: string }>({
  visible: false,
  sql: '',
})

function openConfirm(suggestion: string): void {
  destructiveDialog.value = { visible: true, sql: suggestion }
}

function closeConfirm(): void {
  destructiveDialog.value = { visible: false, sql: '' }
}

function handleConfirm(): void {
  // TODO: 执行确认回调
  closeConfirm()
}

/** severity 配置 */
const severityConfig: Record<string, { icon: string; label: string; color: string }> = {
  error: { icon: '🔴', label: '严重', color: 'var(--color-error)' },
  warning: { icon: '🟡', label: '警告', color: 'var(--color-warning)' },
  info: { icon: '🔵', label: '提示', color: 'var(--color-info)' },
}
</script>

<template>
  <div class="diagnosis-card">
    <!-- 诊断头部 -->
    <div v-if="targetSql" class="diag-header">
      <span class="diag-label">诊断分析</span>
    </div>

    <!-- Findings 列表 -->
    <div class="findings-list">
      <div
        v-for="(finding, fi) in sortedFindings"
        :key="fi"
        class="finding-item"
        :class="`severity-${finding.severity}`"
      >
        <!-- 左侧色条 -->
        <div
          class="finding-bar"
          :style="{ background: severityConfig[finding.severity]?.color }"
        ></div>

        <div class="finding-body">
          <!-- 标题行 -->
          <div class="finding-header">
            <span class="finding-icon">{{ severityConfig[finding.severity]?.icon }}</span>
            <span class="finding-title">{{ finding.title }}</span>
            <span
              class="finding-severity"
              :style="{ color: severityConfig[finding.severity]?.color }"
            >
              {{ severityConfig[finding.severity]?.label }}
            </span>
          </div>

          <!-- 详情 -->
          <p class="finding-detail">{{ finding.detail }}</p>

          <!-- 预期改善 -->
          <p v-if="finding.estimated_improvement" class="improvement-text">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/>
              <polyline points="17 6 23 6 23 12"/>
            </svg>
            {{ finding.estimated_improvement }}
          </p>

          <!-- 建议 -->
          <div v-if="finding.suggestion" class="suggestion-area">
            <div v-if="finding.is_destructive" class="suggestion-card danger">
              <div class="suggestion-header">
                <span class="suggestion-label">建议操作</span>
                <span class="suggestion-badge destructive">破坏性操作</span>
              </div>
              <SqlBlock
                :sql="finding.suggestion"
                :is-readonly="false"
              />
              <div class="suggestion-footer">
                <n-button
                  type="error"
                  size="tiny"
                  @click="openConfirm(finding.suggestion)"
                >
                  <template #icon>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                      <polygon points="5 3 19 12 5 21 5 3"/>
                    </svg>
                  </template>
                  执行此建议
                </n-button>
              </div>
            </div>

            <div v-else class="suggestion-block">
              <SqlBlock :sql="finding.suggestion" :is-readonly="true" />
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 确认对话框 -->
    <ConfirmDialog
      :show="destructiveDialog.visible"
      :sql="destructiveDialog.sql"
      :is-readonly="false"
      @update:show="closeConfirm"
      @confirm="handleConfirm"
      @cancel="closeConfirm"
    />
  </div>
</template>

<style scoped>
.diagnosis-card {
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  overflow: hidden;
}

/* ─── 头部 ─── */
.diag-header {
  padding: 6px 12px;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border-color);
}

.diag-label {
  font-family: var(--font-display);
  font-size: 12px;
  font-weight: 600;
  color: var(--accent-blue);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

/* ─── Findings 列表 ─── */
.findings-list {
  display: flex;
  flex-direction: column;
}

.finding-item {
  display: flex;
  border-bottom: 1px solid var(--border-color);
  position: relative;
}

.finding-item:last-child {
  border-bottom: none;
}

/* 左侧 severity 色条 */
.finding-bar {
  position: absolute;
  left: 0;
  top: 4px;
  bottom: 4px;
  width: 3px;
  border-radius: 0 2px 2px 0;
}

/* 内容区 */
.finding-body {
  flex: 1;
  padding: 10px 12px 10px 20px;
  min-width: 0;
}

.finding-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}

.finding-icon {
  font-size: 13px;
  line-height: 1;
}

.finding-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
}

.finding-severity {
  font-size: 10px;
  font-weight: 500;
  padding: 1px 5px;
  border-radius: 3px;
  background: rgba(255, 255, 255, 0.04);
}

.finding-detail {
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.5;
  margin-bottom: 6px;
}

/* ─── 预期改善 ─── */
.improvement-text {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--color-success);
  margin-bottom: 8px;
  padding: 4px 8px;
  background: rgba(52, 211, 153, 0.06);
  border-radius: var(--radius-sm);
  line-height: 1.4;
}

.improvement-text svg {
  flex-shrink: 0;
}

/* ─── 建议区域 ─── */
.suggestion-area {
  margin-top: 8px;
}

/* 非破坏性建议：纯代码块 */
.suggestion-block {
  border-radius: var(--radius-md);
  overflow: hidden;
}

/* 破坏性建议：带按钮的卡片 */
.suggestion-card {
  border: 1px solid rgba(248, 113, 113, 0.3);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.suggestion-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 10px;
  background: rgba(248, 113, 113, 0.06);
  border-bottom: 1px solid rgba(248, 113, 113, 0.15);
}

.suggestion-label {
  font-size: 11px;
  font-weight: 500;
  color: var(--text-secondary);
}

.suggestion-badge.destructive {
  font-size: 10px;
  font-weight: 500;
  color: var(--color-error);
  padding: 1px 6px;
  border: 1px solid rgba(248, 113, 113, 0.3);
  border-radius: 3px;
}

.suggestion-footer {
  display: flex;
  justify-content: flex-end;
  padding: 6px 10px;
  background: rgba(248, 113, 113, 0.03);
  border-top: 1px solid rgba(248, 113, 113, 0.1);
}
</style>
