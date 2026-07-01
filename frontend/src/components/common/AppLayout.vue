<script setup lang="ts">
import Sidebar from './Sidebar.vue'
import ConnectionIndicator from './ConnectionIndicator.vue'
</script>

<template>
  <div class="layout">
    <!-- 顶栏 -->
    <header class="topbar">
      <div class="topbar-left">
        <span class="brand-label">DB-Pilot</span>
      </div>
      <div class="topbar-right">
        <ConnectionIndicator />
      </div>
    </header>

    <!-- 主体 -->
    <div class="layout-body">
      <Sidebar />
      <main class="content">
        <div class="content-grid">
          <router-view v-slot="{ Component }">
            <transition name="fade" mode="out-in">
              <component :is="Component" />
            </transition>
          </router-view>
        </div>
      </main>
    </div>
  </div>
</template>

<style scoped>
.layout {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--bg-primary);
}

/* ─── 顶栏 ─── */
.topbar {
  height: var(--topbar-height);
  min-height: var(--topbar-height);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border-color);
  z-index: 100;
}

.topbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand-label {
  font-family: var(--font-display);
  font-size: 13px;
  font-weight: 600;
  color: var(--text-tertiary);
  letter-spacing: 0.8px;
  text-transform: uppercase;
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 100%;
}

/* ─── 主体 ─── */
.layout-body {
  flex: 1;
  display: flex;
  overflow: hidden;
}

/* ─── 内容区 ─── */
.content {
  flex: 1;
  overflow: hidden;
  position: relative;
  background:
    linear-gradient(rgba(30, 41, 59, 0.3) 1px, transparent 1px),
    linear-gradient(90deg, rgba(30, 41, 59, 0.3) 1px, transparent 1px);
  background-size: 24px 24px;
}

.content-grid {
  height: 100%;
  overflow-y: auto;
  padding: 24px;
}
</style>
