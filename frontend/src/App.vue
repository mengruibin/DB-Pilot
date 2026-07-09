<script setup lang="ts">
import { darkTheme, zhCN, NNotificationProvider } from 'naive-ui'
import AppLayout from '@/components/common/AppLayout.vue'
import { themeOverrides } from '@/utils/theme'
</script>

<template>
  <n-config-provider :theme="darkTheme" :theme-overrides="themeOverrides" :locale="zhCN">
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

  /* 分割线 */
  --chat-divider: #2D3748;

  /* 表格 */
  --chat-table-header-bg: #1E293B;
  --chat-table-border: #334155;
  --chat-table-text: #E2E8F0;
  --chat-table-hover: rgba(59, 130, 246, 0.05);

  /* 提示文本 */
  --chat-note-text: #64748B;
}

/* ═══════════ 浅色主题变量 ═══════════ */
[data-theme="light"] {
  --bg-primary: #F9FBFC;
  --bg-surface: #FFFFFF;
  --bg-elevated: #FFFFFF;
  --bg-hover: #F1F5F9;
  --border-color: #E2E8F0;
  --border-light: #EDF2F7;
  --text-primary: #334155;
  --text-secondary: #64748B;
  --text-tertiary: #94A3B8;

  --chat-bg: #F9FBFC;
  --chat-card-bg: #FFFFFF;
  --chat-card-border: #EDF2F7;
  --chat-card-shadow: 0 4px 20px -4px rgba(0, 0, 0, 0.05);

  --chat-user-bg: #F1F3F5;
  --chat-user-border: #E9ECEF;
  --chat-user-text: #334155;

  --chat-thinking-bg: #FFFFFF;
  --chat-thinking-border: #EDF2F7;
  --chat-thinking-text: #64748B;
  --chat-thinking-step-bg: rgba(59, 130, 246, 0.06);
  --chat-thinking-step-text: #3B82F6;
  --chat-thinking-divider: #F1F5F9;

  --chat-tool-bg: rgba(191, 219, 254, 0.3);
  --chat-tool-border: #BFDBFE;
  --chat-tool-header-bg: rgba(191, 219, 254, 0.5);
  --chat-tool-text: #1E40AF;
  --chat-tool-label: #2563EB;
  --chat-tool-duration: #6366F1;
  --chat-tool-args-text: #1E40AF;

  --chat-result-bg: #F9FAFB;
  --chat-result-border: #E5E7EB;
  --chat-result-text: #64748B;

  --chat-answer-bg: transparent;
  --chat-answer-border: #E2E8F0;
  --chat-answer-text: #334155;
  --chat-answer-heading: #10B981;

  --chat-code-bg: #0F172A;
  --chat-code-text: #60A5FA;

  --chat-divider: #E2E8F0;

  --chat-table-header-bg: #F8FAFC;
  --chat-table-border: #E2E8F0;
  --chat-table-text: #334155;
  --chat-table-hover: rgba(59, 130, 246, 0.04);

  --chat-note-text: #94A3B8;
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
