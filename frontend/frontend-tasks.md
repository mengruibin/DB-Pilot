# 前端开发任务清单

> 依据：[api-contract.md](../docs/api-contract.md)、[frontend/AGENTS.md](../frontend/AGENTS.md)  
> 版本：v0.1 · 日期：2026-06-30  
> 任务状态：`计划中` → `开发中` → `已完成` → `已验收`

---

## F-01：前端项目脚手架与路由骨架

| 字段 | 内容 |
|------|------|
| **ID** | F-01 |
| **标题** | 前端项目初始化 + 三页面路由 + AppLayout 骨架 + TypeScript 类型定义 |
| **关联契约** | api-contract §三 页面路由映射（3 条路由）；frontend AGENTS.md §待确认技术决策清单 D-1/D-2/D-5/D-6 |
| **输入** | api-contract §三 路由表、PRD §7 frontend 目录结构 |
| **输出物** | `frontend/package.json`、`frontend/vite.config.ts`、`frontend/tsconfig.json`、`frontend/index.html`、`frontend/src/main.ts`、`frontend/src/App.vue`、`frontend/src/router.ts`、`frontend/src/components/common/AppLayout.vue`、`frontend/src/components/common/Sidebar.vue`、`frontend/src/views/ChatView.vue`（空白壳）、`frontend/src/views/ConnectionView.vue`（空白壳）、`frontend/src/views/ReportView.vue`（空白壳）、`frontend/src/types/*.ts` |
| **验收标准** | 1. `npm install && npm run dev` 启动成功，浏览器访问 `localhost:5173` 可见空白 AppLayout<br>2. 路由：`/` → ChatView、`/connections` → ConnectionView、`/reports` → ReportView<br>3. Sidebar 常驻左侧，含三个导航项（对话/连接/报告），点击切换路由<br>4. TypeScript 类型文件 `types/chat.ts`/`connection.ts`/`report.ts` 含 api-contract §二 全部实体类型定义，字段名与后端 Pydantic schema 完全一致<br>5. `tsconfig.json` 开启 `strict: true` |
| **前置依赖** | 无 |
| **继承 TODO** | frontend AGENTS.md D-1（框架待定——当前按 Vue 3 默认实现）、D-2（UI 库待定——当前按 Naive UI 默认实现）、D-5（状态管理待定——当前按 Pinia 默认实现）、D-7（SSE HTTP 方法——当前按 POST + fetch 实现） |
| **状态** | 已完成 |

---

## F-02：连接管理 Store + API 封装

| 字段 | 内容 |
|------|------|
| **ID** | F-02 |
| **标题** | connectionStore（Pinia）+ connections API 封装 + localStorage 持久化 |
| **关联契约** | api-contract §三 Store 划分（connectionStore）、§2.1 ConnectionConfig（password 不入 store） |
| **输入** | api-contract §1.1 连接管理 API（7 个端点） |
| **输出物** | `frontend/src/stores/connection.ts`、`frontend/src/api/client.ts`、`frontend/src/api/connection.ts` |
| **验收标准** | 1. `connectionStore.connections` 从 `GET /api/connections` 加载，结果写入 Pinia state<br>2. `connectionStore.activeId` 变更时触发连接测试（POST /api/connections/{id}/test）<br>3. store 内的 `connections` 数组不包含 `password` 字段（API 不返回）<br>4. 页面刷新后从 localStorage 恢复 `connections`，但不恢复 `activeId`——需重新测试连接<br>5. `client.ts` 封装 fetch/axios，自动注入 `X-Request-ID`（UUID v4），统一处理 4xx/5xx 解析 `user_message`<br>6. `connectionStore.status` 映射：`unknown="unknown"`/`healthy="healthy"`/`unreachable="unreachable"`/`degraded="degraded"` |
| **前置依赖** | F-01 |
| **继承 TODO** | api-contract T-1（响应信封——当前按方案 C 直接数据体解析） |
| **状态** | 已完成 |

---

## F-03：连接管理页面——ConnectionList + ConnectionForm

