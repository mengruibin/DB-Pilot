<script setup lang="ts">
/**
 * MessageBubble — 消息气泡分发组件
 *
 * 根据 message.type 分派到不同渲染组件：
 * text / thinking / tool_call / tool_result / sql / result / error
 *
 * 依据 api-contract §三 MessageBubble 组件
 */
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import type { StoreMessage } from '@/stores/chat'
import SqlBlock from '@/components/sql/SqlBlock.vue'
import ResultTable from '@/components/sql/ResultTable.vue'
import ErrorCard from '@/components/common/ErrorCard.vue'
import DiagnosisCard from '@/components/chat/DiagnosisCard.vue'
import MarkdownRenderer from '@/components/chat/MarkdownRenderer.vue'

const props = defineProps<{
  message: StoreMessage
  /** 是否是最后一条消息（用于打字光标） */
  isLast: boolean
  /** 是否正在流式接收 */
  isStreaming: boolean
}>()

// ─── thinking 计时器 ───

const thinkingExpanded = ref(false)
const thinkingElapsed = ref(0)
let timerInterval: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  if (props.message.type === 'thinking') {
    // 优先使用后端返回的实际耗时（durationMs）作为初始值，
    // 避免 thinking 事件到达流末尾时前端计时器从 0 开始
    if (props.message.durationMs) {
      thinkingElapsed.value = props.message.durationMs
    }
    timerInterval = setInterval(() => {
      thinkingElapsed.value += 100
    }, 100)
  }
})

/** 流式结束时停止计时 */
watch(() => props.isStreaming, (streaming) => {
  if (!streaming && timerInterval) {
    clearInterval(timerInterval)
    timerInterval = null
  }
})

onUnmounted(() => {
  if (timerInterval) clearInterval(timerInterval)
})

/** 格式化耗时 mm:ss.x */
function formatElapsed(ms: number): string {
  const sec = Math.floor(ms / 1000)
  const min = Math.floor(sec / 60)
  const s = sec % 60
  const ds = Math.floor((ms % 1000) / 100)
  return min > 0 ? `${min}m${s}.${ds}s` : `${s}.${ds}s`
}

/** 推理类型中文标签（Agent 架构升级后新增） */
function reasoningTypeLabel(type: string): string {
  const labels: Record<string, string> = {
    planning: '规划中',
    observing: '观察中',
    concluding: '总结中',
    error_recovery: '纠错中',
  }
  return labels[type] || type
}

/** 耗时时间颜色 */
const elapsedColor = computed(() => {
  if (thinkingElapsed.value < 10000) return 'var(--text-tertiary)'
  if (thinkingElapsed.value < 30000) return 'var(--color-warning)'
  return 'var(--color-error)'
})

