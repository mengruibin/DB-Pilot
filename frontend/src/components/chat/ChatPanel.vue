<script setup lang="ts">
/**
 * ChatPanel — 对话面板主容器
 *
 * 顶部：会话标题 + 操作按钮
 * 中部：MessageList（消息列表）
 * 底部：InputArea（双模式输入组件）
 *
 * 写操作确认卡片以内联方式展示在消息流中（非模态弹窗），
 * 设计参考 Claude Code / Codex 等产品的轻量确认模式。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NButton, NSpin } from 'naive-ui'
import { useChatStore } from '@/stores/chat'
import { useConnectionStore } from '@/stores/connection'
import MessageList from './MessageList.vue'
import InputArea from './InputArea.vue'
import WriteConfirmation from './WriteConfirmation.vue'

const chatStore = useChatStore()
const connectionStore = useConnectionStore()

/** 输入框文本（双向绑定用） */
const inputText = ref('')

/** 切换活跃连接 */
function handleSelectConnection(id: string): void {
  connectionStore.setActiveConnection(id)
}

/** 面板标题：当前会话标题或 "新会话" */
const panelTitle = computed(() => {
  return chatStore.currentSession?.title ?? '新会话'
})

/** 发送消息 */
function handleSend(text: string): void {
  if (!connectionStore.activeId) return
  chatStore.sendMessage(connectionStore.activeId, text)
  inputText.value = ''
}

/** 停止流式 */
function handleStop(): void {
  chatStore.cancelStreaming()
}

/** 开始新对话 */
function handleNewSession(): void {
  chatStore.startNewSession()
}

// ─── 回到底部按钮（固定在消息区底部、输入框正上方） ───
/** 消息列表滚动容器（外层全宽滚动，滚动条贴右边缘） */
const messageListWrapperRef = ref<HTMLElement | null>(null)
/** 是否显示"回到底部"按钮 */
const showScrollBottom = ref(false)
/** 距底部阈值（px），小于该值视为"已到底部"（取极小值：不在最底部即显示） */
const SCROLL_BOTTOM_THRESHOLD = 4

/** 根据当前滚动位置更新按钮显隐：离开底部一定距离才显示 */
function updateScrollBottomState(): void {
  const el = messageListWrapperRef.value
  if (!el) return
  const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
  showScrollBottom.value = distanceFromBottom > SCROLL_BOTTOM_THRESHOLD
}

/** 平滑滚动到最新消息 */
function scrollToBottom(smooth = true): void {
  const el = messageListWrapperRef.value
  if (!el) return
  el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
}

onMounted(() => {
  const el = messageListWrapperRef.value
  if (el) el.addEventListener('scroll', updateScrollBottomState, { passive: true })
  updateScrollBottomState()
})

onBeforeUnmount(() => {
  const el = messageListWrapperRef.value
  if (el) el.removeEventListener('scroll', updateScrollBottomState)
})

// 消息数量变化（新增/清空）后重新评估是否在底部
watch(
  () => chatStore.messages.length,
  async () => {
    await nextTick()
    updateScrollBottomState()
  }
)
</script>

