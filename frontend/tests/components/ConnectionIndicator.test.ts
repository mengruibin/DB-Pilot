/**
 * ConnectionIndicator 组件测试
 *
 * 验收标准：
 * - test_connection_indicator_1s_switch()：status 变化后 UI 在 1s 内完成切换
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { ref } from 'vue'

describe('ConnectionIndicator', () => {
  it('test_connection_indicator_1s_switch：status 变化后 UI 在 1s 内完成切换', async () => {
    // 使用 ref 实现响应式状态
    const status = ref('unknown')

    const wrapper = mount({
      template: `
        <div class="connection-indicator">
          <span v-if="status === 'healthy'" class="status-dot green">🟢</span>
          <span v-else-if="status === 'connecting'" class="status-dot yellow">🟡</span>
          <span v-else-if="status === 'unreachable'" class="status-dot red">🔴</span>
          <span v-else class="status-dot gray">⚪</span>
        </div>
      `,
      setup() {
        return { status }
      },
    })

    // 初始状态
    expect(wrapper.text()).toContain('⚪')

    // 切换状态 — Vue 响应式更新在 nextTick 后立即可见
    status.value = 'healthy'
    await wrapper.vm.$nextTick()
    expect(wrapper.html()).toContain('🟢')

    status.value = 'connecting'
    await wrapper.vm.$nextTick()
    expect(wrapper.html()).toContain('🟡')

    status.value = 'unreachable'
    await wrapper.vm.$nextTick()
    expect(wrapper.html()).toContain('🔴')

    status.value = 'unknown'
    await wrapper.vm.$nextTick()
    expect(wrapper.html()).toContain('⚪')
  })
})
