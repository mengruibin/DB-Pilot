## 项目概述

DB-Pilot 是基于 LangGraph StateGraph + FastAPI 构建的**数据库运维 AI Agent 后端服务**。Agent 采用 ReAct（Reasoning + Acting）模式，LLM 自主决定工具调用顺序，通过 LangGraph 条件边自动路由 classify ↔ agent ↔ tools 循环。后端通过 SSE 向前端提供自然语言数据库查询、SQL 诊断优化、故障排查、健康巡检能力，通过可插拔适配器层同时支持 MySQL、PostgreSQL、Oracle。依据 [PRD §1.1]。

---

## 技术栈约束

### 语言与运行时

- [ ] MUST 使用 Python 3.12+（依据 PRD §6.1）
- [ ] MUST NOT 使用 Python 3.11 以下版本（async 语法差异、PEP 695 类型参数语法依赖 3.12）

### 核心框架与版本

| 库 | 版本约束 | 用途 | 依据 |
|---|---|---|---|
| FastAPI | >=0.115.0 | REST API + SSE | PRD §6.1 |
| LangGraph | >=0.3.0 | Agent 状态图编排 | PRD §6.1 |
| LangChain | >=0.3.0 | Chat 模型标准封装（bind_tools / ToolMessage） | 任务 1-6 重构 |
| langchain-anthropic | >=0.3.0 | Anthropic Claude Chat 模型 | 任务 1 重构 |
| langchain-openai | >=0.3.0 | OpenAI / 百炼兼容 Chat 模型 | 任务 1 重构 |
| SQLAlchemy | >=2.0.0 | 内部 ORM（会话/连接配置持久化） | PRD §6.1 |
| Pydantic | >=2.0 | 请求/响应模型、配置管理 | PRD §6.1 |
| sqlglot | >=25.0.0 | 多方言 SQL 解析与安全审计 | PRD §6.1, §8.1 |

### 数据库驱动

- [ ] MUST 使用 `aiomysql` 连接 MySQL（依据 PRD §6.1）
- [ ] MUST 使用 `asyncpg` 连接 PostgreSQL（依据 PRD §6.1）
- [ ] MUST 使用 `oracledb>=2.0` 连接 Oracle（依据 PRD §6.1）
- [ ] MUST NOT 在线程池中运行同步数据库驱动——所有 DB I/O 必须原生异步
- [ ] MUST NOT 在适配器层 import 目标数据库驱动；使用懒加载并在 `connect()` 失败时给出明确安装提示

### LLM 集成

- [ ] MUST 使用 LangChain 标准 Chat 模型（`ChatAnthropic` / `ChatOpenAI`）统一调用 LLM
- [ ] MUST 通过 `app.agent.models.build_chat_model()` 工厂函数获取模型实例
- [ ] MUST 工具绑定使用 `model.bind_tools()` 标准 API（任务 1-3 重构）
- [ ] MUST NOT 直接使用 `httpx` 调用 LLM REST API（旧 `LLMClient` Agent 部分已废弃）
- [ ] MUST 默认使用 Anthropic Claude API（依据 PRD §6.1）
- [ ] SHOULD 架构兼容 OpenAI 格式的模型切换（依据 PRD §10.3）
- [ ] MUST NOT 在代码中硬编码模型名称；模型名必须从 `config.py` 读取

### 禁止项

- [ ] MUST NOT 引入 `celery`、`django`、`flask` 等替代框架
- [ ] MUST NOT 使用同步 HTTP 客户端（`requests`）；必须使用 `httpx` 异步客户端
- [ ] MUST NOT 引入额外 ORM 框架替代 SQLAlchemy

---

## 目录与命名规范

### 关键目录用途（依据 PRD §7）

```
backend/
├── app/
│   ├── main.py          # FastAPI 应用工厂 + 生命周期钩子，禁止在此写业务逻辑
│   ├── config.py        # 全量 Settings，从 .env / 环境变量加载
│   ├── api/             # 仅薄路由层：参数校验 → 调用 agent/ 或 engine/ → 返回 Response
│   ├── agent/           # Agent ReAct 决策引擎：LangGraph 状态图（LangChain Chat 模型 + bind_tools + SafeToolNode）、意图路由、工具注册、安全护栏
│   ├── db/              # 数据库适配器（目标数据库连接，非内部数据库）
│   ├── engine/          # 无状态引擎：NL2SQL、SQL 审计、诊断、巡检
│   ├── models/          # Pydantic schemas + SQLAlchemy ORM 模型
│   └── prompts/         # Prompt 模板定义（Python 字面量，不可含运行时变量）
└── tests/               # pytest 测试，目录镜像 app/ 结构
```

### 命名规则

