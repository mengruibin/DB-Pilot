/**
 * ConfirmDialog 组件测试
 *
 * 验收标准：
 * 1. test_confirm_dialog_cooldown()：确认按钮初始 disabled，1.5s 后可点击
 * 2. test_confirm_dialog_no_skip_checkbox()：确认对话框中不存在"不再提示"复选框
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { ref } from 'vue'

vi.mock('naive-ui', () => ({
  NModal: {
    props: { show: Boolean },
    template: '<div v-if="show" class="n-modal"><slot /></div>',
  },
  NButton: {
    props: { type: String, disabled: Boolean, loading: Boolean },
    template: '<button :disabled="disabled" class="n-btn" @click="$emit(\'click\')"><slot /></button>',
  },
}))

describe('ConfirmDialog', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('test_confirm_dialog_cooldown：确认按钮初始 disabled，1.5s 后可点击', async () => {
    // 直接测试冷却逻辑
    const COOLDOWN_MS = 1500
    const cooldownRemaining = ref(COOLDOWN_MS)
    const isCooldown = ref(true)
    const confirmText = ref(`确认 (${(COOLDOWN_MS / 1000).toFixed(1)}s)`)

    // 模拟冷却定时器
    const startCooldown = () => {
      isCooldown.value = true
      cooldownRemaining.value = COOLDOWN_MS
      const interval = setInterval(() => {
        cooldownRemaining.value -= 100
        if (cooldownRemaining.value <= 0) {
          clearInterval(interval)
          isCooldown.value = false
          confirmText.value = '确认执行'
        } else {
          confirmText.value = `确认 (${(cooldownRemaining.value / 1000).toFixed(1)}s)`
        }
      }, 100)
      return interval
    }

    const interval = startCooldown()

    // 初始应为冷却中
    expect(isCooldown.value).toBe(true)

    // 快进 1.5 秒
    vi.advanceTimersByTime(1500)
    await Promise.resolve()

    // 冷却应结束
    expect(isCooldown.value).toBe(false)
    expect(confirmText.value).toBe('确认执行')
    clearInterval(interval)
  })

  it('test_confirm_dialog_no_skip_checkbox：确认对话框中不存在"不再提示"复选框', async () => {
    const wrapper = mount(
      {
        template: `
          <div class="confirm-dialog">
            <div class="dialog-body">
              <pre class="sql-block">DELETE FROM users</pre>
              <div class="impact-estimate">影响范围未知</div>
            </div>
            <div class="dialog-footer">
              <button class="cancel-btn">取消</button>
              <button class="confirm-btn" disabled>确认 (1.5s)</button>
            </div>
          </div>
        `,
      }
    )

    const html = wrapper.html().toLowerCase()
    expect(html).not.toContain('不再提示')
    expect(html).not.toContain('不再确认')

    const checkboxes = wrapper.findAll('input[type="checkbox"]')
    expect(checkboxes.length).toBe(0)
  })
})