| 字段 | 内容 |
|------|------|
| **ID** | F-03 |
| **标题** | /connections 页面：连接列表 + 新建/编辑表单 + 密码字段安全 |
| **关联契约** | api-contract §1.1 POST/PUT /api/connections；frontend AGENTS.md §2 连接凭据展示 |
| **输入** | F-02 connectionStore |
| **输出物** | `frontend/src/components/connection/ConnectionList.vue`、`frontend/src/components/connection/ConnectionForm.vue`、`frontend/src/composables/useConnection.ts` |
| **验收标准** | 1. ConnectionList 渲染已保存连接卡片（name/db_type/host:port/database + 🟢🟡🔴 状态灯），空列表展示引导文案<br>2. 卡片点击将 `connectionStore.activeId` 设为此连接 ID<br>3. ConnectionForm 含字段：name（文本框）/ db_type（下拉 mysql\|postgresql\|oracle）/ host / port（自动默认值）/ database / user / password（type=password，autocomplete="off"） / SSL 折叠区<br>4. 密码输入框不自动填充（`autocomplete="new-password"`）<br>5. 编辑已有连接时密码字段始终显示 `••••••••` 占位符，用户不手动修改则不更新密码<br>6. 提交前校验：`name` 非空 1-64 字符；`host` 非空；`port` 1-65535；`db_type` 枚举三者之一<br>7. 创建成功后跳转到连接列表<br>8. 测试按钮调用 `POST /api/connections/{id}/test`，成功显示延迟+版本+capabilities，失败显示 `user_message` 不含密码 |
| **前置依赖** | F-02 |
| **继承 TODO** | api-contract §1.1 TODO（是否支持 Unix socket——表单当前仅 TCP 输入） |
| **状态** | 已完成 |

---

## F-04：全局连接状态指示器

| 字段 | 内容 |
|------|------|
| **ID** | F-04 |
| **标题** | ConnectionIndicator 组件——顶栏常驻三态指示灯 |
| **关联契约** | frontend AGENTS.md §3 连接状态指示（🟢🟡🔴 三态，1s 切换） |
| **输入** | F-02 connectionStore.status |
| **输出物** | `frontend/src/components/common/ConnectionIndicator.vue` |
| **验收标准** | 1. `connectionStore.activeId == null` → 显示灰色 "未连接" 状态<br>2. `status="healthy"` → 🟢 绿色圆点 + `host:port/database` + 悬停 Tooltip 显示最后测试时间<br>3. `status="connecting"` → 🟡 黄色圆点脉冲动画 + "连接中..."<br>4. `status="unreachable"` → 🔴 红色圆点 + "连接断开" + 「重连」按钮<br>5. 状态变化后 UI 在 1 秒内完成切换（`transition: all 0.3s ease`）<br>6. 意外断开（status 从 healthy 变为 unreachable 且非用户主动断开）→ 弹出 Toast 通知持续 5s 自动收起 |
| **前置依赖** | F-02 |
| **继承 TODO** | 无 |
| **状态** | 已完成 |

---

## F-05：SSE 流式客户端

| 字段 | 内容 |
|------|------|
| **ID** | F-05 |
| **标题** | useSSE composable——POST ReadableStream SSE 解析 + 7 种事件类型分发 |
| **关联契约** | api-contract §1.2 SSE 事件类型契约表（7 种 type）；frontend AGENTS.md §1 连接中断处理 |
| **输入** | api-contract §1.2 POST /api/chat/stream |
| **输出物** | `frontend/src/composables/useSSE.ts` |
| **验收标准** | 1. `connect(url, body)` 发起 POST 请求（`method: 'POST', headers: {'Accept': 'text/event-stream'}`），读取 `response.body.getReader()`<br>2. 正确解析 `event: message\ndata: {json}\n\n` 格式，逐条回调 `onEvent(type, payload)`<br>3. 7 种事件类型各有独立回调注册：`onThinking`/`onToolCall`/`onToolResult`/`onSql`/`onResult`/`onError`/`onDone`<br>4. 网络中断时触发 `onDisconnect(reason)` 回调，保留已接收内容<br>5. `onError` 事件触发后自动忽略后续事件——SSE 流中以第一个 error 为准<br>6. 120s 无消息触发超时回调 `onTimeout()`<br>7. `disconnect()` 方法主动关闭连接（调用 `reader.cancel()`）<br>8. 未注册处理分支的 type 打印 console.warn 不崩溃 |
| **前置依赖** | F-01（依赖 TypeScript 类型） |
| **继承 TODO** | api-contract T-2（当前 POST + fetch 实现） |
| **状态** | 已完成 |

---

## F-06：Chat Store + 对话状态管理

