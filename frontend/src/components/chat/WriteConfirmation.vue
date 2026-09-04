<script setup lang="ts">
/**
 * WriteConfirmation — 危险操作确认内联卡片（category 分类渲染 + 二次确认状态机）
 *
 * 视觉定位：贴近 DB-Pilot 的“运维确认单”，去除旧版 AI 助手的彩色左边框/图标堆砌。
 *   - 白卡（深色: #242838）中性 1px 边框，8px 圆角，无渐变、无彩色 rail
 *   - 仅用语义色点缀：等待=红点 / 终止连接=红 / 高影响=警示色 / 批准=绿 / 取消=灰
 *
 * 结构（自上而下）：
 *   Header（红点 + 标题；决议后右侧出现「已批准 / 已取消」徽章）
 *   内容区（sql_write：SQL 代码井 + 影响预估行；connection_kill/generic：键值规格表）
 *   二次确认提示（首次点「确认执行」后出现，条件渲染）
 *   操作栏（左：60s 自动取消倒计时；右：取消 + 确认按钮）—— 决议后整栏替换为结果行
 *
 * 交互状态机：
 *   pending（待确认，倒计时 60s）
 *     ├─ 点「确认执行」→ armed（二次确认提示 + 按钮变红）
 *     │    └─ 再点一次 → approved（通知后端执行）
 *     ├─ 点「取消」        → cancelled（通知后端拒绝）
 *     └─ 倒计时归零        → cancelled（自动拒绝，略微早于 store 60s 兜底以抢得渲染时机）
 *   决议后 store.pendingConfirm 立即清空（SSE 恢复），本卡用本地快照短暂展示结果态再淡出。
 */
import { ref, computed, watch, nextTick, onUnmounted } from 'vue'
import { useChatStore } from '@/stores/chat'
import type { ConfirmCategory, ConfirmableWrite } from '@/types/chat'

const chatStore = useChatStore()

/** 卡片本地快照：store 清空 pendingConfirm 后仍能渲染结果态 */
const writes = ref<ConfirmableWrite[]>([])

/** 阶段：pending 待确认 / armed 二次确认已点亮 / resolved 已出结果 */
type Phase = 'pending' | 'armed' | 'resolved'
const phase = ref<Phase>('pending')
/** 决议结果（resolved 时非空） */
const decision = ref<{ kind: 'approved' | 'cancelled'; reason: 'user' | 'timeout' } | null>(null)

const COOLDOWN_DENY_EPSILON_MS = 1000 // 提前于 store 60s 兜底触发，保证「已取消」结果态可见
const RESOLVED_HOLD_MS = 2400         // 结果态停留时长，之后淡出让位给后续消息流
const ARM_APPROVE_GAP_MS = 350        // armed 后最小停留，防止双击瞬间跳过二次确认

// ═══════════ 类别派生（快照首条同批次类别一致） ═══════════
const pendingCount = computed(() => writes.value.length)
const category = computed<ConfirmCategory>(() => writes.value[0]?.category ?? 'generic')

const isArmed = computed(() => phase.value === 'armed')
const isResolved = computed(() => phase.value === 'resolved')

const TITLE_TEXT: Record<ConfirmCategory, string> = {
  sql_write: '需要确认执行写操作',
  connection_kill: '需要确认终止数据库连接',
  generic: '需要确认执行操作',
}
const titleText = computed(() => TITLE_TEXT[category.value])

/** 二次确认提示（首次点击确认后出现；语义按类别区分，杜绝错误承诺“可回滚”） */
const ARMED_HINT_TEXT: Record<ConfirmCategory, string> = {
  sql_write: '该操作将直接修改数据，确认后立即执行且不可回滚，请再次确认。',
  connection_kill: '该操作不可逆，将立即断开连接并中断其事务，请再次确认。',
  generic: '该操作将按下方参数立即执行且不可回滚，请再次确认。',
}
const armedHint = computed(() => ARMED_HINT_TEXT[category.value])

