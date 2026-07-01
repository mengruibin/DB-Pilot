<script setup lang="ts">
/**
 * ErrorCard — 分级错误提示卡片
 *
 * ℹ️ info（蓝色）/ ⚠️ warning（黄色）/ 🛑 error（红色）三级
 * 区分用户误操作 vs 系统故障，提供修正指引或重试按钮。
 *
 * 依据 frontend AGENTS.md §3 错误分级提示
 *     api-contract §1.6 错误码体系
 */
import { computed, ref } from 'vue'
import { NButton } from 'naive-ui'

const props = defineProps<{
  /** 严重级别 */
  severity?: 'info' | 'warning' | 'error'
  /** 错误码 */
  errorCode?: string
  /** 面向用户的消息 */
  userMessage?: string
  /** 后备消息内容 */
  content?: string
}>()

const emit = defineEmits<{
  close: []
  retry: []
}>()

/** 是否已关闭 */
const dismissed = ref(false)

/** 错误类型标签：用户可修正 vs 系统故障 */
const USER_FIXABLE_CODES = new Set([
  'SQL_AUDIT_BLOCKED',
  'INVALID_PARAM',
  'NOT_FOUND',
])

const SYSTEM_ERROR_CODES = new Set([
  'AGENT_ERROR',
  'DB_UNREACHABLE',
  'EXECUTION_TIMEOUT',
  'TOO_MANY_STREAMS',
  'PERMISSION_DENIED',
])

const errorCategory = computed<'user' | 'system' | null>(() => {
  if (!props.errorCode) return null
  if (USER_FIXABLE_CODES.has(props.errorCode)) return 'user'
  if (SYSTEM_ERROR_CODES.has(props.errorCode)) return 'system'
  return null
})

/** 修正指引文本 */
const guidanceText = computed(() => {
  switch (props.errorCode) {
    case 'SQL_AUDIT_BLOCKED':
      return 'SQL 包含危险操作，请检查后重试'
    case 'INVALID_PARAM':
      return '请检查输入参数是否正确'
    case 'NOT_FOUND':
      return '请确认资源是否存在'
    case 'PERMISSION_DENIED':
      return '请联系管理员获取权限'
    case 'EXECUTION_TIMEOUT':
      return '查询超时，建议添加索引或缩小查询范围'
    case 'AGENT_ERROR':
      return 'AI 服务暂时不可用，请稍后重试'
    case 'DB_UNREACHABLE':
      return '请检查网络和防火墙设置'
    case 'TOO_MANY_STREAMS':
      return '请等待或取消其他会话'
    default:
      return null
  }
})

/** 显示重试按钮 */
const showRetry = computed(() => {
  return props.errorCode === undefined || SYSTEM_ERROR_CODES.has(props.errorCode)
})

/** 显示文本 */
const displayMessage = computed(() => {
  return props.userMessage || props.content || '未知错误'
})

/** 严重级别配置 */
const severityConfig = computed(() => {
  switch (props.severity) {
    case 'info':
      return {
        icon: 'ℹ️',
        label: '提示',
        accentColor: 'var(--color-info)',
        bgColor: 'rgba(96, 165, 250, 0.06)',
        borderColor: 'rgba(96, 165, 250, 0.2)',
      }
    case 'warning':
      return {
        icon: '⚠️',
        label: '警告',
        accentColor: 'var(--color-warning)',
        bgColor: 'rgba(251, 191, 36, 0.06)',
        borderColor: 'rgba(251, 191, 36, 0.2)',
      }
    default: // error
      return {
        icon: '🛑',
        label: '错误',
        accentColor: 'var(--color-error)',
        bgColor: 'rgba(248, 113, 113, 0.06)',
        borderColor: 'rgba(248, 113, 113, 0.2)',
      }
  }
})

function handleClose(): void {
  dismissed.value = true
  emit('close')
}

function handleRetry(): void {
  emit('retry')
}
</script>

