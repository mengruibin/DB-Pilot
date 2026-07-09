<script setup lang="ts">
/**
 * MessageList — 消息列表（AI 响应卡片布局 + 虚拟滚动）
 *
 * 设计理念（遵照 DB-Pilot 对话界面设计规范）：
 *   用户消息 → 独立气泡（右对齐）
 *   AI 响应 → 主卡片 = 思考面板（可折叠）+ 最终回答（绿调）
 *
 * 虚拟滚动：消息超 50 条时启用，仅渲染可视区域 ±5 条。
 * 自动滚底：新消息到达自动滚动，用户手动上翻 >200px 时暂停。
 */
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import MessageBubble from './MessageBubble.vue'
import type { StoreMessage } from '@/stores/chat'

const props = defineProps<{
  messages: StoreMessage[]
  isStreaming: boolean
}>()

// ─── 滚动容器 ───
const listRef = ref<HTMLElement | null>(null)
const scrollTop = ref(0)
const containerHeight = ref(0)

const ESTIMATED_ITEM_HEIGHT = 72
const BUFFER = 5
const VIRTUAL_THRESHOLD = 50
const isNearBottom = ref(true)
const showScrollButton = ref(false)

/** 获取实际的滚动容器（外层 .message-list-wrapper） */
function getScrollContainer(): HTMLElement | null {
  if (!listRef.value) return null
  return listRef.value.closest('.message-list-wrapper') as HTMLElement | null
}

// ─── 分组类型 ───
type MessageGroup =
  | { type: 'user'; items: StoreMessage[] }
  | { type: 'ai_response'; items: StoreMessage[] }   /* thinking_group + answer text */
  | { type: 'thinking'; items: StoreMessage[] }       /* 独立思考阶段（无后续 answer） */
  | { type: 'normal'; items: StoreMessage[] }         /* 独立消息 */

/** 判断是否属于思考阶段（应归组降权） */
function isThinkingPhaseMessage(msg: StoreMessage): boolean {
  if (['waiting', 'reasoning', 'thinking', 'tool_call', 'tool_result', 'sql'].includes(msg.type)) return true
  if (msg.type === 'text' && msg.stage === 'thinking') return true
  return false
}

/**
 * 分组策略（遵照设计规范 — 所有 AI 响应使用统一卡片）：
 *   1. 用户消息 → 独立 'user' 组（右对齐气泡）
 *   2. 连续 assistant 消息 → 'ai_response' 卡片（思考面板 + 最终回答）
 *   3. 连续 thinking-phase 消息（流式中）→ 'thinking' 组（降权容器）
 *   4. 其余独立消息 → 'normal'
 */
const groupedMessages = computed<MessageGroup[]>(() => {
  if (props.messages.length > VIRTUAL_THRESHOLD) {
    return props.messages.map((msg) => ({ type: 'normal' as const, items: [msg] }))
  }

  const groups: MessageGroup[] = []
  const len = props.messages.length
  let i = 0

  while (i < len) {
    const msg = props.messages[i]

    // 用户消息：独立气泡
    if (msg.role === 'user') {
      groups.push({ type: 'user', items: [msg] })
      i++
      continue
    }

    // AI 响应卡片：收集当前 user 消息之后的所有连续 assistant 消息
    // 包括 thinking_group、text(stage=answer)、以及独立 text 回答
    const batch: StoreMessage[] = []
    while (i < len && props.messages[i].role === 'assistant') {
      batch.push(props.messages[i])
      i++
    }

    if (batch.length === 0) {
      // 防御：理论上不会走到这里
      i++
      continue
    }

    // 如果 batch 中全部是 thinking-phase 消息（流式进行中），
    // 则作为思考容器渲染；否则作为 AI 响应卡片渲染
    const allThinking = batch.every((m) => isThinkingPhaseMessage(m))
    if (allThinking) {
      groups.push({ type: 'thinking', items: batch })
    } else {
      groups.push({ type: 'ai_response', items: batch })
    }
  }

  return groups
})

