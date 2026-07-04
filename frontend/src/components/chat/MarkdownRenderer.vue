<script setup lang="ts">
/**
 * MarkdownRenderer — 美观的 Markdown 渲染组件
 *
 * 使用 markdown-it + highlight.js 渲染 LLM 返回的 Markdown 文本。
 * 支持代码高亮（SQL/JSON/Python/Shell 等）、GFM 表格、任务列表、链接等。
 *
 * 设计风格：深色编辑器排版，与 DB-Pilot Onyx Console 主题一致
 */
import { computed } from 'vue'
import hljs from 'highlight.js/lib/core'
import sql from 'highlight.js/lib/languages/sql'
import json from 'highlight.js/lib/languages/json'
import bash from 'highlight.js/lib/languages/bash'
import python from 'highlight.js/lib/languages/python'
import javascript from 'highlight.js/lib/languages/javascript'
import yaml from 'highlight.js/lib/languages/yaml'
import xml from 'highlight.js/lib/languages/xml'
import plaintext from 'highlight.js/lib/languages/plaintext'

import MarkdownIt from 'markdown-it'

// ─── 按需注册 highlight.js 语言（避免加载全部语言包） ───
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('json', json)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('shell', bash)
hljs.registerLanguage('python', python)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('js', javascript)
hljs.registerLanguage('yaml', yaml)
hljs.registerLanguage('yml', yaml)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('html', xml)
hljs.registerLanguage('plaintext', plaintext)
hljs.registerLanguage('text', plaintext)

// ─── markdown-it 实例（单例，不参与响应式） ───
const md = new MarkdownIt({
  html: false,           // 禁止原始 HTML，防止 XSS
  linkify: true,         // 自动识别 URL
  typographer: true,     // 智能排版（引号、破折号等）
  breaks: true,          // 单换行转 <br>
  highlight(str: string, lang: string): string {
    const language = lang && hljs.getLanguage(lang) ? lang : ''
    if (language) {
      try {
        const highlighted = hljs.highlight(str, { language, ignoreIllegals: true }).value
        return `<pre class="code-block"><div class="code-header"><span class="code-lang">${md.utils.escapeHtml(language)}</span><button class="code-copy" onclick="navigator.clipboard.writeText(this.closest('.code-block').querySelector('code').textContent)" title="复制代码">📋</button></div><code class="hljs language-${md.utils.escapeHtml(language)}">${highlighted}</code></pre>`
      } catch { /* fallback */ }
    }
    return `<pre class="code-block"><code class="hljs">${md.utils.escapeHtml(str)}</code></pre>`
  },
})

// ─── GFM 任务列表处理 ───
function processTaskLists(html: string): string {
  return html.replace(
    /<li>\[([ xX])\]\s*/g,
    (_, checked: string) =>
      `<li class="task-list-item"><input type="checkbox" disabled${checked.toLowerCase() === 'x' ? ' checked' : ''}> `
  )
}

// ─── 链接处理（新窗口打开 + 安全属性） ───
function processLinks(html: string): string {
  return html.replace(
    /<a\s+href="/g,
    '<a target="_blank" rel="noopener noreferrer" href="'
  )
}

const props = defineProps<{
  content: string
}>()

const renderedHtml = computed(() => {
  if (!props.content) return ''
  const raw = md.render(props.content)
  const withTasks = processTaskLists(raw)
  return processLinks(withTasks)
})
</script>

<template>
  <div class="markdown-body" v-html="renderedHtml" />
</template>

<style scoped>
/* ─── Markdown 渲染主体 — 深色编辑器排版风格 ─── */
.markdown-body {
  color: var(--text-primary);
  line-height: 1.75;
  font-size: 14px;
  word-wrap: break-word;
  overflow-wrap: break-word;
}

/* ─── 段落间距 ─── */
.markdown-body p {
  margin: 0 0 12px;
}
.markdown-body p:last-child {
  margin-bottom: 0;
}

/* ─── 换行（single break） ─── */
.markdown-body br {
  content: '';
  display: block;
  margin: 4px 0;
}

