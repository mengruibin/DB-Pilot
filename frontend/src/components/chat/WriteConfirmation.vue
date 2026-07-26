<script setup lang="ts">
/**
 * WriteConfirmation — 危险操作确认内联卡片（category 分类渲染）
 *
 * 按 confirm_category 渲染不同风格的确认卡片：
 *   - sql_write: SQL 代码块 + 蓝色铅笔图标
 *   - connection_kill: 线程详情表 + 红色警告图标 + 不可逆提示
 *   - generic: key-value 参数表 + 灰色信息图标（降级兜底）
 *
 * 内联于消息列表中，非模态弹窗，不遮挡界面。
 */
import { ref, computed, watch, nextTick, onUnmounted } from 'vue'
import { NButton } from 'naive-ui'
import { useChatStore } from '@/stores/chat'
import type { ConfirmCategory } from '@/types/chat'

const chatStore = useChatStore()

const COOLDOWN_MS = 1500
const cooldownRemaining = ref(0)
let cooldownTimer: ReturnType<typeof setInterval> | null = null

const isCooldown = computed(() => cooldownRemaining.value > 0)
const writes = computed(() => chatStore.pendingConfirm?.writes ?? [])
const pendingCount = computed(() => writes.value.length)

/** 首条操作的 category（同批次所有操作 category 相同） */
const category = computed<ConfirmCategory>(() => writes.value[0]?.category ?? 'generic')

/** 按钮文案按 category 映射 */
const buttonLabel = computed(() => {
  if (isCooldown.value) return `确认 (${(cooldownRemaining.value / 1000).toFixed(1)}s)`
  if (category.value === 'connection_kill') return '确认终止'
  return '确认执行'
})

/** 全部确认按钮文案 */
const confirmAllLabel = computed(() => {
  if (category.value === 'connection_kill' && pendingCount.value > 1) return '全部确认终止'
  if (pendingCount.value > 1) return '全部确认执行'
  return buttonLabel.value
})

/** 危险操作详情表的可读字段名映射 */
const DETAIL_LABELS: Record<string, string> = {
  sql: 'SQL',
  thread_id: '线程 ID',
  elapsed_seconds: '已运行时长',
  current_sql: '当前 SQL',
  user: '用户',
  host: '来源主机',
  database: '数据库',
  command: '命令类型',
  state: '状态',
  pid: '进程 ID',
  transaction_id: '事务 ID',
}