// ─── 虚拟滚动 ───
const virtualRange = computed(() => {
  const total = props.messages.length
  if (total <= VIRTUAL_THRESHOLD) return { start: 0, end: total, totalHeight: 0, offsetTop: 0 }
  const start = Math.max(0, Math.floor(scrollTop.value / ESTIMATED_ITEM_HEIGHT) - BUFFER)
  const end = Math.min(total, Math.ceil((scrollTop.value + containerHeight.value) / ESTIMATED_ITEM_HEIGHT) + BUFFER)
  return { start, end, totalHeight: total * ESTIMATED_ITEM_HEIGHT, offsetTop: start * ESTIMATED_ITEM_HEIGHT }
})

const visibleMessages = computed(() => {
  const { start, end } = virtualRange.value
  return props.messages.slice(start, end)
})
const virtualOffset = computed(() => virtualRange.value.offsetTop)
const virtualTotalHeight = computed(() => virtualRange.value.totalHeight)
const useVirtual = computed(() => props.messages.length > VIRTUAL_THRESHOLD)

// ─── 自动滚动 ───
function scrollToBottom(smooth = true): void {
  const container = getScrollContainer()
  if (!container) return
  container.scrollTo({ top: container.scrollHeight, behavior: smooth ? 'smooth' : 'instant' })
}

function handleScroll(e: Event): void {
  const el = e.target as HTMLElement
  scrollTop.value = el.scrollTop
  containerHeight.value = el.clientHeight
  const nearBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 200
  isNearBottom.value = nearBottom
  showScrollButton.value = !nearBottom
}

watch(() => props.messages.length, () => {
  if (isNearBottom.value) nextTick(() => scrollToBottom(false))
})

watch(() => props.isStreaming, (streaming) => {
  if (streaming && isNearBottom.value) nextTick(() => scrollToBottom(false))
})

onMounted(() => {
  nextTick(() => scrollToBottom(false))
  const container = getScrollContainer()
  if (container) {
    containerHeight.value = container.clientHeight
    container.addEventListener('scroll', handleScroll, { passive: true })
  }
})

onUnmounted(() => {
  const container = getScrollContainer()
  if (container) container.removeEventListener('scroll', handleScroll)
})

defineExpose({ scrollToBottom })

// ─── AI 响应卡片内：分离思考消息与最终回答 ───
function splitAIResponse(items: StoreMessage[]): {
  thinkingItems: StoreMessage[]
  answerItems: StoreMessage[]
  hasToolCalls: boolean
  totalDurationMs: number
} {
  const thinkingItems: StoreMessage[] = []
  const answerItems: StoreMessage[] = []
  let hasToolCalls = false
  let totalDurationMs = 0

  for (const m of items) {
    // 如果有 thinking_group，展开其 steps
    if (m.type === 'thinking_group' && m.thinkingGroup) {
      for (const step of m.thinkingGroup.steps) {
        thinkingItems.push(step)
        if (step.type === 'tool_call' || step.type === 'tool_result') hasToolCalls = true
      }
      totalDurationMs = m.thinkingGroup.totalDurationMs || totalDurationMs
      continue
    }

    // tool_call / tool_result → 思考面板
    if (m.type === 'tool_call' || m.type === 'tool_result') {
      thinkingItems.push(m)
      hasToolCalls = true
      continue
    }

    // reasoning → 思考面板
    if (m.type === 'reasoning') {
      thinkingItems.push(m)
      continue
    }

    // text(stage=thinking) → 思考面板
    if (m.type === 'text' && m.stage === 'thinking') {
      thinkingItems.push(m)
      continue
    }

    // text(stage=answer) → 最终回答
    if (m.type === 'text' && m.stage === 'answer') {
      answerItems.push(m)
      continue
    }

    // text（无 stage，兼容历史） → 最终回答
    if (m.type === 'text' && !m.stage) {
      answerItems.push(m)
      continue
    }

    // sql / result / error / diagnosis → 最终回答区域
    if (['sql', 'result', 'error', 'diagnosis'].includes(m.type)) {
      answerItems.push(m)
      continue
    }

    // 未分类的默认归入回答
    answerItems.push(m)
  }

  return { thinkingItems, answerItems, hasToolCalls, totalDurationMs }
}