/* ─── 标题层级 —— Outfit 显示字体 ─── */
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4),
.markdown-body :deep(h5),
.markdown-body :deep(h6) {
  font-family: var(--font-display, 'Outfit', sans-serif);
  font-weight: 600;
  color: var(--text-primary);
  margin: 20px 0 10px;
  line-height: 1.35;
  letter-spacing: -0.01em;
}
.markdown-body :deep(h1) {
  font-size: 22px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border-color);
  margin-top: 0;
}
.markdown-body :deep(h2) {
  font-size: 18px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--border-color);
}
.markdown-body :deep(h3) {
  font-size: 16px;
}
.markdown-body :deep(h4) {
  font-size: 14.5px;
}
.markdown-body :deep(h5),
.markdown-body :deep(h6) {
  font-size: 14px;
  color: var(--text-secondary);
}

/* ─── 强调 ─── */
.markdown-body :deep(strong) {
  font-weight: 600;
  color: var(--text-primary);
}
.markdown-body :deep(em) {
  font-style: italic;
}

/* ─── 链接 — 青绿点缀 ─── */
.markdown-body :deep(a) {
  color: var(--accent-teal);
  text-decoration: none;
  border-bottom: 1px solid transparent;
  transition: border-color var(--transition-fast), color var(--transition-fast);
}
.markdown-body :deep(a:hover) {
  color: #5EE4D0;
  border-bottom-color: rgba(45, 212, 191, 0.4);
}

/* ─── 列表 ─── */
.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 22px;
  margin: 8px 0;
}
.markdown-body :deep(li) {
  margin: 4px 0;
  line-height: 1.65;
}
.markdown-body :deep(li::marker) {
  color: var(--accent-teal);
}

/* ─── 任务列表 — GFM 复选框 ─── */
.markdown-body :deep(.task-list-item) {
  list-style: none;
  margin-left: -22px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.markdown-body :deep(.task-list-item input[type='checkbox']) {
  appearance: none;
  -webkit-appearance: none;
  width: 15px;
  height: 15px;
  border: 1.5px solid var(--text-tertiary);
  border-radius: 3px;
  flex-shrink: 0;
  cursor: default;
  position: relative;
  transition: border-color var(--transition-fast), background var(--transition-fast);
}
.markdown-body :deep(.task-list-item input[type='checkbox']:checked) {
  background: var(--accent-teal);
  border-color: var(--accent-teal);
}
.markdown-body :deep(.task-list-item input[type='checkbox']:checked::after) {
  content: '✓';
  position: absolute;
  top: -2px;
  left: 2px;
  font-size: 12px;
  color: #0B0E14;
  font-weight: 700;
}

/* ─── 行内代码 ─── */
.markdown-body :deep(code):not(.hljs) {
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 0.88em;
  padding: 2px 7px;
  background: rgba(45, 212, 191, 0.08);
  border: 1px solid rgba(45, 212, 191, 0.12);
  border-radius: 4px;
  color: #5EE4D0;
  white-space: nowrap;
}

/* ─── 代码块 ─── */
.markdown-body :deep(pre.code-block) {
  position: relative;
  margin: 14px 0;
  border-radius: var(--radius-md, 6px);
  overflow: hidden;
  background: #0D1117;
  border: 1px solid var(--border-color);
}
/* 代码块顶部青绿装饰线 */
.markdown-body :deep(pre.code-block)::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  background: linear-gradient(90deg, var(--accent-teal), var(--accent-blue));
  opacity: 0.6;
}
.markdown-body :deep(pre.code-block code.hljs) {
  display: block;
  padding: 14px 16px;
  overflow-x: auto;
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 12.5px;
  line-height: 1.65;
  color: #E2E8F0;
  tab-size: 2;
}
/* 代码块头部（语言标签 + 复制按钮） */
.markdown-body :deep(.code-header) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 14px 0;
  user-select: none;
}
.markdown-body :deep(.code-lang) {
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 11px;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.markdown-body :deep(.code-copy) {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 13px;
  padding: 2px 4px;
  border-radius: 3px;
  opacity: 0;
  transition: opacity var(--transition-fast), background var(--transition-fast);
}
.markdown-body :deep(pre.code-block:hover .code-copy) {
  opacity: 0.7;
}
.markdown-body :deep(.code-copy:hover) {
  opacity: 1 !important;
  background: rgba(255, 255, 255, 0.06);
}

/* ─── 引用块 — 左侧青绿竖线 ─── */
.markdown-body :deep(blockquote) {
  margin: 12px 0;
  padding: 6px 14px;
  border-left: 3px solid var(--accent-teal);
  background: rgba(45, 212, 191, 0.04);
  border-radius: 0 var(--radius-sm, 4px) var(--radius-sm, 4px) 0;
  color: var(--text-secondary);
}
.markdown-body :deep(blockquote p) {
  margin: 4px 0;
}
.markdown-body :deep(blockquote p:last-child) {
  margin-bottom: 0;
}

/* ─── 表格 — 干净的信息布局 ─── */
.markdown-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 12px 0;
  font-size: 13px;
  border-radius: var(--radius-md, 6px);
  overflow: hidden;
}
.markdown-body :deep(th) {
  background: var(--bg-surface);
  color: var(--text-secondary);
  font-weight: 600;
  padding: 8px 12px;
  text-align: left;
  border-bottom: 2px solid var(--border-color);
  font-size: 12.5px;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}