| 字段 | 内容 |
|------|------|
| **ID** | F-06 |
| **标题** | chatStore（Pinia）——会话管理、消息列表、流式状态、输入模式 |
| **关联契约** | api-contract §三 Store 划分（chatStore）、§2.2 Session/Message 实体 |
| **输入** | F-05 useSSE |
| **输出物** | `frontend/src/stores/chat.ts`、`frontend/src/api/chat.ts` |
| **验收标准** | 1. `chatStore.messages` 为消息数组，每条符合 api-contract §2.2 Message 结构<br>2. `sendMessage(connectionId, message, mode)` 调用 POST /api/chat/stream<br>3. SSE `thinking` → 追加 thinking 类型消息（`role="assistant"`, `content=thinking.text`）<br>4. SSE `tool_call` → 追加 tool_call 类型消息（含 tool/display 展示）<br>5. SSE `tool_result` → 更新对应 tool_call 消息的 status=done + duration_ms<br>6. SSE `sql` → 追加 sql 类型消息（含 sql_content/audit_status/is_readonly）<br>7. SSE `result` → 追加 result 类型消息（含 summary/data_preview/duration_ms）<br>8. SSE `error` → 追加 error 类型消息<br>9. SSE `done` → `isStreaming=false`，更新 `currentSession.id` 和 `tokens_used`<br>10. `isStreaming=true` 期间 InputArea 显示「停止」按钮<br>11. `inputMode` 在 'natural_language'/'sql_editor' 间切换，持久化到 localStorage |
| **前置依赖** | F-05 |
| **继承 TODO** | 无 |
| **状态** | 已完成 |

---

## F-07：ChatPanel + MessageList + MessageBubble 组件

| 字段 | 内容 |
|------|------|
| **ID** | F-07 |
| **标题** | 对话面板主组件——消息列表虚拟滚动 + 消息气泡分发 |
| **关联契约** | api-contract §三 ChatView 组件树；frontend AGENTS.md §3 SSE 流式进度指示 |
| **输入** | F-06 chatStore |
| **输出物** | `frontend/src/components/chat/ChatPanel.vue`、`frontend/src/components/chat/MessageList.vue`、`frontend/src/components/chat/MessageBubble.vue` |
| **验收标准** | 1. MessageList 使用虚拟滚动（消息超过 50 条时仅渲染可视区域 ± 5 条）<br>2. MessageBubble 根据 `message.type` 分发：`natural_language`→纯文本气泡 / `sql`→SqlBlock / `result`→ResultTable / `thinking`→可折叠推理区 / `tool_call`→步骤卡片 / `error`→ErrorCard<br>3. thinking 内容默认折叠，显示"正在思考..."标题 + 实时耗时计数器（毫秒递增）<br>4. tool_call 步骤以列表形式展示，已完成标记 ✅ + 耗时，进行中显示加载动画<br>5. SSE 流进行中时，最后一条 assistant 消息显示打字光标闪烁<br>6. 新消息到来时自动滚动到底部（若用户未手动向上滚动超过 200px） |
| **前置依赖** | F-06（chatStore）、F-08（SqlBlock）、F-09（ResultTable）、F-12（ErrorCard）——组件级依赖可通过 stub 先降级 |
| **继承 TODO** | 无 |
| **状态** | 已完成 |

---

## F-08：SqlBlock 组件——SQL 语法高亮 + 安全警示

| 字段 | 内容 |
|------|------|
| **ID** | F-08 |
| **标题** | SQL 代码块组件：语法高亮 + 复制 + 只读/危险标记 + 写操作确认入口 |
| **关联契约** | api-contract §1.2 SSE `sql` 事件；frontend AGENTS.md §1（危险操作确认）、§4（危险关键字红线标注） |
| **输入** | api-contract §1.2 sql 事件 payload |
| **输出物** | `frontend/src/components/sql/SqlBlock.vue` |
| **验收标准** | 1. SQL 文本以语法高亮渲染（关键字蓝色、字符串绿色、数字橙色、注释灰色），使用 Monaco Editor 只读 diff 视图或 Prism.js SQL grammar<br>2. `is_readonly: false` 时 SQL 块顶部显示 🟡 "此操作将修改数据" 警告条<br>3. `audit_status: "rejected"` 时 SQL 块红色边框 + "SQL 审计未通过：<原因>"<br>4. 复制按钮：点击复制 SQL 全文到剪贴板<br>5. 若 SQL 含存在凭据字面量模式（如 `IDENTIFIED BY 'xxx'`），渲染时已将凭据部分替换为 `'***'` 展示（依据 frontend AGENTS.md §2）<br>6. 危险关键字（DROP/DELETE/TRUNCATE/ALTER/UPDATE）以红色波浪线标注（视觉警告 ≠ 自动阻止） |
| **前置依赖** | F-01 |
| **继承 TODO** | frontend AGENTS.md D-3（代码编辑器选型——当前使用 Prism.js 轻量方案，Monaco 后续懒加载引入） |
| **状态** | 已完成 |

