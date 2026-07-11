<script setup lang="ts">
/**
 * ConnectionList — 连接卡片列表
 *
 * 渲染已保存连接卡片（name/db_type/host:port/database + 状态灯），
 * 空列表展示引导文案。
 *
 * 依据 api-contract §三 ConnectionList 组件
 */
import { NButton, NTag, NPopconfirm } from 'naive-ui'
import { useConnectionStore } from '@/stores/connection'
import type { ConnectionConfig, DbType } from '@/types/connection'

const store = useConnectionStore()

const emit = defineEmits<{
  create: []
  edit: [config: ConnectionConfig]
}>()

/** 数据库类型徽标配置 */
const dbBadge: Record<DbType, { label: string; color: string }> = {
  mysql: { label: 'MySQL', color: '#F97316' },
  postgresql: { label: 'PG', color: '#3B82F6' },
  oracle: { label: 'ORA', color: '#EF4444' },
}

/** 状态灯配置 — healthy 统一使用绿色 */
const statusConfig: Record<string, { color: string; label: string; pulse: boolean }> = {
  healthy: { color: '#34D399', label: '已连接', pulse: false },
  connecting: { color: '#FBBF24', label: '连接中…', pulse: true },
  unreachable: { color: '#F87171', label: '连接断开', pulse: true },
  degraded: { color: '#FBBF24', label: '部分受限', pulse: false },
  unknown: { color: '#64748B', label: '未测试', pulse: false },
}

/** 当前活跃连接的状态 */
function getStatusLabel(conn: ConnectionConfig): string {
  if (conn.id === store.activeId) {
    return statusConfig[store.status]?.label ?? '未知'
  }
  return statusConfig[conn.status]?.label ?? '未测试'
}

function getStatusColor(conn: ConnectionConfig): string {
  if (conn.id === store.activeId) {
    return statusConfig[store.status]?.color ?? '#64748B'
  }
  return statusConfig[conn.status]?.color ?? '#64748B'
}

function getStatusPulse(conn: ConnectionConfig): boolean {
  if (conn.id === store.activeId) {
    return statusConfig[store.status]?.pulse ?? false
  }
  return false
}

/** 格式化时间 */
function formatTime(iso: string | null): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  } catch {
    return '—'
  }
}

/** 删除连接 */
async function handleDelete(id: string): Promise<void> {
  try {
    await store.removeConnection(id)
  } catch {
    // 错误已在 ApiError 中处理
  }
}
</script>

<template>
  <div class="connection-list">
    <!-- 空状态 -->
    <div v-if="store.connections.length === 0" class="empty-state">
      <div class="empty-icon">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
          <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
        </svg>
      </div>
      <p class="empty-text">尚无数据库连接</p>
      <p class="empty-hint">添加数据库连接后即可开始智能查询与诊断</p>
      <n-button type="primary" size="small" @click="emit('create')">
        <template #icon>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        </template>
        新建连接
      </n-button>
    </div>

    <!-- 连接卡片网格 -->
    <div v-else class="card-grid">
      <div
        v-for="(conn, idx) in store.connections"
        :key="conn.id"
        class="conn-card"
        :class="{ active: conn.id === store.activeId }"
        :style="{ animationDelay: `${idx * 0.04}s` }"
        @click="store.setActiveConnection(conn.id)"
      >
        <!-- 选中指示线 -->
        <div v-if="conn.id === store.activeId" class="active-bar"></div>

        <!-- 卡片头部：数据库类型徽标 + 名称 -->
        <div class="card-header">
          <div class="card-title-row">
            <n-tag
              :color="{ color: dbBadge[conn.db_type].color + '22', textColor: dbBadge[conn.db_type].color }"
              :bordered="false"
              size="small"
              class="db-tag"
            >
              {{ dbBadge[conn.db_type].label }}
            </n-tag>
            <span class="card-name">{{ conn.name }}</span>
          </div>
        </div>

        <!-- 卡片主体：连接信息 -->
        <div class="card-body">
          <div class="conn-info">
            <span class="info-user">{{ conn.user }}@</span>
            <span class="info-host">{{ conn.host }}:{{ conn.port }}/{{ conn.database }}</span>
          </div>
        </div>

        <!-- 卡片底部：状态 + 操作 -->
        <div class="card-footer">
          <div class="status-area">
            <span
              class="status-dot"
              :class="{ pulse: getStatusPulse(conn) }"
              :style="{ background: getStatusColor(conn) }"
            ></span>
            <span class="status-text" :style="{ color: getStatusColor(conn) }">
              {{ getStatusLabel(conn) }}
            </span>
            <span v-if="conn.last_tested_at" class="tested-at">
              · {{ formatTime(conn.last_tested_at) }}
            </span>
          </div>
          <div class="card-actions" @click.stop>
            <n-button
              text
              size="tiny"
              class="action-btn"
              @click="emit('edit', conn)"
            >
              编辑
            </n-button>
            <n-popconfirm @positive-click="handleDelete(conn.id)">
              <template #trigger>
                <n-button text size="tiny" class="action-btn danger">删除</n-button>
              </template>
              确定删除连接「{{ conn.name }}」？
            </n-popconfirm>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.connection-list {
  flex: 1;
}

