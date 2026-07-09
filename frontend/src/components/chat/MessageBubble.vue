<script setup lang="ts">
/**
 * MessageBubble — 消息气泡分发组件
 *
 * 根据 message.type / message.role 分派渲染，严格遵照
 * DB-Pilot 对话界面设计规范（docs/reasoning-answer-separation-plan.md）。
 *
 * 设计理念：分层卡片布局 ——
 *   用户气泡 → AI 响应卡片（思考面板 → 工具调用 → 最终回答）
 */
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import type { StoreMessage } from '@/stores/chat'
import SqlBlock from '@/components/sql/SqlBlock.vue'
import ResultTable from '@/components/sql/ResultTable.vue'
import ErrorCard from '@/components/common/ErrorCard.vue'
import DiagnosisCard from '@/components/chat/DiagnosisCard.vue'
import MarkdownRenderer from '@/components/chat/MarkdownRenderer.vue'

const props = withDefaults(
  defineProps<{
    message: StoreMessage
    /** 是否是最后一条消息（用于打字光标） */
    isLast: boolean
    /** 是否正在流式接收 */
    isStreaming: boolean
    /** 是否处于思考阶段（由 MessageList 分组后传入） */
    isThinkingPhase?: boolean
    /** 是否渲染在 AI 响应卡片内部（压制外壳样式） */
    isInCard?: boolean
  }>(),
  {
    isThinkingPhase: false,
    isInCard: false,
  },
)

// ─── thinking 计时器 ───
const thinkingElapsed = ref(0)
let timerInterval: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  if (props.message.type === 'thinking') {
    if (props.message.durationMs) thinkingElapsed.value = props.message.durationMs
    timerInterval = setInterval(() => { thinkingElapsed.value += 100 }, 100)
  }
})

watch(() => props.isStreaming, (streaming) => {
  if (!streaming && timerInterval) { clearInterval(timerInterval); timerInterval = null }
})

onUnmounted(() => {
  if (timerInterval) clearInterval(timerInterval)
})

