<script setup lang="ts">
/**
 * InputArea — 输入区域组件
 *
 * 自然语言输入 + 自动高度 + 发送/停止。
 *
 * 依据 frontend AGENTS.md §4 输入区行为规范
 *     api-contract §三 InputArea 组件
 */
import { ref, computed, watch, nextTick } from 'vue'
import type { ConnectionConfig } from '@/types/connection'
import ConnectionSwitcher from './ConnectionSwitcher.vue'
import { useSettingsStore } from '@/stores/settings'

const props = defineProps<{
  /** 输入文本（v-model） */
  modelValue: string
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

const settingsStore = useSettingsStore()

function handleKeydown(e: KeyboardEvent): void {
  if (props.isStreaming) return
  if (e.key !== 'Enter') return

  // Enter 发送模式：Enter=发送，Shift+Enter=换行
  // 换行模式（enterToSend=false）：Enter=换行，Shift+Enter / Ctrl+Enter / Cmd+Enter=发送
  const send = settingsStore.settings.enterToSend
    ? !e.shiftKey
    : e.shiftKey || e.ctrlKey || e.metaKey
  if (send) {
    e.preventDefault()
    submit()
  }
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

// ─── placeholder 文字（随连接状态与 Enter 发送设置响应式变化） ───

const placeholderText = computed(() => {
  if (!props.hasConnection) return '请先选择数据库连接'
  return settingsStore.settings.enterToSend
    ? '输入消息，Enter 发送，Shift+Enter 换行…'
    : '输入消息，Shift+Enter 发送，Enter 换行…'
})
</script>

<template>
  <div class="input-area">
    <!-- 圆角卡片容器 -->
    <div class="input-card">
      <!-- 上层：连接选择器 -->
      <div class="area-topbar">
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
            :value="modelValue"
            :placeholder="placeholderText"
            :disabled="!hasConnection || isStreaming"
            rows="1"
            @input="handleInput"
            @keydown="handleKeydown"
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

/* ─── 上区块：连接选择器 ─── */
.area-topbar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  padding: 10px 10px 9px;
  border-bottom: 1px solid var(--input-card-divider);
  background: var(--input-card-bg);
  border-radius: 16px 16px 0 0;
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


</style>
