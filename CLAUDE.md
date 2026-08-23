# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DB-Pilot 是一个基于大语言模型（LLM）的 **AI 数据库运维助手**，使用自然语言交互帮助 DBA 和开发者完成数据查询、性能诊断、故障排查和健康巡检。

## Commands

### Backend (Python 3.12＋, FastAPI, uv)

```bash
# 启动开发服务器（hot-reload）
# Windows 必须加 --loop app.main:selector_loop_factory：psycopg 异步模式
# 不兼容 ProactorEventLoop（uvicorn 0.51 在 Windows 硬编码 ProactorEventLoop）
cd backend && uv run uvicorn app.main:app --reload --port 8000 --loop app.main:selector_loop_factory

# 代码检查（Ruff）
ruff check backend/app/
ruff check backend/tests/

# 格式化
ruff format backend/app/ backend/tests/

# 运行测试
pytest backend/tests/ -v

# 运行单个测试
pytest backend/tests/test_sql_auditor.py -v

# Text-to-SQL 业务查询评测（需后端运行 + 数据库连接）
cd backend/tests/evaluation && python gen_golden_dataset.py   # 预构建黄金数据集
cd backend/tests/evaluation && python eval_agent.py           # 执行评测并生成报告

# 运维场景评测（工具调用链验证）
cd backend/tests/evaluation && python eval_ops_agent.py                         # 全量运行
cd backend/tests/evaluation && python eval_ops_agent.py --ids OP-001,OP-006     # 指定用例
cd backend/tests/evaluation && python eval_ops_agent.py --cases 10              # 前10条

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
        → secure_tools_node (三阶段安全流水线：PRE_CONFIRM 并行审计 → CONFIRM 批量 interrupt 确认 → PRE_EXECUTE + 并行执行，最多 5 个并发) → agent_node (ReAct 循环, ≤10 轮)
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
│   │   ├── tool_node.py      # secure_tools_node：三阶段安全流水线图节点（薄委托层，委托 security/orchestrator）
│   │   ├── security/         # 安全流水线包：models(抽象) / registry(单一事实源) / stages(阶段实现) / orchestrator(三阶段编排+执行原语)
│   │   └── tools/            # 工具实现（query, diagnosis, health, troubleshoot）
│   ├── db/                   # 数据库适配器（BaseAdapter ABC + mysql/postgresql/oracle）
│   ├── engine/               # 无状态引擎：nl2sql, sql_auditor(sqlglot), diagnosis, health_check, explain_estimator
│   ├── models/               # Pydantic schemas + SQLAlchemy ORM (connection/session/report)
│   └── prompts/              # Prompt 模板（Python 字面量，不含运行时变量）
├── alembic/                  # 数据库迁移（versions/ 目录有 4 个迁移）
└── tests/                    # pytest 测试（conftest + sql_auditor + adapter 集成测试）
    └── evaluation/           # Agent 评测：text_to_sql_evaluation（业务查询）+ ops_evaluation（运维场景）

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

1. **LangGraph ReAct Agent** — LLM 通过 `model.bind_tools()` 决定工具调用顺序，LangGraph 条件边自动路由 `agent ↔ tools` 循环。最多 20 次迭代防无限循环（`_MAX_AGENT_ITERATIONS`），另有连续拦截保护在第 5 次 RE 拦截时强制终止。

2. **SSE 双通道流式** — `astream(stream_mode=["updates", "messages"])` 双通道分离：
   - "messages" 通道：`(AIMessageChunk, metadata)` 逐 token 流，处理 `reasoning_content` / `tool_call_chunks` / `content`
   - "updates" 通道：节点执行完成后的状态 delta，从中提取 `sse_events` 列表和 `is_complete` 标志

3. **推理与回答分离** — 模型原生 `reasoning_content` 字段（DeepSeek/GLM 等支持）→ 作为独立 SSE `reasoning` 事件推送。普通 content 采用"乐观渲染+收编"模式：一律以 `stage="thinking"` 发射，`is_complete` 时若未检测到工具调用则发送 `stage_change("answer")` 触发前端收编。

4. **Security** — 单一声明式安全流水线（security-pipeline-north-star-plan）：
   - **安全注册表**：所有工具的安全行为集中在 `security/registry.py` 的 `SECURITY_REGISTRY`
     （SecurityProfile = 有序阶段序列），禁止在工具 extras/图节点散落安全逻辑。
   - **secure_tools_node**（工具执行，`tool_node.py`）：委托 `security/orchestrator.py` 三阶段编排——
     Phase 1 PRE_CONFIRM（并行纯审计，interrupt 前重跑两遍，禁止 DB 副作用）
     → Phase 2 CONFIRM（含确认阶段的工具批量 `interrupt()` 等待用户确认，任何工具执行之前）
     → Phase 3 PRE_EXECUTE + 执行（确认后只跑一遍，允许 EXPLAIN 副作用，并行最多 5 个并发）。
   - **阶段实现**（`security/stages.py`）：
     - SQLAuditStage（sqlglot 审计，PRE_CONFIRM）：拦截 DROP/ALTER/TRUNCATE 等红线 DDL、
       非 admin 的 DELETE/UPDATE/INSERT/MERGE。写操作在确认前先过此关，红线 DDL 不进确认流。
     - RowEstimationStage（EXPLAIN 多维度评估，PRE_EXECUTE）：规则引擎阻断大查询，
       失败时降级放行。
     - ConfirmStage（标记型，CONFIRM）：orchestrator 据此批量 interrupt，前端按
       `confirm_category`（sql_write / connection_kill / generic）渲染确认卡片。
   - **连续拦截保护**（防改写死循环）：`AgentState.consecutive_blocks` 跟踪连续被
     RowEstimationStage 拦截的次数（结构化 `ROW_ESTIMATION_BLOCKED` 识别，非字符串特征）。
     同一轮无 RE 拦截时自动重置。第 3 次拦截返回强建议 ToolMessage，第 5 次强制终止
     （`is_complete=True` + `CONSECUTIVE_BLOCKS_EXCEEDED` error 事件）。

5. **并行工具执行** — 当 LLM 在同一轮返回多个 `tool_calls` 时，`secure_tools_node`（`security/orchestrator.py`）在 PRE_CONFIRM 与 Phase 3 用 `asyncio.gather(return_exceptions=True)` 并发执行它们。总耗时 ≈ 最慢工具而非耗时之和。通过 `asyncio.Semaphore` 限制最大并发数（默认 5），防止 DB 连接池耗尽。前端的 `onToolResult` 匹配从 `findLastRunningToolCall()` 改为 `tool_call_id` 精确匹配，支持并行安全的结果关联。SSE 事件中的 `tool_call` 和 `tool_result` 均携带 `tool_call_id` 字段用于前后端关联。

6. **SSE 事件协议** — 12+ 种事件类型通过 SSE `event: message` + `data: JSON` 传输：`thinking` / `reasoning` / `token(stage=thinking|answer)` / `tool_call`（含 `tool_call_id`） / `tool_result`（含 `tool_call_id`） / `sql` / `result` / `text` / `error` / `done` / `stage_change` / `confirm_required`。前端 `useSSE` composable 统一解析分发到 Pinia store 回调。

7. **Database Adapters** — BaseAdapter ABC 定义统一接口（execute/explain/get_slow_queries/等），lazy-loaded 驱动（aiomysql/asyncpg/oracledb），`AdapterFactory.create()` 创建实例。连接密码仅存于请求内存 state，不持久化。

8. **Observability** — structlog 结构化日志 + X-Request-ID 全链路追踪 + trace_iterations 记录每轮 ReAct 决策轨迹。
   - **日志字段**：每条日志自动携带 `module` / `func_name` / `lineno`（`CallsiteParameterAdder`）与 `layer`（从模块路径推导：agent/api/db/engine/server/observability），可按层次 `grep '"layer": "agent"'` 过滤
   - **请求归属**：agent 流内所有日志（含数据库适配器、EXPLAIN 评估）经 contextvars 自动绑定 `session_id` / `connection_id` / `user_id` / `user_role`（配合中间件注入的 `trace_id`），可按会话/连接/请求过滤出完整日志链
   - **token 消耗**：每轮 LLM 决策记录 `input_tokens` / `output_tokens`（`_extract_token_usage()` 多 provider 兼容，写入决策日志 + trace_iterations 顶层随 agent_trace 持久化）；会话级由 `_stream_events` 汇总为 `total_tokens` → `done.tokens_used` 事件 + 入库 `messages.tokens_used` / `sessions.tokens_used_total`
   - **中文原文**：JSON 渲染 `ensure_ascii=False`，日志文件直接输出中文（UTF-8）；控制台经 UTF-8 流包装防 Windows GBK 乱码

9. **EXPLAIN 多维度安全评估** — 在执行 `execute_readonly_sql` 前通过 EXPLAIN 获取 5 维标准化指标（访问方式、扫描行数、返回行数、查询成本、额外操作），用硬编码规则引擎（7 条 CRITICAL 规则 R1–R7，WARNING 级别已移除）逐条评估。CRITICAL 命中则阻断并告知 LLM 改写，未命中则 LOW 放行。所有阈值在 `explain_estimator.py` 中硬编码管理，不依赖 .env 配置。`execute_write_sql` 不走自动安全护栏（用户确认即安全屏障），被拒绝的操作由 ToolMessage 返回给 LLM。EXPLAIN 失败时降级放行，不阻断正常业务。同时执行后对结果自动截断（默认 100 行 / 40K 字符，EXPLAIN 类结果 8K 字符），防止 LLM 上下文窗口溢出。

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

#### 危险操作确认卡片（内联设计，category 分类渲染）

确认卡片采用**内联卡片**而非模态弹窗，自然嵌入消息流中，不遮挡界面。  
按 `confirm_category` 分类渲染三种风格的确认卡片：

```
┌── sql_write ──────────────────────────────┐
│ ✎ 需要确认执行写操作                       │
│ ┌─ SQL 代码块（语法高亮）────────────────┐ │
│ │ INSERT INTO users ...                   │ │
│ └─────────────────────────────────────────┘ │
│          [取消]   [确认执行 (1.5s)]         │
└────────────────────────────────────────────┘

