# 后端开发任务清单

> 依据：[api-contract.md](../docs/api-contract.md)、[backend/AGENTS.md](../backend/AGENTS.md)  
> 版本：v0.1 · 日期：2026-06-30  
> 任务状态：`计划中` → `开发中` → `已完成` → `已验收`
> 每个任务对应一个可独立构建、运行、验证的交付单元。

---

## B-01：FastAPI 项目骨架与配置管理

| 字段 | 内容 |
|------|------|
| **ID** | B-01 |
| **标题** | FastAPI 应用工厂 + Settings 配置加载 + .env 模板 |
| **关联契约** | api-contract §1.6 错误码（422 Pydantic 校验）、backend AGENTS.md §技术栈约束 |
| **输入** | PRD §6.1 技术栈表、backend AGENTS.md 禁止项列表 |
| **输出物** | `backend/app/main.py`、`backend/app/config.py`、`backend/.env.example`、`backend/pyproject.toml`、`backend/requirements.txt` |
| **验收标准** | 1. `uvicorn app.main:app --reload` 启动成功，`/docs` 可见 Swagger UI<br>2. `Settings` 类从 `.env` 加载必填字段：`LLM_API_KEY`、`LLM_MODEL`、`DATABASE_URL`（内部 SQLite），缺失任一则启动报错退出<br>3. `pyproject.toml` 声明 Python >= 3.12；依赖含 `fastapi>=0.115.0`、`langgraph>=0.3.0`、`sqlalchemy>=2.0.0`、`pydantic>=2.0`、`sqlglot>=25.0.0`<br>4. `ruff check --select E,F,I,N,UP,B,SIM` 零错误<br>5. `X-Request-ID` 中间件注入所有响应头（UUID v4 格式）<br>6. `.env` 已在 `.gitignore` 中，`.env.example` 含所有必填字段注释 |
| **前置依赖** | 无 |
| **继承 TODO** | api-contract T-1（响应信封格式——默认方案 C 直接数据体）、T-3（认证方案——Phase 1 默认无认证） |
| **状态** | 已完成 |

---

## B-02：内部 SQLite 数据模型——连接配置

| 字段 | 内容 |
|------|------|
| **ID** | B-02 |
| **标题** | ConnectionConfig ORM 模型 + Alembic Migration + Pydantic Schema |
| **关联契约** | api-contract §2.1 ConnectionConfig 实体、backend AGENTS.md §安全与合规红线（密码不落盘） |
| **输入** | api-contract §2.1 字段定义 |
| **输出物** | `backend/app/models/connection.py`（ORM）、`backend/app/models/schemas.py` 中 `ConnectionCreateRequest` / `ConnectionResponse` Pydantic 模型、`backend/alembic/versions/001_create_connections.py` |
| **验收标准** | 1. ORM 表 `connections` 字段与契约 §2.1 完全一致（id/name/db_type/host/port/database/user/ssl_enabled/ssl_ca_cert/extra_params/created_at/updated_at/last_tested_at/status）<br>2. `password` 字段不存在于 ORM 表中（仅内存持有，不入库）<br>3. `ConnectionResponse` Pydantic model 不含 `password` 字段<br>4. `ConnectionCreateRequest` 校验：`db_type` 枚举 `mysql`/`postgresql`/`oracle`，`name` 1-64 字符，`port` 1-65535<br>5. `alembic upgrade head` 执行成功，SQLite 文件生成于 `backend/data/` 目录<br>6. `status` 字段默认值为 `"unknown"` |
| **前置依赖** | B-01 |
| **继承 TODO** | api-contract T-3（`name` 唯一性范围取决于认证方案——当前无认证时为全局唯一） |
| **状态** | 已完成 |

---

## B-03：内部 SQLite 数据模型——会话与消息

| 字段 | 内容 |
|------|------|
| **ID** | B-03 |
| **标题** | Session + Message ORM 模型 + Alembic Migration + Pydantic Schema |
| **关联契约** | api-contract §2.2 Session / Message 实体、backend AGENTS.md §安全与合规红线（数据隐私——查询结果不跨会话可读） |
| **输入** | api-contract §2.2 字段定义 |
| **输出物** | `backend/app/models/session.py`（ORM）、`backend/app/models/schemas.py` 中 `SessionResponse` / `MessageResponse`、`backend/alembic/versions/002_create_sessions_messages.py` |
| **验收标准** | 1. `sessions` 表字段：id/connection_id/title/created_at/last_active_at/status/message_count/tokens_used_total<br>2. `messages` 表字段：id/session_id/role/content/message_type/sql_generated/sql_executed/result_preview/error_info/created_at/tokens_used<br>3. `result_preview` 列类型为 JSON（SQLite JSON），最多存储 20 行（契约 §2.2 Message 约束）<br>4. `session_id` → `sessions.id` 外键级联删除<br>5. `status` 枚举约束：`active`/`idle`/`closed`<br>6. `role` 枚举约束：`user`/`assistant`/`system` |
| **前置依赖** | B-01 |
| **继承 TODO** | 无——契约 §2.2 完全定义 |
| **状态** | 已完成 |

---

## B-04：数据库适配器抽象基类与能力声明

