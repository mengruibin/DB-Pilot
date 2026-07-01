<script setup lang="ts">
/**
 * MessageList — 消息列表（虚拟滚动）
 *
 * 消息超过 50 条时启用虚拟滚动，仅渲染可视区域 ±5 条。
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
    Math.ceil((scrollTop.value + containerHeight.value) / ESTIMATED_ITEM_HEIGHT) + BUFFER
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
  }
)

// isStreaming 期间保持底部
watch(
  () => props.isStreaming,
  (streaming) => {
    if (streaming && isNearBottom.value) {
      nextTick(() => scrollToBottom(false))
    }
  }
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
    <!-- 非虚拟滚动：直接渲染全部 -->
    <template v-if="!useVirtual">
      <MessageBubble
        v-for="(msg, idx) in messages"
        :key="msg.id"
        :message="msg"
        :is-last="idx === messages.length - 1"
        :is-streaming="isStreaming"
      />
    </template>

    <!-- 虚拟滚动 -->
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
