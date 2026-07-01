<script setup lang="ts">
/**
 * ThinkingIndicator — Agent 思考中动画 + 实时耗时计数器
 *
 * isStreaming=true 且尚未收到有效结果时展示。
 * 三点脉冲动画 + 毫秒递增计时器，超过 10s 橙色，超过 30s 红色 + 超时警告。
 *
 * 依据 frontend AGENTS.md §3（Agent 思考阶段展示动画 + 毫秒递增耗时）
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'

const props = defineProps<{
  /** 是否可见（isStreaming && 无有效结果） */
  visible: boolean
}>()

// ─── 计时器 ───

const startTime = ref(0)
const elapsedMs = ref(0)
let timerInterval: ReturnType<typeof setInterval> | null = null

/** 格式化耗时 X.Xs */
const formattedTime = computed(() => {
  const sec = elapsedMs.value / 1000
  return `${sec.toFixed(1)}s`
})

/** 耗时颜色等级 */
const timeColorClass = computed(() => {
  if (elapsedMs.value >= 30000) return 'danger'
  if (elapsedMs.value >= 10000) return 'warn'
  return 'normal'
})

/** 是否显示超时警告 */
const showTimeoutWarning = computed(() => elapsedMs.value >= 30000)

function startTimer(): void {
  if (timerInterval) return
  startTime.value = Date.now()
  elapsedMs.value = 0
  timerInterval = setInterval(() => {
    elapsedMs.value = Date.now() - startTime.value
  }, 100)
}

function stopTimer(): void {
  if (timerInterval) {
    clearInterval(timerInterval)
    timerInterval = null
  }
}

onMounted(() => {
  if (props.visible) startTimer()
})

onUnmounted(() => {
  stopTimer()
})
</script>

<template>
  <Transition name="fade-slide">
    <div v-if="visible" class="thinking-indicator">
      <div class="thinking-content">
        <!-- 三点脉冲 -->
        <div class="dot-container">
          <span class="dot" :class="timeColorClass"></span>
          <span class="dot" :class="timeColorClass"></span>
          <span class="dot" :class="timeColorClass"></span>
        </div>

        <!-- 文字 -->
        <span class="thinking-label" :class="timeColorClass">AI 正在分析</span>

        <!-- 计时器 -->
        <span class="thinking-timer" :class="timeColorClass">{{ formattedTime }}</span>

        <!-- 超时警告 -->
        <span v-if="showTimeoutWarning" class="timeout-warning">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10"/>
            <line x1="12" y1="8" x2="12" y2="12"/>
            <line x1="12" y1="16" x2="12.01" y2="16"/>
          </svg>
          查询超时警告
        </span>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.thinking-indicator {
  padding: 8px 20px;
  display: flex;
  align-items: center;
}

.thinking-content {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  background: var(--bg-surface);
  border: 1px solid var(--border-color);
  border-radius: 20px;
}

/* ─── 三点脉冲 ─── */
.dot-container {
  display: flex;
  align-items: center;
  gap: 4px;
}

.dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--text-tertiary);
  animation: dot-bounce 1.4s ease-in-out infinite both;
}

.dot:nth-child(1) { animation-delay: 0s; }
.dot:nth-child(2) { animation-delay: 0.2s; }
.dot:nth-child(3) { animation-delay: 0.4s; }

@keyframes dot-bounce {
  0%, 80%, 100% {
    opacity: 0.3;
    transform: scale(0.8);
  }
  40% {
    opacity: 1;
    transform: scale(1.1);
  }
}

/* 颜色等级 */
.dot.warn { background: var(--color-warning); }
.dot.danger { background: var(--color-error); }

/* ─── 文字 ─── */
.thinking-label {
  font-size: 12px;
  font-weight: 450;
  color: var(--text-secondary);
  transition: color 0.5s;
}

.thinking-label.warn { color: var(--color-warning); }
.thinking-label.danger { color: var(--color-error); }

/* ─── 计时器 ─── */
.thinking-timer {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-tertiary);
  transition: color 0.5s;
}

.thinking-timer.warn { color: var(--color-warning); }
.thinking-timer.danger { color: var(--color-error); }

/* ─── 超时警告 ─── */
.timeout-warning {
  display: flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
  color: var(--color-error);
  font-weight: 500;
}

/* ─── 过渡动画 ─── */
.fade-slide-enter-active,
.fade-slide-leave-active {
  transition: all 0.25s ease;
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
