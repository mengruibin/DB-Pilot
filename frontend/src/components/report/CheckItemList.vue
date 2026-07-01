<script setup lang="ts">
/**
 * CheckItemList — 分类检查项列表组件
 *
 * 按 category 分组渲染卡片，每项可展开查看 value + threshold + suggestion，
 * 破坏性建议带 ConfirmDialog 执行按钮。
 *
 * 依据 api-contract §2.5 HealthReportCategory / HealthReportItem
 */
import { ref } from 'vue'
import SqlBlock from '@/components/sql/SqlBlock.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import type { HealthReportCategory } from '@/types/report'

defineProps<{
  categories: HealthReportCategory[]
}>()

// ─── 展开状态 ───

/** 展开的 item 索引映射 "catIdx-itemIdx" */
const expandedItems = ref<Set<string>>(new Set())

function toggleItem(catIdx: number, itemIdx: number): void {
  const key = `${catIdx}-${itemIdx}`
  if (expandedItems.value.has(key)) {
    expandedItems.value.delete(key)
  } else {
    expandedItems.value.add(key)
  }
}

function isExpanded(catIdx: number, itemIdx: number): boolean {
  return expandedItems.value.has(`${catIdx}-${itemIdx}`)
}

// ─── 破坏性建议确认 ───

const destructiveDialog = ref<{ visible: boolean; sql: string }>({
  visible: false,
  sql: '',
})

function openConfirm(suggestion: string): void {
  destructiveDialog.value = { visible: true, sql: suggestion }
}

function closeConfirm(): void {
  destructiveDialog.value = { visible: false, sql: '' }
}

function handleConfirm(): void {
  closeConfirm()
}

// ─── 状态配置 ───

interface StatusConfig {
  icon: string
  color: string
  bgColor: string
}

const statusConfig: Record<string, StatusConfig> = {
  pass: {
    icon: '✅',
    color: 'var(--color-success)',
    bgColor: 'rgba(52, 211, 153, 0.08)',
  },
  warning: {
    icon: '⚠️',
    color: 'var(--color-warning)',
    bgColor: 'rgba(251, 191, 36, 0.08)',
  },
  error: {
    icon: '❌',
    color: 'var(--color-error)',
    bgColor: 'rgba(248, 113, 113, 0.08)',
  },
  skipped: {
    icon: '⏭️',
    color: 'var(--text-tertiary)',
    bgColor: 'transparent',
  },
}

/** 分类通过率 */
function categoryPassCount(cat: HealthReportCategory): string {
  const total = cat.items.length
  const passed = cat.items.filter((i) => i.status === 'pass').length
  return `${passed}/${total} 通过`
}
</script>

<template>
  <div class="check-item-list">
    <!-- 无数据 -->
    <div v-if="categories.length === 0" class="empty-state">
      <p class="empty-text">暂无检查数据</p>
    </div>

    <!-- 分类卡片网格 -->
    <div v-else class="category-grid">
      <div
        v-for="(cat, ci) in categories"
        :key="ci"
        class="category-card"
      >
        <!-- 分类头 -->
        <div class="cat-header">
          <span class="cat-name">{{ cat.name }}</span>
          <span class="cat-count">{{ categoryPassCount(cat) }}</span>
        </div>

        <!-- 检查项列表 -->
        <div class="cat-items">
          <div
            v-for="(item, ii) in cat.items"
            :key="ii"
            class="item-row"
            :class="`status-${item.status}`"
          >
            <!-- 主行（可点击展开） -->
            <div
              class="item-main"
              :class="{ clickable: item.suggestion || item.threshold }"
              @click="toggleItem(ci, ii)"
            >
              <span
                class="item-icon"
                :style="{ color: statusConfig[item.status]?.color }"
              >
                {{ statusConfig[item.status]?.icon }}
              </span>
              <span class="item-name">{{ item.name }}</span>
              <span v-if="item.value" class="item-value">{{ item.value }}</span>
              <span v-if="item.threshold" class="item-threshold">
                阈值 {{ item.threshold }}
              </span>
              <button
                v-if="item.suggestion || item.threshold"
                class="expand-btn"
                :class="{ expanded: isExpanded(ci, ii) }"
              >
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
                  <polyline points="6 9 12 15 18 9"/>
                </svg>
              </button>
            </div>

            <!-- 展开详情 -->
            <Transition name="fade-slide">
              <div v-if="isExpanded(ci, ii)" class="item-detail">
                <!-- 当前值 + 阈值 -->
                <div v-if="item.value || item.threshold" class="detail-metrics">
                  <div class="metric-badge">
                    <span class="metric-label">当前值</span>
                    <span class="metric-value">{{ item.value || '-' }}</span>
                  </div>
                  <div class="metric-badge">
                    <span class="metric-label">阈值</span>
                    <span class="metric-value">{{ item.threshold || '-' }}</span>
                  </div>
                </div>

                <!-- 非破坏性建议 -->
                <div v-if="item.suggestion && !item.is_destructive" class="detail-suggestion">
                  <span class="suggestion-label">💡 建议</span>
                  <SqlBlock :sql="item.suggestion" :is-readonly="true" />
                </div>

                <!-- 破坏性建议 -->
                <div v-if="item.suggestion && item.is_destructive" class="detail-suggestion destructive">
                  <div class="destructive-header">
                    <span class="suggestion-label">⚠️ 建议操作</span>
                    <span class="destructive-badge">破坏性操作</span>
                  </div>
                  <SqlBlock :sql="item.suggestion" :is-readonly="false" />
                  <div class="destructive-footer">
                    <button class="exec-btn" @click.stop="openConfirm(item.suggestion)">
                      ▶ 执行此操作
                    </button>
                  </div>
                </div>
              </div>
            </Transition>
          </div>
        </div>
      </div>
    </div>

    <!-- 确认对话框 -->
    <ConfirmDialog
      :show="destructiveDialog.visible"
      :sql="destructiveDialog.sql"
      :is-readonly="false"
      @update:show="closeConfirm"
      @confirm="handleConfirm"
      @cancel="closeConfirm"
    />
  </div>
