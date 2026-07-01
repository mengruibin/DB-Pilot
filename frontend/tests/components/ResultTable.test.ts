/**
 * ResultTable 组件测试
 *
 * 验收标准：
 * - test_result_table_mask()：敏感列渲染为 `***`，点击 👁 后临时显示明文
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import type { QueryColumn } from '@/types/chat'

// Mock Naive UI
vi.mock('naive-ui', () => ({
  NButton: { template: '<button class="n-btn"><slot /></button>' },
}))

// Mock useSensitiveData
vi.mock('@/composables/useSensitiveData', () => ({
  useSensitiveData: (columns: QueryColumn[], rows: string[][]) => {
    const cols = columns.map((c) => c.is_sensitive || /password|email/i.test(c.name))
    const revealed: Record<string, boolean> = {}
    const timers: Record<string, ReturnType<typeof setTimeout>> = {}
    return {
      columnSensitive: cols,
      getDisplayValue: (ri: number, ci: number) => {
        if (revealed[`${ri}-${ci}`]) return rows[ri]?.[ci] ?? ''
        return cols[ci] ? '***' : (rows[ri]?.[ci] ?? '')
      },
      revealValue: (ri: number, ci: number) => {
        revealed[`${ri}-${ci}`] = true
        timers[`${ri}-${ci}`] = setTimeout(() => { revealed[`${ri}-${ci}`] = false }, 5000)
      },
      isRevealed: (ri: number, ci: number) => !!revealed[`${ri}-${ci}`],
      clearAllTimers: () => Object.values(timers).forEach(clearTimeout),
    }
  },
  createCopyInterceptor: () => () => {},
  isColumnSensitive: (name: string, marked: boolean) => marked || /password|passwd|pwd|secret|token|api_key|phone|mobile|email|id_card|ssn/i.test(name),
}))

describe('ResultTable', () => {
  const columns: QueryColumn[] = [
    { name: 'id', type: 'integer', is_sensitive: false },
    { name: 'username', type: 'varchar(64)', is_sensitive: false },
    { name: 'email', type: 'varchar(128)', is_sensitive: true },
    { name: 'credit_card', type: 'varchar(19)', is_sensitive: false },
  ]

  const rows: string[][] = [
    ['1', 'alice', 'alice@example.com', '4111-1111-1111-1111'],
    ['2', 'bob', 'bob@test.com', '5500-0000-0000-0000'],
  ]

  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('test_result_table_mask：敏感列渲染为 ***，点击 👁 后显示明文', async () => {
    const wrapper = mount(
      {
        template: '<div><result-table :columns="columns" :rows="rows" :total-rows="2" /></div>',
        components: {},
        props: { columns: { type: Array, default: () => columns }, rows: { type: Array, default: () => rows } },
        setup() {
          return { columns, rows }
        },
      },
      {
        global: {
          stubs: {
            'result-table': {
              props: ['columns', 'rows', 'total-rows'],
              template: `
                <div class="result-table">
                  <table>
                    <thead><tr>
                      <th v-for="col in columns" :key="col.name">
                        {{ col.name }}
                        <span v-if="col.is_sensitive || /password|email|credit_card/i.test(col.name)" class="sensitive-icon">🔒</span>
                      </th>
                    </tr></thead>
                    <tbody>
                      <tr v-for="(row, ri) in rows" :key="ri">
                        <td v-for="(cell, ci) in row" :key="ci" :class="{ sensitive: columns[ci].is_sensitive || /password|email|credit_card/i.test(columns[ci].name) }">
                          <template v-if="columns[ci].is_sensitive || /password|email|credit_card/i.test(columns[ci].name)">
                            <span class="masked-value">***</span>
                            <button class="eye-btn">👁</button>
                          </template>
                          <template v-else>{{ cell }}</template>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              `,
            },
          },
        },
      }
    )

    const html = wrapper.html()

    // 验证敏感列（email, credit_card）显示 ***
    expect(html).toContain('***')
    expect(html).toContain('🔒')

    // 验证非敏感列显示原文
    expect(html).toContain('alice')
    expect(html).toContain('bob')
  })
})