- [ ] MUST 文件名全小写下划线：`sql_auditor.py`，NOT `SQLAuditor.py`（依据 PRD §7 示例）
- [ ] MUST 类名使用 PascalCase：`BaseAdapter`、`ConnectionConfig`（依据 PRD §6.2）
- [ ] MUST 函数/方法名使用 `snake_case`：`get_slow_queries()`、`explain()`（依据 PRD §6.2）
- [ ] MUST 常量使用 `UPPER_SNAKE_CASE`
- [ ] MUST API 路由前缀统一为 `/api/`，版本号不进入 URL 路径（Phase 5 前不引入 `/v1/`）
- [ ] MUST SSE endpoint 路径为 `/api/chat/stream`（依据 PRD §7 目录注释）
- [ ] MUST 连接管理 CRUD 路径为 `/api/connections`（依据 PRD §7 目录注释）

### TypeScript 前端对接约定

- [ ] MUST 前端 API 调用封装统一放在 `frontend/src/api/`，与后端 `app/api/` 路由一一对应
- [ ] MUST 前端 TypeScript 接口定义中的字段名与后端 Pydantic schema 字段名完全一致（驼峰映射由 `client.ts` 统一处理）

---

## API 与数据契约

### 通用响应格式

<!-- TODO: 需人工确认 —— 选择以下方案之一：
  A. { "code": 0, "data": {...}, "message": "ok" }  中台风格
  B. { "success": true, "data": {...}, "error": null }  偏 RESTful
  C. 直接返回数据体，异常走 HTTP 状态码 + {"detail": "..."}
-->

- [ ] MUST 所有成功响应的 HTTP 状态码为 200（查询）、201（创建）、204（删除）
- [ ] MUST 所有列表接口默认 `pageSize=20`，最大 `pageSize=100`（依据 PRD §10.1 性能要求——限制单次数据量）
- [ ] MUST 分页响应包含 `total`、`page`、`pageSize`、`items` 四个字段
- [ ] MUST 校验错误返回 422，响应体包含 `detail` 字段列出每个字段的校验失败原因（FastAPI 默认行为）

### SSE 流式格式

- [ ] MUST 每条 SSE 消息格式为 `data: {"type": "<TYPE>", "content": ...}\n\n`
- [ ] MUST SSE 消息类型至少包含：`thinking`（Agent 推理过程——ReAct 模式下为 LLM 流式推理文本）、`tool_call`（工具调用）、`tool_result`（工具返回）、`sql`（生成的 SQL 语句）、`result`（最终结果）、`error`（错误终止）、`done`（流结束）
- [ ] MUST SSE 以 `type: "done"` 消息结束，前端据此关闭连接
- [ ] MUST SSE 连接超时 120s 无消息则服务端主动关闭

### 数据库操作原则（依据 PRD §8.1）

- [ ] MUST 目标数据库默认以只读用户连接；写操作需要用户显式确认并临时提权
- [ ] MUST 所有发往目标数据库的 SQL 在执行前必须经过 `sql_auditor.audit()` 校验
- [ ] MUST `statement_timeout` / `max_execution_time` 默认上限 30s（依据 PRD §8.1 Layer 4）
- [ ] MUST 写操作包裹在事务中，异常即 ROLLBACK（依据 PRD §8.1 Layer 5）
- [ ] MUST NOT 在适配器层拼接原始 SQL 字符串；参数必须使用参数化查询（`params` dict）

### 工具函数返回契约

- [ ] MUST 所有 `@tool` 装饰的函数返回 Python 原生类型（dict/list/str），禁止返回 SQLAlchemy ORM 实例
- [ ] MUST 工具函数内部捕获异常后返回 `{"error": "<可读描述>", "detail": "<原始异常类型>"}` 字典，MUST NOT 向上抛出未处理异常
- [ ] MUST 连接配置参数（`connection_id`/`db_type`/`host`/`port`/`database`/`user`/`password`/`ssl_enabled`/`ssl_ca_cert`）使用 `Annotated[str, InjectedToolArg]` 标注，由 `SafeToolNode` 在运行时自动注入，LLM 通过 `bind_tools()` 不可见这些参数
- [ ] MUST `SafeToolNode`（`app/agent/tool_node.py`）在工具执行前依次执行：连接配置注入 → 安全护栏检查 → 工具调用 → 结果脱敏，返回标准 `ToolMessage` 对象

---

## 安全与合规红线

### 绝对禁止（依据 PRD §8）

- [ ] MUST NOT 在代码中硬编码任何密钥、密码、API Key、连接串——所有敏感配置从环境变量或 `.env` 读取
- [ ] MUST NOT 在日志中输出连接密码、数据库凭据、完整连接串（依据 PRD §8.2）
- [ ] MUST NOT 跳过 SQL 审计直接执行用户/LLM 生成的 SQL（依据 PRD §8.1 Layer 2）
- [ ] MUST NOT 将目标数据库密码写入服务端磁盘持久化存储（依据 PRD §8.2：仅内存会话 + 浏览器 localStorage）
- [ ] MUST NOT 允许以下 SQL 操作符/语句通过审计：`DROP`、`ALTER`、`TRUNCATE`、`CREATE`、`GRANT`、`REVOKE`（除非用户显式确认写操作，依据 PRD §8.1 Layer 3）

### 强制遵循