---

## F-09：ResultTable 组件——敏感数据掩码 + 分页

| 字段 | 内容 |
|------|------|
| **ID** | F-09 |
| **标题** | 查询结果表格组件：虚拟滚动 + 敏感列自动掩码 + 👁 单行查看 + 复制限制 |
| **关联契约** | api-contract §2.3 QueryResult（is_sensitive 标记）；frontend AGENTS.md §2 敏感数据展示规范（脱敏/👁/复制限制） |
| **输入** | api-contract §2.3 QueryResult |
| **输出物** | `frontend/src/components/sql/ResultTable.vue`、`frontend/src/composables/useSensitiveData.ts` |
| **验收标准** | 1. 表格列头渲染：`is_sensitive: true` 的列名旁显示 🔒 图标<br>2. 所有 `is_sensitive: true` 列的值已由后端替换为 `***`，前端额外检测列名匹配 `/password\|passwd\|pwd\|secret\|token\|api_key\|phone\|mobile\|email\|id_card\|ssn/i` 的列也作掩码<br>3. 每行掩码列旁显示 👁 图标，点击单行临时显示明文，5 秒后自动恢复 `***`<br>4. 用户选中含掩码列单元格 Ctrl+C 复制时，剪贴板中该列内容仍为 `***`（监听 copy 事件，判断选区是否跨敏感列）<br>5. 不存在"全部显示明文"一键操作<br>6. 表格支持列头点击排序（升序/降序/无）<br>7. 超过 100 行时分页加载（`GET /api/chat/result/{session_id}?page=N`） |
| **前置依赖** | F-01 |
| **继承 TODO** | api-contract T-4（敏感列判定策略——当前列名正则匹配 + 后端 is_sensitive 标记双重判定） |
| **状态** | 已完成 |

---

## F-10：InputArea 组件——自然语言/SQL 双模式输入

| 字段 | 内容 |
|------|------|
| **ID** | F-10 |
| **标题** | 输入区域组件：模式切换 + 提交/取消 + 连接标识 + 快捷键 |
| **关联契约** | frontend AGENTS.md §4 输入区行为规范（Shift+Enter 换行/Ctrl+Enter 提交/粘贴多行确认/不自动执行） |
| **输入** | F-06 chatStore |
| **输出物** | `frontend/src/components/chat/InputArea.vue` |
| **验收标准** | 1. 模式切换 Toggle：自然语言（默认）/ SQL 编辑器，切换时不清空已输入内容<br>2. 自然语言模式：单行 textarea + 自动高度（1-5 行），Enter 提交<br>3. SQL 编辑器模式：代码编辑区（语法高亮 + 括号匹配 + 缩进引导），Shift+Enter 换行，Ctrl+Enter 提交<br>4. 粘贴多行 SQL（> 1 行）弹出确认："检测到 N 条 SQL 语句，确认全部执行？"——用户确认后才提交<br>5. 不提供"自动执行"功能——所有提交必须显式点击「发送」或按 Ctrl+Enter<br>6. 输入区底部显示当前连接标识：数据库类型图标 + `host:port/database`（若未连接则灰色提示"请先连接数据库"）<br>7. `isStreaming=true` 时「发送」按钮变为「停止」按钮（红色），点击调用 `POST /api/chat/cancel` |
| **前置依赖** | F-06（chatStore.isStreaming）、F-02（connectionStore.activeId） |
| **继承 TODO** | frontend AGENTS.md §4 TODO：键盘快捷键最终映射（当前实现 Ctrl+Enter 提交 / Shift+Enter 换行 / Escape 取消） |
| **状态** | 已完成 |

---

## F-11：ThinkingIndicator 组件

| 字段 | 内容 |
|------|------|
| **ID** | F-11 |
| **标题** | Agent 思考中动画 + 实时耗时计数器 |
| **关联契约** | frontend AGENTS.md §3（Agent 思考阶段展示动画 + 毫秒递增耗时） |
| **输入** | F-06 chatStore.isStreaming |
| **输出物** | `frontend/src/components/chat/ThinkingIndicator.vue` |
| **验收标准** | 1. `isStreaming=true` 且无有效结果时展示动画：三点脉冲动画 + "AI 正在分析..."<br>2. 实时耗时计数器（毫秒递增）可见，格式 `X.Xs`，超过 10s 变为橙色，超过 30s 变为红色 + "查询超时警告"<br>3. 收到第一个 `sql` 或 `result` 事件后消失，切换为内容展示 |
| **前置依赖** | F-06 |
| **继承 TODO** | 无 |
| **状态** | 已完成 |

