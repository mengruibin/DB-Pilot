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
/* ─── Onyx Console 主题 — 深色运维控制台风格 ─── */

:root {
  --bg-primary: #0B0E14;
  --bg-surface: #141922;
  --bg-elevated: #1A2132;
  --bg-hover: #1C2333;
  --border-color: #1E293B;
  --border-light: #263049;
  --text-primary: #E2E8F0;
  --text-secondary: #94A3B8;
  --text-tertiary: #64748B;
  --accent-teal: #2DD4BF;
  --accent-blue: #38BDF8;
  --accent-purple: #818CF8;
  --color-success: #34D399;
  --color-warning: #FBBF24;
  --color-error: #F87171;
  --color-info: #60A5FA;
  --font-display: 'Outfit', sans-serif;
  --font-body: 'DM Sans', sans-serif;
  --font-mono: 'JetBrains Mono', monospace;
  --topbar-height: 40px;
  --sidebar-width: 220px;
  --radius-sm: 4px;
  --radius-md: 6px;
  --radius-lg: 8px;
  --transition-fast: 0.15s ease;
  --transition-normal: 0.25s ease;
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
