/**
 * 主题管理 Store
 *
 * 管理浅色/深色主题切换，将用户偏好持久化到 localStorage，
 * 通过 data-theme 属性控制 CSS 变量，并提供 Naive UI 动态主题。
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { darkTheme } from 'naive-ui'

/** localStorage 键名 */
const STORAGE_KEY = 'db-pilot:theme'

export type Theme = 'dark' | 'light'

export const useThemeStore = defineStore('theme', () => {
  // ─── state ───
  /** 当前主题，优先从 localStorage 读取，默认浅色 */
  const theme = ref<Theme>((localStorage.getItem(STORAGE_KEY) as Theme) || 'dark')

  // ─── getters ───
  /** 是否为深色模式 */
  const isDark = computed(() => theme.value === 'dark')

  /** 当前 Naive UI 主题对象；深色 → darkTheme，浅色 → null（Naive UI 默认为浅色） */
  const naiveTheme = computed(() =>
    theme.value === 'dark' ? darkTheme : null,
  )

  // ─── actions ───
  /** 初始化：将 data-theme 设置为当前主题 */
  function initTheme(): void {
    document.documentElement.dataset.theme = theme.value
  }

  /** 切换浅色/深色并持久化 */
  function toggleTheme(): void {
    theme.value = theme.value === 'dark' ? 'light' : 'dark'
    localStorage.setItem(STORAGE_KEY, theme.value)
    document.documentElement.dataset.theme = theme.value
  }

  return { theme, isDark, naiveTheme, initTheme, toggleTheme }
})