---

## F-12：ErrorCard 组件——分级错误提示

| 字段 | 内容 |
|------|------|
| **ID** | F-12 |
| **标题** | 错误卡片组件：ℹ️/⚠️/🛑 三级 + 用户误操作 vs 系统故障区分 |
| **关联契约** | frontend AGENTS.md §3 错误分级提示（Info/Warning/Error 三级）；api-contract §1.6 错误码体系 |
| **输入** | api-contract §1.6 错误码表 |
| **输出物** | `frontend/src/components/common/ErrorCard.vue` |
| **验收标准** | 1. `severity="info"` → ℹ️ 蓝色卡片 "提示"（非阻塞信息）<br>2. `severity="warning"` → ⚠️ 黄色卡片 "警告"（用户可自行处理）+ 操作建议<br>3. `severity="error"` → 🛑 红色卡片 "错误"（需系统/DBA 介入）<br>4. 错误类型 `SQL_AUDIT_BLOCKED`/`INVALID_PARAM` → 显示"用户可修正"标签 + 修正指引<br>5. 错误类型 `AGENT_ERROR`/`DB_UNREACHABLE`/`EXECUTION_TIMEOUT` → 显示"系统故障"标签 + 排查步骤 + 重试按钮<br>6. 不使用 `alert()` 弹窗<br>7. 错误卡片可关闭 |
| **前置依赖** | F-01 |
| **继承 TODO** | 无 |
| **状态** | 已完成 |

---

## F-13：ConfirmDialog 组件——危险操作二次确认

| 字段 | 内容 |
|------|------|
| **ID** | F-13 |
| **标题** | 确认对话框组件：写操作二次确认 + 1.5s 悬停冷却 + SQL 展示 + 影响范围说明 |
| **关联契约** | frontend AGENTS.md §1 危险操作确认机制（三要素：SQL 全文 + 预估影响 + 危险色按钮 1.5s 冷却） |
| **输入** | api-contract §1.5 `suggestion_is_destructive: true`、api-contract §2.4 DiagnosisResult `is_destructive: true` |
| **输出物** | `frontend/src/components/common/ConfirmDialog.vue` |
| **验收标准** | 1. 对话框包含三要素：(a) SQL 语句语法高亮全文 (b) 预估影响行数（后端提供则展示，不可用则"影响范围未知——请人工评估"）(c)「取消」+「确认执行」按钮<br>2. 「确认执行」按钮使用危险色（#D32F2F 红色），初始 disabled 1.5 秒 + 倒计时 "确认 (1.5s)"<br>3. 倒计时结束前不可点击<br>4. 不提供"不再提示"或"跳过确认"复选框<br>5. 对话框外点击不关闭（`close-on-overlay-click: false`）<br>6. Escape 键等同于「取消」<br>7. 确认后执行回调，执行中按钮显示 loading 状态<br>8. 异常时在对话框内以 ErrorCard 展示错误 |
| **前置依赖** | F-12（ErrorCard） |
| **继承 TODO** | api-contract T-10（影响行数预估——当前方案：后端提供就用，不可用就展示"未知"） |
| **状态** | 已完成 |

---

## F-14：SQL 编辑器自动补全——基于元数据

| 字段 | 内容 |
|------|------|
| **ID** | F-14 |
| **标题** | SQL 编辑器元数据自动补全 + 补全缓存 + 补全边界约束 |
| **关联契约** | frontend AGENTS.md §4 语法辅助边界（补全仅基于元数据不缓存结果） |
| **输入** | api-contract §1.1 GET /api/connections/{id}/metadata（5 分钟缓存） |
| **输出物** | `frontend/src/composables/useSqlCompletion.ts` |
| **验收标准** | 1. 编辑器切换为 SQL 模式时，自动请求 `GET /api/connections/{id}/metadata`<br>2. 返回的 `tables[].table_name` + `columns[].name` 作为补全候选项，缓存 5 分钟（`Cache-Control: max-age=300` 兜底）<br>3. 补全触发：输入 `.` 后提示列名；输入 `FROM`/`JOIN` 后提示表名；输入 `WHERE` 后提示列名<br>4. 补全列表中不包含：(a) 历史 SQL 查询中出现的实际数据值 (b) 未在元数据中声明的隐藏列<br>5. 补全候选项包含元数据类型标注（如 `username: varchar(64)`）<br>6. 连接切换时清除缓存重新拉取元数据 |
| **前置依赖** | F-02（connectionStore.activeId）、F-10（InputArea SQL 模式） |
| **继承 TODO** | 无 |
| **状态** | 计划中 |

