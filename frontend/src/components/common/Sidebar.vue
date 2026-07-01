<script setup lang="ts">
import { useRoute } from 'vue-router'

const route = useRoute()

interface NavItem {
  path: string
  label: string
  icon: string
}

/** 导航项列表 */
const navItems: NavItem[] = [
  { path: '/', label: '对话', icon: 'chat' },
  { path: '/connections', label: '连接', icon: 'plug' },
  { path: '/reports', label: '报告', icon: 'chart' },
]

/** 当前路由是否匹配导航项 */
const isActive = (path: string) => {
  if (path === '/') return route.path === '/'
  return route.path.startsWith(path)
}

/** SVG 图标映射 */
const iconMap: Record<string, string> = {
  chat: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`,
  plug: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>`,
  chart: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>`,
  settings: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>`,
}
</script>

<template>
  <aside class="sidebar">
    <!-- 应用标识 -->
    <div class="sidebar-brand">
      <div class="brand-icon">
        <svg width="22" height="22" viewBox="0 0 32 32" fill="none">
          <rect width="32" height="32" rx="6" fill="currentColor" opacity="0.15"/>
          <path d="M16 8c-4 0-7 1.6-7 3.5v9c0 1.9 3 3.5 7 3.5s7-1.6 7-3.5v-9c0-1.9-3-3.5-7-3.5z" stroke="currentColor" stroke-width="1.5" fill="none"/>
          <path d="M9 14c0 1.9 3 3.5 7 3.5s7-1.6 7-3.5" stroke="currentColor" stroke-width="1.5" fill="none"/>
        </svg>
      </div>
      <span class="brand-text">DB-Pilot</span>
    </div>

    <!-- 主导航 -->
    <nav class="nav-list">
      <router-link
        v-for="item in navItems"
        :key="item.path"
        :to="item.path"
        class="nav-item"
        :class="{ active: isActive(item.path) }"
      >
        <span class="nav-icon" v-html="iconMap[item.icon]"></span>
        <span class="nav-label">{{ item.label }}</span>
      </router-link>
    </nav>

    <!-- 底部设置 -->
    <div class="sidebar-footer">
      <router-link to="/settings" class="nav-item settings-btn">
        <span class="nav-icon" v-html="iconMap['settings']"></span>
        <span class="nav-label">设置</span>
      </router-link>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: var(--sidebar-width);
  height: 100%;
  background: var(--bg-surface);
  border-right: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
  user-select: none;
}

/* 品牌标识 */
.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 18px 16px;
  border-bottom: 1px solid var(--border-color);
}

.brand-icon {
  color: var(--accent-teal);
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

.brand-text {
  font-family: var(--font-display);
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: 0.3px;
}

/* 导航列表 */
.nav-list {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 8px;
  gap: 2px;
  overflow-y: auto;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border-radius: var(--radius-md);
  color: var(--text-secondary);
  text-decoration: none;
  font-family: var(--font-body);
  font-size: 14px;
  font-weight: 450;
  cursor: pointer;
  transition: all var(--transition-fast);
  position: relative;
}

.nav-item:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.nav-item.active {
  background: rgba(45, 212, 191, 0.08);
  color: var(--accent-teal);
}

.nav-item.active::before {
  content: '';
  position: absolute;
  left: -8px;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 18px;
  background: var(--accent-teal);
  border-radius: 0 2px 2px 0;
}

.nav-icon {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  width: 18px;
  height: 18px;
}

.nav-label {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 底部设置 */
.sidebar-footer {
  padding: 8px;
  border-top: 1px solid var(--border-color);
}

.settings-btn {
  font-size: 13px;
}
</style>