| 字段 | 内容 |
|------|------|
| **ID** | B-04 |
| **标题** | BaseAdapter ABC + AdapterCapabilities 数据类 + AdapterFactory |
| **关联契约** | api-contract §1.1 连接管理、§2.1 ConnectionStatus；PRD §6.2 BaseAdapter 设计；backend AGENTS.md §目录与命名规范（`db/base.py`） |
| **输入** | PRD §6.2 抽象方法签名 |
| **输出物** | `backend/app/db/base.py`、`backend/app/db/factory.py` |
| **验收标准** | 1. `BaseAdapter(ABC)` 含 `@abstractmethod` 方法：`connect`/`disconnect`/`test_connection`/`execute`/`get_databases`/`get_tables`/`get_columns`/`get_indexes`/`get_slow_queries`/`explain`/`get_connections_status`/`get_lock_info`/`get_replication_status`/`get_metrics`/`get_capabilities`——全部为 `async def`，参数签名与 PRD §6.2 一致<br>2. `AdapterCapabilities` dataclass 含 6 个布尔字段：`supports_explain`/`supports_slow_query_log`/`supports_replication`/`supports_table_spaces`/`supports_json_type`，默认 False<br>3. `AdapterFactory.create(db_type: str, config: ConnectionConfig) -> BaseAdapter` 懒加载驱动模块，`db_type` 非法时抛出 `ValueError` 并提示可接受值<br>4. `connect()` 失败时给出明确安装提示（如 "请安装 aiomysql: pip install aiomysql"）<br>5. 适配器层不 import 目标数据库驱动——懒加载 |
| **前置依赖** | B-02（依赖 ConnectionConfig Pydantic model） |
| **继承 TODO** | 无 |
| **状态** | 已完成 |

---

## B-05：MySQL 适配器实现

| 字段 | 内容 |
|------|------|
| **ID** | B-05 |
| **标题** | MySQLAdapter——连接、元数据查询、诊断查询、指标采集 |
| **关联契约** | api-contract §1.1（test/metadata）、§1.3（query/explain/slow-queries）、§1.4（health-check 依赖指标）；backend AGENTS.md §数据库驱动（aiomysql） |
| **输入** | B-04 BaseAdapter 接口、PRD §5.3 死锁检测方式（`SHOW ENGINE INNODB STATUS`） |
| **输出物** | `backend/app/db/mysql.py` |
| **验收标准** | 1. 所有 BaseAdapter 抽象方法均已实现，`get_capabilities()` 返回 `{supports_explain:True, supports_slow_query_log:True, supports_replication:True, supports_table_spaces:False}`<br>2. `connect()` 使用 `aiomysql.create_pool`，`readonly=True` 模式，`autocommit=True`（依据 backend AGENTS.md §数据库操作原则）<br>3. `execute()` 接受 `params` dict 参数化查询，所有 SQL 通过占位符 `%s` 传递<br>4. `get_tables()` 返回 `[{database, table_name, comment, row_count_estimate}]`<br>5. `get_columns()` 返回 `[{name, type, nullable, is_primary, comment}]`，从 `information_schema.COLUMNS` 查询<br>6. `get_slow_queries()` 从 `mysql.slow_log` 读取，若未启用返回 `[]` + warning<br>7. `explain()` 执行 `EXPLAIN FORMAT=JSON` 并返回原始输出<br>8. `get_connections_status()` 返回契约 §2.1 ConnectionStatus 各字段<br>9. 连接失败不回显密码（异常消息仅包含 host:port） |
| **前置依赖** | B-04 |
| **继承 TODO** | api-contract T-4（敏感列判定——MySQL 适配器不支持从注释读取敏感标记，使用列名正则 fallback） |
| **状态** | 已完成 |

---

## B-06：PostgreSQL 适配器实现

| 字段 | 内容 |
|------|------|
| **ID** | B-06 |
| **标题** | PostgresAdapter——连接、元数据查询、诊断查询、指标采集 |
| **关联契约** | api-contract §1.1–§1.5；backend AGENTS.md §数据库驱动（asyncpg） |
| **输入** | B-04 BaseAdapter 接口、PRD §5.3 死锁检测方式（`pg_locks`）、复制检测（`pg_stat_replication`） |
| **输出物** | `backend/app/db/postgresql.py` |
| **验收标准** | 1. 所有 BaseAdapter 抽象方法均已实现<br>2. `connect()` 使用 `asyncpg.create_pool`，连接参数含 `statement_timeout=30000`<br>3. `execute()` 参数化使用 `$1, $2` 占位符<br>4. `get_columns()` 从 `information_schema.columns` + `pg_catalog` 查询，含 `is_primary` 判断<br>5. `get_slow_queries()` 从 `pg_stat_statements` 扩展读取（若扩展未安装返回 warning 而非崩溃）<br>6. `explain()` 执行 `EXPLAIN (FORMAT JSON, ANALYZE false)`<br>7. `get_lock_info()` 查询 `pg_locks` + `pg_stat_activity` 关联<br>8. `get_replication_status()` 查询 `pg_stat_replication`<br>9. `get_capabilities()` 返回 `{supports_explain:True, supports_slow_query_log:True, supports_replication:True, supports_table_spaces:True}` |
| **前置依赖** | B-04 |
| **继承 TODO** | 无 |
| **状态** | 已完成 |

---

## B-07：Oracle 适配器实现

