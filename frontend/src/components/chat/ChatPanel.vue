<script setup lang="ts">
/**
 * ChatPanel — 对话面板主容器
 *
 * 顶部：会话标题 + 操作按钮
 * 中部：MessageList（消息列表）
 * 底部：InputArea 插槽（F-10 实现）
 *
 * 依据 api-contract §三 ChatPanel 组件
 */
import { ref, computed } from 'vue'
import { NButton, NPopconfirm } from 'naive-ui'
import { useChatStore } from '@/stores/chat'
import { useConnectionStore } from '@/stores/connection'
import MessageList from './MessageList.vue'

const chatStore = useChatStore()
const connectionStore = useConnectionStore()

/** 输入框文本 */
const draftText = ref('')

/** 当前连接标识文本 */
const connectionLabel = computed(() => {
  const c = connectionStore.activeConnection
  if (!c) return ''
  return `${c.db_type}  ${c.host}:${c.port}/${c.database}`
})

/** 当前模式显示名 */
const modeLabel = computed(() => {
  return chatStore.inputMode === 'natural_language' ? '自然语言' : 'SQL'
})

/** 发送消息 */
function handleSend(): void {
  const text = draftText.value.trim()
  if (!text) return
  if (!connectionStore.activeId) return

  chatStore.sendMessage(connectionStore.activeId, text, chatStore.inputMode)
  draftText.value = ''
}

/** 按 Enter 发送（Shift+Enter 换行） */
function handleKeydown(e: KeyboardEvent): void {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
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

    <!-- 输入区域（F-10 完整实现） -->
    <div class="input-area">
      <!-- 连接标识 + 模式标签 -->
      <div class="input-meta">
        <span class="meta-connection" :class="{ disconnected: !connectionStore.activeId }">
          <template v-if="connectionStore.activeId && connectionLabel">
            {{ connectionLabel }}
          </template>
          <template v-else>
            请先连接数据库
          </template>
        </span>
        <span class="meta-mode">{{ modeLabel }}</span>
      </div>

      <!-- 文本输入框 -->
      <div class="input-row">
        <textarea
          v-model="draftText"
          class="input-textarea"
          :placeholder="connectionStore.activeId ? '输入消息，Enter 发送，Shift+Enter 换行…' : '请先选择数据库连接'"
          :disabled="!connectionStore.activeId || chatStore.isStreaming"
          rows="1"
          @keydown="handleKeydown"
        ></textarea>
        <button
          class="send-btn"
          :class="{ streaming: chatStore.isStreaming }"
          :disabled="!draftText.trim() && !chatStore.isStreaming"
          @click="chatStore.isStreaming ? chatStore.cancelStreaming() : handleSend()"
        >
          <template v-if="chatStore.isStreaming">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
              <rect x="6" y="6" width="5" height="12" rx="1"/>
              <rect x="13" y="6" width="5" height="12" rx="1"/>
            </svg>
            停止
          </template>
          <template v-else>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <line x1="22" y1="2" x2="11" y2="13"/>
              <polygon points="22 2 15 22 11 13 2 9 22 2"/>
            </svg>
            发送
          </template>
        </button>
      </div>

      <!-- 快捷键提示 -->
      <p class="input-hint">
        Enter 发送 · Shift+Enter 换行
        <template v-if="chatStore.isStreaming">
          · 再次点击「停止」或按 Escape 取消
        </template>
      </p>
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

.header-btn:hover {
  color: var(--color-error) !important;
}

/* ─── 输入区域 ─── */
.input-area {
  flex-shrink: 0;
  padding: 8px 20px 12px;
  border-top: 1px solid var(--border-color);
  background: var(--bg-surface);
}

.input-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.meta-connection {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-tertiary);
}

.meta-connection.disconnected {
  color: var(--color-error);
  opacity: 0.7;
}

.meta-mode {
  font-size: 11px;
  color: var(--accent-teal);
  padding: 1px 6px;
  border: 1px solid rgba(45, 212, 191, 0.3);
  border-radius: var(--radius-sm);
}

/* 输入行 */
.input-row {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}

.input-textarea {
  flex: 1;
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  color: var(--text-primary);
  font-family: var(--font-body);
  font-size: 13px;
  line-height: 1.5;
  padding: 8px 12px;
  resize: none;
  min-height: 36px;
  max-height: 120px;
  outline: none;
  transition: border-color var(--transition-fast);
}

.input-textarea:focus {
  border-color: var(--accent-teal);
}

.input-textarea::placeholder {
  color: var(--text-tertiary);
}

.input-textarea:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.send-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 14px;
  background: var(--accent-teal);
  color: #0B0E14;
  border: none;
  border-radius: var(--radius-md);
  font-family: var(--font-body);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all var(--transition-fast);
  white-space: nowrap;
  height: 36px;
}

.send-btn:hover:not(:disabled) {
  background: #5EE4D0;
}

.send-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.send-btn.streaming {
  background: var(--color-error);
  color: white;
}

.send-btn.streaming:hover:not(:disabled) {
  background: #FCA5A5;
}

.input-hint {
  font-size: 10px;
  color: var(--text-tertiary);
  margin-top: 4px;
  letter-spacing: 0.2px;
}
</style>
