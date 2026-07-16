<script setup lang="ts">
/**
 * WriteConfirmation — 写操作确认内联卡片
 *
 * 内联于消息列表中的确认卡片，非模态弹窗，不遮挡界面。
 * 设计参考 Claude Code 等产品的内联确认模式：轻量、自然、非侵入。
 *
 * 深色主题：琥珀/暖色强调
 * 浅色主题：石板灰/中性强调
 *
 * 单条 SQL：紧凑单行代码块 + 简单确认/取消
 * 多条 SQL：编号列表 + 全部确认/取消
 */
import { ref, computed, watch, nextTick, onUnmounted } from 'vue'
import { NButton } from 'naive-ui'
import { useChatStore } from '@/stores/chat'

const chatStore = useChatStore()

const COOLDOWN_MS = 1500
const cooldownRemaining = ref(0)
let cooldownTimer: ReturnType<typeof setInterval> | null = null

const isCooldown = computed(() => cooldownRemaining.value > 0)
const writes = computed(() => chatStore.pendingConfirm?.writes ?? [])
const pendingCount = computed(() => writes.value.length)

const confirmText = computed(() => {
  if (isCooldown.value) return `确认 (${(cooldownRemaining.value / 1000).toFixed(1)}s)`
  return '确认执行'
})

function startCooldown(): void {
  cooldownRemaining.value = COOLDOWN_MS
  cooldownTimer = setInterval(() => {
    cooldownRemaining.value = Math.max(0, cooldownRemaining.value - 100)
  }, 100)
}

function stopCooldown(): void {
  if (cooldownTimer) {
    clearInterval(cooldownTimer)
    cooldownTimer = null
  }
  cooldownRemaining.value = 0
}

// 当 pendingConfirm 出现时：启动冷却、滚动到卡片可见
watch(() => chatStore.pendingConfirm, (val) => {
  if (val) {
    startCooldown()
    nextTick(() => {
      document.getElementById('write-confirm-card')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    })
  } else {
    stopCooldown()
  }
}, { immediate: true })

onUnmounted(() => { stopCooldown() })

function handleApprove(): void {
  if (!writes.value.length || isCooldown.value) return
  chatStore.respondToConfirm({
    approved_tool_call_ids: writes.value.map(w => w.tool_call_id),
    denied_tool_call_ids: [],
  })
}

function handleDeny(): void {
  if (!writes.value.length) return
  chatStore.respondToConfirm({
    approved_tool_call_ids: [],
    denied_tool_call_ids: writes.value.map(w => w.tool_call_id),
  })
}
</script>

<template>
  <Transition name="confirm-fade">
    <div
      v-if="chatStore.pendingConfirm"
      id="write-confirm-card"
      class="write-confirm-card"
    >
      <!-- 卡片主体：带左侧强调色边框 -->
      <div class="confirm-card-inner">
        <!-- 头部：图标 + 提示文字 -->
        <div class="confirm-header">
          <svg
            class="confirm-header-icon"
            width="15"
            height="15"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>
          </svg>
          <span class="confirm-header-title">
            需要确认执行写操作
            <span v-if="pendingCount > 1" class="confirm-header-count">（共 {{ pendingCount }} 条）</span>
          </span>
        </div>

        <!-- SQL 代码展示 -->
        <div class="confirm-body">
          <div
            v-for="(w, idx) in writes"
            :key="w.tool_call_id"
            class="sql-row"
          >
            <span v-if="pendingCount > 1" class="sql-row-index">#{{ idx + 1 }}</span>
            <pre class="sql-row-code"><code>{{ w.sql }}</code></pre>
          </div>
        </div>

        <!-- 操作按钮 -->
        <div class="confirm-footer">
          <NButton
            text
            size="tiny"
            class="action-btn action-cancel"
            @click="handleDeny"
          >
            {{ pendingCount > 1 ? '全部取消' : '取消' }}
          </NButton>
          <NButton
            size="tiny"
            class="action-btn action-confirm"
            :class="{ 'is-ready': !isCooldown }"
            :disabled="isCooldown"
            @click="handleApprove"
          >
            {{ pendingCount > 1 ? '全部确认执行' : confirmText }}
          </NButton>
        </div>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
/* ─── 卡片容器 ─── */
.write-confirm-card {
  margin: 8px 0 16px;
  padding: 0;
}

.confirm-card-inner {
  background: var(--chat-card-bg);
  border: 1px solid var(--chat-card-border);
  border-left: 3px solid var(--confirm-accent);
  border-radius: var(--radius-xl);
  box-shadow: var(--chat-card-shadow);
  overflow: hidden;
}

/* ─── 头部 ─── */
.confirm-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 18px 0;
}

.confirm-header-icon {
  color: var(--confirm-accent);
  flex-shrink: 0;
}

.confirm-header-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--confirm-heading);
}

.confirm-header-count {
  font-weight: 400;
  color: var(--text-tertiary);
}

/* ─── SQL 代码区 ─── */
.confirm-body {
  padding: 10px 18px 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.sql-row {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.sql-row-index {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-tertiary);
  padding: 9px 0 0;
  min-width: 22px;
  text-align: right;
  flex-shrink: 0;
}

.sql-row-code {
  flex: 1;
  margin: 0;
  padding: 8px 12px;
  background: var(--chat-code-bg);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.55;
  color: var(--chat-code-text);
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.sql-row-code code {
  font-family: inherit;
}

/* ─── 操作按钮 ─── */
.confirm-footer {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 10px;
  padding: 12px 18px 14px;
}

/* 取消按钮 */
.action-cancel {
  --n-text-color: var(--text-tertiary) !important;
  --n-text-color-hover: var(--text-secondary) !important;
  font-size: 12px !important;
  padding: 4px 6px !important;
  transition: color var(--transition-fast);
}

/* 确认按钮 — 冷却中为描边样式，冷却结束为填充样式 */
.action-confirm {
  --n-color: transparent !important;
  --n-color-hover: transparent !important;
  --n-color-pressed: transparent !important;
  --n-border: 1px solid var(--confirm-accent) !important;
  --n-border-hover: 1px solid var(--confirm-accent) !important;
  --n-text-color: var(--confirm-accent) !important;
  --n-text-color-hover: var(--confirm-accent) !important;
  --n-height: 28px !important;
  font-size: 12px !important;
  font-weight: 500 !important;
  padding: 0 12px !important;
  border-radius: var(--radius-md) !important;
  transition: all var(--transition-fast);
  opacity: 0.7;
}

.action-confirm.is-ready {
  --n-color: var(--confirm-accent) !important;
  --n-color-hover: var(--confirm-accent-hover) !important;
  --n-color-pressed: var(--confirm-accent) !important;
  --n-border: 1px solid var(--confirm-accent) !important;
  --n-border-hover: 1px solid var(--confirm-accent-hover) !important;
  --n-text-color: #FFFFFF !important;
  --n-text-color-hover: #FFFFFF !important;
  opacity: 1;
}

/* ─── 进场过渡 ─── */
.confirm-fade-enter-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}

.confirm-fade-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}

.confirm-fade-enter-from {
  opacity: 0;
  transform: translateY(-8px);
}

.confirm-fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
