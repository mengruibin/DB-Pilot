<script setup lang="ts">
/**
 * ConnectionIndicator — 全局连接状态指示器
 *
 * 常驻顶栏右侧，四态显示数据库连接状态：
 *   unknown（未连接）→ 灰色 / connecting（连接中）→ 🟡 脉冲
 *   healthy（已连接）→ 🟢 发光 / unreachable（连接断开）→ 🔴 + 重连
 *
 * 依据 frontend AGENTS.md §3 连接状态指示
 *     api-contract §三 ConnectionIndicator 组件
 */
import { computed, watch } from 'vue'
import { NTooltip, useNotification } from 'naive-ui'
import { useConnectionStore } from '@/stores/connection'
import type { RuntimeStatus } from '@/stores/connection'

const store = useConnectionStore()
const notification = useNotification()

/** 状态灯颜色映射 — healthy 统一使用绿色 */
const DOT_COLORS: Record<RuntimeStatus, string> = {
  unknown: '#64748B',
  connecting: '#FBBF24',
  healthy: '#34D399',
  unreachable: '#F87171',
  degraded: '#FBBF24',
}

/** 状态文字映射 */
const STATUS_LABELS: Record<RuntimeStatus, string> = {
  unknown: '未连接',
  connecting: '连接中…',
  healthy: '已连接',
  unreachable: '连接断开',
  degraded: '部分受限',
}

/** 当前圆点颜色 */
const dotColor = computed(() => DOT_COLORS[store.status])

/** 当前状态文字 */
const statusLabel = computed(() => STATUS_LABELS[store.status])

/** 当前状态对应的 CSS class */
const statusClass = computed(() => `state-${store.status}`)

/** 是否显示连接信息（healthy 状态展示 host:port/database） */
const showHostInfo = computed(() => store.status === 'healthy' && store.activeConnection)

/** 格式化后的连接字符串 */
const hostInfo = computed(() => {
  const c = store.activeConnection
  if (!c) return ''
  return `${c.host}:${c.port}/${c.database}`
})

/** 是否显示重连按钮 */
const showReconnect = computed(() => store.status === 'unreachable' && store.activeId)

/** Tooltip 内容（healthy 状态时展示） */
const tooltipContent = computed(() => {
  const c = store.activeConnection
  if (!c) return ''
  const parts = [`版本：${store.testResult?.version ?? ''}`]
  if (store.testLatencyMs !== null) {
    parts.push(`延迟：${store.testLatencyMs}ms`)
  }
  if (store.lastTestedAt) {
    const t = new Date(store.lastTestedAt).toLocaleString('zh-CN')
    parts.push(`最后测试：${t}`)
  }
  return parts.join(' · ')
})

/** 是否健康状态（用于 Tooltip 显示） */
const isHealthyWithTooltip = computed(
  () => store.status === 'healthy' && tooltipContent.value.length > 0
)

// ─── 意外断开检测 ───
// 当 status 从 healthy → unreachable 且非用户主动断开时弹出 Toast

let previousStatus: RuntimeStatus = store.status

watch(
  () => store.status,
  (newStatus) => {
    if (previousStatus === 'healthy' && newStatus === 'unreachable' && store.activeId !== null) {
      const host = store.activeConnection?.host ?? '目标主机'
      const port = store.activeConnection?.port ?? ''
      notification.warning({
        title: '连接已断开',
        content: `${host}:${port} 的连接意外终止`,
        duration: 5000,
        closable: true,
      })
    }
    previousStatus = newStatus
  }
)

/** 重连操作 */
function handleReconnect(): void {
  if (store.activeId) {
    store.testConnection(store.activeId)
  }
}
</script>

<template>
  <div class="connection-indicator" :class="statusClass">
    <!-- 连接中 + 已连接：圆点 + 文字 -->
    <n-tooltip v-if="isHealthyWithTooltip" trigger="hover">
      <template #trigger>
        <div class="indicator-content">
          <span class="status-dot" :class="statusClass" :style="{ background: dotColor }"></span>
          <span class="host-text" v-if="showHostInfo">{{ hostInfo }}</span>
          <span class="status-text">{{ statusLabel }}</span>
        </div>
      </template>
      {{ tooltipContent }}
    </n-tooltip>

    <!-- 未使用 Tooltip 的版本（无需 Tooltip 时直接渲染） -->
    <div v-else class="indicator-content">
      <span class="status-dot" :class="statusClass" :style="{ background: dotColor }"></span>
      <span class="host-text" v-if="showHostInfo">{{ hostInfo }}</span>
      <span class="status-text">{{ statusLabel }}</span>
      <!-- 断开状态的重连按钮 -->
      <button
        v-if="showReconnect"
        class="reconnect-btn"
        :disabled="store.isTesting"
        @click="handleReconnect"
      >
        {{ store.isTesting ? '连接中…' : '重连' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.connection-indicator {
  display: flex;
  align-items: center;
  height: 100%;
  padding: 0 4px;
}

.indicator-content {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 100%;
}

/* ─── 状态灯圆点 ─── */
.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
  transition: background-color 0.3s ease, opacity 0.2s ease;
}

/* 已连接：绿色发光 */
.state-healthy .status-dot,
.status-dot.state-healthy {
  box-shadow: 0 0 6px rgba(52, 211, 153, 0.5);
}

/* 连接中：脉冲呼吸动画 */
.state-connecting .status-dot,
.status-dot.state-connecting {
  animation: breathe 1.5s ease-in-out infinite;
}

@keyframes breathe {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.35; transform: scale(0.75); }
}

/* 已断开：慢速闪烁 */
.state-unreachable .status-dot,
.status-dot.state-unreachable {
  animation: blink 2s ease-in-out infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

/* ─── 文字样式 ─── */
.status-text {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
  white-space: nowrap;
  transition: color 0.3s ease;
}

.host-text {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-tertiary);
  white-space: nowrap;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ─── 重连按钮 ─── */
.reconnect-btn {
  background: none;
  border: none;
  font-family: var(--font-body);
  font-size: 12px;
  font-weight: 500;
  color: var(--accent-blue);
  cursor: pointer;
  padding: 2px 6px;
  border-radius: var(--radius-sm);
  transition: all var(--transition-fast);
  white-space: nowrap;
}

.reconnect-btn:hover {
  background: rgba(56, 189, 248, 0.1);
  color: #7DD3FC;
}

.reconnect-btn:disabled {
  color: var(--text-tertiary);
  cursor: not-allowed;
  background: none;
}
</style>