</template>

<style scoped>
.check-item-list {
  width: 100%;
}

/* ─── 空状态 ─── */
.empty-state {
  text-align: center;
  padding: 40px 20px;
}

.empty-text {
  font-size: 14px;
  color: var(--text-tertiary);
}

/* ─── 分类卡片网格 ─── */
.category-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(420px, 1fr));
  gap: 12px;
}

@media (max-width: 960px) {
  .category-grid {
    grid-template-columns: 1fr;
  }
}

.category-card {
  background: var(--bg-elevated);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  overflow: hidden;
  transition: border-color 0.2s;
}

.category-card:hover {
  border-color: rgba(255, 255, 255, 0.1);
}

/* ─── 分类头 ─── */
.cat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 14px;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border-color);
}

.cat-name {
  font-family: var(--font-display);
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.cat-count {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-tertiary);
  padding: 1px 8px;
  background: rgba(255, 255, 255, 0.03);
  border-radius: 10px;
}

/* ─── 检查项行 ─── */
.cat-items {
  display: flex;
  flex-direction: column;
}

.item-row {
  border-bottom: 1px solid var(--border-color);
}

.item-row:last-child {
  border-bottom: none;
}

.item-main {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  min-height: 36px;
  transition: background 0.15s;
}

.item-main.clickable {
  cursor: pointer;
}

.item-main.clickable:hover {
  background: var(--bg-hover);
}

/* 状态行左色条 */
.status-error .item-main {
  border-left: 3px solid var(--color-error);
}

.status-warning .item-main {
  border-left: 3px solid var(--color-warning);
}

.status-pass .item-main {
  border-left: 3px solid var(--color-success);
}

.status-skipped .item-main {
  border-left: 3px solid transparent;
  opacity: 0.5;
}

.item-icon {
  font-size: 13px;
  line-height: 1;
  flex-shrink: 0;
}

.item-name {
  font-size: 13px;
  color: var(--text-primary);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.item-value {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
}

.item-threshold {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-tertiary);
  white-space: nowrap;
}

/* 展开按钮 */
.expand-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  border-radius: 4px;
  transition: all 0.2s;
  flex-shrink: 0;
}

.expand-btn:hover {
  background: rgba(255, 255, 255, 0.06);
  color: var(--text-secondary);
}

.expand-btn svg {
  transition: transform 0.2s;
}

.expand-btn.expanded svg {
  transform: rotate(180deg);
}

/* ─── 展开详情 ─── */
.item-detail {
  padding: 8px 14px 12px 20px;
  background: rgba(255, 255, 255, 0.015);
  border-top: 1px solid var(--border-color);
}

.detail-metrics {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}

.metric-badge {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 10px;
  background: var(--bg-surface);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  flex: 1;
}

.metric-label {
  font-size: 10px;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.metric-value {
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}

/* 建议区域 */
.detail-suggestion {
  margin-top: 4px;
}

.suggestion-label {
  display: block;
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 4px;
}

.detail-suggestion.destructive {
  border: 1px solid rgba(248, 113, 113, 0.25);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.destructive-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 10px;
  background: rgba(248, 113, 113, 0.06);
  border-bottom: 1px solid rgba(248, 113, 113, 0.12);
}

.destructive-badge {
  font-size: 10px;
  font-weight: 500;
  color: var(--color-error);
  padding: 1px 6px;
  border: 1px solid rgba(248, 113, 113, 0.3);
  border-radius: 3px;
}

.destructive-footer {
  display: flex;
  justify-content: flex-end;
  padding: 6px 10px;
  background: rgba(248, 113, 113, 0.03);
  border-top: 1px solid rgba(248, 113, 113, 0.1);
}

.exec-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 12px;
  border: 1px solid rgba(248, 113, 113, 0.4);
  border-radius: var(--radius-sm);
  background: rgba(248, 113, 113, 0.1);
  color: var(--color-error);
  font-family: var(--font-body);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
}

.exec-btn:hover {
  background: rgba(248, 113, 113, 0.2);
  border-color: var(--color-error);
}

/* ─── 过渡动画 ─── */
.fade-slide-enter-active,
.fade-slide-leave-active {
  transition: all 0.2s ease;
}

.fade-slide-enter-from,
.fade-slide-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