| 字段 | 内容 |
|------|------|
| **ID** | B-07 |
| **标题** | OracleAdapter——连接、元数据查询、诊断查询 |
| **关联契约** | api-contract §1.1–§1.5；backend AGENTS.md §数据库驱动（oracledb>=2.0） |
| **输入** | B-04 BaseAdapter 接口 |
| **输出物** | `backend/app/db/oracle.py` |
| **验收标准** | 1. BaseAdapter 核心方法均已实现（`supports_replication:False`, `supports_slow_query_log:False` 通过 `get_capabilities()` 声明）<br>2. `connect()` 使用 `oracledb.connect_async`<br>3. `execute()` 参数化使用 `:1, :2` 占位符<br>4. `get_tables()` 从 `ALL_TABLES` 查询<br>5. `get_columns()` 从 `ALL_TAB_COLUMNS` 查询<br>6. `get_slow_queries()` 返回 `{"items":[], "warning":"Oracle slow query retrieval requires AWR license; not auto-retrieved"}`<br>7. `explain()` 执行 `EXPLAIN PLAN FOR ...` + `SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY)` |
| **前置依赖** | B-04 |
| **继承 TODO** | 无 |
| **状态** | 已完成 |

---

## B-08：连接管理 API——CRUD

| 字段 | 内容 |
|------|------|
| **ID** | B-08 |
| **标题** | 连接 CRUD REST API（GET/POST/PUT/DELETE /api/connections） |
| **关联契约** | api-contract §1.1（GET/POST/PUT/DELETE /api/connections）；backend AGENTS.md §API 与数据契约（分页默认 pageSize=20） |
| **输入** | B-02 ConnectionConfig ORM、B-01 config.py |
| **输出物** | `backend/app/api/connection.py` |
| **验收标准** | 1. `GET /api/connections?page=1&pageSize=20` 返回 `{items, total, page, pageSize}`，password 不在响应中<br>2. `POST /api/connections` 接收契约 §1.1 请求体，校验 `db_type` 枚举、`port` 默认值（3306/5432/1521），返回 201<br>3. `GET /api/connections/{id}` 返回单条连接详情（不含密码），404 时返回 `{"error_code":"NOT_FOUND","user_message":"..."}`<br>4. `PUT /api/connections/{id}` 更新连接，password 仅在传入非空时更新内存<br>5. `DELETE /api/connections/{id}` 返回 204，级联关闭该连接的所有活跃 Session<br>6. pageSize 超过 100 时自动截断为 100<br>7. 所有响应含 `X-Request-ID` 头 |
| **前置依赖** | B-02、B-04（AdapterFactory 用于状态检查，但非阻塞依赖） |
| **继承 TODO** | api-contract T-1（响应信封——当前按方案 C 直接数据体）、api-contract T-3（无认证中间件） |
| **状态** | 已完成 |

---

## B-09：连接测试 API

| 字段 | 内容 |
|------|------|
| **ID** | B-09 |
| **标题** | POST /api/connections/{id}/test——测试连接可用性 |
| **关联契约** | api-contract §1.1 POST /api/connections/{id}/test；frontend AGENTS.md §2（错误信息不含密码） |
| **输入** | B-04 AdapterFactory、B-08 连接 CRUD |
| **输出物** | `backend/app/api/connection.py` 中追加 `test_connection` 端点 |
| **验收标准** | 1. 成功响应 `{"success":true,"latency_ms":<int>,"version":"<db_version>","capabilities":{...}}`<br>2. 失败响应 `{"success":false,"error_code":"DB_UNREACHABLE","user_message":"..."}` —— `user_message` 不包含密码<br>3. 测试使用独立短生命周期连接，测试后立即释放<br>4. 测试成功后更新 `connections.last_tested_at` 和 `status="healthy"`<br>5. 测试失败后更新 `status="unreachable"` |
| **前置依赖** | B-05（至少一个适配器实现可用） |
| **继承 TODO** | api-contract §1.1 TODO：测试结果是否持久化（当前实现持久化 `last_tested_at` + `status`） |
| **状态** | 已完成 |

---

## B-10：连接元数据 API

| 字段 | 内容 |
|------|------|
| **ID** | B-10 |
| **标题** | GET /api/connections/{id}/metadata——获取 Schema 元数据 |
| **关联契约** | api-contract §1.1 GET /api/connections/{id}/metadata；frontend AGENTS.md §4（补全来源，缓存 5 分钟） |
| **输入** | B-05/B-06/B-07 适配器 `get_databases()`/`get_tables()`/`get_columns()`/`get_indexes()` |
| **输出物** | `backend/app/api/connection.py` 中追加 `get_metadata` 端点 |
| **验收标准** | 1. 响应结构 = `{databases:[], tables:[{database, table_name, comment, row_count_estimate, columns:[], indexes:[]}]}`<br>2. `columns` 每项含 `{name, type, nullable, is_primary, comment}`<br>3. `indexes` 每项含 `{name, columns, is_unique, type}`<br>4. 所有文本字段（name/comment）包装为安全字符串，防止 XSS<br>5. 响应头 `Cache-Control: private, max-age=300`（5 分钟缓存）<br>6. 无分页（数据量由数据库本身决定） |
| **前置依赖** | B-05（至少 MySQL 适配器可用）、B-08 |
| **继承 TODO** | 无 |
| **状态** | 已完成 |

---

## B-11：SQL 安全审计引擎

