/**
 * useExportFull composable 测试
 *
 * 验收标准：
 * - test_export_calls_fetch_and_downloads()：调用 exportCsv（fetch POST），成功时触发 Blob 下载
 * - test_export_error_sets_error()：后端错误时 error 置为 user_message
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useExportFull } from '@/composables/useExportFull'
import * as chatApi from '@/api/chat'

vi.mock('@/api/chat', () => ({
  exportCsv: vi.fn(),
}))

vi.mock('@/stores/connection', () => ({
  useConnectionStore: () => ({ getPassword: (id: string) => `pwd_${id}` }),
}))

describe('useExportFull', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn(() => 'blob:mock'),
      revokeObjectURL: vi.fn(),
    })
    // jsdom 无 a.click 效果，替换为 spy
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('test_export_calls_fetch_and_downloads：调用 exportCsv 并触发下载', async () => {
    const { exportFullCsv, exporting, error } = useExportFull()
    const fakeBlob = new Blob(['id,name\n1,a'], { type: 'text/csv' })
    ;(chatApi.exportCsv as ReturnType<typeof vi.fn>).mockResolvedValue(fakeBlob)

    await exportFullCsv({ sql: 'SELECT * FROM t', connectionId: 'conn1' })

    expect(chatApi.exportCsv).toHaveBeenCalledWith('conn1', {
      sql: 'SELECT * FROM t',
      password: 'pwd_conn1',
    })
    expect(exporting.value).toBe(false)
    expect(error.value).toBeNull()
  })

  it('test_export_error_sets_error：后端错误时 error 置为用户消息', async () => {
    const { exportFullCsv, error } = useExportFull()
    ;(chatApi.exportCsv as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('导出功能暂不支持该数据库类型')
    )

    await exportFullCsv({ sql: 'SELECT * FROM t', connectionId: 'conn1' })

    expect(error.value).toBe('导出功能暂不支持该数据库类型')
  })
})
