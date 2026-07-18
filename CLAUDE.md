# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DB-Pilot 是一个基于大语言模型（LLM）的 **AI 数据库运维助手**，使用自然语言交互帮助 DBA 和开发者完成数据查询、性能诊断、故障排查和健康巡检。

## Commands

### Backend (Python 3.12＋, FastAPI, uv)

```bash
# 启动开发服务器（hot-reload）
cd backend && uv run uvicorn app.main:app --reload --port 8000

# 代码检查（Ruff）
ruff check backend/app/
ruff check backend/tests/

# 格式化
ruff format backend/app/ backend/tests/

# 运行测试
pytest backend/tests/ -v

# 运行单个测试
pytest backend/tests/test_sql_auditor.py -v

# 数据库迁移（Alembic）
cd backend && alembic upgrade head
cd backend && alembic revision --autogenerate -m "description"
```

### Frontend (Vue 3 + TypeScript + Vite)

```bash
# 开发服务器（端口 5173，代理 /api 到 :8000）
cd frontend && npm run dev

# 生产构建
npm run build

# 运行测试
npm test
npm run test:watch

# 类型检查
npx vue-tsc --noEmit
```

### Environment

```bash
# 后端 .env 模板见 backend/.env.example
# 关键配置项：
#   LLM_API_KEY, LLM_MODEL, LLM_PROVIDER (anthropic|openai)
#   ENABLE_REASONING=true (启用模型深度思考模式)
#   LLM_API_URL (OpenAI 兼容接口地址)
#   DATABASE_URL (内部库), CORS_ORIGINS
```

## Codebase Architecture

### High-Level Flow

```
用户消息 → SSE POST /api/chat/stream
  → (api/chat.py) 解析连接、构建 AgentState (scoped to connection)
  → (agent/graph.py) LangGraph StateGraph (双通道流式):
      agent_node (LLM bind_tools → 决策工具调用或最终回答)
        → tools_node (安全护栏 → 连接注入 → 工具并行执行 → 脱敏，最多 5 个并发) → agent_node (ReAct 循环, ≤10 轮)
        → END (is_complete=True)
  → SSE 流式返回 12+ 种事件类型 (messages/updates 双通道)
      ┌─ messages: reasoning / token / tool_call_chunks (逐 token)
      └─ updates: tool_call / tool_result / sql / error / stage_change / done / confirm_required
```

### Key Directories

```
backend/
├── app/
│   ├── main.py              # FastAPI 应用工厂 + 中间件（CORS/RequestID/访问日志）
│   ├── config.py             # Pydantic Settings（从 .env 加载，启动时校验必填）
│   ├── api/                  # 薄路由层：chat(SSE), connection(CRUD), query, report, troubleshoot
│   ├── agent/                # LangGraph ReAct Agent 引擎
│   │   ├── graph.py          # StateGraph 编排（classify → agent ↔ tools → format）
│   │   ├── state.py          # AgentState TypedDict + Intent 枚举
│   │   ├── models.py         # Chat 模型工厂（ChatAnthropic / ChatOpenAI 统一接口）
│   │   ├── router.py         # IntentRouter：关键词+LLM 两层意图分类
│   │   ├── tool_node.py      # SafeToolNode：安全护栏+连接注入+脱敏，标准 ToolMessage
│   │   ├── safety.py         # 安全护栏链（SQL 审计/只读检查/连接限额）
│   │   └── tools/            # 工具实现（query, diagnosis, health, troubleshoot）
│   ├── db/                   # 数据库适配器（BaseAdapter ABC + mysql/postgresql/oracle）
│   ├── engine/               # 无状态引擎：nl2sql, sql_auditor(sqlglot), diagnosis, health_check, explain_estimator
│   ├── models/               # Pydantic schemas + SQLAlchemy ORM (connection/session/report)
│   └── prompts/              # Prompt 模板（Python 字面量，不含运行时变量）
├── alembic/                  # 数据库迁移（versions/ 目录有 4 个迁移）
└── tests/                    # pytest 测试（conftest + sql_auditor + adapter 集成测试）

frontend/
├── src/
│   ├── views/                # 页面：ChatView, ConnectionView, ReportView
│   ├── components/           # UI 组件：chat/, common/, connection/, report/, sql/
│   ├── stores/               # Pinia stores (chat, connection, report)
│   ├── composables/          # useSSE, useConnection, useHistory, useExport, useIdleTimeout
│   ├── api/                  # API 客户端（client.ts 统一封装 fetch + SSE）
│   └── types/                # TypeScript 类型定义（与 Pydantic schema 一一对应）
├── tests/                    # Vitest 测试
└── vite.config.ts            # Vite 配置 + 代理到 :8000
```

