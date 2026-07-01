<script setup lang="ts">
/**
 * TroubleshootSteps — 故障排查步骤组件
 *
 * 排查步骤编号列表（时间线样式）+ 展开 detail + 结论区 + 破坏性建议确认。
 *
 * 依据 api-contract §2.6 TroubleshootResult
 *     frontend AGENTS.md §1（KILL 等破坏性诊断建议需确认按钮）
 */
import { ref } from 'vue'
import { NButton } from 'naive-ui'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import type { TroubleshootStep, IssueType } from '@/types/report'

const props = defineProps<{
  /** 排查步骤 */
  steps: TroubleshootStep[]
  /** 结论文本 */
  conclusion: string
  /** 结论严重级别 */
  severity: string
  /** 建议 */
  suggestion?: string
  /** 建议是否破坏性 */
  suggestionIsDestructive?: boolean
  /** 问题类型 */
  issueType?: IssueType
}>()

/** 步骤状态图标映射 */
const statusIcons: Record<string, string> = {
  pass: '✅',
  warning: '⚠️',
  error: '❌',
  skip: '⏭️',
  running: '⏳',
}

/** severity 结论颜色配置 */
const severityColors: Record<string, { bg: string; border: string; text: string }> = {
  error: {
    bg: 'rgba(248, 113, 113, 0.08)',
    border: 'rgba(248, 113, 113, 0.25)',
    text: 'var(--color-error)',
  },
  warning: {
    bg: 'rgba(251, 191, 36, 0.08)',
    border: 'rgba(251, 191, 36, 0.25)',
    text: 'var(--color-warning)',
  },
  info: {
    bg: 'rgba(96, 165, 250, 0.08)',
    border: 'rgba(96, 165, 250, 0.25)',
    text: 'var(--color-info)',
  },
}

/** 每个步骤的展开状态 */
const expandedSteps = ref<boolean[]>(props.steps.map(() => false))

function toggleStep(index: number): void {
  expandedSteps.value[index] = !expandedSteps.value[index]
}

/** 格式化 detail 为 JSON 字符串 */
function formatDetail(detail: Record<string, unknown> | null): string {
  if (!detail) return ''
  try {
    return JSON.stringify(detail, null, 2)
  } catch {
    return String(detail)
  }
}

/** 攻击性建议确认对话框 */
const destructiveDialog = ref<{ visible: boolean; sql: string }>({
  visible: false,
  sql: props.suggestion ?? '',
})

function openConfirm(): void {
  destructiveDialog.value = { visible: true, sql: props.suggestion ?? '' }
}

function closeConfirm(): void {
  destructiveDialog.value = { visible: false, sql: '' }
}

function handleConfirm(): void {
  closeConfirm()
}

/** 排查时间线动画：完成时步骤依次亮起 */
const animationPlayed = ref(true)
</script>

<template>
  <div class="troubleshoot-steps">
    <!-- 标题 -->
    <div class="ts-header">
      <span class="ts-icon">🔍</span>
      <span class="ts-title">故障排查</span>
      <span v-if="issueType" class="ts-issue">{{ issueType }}</span>
    </div>

    <!-- 步骤时间线 -->
    <div class="timeline">
      <div
        v-for="(step, si) in steps"
        :key="si"
        class="timeline-step"
        :class="[
          `status-${step.status}`,
          { 'anim-enter': animationPlayed },
        ]"
        :style="{ animationDelay: `${si * 0.12}s` }"
      >
        <!-- 时间线左侧 -->
        <div class="timeline-left">
          <div class="step-dot" :class="`dot-${step.status}`">
            <span class="dot-icon">{{ statusIcons[step.status] || '⏳' }}</span>
          </div>
          <div v-if="si < steps.length - 1" class="step-line"></div>
        </div>

        <!-- 步骤内容 -->
        <div class="timeline-content">
          <div class="step-header" :class="{ clickable: step.detail }" @click="toggleStep(si)">
            <div class="step-info">
              <span class="step-order">步骤 {{ si + 1 }}</span>
              <span class="step-tool">{{ step.tool }}</span>
              <span v-if="step.duration_ms !== undefined" class="step-duration">
                {{ step.duration_ms }}ms
              </span>
            </div>
            <div class="step-summary">{{ step.display || step.summary }}</div>
            <button
              v-if="step.detail"
              class="expand-btn"
              :class="{ expanded: expandedSteps[si] }"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
                <polyline points="6 9 12 15 18 9"/>
              </svg>
              {{ expandedSteps[si] ? '收起详情' : '查看详情' }}
            </button>
          </div>

          <!-- 展开的 detail -->
          <Transition name="fade-slide">
            <div v-if="expandedSteps[si] && step.detail" class="step-detail">
              <pre class="detail-json"><code>{{ formatDetail(step.detail) }}</code></pre>
            </div>
          </Transition>
        </div>
      </div>
    </div>

    <!-- 结论区 -->
    <div
      class="conclusion-section"
      :style="{
        background: severityColors[severity]?.bg || severityColors.info.bg,
        borderColor: severityColors[severity]?.border || severityColors.info.border,
      }"
    >
      <div class="conclusion-header">
        <span class="conclusion-icon" :style="{ color: severityColors[severity]?.text || severityColors.info.text }">
          {{ severity === 'error' ? '🛑' : severity === 'warning' ? '⚠️' : 'ℹ️' }}
        </span>
        <span class="conclusion-label" :style="{ color: severityColors[severity]?.text || severityColors.info.text }">
          排查结论
        </span>
      </div>
      <p class="conclusion-text">{{ conclusion }}</p>

      <!-- 建议按钮 -->
      <div v-if="suggestion" class="suggestion-action">
        <template v-if="suggestionIsDestructive">
          <n-button type="error" size="tiny" @click="openConfirm">
            <template #icon>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <polygon points="5 3 19 12 5 21 5 3"/>
              </svg>
            </template>
            执行建议（危险操作）
          </n-button>
        </template>
        <div v-else class="suggestion-text">{{ suggestion }}</div>
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
.troubleshoot-steps {
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  overflow: hidden;
}

