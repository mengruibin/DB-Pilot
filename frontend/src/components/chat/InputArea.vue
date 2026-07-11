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
import type { ConnectionConfig } from '@/types/connection'
import ConnectionSwitcher from './ConnectionSwitcher.vue'

const props = defineProps<{
  /** 输入文本（v-model） */
  modelValue: string
  /** 输入模式 */
  inputMode: InputMode
  /** 是否正在流式接收 */
  isStreaming: boolean
  /** 是否有活跃连接 */
  hasConnection: boolean
  /** 所有可用连接列表 */
  connections: ConnectionConfig[]
  /** 当前活跃连接 ID */
  activeConnectionId: string | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
  'update:inputMode': [mode: InputMode]
  send: [text: string]
  stop: []
  'select-connection': [id: string]
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
    <!-- 圆角卡片容器 -->
    <div class="input-card">
      <!-- 上层：模式切换 + 连接选择器 -->
      <div class="area-topbar">
        <div class="mode-tabs">
          <button
            class="mode-tab"
            :class="{ active: inputMode === 'natural_language' }"
            :disabled="isStreaming || !hasConnection"
            @click="toggleMode('natural_language')"
          >
            <span class="tab-label">自然语言</span>
          </button>
          <button
            class="mode-tab"
            :class="{ active: inputMode === 'sql_editor' }"
            :disabled="isStreaming || !hasConnection"
            @click="toggleMode('sql_editor')"
          >
            <span class="tab-label">SQL</span>
          </button>
        </div>
        <ConnectionSwitcher
          :connections="connections"
          :active-id="activeConnectionId"
          @select-connection="(id: string) => emit('select-connection', id)"
        />
      </div>

      <!-- 下层：通栏文本输入框 + 右下角发送按钮 -->
      <div class="area-editor">
        <div class="textarea-wrapper">
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
            </template>
            <template v-else>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <line x1="22" y1="2" x2="11" y2="13"/>
                <polygon points="22 2 15 22 11 13 2 9 22 2"/>
              </svg>
            </template>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.input-area {
  flex-shrink: 0;
  padding: 8px 0 12px;
}

/* ─── 圆角卡片容器 ─── */
.input-card {
  background: var(--input-card-bg);
  border: 1px solid var(--input-card-border);
  border-radius: 8px;
  box-shadow: var(--input-card-shadow);
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
}

/* ─── 上区块：Tab + 连接选择器 ─── */
.area-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 10px 9px;
  border-bottom: 1px solid var(--input-card-divider);
  background: var(--input-card-bg);
  border-radius: 16px 16px 0 0;
}

.mode-tabs {
  display: flex;
  gap: 2px;
  background: var(--input-tab-bg);
  border-radius: var(--radius-md);
  padding: 2px;
}

.mode-tab {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 12px;
  border: none;
  border-radius: 5px;
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
}

.mode-tab.active {
  background: var(--input-tab-active-bg);
  color: var(--mode-tab-active-color);
}

.mode-tab:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.tab-label {
  font-weight: 450;
}

/* ─── 下区块：通栏输入框 + 右下角发送按钮 ─── */
.area-editor {
  padding: 16px 10px 20px;
  background: var(--input-textarea-bg);
  border-radius: 0 0 16px 16px;
  box-shadow: 0 6px 8px -6px rgba(0, 0, 0, 0.35);
}

.textarea-wrapper {
  position: relative;
}

.input-textarea {
  width: 100%;
  background: var(--input-textarea-bg);
  border: none;
  border-radius: var(--radius-md);
  color: var(--text-primary);
  font-family: var(--font-body);
  font-size: 16px;
  line-height: 20px;
  padding: 8px 44px 8px 12px;
  resize: none;
  min-height: 38px;
  max-height: 120px;
  outline: none;
  transition: border-color var(--transition-fast);
  overflow: hidden;
  display: block;
  box-sizing: border-box;
}

.input-textarea:focus {
  border-color: var(--accent-teal);
}

/* 浅色主题下 focus 边框用浅蓝，增加交互层次感 */
[data-theme="light"] .input-textarea:focus {
  border-color: var(--interactive-color);
}

.input-textarea.sql-mode {
  font-family: var(--font-mono);
  font-size: 15px;
  line-height: 1.6;
}

.input-textarea::placeholder {
  color: var(--text-tertiary);
}

.input-textarea:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ─── 发送按钮（绝对定位右下角） ─── */
.submit-btn {
  position: absolute;
  right: 5px;
  bottom: -2px;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  padding: 0;
  background: var(--submit-btn-bg);
  color: var(--submit-btn-color);
  border: none;
  border-radius: 8px;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.submit-btn:hover:not(:disabled) {
  opacity: 0.85;
}

.submit-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.submit-btn.is-stop {
  background: var(--color-error);
  color: white;
}

.submit-btn.is-stop:hover:not(:disabled) {
  background: #FCA5A5;
}

/* ─── 深色主题配色（:root 默认） ─── */
.input-card {
  --input-card-bg: #1e2936;
  --input-card-border: #374151;
  --input-card-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
  --input-card-divider: #374151;
  --input-tab-bg: #0f172a;
  --input-tab-active-bg: #273444;
  --input-textarea-bg: #0f172a;
  --input-textarea-border: #374151;
}

/* ─── 浅色主题配色 ─── */
[data-theme="light"] .input-card {
  --input-card-bg: #f8fafc;
  --input-card-border: #e2e8f0;
  --input-card-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
  --input-card-divider: #e2e8f0;
  --input-tab-bg: #f1f5f9;
  --input-tab-active-bg: #ffffff;
  --input-textarea-bg: #ffffff;
  --input-textarea-border: #e2e8f0;
}

[data-theme="light"] .mode-tab.active {
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
}
</style>