/** 确认按钮文案 */
const baseLabel = computed(() =>
  pendingCount.value > 1
    ? `全部确认${category.value === 'connection_kill' ? '终止' : '执行'}`
    : `确认${category.value === 'connection_kill' ? '终止' : '执行'}`,
)
const primaryLabel = computed(() => (isArmed.value ? '再次确认' : baseLabel.value))
const cancelLabel = computed(() => (pendingCount.value > 1 ? '全部取消' : '取消'))

/** 结果态文字 */
const resultText = computed(() => {
  if (!decision.value) return ''
  const approved = decision.value.kind === 'approved'
  if (category.value === 'connection_kill') return approved ? '已批准终止该连接' : '已取消终止操作'
  return approved ? '已批准执行该写操作' : '已取消该操作'
})

// ═══════════ 60s 自动取消倒计时 ═══════════
const COUNTDOWN_TOTAL_MS = 60_000
const remainingMs = ref(0)
const countdownSec = computed(() => Math.max(0, Math.ceil(remainingMs.value / 1000)))
const countdownDanger = computed(() => countdownSec.value <= 10)
let countdownTimer: ReturnType<typeof setInterval> | null = null

function startCountdown(): void {
  stopCountdown()
  remainingMs.value = COUNTDOWN_TOTAL_MS
  countdownTimer = setInterval(() => {
    remainingMs.value = Math.max(0, remainingMs.value - 200)
    // 略微提前于 store 60s 兜底拒绝，保证「已取消」结果态有渲染窗口
    if (remainingMs.value <= COOLDOWN_DENY_EPSILON_MS) doDeny('timeout')
  }, 200)
}
function stopCountdown(): void {
  if (countdownTimer) {
    clearInterval(countdownTimer)
    countdownTimer = null
  }
}

// ═══════════ 决议 + 结果态 ═══════════
let hideTimer: ReturnType<typeof setTimeout> | null = null

function scheduleHide(): void {
  hideTimer = setTimeout(clearCard, RESOLVED_HOLD_MS)
}
function stopHideTimer(): void {
  if (hideTimer) {
    clearTimeout(hideTimer)
    hideTimer = null
  }
}

/** 本地决议：停止倒计时 → 记录结果 → 通知后端恢复 SSE */
function resolveOutcome(kind: 'approved' | 'cancelled', reason: 'user' | 'timeout'): void {
  if (decision.value) return
  stopCountdown()
  decision.value = { kind, reason }
  phase.value = 'resolved'
  remainingMs.value = 0
  scheduleHide()
}

function doDeny(reason: 'user' | 'timeout'): void {
  if (decision.value || !writes.value.length) return
  const ids = writes.value.map(w => w.tool_call_id)
  resolveOutcome('cancelled', reason)
  chatStore.respondToConfirm({ approved_tool_call_ids: [], denied_tool_call_ids: ids })
}

/** 进入 armed 的时刻（防双击瞬间跳过二次确认） */
let armedAt = 0

function handleCancel(): void {
  doDeny('user')
}

/** 首次点击 → armed（显示二次确认）；二次点击 → 批准 */
function handleConfirmClick(): void {
  if (isResolved.value || !writes.value.length) return
  if (phase.value === 'pending') {
    armedAt = Date.now()
    phase.value = 'armed'
    return
  }
  // armed → 批准：需在 armed 态停留 ≥ARM_APPROVE_GAP_MS，双击的第二次点击被忽略
  if (Date.now() - armedAt < ARM_APPROVE_GAP_MS) return
  const ids = writes.value.map(w => w.tool_call_id)
  resolveOutcome('approved', 'user')
  chatStore.respondToConfirm({ approved_tool_call_ids: ids, denied_tool_call_ids: [] })
}

/** 完全清空本卡（本地快照 + 定时器） */
function clearCard(): void {
  writes.value = []
  phase.value = 'pending'
  decision.value = null
  remainingMs.value = 0
  stopCountdown()
  stopHideTimer()
}

