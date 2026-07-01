<script setup lang="ts">
/**
 * InputArea — 输入区域组件
 *
 * 双模式（自然语言 / SQL）+ 自动高度 + 粘贴确认 + 发送/停止。
 *
 * 依据 frontend AGENTS.md §4 输入区行为规范
 *     api-contract §三 InputArea 组件
 */
import { ref, watch, nextTick } from 'vue'
import type { InputMode } from '@/stores/chat'

const props = defineProps<{
  /** 输入文本（v-model） */
  modelValue: string
  /** 输入模式 */
  inputMode: InputMode
  /** 是否正在流式接收 */
  isStreaming: boolean
  /** 当前连接标识文本 */
  connectionLabel: string
  /** 是否有活跃连接 */
  hasConnection: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
  'update:inputMode': [mode: InputMode]
  send: [text: string]
  stop: []
}>()

// ─── textarea 引用 ───

const textareaRef = ref<HTMLTextAreaElement | null>(null)

/** 自动调整 textarea 高度（1–5 行） */
function autoResize(): void {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  const minHeight = 36
  const maxHeight = 120
  const newHeight = Math.min(Math.max(el.scrollHeight, minHeight), maxHeight)
  el.style.height = `${newHeight}px`
  el.style.overflowY = el.scrollHeight > maxHeight ? 'auto' : 'hidden'
}

watch(() => props.modelValue, () => nextTick(autoResize))

// ─── 事件处理 ───

function handleInput(e: Event): void {
  const target = e.target as HTMLTextAreaElement
  emit('update:modelValue', target.value)
}

function handleKeydown(e: KeyboardEvent): void {
  if (props.isStreaming) return

  // Shift+Enter = 换行（两种模式）
  if (e.key === 'Enter' && e.shiftKey) {
    // 默认行为就是换行，不做特殊处理
    return
  }

  // Enter = 提交（自然语言模式）
  if (e.key === 'Enter' && !e.shiftKey && props.inputMode === 'natural_language') {
    e.preventDefault()
    submit()
    return
  }

  // Ctrl+Enter = 提交（SQL 编辑器模式）
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && props.inputMode === 'sql_editor') {
    e.preventDefault()
    submit()
    return
  }
}

/** 处理粘贴事件（多行 SQL 确认） */
function handlePaste(e: ClipboardEvent): void {
  // 仅在 SQL 模式下检测
  if (props.inputMode !== 'sql_editor') return

  const text = e.clipboardData?.getData('text/plain')
  if (!text) return

  const lines = text.split('\n').filter((l) => l.trim().length > 0)
  if (lines.length <= 1) return

  // 阻止默认粘贴，显示确认
  e.preventDefault()

  const confirmed = window.confirm(
    `检测到 ${lines.length} 条 SQL 语句，确认全部执行？`
  )
  if (confirmed) {
    const current = props.modelValue
    const before = current.slice(0, getCursorPos())
    const after = current.slice(getCursorPos())
    emit('update:modelValue', before + text + after)
    nextTick(autoResize)
  }
}

/** 获取光标位置 */
function getCursorPos(): number {
  return textareaRef.value?.selectionStart ?? props.modelValue.length
}

/** 切换模式 */
function toggleMode(mode: InputMode): void {
  emit('update:inputMode', mode)
  nextTick(() => textareaRef.value?.focus())
}

/** 提交 */
function submit(): void {
  const text = props.modelValue.trim()
  if (!text || props.isStreaming || !props.hasConnection) return
  emit('send', text)
  emit('update:modelValue', '')
  nextTick(autoResize)
}

/** 停止 */
function handleStop(): void {
  emit('stop')
}

// ─── placeholder 文字 ───

const placeholderText = !props.hasConnection
  ? '请先选择数据库连接'
  : props.inputMode === 'natural_language'
    ? '输入消息，Enter 发送，Shift+Enter 换行…'
    : '输入 SQL，Ctrl+Enter 执行，Shift+Enter 换行…'
</script>

