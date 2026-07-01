/**
 * 空闲超时 composable
 *
 * 监听用户活动，30 分钟无操作触发超时回调。
 * 超时前 2 分钟提供倒计时状态（用于展示通知）。
 * 用户任一操作（点击/键盘/触屏）重置计时器。
 *
 * 依据 frontend AGENTS.md §1 会话超时与空闲策略
 */
import { ref, computed, onUnmounted } from 'vue'

/** 空闲超时毫秒（30 分钟） */
const IDLE_TIMEOUT_MS = 30 * 60 * 1000

/** 提前警告毫秒（2 分钟） */
const WARNING_BEFORE_MS = 2 * 60 * 1000

/** 事件列表——用户活动（排除鼠标移动/滚动等非意图事件） */
const ACTIVITY_EVENTS = ['click', 'keydown', 'touchstart'] as const

export function useIdleTimeout(onTimeout?: () => void) {
  // ─── 状态 ───

  /** 是否正在运行 */
  const isRunning = ref(false)

  /** 是否已超时 */
  const isTimedOut = ref(false)

  /** 是否在倒计时警告阶段 */
  const isWarning = ref(false)

  /** 倒计时剩余毫秒 */
  const countdownMs = ref(0)

  /** 格式化倒计时 mm:ss */
  const countdownDisplay = computed(() => {
    if (countdownMs.value <= 0) return '0:00'
    const totalSec = Math.ceil(countdownMs.value / 1000)
    const min = Math.floor(totalSec / 60)
    const sec = totalSec % 60
    return `${min}:${String(sec).padStart(2, '0')}`
  })

  // ─── 定时器 ───

  let idleTimerId: ReturnType<typeof setTimeout> | null = null
  let warningTimerId: ReturnType<typeof setTimeout> | null = null
  let countdownIntervalId: ReturnType<typeof setInterval> | null = null

  // ─── 事件处理 ───

  const activityHandlers: Array<() => void> = []

  function onUserActivity(): void {
    if (!isRunning.value) return
    resetTimers()
  }

  /** 绑定活动事件 */
  function bindEvents(): void {
    for (const eventType of ACTIVITY_EVENTS) {
      const handler = (): void => onUserActivity()
      document.addEventListener(eventType, handler)
      activityHandlers.push(() => document.removeEventListener(eventType, handler))
    }
  }

  /** 解绑活动事件 */
  function unbindEvents(): void {
    for (const cleanup of activityHandlers) {
      cleanup()
    }
    activityHandlers.length = 0
  }

  // ─── 定时器管理 ───

  function resetTimers(): void {
    clearAllTimers()

    isWarning.value = false
    countdownMs.value = 0

    // 空闲超时定时器
    idleTimerId = setTimeout(() => {
      handleTimeout()
    }, IDLE_TIMEOUT_MS)

    // 提前警告定时器（IDLE_TIMEOUT_MS - WARNING_BEFORE_MS 后触发倒计时）
    warningTimerId = setTimeout(() => {
      startCountdown()
    }, IDLE_TIMEOUT_MS - WARNING_BEFORE_MS)
  }

  /** 开始倒计时（超时前 2 分钟） */
  function startCountdown(): void {
    if (!isRunning.value) return

    isWarning.value = true
    countdownMs.value = WARNING_BEFORE_MS

    // 每秒更新倒计时
    countdownIntervalId = setInterval(() => {
      countdownMs.value -= 1000
      if (countdownMs.value <= 0) {
        clearInterval(countdownIntervalId!)
        countdownIntervalId = null
      }
    }, 1000)
  }

  /** 处理超时 */
  function handleTimeout(): void {
    isTimedOut.value = true
    isRunning.value = false
    isWarning.value = false
    countdownMs.value = 0
    clearAllTimers()
    unbindEvents()
    onTimeout?.()
  }

  /** 清除所有定时器 */
  function clearAllTimers(): void {
    if (idleTimerId !== null) {
      clearTimeout(idleTimerId)
      idleTimerId = null
    }
    if (warningTimerId !== null) {
      clearTimeout(warningTimerId)
      warningTimerId = null
    }
    if (countdownIntervalId !== null) {
      clearInterval(countdownIntervalId)
      countdownIntervalId = null
    }
  }

  // ─── 公开方法 ───

  /** 开始监听空闲超时 */
  function start(): void {
    if (isRunning.value) return
    isRunning.value = true
    isTimedOut.value = false
    isWarning.value = false
    countdownMs.value = 0
    bindEvents()
    resetTimers()
  }

  /** 停止监听（主动断开） */
  function stop(): void {
    isRunning.value = false
    isWarning.value = false
    isTimedOut.value = false
    countdownMs.value = 0
    clearAllTimers()
    unbindEvents()
  }

  /** 重置空闲计时（用户主动操作后调用） */
  function reset(): void {
    if (!isRunning.value) return
    resetTimers()
    // 如果之前处于警告状态，重置后退出警告
    isWarning.value = false
  }

  // ─── 生命周期 ───

  onUnmounted(() => {
    stop()
  })

  return {
    /** 是否正在运行 */
    isRunning,
    /** 是否已超时 */
    isTimedOut,
    /** 是否在倒计时警告阶段 */
    isWarning,
    /** 倒计时剩余毫秒 */
    countdownMs,
    /** 格式化倒计时 mm:ss */
    countdownDisplay,
    /** 开始监听 */
    start,
    /** 停止监听 */
    stop,
    /** 重置空闲计时 */
    reset,
  }
}