// ═══════════ 监听 store：新确认出现 → 初始化；外部清空 → 未决议则直接收卡 ═══════════
watch(
  () => chatStore.pendingConfirm,
  (val) => {
    if (val?.writes?.length) {
      stopHideTimer()
      writes.value = [...val.writes]
      phase.value = 'pending'
      decision.value = null
      startCountdown()
      nextTick(() => {
        document.getElementById('write-confirm-card')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
      })
    } else if (!decision.value) {
      // store 被外部（新发送/取消）清空且非本地决议 → 卡直接消失，不残留结果态
      clearCard()
    }
    // decision 已非空 = 本地决议清空的，保留快照供结果态渲染，由 scheduleHide 收尾
  },
  { immediate: true },
)

onUnmounted(() => {
  stopCountdown()
  stopHideTimer()
})

// ═══════════ 内容展示辅助 ═══════════

/** 危险操作详情表可读字段名映射 */
const DETAIL_LABELS: Record<string, string> = {
  sql: 'SQL',
  thread_id: '线程 ID',
  elapsed_seconds: '已运行时长',
  current_sql: '当前 SQL',
  user: '用户',
  host: '来源主机',
  database: '数据库',
  command: '命令类型',
  state: '状态',
  pid: '进程 ID',
  transaction_id: '事务 ID',
}