/* ─── 头部 ─── */
.ts-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border-color);
}

.ts-icon {
  font-size: 14px;
}

.ts-title {
  font-family: var(--font-display);
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.ts-issue {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--accent-blue);
  margin-left: auto;
  padding: 1px 6px;
  border: 1px solid rgba(56, 189, 248, 0.2);
  border-radius: 3px;
}

/* ─── 时间线 ─── */
.timeline {
  padding: 8px 0;
}

.timeline-step {
  display: flex;
  padding: 0 14px;
  opacity: 0;
  transform: translateY(6px);
}

.timeline-step.anim-enter {
  animation: step-enter 0.35s ease forwards;
}

@keyframes step-enter {
  to { opacity: 1; transform: translateY(0); }
}

/* 左侧 */
.timeline-left {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 28px;
  flex-shrink: 0;
  padding-top: 2px;
}

.step-dot {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-surface);
  border: 2px solid var(--border-color);
  z-index: 1;
}

.dot-icon {
  font-size: 11px;
  line-height: 1;
}

.step-line {
  width: 2px;
  flex: 1;
  min-height: 16px;
  background: var(--border-color);
  margin: 2px 0;
}

/* 状态特定样式 */
.dot-pass { border-color: var(--color-success); }
.dot-warning { border-color: var(--color-warning); }
.dot-error { border-color: var(--color-error); }
.dot-skip { border-color: var(--text-tertiary); opacity: 0.5; }
.dot-running { border-color: var(--accent-blue); animation: dot-pulse 1.5s ease-in-out infinite; }

@keyframes dot-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(56, 189, 248, 0.4); }
  50% { box-shadow: 0 0 0 4px rgba(56, 189, 248, 0); }
}

/* 右侧内容 */
.timeline-content {
  flex: 1;
  padding-left: 10px;
  padding-bottom: 12px;
  min-width: 0;
}

.step-header {
  padding: 4px 0;
}

.step-header.clickable {
  cursor: pointer;
}

.step-info {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 2px;
}

.step-order {
  font-size: 10px;
  color: var(--text-tertiary);
  font-weight: 500;
}

.step-tool {
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 500;
  color: var(--accent-blue);
}

.step-duration {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-tertiary);
  margin-left: auto;
}

.step-summary {
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.4;
}

/* 展开按钮 */
.expand-btn {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  margin-top: 4px;
  padding: 1px 6px;
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  font-family: var(--font-body);
  font-size: 11px;
  cursor: pointer;
  transition: color var(--transition-fast);
}

.expand-btn:hover {
  color: var(--text-secondary);
}

.expand-btn svg {
  transition: transform 0.2s;
}

.expand-btn.expanded svg {
  transform: rotate(180deg);
}

/* detail JSON */
.step-detail {
  margin-top: 6px;
}

.detail-json {
  margin: 0;
  padding: 8px 10px;
  background: #0D1117;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-secondary);
  overflow-x: auto;
  max-height: 200px;
  overflow-y: auto;
}

/* ─── 结论区 ─── */
.conclusion-section {
  padding: 10px 14px;
  border-top: 1px solid var(--border-color);
}

.conclusion-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}

.conclusion-icon {
  font-size: 14px;
}

.conclusion-label {
  font-family: var(--font-display);
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}

.conclusion-text {
  font-size: 13px;
  color: var(--text-primary);
  line-height: 1.5;
}

/* 建议 */
.suggestion-action {
  margin-top: 8px;
}

.suggestion-text {
  font-size: 12px;
  color: var(--text-secondary);
  padding: 6px 10px;
  background: var(--bg-surface);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
}

/* ─── 过渡动画 ─── */
.fade-slide-enter-active,
.fade-slide-leave-active {
  transition: all 0.2s ease;
}

.fade-slide-enter-from,
.fade-slide-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
