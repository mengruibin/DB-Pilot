# DB-Pilot

> AI 驱动的数据库运维智能助手 · AI-Powered Database Operations Assistant
>
> 用自然语言对话的方式，完成数据查询、SQL 诊断、故障排查与健康巡检 —— 让每个开发者都拥有一个 7×24 小时在线的 DBA 助手。

[![Python](https://img.shields.io/badge/Python-3.12+-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688)](https://fastapi.tiangolo.com/)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.3%2B-1C3C3C)](https://www.langchain.com/langgraph)
[![Vue](https://img.shields.io/badge/Vue-3-4FC08D)](https://vuejs.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow)]()

---

## 目录

- [项目简介](#项目简介)
- [界面预览](#界面预览)
- [核心功能](#核心功能)
- [系统架构](#系统架构)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [环境变量配置](#环境变量配置)
- [Agent 工具集](#agent-工具集)
- [安全设计](#安全设计)
- [API 概览](#api-概览)
- [SSE 事件协议](#sse-事件协议)
- [Agent 评测体系](#agent-评测体系)
- [测试](#测试)
- [项目结构](#项目结构)
- [相关文档](#相关文档)

---

## 项目简介

DB-Pilot 是一个基于大语言模型（LLM）与 LangGraph ReAct Agent 的**数据库运维智能助手**。它以 Web 应用形态提供自然语言交互界面，帮助 DBA、开发者与运维人员高效完成日常数据库工作：不会写 SQL 就"说出来"，性能问题 AI 自动定位，故障排查引导式进行，巡检报告一键生成。

项目由三部分组成：

| 部分 | 说明 |
|------|------|
| **后端** | FastAPI + LangGraph 构建的 ReAct Agent 服务，通过 SSE 流式输出；可插拔适配器层连接目标数据库 |
| **前端** | Vue 3 + TypeScript 单页应用，流式对话界面 + 连接管理 + 巡检报告 |
| **评测** | 两套独立的 Agent 评测体系（Text-to-SQL + 运维场景工具调用链验证） |

---

## 界面预览

### 🔍 自然语言数据查询（NL2SQL）

以自然语言提问，AI 自动生成并执行 SQL，全程可视化思考过程与工具调用链（工具参数、执行耗时、结果导出）。

![自然语言查询 → SQL → 结果](./docs/screenshots/chat-nl2sql.png)

### ⚠️ 危险操作确认

写操作在执行前经 sqlglot 安全审计与 `impact_estimate`（EXPLAIN 影响行数预估），通过后以内联确认卡片等待用户确认——SQL 高亮展示，并附「预计影响约 N 行（EXPLAIN 预估，非精确值）」帮助你在确认前判断操作量级，确认 / 取消一目了然。

![写操作确认卡片](./docs/screenshots/write-confirm.png)

### 🔌 多数据库连接管理

MySQL / PostgreSQL / Oracle 统一适配，连接卡片一键激活目标数据库，连通状态实时可见。

![连接管理](./docs/screenshots/connections.png)

### 📊 数据库健康巡检

一键发起 20 项多维健康检查，结果按类别分组（连接 / 性能 / 慢查询 / 存储 / 安全 / 复制），附优化建议与总结。

![健康巡检结果](./docs/screenshots/health-check.png)

---

## 核心功能

### 🔍 自然语言数据查询（NL2SQL）
- 自然语言 → SQL 自动转换，Schema 上下文自动注入
- SQL 安全审计（sqlglot）+ EXPLAIN 多维度安全评估
- 查询结果表格展示 + 一键导出 CSV（流式、限量、脱敏）

### 🩺 SQL 智能诊断与优化
- 慢查询日志分析（日志未开启时自动降级到 `performance_schema`）
- 执行计划解读（`EXPLAIN` 联动）
- 索引与 SQL 改写建议

### 🛠️ 故障自动排查
- 死锁 / 锁等待分析（完整锁拓扑：held / waiting 分层）
- 连接池耗尽诊断、主从复制延迟排查
- 危险操作（终止事务）需用户确认，自动验证

### 📊 数据库健康巡检
- **20 项**多维健康检查（连接数、慢查询占比、缓存命中率、复制延迟、表空间、QPS/TPS…）
- 健康评分 + 跨项关联分析 + 一键修复建议
- 报告持久化，HTML 查看 / 导出

### 🔌 多数据库管理
- MySQL / PostgreSQL / Oracle 统一适配层
- 连接配置 CRUD + 连通性测试 + Schema 元数据浏览
- 连接密码仅存内存，不落盘

### 👥 用户系统
- JWT 登录认证 + 用户数据隔离（连接、会话、报告按用户隔离）
- 角色控制：`admin`（可执行写 SQL）/ `readonly`（仅读）
- 会话空闲自动清理（默认 30 分钟）

---

## 系统架构

```
用户消息 → SSE POST /api/chat/stream
  → (api/chat.py) 解析连接、构建 AgentState (scoped to connection)
  → (agent/graph.py) LangGraph StateGraph（双通道流式）:
      agent_node (LLM bind_tools → 决策工具调用或最终回答)
        → secure_tools_node (三阶段安全流水线：PRE_CONFIRM 并行审计 → CONFIRM 批量 interrupt 确认 → PRE_EXECUTE + 并行执行，最多 5 个并发) → agent_node (ReAct 循环, ≤10 轮)
        → END (is_complete=True)
  → SSE 流式返回 12+ 种事件类型 (messages/updates 双通道)
      ┌─ messages: reasoning / token / tool_call_chunks (逐 token)
      └─ updates: tool_call / tool_result / sql / error / stage_change / done / confirm_required
```

### 架构要点

- **LangGraph ReAct Agent** — LLM 通过 `bind_tools()` 自主决定工具调用顺序，条件边自动路由 `agent ↔ tools` 循环；最多 10 轮防无限循环，另有连续拦截保护在第 4 次 RE 拦截时强制终止。
- **SSE 双通道流式** — `astream(stream_mode=["updates", "messages"])` 将逐 token 文本流与节点状态增量分离，前端可同时渲染打字机效果与工具调用进度。
- **推理与回答分离** — 模型原生 `reasoning_content`（DeepSeek / GLM / Claude extended thinking）作为独立 `reasoning` 事件推送；普通内容采用"乐观渲染 + 收编"模式。
- **并行工具执行** — 同轮多 `tool_calls` 用 `asyncio.gather()` 并发执行，`asyncio.Semaphore` 限制最大并发（默认 5），前端用 `tool_call_id` 精确关联结果。
- **上下文压缩** — 懒触发（估算超 60K token）+ 轮次边界摘要，历史进摘要、近 K 轮逐字保留，长对话不爆窗口。
- **LLM 全局限流** — 全后端唯一的 LLM 调用点统一经 `LLMLimiter`（模块级 Semaphore）限流，饱和时排队，超时抛 `LLM_BUSY` 友好降级。
- **可观测性** — structlog 结构化日志 + X-Request-ID 全链路追踪 + 每轮 ReAct 决策轨迹持久化，可按 `session_id` / `connection_id` / `trace_id` 过滤完整日志链。

---

## 技术栈

### 后端

| 类别 | 技术 |
|------|------|
| 语言 / 运行时 | Python 3.12+ |
| Web 框架 | FastAPI ≥0.115 + uvicorn |
| Agent 框架 | LangGraph ≥0.3（StateGraph）+ LangChain ≥0.3 |
| LLM 接入 | langchain-anthropic / langchain-openai（统一 `build_chat_model()` 工厂） |
| ORM / 迁移 | SQLAlchemy 2.0 + Alembic |
| SQL 解析 | sqlglot ≥25（多方言审计） |
| 目标库驱动 | aiomysql / asyncpg / oracledb（懒加载） |
| 内部库 / 检查点 | SQLAlchemy（MySQL / SQLite）+ PostgreSQL（LangGraph checkpointer） |
| 日志 | structlog（JSON / 彩色文本双格式） |
| 测试 | pytest + pytest-asyncio + ruff |

### 前端

| 类别 | 技术 |
|------|------|
| 框架 | Vue 3 + TypeScript + Vite 5 |
| 状态管理 | Pinia |
| UI 组件库 | naive-ui |
| 流式通信 | 原生 fetch + SSE（`useSSE` composable） |
| 渲染 | markdown-it + highlight.js（Markdown / SQL 高亮） |
| 导出 | xlsx + 流式 CSV 下载 |
| 测试 | Vitest + @vue/test-utils |

### 支持的 LLM Provider

- **Anthropic**（默认）：`claude-*` 系列模型
- **OpenAI 兼容接口**：OpenAI、阿里云百炼（DashScope）、DeepSeek 等 —— 设置 `LLM_PROVIDER=openai` + `LLM_API_URL` 即可切换
- 可选开启 `ENABLE_REASONING=true` 启用模型深度思考模式

---

## 快速开始

### 环境要求

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | ≥ 3.12 | 后端运行时 |
| uv | 最新 | Python 依赖管理 |
| Node.js / npm | ≥ 18 | 前端构建 |
| PostgreSQL | ≥ 14 | LangGraph checkpointer（会话持久化） |
| MySQL / PostgreSQL / Oracle | — | 被运维的目标数据库（至少一个） |

> **提示**：本机 `uv run` 若因 `.venv` 文件锁失败，可用 `uv run --no-sync` 绕过。

### 1. 克隆并配置后端

```bash
git clone <repo-url> && cd DB-Pilot

# 复制配置模板并填写真实值
cd backend
cp .env.example .env
```

至少需要配置以下必填项（缺失则拒绝启动）：

```bash
LLM_API_KEY=your-api-key
LLM_MODEL=claude-sonnet-5
DATABASE_URL=mysql+aiomysql://root:password@127.0.0.1:3306/db_pilot   # 内部库
CHECKPOINT_DB_URL=postgresql://postgres:password@127.0.0.1:5432/db_pilot_checkpoint
JWT_SECRET=your-random-secret-at-least-32-chars
```

安装依赖并初始化数据库：

```bash
uv sync
# 创建内部库与 checkpointer 库（按需）
# mysql -uroot -p -e "CREATE DATABASE db_pilot CHARACTER SET utf8mb4"
# psql -U postgres -c "CREATE DATABASE db_pilot_checkpoint;"

# 数据库迁移（Alembic）
uv run alembic upgrade head

# 创建管理员账号
uv run python scripts/create_admin.py --username admin --password your-password
```

### 2. 启动后端

```bash
# Windows 必须加 --loop app.main:selector_loop_factory：psycopg 异步模式
# 不兼容 ProactorEventLoop（uvicorn 0.51 在 Windows 硬编码 ProactorEventLoop）
uv run uvicorn app.main:app --reload --port 8000 --loop app.main:selector_loop_factory
```

后端启动后：API 文档见 `http://127.0.0.1:8000/docs`。

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

访问 `http://localhost:5173`，登录后在「连接管理」页配置目标数据库连接，即可开始对话。

### 4. 生产构建

```bash
cd frontend
npm run build        # 产物输出到 dist/
npm run preview      # 本地预览
```

---

## 环境变量配置

完整模板见 [`backend/.env.example`](backend/.env.example)。关键配置项：

| 配置项 | 必填 | 默认值 | 说明 |
|--------|:---:|--------|------|
| `LLM_API_KEY` | ✅ | — | LLM API Key（默认 Anthropic，用 OpenAI 兼容接口填对应 key） |
| `LLM_MODEL` | ✅ | — | 复杂任务模型（NL2SQL、诊断、故障排查） |
| `LLM_PROVIDER` | — | `anthropic` | `anthropic` \| `openai`（OpenAI 兼容接口） |
| `LLM_API_URL` | — | 空 | 自定义 OpenAI 兼容地址（如阿里云百炼） |
| `ENABLE_REASONING` | — | 空 | 启用模型深度思考模式 |
| `DATABASE_URL` | ✅ | — | 内部库连接串（`mysql+aiomysql://` 或 `sqlite+aiosqlite://`） |
| `CHECKPOINT_DB_URL` | ✅ | — | LangGraph checkpointer 连接串（`postgresql://`） |
| `SERVER_HOST` / `SERVER_PORT` | — | `127.0.0.1` / `8000` | 服务监听地址 |
| `CORS_ORIGINS` | — | `http://localhost:5173` | 允许的跨域来源（逗号分隔） |
| `JWT_SECRET` | ✅ | — | JWT 签名密钥（≥32 字符） |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | — | `1440` | 令牌过期时间（24h） |
| `SESSION_IDLE_TIMEOUT_MINUTES` | — | `30` | 会话空闲自动清理时间 |
| `LOG_LEVEL` / `LOG_FORMAT` / `LOG_DIR` | — | `DEBUG` / `text` / `logs` | 日志配置（生产建议 `json`） |
| `AGENT_MAX_CONCURRENT_TOOLS` | — | `5` | 每轮最大并行工具数（1-20） |
| `AGENT_MAX_CONCURRENT_LLM` | — | `5` | 全局 LLM 并发在途数上限（1-20） |
| `AGENT_LLM_WAIT_TIMEOUT_SECONDS` | — | `30` | LLM 饱和排队超时 |
| `AGENT_COMPACT_TRIGGER_TOKENS` | — | `60000` | 上下文压缩懒触发阈值 |
| `EXPORT_MAX_ROWS` / `EXPORT_BATCH_SIZE` | — | `100000` / `2000` | 导出行数上限与分批大小 |

---

## Agent 工具集

工具注册于 `backend/app/agent/tools/registry.py`（新增工具只需在此注册），安全策略集中声明于 `backend/app/agent/security/registry.py` 的 `SECURITY_REGISTRY`（单一事实源）。

| 工具 | 功能 | 安全声明（`SECURITY_REGISTRY`） |
|------|------|----------|
| `list_tables` | 列出数据库中所有表 | — |
| `describe_table` | 获取指定表结构（含软删除标识标注） | — |
| `execute_readonly_sql` | 执行只读 SQL（SELECT / SHOW / EXPLAIN） | `sql_audit`(PRE_CONFIRM) + `row_estimation`(PRE_EXECUTE) |
| `execute_write_sql` | 执行写 SQL（INSERT / UPDATE / DELETE，需用户确认） | `sql_audit`(PRE_CONFIRM) + `impact_estimate`(PRE_CONFIRM) + `confirm`(sql_write) |
| `execute_write_transaction` | 事务型写 SQL（多条语句原子回滚，需用户确认一次） | `transaction_sql_audit`(PRE_CONFIRM) + `impact_estimate`(PRE_CONFIRM) + `confirm`(sql_write) |
| `get_slow_queries` | 获取慢查询日志（日志检测 / performance_schema 降级 / EXPLAIN 联动） | — |
| `explain_query` | 分析 SQL 执行计划 | `sql_audit`(PRE_CONFIRM) |
| `check_connections` | 检查连接池状态 | — |
| `check_locks` | 检查锁等待（完整锁拓扑：表名 / 锁模式 / 锁类型，区分 held / waiting） | — |
| `analyze_locks` | 查询指定事务 / 线程的详细锁信息（等待链、根阻塞者） | — |
| `kill_transaction` | 终止指定线程的连接（需用户确认，自动验证） | `confirm`(connection_kill) |
| `check_replication` | 检查主从复制状态 | — |
| `run_health_check` | 执行 20 项健康巡检（含关联分析、一键修复建议） | — |

> 安全行为一律经 `SECURITY_REGISTRY` 声明，禁止在工具 extras / 图节点散落安全逻辑。新增危险操作工具只需给工具 profile 加 `confirm` 阶段（含 category 参数）即可接入确认流程。

---

## 安全设计

DB-Pilot 采用**单一声明式安全流水线**（`backend/app/agent/security/` 包）——所有工具的安全策略集中在 `SECURITY_REGISTRY` 声明（SecurityProfile = 有序阶段序列），一个 `secure_tools_node` 按三阶段编排，图结构为 `agent → tools → agent`。

### 1. 三阶段安全流水线（`secure_tools_node`）
- **Phase 1 PRE_CONFIRM（并行审计 + 影响预估）**：每个工具 profile 的 PRE_CONFIRM 阶段（sqlglot 审计等纯函数，interrupt 重放会跑两遍）在确认前并行执行；写工具的 `impact_estimate` 在此用只读 EXPLAIN 产出「预估影响行数」注入确认卡（信息增强、非安全闸门，重放会重复一次只读 EXPLAIN）。红线 DDL 与非 admin 写操作在此被拦，**不再弹确认卡片**。
- **Phase 2 CONFIRM（批量 interrupt 确认）**：含 `confirm` 阶段的工具（写 SQL / 终止连接）批量 `interrupt()` 等待用户确认，任何工具执行之前。前端按 `confirm_category` 渲染内联确认卡片：`sql_write`（SQL 高亮）/ `connection_kill`（线程详情 + "此操作不可逆"）/ `generic`（降级兜底）。
- **Phase 3 PRE_EXECUTE + 执行（只跑一遍）**：确认后对幸存者执行 PRE_EXECUTE（如 EXPLAIN 评估）+ 工具执行。interrupt 重放语义保证 Phase 3 只跑一遍——**EXPLAIN / 工具恰执行一次**；resume 遍截断 sse_events，历史 tool_call 事件不重发。

### 2. 安全阶段（`agent/security/stages.py`）
- **SQLAuditStage**：sqlglot 解析审计，拦截 DROP / ALTER / TRUNCATE / CREATE / GRANT / REVOKE 等危险 DDL、非 admin 的 DELETE / UPDATE / INSERT / MERGE 与多语句注入。**写操作在确认前先过此关**，审计通过才进确认流。
- **RowEstimationStage**：EXPLAIN 提取 5 维标准化指标（访问方式 / 扫描行数 / 返回行数 / 查询成本 / 额外操作），7 条 CRITICAL 规则评估，命中则阻断并引导 LLM 改写；EXPLAIN 失败时降级放行。
- **ImpactEstimateStage**：写工具确认前用只读 EXPLAIN 估算 UPDATE / DELETE / INSERT..SELECT 的影响行数（PG `Plan Rows` / MySQL `rows×filtered` / Oracle 语句节点 Rows），写入确认卡供用户判断量级；失败 / 无法预估（字面量 INSERT 等）时静默降级，不阻断审批。
- **ConfirmStage**：标记型阶段，驱动批量确认（`sql_write` / `connection_kill` / `generic` 分类）。
- **连续拦截保护**：防 LLM 反复改写绕过 —— 结构化 `ROW_ESTIMATION_BLOCKED` 识别，第 3 次拦截返回强建议 ToolMessage，第 4 次强制终止。

### 3. 纵深防护
- **参数化查询**：禁止字符串拼接 SQL，一律使用参数绑定。
- **结果脱敏**：敏感列（密码 / token 等）自动掩码；结果截断（默认 100 行 / 40K 字符）防上下文溢出。
- **连接密码内存态**：目标库密码仅存于请求内存 state，绝不持久化。
- **LLM 全局限流**：`LLMLimiter` 进程级 Semaphore 硬上限，防 provider 429 与成本失控。
- **软删除感知**：`describe_table` 标注软删除标识列，系统提示词强制"删除用 UPDATE 置标而非 DELETE"。
- **用户数据隔离**：连接、会话、报告按用户隔离；非 admin 用户写 SQL 被审计拦截。

---

## API 概览

所有接口前缀为 `/api`，无需版本号。

| 模块 | 方法 & 路径 | 说明 |
|------|-------------|------|
| Health | `GET /api/health` | 存活探针 |
| Chat | `POST /api/chat/stream` | SSE 流式对话（核心） |
| Chat | `POST /api/chat/cancel` | 取消进行中的流 |
| Sessions | `GET/POST /api/chat/sessions` 等 | 会话管理（历史 / 重命名） |
| Connections | `GET/POST /api/connections` | 连接列表 / 新建 |
| Connections | `GET/PUT/DELETE /api/connections/{id}` | 连接详情 / 更新 / 删除 |
| Connections | `POST /api/connections/{id}/test` | 连通性测试 |
| Connections | `GET /api/connections/{id}/metadata` | Schema 元数据 |
| Query | `POST /api/connections/{id}/query` | 执行只读 SQL（经审计） |
| Query | `POST /api/connections/{id}/export` | 流式导出 CSV |
| Query | `GET /api/connections/{id}/slow-queries` | 慢查询列表（分页） |
| Report | `POST /api/connections/{id}/health-check` | SSE 流式健康巡检 |
| Report | `GET /api/reports` / `GET /api/reports/{id}` | 报告列表 / 详情 |
| Report | `GET /api/reports/{id}/export` | 报告导出 |
| Troubleshoot | `POST /api/connections/{id}/troubleshoot` | 故障排查 |
| Auth | `POST /api/auth/login` / `GET /api/auth/me` | 登录 / 当前用户 |
| Users | `GET/POST/PUT/DELETE /api/users` | 用户管理（管理员） |

**分页约定**：列表接口返回 `total` / `page` / `pageSize` / `items`，默认 20，最大 100。

---

## SSE 事件协议

SSE 消息格式：`event: message` + `data: JSON`（`{"type": "<TYPE>", ...}`）。前端 `useSSE` composable 统一解析分发到 Pinia store。

| 事件类型 | 说明 |
|----------|------|
| `thinking` | Agent 推理过程（阶段变更） |
| `reasoning` | 模型深度推理文本（`reasoning_content`） |
| `token` | 逐 token 文本流（`stage: thinking` / `answer`） |
| `tool_call` | 工具调用开始（含 `tool_call_id`、参数 JSON） |
| `tool_result` | 工具返回结果（含 `tool_call_id`、导出信息 `export_sql` / `total_rows`） |
| `sql` | 生成的 SQL 语句 |
| `result` | 最终结果 |
| `text` | 附加文本 |
| `error` | 错误终止（如 `LLM_BUSY` 服务繁忙降级） |
| `done` | 流结束（含 `tokens_used` 汇总） |
| `stage_change` | 阶段切换（`thinking` → `answer`） |
| `confirm_required` | 危险操作待确认 |

---

## Agent 评测体系

两套独立评测脚本，均位于 `backend/tests/evaluation/`。

### Text-to-SQL 评测（`eval_agent.py`）
- 测评集 `text_to_sql_evaluation.json`：**30 条**用例，6 类（基础聚合 / 分组排序 / 复杂条件 / 时间函数 / 嵌套子查询 / 拒答安全）
- 黄金数据集 `golden_dataset.json` 由 `gen_golden_dataset.py` 预执行生成
- 匹配方式：规范化 SQL 字符串 + 结果集语义比对（Level 1 子集 / Level 2 重叠）
- 报告：HTML / JSON 含 KPI 卡片（通过率 / SQL 精确匹配率 / 结果匹配率 / 安全拒绝率）

```bash
cd backend/tests/evaluation && python gen_golden_dataset.py   # 预构建黄金数据集
cd backend/tests/evaluation && python eval_agent.py           # 执行评测并生成报告
```

### 运维场景评测（`eval_ops_agent.py`）
- 测评集 `ops_evaluation.json`：**26 条**用例，8 类（性能诊断 / 连接管理 / 锁分析 / 复制监控 / 健康巡检 / 协同诊断 / 危险操作确认 / 拒答安全）
- **三层评测维度**：
  - **L1 工具选择** — 工具集合匹配 + 禁用工具黑名单
  - **L2 参数正确性** — 参数字段级比对（精确 / 子串 / 范围三种匹配模式）
  - **L3 调用顺序** — 多步推理的工具调用顺序 LCS 验证
- 危险操作确认：检测 `confirm_required` SSE 事件是否正确触发
- 评判**过程合理性**而非结果一致性（运维工具返回动态系统状态）

```bash
cd backend/tests/evaluation && python eval_ops_agent.py                       # 全量运行
cd backend/tests/evaluation && python eval_ops_agent.py --ids OP-001,OP-006   # 指定用例
cd backend/tests/evaluation && python eval_ops_agent.py --cases 10            # 前 10 条
```

> 前置条件：后端服务运行中 + 前端已创建目标库连接（`EVAL_CONN_NAME` 匹配或取第一个可用连接）。

---

## 测试

```bash
# 后端（ruff 检查 + pytest）
ruff check backend/app/ backend/tests/
ruff format backend/app/ backend/tests/
pytest backend/tests/ -v

# 前端（Vitest + 类型检查）
cd frontend
npm test
npx vue-tsc --noEmit
```

后端测试覆盖：SQL 审计器、EXPLAIN 评估、数据脱敏、CSV 导出、上下文压缩、LLM 限流器、软删除识别、工具节点、**安全流水线（注册表 / 阶段 / 编排器重放幂等）**、用户数据隔离、数据库适配器等模块。

---

## 项目结构

```
DB-Pilot/
├── README.md                         # 项目说明（本文档）
├── CLAUDE.md                         # 项目开发指南与架构约定（供 AI 编程助手使用）
├── backend/                          # FastAPI + LangGraph 后端
│   ├── AGENTS.md                     # 后端 AI 协作约定
│   ├── app/
│   │   ├── main.py                   # 应用工厂 + 中间件（CORS / RequestID / 访问日志）
│   │   ├── config.py                 # Pydantic Settings（.env 加载，启动校验必填）
│   │   ├── api/                      # 薄路由层：chat / connection / query / report / troubleshoot / auth / users
│   │   ├── agent/                    # LangGraph ReAct Agent 引擎
│   │   │   ├── graph.py              # StateGraph 编排 + 上下文压缩
│   │   │   ├── state.py              # AgentState TypedDict
│   │   │   ├── models.py             # Chat 模型工厂（Anthropic / OpenAI 统一接口）
│   │   │   ├── tool_node.py          # secure_tools_node：三阶段安全流水线图节点（薄委托层）
│   │   │   ├── security/             # 安全流水线包：models(抽象) / registry(单一事实源) / stages(阶段实现) / orchestrator(三阶段编排+执行原语)
│   │   │   ├── llm_limiter.py        # 全局 LLM 并发限流
│   │   │   └── tools/                # 工具实现（query / diagnosis / health / troubleshoot / registry）
│   │   ├── db/                       # 数据库适配器（BaseAdapter + mysql / postgresql / oracle）
│   │   ├── engine/                   # 无状态引擎（sql_auditor / explain_estimator / health_check / soft_delete / csv_exporter / data_masking）
│   │   ├── models/                   # Pydantic schemas + SQLAlchemy ORM
│   │   ├── prompts/                  # Prompt 模板（Python 字面量）
│   │   └── auth/                     # JWT 认证 + 权限依赖
│   ├── alembic/                      # 数据库迁移
│   ├── tests/                        # pytest 测试
│   │   └── evaluation/               # Agent 评测（text-to-sql + ops）
│   └── scripts/                      # 运维脚本（create_admin 等）
└── frontend/                         # Vue 3 + TypeScript 前端
    ├── AGENTS.md                     # 前端 AI 协作约定
    └── src/
        ├── views/                    # 页面：ChatView / ConnectionView / ReportView / LoginView / UserManagementView
        ├── components/               # 组件：chat / common / connection / report / sql
        ├── stores/                   # Pinia stores（chat / connection / report / auth / theme）
        ├── composables/              # useSSE / useConnection / useHistory / useExport / useIdleTimeout…
        ├── api/                      # API 客户端（与后端路由一一对应）
        └── types/                    # TS 类型定义（与 Pydantic schema 对应）
```

---

## 相关文档

| 文档 | 说明 |
|------|------|
| [CLAUDE.md](CLAUDE.md) | 项目开发指南与架构约定（供 AI 编程助手使用） |

---

## License

[MIT](https://opensource.org/licenses/MIT)
