<script setup lang="ts">
/**
 * SqlBlock — SQL 代码块组件
 *
 * 语法高亮（VS Code Dark+ 风格）+ 复制 + 只读/危险标记 + 凭据掩码。
 *
 * 依据 api-contract §1.2 SSE sql 事件
 *     frontend AGENTS.md §1 危险操作确认、§2 凭据掩码、§4 危险关键字红线
 */
import { ref, computed } from 'vue'

const props = defineProps<{
  /** SQL 全文 */
  sql: string
  /** 审计状态 */
  auditStatus?: string
  /** 是否只读（false=写操作） */
  isReadonly?: boolean
}>()

// ─── 凭据掩码 ───

/** 检测并掩码凭据字面量（IDENTIFIED BY 'xxx' 等模式） */
function maskCredentials(sql: string): string {
  // 匹配 IDENTIFIED BY '任意内容' 或 PASSWORD = '任意内容'
  return sql.replace(
    /(IDENTIFIED\s+BY\s+)'[^']*'/gi,
    "$1'***'"
  ).replace(
    /(PASSWORD\s*=\s*)'[^']*'/gi,
    "$1'***'"
  ).replace(
    /(IDENTIFIED\s+BY\s+)"[^"]*"/gi,
    '$1"***"'
  )
}

/** 掩码后的 SQL */
const maskedSql = computed(() => maskCredentials(props.sql))

// ─── 危险关键字检查 ───

const DANGER_KEYWORDS = /\b(DROP|DELETE|TRUNCATE|ALTER|UPDATE)\b/i

const hasDangerKeywords = computed(() => DANGER_KEYWORDS.test(props.sql))

// ─── SQL 语法高亮 Tokenizer ───

interface SqlToken {
  type: 'keyword' | 'function' | 'string' | 'number' | 'comment' | 'operator' | 'danger' | 'plain'
  value: string
}

/** SQL 关键字列表 */
const KEYWORDS = new Set([
  'SELECT', 'FROM', 'WHERE', 'AND', 'OR', 'NOT', 'IN', 'IS', 'NULL',
  'AS', 'ON', 'JOIN', 'LEFT', 'RIGHT', 'INNER', 'OUTER', 'CROSS', 'FULL',
  'GROUP', 'BY', 'ORDER', 'ASC', 'DESC', 'HAVING',
  'LIMIT', 'OFFSET',
  'INSERT', 'INTO', 'VALUES', 'UPDATE', 'SET', 'DELETE', 'FROM',
  'CREATE', 'TABLE', 'ALTER', 'DROP', 'TRUNCATE', 'INDEX', 'VIEW',
  'DISTINCT', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END',
  'UNION', 'ALL', 'EXISTS', 'LIKE', 'BETWEEN',
  'FOR', 'INNER', 'OUTER', 'NATURAL',
  'WITH', 'RECURSIVE',
  'EXPLAIN', 'ANALYZE',
  'IF', 'GRANT', 'REVOKE', 'CASCADE',
])

/** SQL 函数名列表 */
const FUNCTIONS = new Set([
  'COUNT', 'SUM', 'AVG', 'MIN', 'MAX',
  'NOW', 'DATE', 'DATE_FORMAT', 'DATE_ADD', 'DATE_SUB', 'DATEDIFF',
  'CONCAT', 'SUBSTRING', 'TRIM', 'UPPER', 'LOWER', 'LENGTH', 'REPLACE',
  'COALESCE', 'IFNULL', 'NULLIF', 'CAST', 'CONVERT',
  'ROW_NUMBER', 'RANK', 'DENSE_RANK', 'LEAD', 'LAG',
  'GROUP_CONCAT', 'JSON_EXTRACT', 'JSON_UNQUOTE',
  'LAST_INSERT_ID', 'FOUND_ROWS',
  'ROUND', 'FLOOR', 'CEIL', 'ABS', 'MOD',
  'CURRENT_DATE', 'CURRENT_TIME', 'CURRENT_TIMESTAMP',
  'SESSION_USER', 'CURRENT_USER', 'SYSTEM_USER',
  'EXTRACT', 'POSITION', 'LOCATE',
])

