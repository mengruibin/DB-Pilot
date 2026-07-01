<script setup lang="ts">
/**
 * AppLayout — 应用主布局
 *
 * 顶栏 + Sidebar + 内容区 + 空闲超时管理。
 */
import { watch, onMounted, onUnmounted } from 'vue'
import { useNotification } from 'naive-ui'
import Sidebar from './Sidebar.vue'
import ConnectionIndicator from './ConnectionIndicator.vue'
import { useConnectionStore } from '@/stores/connection'
import { useChatStore } from '@/stores/chat'
import { useIdleTimeout } from '@/composables/useIdleTimeout'

const notification = useNotification()
const connStore = useConnectionStore()
const chatStore = useChatStore()

/** 空闲超时状态 */
let countdownNotice: ReturnType<typeof notification.create> | null = null

const idleTimeout = useIdleTimeout(() => {
  // 超时回调：断开连接
  chatStore.cancelStreaming()
  connStore.status = 'unknown'
  if (countdownNotice) {
    countdownNotice.destroy()
    countdownNotice = null
  }
})

// 有活跃连接时启动空闲计时，断开时停止
watch(() => connStore.activeId, (id) => {
  if (id) {
    idleTimeout.start()
  } else {
    idleTimeout.stop()
    if (countdownNotice) {
      countdownNotice.destroy()
      countdownNotice = null
    }
  }
})

// 监听 countdownDisplay 变化显示通知
watch(() => idleTimeout.isWarning.value, (warning) => {
  if (warning) {
    showCountdownNotice()
  }
})

/** 显示倒计时通知 */
function showCountdownNotice(): void {
  countdownNotice = notification.warning({
    title: '会话即将超时',
    content: `将在 ${idleTimeout.countdownDisplay.value} 后断开连接，点击任意位置保持活跃`,
    duration: 0, // 不自动关闭
    closable: true,
    onClose: () => {
      idleTimeout.reset()
      countdownNotice = null
    },
  })
}

// 用户点击通知中的"保持活跃"或手动重置
function dismissNoticeAndReset(): void {
  if (countdownNotice) {
    countdownNotice.destroy()
    countdownNotice = null
  }
  idleTimeout.reset()
}

// 文档点击事件重置空闲计时（通知交互时）
onMounted(() => {
  document.addEventListener('click', handleDocClick)
})

onUnmounted(() => {
  idleTimeout.stop()
  if (countdownNotice) {
    countdownNotice.destroy()
  }
  document.removeEventListener('click', handleDocClick)
})

function handleDocClick(): void {
  // 如果倒计时通知显示中，点击任意位置重置
  if (idleTimeout.isWarning.value) {
    dismissNoticeAndReset()
  }
}
</script>

<template>
  <div class="layout">
    <!-- 顶栏 -->
    <header class="topbar">
      <div class="topbar-left">
        <span class="brand-label">DB-Pilot</span>
      </div>
      <div class="topbar-right">
        <ConnectionIndicator />
      </div>
    </header>

    <!-- 主体 -->
    <div class="layout-body">
      <Sidebar />
      <main class="content">
        <div class="content-grid">
          <router-view v-slot="{ Component }">
            <transition name="fade" mode="out-in">
              <component :is="Component" />
            </transition>
          </router-view>
        </div>
      </main>
    </div>
  </div>
</template>

<style scoped>
.layout {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--bg-primary);
}

/* ─── 顶栏 ─── */
.topbar {
  height: var(--topbar-height);
  min-height: var(--topbar-height);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border-color);
  z-index: 100;
}

.topbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand-label {
  font-family: var(--font-display);
  font-size: 13px;
  font-weight: 600;
  color: var(--text-tertiary);
  letter-spacing: 0.8px;
  text-transform: uppercase;
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 100%;
}

/* ─── 主体 ─── */
.layout-body {
  flex: 1;
  display: flex;
  overflow: hidden;
}

/* ─── 内容区 ─── */
.content {
  flex: 1;
  overflow: hidden;
  position: relative;
  background:
    linear-gradient(rgba(30, 41, 59, 0.3) 1px, transparent 1px),
    linear-gradient(90deg, rgba(30, 41, 59, 0.3) 1px, transparent 1px);
  background-size: 24px 24px;
}

.content-grid {
  height: 100%;
  overflow-y: auto;
  padding: 24px;
}
</style>