function formatDetailValue(key: string, value: unknown): string {
  if (value === null || value === undefined) return '-'
  const s = String(value)
  // 时长字段友好格式化
  if (key === 'elapsed_seconds' && /^\d+$/.test(s)) {
    const sec = parseInt(s, 10)
    if (sec >= 86400) return `${Math.floor(sec / 86400)}d ${Math.floor((sec % 86400) / 3600)}h`
    if (sec >= 3600) return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`
    if (sec >= 60) return `${Math.floor(sec / 60)}m ${sec % 60}s`
    return `${sec}s`
  }
  if (s.length > 200) return s.slice(0, 197) + '...'
  return s
}

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
      :class="`confirm-category--${category}`"
    >
      <div class="confirm-card-inner">
        <!-- ═══════ 头部 ═══════ -->
        <div class="confirm-header">
          <!-- sql_write: 蓝色铅笔图标 -->
          <svg
            v-if="category === 'sql_write'"
            class="confirm-header-icon"
            width="15" height="15" viewBox="0 0 24 24"
            fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round"
          >
            <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>
          </svg>
          <!-- connection_kill: 红色三角警告图标 -->
          <svg
            v-else-if="category === 'connection_kill'"
            class="confirm-header-icon"
            width="15" height="15" viewBox="0 0 24 24"
            fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round"
          >
            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
            <line x1="12" y1="9" x2="12" y2="13"/>
            <line x1="12" y1="17" x2="12.01" y2="17"/>
          </svg>
          <!-- generic: 灰色信息圆图标 -->
          <svg
            v-else
            class="confirm-header-icon"
            width="15" height="15" viewBox="0 0 24 24"
            fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round"
          >
            <circle cx="12" cy="12" r="10"/>
            <line x1="12" y1="16" x2="12" y2="12"/>
            <line x1="12" y1="8" x2="12.01" y2="8"/>
          </svg>

          <span class="confirm-header-title">
            {{ category === 'connection_kill' ? '需要确认终止数据库连接' : '需要确认执行写操作' }}
            <span v-if="pendingCount > 1" class="confirm-header-count">（共 {{ pendingCount }} 条）</span>
          </span>
        </div>

        <!-- ═══════ 内容区 ═══════ -->
        <div class="confirm-body">
          <!-- ── sql_write: SQL 代码块 ── -->
          <template v-if="category === 'sql_write'">
            <div
              v-for="(w, idx) in writes"
              :key="w.tool_call_id"
              class="sql-row"
            >
              <span v-if="pendingCount > 1" class="sql-row-index">#{{ idx + 1 }}</span>
              <pre class="sql-row-code"><code>{{ w.details?.sql ?? w.description }}</code></pre>
            </div>
          </template>

          <!-- ── connection_kill: 线程详情表 ── -->
          <template v-else-if="category === 'connection_kill'">
            <div
              v-for="w in writes"
              :key="w.tool_call_id"
              class="detail-table"
            >
              <template v-for="(label, k) in DETAIL_LABELS" :key="k">
                <div v-if="k in (w.details ?? {})" class="detail-row">
                  <span class="detail-label">{{ label }}</span>
                  <span class="detail-value">{{ formatDetailValue(k, w.details?.[k]) }}</span>
                </div>
              </template>
              <!-- 兜底：渲染 details 中已知标签之外的字段 -->
              <template v-for="(val, k) in w.details" :key="'extra-'+String(k)">
                <div v-if="!(k in DETAIL_LABELS)" class="detail-row">
                  <span class="detail-label">{{ k }}</span>
                  <span class="detail-value">{{ formatDetailValue(k, val) }}</span>
                </div>
              </template>
              <div class="detail-warning">⚡ 此操作不可逆，将立即断开该连接</div>
            </div>
          </template>

          <!-- ── generic: key-value 参数表（降级兜底） ── -->
          <template v-else>
            <div
              v-for="w in writes"
              :key="w.tool_call_id"
              class="detail-table"
            >
              <div class="detail-row">
                <span class="detail-label">工具</span>
                <span class="detail-value">{{ w.tool }}</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">描述</span>
                <span class="detail-value">{{ w.description }}</span>
              </div>
              <div
                v-for="(val, k) in w.details"
                :key="String(k)"
                class="detail-row"
              >
                <span class="detail-label">{{ k }}</span>
                <span class="detail-value">{{ formatDetailValue(k, val) }}</span>
              </div>
            </div>
          </template>
        </div>

        <!-- ═══════ 操作按钮 ═══════ -->
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
            {{ pendingCount > 1 ? confirmAllLabel : buttonLabel }}
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

/* category 动态强调色 */
.confirm-category--sql_write .confirm-card-inner {
  border-left-color: var(--confirm-accent);
}
.confirm-category--connection_kill .confirm-card-inner {
  border-left-color: var(--confirm-danger, #e53e3e);
}
.confirm-category--generic .confirm-card-inner {
  border-left-color: var(--confirm-generic, #a0aec0);
}

/* ─── 头部 ─── */
.confirm-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 18px 0;
}

.confirm-header-icon {
  flex-shrink: 0;
}
.confirm-category--sql_write .confirm-header-icon {
  color: var(--confirm-accent);
}
.confirm-category--connection_kill .confirm-header-icon {
  color: var(--confirm-danger, #e53e3e);
}
.confirm-category--generic .confirm-header-icon {
  color: var(--confirm-generic, #a0aec0);
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

/* ─── SQL 代码区（sql_write） ─── */
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

/* ─── 详情表（connection_kill / generic） ─── */
.detail-table {
  background: var(--chat-code-bg);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.detail-row {
  display: flex;
  border-bottom: 1px solid var(--border-color);
  font-size: 12px;
  line-height: 1.5;
}
.detail-row:last-child {
  border-bottom: none;
}

.detail-label {
  flex: 0 0 110px;
  padding: 6px 10px;
  font-weight: 500;
  color: var(--text-secondary);
  background: rgba(128, 128, 128, 0.05);
  border-right: 1px solid var(--border-color);
}

.detail-value {
  flex: 1;
  padding: 6px 10px;
  color: var(--chat-code-text);
  font-family: var(--font-mono);
  word-break: break-all;
}

.detail-warning {
  padding: 8px 12px;
  font-size: 12px;
  font-weight: 500;
  color: var(--confirm-danger, #e53e3e);
  background: rgba(229, 62, 62, 0.06);
  text-align: center;
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

.confirm-category--connection_kill .action-confirm {
  --n-border: 1px solid var(--confirm-danger, #e53e3e) !important;
  --n-border-hover: 1px solid var(--confirm-danger, #e53e3e) !important;
  --n-text-color: var(--confirm-danger, #e53e3e) !important;
  --n-text-color-hover: var(--confirm-danger, #e53e3e) !important;
}

.confirm-category--generic .action-confirm {
  --n-border: 1px solid var(--confirm-generic, #a0aec0) !important;
  --n-border-hover: 1px solid var(--confirm-generic, #a0aec0) !important;
  --n-text-color: var(--confirm-generic, #a0aec0) !important;
  --n-text-color-hover: var(--confirm-generic, #a0aec0) !important;
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

.confirm-category--connection_kill .action-confirm.is-ready {
  --n-color: var(--confirm-danger, #e53e3e) !important;
  --n-color-hover: #c53030 !important;
  --n-color-pressed: var(--confirm-danger, #e53e3e) !important;
  --n-border: 1px solid var(--confirm-danger, #e53e3e) !important;
  --n-border-hover: 1px solid #c53030 !important;
  --n-text-color: #FFFFFF !important;
  --n-text-color-hover: #FFFFFF !important;
}

.confirm-category--generic .action-confirm.is-ready {
  --n-color: var(--confirm-generic, #a0aec0) !important;
  --n-color-hover: #718096 !important;
  --n-color-pressed: var(--confirm-generic, #a0aec0) !important;
  --n-border: 1px solid var(--confirm-generic, #a0aec0) !important;
  --n-border-hover: 1px solid #718096 !important;
  --n-text-color: #FFFFFF !important;
  --n-text-color-hover: #FFFFFF !important;
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
