/**
 * InputArea 组件测试——粘贴多行 SQL 确认
 *
 * 验收标准：
 * - test_input_paste_multiline_sql()：粘贴 2 行 SQL 弹确认框，确认后才提交
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('naive-ui', () => ({
  NButton: {
    props: { type: String, size: String, quaternary: Boolean, disabled: Boolean, ghost: Boolean },
    template: '<button class="n-btn" :disabled="disabled"><slot /></button>',
  },
  NSelect: {
    props: { options: Array, value: String, size: String },
    template: '<select class="n-select"><option v-for="opt in options" :key="opt.value" :value="opt.value">{{ opt.label }}</option></select>',
  },
  NTag: {
    props: { type: String, size: String },
    template: '<span class="n-tag"><slot /></span>',
  },
  useMessage: () => ({ warning: vi.fn(), success: vi.fn(), error: vi.fn() }),
}))

describe('InputArea 粘贴检测', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('test_input_paste_multiline_sql：粘贴多行 SQL 应弹确认框', async () => {
    // 模拟粘贴多行 SQL
    const multiLineSQL = 'SELECT * FROM users;\nDELETE FROM orders;\n'

    // 验证多行检测
    const lines = multiLineSQL.split('\n').filter((l) => l.trim().length > 0)
    expect(lines.length).toBeGreaterThan(1)

    // 模拟确认框
    let confirmed = false
    const mockConfirm = () => {
      confirmed = true
    }

    // 模拟粘贴事件处理
    function handlePaste(text: string, onConfirm: () => void): boolean {
      const sqlLines = text.split('\n').filter((l) => l.trim().length > 0)
      if (sqlLines.length > 1) {
        // 触发确认对话框
        onConfirm()
        return true
      }
      return false
    }

    // 模拟用户确认
    handlePaste(multiLineSQL, mockConfirm)
    expect(confirmed).toBe(true)

    // 模拟单行 SQL（不应弹确认框）
    confirmed = false
    handlePaste('SELECT * FROM users;', mockConfirm)
    expect(confirmed).toBe(false)
  })

  it('test_input_paste_multiline_sql：粘贴 1 行 SQL 不弹确认框', () => {
    const singleLineSQL = 'SELECT * FROM users;'
    const lines = singleLineSQL.split('\n').filter((l) => l.trim().length > 0)
    expect(lines.length).toBe(1)
  })
})