// ─── 思考面板折叠状态 ───
const thinkingExpanded = ref(true)  // 默认展开
</script>

<template>
  <div class="message-list" ref="listRef">
    <!-- ═══════════ 非虚拟滚动：分组渲染 ═══════════ -->
    <template v-if="!useVirtual">
      <template v-for="(group, gIdx) in groupedMessages" :key="gIdx">

        <!-- ── 用户消息：独立右对齐气泡 ── -->
        <template v-if="group.type === 'user'">
          <MessageBubble
            v-for="msg in group.items"
            :key="msg.id"
            :message="msg"
            :is-last="false"
            :is-streaming="false"
          />
        </template>

        <!-- ── AI 响应卡片 ── -->
        <template v-else-if="group.type === 'ai_response'">
          <div class="ai-response-card">
            <!-- 思考过程（折叠面板）：仅在有思考内容时显示 -->
            <div v-if="splitAIResponse(group.items).thinkingItems.length > 0" class="card-thinking-section">
              <details class="thinking-details" :open="thinkingExpanded">
                <summary class="thinking-summary">
                  <div class="thinking-summary-left">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="thinking-brain-icon">
                      <path d="M12 2a2 2 0 0 1 2 2c0 1.1-.9 2-2 2a2 2 0 0 1-2-2c0-1.1.9-2 2-2z"/>
                      <path d="M16.7 8.3A5.3 5.3 0 0 0 12 7c-2.5 0-4.6 1.7-5.2 4"/>
                      <path d="M18 12a6 6 0 0 0-12 0"/>
                      <path d="M8 14v.1M16 14v.1"/>
                    </svg>
                    <span class="thinking-summary-title">思考过程</span>
                    <span
                      v-if="splitAIResponse(group.items).totalDurationMs > 0"
                      class="thinking-summary-time"
                    >
                      {{ splitAIResponse(group.items).totalDurationMs < 1000
                        ? `${splitAIResponse(group.items).totalDurationMs}ms`
                        : `${(splitAIResponse(group.items).totalDurationMs / 1000).toFixed(1)}s`
                      }}
                    </span>
                  </div>
                  <svg class="thinking-chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="6 9 12 15 18 9"/>
                  </svg>
                </summary>

                <!-- 思考面板内容 -->
                <div class="thinking-body">
                  <div
                    v-for="(step, sIdx) in splitAIResponse(group.items).thinkingItems"
                    :key="step.id ?? sIdx"
                    class="thinking-step-row"
                  >
                    <!-- tool_call / tool_result -->
                    <div v-if="step.type === 'tool_call' || step.type === 'tool_result'" class="thinking-step">
                      <div class="step-content">
                        <MessageBubble
                          :message="step"
                          :is-last="false"
                          :is-streaming="false"
                          :is-thinking-phase="true"
                          :is-in-card="true"
                        />
                      </div>
                    </div>

                    <!-- reasoning -->
                    <div v-else-if="step.type === 'reasoning'" class="thinking-step">
                      <span class="step-dot reasoning-dot"></span>
                      <div class="step-content">
                        <MessageBubble
                          :message="step"
                          :is-last="false"
                          :is-streaming="false"
                          :is-thinking-phase="true"
                          :is-in-card="true"
                        />
                      </div>
                    </div>

                    <!-- text thinking -->
                    <div v-else-if="step.type === 'text' && step.stage === 'thinking'" class="thinking-step">
                      <span class="step-dot text-dot"></span>
                      <div class="step-content">
                        <MessageBubble
                          :message="step"
                          :is-last="false"
                          :is-streaming="false"
                          :is-thinking-phase="true"
                          :is-in-card="true"
                        />
                      </div>
                    </div>

                    <!-- sql / other -->
                    <div v-else class="thinking-step">
                      <div class="step-content">
                        <MessageBubble
                          :message="step"
                          :is-last="false"
                          :is-streaming="false"
                          :is-thinking-phase="true"
                          :is-in-card="true"
                        />
                      </div>
                    </div>
                  </div>
                </div>
              </details>
            </div>

            <!-- 最终回答（绿调区域） -->
            <div
              v-if="splitAIResponse(group.items).answerItems.length > 0"
              class="card-answer-section"
              :class="{ 'no-thinking': splitAIResponse(group.items).thinkingItems.length === 0 }"
            >
              <div class="answer-heading">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="answer-check-icon">
                  <polyline points="20 6 9 17 4 12"/>
                </svg>
                <span class="answer-heading-text">最终回答</span>
              </div>
              <div class="answer-content">
                <MessageBubble
                  v-for="ansMsg in splitAIResponse(group.items).answerItems"
                  :key="ansMsg.id"
                  :message="ansMsg"
                  :is-last="ansMsg === group.items[group.items.length - 1]"
                  :is-streaming="isStreaming && ansMsg === group.items[group.items.length - 1]"
                  :is-in-card="true"
                />
              </div>
            </div>
          </div>
        </template>

        <!-- ── 独立思考容器（无 answer 跟随） ── -->
        <template v-else-if="group.type === 'thinking'">
          <div class="thinking-process-container">
            <MessageBubble
              v-for="msg in group.items"
              :key="msg.id"
              :message="msg"
              :is-last="msg === group.items[group.items.length - 1]"
              :is-streaming="isStreaming && msg === group.items[group.items.length - 1]"
              :is-thinking-phase="true"
            />
          </div>
        </template>

        <!-- ── 独立普通消息 ── -->
        <template v-else>
          <MessageBubble
            v-for="msg in group.items"
            :key="msg.id"
            :message="msg"
            :is-last="msg === group.items[group.items.length - 1]"
            :is-streaming="isStreaming && msg === group.items[group.items.length - 1]"
          />
        </template>

      </template>
    </template>

    <!-- ═══════════ 虚拟滚动（平坦列表） ═══════════ -->
    <template v-else>
      <div class="virtual-spacer" :style="{ height: `${virtualTotalHeight}px` }">
        <div class="virtual-content" :style="{ transform: `translateY(${virtualOffset}px)` }">
          <MessageBubble
            v-for="msg in visibleMessages"
            :key="msg.id"
            :message="msg"
            :is-last="false"
            :is-streaming="false"
          />
        </div>
      </div>
    </template>

    <!-- ── 空状态 ── -->
    <div v-if="messages.length === 0" class="empty-list">
      <p class="empty-text">暂无消息，输入问题开始对话</p>
    </div>

    <!-- ── 滚动到底部按钮 ── -->
    <Transition name="fade-up">
      <button v-if="showScrollButton" class="scroll-bottom-btn" @click="scrollToBottom(true)">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
          <polyline points="6 9 12 15 18 9"/>
        </svg>
        最新消息
      </button>
    </Transition>
  </div>
