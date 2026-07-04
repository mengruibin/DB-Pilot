<script setup lang="ts">
/**
 * ChatPanel — 对话面板主容器
 *
 * 顶部：会话标题 + 操作按钮
 * 中部：MessageList（消息列表）
 * 底部：InputArea（双模式输入组件）
 *
 * 依据 api-contract §三 ChatPanel 组件树
 */
import { computed, ref } from 'vue'
import { NButton, NPopconfirm } from 'naive-ui'
import { useChatStore } from '@/stores/chat'
import { useConnectionStore } from '@/stores/connection'
import MessageList from './MessageList.vue'
import InputArea from './InputArea.vue'
import ThinkingIndicator from './ThinkingIndicator.vue'

const chatStore = useChatStore()
const connectionStore = useConnectionStore()

/** 输入框文本（双向绑定用） */
const inputText = ref('')

/** 切换活跃连接 */
function handleSelectConnection(id: string): void {
  connectionStore.setActiveConnection(id)
}

/** 是否显示 Agent 思考指示器（streaming 中且尚未收到 sql/result） */
const showThinking = computed(() => {
  if (!chatStore.isStreaming) return false
  return !chatStore.messages.some(
    (m) => m.type === 'sql' || m.type === 'result'
  )
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
</script>

<template>
  <div class="chat-panel">
    <!-- 面板头部 -->
    <div class="panel-header">
      <div class="header-left">
        <span class="header-title">对话</span>
        <span v-if="chatStore.messages.length > 0" class="msg-count">
          {{ chatStore.messages.length }} 条消息
        </span>
      </div>
      <div class="header-actions">
        <n-popconfirm @positive-click="chatStore.clearMessages()">
          <template #trigger>
            <n-button text size="tiny" class="header-btn">清空</n-button>
          </template>
          确定清空当前对话？
        </n-popconfirm>
      </div>
    </div>

    <!-- 消息列表 -->
    <MessageList
      :messages="chatStore.messages"
      :is-streaming="chatStore.isStreaming"
    />

    <!-- Agent 思考指示器 -->
    <ThinkingIndicator :visible="showThinking" />

    <!-- 输入区域（F-10 独立组件） -->
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

.header-btn:hover {
  color: var(--color-error) !important;
}
</style>