┌── connection_kill ─────────────────────────┐
│ ⚠ 需要确认终止数据库连接                   │
│ ┌─ 连接详情 ────────────────────────────┐  │
│ │ 线程 ID     │ 12345                    │  │
│ │ 已运行时长  │ 3600s                    │  │
│ │ 当前 SQL    │ SELECT sleep(...)        │  │
│ └────────────────────────────────────────┘  │
│ ⚡ 此操作不可逆，将立即断开该连接            │
│          [取消]   [确认终止 (1.5s)]          │
└────────────────────────────────────────────┘

┌── generic（降级兜底）──────────────────────┐
│ ℹ 需要确认执行操作                          │
│ 工具: some_new_tool                         │
│ ┌─ 参数详情 ────────────────────────────┐  │
│ │ param_a   │ value_a                    │  │
│ └────────────────────────────────────────┘  │
│          [取消]   [确认执行 (1.5s)]          │
└────────────────────────────────────────────┘
```

- **`sql_write`**：蓝色铅笔图标 + `--confirm-accent` 强调色 + SQL 语法高亮代码块 + "确认执行"
- **`connection_kill`**：红色三角警告图标 + `--confirm-danger` 强调色 + 线程详情表 + "此操作不可逆"提示 + "确认终止"
- **`generic`**：灰色信息圆图标 + `--confirm-generic` 强调色 + key-value 参数表 + "确认执行"（未知类别降级兜底）
- 所有类别共用响应式逻辑：`chatStore.pendingConfirm` 驱动、`isWaitingApproval` computed 自动判断、1.5s 冷却防误触
- 后端新增工具只需在 `security/registry.py` 的 `SECURITY_REGISTRY` 中给工具 profile 加 `confirm` 阶段（`params={"category": ...}`）即可自动接入确认流程

设计 token 在 `App.vue` CSS 变量中定义，支持 **深色/浅色** 双主题（通过 `[data-theme="light"]` 切换）。核心变量以 `--chat-*` 和 `--confirm-*` 前缀命名。字体：Satoshi（正文）+ JetBrains Mono（代码）。

### Tools Registry

所有 Agent 工具集中注册在 `app/agent/tools/registry.py`，新增工具只需在此注册，无需修改 graph.py：

| 工具 | 功能 | 安全声明（`SECURITY_REGISTRY`） |
|------|------|----------|
| `list_tables` | 列出数据库中所有表 | — |
| `describe_table` | 获取指定表结构 | — |
| `execute_readonly_sql` | 执行只读 SQL 查询（SELECT / SHOW / EXPLAIN） | `sql_audit`(PRE_CONFIRM) + `row_estimation`(PRE_EXECUTE) |
| `execute_write_sql` | 执行写 SQL（INSERT / UPDATE / DELETE，需用户确认） | `sql_audit`(PRE_CONFIRM) + `confirm`(sql_write) |
| `execute_write_transaction` | 事务型写 SQL（多条语句原子回滚，需用户确认一次） | `transaction_sql_audit`(PRE_CONFIRM, 逐条审计) + `confirm`(sql_write) |
| `get_slow_queries` | 获取慢查询日志（支持日志检测、performance_schema 降级、EXPLAIN 联动） | — |
| `explain_query` | 分析 SQL 执行计划 | `sql_audit`(PRE_CONFIRM) |
| `check_connections` | 检查连接池状态 | — |
| `check_locks` | 检查锁等待（返回完整锁拓扑：表名/锁模式/锁类型，区分 held/waiting） | — |
| `analyze_locks` | 查询指定事务/线程的详细锁信息（锁模式、等待链、根阻塞者） | — |
| `kill_transaction` | 终止指定线程的连接（需用户确认，自动验证） | `confirm`(connection_kill) |
| `check_replication` | 检查主从复制状态 | — |
| `run_health_check` | 执行 20 项健康巡检（含关联分析、一键修复建议） | — |

> 安全声明一律定义在 `app/agent/security/registry.py` 的 `SECURITY_REGISTRY`（单一事实源），
> 工具 extras 无安全声明残留。新增危险操作工具只需在此登记对应阶段即可接入安全流水线。

### Key Conventions

- FastAPI 路由前缀统一为 `/api/`，无版本号
- SSE endpoint: `POST /api/chat/stream`，取消: `POST /api/chat/cancel`
- 连接管理 CRUD: `/api/connections`
- 分页响应含 `total` / `page` / `pageSize` / `items`，默认 20，最大 100
- 所有 `@tool` 函数返回 Python 原生类型（dict/list/str），禁止返回 ORM 实例
- 连接密码仅存于内存 state，不持久化到数据库
- SQL 审计默认拦截 DROP/ALTER/TRUNCATE/CREATE/GRANT/REVOKE，多语句直接拦截
- **EXPLAIN 安全评估**：`execute_readonly_sql` 的 `row_estimation` 阶段（PRE_EXECUTE，确认后只跑一遍）通过 EXPLAIN 提取多维度指标并用规则引擎评估。`execute_write_sql` 走 `sql_audit`(PRE_CONFIRM) 前置审计 + `confirm`(sql_write) 确认，不走 row_estimation。阈值硬编码在 `explain_estimator.py`，不依赖 .env
- **连续拦截保护**（防改写死循环）：`AgentState.consecutive_blocks` 跟踪连续被 RowEstimationStage 拦截次数。第 1-2 次返回普通拦截消息，第 3 次起返回强建议 ToolMessage（含 SQL + EXPLAIN 建议）+ System Prompt 警告引导 LLM 停止改写并告知用户，第 5 次在 `secure_tools_node` 中强制终止（`is_complete=True`）。同一轮无 RE 拦截时计数器自动重置。RE 拦截识别用结构化 `block_code == "ROW_ESTIMATION_BLOCKED"`（非字符串特征耦合），advisory 序号按"本轮第 k 个"递增（修复并行旧值问题），不影响 SQLAuditStage
- **结果截断**：所有工具返回结果在序列化为 ToolMessage 前经 `truncate_result_for_llm()` 处理，最大 100 行 / 40K 字符，防止 LLM 上下文窗口溢出。截断时附带 `_truncated`、`_original_total_rows` 元信息
- **统一执行超时保护（execution-timeout-plan）**：三个数据库适配器共用 30s 执行超时（`STATEMENT_EXEC_TIMEOUT_SEC/MS`，定义于 `db/base.py`）。服务器端优先让数据库主动终止超时语句——PG `SET statement_timeout` / MySQL≥5.7.8 `SET SESSION MAX_EXECUTION_TIME`（毫秒，仅 SELECT）/ MariaDB `SET SESSION max_statement_time`（秒，全部语句）/ Oracle `AsyncConnection.call_timeout`（毫秒，中断在途语句，连接仍可用）；客户端兜底防服务器端机制缺失时无限等待——asyncpg `command_timeout` / MySQL `asyncio.wait_for`（35s，略高于服务器端让 DB 先终止，触发时关闭脏连接让池重建）。服务器端终止按错误码识别归为 `TimeoutError`（MySQL 3024 / MariaDB 1969），Oracle call_timeout 超时消息含 "call timeout" 归为 `TimeoutError`。超时后由 Agent 提示 LLM 改写查询而非长时间占用 DB 资源
- **结果集行数上限（read-limit-pushdown）**：`truncate_result_for_llm` 只保护 LLM 上下文窗口，不保护 Python 进程内存——适配器 fetchall 会把 DB 返回的全部行物化进堆。对只读 SELECT 做 DB 端 LIMIT 下推：`apply_read_limit()`（`db/base.py`，sqlglot 重写）自动追加 `LIMIT max+1`（Oracle 12c+ 用 `FETCH FIRST`），阈值 `STATEMENT_MAX_RESULT_ROWS=1000` hardcode（不依赖 .env）。已自带 ≤1000 的数值 LIMIT 原样返回、超大 LIMIT 收敛到 1001、非 SELECT/解析失败/占位符 LIMIT fail-safe 原样放行。返回 max+1 行时结果附 `truncated_by_db_limit`/`db_row_limit` 元数据，工具 summary 提示"已达 DB 行数上限"。审计仍跑在**原 SQL** 上，重写仅发生在适配器执行路径内部且不改变语句类型
- **并行工具执行**：`secure_tools_node`（security/orchestrator.py）用 `asyncio.gather()` 并发执行同轮 `tool_calls`（PRE_CONFIRM 与 Phase 3 均受 `AGENT_MAX_CONCURRENT_TOOLS`（默认 5）限制）。SSE 事件的 `tool_call` / `tool_result` 均携带 `tool_call_id` 供前端精确匹配
- **LLM 并发限流**：全后端唯一的 LLM 调用点（`graph.py` agent_node 的 `ainvoke`）经 `app/agent/llm_limiter.py` 的 `LLMLimiter.slot()` 限流。模块级共享 `asyncio.Semaphore`（进程级全局硬上限，区别于 tool_node 的每轮局部信号量），饱和时排队，超过 `AGENT_LLM_WAIT_TIMEOUT_SECONDS`（默认 30s）抛 `LLMConcurrencyBusyError` → SSE `error` 事件（`LLM_BUSY`）+ `is_complete=True` 友好降级「AI 服务繁忙」。`AGENT_MAX_CONCURRENT_LLM`（默认 5，范围 1-20）配置全局并发在途数上限。单进程部署全局生效，多 worker 需外部协调（见 docs/llm-concurrency-limit-plan.md）
- 前端 `onToolResult` 匹配策略：优先用 `tool_call_id` 从 `Map` O(1) 查找，回退到 `tool` 名匹配。`MessageList.vue` 的 `pairToolSteps()` 将 `tool_call` 与对应 `tool_result` 配对渲染
- **读写 SQL 工具分离**：`execute_sql` 已拆分为 `execute_readonly_sql`（只读查询，SECURITY_REGISTRY 声明 `sql_audit`(PRE_CONFIRM) + `row_estimation`(PRE_EXECUTE)）和 `execute_write_sql`（写操作，SECURITY_REGISTRY 声明 `sql_audit`(PRE_CONFIRM) + `confirm`(sql_write)）。**写操作在确认前先过 sql_audit**——红线 DDL 与非 admin 写操作在确认流之前即被拦截（不弹确认卡片）。LLM 错将写 SQL 发给 `execute_readonly_sql` 时，非 admin 用户会被 SQLAuditStage 拦截
- **语句类型白名单（stmt-type-whitelist）**：两个 SQL 执行工具的 `sql_audit` 阶段声明 `allowed_stmt_types`（只读工具=`SELECT/SHOW/EXPLAIN/UNION/USE/SET`，写工具=`INSERT/UPDATE/DELETE`），按 sqlglot AST 顶层类型强制校验（fail-closed：解析失败 / 多语句 / 未知类型一律拦截，block_code=`SQL_STMT_TYPE_BLOCKED`，在审计之前生效）。堵住 sqlglot 对 GRANT/REVOKE/REPLACE/CREATE USER/RENAME 等的 Command 回退与 MERGE/LOAD DATA 等的解析异常绕过——这些高危语句此前能借解析缺口溜过审计。未声明 `allowed_stmt_types` 的 SQL 工具（如 `explain_query`）保持原审计行为。白名单经 `StageRef.params` 注入（orchestrator 实例化阶段时 `STAGE_REGISTRY[ref.name](**ref.params)`）
- **无 WHERE 全表删改防护（write-where-guard）**：`execute_write_sql` 的 `sql_audit` 阶段声明 `require_where=True`，对 UPDATE/DELETE 强制要求非恒真 WHERE——无 WHERE 或 WHERE 恒真时在 PRE_CONFIRM 硬拦（block_code=`WRITE_NO_WHERE_BLOCKED`），不进确认流。判定：**WHERE 先经布尔化简 + 常量折叠（`sqlglot.optimizer.simplify`，OR 传播恒真/AND 吸收恒真/恒假湮灭/幂等），化简后不含列引用/子查询即拦**（`check_write_scope`，`sql_auditor.py`）——`WHERE 1=1`/`WHERE TRUE` 拦，`WHERE 1=1 OR id=1` 恒真分支污染整式也拦（化简后 WHERE 被整体丢），`WHERE 1=2 OR id=1` 化简为 `id=1` 正确放行。刻意放过 `WHERE deleted_at IS NOT NULL` 等合法范围删除（含列引用）；`WHERE id>0`、`WHERE id=id` 这类"基于列的恒真/近全表"化简与静态判定均无法识别（需列语义/取值域知识），属接受范围。化简失败回退原始表达式（fail-safe，不因化简 bug 放宽安全）。纯静态 AST 检查，零 DB 开销。
- **事务写工具（transaction-write）**：`execute_write_transaction(statements: list[str])` 在单次工具调用内完成 `BEGIN → 逐条执行 → 全部成功 COMMIT / 任一失败 ROLLBACK`，解决跨表/多步写操作无回滚的脏数据问题——原子性收在一个调用内，不引入跨轮共享连接的复杂度。适配器层新增 `BaseAdapter.execute_transaction`：MySQL 用 `conn.autocommit(False)` + 显式 commit/rollback，**finally 必须还原 autocommit=True 再 release**（池不重置会话状态，残留 manual-commit 污染下一位 acquire 者），超时关连接由 InnoDB 断连自动回滚；PostgreSQL 用 `async with conn.transaction()`（自动 BEGIN/COMMIT/ROLLBACK）；Oracle 单连接默认手动提交、隐式事务，显式 commit/rollback。安全：SECURITY_REGISTRY 登记 `transaction_sql_audit`(PRE_CONFIRM) 阶段，`TransactionAuditStage` 复用 `SQLAuditStage._audit_single_sql` 对 statements **逐条**执行类型白名单（纯写 INSERT/UPDATE/DELETE，事务内禁止 SELECT）+ sqlglot 审计 + require_where（任一条被拦整事务拦截，block_code 保留原值，reason 带语句索引），+ `confirm`(sql_write) 整个事务一次确认（`ConfirmStage.build_action` 对 statements 注入 `details.sql`，前端确认卡片一个代码块展示全部 SQL，**前端零改动**）。空/非列表 statements 双层 fail-closed（审计层 + 工具层）；`MAX_TRANSACTION_STATEMENTS=50` 限制长事务规模。
- **写操作确认 UI**：采用内联卡片非模态弹窗，`WriteConfirmation.vue` 组件根据 `chatStore.pendingConfirm` 渲染。tool_call 卡片通过 `isWaitingApproval` computed 响应式判断是否等待审批，显示时钟图标 + "等待用户审批"（由 `pendingConfirm.writes` 驱动，无需手动同步 `stepStatus`）。深色主题强调色浅绿 `#4ADE80`，浅色主题浅蓝 `#60A5FA`，变量定义在 `App.vue` 的 `--confirm-*` CSS 变量中
- **MySQL 版本检测**：`MySQLAdapter.connect()` 自动执行 `SELECT VERSION()` 检测 MySQL/MariaDB 版本，存储为 `_db_vendor` 和 `_version_int`，供 `is_mariadb()` / `get_db_version()` 查询
- **软删除感知**：`describe_table` 输出表级 `soft_delete` 字段（`{column, kind(flag|deleted_at), deleted_value, active_value, note}`），由 `engine/soft_delete.py` 的 `detect_soft_delete_columns()` 以列名关键词 + 列注释关键词启发式识别（零配置）。系统提示词规则 11 强制：表含软删除标识时删除必须用 `UPDATE SET 标识=已删除` 代替 `DELETE` 硬删除。仅感知层引导，不做执行层硬拦截
- **check_locks 返回结构**：返回 `{held_locks, waiting_locks, total_held, total_waiting, summary}`，每个锁记录含 `table_name`、`lock_mode`、`lock_type`。MySQL 8.0+ 走 `performance_schema.data_locks`，5.7/MariaDB 走 `SHOW ENGINE INNODB STATUS` 回退
- **get_slow_queries 降级方案**：慢查询日志未开启时返回开启指引 `SET GLOBAL slow_query_log = ON`；日志不可用时自动降级到 `performance_schema.events_statements_summary_by_digest`。返回 `slow_log_enabled` 和 `fallback_used` 标识数据来源
- **run_health_check 关联分析**：检查结果包含 `correlation_notes`（跨项关联分析列表）和 `fix_suggestions`（可执行修复 SQL 命令），从"发现问题"升级到"解决问题"
- **EXPLAIN 安全拼接**：适配器 `explain()` 方法增加多语句检测和单引号转义，作为 `SQLAuditStage` 之后的第二道防线（EXPLAIN 不支持参数化占位符）
- **安全流水线统一确认机制**：确认由 `secure_tools_node` 内的三阶段流水线统一编排（不再有独立 confirm_node）。工具含 `confirm` 阶段（CONFIRM 相位）即触发批量 `interrupt()` 等待用户确认，任何工具执行之前。`execute_write_sql` 和 `kill_transaction` 均通过此机制触发确认（分类分别为 `sql_write` / `connection_kill`）。前端按 `confirm_category`（`sql_write` / `connection_kill` / `generic`）分类渲染对应的确认卡片。新增危险操作工具只需在 `security/registry.py` 的 `SECURITY_REGISTRY` 中给工具 profile 加 `confirm` 阶段（含 category 参数）即可接入确认流程
- **安全行为单一事实源**：安全策略一律经 `app/agent/security/registry.py` 的 `SECURITY_REGISTRY` 声明（SecurityProfile 有序阶段序列），**禁止在工具 extras、图节点里散落安全逻辑**。PRE_CONFIRM 阶段必须纯（无 DB 副作用，interrupt 重放跑两遍）；resume 遍必须截断 sse_events（只返回本遍新事件）。两阶段间的"确认后只跑一遍"由 `interrupt()` 语义天然保证（Phase 3 仅 resume 遍/单遍可达）
- **结构化日志字段**：structlog 处理链 `_build_shared_processors()`（`log_setup.py`）自动为每条日志注入 `module/func_name/lineno/layer`。agent 流内日志经 contextvars 自动携带 `session_id/connection_id/user_id/user_role`（`_stream_events` 绑定、`finally` 解绑，保留中间件注入的 `trace_id`）。文件与控制台 JSON 均 `ensure_ascii=False` 输出中文原文
- **token 消耗统计**：`graph.py._extract_token_usage()` 兼容多 provider（`usage_metadata` → `token_usage` → `usage` 优先级），每轮决策日志与 `trace_iterations` 顶层记录 `input_tokens`/`output_tokens`；`chat.py` Step4 汇总 `total_tokens` 用于 `done.tokens_used` 事件与 `messages.tokens_used` / `sessions.tokens_used_total` 持久化
- **上下文压缩（context-compression-plan）**：懒触发 + 轮次边界摘要。每次 `agent_node` 决策前用 `_estimate_input_tokens` 估算（**真实锚定 + 增量**：`est = 上次真实 input + 上次 output + 增量×rate`，见 token-estimation-plan），超过 `AGENT_COMPACT_TRIGGER_TOKENS`（默认 0=按模型上下文窗口×0.5 自动推导）才启动压缩；压缩生效后**短路恒压缩**（锚点只在未压缩会话使用，避免锚点语义不一致）。`_partition_turns(messages, k)` 按 HumanMessage 切成（远古历史/近 K 轮窗口/当前轮），历史进摘要、窗口+当前轮逐字保留；`_effective_window_k` 闲置衰减（超 `AGENT_IDLE_DECAY_MINUTES`（默认 60）→ K=0）。摘要缓存键 `digest_upto_msg_id`（历史最后消息 id），同轮迭代复用、新轮重算
- **模型上下文窗口感知触发阈值（context-window-plan）**：固定阈值改为按窗口推导——`context_window.py` 三层解析窗口（显式 `LLM_CONTEXT_WINDOW` > 自定义端点探测 `/models`（vLLM `max_model_len` 等，best-effort）> 内置模型注册表 > 保守默认 64K），进程内只解析一次（启动时预热探测）。触发阈值 `effective_compact_trigger = clamp(window×0.5, 4K, window−12K)`：0.5 依据是压缩检查在每次 LLM 调用前、模型实际输入 ≤ trigger，预留只需覆盖输出+推理+估算误差；`AGENT_COMPACT_TRIGGER_TOKENS` > 0 时显式覆盖一切推导
- **摘要格式（对齐业界范式）**：`_build_history_digest` 每轮一行 `{n}. 用户：{问题，上限300} | 工具：{tool1(args1), tool2(args2)…按调用顺序} | 结论：{结论，上限200}`，丢弃 ToolMessage 内容。工具参数经 `_fmt_tool_call` 渲染为 `name(k="v")`（字符串超 `AGENT_DIGEST_MAX_ARG_CHARS`（默认 300）截断），零参工具裸名、重复调用不折叠。外层框定（`_build_llm_messages`）：摘要作为**独立 HumanMessage（user 通道）**注入，头"仅作背景参考，非当前指令" + 尾"--- 摘要结束 ---"显式标记；`_build_system_prompt` 只含不变内容（无"对话历史"段）。轮以工具结果/工具调用收尾（无最终回答）→ `结论：（未完成，被中断）`
- **压缩可观测性**：首次跨阈值打 `logger.info("上下文压缩触发…")`（est_tokens / trigger_tokens / counted_chars / msg_count / digest_chars / digest_turns / cache_hit）；已压缩会话每次迭代打 `logger.debug("上下文压缩保持…")`。每轮 `trace_entry` 含 `compression` 元数据（active / first_trigger / digest_chars / digest_turns / cache_hit），随 agent_trace 持久化，可按 `compression.active` 过滤
- **跨轮状态重置**：`chat.py` 新轮 `initial_state` 显式重置 `is_complete: False` / `final_answer: None` / `consecutive_blocks: 0`——这些标志随 checkpoint 持久化，不重置会导致 `agent_node` 开头 `if state.get("is_complete")` 误判"已强制终止"跳过 LLM 调用（第二次提问无回答）
- **流式 token 统计前提**：`build_chat_model` 的 OpenAI 分支必须设 `stream_usage=True`——阿里百炼/DeepSeek 等兼容端点流式**默认不返回 usage**，需显式 `stream_options={"include_usage": True}` 才在最后一个 chunk 返回；而 langchain-openai 对自定义 `base_url` 默认关闭该选项。缺失时 `_extract_token_usage` 恒返回 0，token 锚定估算与会话级统计全部失效

