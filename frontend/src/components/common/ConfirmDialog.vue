<script setup lang="ts">
/**
 * ConfirmDialog — 危险操作二次确认对话框
 *
 * 写操作三要素：SQL 语法高亮 + 影响行数 + 危险色按钮 1.5s 冷却。
 * 不提供"不再提示"、overlay 点击不关闭、Escape = 取消。
 *
 * 依据 frontend AGENTS.md §1 危险操作确认机制
 *     api-contract §1.5 suggestion_is_destructive
 *     api-contract §2.4 DiagnosisResult is_destructive
 */
import { ref, computed, watch, onUnmounted } from 'vue'
import { NModal, NButton } from 'naive-ui'
import SqlBlock from '@/components/sql/SqlBlock.vue'
import ErrorCard from '@/components/common/ErrorCard.vue'

const props = defineProps<{
  /** 对话框是否可见 */
  show: boolean
  /** 即将执行的 SQL */
  sql: string
  /** 预估影响行数（null = 未知） */
  impactEstimate?: string | null
  /** 审计状态 */
  auditStatus?: string
  /** 是否只读 */
  isReadonly?: boolean
  /** 是否正在执行 */
  loading?: boolean
  /** 执行错误信息 */
  errorMessage?: string | null
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
  confirm: []
  cancel: []
}>()

// ─── 1.5s 冷却倒计时 ───

const COOLDOWN_MS = 1500
const cooldownRemaining = ref(0)
let cooldownTimer: ReturnType<typeof setInterval> | null = null
let cooldownTimeout: ReturnType<typeof setTimeout> | null = null

/** 是否处于冷却中 */
const isCooldown = computed(() => cooldownRemaining.value > 0)

/** 按钮文案 */
const confirmText = computed(() => {
  if (props.loading) return '执行中…'
  if (isCooldown.value) return `确认执行 (${(cooldownRemaining.value / 1000).toFixed(1)}s)`
  return '确认执行'
})

/** 启动冷却 */
function startCooldown(): void {
  cooldownRemaining.value = COOLDOWN_MS
  cooldownTimer = setInterval(() => {
    cooldownRemaining.value = Math.max(0, cooldownRemaining.value - 100)
  }, 100)
  cooldownTimeout = setTimeout(() => {
    stopCooldown()
  }, COOLDOWN_MS)
}

function stopCooldown(): void {
  if (cooldownTimer) {
    clearInterval(cooldownTimer)
    cooldownTimer = null
  }
  if (cooldownTimeout) {
    clearTimeout(cooldownTimeout)
    cooldownTimeout = null
  }
  cooldownRemaining.value = 0
}

// 对话框显示时启动冷却
watch(() => props.show, (show) => {
  if (show) {
    startCooldown()
  } else {
    stopCooldown()
  }
})

onUnmounted(() => {
  stopCooldown()
})

// ─── 事件 ───

function handleConfirm(): void {
  if (isCooldown.value || props.loading) return
  emit('confirm')
}

function handleCancel(): void {
  if (props.loading) return
  emit('update:show', false)
  emit('cancel')
}

/** 关闭对话框（仅 Escape 触发） */
function handleClose(): void {
  if (!props.loading) {
    emit('update:show', false)
    emit('cancel')
  }
}

/** 重试（清除错误后继续） */
function handleRetry(): void {
  emit('confirm')
}
</script>

<template>
  <n-modal
    :show="show"
    :mask-closable="false"
    :close-on-esc="!loading"
    preset="card"
    title="确认执行"
    size="small"
    :bordered="true"
    :style="{ maxWidth: '640px', width: '90%' }"
    :segmented="{ content: true, footer: true }"
    @update:show="handleClose"
  >
    <!-- 警告提示 -->
    <div class="confirm-banner">
      <span class="banner-icon">⚠️</span>
      <span class="banner-text">即将执行以下 SQL 语句，请仔细确认</span>
    </div>

    <!-- SQL 代码块 -->
    <div class="sql-section">
      <SqlBlock
        :sql="sql"
        :audit-status="auditStatus"
        :is-readonly="isReadonly"
      />
    </div>

    <!-- 影响范围 -->
    <div class="impact-section">
      <span class="impact-label">预估影响范围：</span>
      <span v-if="impactEstimate" class="impact-value">{{ impactEstimate }}</span>
      <span v-else class="impact-unknown">影响范围未知——请人工评估</span>
    </div>

    <!-- 错误消息 -->
    <div v-if="errorMessage" class="error-section">
      <ErrorCard
        severity="error"
        :content="errorMessage"
        @retry="handleRetry"
      />
    </div>

    <!-- 按钮区 -->
    <template #footer>
      <div class="dialog-footer">
        <n-button
          secondary
          :disabled="loading"
          @click="handleCancel"
        >
          取消
        </n-button>
        <n-button
          type="error"
          :loading="loading"
          :disabled="isCooldown || loading"
          @click="handleConfirm"
        >
          {{ confirmText }}
        </n-button>
      </div>
    </template>
  </n-modal>
</template>

<style scoped>
.confirm-banner {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  background: rgba(248, 113, 113, 0.08);
  border: 1px solid rgba(248, 113, 113, 0.2);
  border-radius: var(--radius-md);
  margin-bottom: 12px;
}

.banner-icon {
  font-size: 14px;
}

.banner-text {
  font-size: 13px;
  color: var(--color-error);
  font-weight: 450;
}

/* SQL 区域 */
.sql-section {
  margin-bottom: 12px;
}

/* 影响范围 */
.impact-section {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 12px;
  background: var(--bg-surface);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  margin-bottom: 12px;
}

.impact-label {
  font-size: 12px;
  color: var(--text-secondary);
}

.impact-value {
  font-size: 12px;
  color: var(--color-warning);
  font-weight: 500;
}

.impact-unknown {
  font-size: 12px;
  color: var(--color-warning);
  font-weight: 500;
}

/* 错误区域 */
.error-section {
  margin-bottom: 12px;
}

/* 按钮区 */
.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 4px;
}
</style>
