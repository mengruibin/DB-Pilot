<script setup lang="ts">
/**
 * ChatView — 主对话页面
 *
 * 有活跃连接时显示 ChatPanel，否则显示欢迎引导页。
 *
 * 依据 api-contract §三 ChatView 组件树
 */
import { useConnectionStore } from '@/stores/connection'
import ChatPanel from '@/components/chat/ChatPanel.vue'

const connectionStore = useConnectionStore()
</script>

<template>
  <div class="chat-view">
    <!-- 有活跃连接 → ChatPanel -->
    <ChatPanel v-if="connectionStore.activeId" />

    <!-- 无连接 → 欢迎引导 -->
    <div v-else class="welcome-state">
      <div class="welcome-content">
        <!-- 品牌标志 -->
        <div class="welcome-icon">
          <svg width="56" height="56" viewBox="0 0 32 32" fill="none">
            <rect width="32" height="32" rx="6" fill="currentColor" opacity="0.08"/>
            <path d="M16 8c-4 0-7 1.6-7 3.5v9c0 1.9 3 3.5 7 3.5s7-1.6 7-3.5v-9c0-1.9-3-3.5-7-3.5z" stroke="currentColor" stroke-width="1.5" fill="none"/>
            <path d="M9 14c0 1.9 3 3.5 7 3.5s7-1.6 7-3.5" stroke="currentColor" stroke-width="1.5" fill="none"/>
          </svg>
        </div>

        <h1 class="welcome-title">DB-Pilot</h1>
        <p class="welcome-desc">
          数据库智能运维助手
        </p>

        <!-- 快速入门提示 -->
        <div class="quick-steps">
          <div class="step-item">
            <span class="step-num">1</span>
            <span class="step-text">在「连接」页面添加数据库连接</span>
          </div>
          <div class="step-item">
            <span class="step-num">2</span>
            <span class="step-text">点击连接卡片激活目标数据库</span>
          </div>
          <div class="step-item">
            <span class="step-num">3</span>
            <span class="step-text">输入自然语言或 SQL 开始查询</span>
          </div>
        </div>

        <!-- 示例查询 -->
        <div class="examples">
          <p class="examples-label">试试这样问：</p>
          <div class="example-chips">
            <span class="example-chip">最近一小时慢查询有哪些？</span>
            <span class="example-chip">显示当前数据库连接数</span>
            <span class="example-chip">分析 orders 表的索引建议</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-view {
  height: 100%;
  display: flex;
  flex-direction: column;
}

/* ─── 欢迎引导 ─── */
.welcome-state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  overflow-y: auto;
}

.welcome-content {
  text-align: center;
  max-width: 440px;
  padding: 20px;
}

.welcome-icon {
  color: var(--accent-teal);
  margin-bottom: 16px;
  opacity: 0.6;
}

.welcome-title {
  font-family: var(--font-display);
  font-size: 28px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 4px;
  letter-spacing: 0.5px;
}

.welcome-desc {
  font-size: 14px;
  color: var(--text-tertiary);
  margin-bottom: 32px;
}

/* ─── 快速步骤 ─── */
.quick-steps {
  text-align: left;
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 28px;
  padding: 16px 20px;
  background: var(--bg-surface);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
}

.step-item {
  display: flex;
  align-items: center;
  gap: 10px;
}

.step-num {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: rgba(45, 212, 191, 0.15);
  color: var(--accent-teal);
  font-family: var(--font-display);
  font-size: 11px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.step-text {
  font-size: 13px;
  color: var(--text-secondary);
}

/* ─── 示例查询 ─── */
.examples-label {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-bottom: 8px;
}

.example-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  justify-content: center;
}

.example-chip {
  font-size: 12px;
  color: var(--text-secondary);
  background: var(--bg-surface);
  border: 1px solid var(--border-color);
  border-radius: 16px;
  padding: 4px 12px;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.example-chip:hover {
  border-color: var(--accent-teal);
  color: var(--accent-teal);
  background: rgba(45, 212, 191, 0.06);
}
</style>