- [ ] MUST 每次 SQL 执行前调用 `sqlglot.parse()` 解析 AST 并遍历检查危险操作节点
- [ ] MUST `.env` 文件加入 `.gitignore`，仓库中仅保留 `.env.example` 模板
- [ ] MUST 服务启动时校验所有 `Settings` 必填字段非空，缺失则拒绝启动
- [ ] MUST 会话空闲 30 分钟后自动清理内存中的连接凭据（依据 PRD §8.2）
- [ ] MUST 内部数据库连接串通过 `DATABASE_URL` 环境变量配置，不硬编码在代码中

### 数据隐私

- [ ] MUST NOT 将用户查询的数据库 Schema 元数据发送给第三方日志/监控服务
- [ ] MUST NOT 将用户查询的实际数据行缓存到 LLM 对话历史中超过会话生命周期
- [ ] SHOULD 在 Prompt 模板中明确标注「禁止在输出中包含实际数据行内容到日志」

---

## AI 行为准则

### 不确定时的默认动作

- [ ] MUST 当 PRD 中缺少明确规格（如错误码方案、认证方式）时，AI 必须在代码注释中以 `<!-- TODO: 需人工确认 -->` 标记，列出 2-3 个候选方案及其适用场景，并默认选择标注 `[DEFAULT]` 的方案实现
- [ ] MUST 当遇到多种等效实现方式时，优先选择项目现有代码中已使用的模式（如已在 `db/base.py` 中定义了抽象方法签名，新适配器必须遵循该签名，不得自行扩展参数）
- [ ] MUST 当无法确定一条规则是否适用时，倾向更严格的安全策略（如不确定 SQL 是否危险 → 拦截并询问用户）
- [ ] MUST NOT 自行创建 PRD 未提及的新目录或模块；如需新增，必须在代码注释中说明扩展理由并标记 `<!-- EXTENSION: 理由 -->`

### 提问策略

- [ ] MUST 在遇到架构级决策分歧（如缓存策略选型、WebSocket vs SSE 升级）时暂停编码并向用户提问，提供 2-3 个方案及推荐
- [ ] SHOULD 在单个对话轮次中发现 3+ 个 TODO 标记需要确认时，汇总为列表一次性提问，避免碎片化确认
- [ ] MUST NOT 对 PRD 已明确规定的规则重新提问确认（如"FastAPI 还是 Flask？"——PRD §6.1 已确定）

### 输出格式要求

- [ ] MUST 生成 Python 代码时，每个函数必须包含完整的类型注解（参数 + 返回值）
- [ ] MUST 所有公开函数/类包含 docstring（Google style），说明参数、返回值、可能抛出的异常
- [ ] MUST 涉及数据库操作的代码生成后，附带一个 `# SAFETY: <依据PRD章节>` 注释说明安全措施
- [ ] MUST 每次代码生成后，在响应末尾附一个自检清单：
  ```
  ✅ 类型注解完整
  ✅ 异步 I/O 无阻塞
  ✅ SQL 经审计
  ✅ 敏感信息未硬编码
  ✅ 错误已捕获
  ```
- [ ] MUST NOT 生成超过 200 行的单文件；超过时必须拆分模块并说明拆分逻辑

---

## 验证与测试要求

### 自动化检查项（代码生成后必须通过）

- [ ] MUST 代码可通过 `ruff check --select E,F,I,N,UP,B,SIM` 零错误（依据技术栈约束——推荐 Ruff 作为 Python linter）
- [ ] MUST 所有 `async def` 函数内部不包含阻塞调用（`time.sleep`、同步 `requests`、同步 DB 驱动）
- [ ] MUST 所有 Pydantic model 字段包含 `description` 属性（用于自动生成 OpenAPI 文档）
- [ ] MUST 每个 API 端点至少有一个 pytest 测试用例覆盖 200 响应
- [ ] MUST `sql_auditor.audit()` 必须通过以下 5 条基线测试：
  1. 合法 SELECT → 通过
  2. DROP TABLE → 拒绝
  3. DELETE FROM → 拒绝（未确认模式）
  4. SELECT ... INTO OUTFILE → 拒绝（数据导出风险）
  5. 多语句 `SELECT 1; DROP TABLE users;` → 拒绝
- [ ] MUST 适配器层每个数据库类型至少有一个集成测试（连接测试库 → 执行 → 断言返回结构）

### 性能门禁（依据 PRD §10.1）

- [ ] MUST `explain()` 适配器方法调用超时上限 10s
- [ ] MUST `get_slow_queries()` 单次返回条数上限 100 条
- [ ] MUST 巡检报告 20 项检查总耗时 < 30s（依据 PRD §10.1）
- [ ] MUST SSE 流式首 Token 延迟（从接收到用户消息到发送第一个 `thinking` 事件）< 2s（依据 PRD §10.1）

### 前端对接验证

- [ ] MUST 后端 API 变更后同步更新 `frontend/src/types/` 下的对应 TypeScript 类型定义文件
- [ ] MUST SSE 消息新增 type 时必须在 `frontend/src/composables/useSSE.ts` 中注册对应处理分支