/** 将 SQL 文本分词 */
function tokenize(sql: string): SqlToken[] {
  const tokens: SqlToken[] = []
  // 正则表达式匹配各类型 token，按优先级排列
  const regex = /('(?:[^'\\]|\\.)*')|("(?:[^"\\]|\\.)*")|(--[^\n]*)|(\/\*[\s\S]*?\*\/)|(\b\d+(?:\.\d+)?\b)|(\b[A-Za-z_]\w*\b)|([<>!=]=?|&&|\|\||[+\-*/%,;.()\[\]])|(\s+)/g
  let match: RegExpExecArray | null
  let lastIndex = 0

  while ((match = regex.exec(sql)) !== null) {
    // 处理未匹配的间隔文本
    if (match.index > lastIndex) {
      const between = sql.slice(lastIndex, match.index)
      if (between.trim()) {
        tokens.push(...tokenizePlain(between))
      } else if (between) {
        tokens.push({ type: 'plain', value: between })
      }
    }

    const [
      , // full match
      strSingle,   // 1: 'string'
      strDouble,   // 2: "string"
      commentLine, // 3: -- line comment
      commentBlock,// 4: /* block */
      num,         // 5: number
      word,        // 6: word
      op,          // 7: operator
      space,       // 8: whitespace
    ] = match

    if (strSingle) {
      tokens.push({ type: 'string', value: strSingle })
    } else if (strDouble) {
      tokens.push({ type: 'string', value: strDouble })
    } else if (commentLine || commentBlock) {
      tokens.push({ type: 'comment', value: commentLine || commentBlock })
    } else if (num) {
      tokens.push({ type: 'number', value: num })
    } else if (word) {
      const upper = word.toUpperCase()
      if (DANGER_KEYWORDS.test(word)) {
        tokens.push({ type: 'danger', value: word })
      } else if (KEYWORDS.has(upper)) {
        tokens.push({ type: 'keyword', value: word })
      } else if (FUNCTIONS.has(upper)) {
        tokens.push({ type: 'function', value: word })
      } else {
        tokens.push({ type: 'plain', value: word })
      }
    } else if (op) {
      tokens.push({ type: 'operator', value: op })
    } else if (space) {
      tokens.push({ type: 'plain', value: space })
    }

    lastIndex = regex.lastIndex
  }

  // 处理剩余文本
  if (lastIndex < sql.length) {
    const remaining = sql.slice(lastIndex)
    tokens.push(...tokenizePlain(remaining))
  }

  return tokens
}

/** 处理非结构化文本 */
function tokenizePlain(text: string): SqlToken[] {
  // 将剩余文本中的危险关键字高亮
  const parts = text.split(DANGER_KEYWORDS)
  if (parts.length === 1) {
    return [{ type: 'plain', value: text }]
  }
  const result: SqlToken[] = []
  let remaining = text
  const re = new RegExp(DANGER_KEYWORDS.source, 'gi')
  let m: RegExpExecArray | null
  let idx = 0
  while ((m = re.exec(remaining)) !== null) {
    if (m.index > idx) {
      result.push({ type: 'plain', value: remaining.slice(idx, m.index) })
    }
    result.push({ type: 'danger', value: m[0] })
    idx = re.lastIndex
  }
  if (idx < remaining.length) {
    result.push({ type: 'plain', value: remaining.slice(idx) })
  }
  return result
}

/** 分词结果 */
const tokens = computed(() => tokenize(maskedSql.value))

// ─── 复制 ───

const copySuccess = ref(false)

async function handleCopy(): Promise<void> {
  try {
    await navigator.clipboard.writeText(maskedSql.value)
    copySuccess.value = true
    setTimeout(() => { copySuccess.value = false }, 2000)
  } catch {
    // 静默失败
  }
}

// ─── 行号 ───

const lineCount = computed(() => maskedSql.value.split('\n').length)
const lineNumbers = computed(() => {
  const count = lineCount.value
  return Array.from({ length: count }, (_, i) => i + 1)
})
</script>