<template>
  <div class="input-area">
    <!-- 模式切换 + 连接标识 -->
    <div class="area-topbar">
      <div class="mode-tabs">
        <button
          class="mode-tab"
          :class="{ active: inputMode === 'natural_language' }"
          :disabled="isStreaming || !hasConnection"
          @click="toggleMode('natural_language')"
        >
          <span class="tab-icon">📝</span>
          <span class="tab-label">自然语言</span>
        </button>
        <button
          class="mode-tab"
          :class="{ active: inputMode === 'sql_editor' }"
          :disabled="isStreaming || !hasConnection"
          @click="toggleMode('sql_editor')"
        >
          <span class="tab-icon">💻</span>
          <span class="tab-label">SQL</span>
        </button>
      </div>
      <div class="connection-badge" :class="{ connected: hasConnection }">
        <template v-if="hasConnection">
          {{ connectionLabel }}
        </template>
        <template v-else>
          请先连接数据库
        </template>
      </div>
    </div>

    <!-- 编辑区 -->
    <div class="area-editor">
      <textarea
        ref="textareaRef"
        class="input-textarea"
        :class="{ 'sql-mode': inputMode === 'sql_editor' }"
        :value="modelValue"
        :placeholder="placeholderText"
        :disabled="!hasConnection || isStreaming"
        rows="1"
        @input="handleInput"
        @keydown="handleKeydown"
        @paste="handlePaste"
      ></textarea>
      <button
        class="submit-btn"
        :class="{ 'is-stop': isStreaming }"
        :disabled="(!modelValue.trim() && !isStreaming) || !hasConnection"
        @click="isStreaming ? handleStop() : submit()"
      >
        <template v-if="isStreaming">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
            <rect x="6" y="6" width="5" height="12" rx="1"/>
            <rect x="13" y="6" width="5" height="12" rx="1"/>
          </svg>
          <span>停止</span>
        </template>
        <template v-else>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <line x1="22" y1="2" x2="11" y2="13"/>
            <polygon points="22 2 15 22 11 13 2 9 22 2"/>
          </svg>
          <span>发送</span>
        </template>
      </button>
    </div>

    <!-- 底部快捷键提示 -->
    <p class="area-hint">
      <template v-if="inputMode === 'natural_language'">
        Enter 发送 · Shift+Enter 换行
      </template>
      <template v-else>
        Ctrl+Enter 执行 · Shift+Enter 换行
      </template>
      <template v-if="isStreaming">
        · 点击「停止」或按 Escape 取消
      </template>
    </p>
  </div>
</template>

<style scoped>
.input-area {
  flex-shrink: 0;
  padding: 8px 20px 12px;
  border-top: 1px solid var(--border-color);
  background: var(--bg-surface);
}

/* ─── 顶栏 ─── */
.area-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.mode-tabs {
  display: flex;
  gap: 2px;
  background: var(--bg-primary);
  border-radius: var(--radius-md);
  padding: 2px;
}

.mode-tab {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--text-tertiary);
  font-family: var(--font-body);
  font-size: 12px;
  cursor: pointer;
  transition: all var(--transition-fast);
  white-space: nowrap;
}

.mode-tab:hover:not(:disabled) {
  color: var(--text-secondary);
  background: var(--bg-hover);
}

.mode-tab.active {
  background: var(--bg-elevated);
  color: var(--accent-teal);
}

.mode-tab:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.tab-icon {
  font-size: 13px;
}

.tab-label {
  font-weight: 450;
}

.connection-badge {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-tertiary);
  padding: 2px 8px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  white-space: nowrap;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.connection-badge.connected {
  color: var(--accent-teal);
  border-color: rgba(45, 212, 191, 0.2);
}

/* ─── 编辑区 ─── */
.area-editor {
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
  line-height: 20px;
  padding: 7px 12px;
  resize: none;
  min-height: 36px;
  max-height: 120px;
  outline: none;
  transition: border-color var(--transition-fast);
  overflow: hidden;
}

.input-textarea:focus {
  border-color: var(--accent-teal);
}

.input-textarea.sql-mode {
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
}

.input-textarea::placeholder {
  color: var(--text-tertiary);
}

.input-textarea:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* 提交按钮 */
.submit-btn {
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

.submit-btn:hover:not(:disabled) {
  background: #5EE4D0;
}

.submit-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.submit-btn.is-stop {
  background: var(--color-error);
  color: white;
}

.submit-btn.is-stop:hover:not(:disabled) {
  background: #FCA5A5;
}

/* ─── 提示条 ─── */
.area-hint {
  font-size: 10px;
  color: var(--text-tertiary);
  margin-top: 4px;
  letter-spacing: 0.2px;
}
</style>