### Architecture Principles

1. **LangGraph ReAct Agent** — LLM 通过 `model.bind_tools()` 决定工具调用顺序，LangGraph 条件边自动路由 `agent ↔ tools` 循环。最多 10 次迭代防无限循环。

2. **SSE 双通道流式** — `astream(stream_mode=["updates", "messages"])` 双通道分离：
   - "messages" 通道：`(AIMessageChunk, metadata)` 逐 token 流，处理 `reasoning_content` / `tool_call_chunks` / `content`
   - "updates" 通道：节点执行完成后的状态 delta，从中提取 `sse_events` 列表和 `is_complete` 标志

3. **推理与回答分离** — 模型原生 `reasoning_content` 字段（DeepSeek/GLM 等支持）→ 作为独立 SSE `reasoning` 事件推送。普通 content 采用"乐观渲染+收编"模式：一律以 `stage="thinking"` 发射，`is_complete` 时若未检测到工具调用则发送 `stage_change("answer")` 触发前端收编。

4. **Security** — 工具执行前经 SafeToolNode：连接配置注入 → 工具查找（读取 `extras` 元数据决定安全检查项）→ 安全护栏链 → 执行 → 结果脱敏。护栏链包括三层：
   - SQLAuditCheck（sqlglot 审计拦截 DROP/ALTER/TRUNCATE 等危险 DDL）
   - PerformanceCheck（静态文本分析 SELECT * / 缺 LIMIT / WHERE 函数，非阻断警告）
   - RowEstimationCheck（EXPLAIN 多维度评估，根据规则引擎阻断大查询或发出警告，失败时降级放行）

5. **并行工具执行** — 当 LLM 在同一轮返回多个 `tool_calls` 时，SafeToolNode 用 `asyncio.gather(return_exceptions=True)` 并发执行它们。总耗时 ≈ 最慢工具而非耗时之和。通过 `asyncio.Semaphore` 限制最大并发数（默认 5），防止 DB 连接池耗尽。前端的 `onToolResult` 匹配从 `findLastRunningToolCall()` 改为 `tool_call_id` 精确匹配，支持并行安全的结果关联。SSE 事件中的 `tool_call` 和 `tool_result` 均携带 `tool_call_id` 字段用于前后端关联。

6. **SSE 事件协议** — 12+ 种事件类型通过 SSE `event: message` + `data: JSON` 传输：`thinking` / `reasoning` / `token(stage=thinking|answer)` / `tool_call`（含 `tool_call_id`） / `tool_result`（含 `tool_call_id`） / `sql` / `result` / `text` / `error` / `done` / `stage_change` / `confirm_required`。前端 `useSSE` composable 统一解析分发到 Pinia store 回调。

7. **Database Adapters** — BaseAdapter ABC 定义统一接口（execute/explain/get_slow_queries/等），lazy-loaded 驱动（aiomysql/asyncpg/oracledb），`AdapterFactory.create()` 创建实例。连接密码仅存于请求内存 state，不持久化。

8. **Observability** — structlog 结构化日志 + X-Request-ID 全链路追踪 + trace_iterations 记录每轮 ReAct 决策轨迹。

9. **EXPLAIN 多维度安全评估** — 在执行 `execute_sql` 前通过 EXPLAIN 获取 5 维标准化指标（访问方式、扫描行数、返回行数、查询成本、额外操作），用硬编码规则引擎（6 条 CRITICAL + 5 条 WARNING）逐条评估。CRITICAL 命中则阻断并告知 LLM 改写，WARNING 仅告警。所有阈值在 `explain_estimator.py` 中硬编码管理，不依赖 .env 配置。EXPLAIN 失败时降级放行，不阻断正常业务。同时执行后对结果自动截断（200 行 / 80K 字符），防止 LLM 上下文窗口溢出。

### Frontend 设计系统

采用分层卡片布局，所有 AI 响应使用统一卡片容器（`ai-response-card`）：

```
用户消息（右对齐气泡）
  → AI 响应卡片（思考面板 + 最终回答）
    ┌─ 思考面板（details/summary 折叠）
    │   ├─ reasoning（深度推理文本）
    │   ├─ tool_call 卡片（蓝色调，spinner/check/clock + 参数 JSON）
    │   │   - running: 旋转图标 + "正在调用工具"
    │   │   - waiting_approval: 时钟图标 + "等待用户审批"（由 pendingConfirm 驱动）
    │   │   - done: 勾选图标 + "已完成调用"
    │   └─ tool_result 折叠块
    └─ 最终回答（绿调，Markdown 渲染 + 打字光标）
```

