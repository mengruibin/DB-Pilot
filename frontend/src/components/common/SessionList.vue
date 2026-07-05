<script setup lang="ts">
/**
 * SessionList — 对话历史列表组件（F3）
 *
 * 侧边栏中的会话列表区域，包含标题栏、"新对话"按钮、会话条目列表。
 * 覆盖加载中 / 空列表 / 错误 / 正常四种状态。
 */
import SessionItem from './SessionItem.vue'
import { useChatStore } from '@/stores/chat'
import { useConnectionStore } from '@/stores/connection'

const chatStore = useChatStore()
const connectionStore = useConnectionStore()

/** 开始新对话 */
function handleNewSession(): void {
  chatStore.startNewSession()
}

/** 删除会话 */
function handleDeleteSession(sessionId: string): void {
  chatStore.deleteSession(sessionId)
}

/** 重试加载会话列表 */
function handleRetry(): void {
  chatStore.fetchSessions(connectionStore.activeId ?? undefined)
}
</script>

<template>
  <div class="session-list-section">
    <!-- 标题栏 -->
    <div class="session-header">
      <span class="session-header-label">对话历史</span>
      <button class="new-session-btn" title="新对话" @click="handleNewSession">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <line x1="12" y1="5" x2="12" y2="19"/>
          <line x1="5" y1="12" x2="19" y2="12"/>
        </svg>
      </button>
    </div>

    <!-- 列表区域 -->
    <div class="session-scroll">
      <!-- 加载中 -->
      <div v-if="chatStore.sessionsLoading" class="session-status">
        <div v-for="n in 3" :key="n" class="skeleton-row">
          <div class="skeleton-line skeleton-title"></div>
          <div class="skeleton-line skeleton-meta"></div>
        </div>
      </div>

      <!-- 错误状态 -->
      <div v-else-if="chatStore.sessionFetchError" class="session-status session-error">
        <span class="error-text">加载失败</span>
        <button class="retry-btn" @click="handleRetry">重试</button>
      </div>

      <!-- 空列表 -->
      <div v-else-if="chatStore.sessions.length === 0" class="session-status session-empty">
        <span class="empty-text">暂无对话记录</span>
        <span class="empty-hint">发送第一条消息即可开始</span>
      </div>

      <!-- 正常列表 -->
      <template v-else>
        <SessionItem
          v-for="session in chatStore.sessions"
          :key="session.id"
          :session="session"
          :is-active="session.id === chatStore.currentSessionId"
          @deleted="handleDeleteSession"
        />
      </template>
    </div>
  </div>
</template>

<style scoped>
.session-list-section {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  border-top: 1px solid var(--border-color);
  margin-top: 4px;
  padding-top: 4px;
}

/* 标题栏 */
.session-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 10px 6px 12px;
}

.session-header-label {
  font-size: 11px;
  font-weight: 500;
  color: var(--text-tertiary);
  letter-spacing: 0.3px;
  text-transform: uppercase;
}

.new-session-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  transition: all var(--transition-fast);
  padding: 0;
}

.new-session-btn:hover {
  color: var(--accent-teal);
  background: rgba(45, 212, 191, 0.1);
}

/* 滚动列表区域 */
.session-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 0 4px 4px;
}

/* 状态区域 */
.session-status {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 16px 10px;
  text-align: center;
}

.empty-text,
.error-text {
  font-size: 12px;
  color: var(--text-tertiary);
}

.empty-hint {
  font-size: 11px;
  color: var(--text-tertiary);
  opacity: 0.7;
}

.retry-btn {
  font-size: 12px;
  color: var(--accent-teal);
  background: transparent;
  border: 1px solid var(--border-color);
  border-radius: 4px;
  padding: 3px 10px;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.retry-btn:hover {
  border-color: var(--accent-teal);
  background: rgba(45, 212, 191, 0.06);
}

/* 骨架屏 */
.skeleton-row {
  padding: 6px 10px;
  margin-bottom: 2px;
}

.skeleton-line {
  height: 10px;
  border-radius: 4px;
  background: var(--bg-hover);
  animation: pulse 1.5s ease-in-out infinite;
}

.skeleton-title {
  width: 70%;
  margin-bottom: 4px;
}

.skeleton-meta {
  width: 40%;
  height: 8px;
}

@keyframes pulse {
  0%, 100% { opacity: 0.4; }
  50% { opacity: 0.8; }
}
</style>
