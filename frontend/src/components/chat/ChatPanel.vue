<script setup lang="ts">
/**
 * ChatPanel — 对话面板主容器
 *
 * 顶部：会话标题 + 操作按钮
 * 中部：MessageList（消息列表）
 * 底部：InputArea（双模式输入组件）
 *
 * 依据 api-contract §三 ChatPanel 组件树
 * 扩展（F4）：支持历史会话标题显示和消息加载状态。
 */
import { computed, ref } from 'vue'
import { NButton, NPopconfirm, NSpin } from 'naive-ui'
import { useChatStore } from '@/stores/chat'
import { useConnectionStore } from '@/stores/connection'
import MessageList from './MessageList.vue'
import InputArea from './InputArea.vue'

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
  chatStore.sendMessage(connectionStore.activeId, text, chatStore.inputMode)
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
        <n-popconfirm @positive-click="handleNewSession">
          <template #trigger>
            <n-button text size="tiny" class="header-btn new-session-btn">新对话</n-button>
          </template>
          当前对话将保留在侧边栏中，确定开始新会话？
        </n-popconfirm>
      </div>
    </div>

    <!-- 消息列表（外层全宽滚动，内层限宽居中，滚动条贴最右侧） -->
    <div class="message-list-wrapper">
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
      </div>
    </div>

    <!-- 输入区域（与消息列表同宽限宽居中） -->
    <div class="input-area-wrapper">
      <InputArea
        v-model="inputText"
        :input-mode="chatStore.inputMode"
        :is-streaming="chatStore.isStreaming"
        :has-connection="!!connectionStore.activeId"
        :connections="connectionStore.connections"
        :active-connection-id="connectionStore.activeId"
        @update:input-mode="chatStore.setInputMode"
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

/* 消息列表外层：全宽滚动容器，滚动条贴右边缘 */
.message-list-wrapper {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
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