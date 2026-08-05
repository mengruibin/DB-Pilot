/**
 * 浏览器下载工具（safeFileName / triggerDownload）
 *
 * 从 useExport.ts 抽出，供客户端导出（useExport）与服务端导出（useExportFull）共用。
 */

/** 文件名前缀（安全转义：去非法字符 + 时间戳） */
export function safeFileName(prefix: string = 'export'): string {
  const timestamp = Date.now()
  const safe = prefix.replace(/[\\/:*?"<>|]/g, '_').slice(0, 80)
  return `${safe}_${timestamp}`
}

/** 触发浏览器下载（Blob → 临时 <a> → click） */
export function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