| 字段 | 内容 |
|------|------|
| **ID** | B-11 |
| **标题** | sql_auditor.py——sqlglot AST 解析 + 危险操作检测 + 多语句拦截 |
| **关联契约** | api-contract §1.6 错误码 400（SQL_AUDIT_BLOCKED）；backend AGENTS.md §安全与合规红线（绝对禁止清单）、§验证与测试要求（5 条基线测试） |
| **输入** | PRD §8.1 SQL 安全五层模型 Layer 1-2、backend AGENTS.md 禁止操作符清单 |
| **输出物** | `backend/app/engine/sql_auditor.py`、`backend/tests/test_sql_auditor.py` |
| **验收标准** | 1. `audit(sql: str, db_type: str, user_role: str) -> AuditResult` 返回 `{passed:bool, violations:[{type, message, location}]}`<br>2. 以下 SQL 必须拦截：`DROP TABLE`/`DROP DATABASE`/`ALTER TABLE`/`TRUNCATE`/`CREATE DATABASE`/`GRANT`/`REVOKE`<br>3. `DELETE`/`UPDATE` 在 `user_role != "admin"` 时拦截（依据 api-contract §1.6 403）<br>4. `SELECT ... INTO OUTFILE` 拦截（数据导出风险）<br>5. 多语句（`SELECT 1; DROP TABLE users;`）拦截<br>6. 纯 `SELECT`（只读）通过<br>7. 适配器方言：MySQL → sqlglot MySQL dialect，PG → PostgreSQL dialect<br>8. AuditResult 含 `is_readonly: bool` 标记<br>9. 5 条基线测试全部通过（backend AGENTS.md 测试要求） |
| **前置依赖** | B-01（依赖 sqlglot） |
| **继承 TODO** | 无——PRD §8.1 完全定义 |
| **状态** | 已完成 |

---

## B-12：LangGraph Agent 状态图与意图路由

| 字段 | 内容 |
|------|------|
| **ID** | B-12 |
| **标题** | Agent 状态图定义 + Intent Router + 状态管理 |
| **关联契约** | api-contract §1.2 SSE 事件类型；PRD §4.2 Agent 工作流 6 步；backend AGENTS.md §技术栈约束（LangGraph>=0.3.0） |
| **输入** | PRD §6.3 Agent 设计（工具注册表、意图路由流程） |
| **输出物** | `backend/app/agent/__init__.py`、`backend/app/agent/state.py`、`backend/app/agent/router.py`、`backend/app/agent/graph.py` |
| **验收标准** | 1. `AgentState` TypedDict 含：`messages`（LangChain Message 列表）、`connection_id`、`session_id`、`intent`、`user_role`<br>2. `IntentRouter.classify(user_message: str) -> Intent` 返回枚举 `QUERY`/`DIAGNOSIS`/`TROUBLESHOOT`/`HEALTH_CHECK`/`GENERAL`<br>3. 快速规则匹配：关键词"慢查询/卡/死锁/锁/连接数"命中 DIAGNOSIS/TROUBLESHOOT 的置信度 >0.8 时跳过 LLM 分类<br>4. 低置信度时调用 Haiku 轻量模型（从 `config.py` 读取 `LLM_CLASSIFIER_MODEL`）分类<br>5. StateGraph 节点：`classify` → `route_to_engine`（条件边）→ `nl2sql_node`/`diagnosis_node`/`troubleshoot_node`/`healthcheck_node`/`general_node` → `format_response`<br>6. 每个 node 可调用多个 tool，tool 返回后追加到 `messages` |
| **前置依赖** | B-01 |
| **继承 TODO** | 无 |
| **状态** | 已完成 |

---

## B-13：Agent 工具——查询类

| 字段 | 内容 |
|------|------|
| **ID** | B-13 |
| **标题** | 查询工具集：list_tables / describe_table / run_query |
| **关联契约** | api-contract §1.2 SSE tool_call/tool_result；PRD §6.3 工具注册表；backend AGENTS.md §工具函数返回契约 |
| **输入** | B-04 BaseAdapter、B-11 SQL Auditor |
| **输出物** | `backend/app/agent/tools/query.py` |
| **验收标准** | 1. `list_tables(connection_id: str) -> dict`：调用适配器 `get_tables()`，返回 `{"tables":[...]}` ，异常返回 `{"error":"...","detail":"..."}`<br>2. `describe_table(connection_id: str, table_name: str) -> dict`：返回 `{"columns":[...],"indexes":[...]}`<br>3. `run_query(connection_id: str, sql: str, user_role: str) -> dict`：先调 `sql_auditor.audit()` 拦截危险操作；通过后调适配器 `execute()`；返回 `{"columns":[...],"rows":[...],"execution_time_ms":<int>,"audit_status":"passed"}`<br>4. 所有函数用 `@tool` 装饰，返回 Python dict<br>5. 所有函数包含完整类型注解 + Google docstring<br>6. 每个函数末尾含 `# SAFETY: <依据>` 注释 |
| **前置依赖** | B-11、B-05（至少 MySQL 适配器） |
| **继承 TODO** | api-contract T-4（敏感列脱敏——run_query 返回时对列名匹配敏感模式的列执行 `***` 替换） |
| **状态** | 已完成 |

---

## B-14：Agent 工具——诊断类

| 字段 | 内容 |
|------|------|
| **ID** | B-14 |
| **标题** | 诊断工具集：explain_query / get_slow_queries |
| **关联契约** | api-contract §1.3 POST /api/connections/{id}/explain、GET /api/connections/{id}/slow-queries；PRD §5.2 |
| **输入** | B-04 BaseAdapter、B-11 SQL Auditor |
| **输出物** | `backend/app/agent/tools/diagnosis.py` |
| **验收标准** | 1. `explain_query(connection_id: str, sql: str, format: str = "tree") -> dict`：先审计 SQL，通过后调适配器 `explain()`，返回 `{"explain_output":"...","format":"tree"}`<br>2. 如适配器 `supports_explain=False`，返回 `{"error":"该数据库类型不支持 EXPLAIN"}`<br>3. `get_slow_queries(connection_id: str, time_range: str = "1h", limit: int = 20) -> dict`：调用适配器 `get_slow_queries()`，返回 `{"items":[...],"total":<int>}`<br>4. 异常返回 `{"error":"...","detail":"..."}` |
| **前置依赖** | B-13（共用适配器连接池） |
| **继承 TODO** | api-contract T-8（慢查询阈值联动——当前固定 limit=20） |
| **状态** | 计划中 |