</template>

<style scoped>
/* ═══════════ 消息列表容器 ═══════════ */
.message-list {
  flex: none;
  overflow-y: visible;
  padding: 16px 0px;
  position: relative;
  scroll-behavior: smooth;
}

/* ═══════════ AI 响应主卡片 ═══════════ */
.ai-response-card {
  background: var(--chat-card-bg);
  border: 1px solid var(--chat-card-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--chat-card-shadow);
  overflow: hidden;
  margin-bottom: 16px;
}

/* ── 思考面板 ── */
.card-thinking-section {
  border-bottom: 1px solid var(--chat-divider);
}

.thinking-details > .thinking-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 20px;
  cursor: pointer;
  list-style: none;
  transition: background var(--transition-fast);
}

.thinking-details > .thinking-summary::-webkit-details-marker {
  display: none;
}

.thinking-details > .thinking-summary:hover {
  background: rgba(128, 128, 128, 0.04);
}

.thinking-summary-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.thinking-brain-icon {
  color: var(--text-tertiary);
  flex-shrink: 0;
}

.thinking-summary-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
}

.thinking-summary-time {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-tertiary);
  background: var(--bg-surface);
  padding: 1px 8px;
  border-radius: 10px;
}

.thinking-chevron {
  color: var(--text-tertiary);
  flex-shrink: 0;
  transition: transform 0.2s ease;
}

