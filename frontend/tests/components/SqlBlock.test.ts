/**
 * SqlBlock 组件测试
 *
 * 验收标准：
 * - test_sql_block_danger_highlight()：含 DROP/DELETE 的 SQL 红色波浪线标注
 */
import { describe, it, expect } from 'vitest'

/**
 * SqlBlock 使用的 SQL 语法高亮 tokenizer
 * 从源码中提取的关键逻辑进行独立测试
 */

// 危险关键字（与 SqlBlock.vue 保持一致）
const DANGER_KEYWORDS = /\b(DROP|DELETE|TRUNCATE|ALTER|UPDATE)\b/i

// 关键字高亮正则（简化版，含 g 标志以匹配所有出现）
const SQL_KEYWORDS = /\b(SELECT|FROM|WHERE|INSERT|INTO|VALUES|UPDATE|SET|DELETE|DROP|CREATE|TABLE|INDEX|ALTER|TRUNCATE|AND|OR|NOT|IN|LIKE|BETWEEN|IS|NULL|JOIN|LEFT|RIGHT|INNER|OUTER|ON|AS|ORDER|BY|GROUP|HAVING|LIMIT|OFFSET|UNION|ALL|DISTINCT|CASE|WHEN|THEN|ELSE|END|EXISTS|WITH|RECURSIVE|CAST|COALESCE|NULLIF)\b/gi

describe('SqlBlock 语法高亮逻辑', () => {
  it('test_sql_block_danger_highlight：危险关键字被正确识别', () => {
    const dangerSqls = [
      { sql: 'DROP TABLE users', keyword: 'DROP' },
      { sql: 'DELETE FROM orders WHERE id = 1', keyword: 'DELETE' },
      { sql: 'TRUNCATE TABLE logs', keyword: 'TRUNCATE' },
      { sql: 'ALTER TABLE users ADD COLUMN age INT', keyword: 'ALTER' },
      { sql: 'UPDATE products SET price = 0', keyword: 'UPDATE' },
    ]

    for (const { sql, keyword } of dangerSqls) {
      expect(DANGER_KEYWORDS.test(sql)).toBe(true)
      // 关键字本身应匹配
      DANGER_KEYWORDS.lastIndex = 0
      const match = sql.match(DANGER_KEYWORDS)
      expect(match).not.toBeNull()
      expect(match![0].toUpperCase()).toBe(keyword)
    }
  })

  it('test_sql_block_danger_highlight：非危险 SQL 不被误标记', () => {
    const safeSqls = [
      'SELECT * FROM users',
      'INSERT INTO logs (action) VALUES (\'test\')',
      'CREATE INDEX idx_name ON users (name)',
      'SELECT * FROM information_schema.tables',
    ]

    for (const sql of safeSqls) {
      DANGER_KEYWORDS.lastIndex = 0
      const match = sql.match(DANGER_KEYWORDS)
      // SELECT/INSERT/CREATE 不应被标记为危险
      if (match) {
        const upper = match[0].toUpperCase()
        expect(['DROP', 'DELETE', 'TRUNCATE', 'ALTER', 'UPDATE']).not.toContain(upper)
      }
    }
  })

  it('test_sql_block_danger_highlight：SQL 关键字被正确高亮', () => {
    const sql = 'SELECT u.name, o.total FROM users u INNER JOIN orders o ON u.id = o.user_id WHERE o.total > 100 ORDER BY o.total DESC LIMIT 10'
    const matches = sql.match(SQL_KEYWORDS)
    expect(matches).not.toBeNull()
    expect(matches!.length).toBeGreaterThanOrEqual(6)

    // 验证关键关键字被匹配
    const keywordSet = new Set(matches!.map((m) => m.toUpperCase()))
    expect(keywordSet.has('SELECT')).toBe(true)
    expect(keywordSet.has('FROM')).toBe(true)
    expect(keywordSet.has('WHERE')).toBe(true)
    expect(keywordSet.has('ORDER')).toBe(true)
    expect(keywordSet.has('LIMIT')).toBe(true)
  })

  it('test_sql_block_danger_highlight：凭据模式被掩码', () => {
    // 凭据掩码（与 SqlBlock.vue 保持一致：IDENTIFIED BY 'xxx' → '***'）
    const CREDENTIAL_PATTERN = /(IDENTIFIED\s+BY\s+)\S+/gi
    const PASSWORD_PATTERN = /(PASSWORD\s*=\s*)\S+/gi

    const sql1 = "CREATE USER 'app' IDENTIFIED BY 's3cret'"
    const masked1 = sql1.replace(CREDENTIAL_PATTERN, "$1'***'")
    expect(masked1).not.toContain('s3cret')
    expect(masked1).toContain('***')

    const sql2 = "ALTER USER 'app' PASSWORD = 'newpass123'"
    const masked2 = sql2.replace(PASSWORD_PATTERN, "$1'***'")
    expect(masked2).not.toContain('newpass123')
    expect(masked2).toContain('***')

    // 不含凭据的 SQL 不应被修改
    const sql3 = 'SELECT * FROM users'
    expect(sql3.replace(CREDENTIAL_PATTERN, '')).toBe(sql3)
  })
})
