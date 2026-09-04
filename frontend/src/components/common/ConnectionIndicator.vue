<script setup lang="ts">
/**
 * ConnectionIndicator — 连接坞（原顶栏状态指示器，改为侧栏底部坞布局）
 *
 * 常驻侧栏底部，四态显示数据库连接状态：
 *   unknown（未连接）→ 灰色 / connecting（连接中）→ 🟡 脉冲
 *   healthy（已连接）→ 🟢 发光 / unreachable（连接断开）→ 🔴 + 重连
 *
 * 布局：单行坞 —— 左状态圆点 + （状态文字 / 库地址小字）堆叠，断开时右侧重连。
 * 整条可点击跳转「连接管理」；健康态 hover 的 title 展示 版本/延迟/最后测试 详情。
 *
 * 依据 frontend AGENTS.md §3 连接状态指示
 *     api-contract §三 ConnectionIndicator 组件
 */
import { computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useNotification } from 'naive-ui'
import { useConnectionStore } from '@/stores/connection'
import type { RuntimeStatus } from '@/stores/connection'

const store = useConnectionStore()
const router = useRouter()
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

/** 是否已选定目标库（有活跃连接对象，展示库地址小字） */
const showHost = computed(() => !!store.activeConnection)

/** 格式化后的连接字符串 host:port[/database] */
const hostInfo = computed(() => {
  const c = store.activeConnection
  if (!c) return ''
  return c.database ? `${c.host}:${c.port}/${c.database}` : `${c.host}:${c.port}`
})

/** 是否显示重连按钮 */
const showReconnect = computed(() => store.status === 'unreachable' && store.activeId)

/** hover 详情（healthy 时：库地址 + 版本/延迟/最后测试 + 点击提示） */
const dockTitle = computed(() => {
  const c = store.activeConnection
  if (!c) return ''
  if (store.status === 'healthy') {
    const parts = [hostInfo.value]
    parts.push(`版本：${store.testResult?.version ?? ''}`)
    if (store.testLatencyMs !== null) {
      parts.push(`延迟：${store.testLatencyMs}ms`)
    }
    if (store.lastTestedAt) {
      parts.push(`最后测试：${new Date(store.lastTestedAt).toLocaleString('zh-CN')}`)
    }
    parts.push('点击管理连接')
    return parts.join(' · ')
  }
  return '点击管理数据库连接'
})

/** 点击坞：跳转连接管理 */
function handleDockClick(): void {
  router.push('/connections')
}

/** 重连操作（阻止冒泡，避免触发坞的跳转） */
function handleReconnect(): void {
  if (store.activeId) {
    store.testConnection(store.activeId)
  }
}

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
</script>

<template>
  <div
    class="connection-dock"
    :class="statusClass"
    role="button"
    tabindex="0"
    :title="dockTitle || undefined"
    @click="handleDockClick"
    @keydown.enter="handleDockClick"
  >
    <span class="status-dot" :class="statusClass" :style="{ background: dotColor }"></span>
    <div class="dock-text">
      <span class="dock-status">{{ statusLabel }}</span>
      <span v-if="showHost" class="dock-host">{{ hostInfo }}</span>
    </div>
    <button
      v-if="showReconnect"
      class="reconnect-btn"
      :disabled="store.isTesting"
      @click.stop="handleReconnect"
    >
      {{ store.isTesting ? '连接中…' : '重连' }}
    </button>
  </div>
</template>

<style scoped>
/* 连接坞：单行、左圆点 + 右侧状态/库地址堆叠 */
.connection-dock {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 6px 10px;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background var(--transition-fast);
}

.connection-dock:hover {
  background: var(--bg-hover);
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

/* ─── 文字（右侧堆叠） ─── */
.dock-text {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
  line-height: 1.25;
}

.dock-status {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
  white-space: nowrap;
}

.dock-host {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-tertiary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ─── 重连按钮（右侧） ─── */
.reconnect-btn {
  margin-left: auto;
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
  flex-shrink: 0;
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
