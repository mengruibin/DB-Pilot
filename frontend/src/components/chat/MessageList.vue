<script setup lang="ts">
/**
 * MessageList — 消息列表（虚拟滚动 + 思考阶段分组）
 *
 * 消息超过 50 条时启用虚拟滚动，仅渲染可视区域 ±5 条。
 * 非虚拟滚动模式下，连续思考阶段消息（waiting/thinking/tool_call/tool_result/sql）
 * 自动归组到统一容器内，使用降权样式；thinking_group 保持独立。
 *
 * 新消息自动滚动到底部（用户手动上翻 >200px 时暂停）。
 *
 * 依据 api-contract §三 MessageList 组件
 */
import { ref, computed, watch, nextTick, onMounted } from 'vue'
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

/** 估计行高（虚拟滚动用） */
const ESTIMATED_ITEM_HEIGHT = 72
/** 虚拟滚动缓冲区 */
const BUFFER = 5
/** 虚拟滚动阈值 */
const VIRTUAL_THRESHOLD = 50

/** 是否接近底部（200px 阈值） */
const isNearBottom = ref(true)

/** 是否显示"滚动到底部"按钮 */
const showScrollButton = ref(false)

// ─── 思考阶段消息分组 ───

/** 分组类型 */
type MessageGroup =
  | { type: 'normal'; items: StoreMessage[] }
  | { type: 'thinking'; items: StoreMessage[] }

/** 判断消息是否属于思考阶段（应归组降权） */
function isThinkingPhaseMessage(msg: StoreMessage): boolean {
  if (['waiting', 'reasoning', 'thinking', 'tool_call', 'tool_result', 'sql'].includes(msg.type)) return true
  // 流式 text 消息根据 stage 判断：thinking 阶段 → 归组，answer 阶段 → 最终回答
  if (msg.type === 'text' && msg.stage === 'thinking') return true
  return false
}

/**
 * 将连续思考阶段消息归组，非思考消息保持独立。
 * 虚拟滚动模式下不做分组（思考过程通常出现在对话开头，不会触达虚拟滚动阈值）。
 */
const groupedMessages = computed<MessageGroup[]>(() => {
  // 虚拟滚动模式：不做分组，保持平坦列表
  if (props.messages.length > VIRTUAL_THRESHOLD) {
    return props.messages.map((msg) => ({ type: 'normal' as const, items: [msg] }))
  }

  const groups: MessageGroup[] = []
  let current: StoreMessage[] | null = null
  let currentIsThinking = false

  for (const msg of props.messages) {
    const isThinking = isThinkingPhaseMessage(msg)

    // thinking_group 类型虽属于思考阶段产物，但其本身已是独立折叠容器，不归组
    const shouldGroup = isThinking && msg.type !== 'thinking_group'

    if (current === null || shouldGroup !== currentIsThinking) {
      // 开启新组
      if (current !== null) {
        groups.push({ type: currentIsThinking ? 'thinking' : 'normal', items: current })
      }
      current = [msg]
      currentIsThinking = shouldGroup
    } else {
      current.push(msg)
    }
  }

  // 收尾最后一组
  if (current !== null) {
    groups.push({ type: currentIsThinking ? 'thinking' : 'normal', items: current })
  }

  return groups
})

// ─── 虚拟滚动计算 ───

/** 虚拟滚动的可见范围 */
const virtualRange = computed(() => {
  const total = props.messages.length
  if (total <= VIRTUAL_THRESHOLD) {
    return { start: 0, end: total, totalHeight: 0, offsetTop: 0 }
  }

  const start = Math.max(0, Math.floor(scrollTop.value / ESTIMATED_ITEM_HEIGHT) - BUFFER)
  const end = Math.min(
    total,
    Math.ceil((scrollTop.value + containerHeight.value) / ESTIMATED_ITEM_HEIGHT) + BUFFER,
  )

  const totalHeight = total * ESTIMATED_ITEM_HEIGHT
  const offsetTop = start * ESTIMATED_ITEM_HEIGHT

  return { start, end, totalHeight, offsetTop }
})

/** 当前需要渲染的消息 */
const visibleMessages = computed(() => {
  const { start, end } = virtualRange.value
  return props.messages.slice(start, end)
})