function formatDetailValue(key: string, value: unknown): string {
  if (value === null || value === undefined) return '-'
  const s = String(value)
  // 时长字段友好格式化
  if (key === 'elapsed_seconds' && /^\d+$/.test(s)) {
    const sec = parseInt(s, 10)
    if (sec >= 86400) return `${Math.floor(sec / 86400)}d ${Math.floor((sec % 86400) / 3600)}h`
    if (sec >= 3600) return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`
    if (sec >= 60) return `${Math.floor(sec / 60)}m ${sec % 60}s`
    return `${sec}s`
  }
  if (s.length > 200) return s.slice(0, 197) + '...'
  return s
}

/** 预估影响行数 → 展示文案（行数缺失时提示无法预估，避免误导） */
function fmtImpactRows(rows?: number | null): string {
  if (rows === null || rows === undefined || rows < 0) return '影响行数无法预估'
  return `预计影响约 ${rows.toLocaleString('zh-CN')} 行`
}

/** 影响信息兜底说明 */
const DEFAULT_IMPACT_NOTE = 'EXPLAIN 估算，非精确值'

/** details 中可读出的数据库名（sql_write 通常无该字段，保留兼容） */
function findDbName(w: ConfirmableWrite): string | undefined {
  const db = w.details?.database
  return typeof db === 'string' && db.length ? db : undefined
}
</script>

<template>
  <Transition name="confirm-fade">
    <div
      v-if="writes.length > 0"
      id="write-confirm-card"
      class="write-confirm-card"
      :class="[`wconf-cat-${category}`, { 'is-armed': isArmed, 'is-resolved': isResolved }]"
    >
      <!-- ═══════ Header：红点 + 标题；右侧决议徽章/条数 ═══════ -->
      <div class="wconf-head">
        <div class="wconf-head-left">
          <span
            class="wconf-dot"
            :class="{ 'is-approved': decision?.kind === 'approved', 'is-cancelled': decision?.kind === 'cancelled' }"
          ></span>
          <span class="wconf-title">{{ titleText }}</span>
        </div>
        <div class="wconf-head-right">
          <span v-if="isResolved && decision" class="wconf-badge" :class="`is-${decision.kind}`">
            {{ decision.kind === 'approved' ? '已批准' : '已取消' }}
          </span>
          <span v-else-if="pendingCount > 1" class="wconf-count">共 {{ pendingCount }} 条</span>
        </div>
      </div>

      <!-- ═══════ 内容区 ═══════ -->
      <div class="wconf-body">
        <!-- ── sql_write：SQL 代码井 + 影响预估行 ── -->
        <template v-if="category === 'sql_write'">
          <div v-for="w in writes" :key="w.tool_call_id" class="wconf-item">
            <!-- SQL 代码区 -->
            <div class="wconf-sqlblock">
              <pre class="wconf-sql sql-row-code"><code>{{ w.details?.sql ?? w.description }}</code></pre>
            </div>

            <!-- 影响预估区（ImpactEstimateStage → w.impact，纯展示增强） -->
            <div
              v-if="w.impact?.available"
              class="wconf-impact sql-impact"
              :class="{ 'sql-impact--high': w.impact?.high_impact }"
            >
              <template v-if="w.impact?.per_statement?.length">
                <!-- 事务写：逐语句预估 -->
                <div
                  v-for="stmt in w.impact!.per_statement!"
                  :key="`${w.tool_call_id}-${stmt.idx}`"
                  class="wconf-improw"
                >
                  <span class="wconf-imp-idx">#{{ stmt.idx }}</span>
                  <span class="wconf-imp-type">{{ stmt.stmt_type ?? '语句' }}</span>
                  <span class="wconf-imp-rows">{{ fmtImpactRows(stmt.estimated_rows) }}</span>
                </div>
              </template>
              <template v-else>
                <!-- 单语句预估：影响行数（红字强调）+ 目标表/库 -->
                <div class="wconf-improw">
                  <span class="wconf-imp-rows">{{ fmtImpactRows(w.impact?.estimated_rows) }}</span>
                  <span v-if="w.impact?.target_table" class="wconf-imp-chip">
                    目标表 <b>{{ w.impact.target_table }}</b>
                  </span>
                  <span v-if="findDbName(w)" class="wconf-imp-chip">
                    库 <b>{{ findDbName(w) }}</b>
                  </span>
                </div>
              </template>
              <!-- 估算精度说明 -->
              <p v-if="w.impact?.note || w.impact" class="wconf-impnote">
                {{ w.impact?.note ?? DEFAULT_IMPACT_NOTE }}
              </p>
            </div>
          </div>
        </template>

        <!-- ── connection_kill：连接详情规格表 ── -->
        <template v-else-if="category === 'connection_kill'">
          <div v-for="w in writes" :key="w.tool_call_id" class="wconf-item">
            <div class="wconf-spec">
              <template v-for="(label, k) in DETAIL_LABELS" :key="k">
                <div v-if="k in (w.details ?? {})" class="wconf-spec-row">
                  <span class="wconf-spec-k">{{ label }}</span>
                  <span class="wconf-spec-v">{{ formatDetailValue(k, w.details?.[k]) }}</span>
                </div>
              </template>
              <!-- 兜底：渲染已知标签之外的额外字段 -->
              <template v-for="(val, k) in w.details" :key="'x-' + String(k)">
                <div v-if="!(k in DETAIL_LABELS)" class="wconf-spec-row">
                  <span class="wconf-spec-k">{{ k }}</span>
                  <span class="wconf-spec-v">{{ formatDetailValue(k, val) }}</span>
                </div>
              </template>
            </div>
            <div class="wconf-permaalert">此操作不可逆，将立即断开该连接</div>
          </div>
        </template>

        <!-- ── generic：工具 + 参数规格表（降级兜底） ── -->
        <template v-else>
          <div v-for="w in writes" :key="w.tool_call_id" class="wconf-item">
            <div class="wconf-spec">
              <div class="wconf-spec-row">
                <span class="wconf-spec-k">工具</span>
                <span class="wconf-spec-v">{{ w.tool }}</span>
              </div>
              <div class="wconf-spec-row">
                <span class="wconf-spec-k">描述</span>
                <span class="wconf-spec-v">{{ w.description }}</span>
              </div>
              <template v-for="(val, k) in w.details" :key="String(k)">
                <div class="wconf-spec-row">
                  <span class="wconf-spec-k">{{ DETAIL_LABELS[k as string] ?? k }}</span>
                  <span class="wconf-spec-v">{{ formatDetailValue(k as string, val) }}</span>
                </div>
              </template>
            </div>
          </div>
        </template>
      </div>

      <!-- ═══════ 二次确认提示（armed 条件显示） ═══════ -->
      <div v-if="isArmed" class="wconf-armed">
        <svg
          class="wconf-armed-icon" width="14" height="14" viewBox="0 0 24 24"
          fill="none" stroke="currentColor" stroke-width="2"
          stroke-linecap="round" stroke-linejoin="round"
        >
          <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
          <line x1="12" y1="9" x2="12" y2="13"/>
          <line x1="12" y1="17" x2="12.01" y2="17"/>
        </svg>
        <span class="wconf-armed-text">{{ armedHint }}</span>
      </div>

      <!-- ═══════ 操作栏（决议后替换为结果行） ═══════ -->
      <div v-if="isResolved" class="wconf-result" :class="`is-${decision?.kind}`">
        {{ resultText }}
      </div>
      <div v-else class="wconf-actions">
        <span class="wconf-countdown" :class="{ 'is-danger': countdownDanger }">
          {{ countdownSec }}s 后自动取消
        </span>
        <div class="wconf-action-btns">
          <button class="wconf-btn wconf-btn--ghost" type="button" @click="handleCancel">
            {{ cancelLabel }}
          </button>
          <button
            class="wconf-btn wconf-btn--primary"
            :class="{ 'is-danger': isArmed }"
            type="button"
            @click="handleConfirmClick"
          >
            {{ primaryLabel }}
          </button>
        </div>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
/* ══════════════════════════════════════════════════════════════
   卡片主题变量 — 深色（DB-Pilot 默认）
   中性卡 + 克制语义色：不再有蓝/绿彩色左边框与图标堆砌
   ══════════════════════════════════════════════════════════════ */
.write-confirm-card {
  --wconf-bg: #242838;
  --wconf-border: #2e3347;
  --wconf-radius: 8px;
  --wconf-title: #e6ebf5;
  --wconf-mut: #98a3ba;          /* 次要/说明文字 */
  --wconf-faint: #6f7a91;        /* 更弱标注（倒计时等） */
  --wconf-divider: rgba(148, 163, 184, 0.16);
  --wconf-dot: #ef4444;
  --wconf-approved: #34d399;     /* 批准：青绿，对齐 ✓ 已完成调用 */
  --wconf-cancelled: #9aa3b8;
  --wconf-sql-bg: #1a2040;       /* SQL 代码井：蓝紫底 */
  --wconf-sql-text: #93c5fd;     /* SQL 代码：与已完成调用代码块同调 */
  --wconf-impact-bg: #1e2434;    /* 影响预估：中性偏浅，与 SQL 井区隔 */
  --wconf-improws: #f87171;      /* 影响行数强调 */
  --wconf-chip: #a7b1c5;
  --wconf-warn-bg: #2a1f10;      /* 二次确认/危险提示：橙底克制 */
  --wconf-warn-text: #f6bd6a;
  --wconf-perma-bg: rgba(239, 68, 68, 0.10);
  --wconf-perma-text: #f87171;
  --wconf-btn-bg: #3b82f6;
  --wconf-btn-text: #ffffff;
  --wconf-btn-hover: #2563eb;
  --wconf-btn-danger: #ef4444;
  --wconf-btn-danger-hover: #dc2626;
  --wconf-ghost: #aeb8cc;
  --wconf-ghost-border: rgba(174, 184, 204, 0.4);
  --wconf-shadow: 0 4px 16px -2px rgba(0, 0, 0, 0.35);

  margin: 4px 0 20px;
  background: var(--wconf-bg);
  border: 1px solid var(--wconf-border);
  border-radius: var(--wconf-radius);
  box-shadow: var(--wconf-shadow);
  overflow: hidden;
  font-size: 13px;
  line-height: 1.55;
  color: var(--wconf-title);
}

/* ═══════ 浅色主题覆盖 ═══════ */
[data-theme="light"] .write-confirm-card {
  --wconf-bg: #ffffff;
  --wconf-border: #e5e7eb;
  --wconf-title: #111827;
  --wconf-mut: #6b7280;
  --wconf-faint: #9ca3af;
  --wconf-divider: #eceef1;
  --wconf-dot: #ef4444;
  --wconf-approved: #16a34a;
  --wconf-cancelled: #9ca3af;
  --wconf-sql-bg: rgba(248, 250, 252, 0.55);
  --wconf-sql-text: #334155;
  --wconf-impact-bg: #fafafa;
  --wconf-improws: #ef4444;
  --wconf-chip: #64748b;
  --wconf-warn-bg: #fffbeb;
  --wconf-warn-text: #b45309;
  --wconf-perma-bg: rgba(239, 68, 68, 0.06);
  --wconf-perma-text: #dc2626;
  --wconf-btn-bg: #2563eb;
  --wconf-btn-text: #ffffff;
  --wconf-btn-hover: #1d4ed8;
  --wconf-btn-danger: #ef4444;
  --wconf-btn-danger-hover: #dc2626;
  --wconf-ghost: #6b7280;
  --wconf-ghost-border: transparent;
  --wconf-shadow: 0 1px 2px rgba(16, 24, 40, 0.05), 0 1px 3px rgba(16, 24, 40, 0.06);
}

/* ═══════════════ Header ═══════════════ */
.wconf-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px 10px;
  border-bottom: 1px solid var(--wconf-divider);
}
.wconf-head-left {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
}
.wconf-dot {
  width: 6px;
  height: 6px;
  min-width: 6px;
  border-radius: 50%;
  background: var(--wconf-dot);
  transition: background var(--transition-fast);
}
.wconf-dot.is-approved { background: var(--wconf-approved); }
.wconf-dot.is-cancelled { background: var(--wconf-cancelled); }
.wconf-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--wconf-title);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.wconf-head-right { flex-shrink: 0; display: flex; }
.wconf-count {
  font-size: 11px;
  color: var(--wconf-faint);
  padding: 1px 8px;
  border: 1px solid var(--wconf-divider);
  border-radius: 999px;
  white-space: nowrap;
}
.wconf-badge {
  font-size: 11px;
  font-weight: 500;
  padding: 1px 8px;
  border-radius: 999px;
  white-space: nowrap;
}
.wconf-badge.is-approved { color: var(--wconf-approved); background: color-mix(in srgb, var(--wconf-approved) 14%, transparent); }
.wconf-badge.is-cancelled { color: var(--wconf-cancelled); background: color-mix(in srgb, var(--wconf-cancelled) 16%, transparent); }

/* ═══════════════ 内容区 ═══════════════ */
.wconf-body { padding: 12px 16px; display: flex; flex-direction: column; gap: 10px; }

.wconf-item { display: flex; flex-direction: column; gap: 8px; }
.wconf-item + .wconf-item {
  padding-top: 10px;
  border-top: 1px dashed var(--wconf-divider);
}

/* ── SQL 代码井：与影响预估区在视觉上做硬区分（等宽 + 蓝紫井） ── */
.wconf-sqlblock { min-width: 0; }
.wconf-sql {
  margin: 0;
  padding: 9px 12px;
  background: var(--wconf-sql-bg);
  border: 1px solid color-mix(in srgb, var(--wconf-sql-text) 18%, transparent);
  border-radius: 6px;
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 12px;
  line-height: 1.6;
  color: var(--wconf-sql-text);
  white-space: pre-wrap;
  word-break: break-all;
  overflow-x: auto;
}
.wconf-sql code { font-family: inherit; }

/* ── 影响预估区：中性浅底 + 红字行数（区别于 SQL 井） ── */
.wconf-impact {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 12px;
  background: var(--wconf-impact-bg);
  border-radius: 6px;
}
.wconf-improw {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}
.wconf-imp-idx {
  font-family: var(--font-mono, monospace);
  font-size: 11px;
  color: var(--wconf-faint);
}
.wconf-imp-type {
  font-family: var(--font-mono, monospace);
  font-size: 11px;
  color: var(--wconf-mut);
  padding: 0 6px;
  border: 1px solid var(--wconf-divider);
  border-radius: 4px;
}
.wconf-imp-rows {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--wconf-improws);
  font-variant-numeric: tabular-nums;
}
.wconf-imp-chip {
  font-size: 11.5px;
  color: var(--wconf-chip);
}
.wconf-imp-chip b { font-weight: 500; color: var(--wconf-title); }
.wconf-impnote {
  margin: 0;
  font-size: 11px;
  font-style: italic;
  color: var(--wconf-faint);
}

/* ── 连接/通用参数规格表 ── */
.wconf-spec {
  border: 1px solid var(--wconf-divider);
  border-radius: 6px;
  overflow: hidden;
}
.wconf-spec-row {
  display: flex;
  font-size: 12px;
  line-height: 1.5;
}
.wconf-spec-row + .wconf-spec-row { border-top: 1px solid var(--wconf-divider); }
.wconf-spec-k {
  flex: 0 0 104px;
  padding: 6px 10px;
  color: var(--wconf-mut);
  font-size: 11px;
  border-right: 1px solid var(--wconf-divider);
  background: color-mix(in srgb, var(--wconf-mut) 6%, transparent);
}
.wconf-spec-v {
  flex: 1;
  min-width: 0;
  padding: 6px 10px;
  color: var(--wconf-title);
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 12px;
  word-break: break-all;
}

/* 终止连接常驻危险提示 */
.wconf-permaalert {
  padding: 7px 12px;
  font-size: 12px;
  font-weight: 500;
  color: var(--wconf-perma-text);
  background: var(--wconf-perma-bg);
  border-radius: 6px;
}

/* ═══════════════ 二次确认提示 ═══════════════ */
.wconf-armed {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin: 0 16px 10px;
  padding: 8px 12px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--wconf-warn-text);
  background: var(--wconf-warn-bg);
  border: 1px solid color-mix(in srgb, var(--wconf-warn-text) 22%, transparent);
  border-radius: 6px;
}
.wconf-armed-icon { flex-shrink: 0; margin-top: 1px; }
.wconf-armed-text { flex: 1; }

/* ═══════════════ 操作栏 ═══════════════ */
.wconf-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 11px 16px 13px;
  border-top: 1px solid var(--wconf-divider);
}
.wconf-countdown {
  font-size: 11px;
  color: var(--wconf-faint);
  font-variant-numeric: tabular-nums;
  transition: color var(--transition-fast);
}
.wconf-countdown.is-danger { color: var(--wconf-dot); font-weight: 600; }
.wconf-action-btns { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }

.wconf-btn {
  font-family: var(--font-body, inherit);
  font-size: 12.5px;
  font-weight: 500;
  line-height: 1;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  transition: background var(--transition-fast), color var(--transition-fast), border-color var(--transition-fast);
}
.wconf-btn:focus-visible { outline: none; box-shadow: 0 0 0 2px var(--wconf-bg), 0 0 0 4px color-mix(in srgb, var(--wconf-btn-bg) 60%, transparent); }

/* 取消：浅色=文字按钮；深色=带描边 */
.wconf-btn--ghost {
  padding: 6px 12px;
  color: var(--wconf-ghost);
  background: transparent;
  border: 1px solid var(--wconf-ghost-border);
}
.wconf-btn--ghost:hover { color: var(--wconf-title); border-color: var(--wconf-ghost); }

/* 确认：主题蓝 */
.wconf-btn--primary {
  padding: 7px 16px;
  color: var(--wconf-btn-text);
  background: var(--wconf-btn-bg);
}
.wconf-btn--primary:hover { background: var(--wconf-btn-hover); }
/* armed：二次确认态按钮变红 */
.wconf-btn--primary.is-danger { background: var(--wconf-btn-danger); }
.wconf-btn--primary.is-danger:hover { background: var(--wconf-btn-danger-hover); }

/* ═══════════════ 结果行（替换操作栏） ═══════════════ */
.wconf-result {
  padding: 11px 16px 13px;
  border-top: 1px solid var(--wconf-divider);
  font-size: 12.5px;
  font-weight: 500;
}
.wconf-result.is-approved { color: var(--wconf-approved); }
.wconf-result.is-cancelled { color: var(--wconf-cancelled); }

/* ═══════════════ 进场过渡 ═══════════════ */
.confirm-fade-enter-active { transition: opacity 0.2s ease, transform 0.2s ease; }
.confirm-fade-leave-active { transition: opacity 0.16s ease, transform 0.16s ease; }
.confirm-fade-enter-from { opacity: 0; transform: translateY(-6px); }
.confirm-fade-leave-to { opacity: 0; transform: translateY(-4px); }
</style>
