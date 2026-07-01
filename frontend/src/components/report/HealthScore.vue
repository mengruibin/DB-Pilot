<script setup lang="ts">
/**
 * HealthScore — 健康评分环形图组件
 *
 * SVG 环形图 + count-up 数字动画 + 评级标签。
 * 0-59 红色 / 60-79 黄色 / 80-100 绿色。
 *
 * 依据 api-contract §2.5 HealthReport（score 0-100）
 *     PRD §5.4 健康评分规则
 */
import { ref, computed, watch, onUnmounted } from 'vue'

const props = defineProps<{
  /** 健康评分 0-100 */
  score: number
  /** 是否自动播放动画 */
  animate?: boolean
  /** 环尺寸（默认 180） */
  size?: number
  /** 环粗细（默认 12） */
  strokeWidth?: number
}>()

const emit = defineEmits<{
  /** 动画完成 */
  'animated': []
}>()

// ─── 尺寸 ───

const S = computed(() => props.size ?? 180)
const SW = computed(() => props.strokeWidth ?? 12)
const radius = computed(() => (S.value - SW.value) / 2)
const circumference = computed(() => 2 * Math.PI * radius.value)
const center = computed(() => S.value / 2)

// ─── 动画状态 ───

const displayScore = ref(0)
let animationId: number | null = null

/** 开始 count-up 动画 */
function startAnimation(target: number): void {
  if (animationId) cancelAnimationFrame(animationId)
  displayScore.value = 0

  const duration = 600 // ms
  const startTime = performance.now()

  function tick(now: number): void {
    const elapsed = now - startTime
    const progress = Math.min(elapsed / duration, 1)
    // ease-out quad
    const eased = 1 - (1 - progress) * (1 - progress)
    displayScore.value = Math.round(eased * target)

    if (progress < 1) {
      animationId = requestAnimationFrame(tick)
    } else {
      displayScore.value = target
      emit('animated')
    }
  }

  animationId = requestAnimationFrame(tick)
}

watch(
  () => props.score,
  (val) => {
    if (props.animate !== false) {
      startAnimation(val)
    } else {
      displayScore.value = val
    }
  },
  { immediate: true }
)

onUnmounted(() => {
  if (animationId) cancelAnimationFrame(animationId)
})

// ─── 颜色 & 评级 ───

const scoreConfig = computed(() => {
  const s = props.score
  if (s >= 80) {
    return {
      start: '#34D399',
      end: '#10B981',
      label: '优秀',
      pulseColor: 'rgba(52, 211, 153, 0.3)',
    }
  }
  if (s >= 60) {
    return {
      start: '#FBBF24',
      end: '#F59E0B',
      label: '良好',
      pulseColor: 'rgba(251, 191, 36, 0.3)',
    }
  }
  return {
    start: '#F87171',
    end: '#EF4444',
    label: '危险',
    pulseColor: 'rgba(248, 113, 113, 0.3)',
  }
})

const gradientId = computed(() => `score-grad-${Math.random().toString(36).slice(2, 8)}`)

/** SVG stroke-dasharray / offset */
const dashOffset = computed(() => {
  const ratio = Math.min(Math.max(displayScore.value / 100, 0), 1)
  return circumference.value * (1 - ratio)
})
</script>

<template>
  <div class="health-score" :style="{ width: `${S}px` }">
    <svg
      :width="S"
      :height="S"
      :viewBox="`0 0 ${S} ${S}`"
      class="score-ring"
    >
      <!-- 渐变定义 -->
      <defs>
        <linearGradient :id="gradientId" x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" :stop-color="scoreConfig.start" />
          <stop offset="100%" :stop-color="scoreConfig.end" />
        </linearGradient>
      </defs>

      <!-- 背景环 -->
      <circle
        :cx="center"
        :cy="center"
        :r="radius"
        :stroke-width="SW"
        fill="none"
        stroke="rgba(255, 255, 255, 0.06)"
        class="bg-ring"
      />

      <!-- 前景环（分数环） -->
      <circle
        :cx="center"
        :cy="center"
        :r="radius"
        :stroke-width="SW"
        fill="none"
        :stroke="`url(#${gradientId})`"
        stroke-linecap="round"
        class="score-arc"
        :stroke-dasharray="circumference"
        :stroke-dashoffset="dashOffset"
        transform="rotate(-90, center, center)"
      />

      <!-- 中心文字 -->
      <text
        :x="center"
        :y="center - 8"
        text-anchor="middle"
        fill="var(--text-primary)"
        font-size="36"
        font-weight="700"
        font-family="var(--font-display, 'Outfit', sans-serif)"
        class="score-text"
      >
        {{ displayScore }}
      </text>
      <text
        :x="center"
        :y="center + 18"
        text-anchor="middle"
        fill="var(--text-tertiary)"
        font-size="11"
        font-family="var(--font-body, 'DM Sans', sans-serif)"
        letter-spacing="2"
      >
        SCORE
      </text>
    </svg>

    <!-- 评级标签 -->
    <div class="score-label" :style="{ color: scoreConfig.start }">
      {{ scoreConfig.label }}
    </div>

    <!-- 呼吸光晕 -->
    <div
      class="score-glow"
      :style="{ background: scoreConfig.pulseColor }"
    ></div>
  </div>
</template>

<style scoped>
.health-score {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  flex-shrink: 0;
}

.score-ring {
  position: relative;
  z-index: 1;
  filter: drop-shadow(0 0 8px rgba(255, 255, 255, 0.04));
}

.bg-ring {
  transition: stroke 0.3s;
}

.score-arc {
  transition: stroke-dashoffset 0.1s linear;
  filter: drop-shadow(0 0 6px var(--score-glow-color, transparent));
}

/* 中心数字 */
.score-text {
  dominant-baseline: central;
}

/* 评级标签 */
.score-label {
  font-family: var(--font-display, 'Outfit', sans-serif);
  font-size: 13px;
  font-weight: 600;
  margin-top: 4px;
  letter-spacing: 1px;
  text-transform: uppercase;
  position: relative;
  z-index: 1;
}

/* 呼吸光晕脉冲 */
.score-glow {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 160px;
  height: 160px;
  border-radius: 50%;
  transform: translate(-50%, -50%);
  z-index: 0;
  opacity: 0;
  pointer-events: none;
}

.health-score:hover .score-glow {
  animation: health-pulse 2s ease-out 2;
}

@keyframes health-pulse {
  0% {
    opacity: 0;
    transform: translate(-50%, -50%) scale(0.8);
  }
  50% {
    opacity: 0.6;
  }
  100% {
    opacity: 0;
    transform: translate(-50%, -50%) scale(1.4);
  }
}
</style>