function formatElapsed(ms: number): string {
  const sec = Math.floor(ms / 1000)
  const min = Math.floor(sec / 60)
  const s = sec % 60
  const ds = Math.floor((ms % 1000) / 100)
  return min > 0 ? `${min}m${s}.${ds}s` : `${s}.${ds}s`
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60000)}m${Math.floor((ms % 60000) / 1000)}s`
}

// ─── 用户头像首字符 ───
const userInitial = computed(() => props.message.content.charAt(0).toUpperCase())

// ─── ResultTable 数据 ───
const resultColumns = computed(() => {
  if (!props.message.dataPreview?.columns) return []
  return props.message.dataPreview.columns.map((name) => ({ name, type: 'text', is_sensitive: false }))
})
const resultRows = computed(() => props.message.dataPreview?.rows ?? [])

// ─── tool_result 折叠 ───
const toolResultExpanded = ref(false)

// ─── 工具调用参数 JSON ───
const toolArgsJson = computed(() => {
  if (!props.message.toolArgs) return ''
  return JSON.stringify(props.message.toolArgs, null, 2)
})

// ─── 光标显示条件：仅流式回答阶段 ───
const showCursor = computed(() => {
  return props.isStreaming && props.isLast
    && props.message.type === 'text'
    && props.message.stage !== 'thinking'
})
</script>

<template>
  <div
    class="message-bubble"
    :class="[
      `role-${message.role}`,
      `type-${message.type}`,
      { 'is-streaming': isStreaming && isLast },
      { 'in-card': isInCard },
    ]"
  >
    <!-- ═══════════ 用户消息气泡 ═══════════ -->
    <template v-if="message.role === 'user' && message.type === 'text'">
      <div class="user-msg-wrapper">
        <div class="user-bubble">
          <p class="user-text">{{ message.content }}</p>
        </div>
      </div>
    </template>

    <!-- ═══════════ 助手消息 ═══════════ -->
    <template v-else>
      <!-- === waiting（三点脉冲） === -->
      <template v-if="message.type === 'waiting'">
        <div class="waiting-dots">
          <span class="dot"></span>
          <span class="dot"></span>
          <span class="dot"></span>
        </div>
      </template>

      <!-- === reasoning（深度推理文本） === -->
      <template v-if="message.type === 'reasoning'">
        <div class="reasoning-text">
          <MarkdownRenderer :content="message.content" />
        </div>
      </template>

      <!-- === thinking === -->
      <template v-if="message.type === 'thinking'">
        <div class="thinking-block" :class="{ expanded: thinkingElapsed > 0 }">
          <div class="thinking-header">
            <span class="thinking-label">推理过程</span>
            <span class="thinking-timer">{{ formatElapsed(thinkingElapsed) }}</span>
          </div>
          <div class="thinking-body">
            <p class="thinking-content">{{ message.content }}</p>
          </div>
        </div>
      </template>

      <!-- === tool_call（工具调用卡片） === -->
      <template v-if="message.type === 'tool_call'">
        <div class="tool-call-card">
          <div class="tool-call-header">
            <div class="tool-call-left">
              <!-- 加载中旋转图标 -->
              <svg
                v-if="message.stepStatus === 'running'"
                class="tool-call-spinner"
                width="14" height="14" viewBox="0 0 24 24" fill="none"
                stroke="currentColor" stroke-width="2.5"
              >
                <circle cx="12" cy="12" r="10" stroke-opacity="0.25" />
                <path d="M12 2a10 10 0 0 1 10 10" stroke-linecap="round" />
              </svg>
              <!-- 完成勾选 -->
              <svg
                v-else-if="message.stepStatus === 'done'"
                class="tool-call-icon done"
                width="14" height="14" viewBox="0 0 24 24" fill="none"
                stroke="currentColor" stroke-width="2.5" stroke-linecap="round"
                stroke-linejoin="round"
              >
                <polyline points="20 6 9 17 4 12" />
              </svg>
              <!-- 默认工具图标 -->
              <svg
                v-else
                class="tool-call-icon"
                width="14" height="14" viewBox="0 0 24 24" fill="none"
                stroke="currentColor" stroke-width="2" stroke-linecap="round"
                stroke-linejoin="round"
              >
                <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
              </svg>
              <span class="tool-call-name">
                <template v-if="message.stepStatus === 'running'">正在调用工具：</template>
                <template v-else-if="message.stepStatus === 'done'">已完成调用：</template>
                <template v-else>调用工具：</template>
                <strong>{{ message.tool }}</strong>
              </span>
            </div>
            <span
              v-if="message.durationMs !== undefined"
              class="tool-call-duration"
            >{{ formatDuration(message.durationMs) }}</span>
          </div>
          <!-- 参数 JSON -->
          <div v-if="toolArgsJson" class="tool-call-body">
            <pre class="tool-call-args">{{ toolArgsJson }}</pre>
          </div>
        </div>
      </template>

      <!-- === tool_result（工具返回结果，折叠块） === -->
      <template v-if="message.type === 'tool_result'">
        <details class="tool-result-block" @toggle="toolResultExpanded = ($event.target as HTMLDetailsElement).open">
          <summary class="tool-result-summary">
            <svg class="tool-result-chevron" :class="{ open: toolResultExpanded }" width="12" height="12" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M6 4L10 8L6 12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="tool-result-icon">
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
              <line x1="3" y1="9" x2="21" y2="9"/>
              <line x1="9" y1="21" x2="9" y2="9"/>
            </svg>
            <span class="tool-result-label">工具返回结果</span>
            <span v-if="message.durationMs !== undefined" class="tool-result-duration">{{ formatDuration(message.durationMs) }}</span>
          </summary>
          <div class="tool-result-content">
            <MarkdownRenderer :content="message.content" />
          </div>
        </details>
      </template>

      <!-- === text（Markdown 回答，区分 thinking / answer 阶段） === -->
      <template v-if="message.type === 'text'">
        <!-- thinking 阶段文本：左侧带竖线提示 -->
        <div v-if="message.stage === 'thinking' || isThinkingPhase" class="thinking-text-block">
          <MarkdownRenderer :content="message.content" />
        </div>
        <!-- answer 阶段：Markdown 渲染 -->
        <div v-else class="answer-text-block">
          <MarkdownRenderer :content="message.content" />
          <span v-if="showCursor" class="cursor-blink"></span>
        </div>
      </template>

      <!-- === sql === -->
      <template v-if="message.type === 'sql'">
        <SqlBlock
          :sql="message.sqlContent || message.content"
          :audit-status="message.auditStatus"
          :is-readonly="message.isReadonly"
        />
      </template>

      <!-- === result === -->
      <template v-if="message.type === 'result'">
        <ResultTable
          v-if="resultColumns.length > 0 || resultRows.length > 0"
          :columns="resultColumns"
          :rows="resultRows"
          :total-rows="message.totalRows ?? resultRows.length"
          :execution-time-ms="message.executionTimeMs"
        />
        <MarkdownRenderer v-else :content="message.summary || message.content" />
      </template>

      <!-- === error === -->
      <template v-if="message.type === 'error'">
        <ErrorCard
          :severity="(message.severity as 'info' | 'warning' | 'error') ?? 'error'"
          :error-code="message.errorCode"
          :user-message="message.userMessage"
          :content="message.content"
        />
      </template>

      <!-- === diagnosis === -->
      <template v-if="message.type === 'diagnosis' && message.findings">
        <DiagnosisCard
          :findings="message.findings"
          :target-sql="message.targetSql"
        />
      </template>
    </template>
  </div>
</template>

<style scoped>
/* ═══════════ 基础 ═══════════ */
.message-bubble {
  animation: msg-enter 0.2s ease both;
}

.message-bubble.in-card {
  animation: none;
}

@keyframes msg-enter {
  from { opacity: 0; transform: translateY(6px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* ═══════════ 用户消息 ─ 右对齐、圆角气泡 ═══════════ */
.role-user {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 16px;
}

.user-msg-wrapper {
  display: flex;
  flex-direction: row-reverse;
  align-items: flex-end;
  gap: 10px;
  max-width: 70%;
}

.user-avatar {
  width: 30px;
  height: 30px;
  min-width: 30px;
  border-radius: 50%;
  background: var(--accent-teal);
  color: #0B0E14;
  font-size: 13px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.user-bubble {
  background: var(--chat-user-bg);
  border: 1px solid var(--chat-user-border);
  border-radius: var(--radius-2xl);
  border-top-right-radius: 4px;
  padding: 10px 18px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}

.user-text {
  font-size: 15px;
  line-height: 1.65;
  color: var(--chat-user-text);
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
}

/* ═══════════ waiting 三点脉冲 ═══════════ */
.waiting-dots {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 12px 20px;
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-tertiary);
  animation: dot-bounce 1.4s ease-in-out infinite both;
}
.dot:nth-child(1) { animation-delay: 0s; }
.dot:nth-child(2) { animation-delay: 0.2s; }
.dot:nth-child(3) { animation-delay: 0.4s; }

@keyframes dot-bounce {
  0%, 80%, 100% { opacity: 0.3; transform: scale(0.8); }
  40%          { opacity: 1;   transform: scale(1.1); }
}

/* ═══════════ reasoning（深度推理文本） ═══════════ */
.reasoning-text {
  font-size: 13px;
  color: var(--chat-thinking-text);
  line-height: 1.7;
  opacity: 0.85;
  padding: 2px 0;
}

/* ═══════════ thinking（推理折叠块） ═══════════ */
.thinking-block {
  border: 1px solid var(--chat-thinking-border);
  border-radius: var(--radius-md);
  overflow: hidden;
  opacity: 0.9;
  margin: 4px 0;
}

.thinking-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 7px 14px;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--chat-thinking-divider);
}

.thinking-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-tertiary);
}

.thinking-timer {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-tertiary);
}

.thinking-body {
  padding: 10px 14px;
}

.thinking-content {
  font-size: 12.5px;
  color: var(--text-tertiary);
  line-height: 1.7;
  white-space: pre-wrap;
  opacity: 0.85;
  margin: 0;
}

/* ═══════════ 思考阶段文本（左侧竖线） ═══════════ */
.thinking-text-block {
  padding: 2px 0 2px 14px;
  border-left: 2px solid var(--chat-divider);
  font-size: 13px;
  color: var(--chat-thinking-text);
  line-height: 1.65;
  margin: 6px 0;
}

/* ═══════════ tool_call 卡片 ─ 蓝色调 ═══════════ */
.tool-call-card {
  border: 1px solid var(--chat-tool-border);
  border-radius: var(--radius-lg);
  background: var(--chat-tool-bg);
  overflow: hidden;
  margin: 6px 0;
}

.tool-call-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  background: var(--chat-tool-header-bg);
  border-bottom: 1px solid var(--chat-tool-border);
}

.tool-call-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tool-call-spinner {
  color: var(--accent-blue);
  animation: spin 0.8s linear infinite;
  flex-shrink: 0;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.tool-call-icon {
  color: var(--chat-tool-label);
  flex-shrink: 0;
}

.tool-call-icon.done {
  color: var(--color-success);
}

.tool-call-name {
  font-size: 13px;
  color: var(--chat-tool-text);
  font-weight: 400;
}

.tool-call-name strong {
  font-weight: 600;
  color: var(--chat-tool-label);
}

.tool-call-duration {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 500;
  color: var(--chat-tool-duration);
  text-transform: uppercase;
  letter-spacing: 0.03em;
  white-space: nowrap;
}

.tool-call-body {
  padding: 14px 16px;
}

.tool-call-args {
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.6;
  color: var(--chat-tool-args-text);
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
}

/* ═══════════ tool_result 折叠块 ═══════════ */
.tool-result-block {
  margin: 6px 0;
}

.tool-result-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-tertiary);
  cursor: pointer;
  list-style: none;
  padding: 6px 0;
  transition: color var(--transition-fast);
}

.tool-result-summary:hover {
  color: var(--text-secondary);
}

.tool-result-summary::-webkit-details-marker {
  display: none;
}

.tool-result-chevron {
  flex-shrink: 0;
  transition: transform 0.2s ease;
}

.tool-result-chevron.open {
  transform: rotate(90deg);
}

.tool-result-icon {
  flex-shrink: 0;
}

.tool-result-label {
  flex: 1;
}

.tool-result-duration {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-tertiary);
  background: var(--bg-surface);
  padding: 1px 8px;
  border-radius: var(--radius-sm);
}

.tool-result-content {
  padding: 12px 14px;
  background: var(--chat-result-bg);
  border: 1px solid var(--chat-result-border);
  border-radius: var(--radius-md);
  font-size: 12px;
  color: var(--chat-result-text);
  line-height: 1.6;
  overflow-x: auto;
}

.tool-result-content :deep(pre) {
  margin: 0;
  font-family: var(--font-mono);
  font-size: 12px;
  white-space: pre-wrap;
}

/* ═══════════ answer 文本 ═══════════ */
.answer-text-block {
  font-size: 14px;
  line-height: 1.7;
  color: var(--text-primary);
  word-break: break-word;
}

/* ═══════════ 打字光标 ═══════════ */
.cursor-blink {
  display: inline-block;
  width: 2px;
  height: 17px;
  background: var(--accent-teal);
  margin-left: 2px;
  vertical-align: text-bottom;
  animation: blink-cursor 0.8s step-end infinite;
}

@keyframes blink-cursor {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0; }
}

/* ═══════════ 卡片内嵌模式 ═══════════ */
.message-bubble.in-card.role-assistant {
  margin: 0;
}

.message-bubble.in-card .tool-call-card {
  margin: 8px 0;
}

.message-bubble.in-card .reasoning-text {
  padding: 0;
}
</style>
