<script setup lang="ts">
/**
 * SettingsView — 用户设置页（Phase 1：纯前端偏好，localStorage 持久化）
 *
 * 分组：外观 / 对话行为 / 写操作确认。
 * 主题三态（浅色/深色/跟随系统）由 themeStore 管理，其余项由 settingsStore 管理。
 * 所有改动即时生效并持久化到 localStorage（键 db-pilot:settings / db-pilot:theme）。
 */
import { computed } from 'vue'
import { NSwitch, NRadioGroup, NRadioButton, NButton, NTooltip } from 'naive-ui'
import { useThemeStore } from '@/stores/theme'
import { useSettingsStore, type UserSettings } from '@/stores/settings'

const themeStore = useThemeStore()
const settingsStore = useSettingsStore()

/** settings 响应式代理（模板直接读写，写入即持久化） */
const settings = computed(() => settingsStore.settings)

// ─── 单项更新辅助 ───
function set(patch: Partial<UserSettings>): void {
  settingsStore.update(patch)
}

/** 主题选项（value 对应 themeStore.theme 三态） */
const themeOptions = [
  { value: 'light', label: '浅色' },
  { value: 'dark', label: '深色' },
  { value: 'system', label: '跟随系统' },
] as const

/** 写确认倒计时选项（秒） */
const countdownOptions = [
  { value: 30, label: '30 秒' },
  { value: 60, label: '60 秒' },
  { value: 120, label: '120 秒' },
  { value: 0, label: '不自动取消' },
] as const

/** 恢复全部默认（主题恢复为深色，历史默认值） */
function handleReset(): void {
  settingsStore.resetToDefaults()
  themeStore.setTheme('dark')
}
</script>

