<script setup lang="ts">
import { onMounted } from 'vue'
import { zhCN, NNotificationProvider } from 'naive-ui'
import AppLayout from '@/components/common/AppLayout.vue'
import { darkThemeOverrides, lightThemeOverrides } from '@/utils/theme'
import { useThemeStore } from '@/stores/theme'

const themeStore = useThemeStore()

onMounted(() => {
  themeStore.initTheme()
})
</script>

<template>
  <n-config-provider :theme="themeStore.naiveTheme" :theme-overrides="themeStore.isDark ? darkThemeOverrides : lightThemeOverrides" :locale="zhCN">
    <n-notification-provider>
      <AppLayout />
    </n-notification-provider>
  </n-config-provider>
</template>

<style>
/* ─── DB-Pilot 设计系统 — 深色主题（默认） ───
 * 色彩体系、排版、阴影遵照 docs/reasoning-answer-separation-plan.md 设计规范
 */

:root {
  /* ═══════════ shell（Naive UI 对齐） ═══════════ */
  --bg-primary: #0B0F1A;
  --bg-surface: #161B26;
  --bg-elevated: #1C2333;
  --bg-hover: #1E293B;
  --border-color: #2D3748;
  --border-light: #334155;
  --text-primary: #E2E8F0;
  --text-secondary: #94A3B8;
  --text-tertiary: #64748B;
  --accent-teal: #2DD4BF;
  --accent-blue: #3B82F6;
  --accent-purple: #818CF8;
  --color-success: #10B981;
  --color-warning: #FBBF24;
  --color-error: #F87171;
  --color-info: #60A5FA;
  --font-display: 'Outfit', sans-serif;
  --font-body: 'Satoshi', 'DM Sans', sans-serif;
  --font-mono: 'JetBrains Mono', monospace;
  --topbar-height: 40px;
  --sidebar-width: 250px;
  --radius-sm: 4px;
  --radius-md: 8px;
  --radius-lg: 12px;
  --radius-xl: 16px;
  --radius-2xl: 22px;
  --transition-fast: 0.15s ease;
  --transition-normal: 0.25s ease;

  /* ═══════════ 对话设计 token（深色） ═══════════ */
  --chat-bg: #0B0F1A;
  --chat-card-bg: #161B26;
  --chat-card-border: #2D3748;
  --chat-card-shadow: 0 4px 20px -4px rgba(0, 0, 0, 0.25);

  /* 用户气泡 */
  --chat-user-bg: #1C2333;
  --chat-user-border: #2D3748;
  --chat-user-text: #E2E8F0;

  /* 思考过程折叠面板 */
  --chat-thinking-bg: #161B26;
  --chat-thinking-border: #2D3748;
  --chat-thinking-text: #94A3B8;
  --chat-thinking-step-bg: rgba(59, 130, 246, 0.08);
  --chat-thinking-step-text: #60A5FA;
  --chat-thinking-divider: #1E293B;

  /* 工具调用卡片 */
  --chat-tool-bg: rgba(30, 64, 175, 0.2);
  --chat-tool-border: #1E40AF;
  --chat-tool-header-bg: rgba(30, 64, 175, 0.3);
  --chat-tool-text: #93C5FD;
  --chat-tool-label: #60A5FA;
  --chat-tool-duration: #818CF8;
  --chat-tool-args-text: #93C5FD;

  /* 工具返回结果 */
  --chat-result-bg: #1C2333;
  --chat-result-border: #334155;
  --chat-result-text: #94A3B8;

  /* 最终回答 — 背景与卡片统一 #1C2333，文字浅灰高对比 */
  --chat-answer-bg: transparent;
  --chat-answer-border: #2D3748;
  --chat-answer-text: #E2E8F0;
  --chat-answer-heading: #10B981;

  /* 代码块 */
  --chat-code-bg: #020617;
  --chat-code-text: #60A5FA;

  /* 行内代码 — 深色主题用青绿透明底 */
  --chat-code-inline-bg: rgba(45, 212, 191, 0.08);
  --chat-code-inline-border: rgba(45, 212, 191, 0.12);
  --chat-code-inline-text: #5EE4D0;

  /* 分割线 */
  --chat-divider: #2D3748;

  /* 表格 */
  --chat-table-header-bg: #1E293B;
  --chat-table-border: #334155;
  --chat-table-text: #E2E8F0;
  --chat-table-hover: rgba(59, 130, 246, 0.05);
  --chat-table-even-bg: rgba(30, 41, 59, 0.25);

  /* 提示文本 */
  --chat-note-text: #64748B;

  /* 背景网格点颜色（AppLayout 内容区背景使用） */
  --grid-dot-color: rgba(30, 41, 59, 0.3);

  /* 侧边栏品牌图标 — 深色主题下使用青绿 */
  --brand-icon-color: var(--accent-teal);

  /* 输入区模式标签选中色 */
  --mode-tab-active-color: #FFFFFF;

  /* 发送按钮 */
  --submit-btn-bg: #000000;
  --submit-btn-color: var(--accent-teal);

  /* 强调色软透明变体 — 用于半透明背景/边框场景（青绿） */
  --accent-soft-bg: rgba(45, 212, 191, 0.12);
  --accent-soft-border: rgba(45, 212, 191, 0.2);
  --accent-soft-border-hover: rgba(45, 212, 191, 0.4);

  /* 交互反馈色 — 深色主题用品牌浅绿 */
  --interactive-color: var(--accent-teal);

  /* 连接卡片选中态 */
  --conn-card-active-bg: rgba(45, 212, 191, 0.04);
  --conn-card-active-border: rgba(45, 212, 191, 0.3);

  /* 写操作确认卡片 — 绿色强调（深色主题） */
  --confirm-accent: #4ADE80;
  --confirm-accent-hover: #22C55E;
  --confirm-heading: #E2E8F0;
}