---

## B-15：Agent 工具——故障排查类

| 字段 | 内容 |
|------|------|
| **ID** | B-15 |
| **标题** | 故障排查工具集：check_connections / check_locks / check_replication |
| **关联契约** | api-contract §1.5 POST /api/connections/{id}/troubleshoot；PRD §5.3 故障类型覆盖 |
| **输入** | B-04 BaseAdapter |
| **输出物** | `backend/app/agent/tools/troubleshoot.py` |
| **验收标准** | 1. `check_connections(connection_id: str) -> dict`：返回 `{"status":"pass","data":{...ConnectionStatus}}`，若使用率>80% 则 `status="warning"`，>95% `status="error"`<br>2. `check_locks(connection_id: str) -> dict`：返回 `{"status":"pass|warning|error","waiting_transactions":<int>,"blocking_trx_id":"..."}`，有锁等待时 status 至少为 warning<br>3. `check_replication(connection_id: str) -> dict`：返回 `{"status":"pass|warning|error","delay_seconds":<int>}`，延迟>10s 为 warning，>60s 为 error；适配器不支持则 `status="skipped"` |
| **前置依赖** | B-05（至少 MySQL 适配器） |
| **继承 TODO** | 无 |
| **状态** | 计划中 |

---

## B-16：Agent 工具——健康巡检类

| 字段 | 内容 |
|------|------|
| **ID** | B-16 |
| **标题** | 健康巡检工具集：get_metrics + 20 项检查逻辑 |
| **关联契约** | api-contract §1.4 POST /api/connections/{id}/health-check、§2.5 HealthReport；PRD §5.4 20+ 检查维度 |
| **输入** | B-04 BaseAdapter `get_metrics()`、B-15 故障排查工具 |
| **输出物** | `backend/app/agent/tools/health.py`、`backend/app/engine/health_check.py` |
| **验收标准** | 1. `health_check.py` 中 `HealthCheckEngine` 含 20 项检查注册表，每项含 `category/name/check_fn/threshold/suggestion`<br>2. `run_health_check(connection_id: str, check_items: list[str])` 异步生成器 yield 每个检查项结果（用于 SSE 流式推送）<br>3. 每个检查项结果为 `{current, total, item, status, value, threshold, suggestion}`<br>4. 健康评分公式：`score = floor((pass_count / total_checked) * 100)`，skipped 不计入分母<br>5. 评分分级：0-59=红色/严重、60-79=黄色/警告、80-100=绿色/健康<br>6. 总耗时 < 30s（20 项 × 1.5s/项上限）<br>7. 返回 `{"report_id":"...","score":<int>,"severity_counts":{error, warning, pass, skipped},"categories":[...]}` |
| **前置依赖** | B-14（共用适配器）、B-15（check_connections/check_locks 复用） |
| **继承 TODO** | 无 |
| **状态** | 计划中 |

---

## B-17：NL2SQL 引擎

| 字段 | 内容 |
|------|------|
| **ID** | B-17 |
| **标题** | NL2SQL Engine——Schema 上下文注入 + Prompt 模板 + LLM 生成 SQL |
| **关联契约** | api-contract §1.2 SSE `sql` 事件；PRD §5.1 NL2SQL 流程；backend AGENTS.md §提示（Prompt 模板定义） |
| **输入** | B-10 元数据 API（Schema 上下文来源） |
| **输出物** | `backend/app/engine/nl2sql.py`、`backend/app/prompts/nl2sql.py` |
| **验收标准** | 1. `generate_sql(natural_language: str, schema_context: dict, connection_id: str) -> dict` 返回 `{"sql":"...","explanation":"..."}`<br>2. Prompt 模板注入：目标数据库类型方言声明 + 表/列清单（含注释）+ 用户自然语言<br>3. Prompt 中不含实际数据行（依据 backend AGENTS.md §数据隐私）<br>4. LLM 响应解析：从 markdown 代码块中提取 SQL（正则 ` ```sql ... ``` `）<br>5. 生成的 SQL 经过 `sql_auditor.audit()` 校验<br>6. LLM 调用超时 15s，超时返回 `{"error":"NL2SQL generation timeout"}` |
| **前置依赖** | B-11（SQL Auditor）、B-12（Agent graph 调用入口） |
| **继承 TODO** | 无 |
| **状态** | 计划中 |

---

## B-18：诊断引擎——LLM 解析 EXPLAIN 输出

