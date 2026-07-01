<script setup lang="ts">
/**
 * ConnectionView — 连接管理页面
 *
 * 组合 ConnectionList（列表视图）与 ConnectionForm（编辑视图），
 * 管理两种视图间的切换与数据流。
 *
 * 依据 api-contract §三 ConnectionView 组件树
 */
import { ref, onMounted } from 'vue'
import { NButton } from 'naive-ui'
import { useConnectionStore } from '@/stores/connection'
import ConnectionList from '@/components/connection/ConnectionList.vue'
import ConnectionForm from '@/components/connection/ConnectionForm.vue'
import type { ConnectionConfig } from '@/types/connection'

const store = useConnectionStore()

type ViewMode = 'list' | 'form'

/** 当前视图模式 */
const view = ref<ViewMode>('list')

/** 编辑中的连接 */
const editingConnection = ref<ConnectionConfig | null>(null)

/** 页面是否首次加载 */
const initialLoad = ref(true)

// 页面加载时刷新连接列表
onMounted(async () => {
  // 先展示 localStorage 缓存的列表，再异步刷新
  if (store.connections.length === 0) {
    await store.loadConnections()
  } else {
    // 已有缓存，静默刷新
    store.loadConnections()
  }
  initialLoad.value = false
})

/** 切换到新建表单 */
function handleCreate() {
  editingConnection.value = null
  view.value = 'form'
}

/** 切换到编辑表单 */
function handleEdit(config: ConnectionConfig) {
  editingConnection.value = config
  view.value = 'form'
}

/** 保存成功回调 */
function handleSaved(config: ConnectionConfig) {
  // 保存后自动设为活跃连接并进行测试
  store.setActiveConnection(config.id)
  // 返回列表
  view.value = 'list'
  editingConnection.value = null
}

/** 取消编辑 */
function handleCancel() {
  view.value = 'list'
  editingConnection.value = null
}
</script>

<template>
  <div class="connection-view">
    <!-- 页面头部 -->
    <div class="page-header">
      <div class="header-left">
        <h1 class="page-title">连接管理</h1>
        <span v-if="store.connections.length > 0" class="conn-count">
          {{ store.connections.length }} 个连接
        </span>
      </div>
      <div v-if="view === 'list'" class="header-actions">
        <n-button type="primary" size="small" @click="handleCreate">
          <template #icon>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
              <line x1="12" y1="5" x2="12" y2="19"/>
              <line x1="5" y1="12" x2="19" y2="12"/>
            </svg>
          </template>
          新建连接
        </n-button>
      </div>
    </div>

    <!-- 列表视图 -->
    <div v-if="view === 'list'" class="list-container">
      <div v-if="initialLoad && store.connections.length === 0" class="loading-state">
        <p>正在加载连接列表…</p>
      </div>
      <ConnectionList
        v-else
        @create="handleCreate"
        @edit="handleEdit"
      />
    </div>

    <!-- 表单视图 -->
    <div v-else class="form-container">
      <ConnectionForm
        :connection="editingConnection"
        @saved="handleSaved"
        @cancel="handleCancel"
      />
    </div>
  </div>
</template>

<style scoped>
.connection-view {
  height: 100%;
  display: flex;
  flex-direction: column;
}

/* ─── 页面头部 ─── */
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.page-title {
  font-family: var(--font-display);
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: 0.2px;
}

.conn-count {
  font-size: 13px;
  color: var(--text-tertiary);
  font-family: var(--font-mono);
}

/* ─── 列表容器 ─── */
.list-container {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}

/* ─── 表单容器 ─── */
.form-container {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}

/* ─── 加载状态 ─── */
.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 200px;
  color: var(--text-tertiary);
  font-size: 14px;
}
</style>