<template>
  <div
    class="sql-block"
    :class="{
      'is-write': isReadonly === false,
      'is-rejected': auditStatus === 'rejected',
      'has-danger': hasDangerKeywords,
    }"
  >
    <!-- 警告条：写操作 -->
    <div v-if="isReadonly === false" class="sql-banner warn">
      <span class="banner-icon">🟡</span>
      <span class="banner-text">此操作将修改数据</span>
    </div>

    <!-- 警告条：审计拒绝 -->
    <div v-if="auditStatus === 'rejected'" class="sql-banner error">
      <span class="banner-icon">⛔</span>
      <span class="banner-text">SQL 审计未通过</span>
    </div>

    <!-- 代码区域 -->
    <div class="code-container">
      <!-- 行号 -->
      <div class="line-numbers" aria-hidden="true">
        <span v-for="n in lineNumbers" :key="n" class="line-num">{{ n }}</span>
      </div>

      <!-- 代码内容 -->
      <pre class="code-content"><code><template v-for="(tok, i) in tokens" :key="i">
  <span v-if="tok.type === 'keyword'" class="tk-keyword">{{ tok.value }}</span>
  <span v-else-if="tok.type === 'function'" class="tk-function">{{ tok.value }}</span>
  <span v-else-if="tok.type === 'string'" class="tk-string">{{ tok.value }}</span>
  <span v-else-if="tok.type === 'number'" class="tk-number">{{ tok.value }}</span>
  <span v-else-if="tok.type === 'comment'" class="tk-comment">{{ tok.value }}</span>
  <span v-else-if="tok.type === 'operator'" class="tk-operator">{{ tok.value }}</span>
  <span v-else-if="tok.type === 'danger'" class="tk-danger">{{ tok.value }}</span>
  <span v-else class="tk-plain">{{ tok.value }}</span>
</template></code></pre>

      <!-- 复制按钮 -->
      <button
        class="copy-btn"
        :class="{ success: copySuccess }"
        @click="handleCopy"
      >
        <template v-if="copySuccess">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
          已复制
        </template>
        <template v-else>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
          复制
        </template>
      </button>
    </div>
  </div>
</template>

<style scoped>
.sql-block {
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  overflow: hidden;
  transition: border-color 0.2s;
}

.sql-block.is-write {
  border-color: rgba(251, 191, 36, 0.3);
}

.sql-block.is-rejected {
  border-color: var(--color-error);
  box-shadow: 0 0 12px rgba(248, 113, 113, 0.12);
}

/* ─── 警告条 ─── */
.sql-banner {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  font-size: 11px;
  font-weight: 500;
  border-bottom: 1px solid var(--border-color);
}

.sql-banner.warn {
  background: rgba(251, 191, 36, 0.08);
  color: var(--color-warning);
}

.sql-banner.error {
  background: rgba(248, 113, 113, 0.08);
  color: var(--color-error);
}

.banner-icon {
  font-size: 12px;
}

/* ─── 代码区 ─── */
.code-container {
  display: flex;
  position: relative;
  background: #0D1117;
}

/* 行号 */
.line-numbers {
  display: flex;
  flex-direction: column;
  padding: 10px 0;
  padding-right: 10px;
  min-width: 36px;
  text-align: right;
  border-right: 1px solid rgba(255, 255, 255, 0.05);
  user-select: none;
  background: rgba(0, 0, 0, 0.2);
}

.line-num {
  display: block;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
  color: #4B5563;
}

/* 代码 */
.code-content {
  flex: 1;
  padding: 10px 14px;
  margin: 0;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
  color: #D4D4D4;
  overflow-x: auto;
  white-space: pre;
  tab-size: 2;
}

/* ─── 语法高亮配色 ─── */
.tk-keyword {
  color: #569CD6;
}

.tk-function {
  color: #DCDCAA;
}

.tk-string {
  color: #CE9178;
}

.tk-number {
  color: #B5CEA8;
}

.tk-comment {
  color: #6A9955;
  font-style: italic;
}

.tk-operator {
  color: #D4D4D4;
}

.tk-plain {
  color: #D4D4D4;
}

/* 危险关键字：红色波浪线 */
.tk-danger {
  color: #F87171;
  text-decoration: underline wavy #F87171;
  text-underline-offset: 3px;
}

/* ─── 复制按钮 ─── */
.copy-btn {
  position: absolute;
  top: 6px;
  right: 6px;
  display: flex;
  align-items: center;
  gap: 3px;
  padding: 3px 8px;
  background: rgba(30, 41, 59, 0.9);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  color: var(--text-tertiary);
  font-family: var(--font-body);
  font-size: 11px;
  cursor: pointer;
  opacity: 0;
  transition: all var(--transition-fast);
}

.code-container:hover .copy-btn {
  opacity: 1;
}

.copy-btn:hover {
  background: rgba(45, 212, 191, 0.1);
  border-color: rgba(45, 212, 191, 0.3);
  color: var(--accent-teal);
}

.copy-btn.success {
  opacity: 1;
  background: rgba(52, 211, 153, 0.1);
  border-color: rgba(52, 211, 153, 0.3);
  color: var(--color-success);
}
</style>