| 字段 | 内容 |
|------|------|
| **ID** | B-18 |
| **标题** | Diagnosis Engine——EXPLAIN 输出解析 + 索引建议生成 |
| **关联契约** | api-contract §1.3 POST /api/connections/{id}/explain、§2.4 DiagnosisResult |
| **输入** | B-14 explain_query 工具返回 |
| **输出物** | `backend/app/engine/diagnosis.py`、`backend/app/prompts/diagnosis.py` |
| **验收标准** | 1. `analyze_explain(explain_output: str, sql: str, db_type: str) -> dict` 返回 `{"bottleneck":"...","suggestion":"...","estimated_improvement":"...","is_destructive":bool}`<br>2. suggestion 中 `is_destructive=True` 的 SQL（CREATE INDEX/ALTER TABLE）标记 `# SUGGESTION` 注释<br>3. 识别以下瓶颈模式："全表扫描"/"filesort"/"temporary table"/"seq scan"<br>4. LLM 不可用时的降级方案：规则引擎返回 Explain 原始输出 + "请人工分析"（不崩溃） |
| **前置依赖** | B-17（共用 LLM 调用基础设施） |
| **继承 TODO** | api-contract §1.3 TODO：explain 解析 fallback 策略（当前实现：LLM + 规则降级） |
| **状态** | 计划中 |

---

## B-19：SSE 对话流 API

| 字段 | 内容 |
|------|------|
| **ID** | B-19 |
| **标题** | POST /api/chat/stream——SSE 流式对话核心端点 |
| **关联契约** | api-contract §1.2 POST /api/chat/stream（7 种 type 契约表）；backend AGENTS.md §SSE 流式格式 |
| **输入** | B-12 Agent Graph、B-03 Session/Message ORM |
| **输出物** | `backend/app/api/chat.py` |
| **验收标准** | 1. 接受 `{connection_id, message, mode, session_id, context}`<br>2. `session_id=null` 时创建新 Session（title 由首条消息截取前 30 字符生成）<br>3. 返回 `Content-Type: text/event-stream`，每条消息格式 `event: message\ndata: {json}\n\n`<br>4. 事件顺序严格遵守：`thinking` → `tool_call` → `tool_result`（可多轮）→ `sql` → `result` → `done`<br>5. `error` 事件后不发后续事件，直接 `done`<br>6. `done` 事件含 `session_id` + `tokens_used`<br>7. 120s 无消息自动 `done` 关闭<br>8. User Message 和 Assistant 响应写入 `messages` 表<br>9. `data_preview` 仅返回前 100 行（完整数据通过分页接口获取）<br>10. 首 Token（第一个 `thinking` 事件）延迟 < 2s |
| **前置依赖** | B-12、B-13、B-03 |
| **继承 TODO** | api-contract T-2（SSE 使用 POST + fetch ReadableStream） |
| **状态** | 计划中 |

---

## B-20：操作取消 API

| 字段 | 内容 |
|------|------|
| **ID** | B-20 |
| **标题** | POST /api/chat/cancel——取消进行中的 Agent 操作 |
| **关联契约** | api-contract §1.2 POST /api/chat/cancel；frontend AGENTS.md §1（长任务取消按钮） |
| **输入** | B-19 SSE 流管理 |
| **输出物** | `backend/app/api/chat.py` 中追加 `cancel_chat` 端点 |
| **验收标准** | 1. 接受 `{session_id}`，返回 204<br>2. 后端取消 Agent 执行：调用 `asyncio.Task.cancel()` 取消当前 session 的 Agent 协程<br>3. 若工具已在目标数据库执行 SQL，发送 `KILL QUERY <connection_id>`<br>4. 回滚未提交事务<br>5. 更新 Session status 为 `"closed"`<br>6. 取消后前端 SSE 流收到 `done` 事件（非 error 事件） |
| **前置依赖** | B-19 |
| **继承 TODO** | 无 |
| **状态** | 计划中 |

---

## B-21：直接查询与诊断 REST API

| 字段 | 内容 |
|------|------|
| **ID** | B-21 |
| **标题** | POST /api/connections/{id}/query + POST /api/connections/{id}/explain + GET /api/connections/{id}/slow-queries |
| **关联契约** | api-contract §1.3（query/explain/slow-queries 三个端点） |
| **输入** | B-11 SQL Auditor、B-05 Adapter |
| **输出物** | `backend/app/api/query.py`（或追加至 `connection.py`） |
| **验收标准** | 1. `POST /api/connections/{id}/query` 接收 `{sql, params, max_execution_ms}`，经审计后执行，返回契约 §1.3 QueryResult<br>2. SQL 审计拦截返回 400（非 500）<br>3. `POST /api/connections/{id}/explain` 接收 `{sql, format}`，返回 `{explain_output, parsed, format}`<br>4. `GET /api/connections/{id}/slow-queries?time_range=1h&limit=20&page=1&pageSize=20` 返回契约 §1.3 分页响应<br>5. 慢查询日志未启用时返回 200 + warning（非 500） |
| **前置依赖** | B-11、B-05、B-18（explain parsed 字段依赖诊断引擎） |
| **继承 TODO** | 无 |
| **状态** | 计划中 |

---

## B-22：健康巡检 SSE API

| 字段 | 内容 |
|------|------|
| **ID** | B-22 |
| **标题** | POST /api/connections/{id}/health-check——巡检 SSE 流 + GET /api/reports + GET /api/reports/{id} |
| **关联契约** | api-contract §1.4（health-check SSE + reports CRUD）、§2.5 HealthReport |
| **输入** | B-16 HealthCheckEngine |
| **输出物** | `backend/app/api/report.py` |
| **验收标准** | 1. `POST /api/connections/{id}/health-check` 接受 `{check_items, timeout_sec}`，返回 SSE 流<br>2. SSE 事件类型：`check_progress`/`check_warning`/`check_error`/`health_result`（区别于 chat SSE）<br>3. 巡检完成后报告持久化到 `reports` 表（ORM 模型由本任务新增）<br>4. `GET /api/reports?connection_id=...&page=1&pageSize=20` 返回历史报告列表<br>5. `GET /api/reports/{id}` 返回完整报告详情<br>6. 巡检总耗时 < 30s（每项 ≤ 1.5s），超时项标记 `skipped` 继续<br>7. 取消逻辑：接收 cancel → 完成当前检查项 → 生成部分报告标记 `status="partial"` |
| **前置依赖** | B-16、B-20（共用取消机制） |
| **继承 TODO** | api-contract T-9（报告导出格式——Phase 4+ 实现） |
| **状态** | 计划中 |

