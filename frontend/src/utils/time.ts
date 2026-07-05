/**
 * 时间格式化工具函数（F5）
 *
 * 用于侧边栏会话列表的相对时间显示。
 *
 * 格式规则：
 * - < 1分钟: "刚刚"
 * - < 1小时: "X分钟前"
 * - < 24小时: "X小时前"
 * - < 7天: "X天前"
 * - 同年: "M月D日"
 * - 跨年: "YYYY年M月D日"
 */

/**
 * 格式化为相对时间显示
 *
 * @param isoString ISO 8601 时间字符串
 * @returns 格式化后的相对时间字符串，无效输入返回空字符串
 */
export function formatRelativeTime(isoString: string | null | undefined): string {
  if (!isoString) return ''

  const date = new Date(isoString)
  if (Number.isNaN(date.getTime())) return ''

  const now = Date.now()
  const diffMs = now - date.getTime()

  // 未来时间或时间差为负 → 返回空
  if (diffMs < 0) return ''

  const diffSec = Math.floor(diffMs / 1000)

  // < 1分钟
  if (diffSec < 60) return '刚刚'

  const diffMin = Math.floor(diffSec / 60)

  // < 1小时
  if (diffMin < 60) return `${diffMin}分钟前`

  const diffHour = Math.floor(diffMin / 60)

  // < 24小时
  if (diffHour < 24) return `${diffHour}小时前`

  const diffDay = Math.floor(diffHour / 24)

  // < 7天
  if (diffDay < 7) return `${diffDay}天前`

  const nowDate = new Date()
  const thisYear = nowDate.getFullYear()
  const targetYear = date.getFullYear()
  const month = date.getMonth() + 1
  const day = date.getDate()

  // 同年
  if (targetYear === thisYear) return `${month}月${day}日`

  // 跨年
  return `${targetYear}年${month}月${day}日`
}
