<script setup lang="ts">
/**
 * ConnectionSwitcher — 连接快速切换下拉组件
 *
 * 替换 InputArea 中的静态连接 badge，点击展开连接列表，
 * 支持快速切换当前活跃连接。下拉向上展开，避免被底部裁切。
 *
 * 依据 frontend AGENTS.md §4 输入区行为规范
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import type { ConnectionConfig, DbType } from '@/types/connection'

const props = defineProps<{
  /** 所有可用连接列表 */
  connections: ConnectionConfig[]
  /** 当前活跃连接 ID */
  activeId: string | null
}>()

const emit = defineEmits<{
  'select-connection': [id: string]
}>()

const router = useRouter()

// ─── 状态 ───

const isOpen = ref(false)

// ─── 数据库类型徽标配置（与 ConnectionList.vue 一致） ───

const dbBadge: Record<DbType, { label: string; color: string }> = {
  mysql: { label: 'MySQL', color: '#F97316' },
  postgresql: { label: 'PG', color: '#3B82F6' },
  oracle: { label: 'ORA', color: '#EF4444' },
}

// ─── 状态灯配置（与 ConnectionList.vue 一致） ───
// healthy 统一使用绿色
const statusConfig: Record<string, { color: string; label: string; pulse: boolean }> = {
  healthy: { color: '#34D399', label: '已连接', pulse: false },
  connecting: { color: '#FBBF24', label: '连接中…', pulse: true },
  unreachable: { color: '#F87171', label: '连接断开', pulse: true },
  degraded: { color: '#FBBF24', label: '部分受限', pulse: false },
  unknown: { color: '#64748B', label: '未测试', pulse: false },
}

// ─── Getters ───

/** 当前活跃连接对象 */
const activeConnection = computed(() => {
  if (!props.activeId) return null
  return props.connections.find((c) => c.id === props.activeId) ?? null
})

/** trigger 显示文本 */
const triggerLabel = computed(() => {
  const c = activeConnection.value
  if (!c) return '请先连接数据库'
  return `${c.db_type}  ${c.host}:${c.port}/${c.database}`
})

/** 当前活跃连接的状态（优先使用 store 运行时状态） */
function getStatus(conn: ConnectionConfig): { color: string; label: string; pulse: boolean } {
  if (conn.id === props.activeId) {
    // 活跃连接使用 props 无法感知 store.status 的变化，
    // 但 ChatPanel 会在 status 变化时重新渲染整个组件树，
    // 所以这里从 conn.status 读取（由 ChatPanel 传入最新连接对象）
    return statusConfig[conn.status] ?? statusConfig.unknown
  }
  return statusConfig[conn.status] ?? statusConfig.unknown
}

// ─── 事件处理 ───

function toggleDropdown(): void {
  isOpen.value = !isOpen.value
}

function selectConnection(id: string): void {
  emit('select-connection', id)
  isOpen.value = false
}

function goToConnections(): void {
  isOpen.value = false
  router.push('/connections')
}

// ─── 点击外部关闭 ───

function onClickOutside(e: MouseEvent): void {
  const target = e.target as HTMLElement
  if (!target.closest('.connection-switcher')) {
    isOpen.value = false
  }
}

function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape' && isOpen.value) {
    isOpen.value = false
  }
}