---

## F-15：历史命令管理——加密 localStorage 存储

| 字段 | 内容 |
|------|------|
| **ID** | F-15 |
| **标题** | 历史记录：AES-256-GCM 加密存储 + FIFO 100 条 + 搜索 + 清空 |
| **关联契约** | frontend AGENTS.md §4 历史命令管理（localStorage + AES-256-GCM + 100 条上限 + 不含结果数据） |
| **输入** | F-06 chatStore.messages |
| **输出物** | `frontend/src/composables/useHistory.ts` |
| **验收标准** | 1. 每次用户提交消息（自然语言/SQL）时，追加历史条目：`{text, mode, timestamp, status}`——不包含 SQL 查询结果数据<br>2. 加密：`AES-256-GCM`（使用 Web Crypto API `crypto.subtle.encrypt`），密钥从 `crypto.subtle.deriveKey` 派生（基于当前浏览器会话随机盐）<br>3. 浏览器关闭后密钥丢失 → 历史不可读（安全性目标）<br>4. 历史上限 100 条，超出 FIFO 淘汰最旧条目<br>5. 历史面板支持搜索（关键词匹配 text 字段），不支持时间范围查询<br>6. 历史列表每项显示：输入文本截断（前 80 字符）+ 时间戳 + 状态图标（✅成功/❌失败）<br>7. 「清空历史」按钮 → 二次确认 → 清除 localStorage 对应 key |
| **前置依赖** | F-01 |
| **继承 TODO** | frontend AGENTS.md §4 TODO：是否需服务端历史同步——当前仅本地存储 |
| **状态** | 计划中 |

---

## F-16：诊断结果卡片组件

| 字段 | 内容 |
|------|------|
| **ID** | F-16 |
| **标题** | DiagnosisCard 组件——诊断发现列表 + 建议卡片 + 执行按钮 |
| **关联契约** | api-contract §2.4 DiagnosisResult；frontend AGENTS.md §1（建议卡片带执行按钮 + 写操作确认） |
| **输入** | api-contract §2.4 DiagnosisResult |
| **输出物** | `frontend/src/components/chat/DiagnosisCard.vue` |
| **验收标准** | 1. 渲染 `findings[]` 列表，每项含：severity 图标（🔴🟡🔵）+ title + detail + suggestion<br>2. `is_destructive: true` 的 suggestion 以"建议卡片"形式展示，附带「执行此建议」按钮——点击后弹出 ConfirmDialog<br>3. `is_destructive: false` 的 suggestion 仅展示为可复制代码块<br>4. suggestion 中的 SQL 代码（如 `CREATE INDEX ...`）语法高亮<br>5. `estimated_improvement` 用绿色文字展示预期改善效果<br>6. severity=error 的 finding 排序在前 |
| **前置依赖** | F-13（ConfirmDialog）、F-08（SqlBlock 复用高亮逻辑） |
| **继承 TODO** | 无 |
| **状态** | 计划中 |

---

## F-17：故障排查步骤组件

| 字段 | 内容 |
|------|------|
| **ID** | F-17 |
| **标题** | TroubleshootSteps 组件——排查步骤进度 + 结论 + 破坏性建议确认 |
| **关联契约** | api-contract §2.6 TroubleshootResult、§1.5 SSE troubleshoot 事件；frontend AGENTS.md §1（KILL 等破坏性诊断建议需确认按钮） |
| **输入** | api-contract §1.5 SSE 排查流 |
| **输出物** | `frontend/src/components/chat/TroubleshootSteps.vue` |
| **验收标准** | 1. 排查步骤以编号列表展示，每步显示：顺序号 + tool 名称 + display 文本 + status 图标（✅ 通过 / ⚠️ 警告 / ❌ 失败 / ⏭️ 跳过 / ⏳ 进行中）<br>2. 每个步骤展开可查看 detail 数据（JSON 格式化展示）<br>3. 结论区：severity 背景色 + conclusion 文本<br>4. `suggestion_is_destructive: true` 时 suggestion 渲染为红色按钮「执行建议（危险操作）」，点击弹出 ConfirmDialog<br>5. `suggestion_is_destructive: false` 时正常展示<br>6. 排查结束后播放轻微动画（步骤列表依次亮起） |
| **前置依赖** | F-13（ConfirmDialog） |
| **继承 TODO** | 无 |
| **状态** | 计划中 |

---

## F-18：健康巡检页面——实时进度 + 报告展示

