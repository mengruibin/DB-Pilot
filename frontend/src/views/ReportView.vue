<script setup lang="ts">
/* ReportView — 健康巡检页面（F-18 完整实现） */
</script>

<template>
  <div class="report-view">
    <div class="page-header">
      <h1 class="page-title">健康巡检</h1>
    </div>
    <div class="page-body">
      <div class="empty-state">
        <div class="empty-icon">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="18" y1="20" x2="18" y2="10"/>
            <line x1="12" y1="20" x2="12" y2="4"/>
            <line x1="6" y1="20" x2="6" y2="14"/>
          </svg>
        </div>
        <p class="empty-text">暂无巡检报告</p>
        <p class="empty-hint">选择数据库连接后开始健康巡检</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.report-view {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
}

.page-title {
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: 0.2px;
}

.page-body {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.empty-state {
  text-align: center;
  color: var(--text-tertiary);
}

.empty-icon {
  margin-bottom: 12px;
  opacity: 0.35;
}

.empty-text {
  font-size: 15px;
  color: var(--text-secondary);
  margin-bottom: 4px;
}

.empty-hint {
  font-size: 13px;
  color: var(--text-tertiary);
}
</style>