.markdown-body :deep(td) {
  padding: 7px 12px;
  border-bottom: 1px solid var(--border-color);
  color: var(--text-primary);
}
.markdown-body :deep(tr:last-child td) {
  border-bottom: none;
}
.markdown-body :deep(tr:nth-child(even) td) {
  background: rgba(20, 25, 34, 0.4);
}
.markdown-body :deep(tr:hover td) {
  background: rgba(45, 212, 191, 0.03);
}

/* ─── 水平分割线 ─── */
.markdown-body :deep(hr) {
  border: none;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--border-color), transparent);
  margin: 20px 0;
}

/* ─── 图片 ─── */
.markdown-body :deep(img) {
  max-width: 100%;
  border-radius: var(--radius-md, 6px);
  border: 1px solid var(--border-color);
  margin: 8px 0;
}

/* ─── 内联代码高亮主题 — 深色编辑器风 ─── */
/* highlight.js 深色覆盖 — 与 Onyx Console 主题协调 */
.markdown-body :deep(.hljs) { color: #E2E8F0; }
.markdown-body :deep(.hljs-keyword) { color: #F472B6; }
.markdown-body :deep(.hljs-string) { color: #34D399; }
.markdown-body :deep(.hljs-number) { color: #FBBF24; }
.markdown-body :deep(.hljs-literal) { color: #F472B6; }
.markdown-body :deep(.hljs-built_in) { color: #38BDF8; }
.markdown-body :deep(.hljs-title) { color: #38BDF8; }
.markdown-body :deep(.hljs-attr) { color: #A78BFA; }
.markdown-body :deep(.hljs-attribute) { color: #A78BFA; }
.markdown-body :deep(.hljs-comment) { color: #64748B; font-style: italic; }
.markdown-body :deep(.hljs-type) { color: #FBBF24; }
.markdown-body :deep(.hljs-meta) { color: #64748B; }
.markdown-body :deep(.hljs-tag) { color: #38BDF8; }
.markdown-body :deep(.hljs-name) { color: #F472B6; }
.markdown-body :deep(.hljs-selector-class) { color: #34D399; }
.markdown-body :deep(.hljs-selector-id) { color: #FBBF24; }
.markdown-body :deep(.hljs-variable) { color: #F472B6; }
.markdown-body :deep(.hljs-params) { color: #E2E8F0; }
.markdown-body :deep(.hljs-symbol) { color: #FBBF24; }
.markdown-body :deep(.hljs-section) { color: #38BDF8; }
.markdown-body :deep(.hljs-link) { color: var(--accent-teal); }
.markdown-body :deep(.hljs-deletion) { color: #F87171; background: rgba(248, 113, 113, 0.08); }
.markdown-body :deep(.hljs-addition) { color: #34D399; background: rgba(52, 211, 153, 0.08); }

/* ─── 首条内容上边距归零（适配气泡内的 padding 定位） ─── */
.markdown-body > :first-child {
  margin-top: 0;
}

/* ─── 内容进场微动效 ─── */
.markdown-body {
  animation: md-fade-in 0.2s ease both;
}
@keyframes md-fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}
</style>