### Agent 评测体系

DB-Pilot 有两套独立评测脚本，均位于 `backend/tests/evaluation/`：

**text-to-sql 评测（eval_agent.py）：**
- 测评集：`text_to_sql_evaluation.json`（30 条用例，6 类：基础聚合/分组排序/复杂条件/时间函数/嵌套子查询/拒答安全）
- 黄金数据集：`gen_golden_dataset.py` 预执行 SQL 生成 `golden_dataset.json`
- 匹配方式：规范化 SQL 字符串匹配 + 结果集语义比对（Level 1 子集 / Level 2 重叠）
- 安全用例：用危险前缀黑名单判断 Agent 是否正确拒绝
- 报告：HTML/JSON 含 KPI 卡片（通过率/SQL 精确匹配率/结果匹配率/安全拒绝率等）

**运维场景评测（eval_ops_agent.py）：**
- 测评集：`ops_evaluation.json`（26 条用例，8 类：性能诊断/连接管理/锁分析/复制监控/健康巡检/协同诊断/危险操作确认/拒答安全）
- 三层评测维度：
  - L1 工具选择 — Agent 是否调用了正确的工具（集合匹配 + 禁用工具黑名单）
  - L2 参数正确性 — 工具参数字段级比对（精确/子串/范围三种匹配模式）
  - L3 调用顺序 — 多步推理时工具调用顺序 LCS 最长公共子序列验证
- 危险操作确认：检测 `confirm_required` SSE 事件是否正确触发
- 输出不依赖固定结果集（运维工具返回动态系统状态），评判**过程合理性**而非结果一致性
- 复用 `eval_agent.py` 的 `EvalConfig` / `APIClient` 等组件
- 前置条件：后端服务运行中 + 数据库连接已在前端创建（通过 `EVAL_CONN_NAME` 匹配或取第一个可用连接）