/* ─── 空状态 ─── */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 300px;
  color: var(--text-tertiary);
}

.empty-icon {
  margin-bottom: 16px;
  opacity: 0.3;
}

.empty-text {
  font-family: var(--font-display);
  font-size: 16px;
  color: var(--text-secondary);
  margin-bottom: 4px;
}

.empty-hint {
  font-size: 13px;
  color: var(--text-tertiary);
  margin-bottom: 20px;
}

/* ─── 卡片网格 ─── */
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 10px;
  animation: cards-enter 0.3s ease both;
}

@keyframes cards-enter {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}

/* ─── 连接卡片 ─── */
.conn-card {
  position: relative;
  background: var(--bg-surface);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  padding: 14px 16px;
  cursor: pointer;
  transition: all var(--transition-fast);
  animation: cards-enter 0.3s ease both;
  overflow: hidden;
}

.conn-card:hover {
  border-color: #334155;
  background: var(--bg-hover);
}

.conn-card.active {
  border-color: var(--conn-card-active-border);
  background: var(--conn-card-active-bg);
}

.active-bar {
  position: absolute;
  left: 0;
  top: 8px;
  bottom: 8px;
  width: 3px;
  background: #34D399;
  border-radius: 0 2px 2px 0;
}

/* 卡片头部 */
.card-header {
  margin-bottom: 8px;
}

.card-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.db-tag {
  font-family: var(--font-mono);
  font-size: 11px !important;
  font-weight: 500;
  padding: 0 6px;
}

.card-name {
  font-family: var(--font-display);
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 卡片主体 */
.card-body {
  margin-bottom: 10px;
}

.conn-info {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.info-user {
  color: var(--text-tertiary);
}

/* 卡片底部 */
.card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.status-area {
  display: flex;
  align-items: center;
  gap: 6px;
}

.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}

.status-dot.pulse {
  animation: dot-pulse 1.5s ease-in-out infinite;
}

@keyframes dot-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

.status-text {
  font-size: 12px;
  font-weight: 500;
}

.tested-at {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-tertiary);
}

.card-actions {
  display: flex;
  gap: 4px;
  opacity: 0;
  transition: opacity var(--transition-fast);
}

.conn-card:hover .card-actions {
  opacity: 1;
}

.action-btn {
  color: var(--text-tertiary) !important;
  font-size: 12px !important;
  padding: 2px 6px !important;
}

.action-btn:hover {
  color: var(--text-secondary) !important;
}

.action-btn.danger:hover {
  color: var(--color-error) !important;
}
</style>