.thinking-details[open] > .thinking-summary .thinking-chevron {
  transform: rotate(180deg);
}

/* 思考面板内容 */
.thinking-body {
  padding: 4px 20px 18px;
  opacity: 0.88;
}

.thinking-step-row {
  position: relative;
}

.thinking-step {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  margin-bottom: 4px;
}

.step-dot {
  width: 6px;
  height: 6px;
  min-width: 6px;
  border-radius: 50%;
  margin-top: 6px;
  flex-shrink: 0;
}

.reasoning-dot {
  background: var(--text-tertiary);
}

.text-dot {
  background: var(--accent-blue);
}

.step-content {
  flex: 1;
  min-width: 0;
}

/* ── 最终回答区域 — 背景与卡片统一，文字高对比 ── */
.card-answer-section {
  padding: 18px 20px;
  background: var(--chat-answer-bg, var(--bg-elevated));
  border-top: 1px solid var(--chat-answer-border, var(--border-color));
  color: var(--chat-answer-text, var(--text-primary));
}

/* 无思考面板时，去掉顶部 border */
.card-answer-section.no-thinking {
  border-top: none;
  border-radius: var(--radius-xl);
}

.answer-heading {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.answer-check-icon {
  color: var(--chat-answer-heading);
  flex-shrink: 0;
}

.answer-heading-text {
  font-size: 13px;
  font-weight: 700;
  color: var(--chat-answer-heading);
}

.answer-content {
  color: var(--chat-answer-text);
  line-height: 1.7;
}

/* ═══════════ 独立思考容器（降权） ═══════════ */
.thinking-process-container {
  max-width: 85%;
  border: 1px solid var(--chat-thinking-border);
  background: var(--chat-card-bg);
  border-radius: var(--radius-lg);
  opacity: 0.9;
  margin-bottom: 12px;
  padding: 12px 16px;
  overflow: hidden;
}

/* ═══════════ 虚拟滚动 ═══════════ */
.virtual-spacer {
  position: relative;
  overflow: hidden;
}
.virtual-content {
  will-change: transform;
}

/* ═══════════ 空状态 ═══════════ */
.empty-list {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 120px;
}
.empty-text {
  font-size: 13px;
  color: var(--text-tertiary);
}

/* ═══════════ 滚动到底部按钮 ═══════════ */
.scroll-bottom-btn {
  position: absolute;
  bottom: 12px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 16px;
  background: var(--bg-elevated);
  border: 1px solid var(--border-color);
  border-radius: 20px;
  color: var(--text-secondary);
  font-family: var(--font-body);
  font-size: 12px;
  cursor: pointer;
  transition: all var(--transition-fast);
  z-index: 10;
  white-space: nowrap;
}
.scroll-bottom-btn:hover {
  background: var(--bg-hover);
  border-color: var(--accent-teal);
  color: var(--accent-teal);
}

/* ── 过渡动画 ── */
.fade-up-enter-active,
.fade-up-leave-active {
  transition: all 0.2s ease;
}
.fade-up-enter-from,
.fade-up-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(8px);
}
</style>