/* ═══════════ 浅色主题变量 ═══════════ */
[data-theme="light"] {
  --bg-primary: #F8FAFC;
  --bg-surface: #FFFFFF;
  --bg-elevated: #FFFFFF;
  --bg-hover: #F1F5F9;
  --border-color: #E2E8F0;
  --border-light: #EDF2F7;
  --text-primary: #334155;
  --text-secondary: #64748B;
  --text-tertiary: #94A3B8;

  /* ═══════════ 浅色主题：灰黑白简约配色（覆盖深色主题的绿色） ═══════════ */
  /* 品牌主色：青绿 → 深石板灰 */
  --accent-teal: #1E293B;
  /* 成功状态色：翠绿 → 中深灰 */
  --color-success: #334155;
  /* Agent 回答标题色：深绿 → 浅蓝，契合交互层蓝色基调 */
  --chat-answer-heading: #3B82F6;

  --chat-bg: #F8FAFC;
  --chat-card-bg: #FFFFFF;
  --chat-card-border: #EDF2F7;
  --chat-card-shadow: 0 4px 20px -4px rgba(0, 0, 0, 0.05);

  --chat-user-bg: #E8EAEF;
  --chat-user-border: #D1D5DB;
  --chat-user-text: #334155;

  --chat-thinking-bg: #FFFFFF;
  --chat-thinking-border: #EDF2F7;
  --chat-thinking-text: #64748B;
  --chat-thinking-step-bg: rgba(59, 130, 246, 0.06);
  --chat-thinking-step-text: #3B82F6;
  --chat-thinking-divider: #F1F5F9;

  --chat-tool-bg: rgba(191, 219, 254, 0.2);
  --chat-tool-border: #BFDBFE;
  --chat-tool-header-bg: rgba(191, 219, 254, 0.5);
  --chat-tool-text: #1E40AF;
  --chat-tool-label: #2563EB;
  --chat-tool-duration: #6366F1;
  --chat-tool-args-text: #1E40AF;

  --chat-result-bg: #F8FAFC;
  --chat-result-border: #E2E8F0;
  --chat-result-text: #64748B;

  --chat-answer-bg: #F1F5F9;
  --chat-answer-border: #E2E8F0;
  --chat-answer-text: #334155;

  --chat-code-bg: #0F172A;
  --chat-code-text: #60A5FA;

  /* 行内代码 — 浅色主题用深灰透明底，契合灰黑白简约风 */
  --chat-code-inline-bg: rgba(30, 41, 59, 0.06);
  --chat-code-inline-border: rgba(30, 41, 59, 0.12);
  --chat-code-inline-text: #1E293B;

  --chat-divider: #E2E8F0;

  --chat-table-header-bg: #F8FAFC;
  --chat-table-border: #E2E8F0;
  --chat-table-text: #334155;
  --chat-table-hover: rgba(59, 130, 246, 0.04);
  --chat-table-even-bg: #F1F5F9;

  --chat-note-text: #94A3B8;

  /* 浅色网格点颜色 */
  --grid-dot-color: rgba(226, 232, 240, 0.5);

  /* 侧边栏品牌图标 — 浅色主题下使用深色 */
  --brand-icon-color: #1E293B;

  /* 输入区模式标签选中色 — 浅色主题下使用黑色 */
  --mode-tab-active-color: #000000;

  /* 发送按钮 — 浅色主题下黑色背景白色图标 */
  --submit-btn-bg: #1E293B;
  --submit-btn-color: #FFFFFF;

  /* 强调色软透明变体 — 浅色主题用深灰透明，契合灰黑白简约风 */
  --accent-soft-bg: rgba(30, 41, 59, 0.10);
  --accent-soft-border: rgba(30, 41, 59, 0.2);
  --accent-soft-border-hover: rgba(30, 41, 59, 0.4);

  /* 交互反馈色 — 浅色主题用浅蓝，增加交互层次感 */
  --interactive-color: #3B82F6;

  /* 连接卡片选中态 — 浅色主题下灰色 */
  --conn-card-active-bg: #f3f4f6;
  --conn-card-active-border: #d1d5db;

  /* 写操作确认卡片 — 浅蓝色强调（浅色主题） */
  --confirm-accent: #60A5FA;
  --confirm-accent-hover: #3B82F6;
  --confirm-heading: #334155;
}
*,
*::before,
*::after {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

html, body {
  height: 100%;
  overflow: hidden;
}

body {
  font-family: var(--font-body);
  font-size: 14px;
  line-height: 1.5;
  color: var(--text-primary);
  background-color: var(--bg-primary);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

#app {
  height: 100%;
}

a {
  color: var(--accent-blue);
  text-decoration: none;
}

::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}

::-webkit-scrollbar-track {
  background: transparent;
}

::-webkit-scrollbar-thumb {
  background: #334155;
  border-radius: 3px;
}

::-webkit-scrollbar-thumb:hover {
  background: #475569;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
