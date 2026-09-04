<script setup lang="ts">
/**
 * AppLayout — 应用主布局
 *
 * Sidebar + 内容区 + 空闲超时管理（无顶栏：品牌/连接状态均收进侧栏底部坞）。
 */
import { watch, onMounted, onUnmounted } from 'vue'
import { useNotification } from 'naive-ui'
import Sidebar from './Sidebar.vue'
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
  background: var(--bg-primary);
}

.content-grid {
  height: 100%;
  overflow-y: auto;
  padding: 24px;
  /* max-width: 1020px;
  margin: 0 auto; */
}
</style>