---

## B-23：故障排查 SSE API

| 字段 | 内容 |
|------|------|
| **ID** | B-23 |
| **标题** | POST /api/connections/{id}/troubleshoot——故障排查 SSE 流 |
| **关联契约** | api-contract §1.5 POST /api/connections/{id}/troubleshoot、§2.6 TroubleshootResult |
| **输入** | B-15 故障排查工具、B-12 Agent Graph |
| **输出物** | `backend/app/agent/tools/troubleshoot.py` 中追加 TroubleshootWorkflow，`backend/app/api/chat.py` 中追加端点 |
| **验收标准** | 1. 接受 `{issue_type, context}`，"auto" 时自动检测（依次 check_connections → check_locks → check_replication）<br>2. SSE 流事件类型：`thinking`/`tool_call`/`tool_result`/`diagnosis`<br>3. `diagnosis` 事件含 `{conclusion, severity, suggestion, suggestion_is_destructive}`<br>4. 工具调用超时（>10s）跳过当前步骤，发 `skip` 事件继续下一步<br>5. `suggestion_is_destructive: true` 时前端渲染需确认按钮<br>6. 排查完成后结论写入当前 Session 的 messages |
| **前置依赖** | B-15、B-19（共用 SSE 基础设施） |
| **继承 TODO** | 无 |
| **状态** | 计划中 |

---

## B-24：会话生命周期管理

| 字段 | 内容 |
|------|------|
| **ID** | B-24 |
| **标题** | Session 空闲超时清理 + 历史消息查询 API |
| **关联契约** | api-contract §2.2 Session 约束（空闲 30min 关闭）；backend AGENTS.md §安全与合规红线（会话超时清理） |
| **输入** | B-03 Session/Message ORM |
| **输出物** | `backend/app/models/session.py` 中追加 `SessionManager` 类，`backend/app/api/chat.py` 中追加 `GET /api/sessions` / `GET /api/sessions/{id}/messages` |
| **验收标准** | 1. 后台 `asyncio.Task` 每 60s 扫描 `status="active"` 且 `last_active_at < NOW() - 30min` 的 Session，更新 `status="closed"`<br>2. 关闭时清除内存中的连接凭据<br>3. `GET /api/sessions?connection_id=...&status=active` 返回会话列表<br>4. `GET /api/sessions/{id}/messages?page=1&pageSize=50` 返回消息历史（不含 `result_preview` 中的完整数据行——仅返回前 20 行预览）<br>5. 跨会话数据隔离：查询 Session A 的消息不会返回 Session B 的结果数据 |
| **前置依赖** | B-03、B-19 |
| **继承 TODO** | 无 |
| **状态** | 计划中 |

---

## B-25：报告导出 API

| 字段 | 内容 |
|------|------|
| **ID** | B-25 |
| **标题** | GET /api/reports/{id}/export——导出巡检报告（HTML/PDF） |
| **关联契约** | api-contract §1.4 GET /api/reports/{id}/export、T-9（报告导出格式） |
| **输入** | B-22 HealthReport 数据 |
| **输出物** | `backend/app/api/report.py` 中追加 `export_report` 端点 |
| **验收标准** | 1. `GET /api/reports/{id}/export?format=html` 返回 HTML 报告（Content-Type: text/html）<br>2. `GET /api/reports/{id}/export?format=pdf` 返回 PDF 文件（Content-Disposition: attachment）<br>3. HTML 模板用 Jinja2 渲染，含健康评分环形图（内联 CSS）、分类检查项列表（三色标记）<br>4. PDF 用 WeasyPrint 从 HTML 生成<br>5. 导出文件含水印：`Generated by DB-Pilot | <timestamp> UTC` （用户标识待 T-3 决策后追加） |
| **前置依赖** | B-22 |
| **继承 TODO** | api-contract T-3（导出水印 User 字段）、T-9（后端生成方案） |
| **状态** | 计划中 |

---

## B-26：LLM 调用基础设施

| 字段 | 内容 |
|------|------|
| **ID** | B-26 |
| **标题** | LLM Client 封装——Anthropic/OpenAI 统一接口 + 流式输出 |
| **关联契约** | backend AGENTS.md §LLM 集成（Claude 默认，OpenAI 兼容） |
| **输入** | B-01 config.py（LLM_API_KEY, LLM_MODEL 等） |
| **输出物** | `backend/app/engine/llm_client.py` |
| **验收标准** | 1. `LLMClient` 类支持 `chat(messages, model, stream=True) -> AsyncIterator[str]`<br>2. 默认使用 Anthropic SDK；环境变量 `LLM_PROVIDER=openai` 切换为 OpenAI SDK<br>3. 流式输出 chunk 统一为 `{"type":"text_delta","text":"..."}` 格式<br>4. `LLM_CLASSIFIER_MODEL` 独立配置用于 Intent 分类（默认 Haiku）<br>5. `LLM_MAIN_MODEL` 用于 NL2SQL/诊断/故障排查（默认 Opus）<br>6. token 消耗统计返回给调用方（用于 Session.tokens_used_total 累加）<br>7. API Key 硬编码检查：grep `SK-` / `sk-ant-` 无匹配 |
| **前置依赖** | B-01 |
| **继承 TODO** | 无 |
| **状态** | 计划中 |

