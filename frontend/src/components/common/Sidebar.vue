<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useConnectionStore } from '@/stores/connection'
import { useThemeStore } from '@/stores/theme'
import { useAuthStore } from '@/stores/auth'
import SessionList from './SessionList.vue'

const route = useRoute()
const router = useRouter()
const connectionStore = useConnectionStore()
const themeStore = useThemeStore()
const authStore = useAuthStore()

interface NavItem {
  path: string
  label: string
  icon: string
}

/** 基础导航项 */
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

/** 含 admin 导航项的完整列表 */
const allNavItems = computed<NavItem[]>(() => {
  const items = [...navItems]
  if (authStore.isAdmin) {
    items.push({ path: '/users', label: '用户管理', icon: 'users' })
  }
  return items
})

/** 是否在对话路由且有活跃连接（显示会话列表的条件） */
const showSessionList = computed(() => {
  return route.path === '/' && connectionStore.activeId
})

function handleLogout() {
  authStore.logout()
  router.push('/login')
}

/** SVG 图标映射 */
const iconMap: Record<string, string> = {
  chat: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`,
  plug: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>`,
  chart: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>`,
  users: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>`,
  logout: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>`,
  settings: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>`,
  sun: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>`,
  moon: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>`,
}
</script>

<template>
  <aside class="sidebar">
    <!-- 应用标识 -->
    <div class="sidebar-brand">
      <div class="brand-icon">
        <svg width="29" height="29" viewBox="0 0 32 32" fill="none">
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
        v-for="item in allNavItems"
        :key="item.path"
        :to="item.path"
        class="nav-item"
        :class="{ active: isActive(item.path) }"
      >
        <span class="nav-icon" v-html="iconMap[item.icon]"></span>
        <span class="nav-label">{{ item.label }}</span>
      </router-link>
    </nav>

    <!-- 对话历史列表（仅对话页且有活跃连接时显示） -->
    <SessionList v-if="showSessionList" />

    <!-- 底部选项 -->
    <div class="sidebar-footer">
      <!-- 用户信息 -->
      <div v-if="authStore.isAuthenticated" class="sidebar-user">
        <div class="user-info">
          <span class="user-avatar">{{ authStore.currentUser?.username?.charAt(0).toUpperCase() }}</span>
          <div class="user-detail">
            <span class="user-name">{{ authStore.currentUser?.username }}</span>
            <span class="user-role-tag" :class="authStore.isAdmin ? 'role-admin' : 'role-readonly'">
              {{ authStore.isAdmin ? '管理员' : '只读' }}
            </span>
          </div>
        </div>
        <button class="nav-item logout-btn" @click="handleLogout">
          <span class="nav-icon" v-html="iconMap['logout']"></span>
          <span class="nav-label">退出登录</span>
        </button>
      </div>

      <div class="sidebar-tools">
        <button class="nav-item theme-toggle-btn" @click="themeStore.toggleTheme()">
          <span class="nav-icon" v-html="themeStore.isDark ? iconMap['sun'] : iconMap['moon']"></span>
          <span class="nav-label">{{ themeStore.isDark ? '浅色模式' : '深色模式' }}</span>
        </button>
        <router-link to="/settings" class="nav-item settings-btn">
          <span class="nav-icon" v-html="iconMap['settings']"></span>
          <span class="nav-label">设置</span>
        </router-link>
      </div>
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
  color: var(--brand-icon-color);
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

/* 浅色主题下品牌图标：黑色背景 + 白色线条 */
[data-theme="light"] .brand-icon rect {
  fill: #000000;
  opacity: 1;
}
[data-theme="light"] .brand-icon path {
  stroke: #FFFFFF;
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
  display: flex;
  flex-direction: column;
  padding: 8px;
  gap: 2px;
  flex-shrink: 0;
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
  background: var(--bg-hover);
  color: var(--text-primary);
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

/* 底部区域 */
.sidebar-footer {
  border-top: 1px solid var(--border-color);
  flex-shrink: 0;
}

/* 用户信息区 */
.sidebar-user {
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
}

.user-avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--accent-teal);
  color: #0B0F1A;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 600;
  flex-shrink: 0;
}

.user-detail {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.user-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.user-role-tag {
  font-size: 11px;
  font-weight: 500;
  display: inline-block;
  padding: 1px 8px;
  border-radius: 8px;
  align-self: flex-start;
}

.user-role-tag.role-admin {
  background: rgba(45, 212, 191, 0.12);
  color: var(--accent-teal);
}

.user-role-tag.role-readonly {
  background: rgba(148, 163, 184, 0.12);
  color: var(--text-secondary);
}

[data-theme="light"] .user-avatar {
  background: #1E293B;
  color: #FFFFFF;
}

[data-theme="light"] .user-role-tag.role-admin {
  background: rgba(30, 41, 59, 0.08);
  color: #1E293B;
}

.logout-btn {
  width: 100%;
  background: none;
  border: none;
  font-family: inherit;
  font-size: 13px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border-radius: var(--radius-md);
  color: var(--text-secondary);
  transition: all var(--transition-fast);
}

.logout-btn:hover {
  background: rgba(248, 113, 113, 0.08);
  color: var(--color-error);
}

/* 底部工具 */
.sidebar-tools {
  padding: 8px;
}

.settings-btn {
  font-size: 13px;
}

/* 主题切换按钮 — 与 nav-item 样式一致，覆盖默认 button 样式 */
.theme-toggle-btn {
  width: 100%;
  background: none;
  border: none;
  font-family: inherit;
  cursor: pointer;
}
</style>
