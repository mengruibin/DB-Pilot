/**
 * 用户级设置 Store
 *
 * 管理纯前端的用户偏好配置（外观 / 对话行为 / 写操作确认倒计时），
 * 持久化到 localStorage（键 db-pilot:settings），带版本号便于未来 schema 迁移。
 *
 * 容错策略：读取时与默认值浅合并——JSON 解析失败、非对象、旧版本缺字段
 * 均回落默认值，不会让 store 处于残缺状态。
 *
 * 注意：主题偏好由 themeStore 管理（含三态 dark/light/system），
 * 本 store 不重复存储，避免两个持久化源打架。
 */
import { defineStore } from 'pinia'
import { reactive } from 'vue'

/** localStorage 键名 */
const STORAGE_KEY = 'db-pilot:settings'
/** 当前 schema 版本（未来字段变更时递增并在此做迁移） */
const SCHEMA_VERSION = 1

/** 思考面板默认状态：展开 / 收起 */
export type ThinkingPanelDefault = 'expanded' | 'collapsed'
/** 自动滚动策略：smart=用户上翻后暂停跟随 / always=始终吸底 */
export type AutoScrollMode = 'smart' | 'always'

export interface UserSettings {
  /** 外观：是否显示流式打字光标 */
  showTypingCursor: boolean
  /** 对话：思考面板默认展开 / 收起 */
  thinkingPanelDefault: ThinkingPanelDefault
  /** 对话：深度推理模式（端到端）。开启时每轮对话请求携带 enable_reasoning=true，
   * 由后端按请求保留并返回模型推理内容（需模型支持深度思考）；
   * 关闭时请求明确关闭，历史会话中的推理文本也一并隐藏 */
  enableReasoning: boolean
  /** 对话：自动滚动策略 */
  autoScroll: AutoScrollMode
  /** 对话：true=Enter 发送 Shift+Enter 换行；false=Enter 换行 Shift+Enter 发送 */
  enterToSend: boolean
  /** 写操作确认自动取消倒计时秒数，0 = 不自动取消（后端 interrupt 将一直等待用户决策） */
  confirmCountdownSec: number
}

const DEFAULT_SETTINGS: UserSettings = {
  showTypingCursor: true,
  thinkingPanelDefault: 'expanded',
  enableReasoning: true,
  autoScroll: 'smart',
  enterToSend: true,
  confirmCountdownSec: 60,
}

/** 从 localStorage 读取并与默认值合并（fail-safe，任何异常都回落默认值） */
function loadSettings(): UserSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { ...DEFAULT_SETTINGS }
    const parsed = JSON.parse(raw) as Partial<UserSettings> | null
    if (!parsed || typeof parsed !== 'object') return { ...DEFAULT_SETTINGS }
    return { ...DEFAULT_SETTINGS, ...parsed }
  } catch {
    return { ...DEFAULT_SETTINGS }
  }
}

export const useSettingsStore = defineStore('settings', () => {
  const settings = reactive<UserSettings>(loadSettings())

  /** 部分更新配置并持久化 */
  function update(patch: Partial<UserSettings>): void {
    Object.assign(settings, patch)
    persist()
  }

  /** 持久化到 localStorage（存储不可用如隐私模式时静默降级为会话内生效） */
  function persist(): void {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: SCHEMA_VERSION, ...settings }))
    } catch {
      /* 静默降级 */
    }
  }

  /** 恢复全部默认值 */
  function resetToDefaults(): void {
    Object.assign(settings, DEFAULT_SETTINGS)
    persist()
  }

  return { settings, update, resetToDefaults }
})