<template>
  <div class="settings-page">
    <!-- 页头 -->
    <header class="settings-header">
      <h1 class="settings-title">设置</h1>
      <p class="settings-subtitle">偏好仅保存在本机浏览器，不会同步到服务端</p>
    </header>

    <div class="settings-body">
      <!-- ═══════════ 外观 ═══════════ -->
      <section class="settings-card">
        <h2 class="card-title">外观</h2>

        <div class="settings-row">
          <div class="row-text">
            <span class="row-label">主题</span>
            <span class="row-desc">深色 / 浅色，或跟随系统深浅色自动切换</span>
          </div>
          <NRadioGroup
            :value="themeStore.theme"
            size="small"
            @update:value="(v: string) => themeStore.setTheme(v as 'dark' | 'light' | 'system')"
          >
            <NRadioButton v-for="opt in themeOptions" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </NRadioButton>
          </NRadioGroup>
        </div>

        <div class="settings-row">
          <div class="row-text">
            <span class="row-label">流式打字光标</span>
            <span class="row-desc">回答生成时在文末显示闪烁光标</span>
          </div>
          <NSwitch
            :value="settings.showTypingCursor"
            size="small"
            @update:value="(v: boolean) => set({ showTypingCursor: v })"
          />
        </div>
      </section>

      <!-- ═══════════ 对话行为 ═══════════ -->
      <section class="settings-card">
        <h2 class="card-title">对话行为</h2>

        <div class="settings-row">
          <div class="row-text">
            <span class="row-label">思考面板默认展开</span>
            <span class="row-desc">关闭后每轮回答的思考过程默认收起，可手动展开</span>
          </div>
          <NSwitch
            :value="settings.thinkingPanelDefault === 'expanded'"
            size="small"
            @update:value="(v: boolean) => set({ thinkingPanelDefault: v ? 'expanded' : 'collapsed' })"
          />
        </div>

        <div class="settings-row">
          <div class="row-text">
            <span class="row-label">深度推理模式</span>
            <span class="row-desc">
              开启后每次对话请求启用深度推理并展示思考过程（需模型支持，如 deepseek-reasoner / GLM thinking）；关闭则不请求推理内容
              <NTooltip trigger="hover" placement="top">
                <template #trigger>
                  <span class="hint-mark">与服务端配置的关系？</span>
                </template>
                本开关按请求生效，优先生效于服务端 .env 的 ENABLE_REASONING 全局默认值。若当前模型本身不产生推理内容，开启后也不会有额外展示。
              </NTooltip>
            </span>
          </div>
          <NSwitch
            :value="settings.enableReasoning"
            size="small"
            @update:value="(v: boolean) => set({ enableReasoning: v })"
          />
        </div>

        <div class="settings-row">
          <div class="row-text">
            <span class="row-label">自动滚动</span>
            <span class="row-desc">智能 = 手动上翻查看历史时暂停跟随；始终 = 流式输出时持续吸底</span>
          </div>
          <NRadioGroup
            :value="settings.autoScroll"
            size="small"
            @update:value="(v: string) => set({ autoScroll: v as 'smart' | 'always' })"
          >
            <NRadioButton value="smart">智能</NRadioButton>
            <NRadioButton value="always">始终</NRadioButton>
          </NRadioGroup>
        </div>

        <div class="settings-row">
          <div class="row-text">
            <span class="row-label">Enter 发送消息</span>
            <span class="row-desc">开启 = Enter 发送、Shift+Enter 换行；关闭 = Enter 换行、Shift+Enter 发送</span>
          </div>
          <NSwitch
            :value="settings.enterToSend"
            size="small"
            @update:value="(v: boolean) => set({ enterToSend: v })"
          />
        </div>
      </section>

      <!-- ═══════════ 写操作确认 ═══════════ -->
      <section class="settings-card">
        <h2 class="card-title">写操作确认</h2>

        <div class="settings-row">
          <div class="row-text">
            <span class="row-label">确认倒计时</span>
            <span class="row-desc">
              写操作确认卡的超时时长，超时自动取消
              <NTooltip trigger="hover" placement="top">
                <template #trigger>
                  <span class="hint-mark">选「不自动取消」会怎样？</span>
                </template>
                确认卡将一直等待你的决策，SSE 流保持挂起，直到你点击「确认」或「取消」。数据库的写操作不会在你决策前执行。
              </NTooltip>
            </span>
          </div>
          <NRadioGroup
            :value="settings.confirmCountdownSec"
            size="small"
            @update:value="(v: number) => set({ confirmCountdownSec: v })"
          >
            <NRadioButton v-for="opt in countdownOptions" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </NRadioButton>
          </NRadioGroup>
        </div>
      </section>

      <!-- 恢复默认 -->
      <footer class="settings-footer">
        <NButton size="small" tertiary @click="handleReset">恢复默认设置</NButton>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.settings-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--bg-primary);
  overflow-y: auto;
}

/* 页头 */
.settings-header {
  padding: 28px 32px 0;
  flex-shrink: 0;
}

.settings-title {
  margin: 0;
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 600;
  color: var(--text-primary);
}

.settings-subtitle {
  margin: 6px 0 0;
  font-size: 13px;
  color: var(--text-tertiary);
}

/* 内容区：居中限宽，与对话页阅读宽度一致 */
.settings-body {
  flex: 1;
  width: 100%;
  max-width: 760px;
  margin: 0 auto;
  padding: 20px 32px 40px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  box-sizing: border-box;
}

/* 分组卡片 */
.settings-card {
  background: var(--bg-surface);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  padding: 18px 20px 8px;
}

.card-title {
  margin: 0 0 6px;
  font-family: var(--font-display);
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

/* 设置行：左文案右控件 */
.settings-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 14px 0;
}

.settings-row + .settings-row {
  border-top: 1px solid var(--border-color);
}

.row-text {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.row-label {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}

.row-desc {
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--text-tertiary);
}

.hint-mark {
  color: var(--accent-teal);
  cursor: help;
  text-decoration: underline dotted;
  text-underline-offset: 3px;
}

/* 控件不收缩 */
.settings-row :deep(.n-radio-group),
.settings-row :deep(.n-switch) {
  flex-shrink: 0;
}

/* 恢复默认 */
.settings-footer {
  display: flex;
  justify-content: flex-end;
}
</style>
