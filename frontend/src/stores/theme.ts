/**
 * 主题管理 Store
 *
 * 管理浅色/深色主题切换，将用户偏好持久化到 localStorage，
 * 通过 data-theme 属性控制 CSS 变量，并提供 Naive UI 动态主题。
 *
 * 三态支持：'dark' | 'light' | 'system'（跟随系统深浅色）。
 *   - resolvedTheme 为最终生效值：system 态由 matchMedia 实时推导并监听变化
 *   - 旧版 localStorage 值（dark/light）天然兼容，无需迁移
 *   - toggleTheme 保持"快切显式值"语义：无论当前是否为 system，
 *     点击都切换到与当前生效主题相反的显式值（设置页提供三选）
 */
import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { darkTheme } from 'naive-ui'

/** localStorage 键名 */
const STORAGE_KEY = 'db-pilot:theme'

export type Theme = 'dark' | 'light' | 'system'
/** 最终生效的主题（不含 system） */
export type ResolvedTheme = Exclude<Theme, 'system'>

/** 读取系统深浅色偏好 */
function getSystemPrefersDark(): boolean {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-color-scheme: dark)').matches
}

export const useThemeStore = defineStore('theme', () => {
  // ─── state ───
  /** 用户主题偏好，优先从 localStorage 读取，默认深色（历史行为） */
  const theme = ref<Theme>((localStorage.getItem(STORAGE_KEY) as Theme) || 'dark')
  if (theme.value !== 'dark' && theme.value !== 'light' && theme.value !== 'system') {
    theme.value = 'dark'
  }

  /** 系统深浅色（system 态使用，实时跟随） */
  const systemPrefersDark = ref(getSystemPrefersDark())

  // ─── getters ───
  /** 最终生效主题：system 态由系统偏好推导，其余为显式值 */
  const resolvedTheme = computed<ResolvedTheme>(() => {
    if (theme.value !== 'system') return theme.value
    return systemPrefersDark.value ? 'dark' : 'light'
  })

  /** 是否为深色模式（按最终生效主题判断） */
  const isDark = computed(() => resolvedTheme.value === 'dark')

  /** 当前 Naive UI 主题对象；深色 → darkTheme，浅色 → null（Naive UI 默认为浅色） */
  const naiveTheme = computed(() =>
    resolvedTheme.value === 'dark' ? darkTheme : null,
  )

  // ─── actions ───
  /** 将 data-theme 设置为当前生效主题 */
  function applyTheme(): void {
    document.documentElement.dataset.theme = resolvedTheme.value
  }

  /** 系统深浅色变化 → system 态实时切换 */
  function onSystemChange(e: MediaQueryListEvent): void {
    systemPrefersDark.value = e.matches
  }

  /** 初始化：应用当前主题并挂载系统偏好监听 */
  function initTheme(): void {
    if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
      window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', onSystemChange)
    }
    applyTheme()
  }

  /** 显式设置主题偏好（dark / light / system）并持久化 */
  function setTheme(value: Theme): void {
    theme.value = value
    localStorage.setItem(STORAGE_KEY, value)
  }

  /** 快切：切换到与当前生效主题相反的显式值（侧栏按钮语义，覆盖 system 态） */
  function toggleTheme(): void {
    setTheme(resolvedTheme.value === 'dark' ? 'light' : 'dark')
  }

  // 生效主题变化（含 system 态下系统切换）时同步 data-theme
  watch(resolvedTheme, applyTheme)

  return { theme, systemPrefersDark, resolvedTheme, isDark, naiveTheme, initTheme, setTheme, toggleTheme }
})