<template>
  <Transition name="fade-slide">
    <div
      v-if="!dismissed"
      class="error-card"
      :class="`severity-${severity ?? 'error'}`"
      :style="{
        background: severityConfig.bgColor,
        borderColor: severityConfig.borderColor,
      }"
    >
      <!-- 左侧色条 -->
      <div
        class="severity-bar"
        :style="{ background: severityConfig.accentColor }"
      ></div>

      <!-- 图标 -->
      <span class="severity-icon">{{ severityConfig.icon }}</span>

      <!-- 内容区 -->
      <div class="card-body">
        <!-- 标题行 -->
        <div class="card-header-row">
          <span class="severity-label" :style="{ color: severityConfig.accentColor }">
            {{ severityConfig.label }}
          </span>
          <span v-if="errorCategory" class="error-category-tag" :class="errorCategory">
            {{ errorCategory === 'user' ? '用户可修正' : '系统故障' }}
          </span>
        </div>

        <!-- 错误消息 -->
        <p class="error-message">{{ displayMessage }}</p>

        <!-- 修正指引 -->
        <p v-if="guidanceText" class="guidance-text">{{ guidanceText }}</p>

        <!-- 操作按钮 -->
        <div v-if="showRetry || errorCode" class="card-actions">
          <n-button
            v-if="showRetry"
            size="tiny"
            secondary
            class="retry-btn"
            @click="handleRetry"
          >
            <template #icon>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="23 4 23 10 17 10"/>
                <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
              </svg>
            </template>
            重试
          </n-button>
        </div>
      </div>

      <!-- 关闭按钮 -->
      <button class="close-btn" @click="handleClose">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <line x1="18" y1="6" x2="6" y2="18"/>
          <line x1="6" y1="6" x2="18" y2="18"/>
        </svg>
      </button>
    </div>
  </Transition>
</template>

<style scoped>
.error-card {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 14px;
  border: 1px solid;
  border-radius: var(--radius-md);
  position: relative;
  min-height: 48px;
}

/* 左侧色条 */
.severity-bar {
  position: absolute;
  left: 0;
  top: 4px;
  bottom: 4px;
  width: 3px;
  border-radius: 0 2px 2px 0;
}

/* 图标 */
.severity-icon {
  font-size: 16px;
  flex-shrink: 0;
  margin-top: 1px;
  position: relative;
}

/* 内容区 */
.card-body {
  flex: 1;
  min-width: 0;
}

.card-header-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.severity-label {
  font-family: var(--font-display);
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}

.error-category-tag {
  font-size: 10px;
  font-weight: 500;
  padding: 1px 6px;
  border-radius: 3px;
}

.error-category-tag.user {
  background: rgba(45, 212, 191, 0.12);
  color: var(--accent-teal);
}

.error-category-tag.system {
  background: rgba(248, 113, 113, 0.12);
  color: var(--color-error);
}

.error-message {
  font-size: 13px;
  color: var(--text-primary);
  line-height: 1.5;
  word-break: break-word;
}

.guidance-text {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 4px;
  line-height: 1.4;
}

/* 操作按钮 */
.card-actions {
  margin-top: 8px;
  display: flex;
  gap: 6px;
}

.retry-btn {
  color: var(--text-secondary) !important;
  font-size: 11px !important;
}

.retry-btn:hover {
  color: var(--accent-blue) !important;
}

/* 关闭按钮 */
.close-btn {
  position: absolute;
  top: 6px;
  right: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  transition: all var(--transition-fast);
  padding: 0;
}

.close-btn:hover {
  background: var(--bg-hover);
  color: var(--text-secondary);
}

/* 过渡动画 */
.fade-slide-enter-active,
.fade-slide-leave-active {
  transition: all 0.2s ease;
}

.fade-slide-enter-from {
  opacity: 0;
  transform: translateY(-4px);
}

.fade-slide-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