#### 写操作确认卡片（内联设计）

写操作确认采用**内联卡片**而非模态弹窗，自然嵌入消息流中，不遮挡界面：

```
消息列表
  → ... 工具调用卡片（显示"等待用户审批"）
  → ┌───────────────────────────────────────┐
    │ ✎ 需要确认执行写操作                   │
    │ ┌─ SQL 代码块 ──────────────────────┐  │
    │ │ INSERT INTO users ...              │  │
    │ └────────────────────────────────────┘  │
    │          [取消]   [确认执行 (1.5s)]      │
    └───────────────────────────────────────┘
```

- 深色主题强调色：浅绿 `#4ADE80`
- 浅色主题强调色：浅蓝 `#60A5FA`
- 左侧 3px 强调色边框，与 AI 响应卡片同宽同风格
- 响应式：由 `chatStore.pendingConfirm` 驱动，`isWaitingApproval` computed 自动判断
- 1.5s 冷却按钮防误触，冷却中为描边样式，结束后填充强调色

设计 token 在 `App.vue` CSS 变量中定义，支持 **深色/浅色** 双主题（通过 `[data-theme="light"]` 切换）。核心变量以 `--chat-*` 和 `--confirm-*` 前缀命名。字体：Satoshi（正文）+ JetBrains Mono（代码）。

### Tools Registry

所有 Agent 工具集中注册在 `app/agent/tools/registry.py`，新增工具只需在此注册，无需修改 graph.py：

| 工具 | 功能 |
|------|------|
| `list_tables` | 列出数据库中所有表 |
| `describe_table` | 获取指定表结构 |
| `run_query` | 执行只读 SQL 查询 |
| `get_slow_queries` | 获取慢查询日志 |
| `explain_query` | 分析 SQL 执行计划 |
| `check_connections` | 检查连接池状态 |
| `check_locks` | 检查锁等待 |
| `check_replication` | 检查主从复制状态 |
| `run_health_check` | 执行 20 项健康巡检 |

### Key Conventions

- FastAPI 路由前缀统一为 `/api/`，无版本号
- SSE endpoint: `POST /api/chat/stream`，取消: `POST /api/chat/cancel`
- 连接管理 CRUD: `/api/connections`
- 分页响应含 `total` / `page` / `pageSize` / `items`，默认 20，最大 100
- 所有 `@tool` 函数返回 Python 原生类型（dict/list/str），禁止返回 ORM 实例
- 连接密码仅存于内存 state，不持久化到数据库
- SQL 审计默认拦截 DROP/ALTER/TRUNCATE/CREATE/GRANT/REVOKE，多语句直接拦截
- **EXPLAIN 安全评估**：`execute_sql` 工具声明 `needs_row_estimation: True` 触发 RowEstimationCheck，通过 EXPLAIN 提取多维度指标并用规则引擎评估。阈值硬编码在 `explain_estimator.py`，不依赖 .env
- **结果截断**：所有工具返回结果在序列化为 ToolMessage 前经 `truncate_result_for_llm()` 处理，最大 200 行 / 80K 字符，防止 LLM 上下文窗口溢出。截断时附带 `_truncated`、`_original_total_rows` 元信息
- **并行工具执行**：SafeToolNode 用 `asyncio.gather()` 并发执行同轮 `tool_calls`，受 `AGENT_MAX_CONCURRENT_TOOLS`（默认 5）限制。SSE 事件的 `tool_call` / `tool_result` 均携带 `tool_call_id` 供前端精确匹配
- 前端 `onToolResult` 匹配策略：优先用 `tool_call_id` 从 `Map` O(1) 查找，回退到 `tool` 名匹配。`MessageList.vue` 的 `pairToolSteps()` 将 `tool_call` 与对应 `tool_result` 配对渲染
- **写操作确认 UI**：采用内联卡片非模态弹窗，`WriteConfirmation.vue` 组件根据 `chatStore.pendingConfirm` 渲染。tool_call 卡片通过 `isWaitingApproval` computed 响应式判断是否等待审批，显示时钟图标 + "等待用户审批"（由 `pendingConfirm.writes` 驱动，无需手动同步 `stepStatus`）。深色主题强调色浅绿 `#4ADE80`，浅色主题浅蓝 `#60A5FA`，变量定义在 `App.vue` 的 `--confirm-*` CSS 变量中
