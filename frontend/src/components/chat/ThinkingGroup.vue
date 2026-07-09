<script setup lang="ts">
/**
 * ThinkingGroup — 思考过程折叠组件（兼容回退）
 *
 * 主流渲染路径：MessageList 直接遍历 thinking_group.steps 内联渲染。
 * 此组件作为兼容回退，当 steps 为空但 thinking_group 消息存在时使用。
 */
import type { StoreMessage } from '@/stores/chat'
import MarkdownRenderer from '@/components/chat/MarkdownRenderer.vue'
import SqlBlock from '@/components/sql/SqlBlock.vue'

defineProps<{
  steps: StoreMessage[]
  stepCount: number
  totalDurationMs: number
  defaultExpanded?: boolean
}>()

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  const min = Math.floor(ms / 60000)
  const sec = Math.floor((ms % 60000) / 1000)
  return `${min}m${sec}s`
}

function toolArgsSummary(step: StoreMessage): string {
  if (!step.toolArgs) return ''
  const json = JSON.stringify(step.toolArgs)
  return json.length > 80 ? json.slice(0, 80) + '...' : json
}
</script>

<template>
  <div v-if="steps.length > 0" class="thinking-group-fallback">
    <div
      v-for="(step, idx) in steps"
      :key="step.id ?? idx"
      class="tg-step"
    >
      <!-- reasoning -->
      <div v-if="step.type === 'reasoning'" class="tg-reasoning">
        <MarkdownRenderer :content="step.content" />
      </div>

      <!-- text thinking -->
      <div v-else-if="step.type === 'text' && step.stage === 'thinking'" class="tg-think-text">
        <MarkdownRenderer :content="step.content" />
      </div>

      <!-- tool_call -->
      <div v-else-if="step.type === 'tool_call'" class="tg-tool-row">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="tg-tool-icon">
          <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
        </svg>
        <span class="tg-tool-name">{{ step.tool }}</span>
        <span v-if="step.toolArgs" class="tg-tool-args">{{ toolArgsSummary(step) }}</span>
      </div>

      <!-- tool_result -->
      <div v-else-if="step.type === 'tool_result'" class="tg-result-row">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="tg-result-icon">
          <polyline points="20 6 9 17 4 12"/>
        </svg>
        <span class="tg-tool-name tg-result-name">{{ step.tool }}</span>
        <span class="tg-result-text">{{ step.content }}</span>
        <span v-if="step.durationMs !== undefined" class="tg-result-duration">{{ formatDuration(step.durationMs) }}</span>
      </div>

      <!-- sql -->
      <div v-else-if="step.type === 'sql'" class="tg-sql-block">
        <SqlBlock
          :sql="step.sqlContent || step.content"
          :audit-status="step.auditStatus"
          :is-readonly="step.isReadonly"
        />
      </div>
    </div>
  </div>
  <div v-else class="tg-empty">
    <span class="tg-empty-text">暂无思考步骤</span>
  </div>
</template>

<style scoped>
.thinking-group-fallback {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.tg-step {
  font-size: 13px;
}

/* ── reasoning ── */
.tg-reasoning {
  font-size: 12.5px;
  color: var(--chat-thinking-text);
  line-height: 1.65;
  opacity: 0.85;
}

/* ── thinking text ── */
.tg-think-text {
  font-size: 13px;
  color: var(--chat-thinking-text);
  line-height: 1.6;
  padding-left: 12px;
  border-left: 2px solid var(--chat-divider);
}

/* ── tool row ── */
.tg-tool-row,
.tg-result-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 0;
}

.tg-tool-icon {
  color: var(--chat-tool-label);
  flex-shrink: 0;
}

.tg-result-icon {
  color: var(--color-success);
  flex-shrink: 0;
}

.tg-tool-name {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 500;
  color: var(--chat-tool-label);
  padding: 1px 6px;
  background: var(--chat-tool-bg);
  border-radius: var(--radius-sm);
}

.tg-result-name {
  color: var(--color-success);
  background: rgba(16, 185, 129, 0.1);
}

.tg-tool-args {
  font-size: 11px;
  color: var(--text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.tg-result-text {
  font-size: 12px;
  color: var(--text-secondary);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tg-result-duration {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-tertiary);
  background: var(--bg-surface);
  padding: 0 6px;
  border-radius: var(--radius-sm);
}

/* ── sql ── */
.tg-sql-block {
  margin: 4px 0;
}

/* ── empty ── */
.tg-empty {
  padding: 4px 0;
}
.tg-empty-text {
  font-size: 12px;
  color: var(--text-tertiary);
}
</style>
