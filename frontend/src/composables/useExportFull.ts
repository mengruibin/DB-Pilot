/**
 * 完整结果导出 composable（query-result-export-plan）
 *
 * 服务端流式 CSV 导出：从聊天 tool_result 卡片的「导出完整结果」按钮触发。
 * 数据量可能很大，导出由后端游标流式生成，前端 fetch → Blob → 下载，
 * 不把全量数据加载进前端内存（区别于 useExport 的客户端导出）。
 */
import { ref } from 'vue'
import { exportCsv } from '@/api/chat'
import { useConnectionStore } from '@/stores/connection'
import { safeFileName, triggerDownload } from '@/utils/download'

export interface ExportFullOptions {
  /** 要导出的只读 SQL（来自 tool_result 事件的 export_sql） */
  sql: string
  /** 目标连接 ID */
  connectionId: string
}

export function useExportFull() {
  /** 是否正在导出 */
  const exporting = ref(false)
  /** 导出错误消息（成功后为 null） */
  const error = ref<string | null>(null)

  /**
   * 导出完整结果为 CSV 并触发浏览器下载。
   * 密码从连接 store 内存取用（不落盘），每次请求传入。
   */
  async function exportFullCsv(opts: ExportFullOptions): Promise<void> {
    if (!opts.sql || !opts.connectionId || exporting.value) return

    exporting.value = true
    error.value = null
    try {
      const password = useConnectionStore().getPassword(opts.connectionId)
      const blob = await exportCsv(opts.connectionId, { sql: opts.sql, password })
      triggerDownload(blob, `${safeFileName('export')}.csv`)
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
    } finally {
      exporting.value = false
    }
  }

  return { exporting, error, exportFullCsv }
}
