<script setup lang="ts">
/**
 * SessionItem — 单条会话条目组件（F3）
 *
 * 侧边栏中每个会话的展示行，支持选中、重命名、删除操作。
 */
import { computed, nextTick, ref } from 'vue'
import { NPopconfirm } from 'naive-ui'
import { useChatStore } from '@/stores/chat'
import { formatRelativeTime } from '@/utils/time'
import type { Session } from '@/types/chat'

const props = defineProps<{
  session: Session
  isActive: boolean
}>()

const emit = defineEmits<{
  deleted: [sessionId: string]
}>()

const chatStore = useChatStore()

// ─── 重命名状态 ───

const isRenaming = ref(false)
const renameText = ref('')
const renameInput = ref<HTMLInputElement | null>(null)

/** 进入重命名模式 */
function startRenaming(): void {
  renameText.value = props.session.title
  isRenaming.value = true
  nextTick(() => {
    renameInput.value?.focus()
    renameInput.value?.select()
  })
}

/** 确认重命名 */
function confirmRename(): void {
  const trimmed = renameText.value.trim()
  if (trimmed && trimmed !== props.session.title) {
    chatStore.renameSession(props.session.id, trimmed)
  }
  isRenaming.value = false
}

/** 取消重命名 */
function cancelRename(): void {
  isRenaming.value = false
  renameText.value = ''
}

/** 键盘处理 */
function onRenameKeydown(e: KeyboardEvent): void {
  if (e.key === 'Enter') {
    e.preventDefault()
    confirmRename()
  } else if (e.key === 'Escape') {
    cancelRename()
  }
}

// ─── 删除处理 ───

function handleDelete(): void {
  emit('deleted', props.session.id)
}

// ─── 计算属性 ───

const displayTime = computed(() => formatRelativeTime(props.session.last_active_at))
</script>

<template>
  <div
    class="session-item"
    :class="{ active: isActive }"
    @click="chatStore.switchToSession(session.id)"
  >
    <!-- 重命名状态：显示输入框 -->
    <template v-if="isRenaming">
      <input
        ref="renameInput"
        v-model="renameText"
        class="rename-input"
        @keydown="onRenameKeydown"
        @blur="confirmRename"
        @click.stop
      />
    </template>

    <!-- 正常状态：显示标题和信息 -->
    <template v-else>
      <div class="session-content">
        <span class="session-title" :title="session.title">{{ session.title }}</span>
        <div class="session-meta">
          <span class="session-time">{{ displayTime }}</span>
          <span class="session-dot">·</span>
          <span class="session-count">{{ session.message_count }} 条</span>
        </div>
      </div>

      <!-- 操作按钮（悬停显示） -->
      <div class="session-actions" @click.stop>
        <!-- 重命名按钮 -->
        <button class="action-btn rename-btn" title="重命名" @click="startRenaming">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
            <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
          </svg>
        </button>

        <!-- 删除按钮 -->
        <n-popconfirm @positive-click="handleDelete">
          <template #trigger>
            <button class="action-btn delete-btn" title="删除">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="3 6 5 6 21 6"/>
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
              </svg>
            </button>
          </template>
          确定删除此会话？此操作不可撤销。
        </n-popconfirm>
      </div>
    </template>
  </div>
</template>

<style scoped>
.session-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 10px;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background var(--transition-fast);
  position: relative;
  gap: 4px;
}

.session-item:hover {
  background: var(--bg-hover);
}

.session-item.active {
  background: var(--bg-hover);
}

/* 内容区域 */
.session-content {
  flex: 1;
  min-width: 0;
  overflow: hidden;
}

.session-title {
  display: block;
  font-size: 13px;
  font-weight: 450;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  line-height: 1.3;
}

.session-item.active .session-title {
  color: var(--text-primary);
}

.session-meta {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 1px;
}

.session-dot {
  opacity: 0.5;
}

/* 操作按钮（默认隐藏，悬停显示） */
.session-actions {
  display: none;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

.session-item:hover .session-actions {
  display: flex;
}

.action-btn {
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
  padding: 0;
  transition: all var(--transition-fast);
}

.action-btn:hover {
  background: var(--bg-hover);
}

.delete-btn:hover {
  color: var(--color-error, #e74c3c);
  background: rgba(231, 76, 60, 0.1);
}

.rename-btn:hover {
  color: var(--accent-teal);
  background: rgba(45, 212, 191, 0.1);
}

/* 重命名输入框 */
.rename-input {
  width: 100%;
  font-family: var(--font-body);
  font-size: 13px;
  padding: 3px 6px;
  border: 1px solid var(--accent-teal);
  border-radius: 4px;
  background: var(--bg-surface);
  color: var(--text-primary);
  outline: none;
}
</style>