/** 虚拟滚动偏移量 */
const virtualOffset = computed(() => virtualRange.value.offsetTop)
const virtualTotalHeight = computed(() => virtualRange.value.totalHeight)

/** 是否启用虚拟滚动 */
const useVirtual = computed(() => props.messages.length > VIRTUAL_THRESHOLD)

// ─── 自动滚动 ───

/** 滚动到底部 */
function scrollToBottom(smooth = true): void {
  if (!listRef.value) return
  const el = listRef.value
  el.scrollTo({
    top: el.scrollHeight,
    behavior: smooth ? 'smooth' : 'instant',
  })
}

/** 处理滚动事件 */
function handleScroll(): void {
  if (!listRef.value) return
  const el = listRef.value
  scrollTop.value = el.scrollTop
  containerHeight.value = el.clientHeight

  const nearBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 200
  isNearBottom.value = nearBottom
  showScrollButton.value = !nearBottom
}

// 新消息到达时自动滚动
watch(
  () => props.messages.length,
  () => {
    if (isNearBottom.value) {
      nextTick(() => scrollToBottom(false))
    }
  },
)

// isStreaming 期间保持底部
watch(
  () => props.isStreaming,
  (streaming) => {
    if (streaming && isNearBottom.value) {
      nextTick(() => scrollToBottom(false))
    }
  },
)

// 首次挂载滚动到底部
onMounted(() => {
  nextTick(() => scrollToBottom(false))
  if (listRef.value) {
    containerHeight.value = listRef.value.clientHeight
  }
})

defineExpose({ scrollToBottom })
</script>

<template>
  <div class="message-list" ref="listRef" @scroll="handleScroll">
    <!-- 非虚拟滚动：按分组渲染 -->
    <template v-if="!useVirtual">
      <template v-for="(group, gIdx) in groupedMessages" :key="gIdx">
        <!-- 思考阶段组：统一容器降权 -->
        <div v-if="group.type === 'thinking'" class="thinking-process-container">
          <MessageBubble
            v-for="msg in group.items"
            :key="msg.id"
            :message="msg"
            :is-last="false"
            :is-streaming="isStreaming && msg === group.items[group.items.length - 1]"
            :is-thinking-phase="true"
          />
        </div>
        <!-- 普通消息：独立渲染 -->
        <MessageBubble
          v-else
          v-for="msg in group.items"
          :key="msg.id"
          :message="msg"
          :is-last="false"
          :is-streaming="isStreaming"
        />
      </template>
    </template>

    <!-- 虚拟滚动：平坦列表，不分组 -->
    <template v-else>
      <div class="virtual-spacer" :style="{ height: `${virtualTotalHeight}px` }">
        <div
          class="virtual-content"
          :style="{ transform: `translateY(${virtualOffset}px)` }"
        >
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

    <!-- 空状态 -->
    <div v-if="messages.length === 0" class="empty-list">
      <p class="empty-text">暂无消息，输入问题开始对话</p>
    </div>

    <!-- 滚动到底部按钮 -->
    <Transition name="fade-up">
      <button
        v-if="showScrollButton"
        class="scroll-bottom-btn"
        @click="scrollToBottom(true)"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
          <polyline points="6 9 12 15 18 9"/>
        </svg>
        最新消息
      </button>
    </Transition>
  </div>
</template>

<style scoped>
.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 12px 20px;
  position: relative;
  scroll-behavior: smooth;
}

/* ─── 思考阶段统一容器 ─── */
.thinking-process-container {
  max-width: 85%;
  border: 1px solid rgba(128, 128, 128, 0.15);
  border-radius: var(--radius-md);
  opacity: 0.85;
  margin-bottom: 10px;
  overflow: hidden;
}

/* ─── 虚拟滚动 ─── */
.virtual-spacer {
  position: relative;
  overflow: hidden;
}

.virtual-content {
  will-change: transform;
}

/* ─── 空状态 ─── */
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

/* ─── 滚动到底部按钮 ─── */
.scroll-bottom-btn {
  position: absolute;
  bottom: 12px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 12px;
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

/* 过渡动画 */
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