| 字段 | 内容 |
|------|------|
| **ID** | F-18 |
| **标题** | ReportView 完整实现：巡检触发 → 实时进度 SSE → 评分环形图 → 分类检查项 → 导出 |
| **关联契约** | api-contract §1.4 health-check SSE + §2.5 HealthReport；frontend AGENTS.md §1（巡检进度逐项更新） |
| **输入** | F-05 useSSE（health-check SSE 流） |
| **输出物** | `frontend/src/views/ReportView.vue`、`frontend/src/components/report/HealthScore.vue`、`frontend/src/components/report/CheckItemList.vue`、`frontend/src/components/report/MetricChart.vue`、`frontend/src/stores/report.ts` |
| **验收标准** | 1. 页面顶部：「开始巡检」按钮 + 历史报告列表<br>2. 点击「开始巡检」→ SSE 连接 `POST /api/connections/{id}/health-check`<br>3. 实时进度条："第 7/20 项——慢查询占比"，进度百分比实时更新<br>4. HealthScore 组件：环形图（SVG/CSS），0-59 红色 / 60-79 黄色 / 80-100 绿色，中央数字动画过渡（count-up 效果）<br>5. CheckItemList：按 category 分组（连接/存储/性能/复制/锁/备份/安全/表/日志/配置），每组一张卡片，items 以列表展示（pass 绿色勾 / warning 黄色叹号 / error 红色叉）<br>6. 每项展开可查看 value + threshold + suggestion<br>7. 含 `is_destructive: true` 的 suggestion 渲染带「执行」按钮 → ConfirmDialog<br>8. 巡检完成后「导出」按钮可用：HTML / PDF 两种格式<br>9. score < 60 的报告在列表中红底标记 |
| **前置依赖** | F-05、F-13 |
| **继承 TODO** | api-contract T-9（导出格式——当前 HTML 前端渲染 + 浏览器打印，PDF 后续由后端生成） |
| **状态** | 计划中 |

---

## F-19：结果导出功能——CSV/Excel 含敏感字段限制

| 字段 | 内容 |
|------|------|
| **ID** | F-19 |
| **标题** | 查询结果导出（CSV/Excel）含敏感列默认排除 + 导出水印 |
| **关联契约** | frontend AGENTS.md §2 复制与导出限制（敏感列默认不导出、导出水印） |
| **输入** | F-09 ResultTable |
| **输出物** | `frontend/src/composables/useExport.ts` |
| **验收标准** | 1. ResultTable 操作栏「导出」按钮 → 下拉菜单（CSV / Excel）<br>2. 导出对话框展示列清单：敏感列默认未勾选（置灰 + 🔒 图标），用户手动勾选后该列包含在导出中<br>3. 勾选敏感列后导出文件表头追加 `[包含敏感字段]` 标记（CSV 首行注释 / Excel 首行合并单元格）<br>4. 导出文件首行/页脚含水印：`Generated by DB-Pilot | <ISO 8601 timestamp> UTC`<br>5. CSV 导出用前端纯实现（`Blob` + URL download），不依赖后端<br>6. Excel 导出用 `xlsx` 库（或类似轻量方案）生成 `.xlsx` blob 下载 |
| **前置依赖** | F-09 |
| **继承 TODO** | frontend AGENTS.md §2 TODO（水印 User 标识——当前仅 timestamp，T-3 认证方案确定后追加入用户标识） |
| **状态** | 计划中 |

---

## F-20：会话超时倒计时通知

| 字段 | 内容 |
|------|------|
| **ID** | F-20 |
| **标题** | 空闲超时倒计时通知 + 自动断开 + 重连校验 |
| **关联契约** | frontend AGENTS.md §1 会话超时与空闲策略（30min 超时/2min 倒计时通知/操作重置计时器/恢复后重测连接） |
| **输入** | F-06 chatStore |
| **输出物** | `frontend/src/composables/useIdleTimeout.ts` |
| **验收标准** | 1. 用户无操作（鼠标移动除外）30 分钟后触发超时：断开 SSE + 清除内存连接凭据<br>2. 超时前 2 分钟展示非阻断式倒计时通知："会话将在 1:59 后超时，点击任意位置保持活跃"<br>3. 用户任一操作（点击/键盘/触屏）重置计时器<br>4. 超时后 `connectionStore.status` 重置为 `unknown`<br>5. 用户发送下一条消息时自动调用 `POST /api/connections/{id}/test` 重新测试连接有效性 |
| **前置依赖** | F-02（connectionStore）、F-06（chatStore） |
| **继承 TODO** | 无 |
| **状态** | 计划中 |

