/**
 * WriteConfirmation 组件测试（write-impact-estimate-plan Task 6 验收）
 *
 * 验收标准：
 * 1. writes 带 impact → sql_write 卡片展示预估影响行数（含千分位与 EXPLAIN 说明）
 * 2. writes 不带 impact → 卡片不渲染影响行区块（纯展示增强，不影响既有结构）
 * 3. 事务写 per_statement → 逐语句行数展示
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'

// ── mock 依赖 ──
// vi.hoisted 在 import 前执行，不能引用外部 import；用 getter 让组件按 Pinia
// 解包语义读取 store.pendingConfirm 的"当前值"，测试在挂载前赋值即可。
const state = vi.hoisted(() => ({
  pendingConfirm: null as { writes: Array<Record<string, unknown>> } | null,
  respondToConfirm: vi.fn(),
}))

vi.mock('@/stores/chat', () => ({
  useChatStore: () => ({
    get pendingConfirm() {
      return state.pendingConfirm
    },
    respondToConfirm: state.respondToConfirm,
  }),
}))

// 组件现会读取用户设置（确认倒计时时长）；测试环境无 Pinia，给与历史默认一致的 mock
vi.mock('@/stores/settings', () => ({
  useSettingsStore: () => ({
    settings: { confirmCountdownSec: 60 },
  }),
}))

vi.mock('naive-ui', () => ({
  NButton: {
    props: { text: Boolean, size: String, disabled: Boolean },
    emits: ['click'],
    template:
      '<button :disabled="disabled" class="n-btn" @click="$emit(\'click\')"><slot /></button>',
  },
}))

import WriteConfirmation from '@/components/chat/WriteConfirmation.vue'

function sqlWriteWrite(overrides: Record<string, unknown>): Record<string, unknown> {
  return {
    tool_call_id: 'call_w',
    tool: 'execute_write_sql',
    category: 'sql_write',
    description: 'execute_write_sql: sql=UPDATE t SET a=1 WHERE id>0',
    details: { sql: 'UPDATE t SET a=1 WHERE id>0' },
    ...overrides,
  }
}

describe('WriteConfirmation — 写操作预估影响展示', () => {
  beforeEach(() => {
    state.pendingConfirm = null
    // jsdom 未实现 scrollIntoView，stub 避免 watch 回调抛错
    Element.prototype.scrollIntoView = vi.fn()
  })

  afterEach(() => {
    state.pendingConfirm = null
  })

  it('带 impact：sql_write 卡片展示预估影响行数 + EXPLAIN 说明', async () => {
    state.pendingConfirm = {
      writes: [
        sqlWriteWrite({
          impact: {
            available: true,
            stmt_type: 'UPDATE',
            target_table: 'users',
            estimated_rows: 12430,
            method: 'explain',
            high_impact: true,
            note: 'EXPLAIN 执行计划预估，非精确值，实际受影响行数可能不同',
          },
        }),
      ],
    }
    const wrapper = mount(WriteConfirmation)
    await wrapper.vm.$nextTick()

    const html = wrapper.html()
    expect(html).toContain('预计影响约 12,430 行')
    expect(html).toContain('EXPLAIN 执行计划预估，非精确值')
    expect(wrapper.find('.sql-impact').exists()).toBe(true)
    // high_impact：警示样式类挂上
    expect(wrapper.find('.sql-impact--high').exists()).toBe(true)
    wrapper.unmount()
  })

  it('不带 impact：卡片不渲染影响行区块（结构不受影响）', async () => {
    state.pendingConfirm = { writes: [sqlWriteWrite({})] }
    const wrapper = mount(WriteConfirmation)
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.sql-impact').exists()).toBe(false)
    expect(wrapper.find('.sql-row-code').text()).toContain('UPDATE t SET a=1 WHERE id>0')
    wrapper.unmount()
  })

  it('事务写 per_statement：逐语句展示预估行数，无法预估行提示', async () => {
    state.pendingConfirm = {
      writes: [
        {
          tool_call_id: 'call_tx',
          tool: 'execute_write_transaction',
          category: 'sql_write',
          description: 'execute_write_transaction',
          details: {
            sql: 'INSERT INTO orders (id) VALUES (1);\nUPDATE t SET a=1 WHERE id>0',
          },
          impact: {
            available: true,
            stmt_count: 2,
            method: 'explain',
            high_impact: false,
            per_statement: [
              { idx: 1, stmt_type: 'INSERT', estimated_rows: null },
              { idx: 2, stmt_type: 'UPDATE', estimated_rows: 5000 },
            ],
            note: 'EXPLAIN 执行计划预估，非精确值，实际受影响行数可能不同',
          },
        },
      ],
    }
    const wrapper = mount(WriteConfirmation)
    await wrapper.vm.$nextTick()

    const html = wrapper.html()
    expect(html).toContain('#1')
    expect(html).toContain('影响行数无法预估')
    expect(html).toContain('#2')
    expect(html).toContain('预计影响约 5,000 行')
    wrapper.unmount()
  })
})

describe('WriteConfirmation — 二次确认状态机', () => {
  beforeEach(() => {
    state.pendingConfirm = {
      writes: [sqlWriteWrite({ impact: { available: true, estimated_rows: 10 } })],
    }
    state.respondToConfirm.mockClear()
    // jsdom 未实现 scrollIntoView，stub 避免 watch 回调抛错
    Element.prototype.scrollIntoView = vi.fn()
  })

  afterEach(() => {
    state.pendingConfirm = null
    vi.useRealTimers()
  })

  it('首次点击进入 armed：显示二次确认提示、按钮变“再次确认”，未触发确认请求', async () => {
    const wrapper = mount(WriteConfirmation)
    await wrapper.vm.$nextTick()

    const primary = wrapper.find('.wconf-btn--primary')
    expect(primary.text()).toBe('确认执行')
    await primary.trigger('click')
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.wconf-armed').exists()).toBe(true)
    expect(primary.text()).toBe('再次确认')
    expect(wrapper.find('.wconf-btn--primary').classes()).toContain('is-danger')
    expect(state.respondToConfirm).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('二次点击批准：触发一次确认请求并传入全部 tool_call_id，展示已批准徽章', async () => {
    vi.useFakeTimers()
    const wrapper = mount(WriteConfirmation)
    await wrapper.vm.$nextTick()

    const primary = wrapper.find('.wconf-btn--primary')
    await primary.trigger('click')
    // 越过 350ms 防双击护栏后再二次点击
    await vi.advanceTimersByTimeAsync(400)
    await primary.trigger('click')
    await wrapper.vm.$nextTick()

    expect(state.respondToConfirm).toHaveBeenCalledTimes(1)
    expect(state.respondToConfirm).toHaveBeenCalledWith({
      approved_tool_call_ids: ['call_w'],
      denied_tool_call_ids: [],
    })
    expect(wrapper.find('.wconf-badge.is-approved').exists()).toBe(true)
    wrapper.unmount()
  })

  it('点击取消：触发拒绝请求，展示已取消徽章', async () => {
    const wrapper = mount(WriteConfirmation)
    await wrapper.vm.$nextTick()

    await wrapper.find('.wconf-btn--ghost').trigger('click')
    await wrapper.vm.$nextTick()

    expect(state.respondToConfirm).toHaveBeenCalledTimes(1)
    expect(state.respondToConfirm).toHaveBeenCalledWith({
      approved_tool_call_ids: [],
      denied_tool_call_ids: ['call_w'],
    })
    expect(wrapper.find('.wconf-badge.is-cancelled').exists()).toBe(true)
    wrapper.unmount()
  })

  it('60s 倒计时归零：自动拒绝并进入已取消', async () => {
    vi.useFakeTimers()
    const wrapper = mount(WriteConfirmation)
    await wrapper.vm.$nextTick()

    // 组件在 store 60s 兜底（1000ms 提前量）前先触发
    await vi.advanceTimersByTimeAsync(61_000)
    await wrapper.vm.$nextTick()

    expect(state.respondToConfirm).toHaveBeenCalledTimes(1)
    expect(state.respondToConfirm).toHaveBeenCalledWith({
      approved_tool_call_ids: [],
      denied_tool_call_ids: ['call_w'],
    })
    expect(wrapper.find('.wconf-badge.is-cancelled').exists()).toBe(true)
    wrapper.unmount()
  })
})
