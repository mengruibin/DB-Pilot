<script setup lang="ts">
/**
 * ThinkingGroup — 思考过程折叠组件
 *
 * 将思考阶段的 message（thinking text / tool_call / tool_result / sql）
 * 折叠为可展开区域，展示在最终回答上方。
 *
 * 用法：由 MessageBubble 在 type === 'thinking_group' 时渲染
 */
import { ref } from 'vue'
import type { StoreMessage } from '@/stores/chat'
import MarkdownRenderer from '@/components/chat/MarkdownRenderer.vue'
import SqlBlock from '@/components/sql/SqlBlock.vue'

const props = withDefaults(
  defineProps<{
    /** 思考过程步骤消息列表（thinking text / tool_call / tool_result / sql） */
    steps: StoreMessage[]
    /** 工具调用步骤数量 */
    stepCount: number
    /** 整个 SSE 流总耗时（毫秒） */
    totalDurationMs: number
    /** 是否默认展开（默认折叠） */
    defaultExpanded?: boolean
  }>(),
  {
    defaultExpanded: false,
  },
)

/** 展开/折叠状态 */
const expanded = ref(props.defaultExpanded)

function toggle(): void {
  expanded.value = !expanded.value
}

/** 格式化耗时：优先 X.Xs，长耗时用 XmXs */
function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  const min = Math.floor(ms / 60000)
  const sec = Math.floor((ms % 60000) / 1000)
  return `${min}m${sec}s`
}

/** tool_call 参数摘要（截取前 60 字符） */
function toolArgsSummary(step: StoreMessage): string {
  if (!step.toolArgs) return ''
  const json = JSON.stringify(step.toolArgs)
  return json.length > 60 ? json.slice(0, 60) + '...' : json
}
</script>

<template>
  <div class="thinking-group">
    <!-- 头部：点击展开/折叠 -->
    <button class="tg-header" @click="toggle">
      <!-- chevron 图标，展开时旋转 -->
      <svg
        class="tg-chevron"
        :class="{ rotated: expanded }"
        width="16"
        height="16"
        viewBox="0 0 16 16"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path
          d="M6 4L10 8L6 12"
          stroke="currentColor"
          stroke-width="1.5"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </svg>


      <!-- 标题 -->
      <span class="tg-label">思考过程</span>


      <!-- 总耗时（等宽字体右对齐） -->
      <span class="tg-duration">{{ formatDuration(totalDurationMs) }}</span>
    </button>

    <!-- 展开/折叠主体 -->
    <Transition name="fade-slide">
      <div v-show="expanded" class="tg-body">
        <!-- 遍历步骤消息 -->
        <div
          v-for="step in steps"
          :key="step.id"
          class="tg-step"
          :class="`step-${step.type}`"
        >
          <!-- text 类型：思考文本 -->
          <div v-if="step.type === 'text'" class="tg-thinking-text">
            <MarkdownRenderer :content="step.content" />
          </div>

          <!-- tool_call 类型：工具调用步骤 -->
          <div v-if="step.type === 'tool_call'" class="tg-tool-row">
            <span class="tg-tool-icon">🔧</span>
            <span class="tg-tool-name">{{ step.tool }}</span>
            <span class="tg-tool-args">{{ toolArgsSummary(step) }}</span>
          </div>

          <!-- tool_result 类型：工具返回结果 -->
          <div v-if="step.type === 'tool_result'" class="tg-tool-row tg-result-row">
            <span class="tg-tool-icon">✅</span>
            <span class="tg-tool-name">{{ step.tool }}</span>
            <span class="tg-result-text">{{ step.content }}</span>
            <span v-if="step.durationMs !== undefined" class="tg-result-duration">
              {{ formatDuration(step.durationMs) }}
            </span>
          </div>

          <!-- sql 类型：SQL 代码块（紧凑模式） -->
          <div v-if="step.type === 'sql'" class="tg-sql-block">
            <SqlBlock
              :sql="step.sqlContent || step.content"
              :audit-status="step.auditStatus"
              :is-readonly="step.isReadonly"
            />
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
/* ─── 容器 ─── */
.thinking-group {
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  overflow: hidden;
  margin-bottom: 8px;
}

/* ─── 头部 ─── */
.tg-header {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  background: var(--bg-surface);
  border: none;
  cursor: pointer;
  font-size: 13px;
  color: var(--text-secondary);
  transition: background var(--transition-fast);
  text-align: left;
}

.tg-header:hover {
  background: var(--bg-hover);
}

.tg-header:focus-visible {
  outline: 2px solid var(--accent-teal);
  outline-offset: -2px;
}

/* ─── Chevron 旋转动画 ─── */
.tg-chevron {
  flex-shrink: 0;
  transition: transform 0.2s ease;
  color: var(--text-tertiary);
}

.tg-chevron.rotated {
  transform: rotate(90deg);
}

.tg-icon {
  flex-shrink: 0;
  font-size: 14px;
  line-height: 1;
}

.tg-label {
  flex-shrink: 0;
  font-weight: 500;
}

/* ─── 步数徽章 ─── */
.tg-badge {
  font-size: 11px;
  padding: 1px 8px;
  border-radius: 10px;
  background: rgba(129, 140, 248, 0.1);
  color: var(--accent-purple);
  flex-shrink: 0;
}

/* ─── 耗时（等宽字体，右对齐） ─── */
.tg-duration {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-tertiary);
  margin-left: auto;
  flex-shrink: 0;
}

/* ─── 展开主体 ─── */
.tg-body {
  padding: 10px 14px;
  background: var(--bg-surface);
  border-top: 1px solid var(--border-color);
  opacity: 0.85;
}

/* ─── 步骤消息 ─── */
.tg-step {
  margin-bottom: 6px;
}

.tg-step:last-child {
  margin-bottom: 0;
}

/* ─── 思考文本 ─── */
.tg-thinking-text {
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.6;
  padding-left: 2px;
  border-left: 2px solid var(--border-light);
  padding-left: 10px;
  margin-bottom: 8px;
}

/* ─── 工具行 ─── */
.tg-tool-row {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 3px 0;
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.5;
}

.tg-tool-icon {
  flex-shrink: 0;
  font-size: 12px;
  margin-top: 1px;
}

.tg-tool-name {
  flex-shrink: 0;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--accent-teal);
  font-weight: 500;
  padding: 1px 6px;
  background: rgba(45, 212, 191, 0.08);
  border-radius: var(--radius-sm);
}

.tg-tool-args {
  color: var(--text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tg-result-text {
  flex: 1;
  color: var(--text-secondary);
  min-width: 0;
}

.tg-result-duration {
  flex-shrink: 0;
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-tertiary);
  background: var(--bg-primary);
  padding: 0 6px;
  border-radius: var(--radius-sm);
  line-height: 1.8;
}

/* ─── SQL 块（紧凑） ─── */
.tg-sql-block {
  margin: 4px 0;
}

/* ─── fade-slide 过渡 ─── */
.fade-slide-enter-active,
.fade-slide-leave-active {
  transition: all 0.25s ease;
}

.fade-slide-enter-from {
  opacity: 0;
  transform: translateY(-4px);
}

.fade-slide-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