---

## B-27：pytest 集成测试——适配器层

| 字段 | 内容 |
|------|------|
| **ID** | B-27 |
| **标题** | 适配器层集成测试（MySQL 连接 → 元数据 → 查询 → 诊断） |
| **关联契约** | backend AGENTS.md §验证与测试要求（每个适配器类型至少一个集成测试） |
| **输入** | B-05 MySQLAdapter |
| **输出物** | `backend/tests/test_adapter_mysql.py`、`backend/tests/conftest.py`（共享 fixtures） |
| **验收标准** | 1. `conftest.py` 提供 `mysql_test_config` fixture（从环境变量 `TEST_MYSQL_URL` 读取）<br>2. `test_mysql_connect()`：连接成功返回 True<br>3. `test_mysql_get_tables()`：返回非空表列表<br>4. `test_mysql_get_columns()`：指定表返回列列表含 name/type/is_primary 字段<br>5. `test_mysql_execute_select()`：SELECT 1 返回 `{"columns":["1"],"rows":[[1]]}`<br>6. `test_mysql_explain()`：EXPLAIN SELECT 1 返回非空字符串<br>7. 所有测试可通过 `pytest -m "integration" --no-header -q` 运行<br>8. 连接到测试数据库失败时 SKIP 而非 FAIL（`pytest.skip("TEST_MYSQL_URL not configured")`） |
| **前置依赖** | B-05 |
| **继承 TODO** | 无 |
| **状态** | 计划中 |

---

## B-28：pytest 集成测试——API 层全链路

| 字段 | 内容 |
|------|------|
| **ID** | B-28 |
| **标题** | API 层全链路测试（创建连接 → 测试 → 查询 → 诊断 → 巡检 → 会话 → 取消） |
| **关联契约** | backend AGENTS.md §验证与测试要求（每个 API 端点至少一个 pytest 测试） |
| **输入** | B-08-B-25 全部 API 端点 |
| **输出物** | `backend/tests/test_api_connections.py`、`backend/tests/test_api_chat.py`、`backend/tests/test_api_health.py`、`backend/tests/test_api_troubleshoot.py` |
| **验收标准** | 1. `test_create_connection_201()`：POST /api/connections 返回 201<br>2. `test_create_connection_invalid_db_type_422()`：POST 非法 db_type 返回 422<br>3. `test_test_connection_200()`：POST /connections/{id}/test 返回 success=true<br>4. `test_execute_query_audit_blocked()`：POST /connections/{id}/query 含 DROP 返回 400<br>5. `test_chat_stream_200()`：POST /api/chat/stream 返回 SSE 流，首个事件 type=thinking<br>6. `test_chat_cancel_204()`：POST /api/chat/cancel 返回 204<br>7. `test_health_check_sse()`：巡检 SSE 流以 health_result 事件结束<br>8. `test_session_timeout()`：模拟 30min 无操作后 session status 变为 closed<br>9. 全链路测试使用 `httpx.AsyncClient` + `pytest-asyncio` |
| **前置依赖** | B-19、B-21、B-22、B-23、B-24 |
| **继承 TODO** | api-contract T-1（响应格式按方案 C 验证）、T-3（当前无鉴权头） |
| **状态** | 计划中 |

---

## 任务依赖拓扑图

```
B-01 ──────────────────────────────────────────────────┐
  ├── B-02 ──┐                                         │
  │          ├── B-04 ──┬── B-05 ──┬── B-09 ──┐       │
  │          │          ├── B-06 ──┤          │       │
  │          │          ├── B-07 ──┘          │       │
  │          │          └── B-10             │       │
  │          └── B-08 ───────────┬───────────┘       │
  ├── B-03 ──┬───────────────────┼── B-24            │
  │          └── B-19 ───────────┤                    │
  ├── B-11 ──┬── B-13 ──────────┤                    │
  │          ├── B-17 ───────────┤                    │
  │          └── B-21 ───────────┤                    │
  ├── B-12 ──┬── B-17 ───────────┤                    │
  │          ├── B-18 ────────────┤                    │
  │          ├── B-13 ────────────┤                    │
  │          └── B-19 ────────────┤                    │
  ├── B-14 ──┬── B-18 ────────────┤                    │
  │          ├── B-21 ────────────┤                    │
  │          └── B-22 ────────────┤                    │
  ├── B-15 ──┬── B-23 ────────────┤                    │
  │          └── B-16             │                    │
  ├── B-16 ─── B-22 ──────────────┤                    │
  ├── B-26 ───────────────────────┼── B-17/B-18       │
  ├── B-20 ───────────────────────┤                    │
  ├── B-25 ───────────────────────┘                    │
  ├── B-27 ────────────────────────────────────────────┤
  └── B-28 ────────────────────────────────────────────┘
```

图例：B-01→B-03 为基础设施层 → B-04→B-07 为适配器层 → B-08→B-10 为 API 薄路由层 → B-11→B-18 为引擎层 → B-19→B-25 为集成层 → B-27→B-28 为测试层。
