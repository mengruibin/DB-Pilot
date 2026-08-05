/**
 * MessageBubble tool_result 导出按钮测试（query-result-export-plan）
 *
 * 验收标准：
 * - 有 exportSql 时渲染「导出完整结果」按钮（含行数）
 * - 无 exportSql 时不渲染按钮
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import { useChatStore } from '@/stores/chat'
import MessageBubble from '@/components/chat/MessageBubble.vue'
import type { StoreMessage } from '@/stores/chat'

vi.mock('@/composables/useExportFull', () => ({
  useExportFull: () => ({
    // 注意：exporting/error 须为假值，模板 v-if="exporting" 才渲染「导出完整结果」
    //（计划初版误写为 vi.fn()（真值），会渲染成「导出中…」导致断言失败）
    exporting: false,
    error: null,
    exportFullCsv: vi.fn(),
  }),
}))

function makeMessage(overrides: Partial<StoreMessage> = {}): StoreMessage {
  return {
    id: 'm1',
    sessionId: 's1',
    role: 'assistant',
    type: 'tool_result',
    content: '返回 5000 行',
    createdAt: new Date().toISOString(),
    ...overrides,
  } as StoreMessage
}

const stubs = {
  MarkdownRenderer: { template: '<div class="md"><slot /></div>' },
  SqlBlock: true,
  ResultTable: true,
  ErrorCard: true,
  DiagnosisCard: true,
}

describe('MessageBubble tool_result 导出按钮', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    const chatStore = useChatStore()
    chatStore.sessions = [
      { id: 's1', connection_id: 'conn1', title: 't', created_at: '', last_active_at: '', status: 'active', message_count: 0, tokens_used_total: 0 },
    ]
    chatStore.currentSessionId = 's1'
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('test_export_button_renders_with_sql：有 exportSql 时渲染按钮并显示行数', () => {
    const wrapper = mount(MessageBubble, {
      props: {
        message: makeMessage({ exportSql: 'SELECT * FROM t', exportTotalRows: 5000 }),
        isLast: false,
        isStreaming: false,
      },
      global: { stubs },
    })
    const btn = wrapper.find('.export-full-btn')
    expect(btn.exists()).toBe(true)
    expect(btn.text()).toContain('导出完整结果')
    expect(btn.text()).toContain('5000')
  })

  it('test_export_button_hidden_without_sql：无 exportSql 时不渲染按钮', () => {
    const wrapper = mount(MessageBubble, {
      props: {
        message: makeMessage({}),
        isLast: false,
        isStreaming: false,
      },
      global: { stubs },
    })
    expect(wrapper.find('.export-full-btn').exists()).toBe(false)
  })
})