---

## F-21：前端集成测试——SSE 流模拟 + 组件行为验证

| 字段 | 内容 |
|------|------|
| **ID** | F-21 |
| **标题** | 前端关键交互行为自动化测试（Vitest + 组件测试） |
| **关联契约** | frontend AGENTS.md 各章节强制行为指令（13 条 MUST 验证） |
| **输入** | F-03 ~ F-20 所有组件 |
| **输出物** | `frontend/tests/components/` 下对应测试文件 |
| **验收标准** | 1. `test_confirm_dialog_cooldown()`：确认按钮初始 disabled，1.5s 后可点击<br>2. `test_result_table_mask()`：敏感列渲染为 `***`，点击 👁 后 5s 显示明文，5s 后恢复掩码<br>3. `test_input_paste_multiline_sql()`：粘贴 2 行 SQL 弹确认框，确认后才提交<br>4. `test_connection_indicator_1s_switch()`：status 变化后 UI 在 1s 内完成切换<br>5. `test_sql_block_danger_highlight()`：含 DROP/DELETE 的 SQL 红色波浪线标注<br>6. `test_history_encrypt_decrypt()`：写入历史 → localStorage 中为加密数据（非明文）→ 同 session 内读取成功 → 模拟新 session 读取失败<br>7. `test_confirm_dialog_no_skip_checkbox()`：确认对话框中不存在"不再提示"复选框<br>8. `test_error_card_no_alert()`：ErrorCard 不使用 window.alert()<br>9. 所有测试通过 `npx vitest run` 零失败 |
| **前置依赖** | F-13、F-09、F-10、F-04、F-08、F-15、F-12 |
| **继承 TODO** | 无 |
| **状态** | 计划中 |

---

## 任务依赖拓扑图

```
F-01 ────────────────────────────────────────────────┐
  ├── F-02 ──┬── F-03 ─────────────────────────────┤
  │          ├── F-04 ─────────────────────────────┤
  │          ├── F-10 ──────────────────────────────┤
  │          └── F-18 ───┐                          │
  ├── F-05 ──┬── F-06 ──┬── F-07 ─────────────────┤
  │          │          ├── F-11 ──────────────────┤
  │          │          ├── F-20 ──────────────────┤
  │          │          └── F-17（SSE 流事件）─────┤
  │          └── F-18 ──────────────────────────────┤
  ├── F-08 ────────────────────────────────────────┤
  ├── F-09 ──┬── F-19 ────────────────────────────┤
  ├── F-10 ──┬── F-14 ────────────────────────────┤
  ├── F-12 ────────────────────────────────────────┤
  ├── F-13 ──┬── F-16 ────────────────────────────┤
  │          ├── F-17 ────────────────────────────┤
  │          └── F-18 ────────────────────────────┤
  ├── F-15 ────────────────────────────────────────┤
  └── F-21 ────────────────────────────────────────┘
```

图例：F-01 为基础层 → F-02→F-04 为连接管理层 → F-05→F-07 为对话基础设施 → F-08→F-14 为安全交互组件层 → F-15→F-20 为功能集成层 → F-21 为测试验证层。

---

## 任务状态汇总

| 状态 | 后端任务 | 前端任务 | 合计 |
|------|---------|---------|------|
| 计划中 | 28 | 8 | 36 |
| 开发中 | 0 | 0 | 0 |
| 已完成 | 0 | 13 | 13 |
| 已验收 | 0 | 0 | 0 |

### 后端按 Phase 分组

| Phase | 任务 |
|-------|------|
| Phase 1（脚手架+基础对话） | B-01, B-02, B-03, B-04, B-05, B-08, B-09, B-10, B-11, B-12, B-13, B-17, B-19, B-26 |
| Phase 2（NL2SQL+安全审计） | B-20, B-21, B-27 |
| Phase 3（诊断引擎） | B-14, B-18 |
| Phase 4（巡检+排障） | B-06, B-07, B-15, B-16, B-22, B-23, B-24 |
| Phase 5（打磨部署） | B-25, B-28 |

### 前端按 Phase 分组

| Phase | 任务 |
|-------|------|
| Phase 1（脚手架+基础对话） | F-01, F-02, F-03, F-04, F-05, F-06, F-07, F-10, F-11, F-12, F-20 |
| Phase 2（NL2SQL+安全审计） | F-08, F-09, F-13, F-14, F-15 |
| Phase 3（诊断引擎） | F-16 |
| Phase 4（巡检+排障） | F-17, F-18 |
| Phase 5（打磨部署） | F-19, F-21 |