onMounted(() => {
  document.addEventListener('click', onClickOutside)
  document.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  document.removeEventListener('click', onClickOutside)
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div class="connection-switcher">
    <!-- trigger -->
    <button
      class="switcher-trigger"
      :class="{ connected: !!activeId }"
      :title="activeConnection ? activeConnection.name : ''"
      @click="toggleDropdown"
    >
      <span class="trigger-text">{{ triggerLabel }}</span>
      <svg
        class="trigger-arrow"
        :class="{ open: isOpen }"
        width="10"
        height="6"
        viewBox="0 0 10 6"
        fill="none"
        stroke="currentColor"
        stroke-width="1.5"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <path d="M1 1l4 4 4-4" />
      </svg>
    </button>

    <!-- dropdown -->
    <Transition name="dropdown">
      <div v-if="isOpen" class="switcher-dropdown">
        <!-- 空状态 -->
        <div v-if="connections.length === 0" class="empty-state">
          暂无连接，请先创建
        </div>

        <!-- 连接列表 -->
        <div v-else class="conn-list">
          <div
            v-for="conn in connections"
            :key="conn.id"
            class="conn-item"
            :class="{ active: conn.id === activeId }"
            @click="selectConnection(conn.id)"
          >
            <!-- 活跃指示线 -->
            <div v-if="conn.id === activeId" class="item-active-bar"></div>

            <!-- 状态灯 -->
            <span
              class="item-status-dot"
              :class="{ pulse: getStatus(conn).pulse }"
              :style="{ background: getStatus(conn).color }"
            ></span>

            <!-- db_type 徽标 -->
            <span
              class="item-db-badge"
              :style="{ color: dbBadge[conn.db_type].color, background: dbBadge[conn.db_type].color + '18' }"
            >
              {{ dbBadge[conn.db_type].label }}
            </span>

            <!-- 连接信息 -->
            <div class="item-info">
              <span class="item-name">{{ conn.name }}</span>
              <span class="item-conn-str">{{ conn.host }}:{{ conn.port }}/{{ conn.database }}</span>
            </div>

            <!-- 状态标签 -->
            <span class="item-status-text" :style="{ color: getStatus(conn).color }">
              {{ getStatus(conn).label }}
            </span>
          </div>
        </div>

        <!-- 底部：管理连接 -->
        <div class="dropdown-footer" @click="goToConnections">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <line x1="12" y1="5" x2="12" y2="19"/>
            <line x1="5" y1="12" x2="19" y2="12"/>
          </svg>
          <span>管理连接</span>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
/* ─── 容器 ─── */
.connection-switcher {
  position: relative;
}

/* ─── trigger ─── */
.switcher-trigger {
  display: flex;
  align-items: center;
  gap: 4px;
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-tertiary);
  padding: 2px 8px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  white-space: nowrap;
  max-width: 200px;
  background: transparent;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.switcher-trigger:hover {
  border-color: #334155;
  background: var(--bg-hover);
}

.switcher-trigger.connected {
  color: var(--accent-teal);
  border-color: var(--accent-soft-border);
}

.switcher-trigger.connected:hover {
  border-color: var(--accent-soft-border-hover);
}

.trigger-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.trigger-arrow {
  flex-shrink: 0;
  transition: transform var(--transition-fast);
}

.trigger-arrow.open {
  transform: rotate(180deg);
}

/* ─── dropdown ─── */
.switcher-dropdown {
  position: absolute;
  bottom: calc(100% + 6px);
  right: 0;
  min-width: 280px;
  max-width: 360px;
  background: var(--bg-elevated, var(--bg-surface));
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25), 0 2px 6px rgba(0, 0, 0, 0.15);
  z-index: 100;
  overflow: hidden;
}

/* ─── 空状态 ─── */
.empty-state {
  padding: 24px 16px;
  text-align: center;
  color: var(--text-tertiary);
  font-family: var(--font-body);
  font-size: 13px;
}

/* ─── 连接列表 ─── */
.conn-list {
  max-height: 280px;
  overflow-y: auto;
  padding: 4px;
}

.conn-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background var(--transition-fast);
  overflow: hidden;
}

.conn-item:hover {
  background: var(--bg-hover);
}

.conn-item.active {
  background: var(--conn-card-active-bg);
}

.item-active-bar {
  position: absolute;
  left: 0;
  top: 4px;
  bottom: 4px;
  width: 3px;
  background: #34D399;
  border-radius: 0 2px 2px 0;
}

/* ─── 状态灯 ─── */
.item-status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}

.item-status-dot.pulse {
  animation: dot-pulse 1.5s ease-in-out infinite;
}

@keyframes dot-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

/* ─── db_type 徽标 ─── */
.item-db-badge {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 500;
  padding: 1px 5px;
  border-radius: 4px;
  flex-shrink: 0;
  line-height: 1.4;
}

/* ─── 连接信息 ─── */
.item-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.item-name {
  font-family: var(--font-body);
  font-size: 12px;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.item-conn-str {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-tertiary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ─── 状态文本 ─── */
.item-status-text {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 500;
  flex-shrink: 0;
  white-space: nowrap;
}

/* ─── 底部链接 ─── */
.dropdown-footer {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border-top: 1px solid var(--border-color);
  color: var(--text-tertiary);
  font-family: var(--font-body);
  font-size: 12px;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.dropdown-footer:hover {
  color: var(--accent-teal);
  background: var(--bg-hover);
}

/* ─── 展开/收起动画 ─── */
.dropdown-enter-active {
  transition: all 0.15s ease-out;
}

.dropdown-leave-active {
  transition: all 0.1s ease-in;
}

.dropdown-enter-from {
  opacity: 0;
  transform: translateY(4px);
}

.dropdown-leave-to {
  opacity: 0;
  transform: translateY(4px);
}
</style>