<template>
  <div class="chat-panel">
    <!-- 面板头部 -->
    <div class="panel-header">
      <div class="header-left">
        <span class="header-title">{{ panelTitle }}</span>
        <span v-if="chatStore.messages.length > 0" class="msg-count">
          {{ chatStore.messages.length }} 条消息
        </span>
      </div>
      <div class="header-actions">
        <n-button text size="tiny" class="header-btn new-session-btn" @click="handleNewSession">新对话</n-button>
      </div>
    </div>

    <!-- 消息列表（外层全宽滚动，内层限宽居中，滚动条贴最右侧） -->
    <div ref="messageListWrapperRef" class="message-list-wrapper">
      <div class="message-list-inner">
        <!-- 历史消息加载中 -->
        <div v-if="chatStore.messagesLoading" class="loading-state">
          <n-spin size="small" />
          <span class="loading-text">加载消息历史...</span>
        </div>

        <!-- 消息列表 -->
        <MessageList
          v-else
          :messages="chatStore.messages"
          :is-streaming="chatStore.isStreaming"
        />

        <!-- 写操作确认内联卡片（作为消息流的一部分自然展示） -->
        <WriteConfirmation />
      </div>
    </div>

    <!-- 回到底部按钮浮层：零高度，位于消息区与输入框之间，作定位上下文。
         ⚠ 注意不能放进滚动容器内——绝对定位子元素会随滚动内容一起滚动，
         导致按钮向上滚动时跑到屏幕外。 -->
    <div class="scroll-btn-zone">
      <Transition name="fade-up">
        <button
          v-if="showScrollBottom"
          class="scroll-bottom-btn"
          title="回到底部最新消息"
          aria-label="回到底部最新消息"
          @click="scrollToBottom(true)"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>
      </Transition>
    </div>

    <!-- 输入区域（与消息列表同宽限宽居中） -->
    <div class="input-area-wrapper">
      <InputArea
        v-model="inputText"
        :is-streaming="chatStore.isStreaming"
        :has-connection="!!connectionStore.activeId"
        :connections="connectionStore.connections"
        :active-connection-id="connectionStore.activeId"
        @send="handleSend"
        @stop="handleStop"
        @select-connection="handleSelectConnection"
      />
    </div>
  </div>
</template>

<style scoped>
.chat-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--bg-primary);
}

/* ─── 面板头部 ─── */
.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 20px;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.header-title {
  font-family: var(--font-display);
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.msg-count {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-tertiary);
}

.header-btn {
  color: var(--text-tertiary) !important;
  font-size: 12px !important;
}

.new-session-btn:hover {
  color: var(--interactive-color) !important;
}

/* 历史消息加载状态 */
.loading-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
}

.loading-text {
  font-size: 13px;
  color: var(--text-tertiary);
}

/* 消息列表外层：全宽滚动容器，滚动条贴右边缘。
   position: relative 作为回到底部按钮的定位上下文（按钮不随内容滚动） */
.message-list-wrapper {
  position: relative;
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
}

/* ═══════════ 回到底部浮动按钮 ═══════════ */
/* 浮层：零高度，不参与布局，仅作为按钮的固定定位上下文（不随消息滚动） */
.scroll-btn-zone {
  position: relative;
  height: 0;
  flex-shrink: 0;
}

/* 圆形纯箭头，固定在消息区底部（输入框正上方），仅当滚动离开底部时显示 */
.scroll-bottom-btn {
  position: absolute;
  bottom: 12px;
  left: 50%;
  transform: translateX(-50%);
  width: 38px;
  height: 38px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  background: var(--bg-elevated);
  border: 1px solid var(--border-light);
  border-radius: 50%;
  color: var(--text-secondary);
  cursor: pointer;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.35);
  transition: all var(--transition-fast);
  z-index: 20;
}
.scroll-bottom-btn:hover {
  background: var(--bg-hover);
  border-color: var(--accent-teal);
  color: var(--accent-teal);
  transform: translateX(-50%) translateY(-2px);
}

/* 浅色主题：白底 + 轻投影，与深色主题形成区分 */
[data-theme="light"] .scroll-bottom-btn {
  background: #FFFFFF;
  box-shadow: 0 2px 10px rgba(15, 23, 42, 0.1);
}
[data-theme="light"] .scroll-bottom-btn:hover {
  background: var(--bg-hover);
}

/* 回到底部按钮过渡动画 */
.fade-up-enter-active,
.fade-up-leave-active {
  transition: all 0.2s ease;
}
.fade-up-enter-from,
.fade-up-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(8px);
}

/* 消息列表内层：限制最大宽度并居中 */
.message-list-inner {
  max-width: 900px;
  width: 100%;
  margin: 0 auto;
  min-height: 100%;
  display: flex;
  flex-direction: column;
}

/* 输入区域容器：与消息列表同宽限宽居中 */
.input-area-wrapper {
  flex-shrink: 0;
  max-width: 900px;
  width: 100%;
  margin: 0 auto;
}
</style>