// ─── 格式化 duration_ms ───

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60000)}m${Math.floor((ms % 60000) / 1000)}s`
}

// ─── 用户消息首字符提取（用于头像占位） ───

const userInitial = computed(() => {
  return props.message.content.charAt(0).toUpperCase()
})

// ─── ResultTable 数据转换 ───

const resultColumns = computed(() => {
  if (!props.message.dataPreview?.columns) return []
  return props.message.dataPreview.columns.map((name) => ({
    name,
    type: 'text',
    is_sensitive: false,
  }))
})

const resultRows = computed(() => {
  return props.message.dataPreview?.rows ?? []
})
</script>

<template>
  <div
    class="message-bubble"
    :class="[
      `role-${message.role}`,
      `type-${message.type}`,
      { 'is-streaming': isStreaming && isLast },
    ]"
  >
    <!-- ─── 用户消息：纯文本 ─── -->
    <template v-if="message.role === 'user' && message.type === 'text'">
      <div class="user-avatar">{{ userInitial }}</div>
      <div class="bubble user-bubble">
        <p class="user-text">{{ message.content }}</p>
      </div>
    </template>

    <!-- ─── 助手回复 ─── -->
    <template v-else>
      <div class="bubble assistant-bubble">
        <!-- === text（Markdown 渲染） === -->
        <template v-if="message.type === 'text'">
          <MarkdownRenderer :content="message.content" />
        </template>

        <!-- === thinking === -->
        <template v-if="message.type === 'thinking'">
          <div class="thinking-block" :class="{ expanded: thinkingExpanded }">
            <button class="thinking-header" @click="thinkingExpanded = !thinkingExpanded">
              <span class="thinking-icon">🧠</span>
              <span class="thinking-title">AI 推理过程</span>
              <span v-if="message.reasoningType" class="thinking-badge" :class="`badge-${message.reasoningType}`">
                {{ reasoningTypeLabel(message.reasoningType) }}
              </span>
              <span class="thinking-timer" :style="{ color: elapsedColor }">
                {{ formatElapsed(thinkingElapsed) }}
              </span>
              <span class="thinking-toggle">{{ thinkingExpanded ? '收起' : '展开' }}</span>
            </button>
            <div v-show="thinkingExpanded" class="thinking-body">
              <p class="thinking-content">{{ message.content }}</p>
            </div>
          </div>
        </template>

        <!-- === tool_call === -->
        <template v-if="message.type === 'tool_call'">
          <div class="tool-step">
            <div class="step-indicator">
              <span v-if="message.stepStatus === 'running'" class="step-spinner"></span>
              <span v-else-if="message.stepStatus === 'done'" class="step-icon done">✅</span>
              <span v-else class="step-icon pending">🔧</span>
            </div>
            <div class="step-body">
              <span class="step-tool">{{ message.tool }}</span>
              <span class="step-desc">{{ message.content }}</span>
            </div>
          </div>
        </template>

        <!-- === tool_result === -->
        <template v-if="message.type === 'tool_result'">
          <div class="tool-step result">
            <div class="step-indicator">
              <span class="step-icon done">✅</span>
            </div>
            <div class="step-body">
              <span class="step-tool">{{ message.tool }}</span>
              <MarkdownRenderer class="tool-result-content" :content="message.content" />
              <span v-if="message.durationMs !== undefined" class="step-duration">
                {{ formatDuration(message.durationMs) }}
              </span>
            </div>
          </div>
        </template>

        <!-- === sql（F-08 SqlBlock 组件）=== -->
        <template v-if="message.type === 'sql'">
          <SqlBlock
            :sql="message.sqlContent || message.content"
            :audit-status="message.auditStatus"
            :is-readonly="message.isReadonly"
          />
        </template>

        <!-- === result（F-09 ResultTable 组件 / 纯文本回退）=== -->
        <template v-if="message.type === 'result'">
          <!-- 有结构化数据（dataPreview）→ ResultTable -->
          <ResultTable
            v-if="resultColumns.length > 0 || resultRows.length > 0"
            :columns="resultColumns"
            :rows="resultRows"
            :total-rows="message.totalRows ?? resultRows.length"
            :execution-time-ms="message.executionTimeMs"
          />
          <!-- 纯文本回答（无 dataPreview）→ Markdown 渲染 -->
          <MarkdownRenderer v-else :content="message.summary || message.content" />
        </template>

        <!-- === error（F-12 ErrorCard 组件）=== -->
        <template v-if="message.type === 'error'">
          <ErrorCard
            :severity="(message.severity as 'info' | 'warning' | 'error') ?? 'error'"
            :error-code="message.errorCode"
            :user-message="message.userMessage"
            :content="message.content"
          />
        </template>

        <!-- === diagnosis（F-16 DiagnosisCard 组件）=== -->
        <template v-if="message.type === 'diagnosis' && message.findings">
          <DiagnosisCard
            :findings="message.findings"
            :target-sql="message.targetSql"
          />
        </template>

        <!-- 打字光标（最后一条流式消息） -->
        <span
          v-if="isStreaming && isLast"
          class="cursor-blink"
        ></span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.message-bubble {
  display: flex;
  gap: 10px;
  margin-bottom: 10px;
  animation: msg-enter 0.2s ease both;
}

@keyframes msg-enter {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}

/* ─── 用户消息 ─── */
.role-user {
  flex-direction: row-reverse;
}

.user-avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--accent-teal);
  color: #0B0E14;
  font-size: 12px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  align-self: flex-end;
}

/* ─── 气泡通用 ─── */
.bubble {
  max-width: 80%;
  border-radius: var(--radius-md);
  font-size: 14px;
  line-height: 1.6;
  word-break: break-word;
}

.user-bubble {
  background: rgba(45, 212, 191, 0.1);
  border: 1px solid rgba(45, 212, 191, 0.2);
  padding: 8px 14px;
  border-top-right-radius: 2px;
}

.user-text {
  color: var(--text-primary);
  white-space: pre-wrap;
}

.assistant-bubble {
  background: var(--bg-elevated);
  border: 1px solid var(--border-color);
  padding: 8px 14px;
  border-top-left-radius: 2px;
  max-width: 85%;
}

.assistant-text {
  color: var(--text-primary);
  white-space: pre-wrap;
}

/* ─── 打字光标 ─── */
.cursor-blink {
  display: inline-block;
  width: 2px;
  height: 16px;
  background: var(--accent-teal);
  margin-left: 2px;
  vertical-align: text-bottom;
  animation: blink-cursor 0.8s step-end infinite;
}

@keyframes blink-cursor {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

/* ─── thinking 推理区 ─── */
.thinking-block {
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.thinking-header {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 10px;
  background: var(--bg-surface);
  border: none;
  cursor: pointer;
  color: var(--text-secondary);
  font-family: var(--font-body);
  font-size: 12px;
  text-align: left;
  transition: background var(--transition-fast);
}

.thinking-header:hover {
  background: var(--bg-hover);
}

.thinking-icon {
  font-size: 14px;
}

.thinking-title {
  font-weight: 500;
  color: var(--text-secondary);
}

.thinking-timer {
  font-family: var(--font-mono);
  font-size: 11px;
  margin-left: auto;
  transition: color 0.3s;
}

.thinking-toggle {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-left: 8px;
}

/* reasoning_type 标签（Agent 架构升级后新增） */
.thinking-badge {
  font-size: 10px;
  font-weight: 500;
  padding: 1px 6px;
  border-radius: 8px;
  line-height: 1.4;
  margin-left: 4px;
}

.badge-planning {
  background: rgba(251, 191, 36, 0.15);
  color: var(--color-warning, #fbbf24);
}

.badge-observing {
  background: rgba(52, 211, 153, 0.15);
  color: var(--accent-teal, #34d399);
}

.badge-concluding {
  background: rgba(167, 139, 250, 0.15);
  color: var(--accent-purple, #a78bfa);
}

.badge-error_recovery {
  background: rgba(248, 113, 113, 0.15);
  color: var(--color-error, #f87171);
}

.thinking-body {
  padding: 8px 12px 12px;
  background: var(--bg-surface);
  border-top: 1px solid var(--border-color);
}

.thinking-content {
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.7;
  white-space: pre-wrap;
}

/* ─── tool_call / tool_result ─── */
.tool-step {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  padding: 2px 0;
}

.step-indicator {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: 2px;
}

.step-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid var(--border-color);
  border-top-color: var(--accent-blue);
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.step-icon {
  font-size: 13px;
  line-height: 1;
}

.step-body {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}

.step-tool {
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 500;
  color: var(--accent-blue);
  white-space: nowrap;
}

.step-desc {
  font-size: 13px;
  color: var(--text-secondary);
}

.step-duration {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-tertiary);
  margin-left: auto;
}

.tool-step.result .step-body {
  align-items: center;
}

/* tool_result 内的 Markdown 渲染（紧凑模式） */
.tool-result-content {
  font-size: 13px;
  color: var(--text-secondary);
  display: inline;
}
.tool-result-content :deep(p) {
  display: inline;
  margin: 0;
}
.tool-result-content :deep(p:not(:last-child)::after) {
  content: ' ';
}
.tool-result-content :deep(code):not(.hljs) {
  font-size: 0.85em;
  padding: 1px 5px;
}
.tool-result-content :deep(pre.code-block) {
  margin: 6px 0;
}
.tool-result-content :deep(pre.code-block code.hljs) {
  padding: 8px 12px;
  font-size: 11.5px;
}
.tool-result-content :deep(strong) {
  color: var(--text-primary);
}

/* ─── sql stub ─── */
.sql-block.stub {
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.sql-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 10px;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border-color);
}

.sql-badge {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
  color: var(--accent-teal);
  text-transform: uppercase;
}

.sql-warning {
  font-size: 11px;
  color: var(--color-warning);
}

.sql-rejected {
  font-size: 11px;
  color: var(--color-error);
}

.sql-code {
  padding: 10px 12px;
  margin: 0;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-primary);
  background: #0D1117;
  overflow-x: auto;
  white-space: pre;
}

/* ─── result stub ─── */
.result-block.stub {
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.result-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 10px;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border-color);
}

.result-summary {
  font-size: 13px;
  color: var(--text-secondary);
}

.result-duration {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-tertiary);
}

.result-preview {
  padding: 4px;
  overflow-x: auto;
}

.preview-table {
  width: 100%;
  border-collapse: collapse;
  font-family: var(--font-mono);
  font-size: 11px;
}

.preview-table th {
  background: var(--bg-surface);
  color: var(--text-tertiary);
  padding: 4px 8px;
  text-align: left;
  border-bottom: 1px solid var(--border-color);
  white-space: nowrap;
}

.preview-table td {
  padding: 3px 8px;
  border-bottom: 1px solid var(--border-color);
  color: var(--text-secondary);
  white-space: nowrap;
}

.result-more {
  padding: 4px 8px;
  font-size: 11px;
  color: var(--text-tertiary);
}

/* ─── error stub ─── */
.error-card.stub {
  display: flex;
  gap: 8px;
  padding: 8px 12px;
  border-radius: var(--radius-md);
  border: 1px solid;
}

.severity-error {
  background: rgba(248, 113, 113, 0.08);
  border-color: rgba(248, 113, 113, 0.25);
}

.severity-warning {
  background: rgba(251, 191, 36, 0.08);
  border-color: rgba(251, 191, 36, 0.25);
}

.severity-info {
  background: rgba(96, 165, 250, 0.08);
  border-color: rgba(96, 165, 250, 0.25);
}

.error-icon {
  font-size: 16px;
  flex-shrink: 0;
  margin-top: 1px;
}

.error-body {
  min-width: 0;
}

.error-title {
  font-size: 13px;
  color: var(--text-primary);
  line-height: 1.5;
}

.error-code {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 2px;
}
</style>
