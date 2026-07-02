# DBA Agent 前后端技术契约草案

> 版本：v0.1 · 日期：2026-06-30 · 状态：草案待审  
> 依据：[PRD](../PRD.md)、[frontend/AGENTS.md](../frontend/AGENTS.md)

---

## 一、核心 API 列表

### 1.1 连接管理（依据 PRD §3.1「多数据库管理」、§6.2 BaseAdapter 连接方法）

| 方法 | 路径 | 说明 | 依据 |
|------|------|------|------|
| `GET` | `/api/connections` | 获取已保存连接列表 | PRD §7 connection.py |
| `POST` | `/api/connections` | 创建新连接 | PRD §7 connection.py |
| `GET` | `/api/connections/{id}` | 获取连接详情（不含密码） | PRD §8.2 凭据安全 |
| `PUT` | `/api/connections/{id}` | 更新连接配置 | PRD §7 connection.py |
| `DELETE` | `/api/connections/{id}` | 删除连接 | PRD §7 connection.py |
| `POST` | `/api/connections/{id}/test` | 测试连接可用性 | PRD §10.2「连接测试提供明确反馈」 |
| `GET` | `/api/connections/{id}/metadata` | 获取连接元数据（库/表/列/索引） | PRD §5.1 NL2SQL Schema 注入；frontend AGENTS.md §4 补全来源 |

#### GET /api/connections

**响应**：

```
{
  "items": [
    {
      "id": "conn_abc123",
      "name": "生产 MySQL",
      "db_type": "mysql",
      "host": "10.0.1.100",
      "port": 3306,
      "database": "orders",
      "user": "readonly_user",
      "ssl_enabled": true,
      "created_at": "2026-06-30T10:00:00Z",
      "last_tested_at": "2026-06-30T14:32:00Z",
      "status": "healthy"
    }
  ],
  "total": 1,
  "page": 1,
  "pageSize": 20
}
```

- [ ] MUST `password` 字段不出现在任何连接列表/详情响应中（依据 PRD §8.2）
- [ ] MUST `status` 枚举：`unknown`（未测试）、`healthy`（可用）、`unreachable`（不可达）、`degraded`（部分功能受限——如复制断开但主库可查）

#### POST /api/connections

**请求体**：

```
{
  "name": "生产 MySQL",
  "db_type": "mysql",                          // "mysql" | "postgresql" | "oracle"
  "host": "10.0.1.100",
  "port": 3306,
  "database": "orders",
  "user": "readonly_user",
  "password": "s3cret",                        // 仅创建/更新时可传入，不在响应中返回
  "ssl_enabled": true,
  "ssl_ca_cert": "-----BEGIN CERTIFICATE-----..." // 可选
}
```

- [ ] MUST 创建时校验 `db_type` 是否为支持的值（`mysql`/`postgresql`/`oracle`）
- [ ] MUST `port` 若未传则按 `db_type` 填默认值（3306 / 5432 / 1521）
- <!-- TODO: 需人工确认：连接串是否支持 Unix socket 或 Cloud SQL Proxy 等非 TCP 方式？PRD 未提及 -->

#### POST /api/connections/{id}/test

**响应**：

```
{
  "success": true,
  "latency_ms": 42,
  "version": "MySQL 8.0.35",
  "capabilities": {
    "supports_explain": true,
    "supports_slow_query_log": true,
    "supports_replication": true,
    "supports_table_spaces": false
  }
}
```

- [ ] MUST 测试连接时使用独立短生命周期连接，不在测试中泄露密码（依据 PRD §8.2）
- [ ] MUST 失败时返回结构化错误（`error_code` + `user_message`），不回显密码（依据 frontend AGENTS.md §2"连接测试时错误信息中 MUST NOT 包含密码原文"）

<!-- TODO: 需人工确认：连接测试是否需要后端记忆上次测试结果并持久化，还是仅即时返回？-->

#### GET /api/connections/{id}/metadata

**响应**：

```
{
  "databases": ["orders", "analytics"],
  "tables": [
    {
      "database": "orders",
      "table_name": "users",
      "comment": "用户表",
      "row_count_estimate": 1500000,
      "columns": [
        {"name": "id", "type": "bigint", "nullable": false, "is_primary": true, "comment": "用户ID"},
        {"name": "username", "type": "varchar(64)", "nullable": false, "is_primary": false, "comment": "用户名"},
        {"name": "password_hash", "type": "varchar(256)", "nullable": false, "is_primary": false, "comment": ""}
      ],
      "indexes": [
        {"name": "PRIMARY", "columns": ["id"], "is_unique": true, "type": "BTREE"},
        {"name": "idx_username", "columns": ["username"], "is_unique": true, "type": "BTREE"}
      ]
    }
  ]
}
```

- [ ] MUST 前端缓存此响应 5 分钟（依据 frontend AGENTS.md §4）
- [ ] MUST 后端不对此 endpoint 做超限保护——元数据拉取频率低但数据量大

---

### 1.2 对话与 Agent 交互（依据 PRD §4.2 Agent 工作流、§5.1 NL2SQL 流程）

| 方法 | 路径 | 说明 | 依据 |
|------|------|------|------|
| `POST` | `/api/chat/stream` | **SSE** 流式对话 | PRD §7 chat.py |
| `POST` | `/api/chat/cancel` | 取消当前正在执行的操作 | frontend AGENTS.md §1「所有超过 5s 的操作提供取消按钮」 |

#### POST /api/chat/stream （SSE）

**请求体**：

```
{
  "connection_id": "conn_abc123",
  "message": "最近一小时慢查询有哪些？",
  "mode": "natural_language",                  // "natural_language" | "sql_editor"
  "session_id": null,                          // null=新会话，非空=续接已有会话
  "context": {                                 // 可选：前端附加上下文
    "selected_table": "orders",                // 用户当前关注的表（NL2SQL 优先用此表）
    "user_role": "standard"                    // 依据 PRD §8.3: "readonly" | "standard" | "admin"
  }
}
```

**SSE 事件流**（`Content-Type: text/event-stream`）：

```
event: message
data: {"type":"thinking","content":"分析用户意图：DIAGNOSIS，检测关键词'慢查询'，时间范围 1h"}

event: message
data: {"type":"tool_call","tool":"get_slow_queries","args":{"time_range":"1h","limit":50},"display":"正在查询最近1小时慢查询日志..."}

event: message
data: {"type":"tool_result","tool":"get_slow_queries","summary":"返回 12 条慢查询记录","duration_ms":320}

event: message
data: {"type":"tool_call","tool":"explain_query","args":{"sql":"SELECT * FROM orders WHERE status='pending'"},"display":"正在分析执行计划..."}

event: message
data: {"type":"sql","content":"SELECT u.username, COUNT(*) as cnt FROM orders o JOIN users u ON u.id = o.user_id WHERE o.created_at > NOW() - INTERVAL 1 HOUR GROUP BY u.username ORDER BY cnt DESC LIMIT 50;","audit_status":"passed","is_readonly":true}

event: message
data: {"type":"result","summary":"查询完成：返回 50 行，耗时 1.2s","data_preview":{"columns":["username","cnt"],"rows":[["alice",42],["bob",35]],"total_rows":50},"duration_ms":1200}

event: message
data: {"type":"done","session_id":"sess_xyz789","tokens_used":1240}
```

**SSE 事件类型契约**：

| `type` | 触发场景 | payload 必含字段 | 前端响应动作 | 依据 |
|---------|---------|-----------------|-------------|------|
| `thinking` | Agent 每一步推理（ReAct 模式下为 LLM 流式推理文本） | `content`（string），可选 `agent_run_id`、`iteration`、`reasoning_type` | 可折叠区域追加文本 | PRD §4.2 Step 5 |
| `tool_call` | Agent 调用工具前 | `tool`、`args`、`display`，可选 `agent_run_id`、`iteration` | 进度步骤列表追加 | PRD §4.2 Step 4 |
| `tool_result` | 工具返回后 | `tool`、`summary`、`duration_ms`，可选 `agent_run_id`、`iteration`、`safety_checks_passed` | 步骤标记 ✅ + 耗时 | PRD §4.2 Step 4 |
| `sql` | Agent 生成 SQL 后 | `content`、`audit_status`、`is_readonly`，可选 `agent_run_id`、`iteration` | SQL Block 组件渲染 | PRD §5.1 流程 Step 4-5 |
| `result` | 最终结果 | `summary`、`data_preview`、`duration_ms`，可选 `agent_run_id`、`trace_summary` | 结果表格渲染 | PRD §5.1 流程 Step 7 |
| `error` | 执行出错 | `error_code`、`user_message`、`severity`，可选 `agent_run_id`、`iteration` | 错误卡片展示 | PRD §10.2 |
| `done` | 流正常结束 | `session_id`、`tokens_used`，可选 `agent_run_id`、`total_iterations`、`trace_summary` | 关闭 SSE 连接 | PRD §4.2 Step 6 |

- [ ] MUST 后端确保先发 `sql` 后发 `result`——绝不可跳过 `sql` 直接发 `result`（前端依赖 `sql` 事件渲染代码块）
- [ ] MUST `error` 事件后不再发送任何后续事件，以 `done` 结束（`session_id` 仍返回以持久化错误会话）
- [ ] MUST `data_preview` 仅返回前 100 行预览，完整结果通过分页接口 `/api/chat/result/{session_id}` 获取
- [ ] MUST SSE 单次连接最长 120s 无消息则服务端发送 `done` 并关闭（依据 backend AGENTS.md SSE 超时约束）

**2026-07-02 Agent 架构升级说明**：
- Agent 现在基于 LangGraph ReAct 模式，`thinking` → `tool_call` → `tool_result` 可在同一轮对话中**多轮循环**（最多 10 轮），前端需自适应展示动态数量的工具调用步骤
- 所有事件新增可选字段 `agent_run_id`（UUID）、`iteration`（第几轮）——这些字段缺失时前端不做特殊处理
- `done` 事件新增可选字段 `total_iterations`（总共几轮）、`trace_summary`（决策链路摘要）——用于调试面板展示
- `thinking` 事件内容从静态中文文本变为 LLM 流式推理过程（逐 token 推送），建议前端以打字机效果或可折叠区域展示

#### POST /api/chat/cancel

**请求体**：

```
{
  "session_id": "sess_xyz789"
}
```

**响应**：204 No Content

- [ ] MUST 后端接收取消信号后中止当前 Agent 工具调用（若工具已在目标数据库执行 SQL，发送 `KILL QUERY`），并回滚未提交事务
- [ ] MUST 前端在收到 204 后清空当前流式内容中未完成步骤的展示

---

### 1.3 查询与诊断（依据 PRD §5.1、§5.2）

| 方法 | 路径 | 说明 | 依据 |
|------|------|------|------|
| `POST` | `/api/connections/{id}/query` | 执行只读 SQL（直接模式） | PRD §5.1 |
| `POST` | `/api/connections/{id}/explain` | 获取 SQL 执行计划 | PRD §5.2 |
| `GET` | `/api/connections/{id}/slow-queries` | 获取慢查询列表 | PRD §5.2 |

#### POST /api/connections/{id}/query

**请求体**：

```
{
  "sql": "SELECT username, COUNT(*) as cnt FROM users GROUP BY username ORDER BY cnt DESC LIMIT 10",
  "params": {},                                 // 可选：参数化查询值
  "max_execution_ms": 30000                     // 默认 30s（依据 PRD §8.1 Layer 4）
}
```

**响应**：

```
{
  "columns": ["username", "cnt"],
  "rows": [["alice", 42], ["bob", 35]],
  "total_rows": 50,
  "execution_time_ms": 320,
  "is_readonly": true,
  "audit_status": "passed"
}
```

- [ ] MUST 后端在适配器层根据 `ConnectionConfig` 创建连接时，默认设置 `autocommit=True` 且 `readonly=True`（依据 PRD §8.1 Layer 1）
- [ ] MUST 所有 SQL 经过 `sql_auditor.audit()` 校验后执行，拦截结果以 HTTP 400 返回（非 500）（依据 PRD §8.1 Layer 2）

#### POST /api/connections/{id}/explain

**请求体**：

```
{
  "sql": "SELECT * FROM orders WHERE user_id = 123",
  "format": "tree"                             // "tree" | "json" | "traditional"（各数据库方言自动映射）
}
```

**响应**：

```
{
  "explain_output": "-> Filter: (orders.user_id = 123)\n    -> Table scan on orders  (rows=1495832)",
  "parsed": {
    "total_cost_estimate": "扫描 1495832 行",
    "bottleneck": "全表扫描——user_id 列缺少索引",
    "suggestion": "CREATE INDEX idx_orders_user_id ON orders(user_id);",
    "estimated_improvement": "扫描行数将从 1495832 降至 <10"
  },
  "format": "tree"
}
```

- [ ] MUST `parsed.suggestion` 字段由 LLM 根据 EXPLAIN 原始输出分析生成，但 MUST 以 `# SUGGESTION` 标记区别于确定性信息
- <!-- TODO: 需人工确认：explain 解析由后端 LLM 还是纯规则引擎完成？PRD §5.2 描述"AI 自动分析"倾向 LLM，但需明确 fallback 策略（LLM 不可用时的降级方案） -->

#### GET /api/connections/{id}/slow-queries

**Query 参数**：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `time_range` | string | `"1h"` | 时间范围（1h/6h/24h/7d） |
| `limit` | int | 20 | 最大 100（依据 PRD §10.1） |
| `sort_by` | string | `"query_time"` | 排序字段 |
| `page` | int | 1 | 页码 |
| `pageSize` | int | 20 | 每页条数，最大 100 |

**响应**：

```
{
  "items": [
    {
      "id": "slow_001",
      "sql_text": "SELECT * FROM orders WHERE status = 'pending' ORDER BY created_at",
      "query_time_sec": 2.3,
      "lock_time_sec": 0.05,
      "rows_examined": 1495832,
      "rows_sent": 50,
      "executed_at": "2026-06-30T14:28:00Z"
    }
  ],
  "total": 12,
  "page": 1,
  "pageSize": 20
}
```

- [ ] MUST 若目标数据库未开启慢查询日志，返回 `{"items":[], "warning":"slow_query_log 未启用"}`——返回 200 而非 500

---

### 1.4 健康巡检（依据 PRD §5.4）

| 方法 | 路径 | 说明 | 依据 |
|------|------|------|------|
| `POST` | `/api/connections/{id}/health-check` | 触发健康巡检 | PRD §5.4 |
| `GET` | `/api/reports` | 获取历史巡检报告列表 | PRD §7 report.py |
| `GET` | `/api/reports/{id}` | 获取单份巡检报告详情 | PRD §5.4 |
| `GET` | `/api/reports/{id}/export` | 导出巡检报告 | PRD §5.4 |

#### POST /api/connections/{id}/health-check

**请求体**：

```
{
  "check_items": ["all"],                      // 或指定检查项列表 ["connections","storage","replication","security"]
  "timeout_sec": 30                            // 默认 30s（依据 PRD §10.1 巡检报告耗时 < 30s）
}
```

**响应**（SSE 流式——逐项返回检查进度）：

```
event: message
data: {"type":"check_progress","current":1,"total":20,"item":"连接数使用率","status":"passing"}

event: message
data: {"type":"check_progress","current":2,"total":20,"item":"磁盘使用率","status":"passing"}

event: message
data: {"type":"check_warning","current":7,"total":20,"item":"慢查询占比","status":"warning","value":"12.5%","threshold":"<5%","suggestion":"存在 12 条慢查询，建议逐一分析"}

event: message
data: {"type":"check_error","current":15,"total":20,"item":"主从延迟","status":"error","value":"45s","threshold":"<10s","suggestion":"从库延迟严重，检查 IO 线程状态"}

event: message  
data: {"type":"health_result","report_id":"rpt_20260630_001","score":72,"summary":"3 项异常（1 严重 + 2 警告），详见报告","severity_counts":{"error":1,"warning":2,"pass":17,"skipped":0}}
```

**完整报告 GET 响应**：

```
{
  "id": "rpt_20260630_001",
  "connection_id": "conn_abc123",
  "score": 72,
  "generated_at": "2026-06-30T15:00:00Z",
  "duration_sec": 18.5,
  "severity_counts": {"error": 1, "warning": 2, "pass": 17, "skipped": 0},
  "categories": [
    {
      "name": "连接",
      "items": [
        {"name":"连接数使用率","status":"pass","value":"45/200 (22.5%)","threshold":"<80%","suggestion":null},
        {"name":"连接异常比例","status":"pass","value":"0.3%","threshold":"<5%","suggestion":null}
      ]
    },
    {
      "name": "复制",
      "items": [
        {"name":"主从延迟","status":"error","value":"45s","threshold":"<10s","suggestion":"从库延迟严重，检查 IO 线程状态"},
        {"name":"IO/SQL线程状态","status":"pass","value":"正常运行","threshold":null,"suggestion":null}
      ]
    }
    // ... 其余类别
  ]
}
```

- [ ] MUST 健康评分 0-100 的计算逻辑在后端实现，前端仅展示（依据 PRD §5.4）
- [ ] MUST 巡检 SSE 流每个 `check_item` 耗时不超过 1.5s（保证 20 项 < 30s，依据 PRD §10.1）
- [ ] MUST 巡检过程中用户取消（POST /api/chat/cancel），已完成检查项不丢弃——生成部分报告并标注"巡检中断于第 N/20 项"

---

### 1.5 故障排查（依据 PRD §5.3）

| 方法 | 路径 | 说明 | 依据 |
|------|------|------|------|
| `POST` | `/api/connections/{id}/troubleshoot` | 触发故障排查工作流 | PRD §5.3 |

#### POST /api/connections/{id}/troubleshoot

**请求体**：

```
{
  "issue_type": "auto",                        // "auto" 自动检测 | "deadlock" | "connection_flood" | "replication_lag" | "slow_performance" | "disk_full"
  "context": "数据库从 14:30 开始响应变慢，用户投诉订单查询超时"
}
```

**响应**（SSE 流式——按排查步骤逐条返回）：

```
event: message
data: {"type":"thinking","content":"开始故障排查：优先检查连接数和锁等待"}

event: message
data: {"type":"tool_call","tool":"check_connections","display":"检查连接池状态..."}

event: message
data: {"type":"tool_result","tool":"check_connections","summary":"连接数 45/200，正常","status":"pass"}

event: message
data: {"type":"tool_call","tool":"check_locks","display":"检查锁等待..."}

event: message
data: {"type":"tool_result","tool":"check_locks","summary":"发现 3 个锁等待","status":"warning","detail":{"waiting_transactions":3,"blocking_trx_id":"82491","blocked_queries":2,"blocking_duration_sec":480}}

event: message
data: {"type":"diagnosis","conclusion":"事务 #82491 长时间未提交导致锁等待","severity":"error","suggestion":"检查事务 #82491 状态，必要时执行 KILL 82491;","suggestion_is_destructive":true}
```

- [ ] MUST `suggestion_is_destructive: true` 的前端渲染为需确认按钮（依据 frontend AGENTS.md §1）
- [ ] MUST 排查过程中任何工具调用超时（>10s）时，跳过该步骤发送 `skip` 事件并继续下一步

---

### 1.6 错误码体系

<!-- TODO: 需人工确认：以下错误码方案基于 PRD §10.2"错误信息友好可读"推导，PRD 未定义具体编码规范。候选方案：
  A. 模块码 + 状态码：`DB_001` / `SQL_002` / `AUTH_001`（中台风格）
  B. HTTP 状态码 + detail 字段：422 + {"field":"sql","reason":"syntax_error"}（RESTful 风格，FastAPI 默认）
  C. RFC 7807 Problem Details：{"type":"...","title":"...","status":422,"detail":"...","instance":"..."}
  默认选用方案 B（FastAPI 原生），以下按方案 B 定义。
-->

| HTTP 状态码 | 场景 | 响应体结构 | 依据 |
|------------|------|-----------|------|
| `200` | 查询/诊断/巡检成功 | 标准数据体 | PRD §10.2 |
| `201` | 连接/资源创建成功 | 创建后的资源对象 | REST 惯例 |
| `204` | 取消/删除成功 | 空体 | REST 惯例 |
| `400` | SQL 审计拦截 | `{"error_code":"SQL_AUDIT_BLOCKED","user_message":"该 SQL 包含危险操作：DROP TABLE","sql_highlight":"DROP TABLE","severity":"warning"}` | PRD §8.1 Layer 2 |
| `400` | 参数校验失败 | `{"error_code":"INVALID_PARAM","user_message":"请填写端口号","field":"port"}` | frontend AGENTS.md §3「用户可自行修正的错误」 |
| `401` | <!-- TODO: 需人工确认：若采用无认证方案，此状态码不存在 --> | | |
| `403` | 权限不足（如只读用户尝试写操作） | `{"error_code":"PERMISSION_DENIED","user_message":"您的角色为只读用户，无法执行写操作","required_role":"admin"}` | PRD §8.3 |
| `404` | 连接/会话/报告不存在 | `{"error_code":"NOT_FOUND","user_message":"连接 conn_xyz 不存在或已删除"}` | REST 惯例 |
| `408` | SQL 执行超时（>30s） | `{"error_code":"EXECUTION_TIMEOUT","user_message":"查询超时（30s），建议添加索引或缩小查询范围","killed":true}` | PRD §8.1 Layer 4 |
| `422` | 请求体验证失败（Pydantic） | FastAPI 默认 `{"detail":[{"loc":["body","db_type"],"msg":"value is not a valid enumeration member"}]}` | FastAPI 默认行为 |
| `500` | Agent/LLM 故障 | `{"error_code":"AGENT_ERROR","user_message":"AI 服务暂时不可用，请稍后重试","severity":"error","retry_after_sec":60}` | PRD §10.2「系统故障」 |
| `502` | 目标数据库不可达 | `{"error_code":"DB_UNREACHABLE","user_message":"无法连接到目标数据库 10.0.1.100:3306，请检查网络和防火墙","severity":"error"}` | PRD §10.2「连接断开」反馈 |
| `503` | SSE 连接数超限 | `{"error_code":"TOO_MANY_STREAMS","user_message":"当前并发会话数已达上限，请等待或取消其他会话","severity":"warning"}` | <!-- TODO: 需人工确认：并发限制的数值 --> |

- [ ] MUST 所有 4xx/5xx 响应体必须包含 `user_message` 字段，其内容面向最终用户（非开发者）（依据 PRD §10.2）
- [ ] MUST 错误堆栈不出现在响应体中——生产环境 `server_error` 仅记录服务端日志
- [ ] MUST 统一响应头 `X-Request-ID`（UUID v4），便于前后端联调问题追踪

---

## 二、关键实体数据模型

### 2.1 连接相关

#### ConnectionConfig（依据 PRD §6.2）

```
{
  "id": "conn_abc123",                 // string, UUID v4, 后端生成
  "name": "生产 MySQL",                // string, 1-64 字符, required
  "db_type": "mysql",                  // enum: "mysql" | "postgresql" | "oracle", required
  "host": "10.0.1.100",               // string, IP 或域名, required
  "port": 3306,                        // int, 1-65535, 默认按 db_type
  "database": "orders",               // string, required
  "user": "readonly_user",            // string, required
  "password": "***",                   // string, 仅在创建/更新请求中传入, 响应中永远为 "***"
  "ssl_enabled": true,                 // bool, default false
  "ssl_ca_cert": null,                 // string | null, PEM 格式
  "extra_params": {                    // object | null, 额外连接参数（如 charset, connect_timeout）
    "charset": "utf8mb4"
  },
  "created_at": "2026-06-30T10:00:00Z",  // datetime, ISO 8601
  "updated_at": "2026-06-30T14:00:00Z",  // datetime, ISO 8601
  "last_tested_at": "2026-06-30T14:32:00Z",  // datetime | null
  "status": "healthy"                  // enum: "unknown" | "healthy" | "unreachable" | "degraded"
}
```

**约束**：
- [ ] MUST `name` 在单个用户/会话范围内唯一
- [ ] MUST `password` 字段在服务端仅存于内存，不持久化到数据库（依据 PRD §8.2）
- [ ] MUST `db_type` 校验枚举值，拒绝未支持的类型

#### ConnectionStatus（依据 PRD §6.2 `get_connections_status()`）

```
{
  "total_connections": 200,            // int, 最大连接数
  "active_connections": 45,            // int, 当前活跃连接
  "idle_connections": 30,              // int
  "waiting_connections": 3,            // int, 等待获取连接的请求
  "usage_percent": 22.5,               // float
  "aborted_connections_rate": 0.3,     // float, 百分比
  "sampled_at": "2026-06-30T14:30:00Z"
}
```

### 2.2 会话相关

#### Session（依据 PRD §4.2 Agent 工作流、§8.2 会话超时）

```
{
  "id": "sess_xyz789",                 // string, UUID v4
  "connection_id": "conn_abc123",      // string, 关联的连接
  "title": "慢查询诊断 14:30",          // string, 由首条用户消息自动生成
  "created_at": "2026-06-30T14:30:00Z",
  "last_active_at": "2026-06-30T14:45:00Z",
  "status": "active",                  // enum: "active" | "idle" | "closed"
  "message_count": 12,                  // int
  "tokens_used_total": 8420            // int, 累计 token 消耗（用于成本追踪）
}
```

**约束**：
- [ ] MUST 会话空闲 30 分钟后自动 `status = "closed"`（依据 PRD §8.2）
- [ ] MUST 关闭的会话仍可读取历史消息（前端可回看），但不可发送新消息
- [ ] MUST 历史消息中的目标数据库查询结果不跨会话可读——每个会话独立数据作用域

#### Message（依据 PRD §5.1 对话流程）

```
{
  "id": "msg_001",                     // string
  "session_id": "sess_xyz789",
  "role": "user",                      // enum: "user" | "assistant" | "system"
  "content": "最近一小时慢查询有哪些？", // string, 纯文本
  "message_type": "natural_language",  // enum: "natural_language" | "sql" | "diagnosis" | "troubleshoot" | "health_check"
  "sql_generated": null,               // string | null, LLM 生成的 SQL
  "sql_executed": null,                // string | null, 实际执行的 SQL（可能经改写）
  "result_preview": null,              // object | null, 查询结果摘要（不超过 20 行）
  "error_info": null,                  // object | null, 失败时 {"error_code":"...","user_message":"..."}
  "created_at": "2026-06-30T14:30:05Z",
  "tokens_used": 340                   // int
}
```

### 2.3 查询结果

#### QueryResult（依据 PRD §5.1 流程 Step 6-7）

```
{
  "columns": [
    {"name": "username", "type": "varchar(64)", "is_sensitive": false},
    {"name": "password_hash", "type": "varchar(256)", "is_sensitive": true}
  ],
  "rows": [["alice", "***"], ["bob", "***"]],
  "total_rows": 50,
  "returned_rows": 20,                 // 前端预览行数（默认 100，分页获取完整数据）
  "execution_time_ms": 320,
  "audit_status": "passed",
  "is_readonly": true
}
```

**约束**：
- [ ] MUST 后端在返回结果前对 `is_sensitive: true` 的列执行脱敏处理，前端不可信——即前端脱敏为防御层，后端为强制层（依据 frontend AGENTS.md §2）
- [ ] MUST `rows` 中 `is_sensitive: true` 的列后端已替换为 `"***"`——前端仅对未标记 `is_sensitive` 但列名匹配敏感模式的列做二次检查（依据 frontend AGENTS.md §2 脱敏规则）
- [ ] SHOULD 后端脱敏逻辑通过 `adapter.get_column_sensitivity()` 获取列敏感标记，若适配器不支持则用列名正则 fallback

### 2.4 诊断相关

#### DiagnosisResult（依据 PRD §5.2）

```
{
  "type": "slow_query_analysis",       // enum: "slow_query_analysis" | "explain_plan" | "index_suggestion" | "sql_rewrite" | "config_review"
  "target_sql": "SELECT * FROM ...",   // string, 被分析的 SQL
  "findings": [
    {
      "severity": "warning",           // enum: "error" | "warning" | "info"
      "category": "missing_index",
      "title": "user_id 列缺少索引",
      "detail": "该查询对 orders 表进行全表扫描，扫描行数 1,495,832 行，但仅返回 50 行",
      "suggestion": "CREATE INDEX idx_orders_user_id ON orders(user_id);",
      "is_destructive": false,         // bool, 执行此建议是否会修改数据/结构
      "estimated_improvement": "扫描行数预计从 1,495,832 降至 <10",
      "reference": "PRD §5.2 索引建议"
    }
  ],
  "generated_at": "2026-06-30T14:35:00Z"
}
```

#### SlowQuery（依据 PRD §5.2 慢查询分析）

```
{
  "id": "slow_001",
  "sql_text": "SELECT * FROM orders WHERE status = 'pending'",
  "query_time_sec": 2.3,
  "lock_time_sec": 0.05,
  "rows_examined": 1495832,
  "rows_sent": 50,
  "executed_at": "2026-06-30T14:28:00Z",
  "user": "app_user",                  // string | null, 执行用户
  "host": "10.0.2.50"                  // string | null, 来源主机
}
```

### 2.5 健康巡检

#### HealthReport（依据 PRD §5.4）

```
{
  "id": "rpt_20260630_001",
  "connection_id": "conn_abc123",
  "status": "completed",               // enum: "running" | "completed" | "cancelled" | "partial"
  "score": 72,                         // int, 0-100
  "generated_at": "2026-06-30T15:00:00Z",
  "duration_sec": 18.5,
  "severity_counts": {"error": 1, "warning": 2, "pass": 17, "skipped": 0},
  "categories": [
    {
      "name": "连接",                  // string, 类别名（用于前端分组渲染）
      "items": [
        {
          "name": "连接数使用率",
          "status": "pass",            // enum: "pass" | "warning" | "error" | "skipped"
          "value": "45/200 (22.5%)",
          "threshold": "<80%",
          "suggestion": null,          // string | null
          "is_destructive": false
        }
      ]
    }
  ]
}
```

**约束**：
- [ ] MUST `status: "running"` 的报告通过 SSE 实时推送更新（前端订阅 `health_result` 事件），轮询仅作为降级方案
- [ ] MUST `score` 取值范围 0-100，其中 0-59 红色/严重、60-79 黄色/警告、80-100 绿色/健康（依据 PRD §5.4 三项分级）

### 2.6 故障排查

#### TroubleshootResult（依据 PRD §5.3）

```
{
  "session_id": "sess_xyz789",
  "issue_type": "deadlock",            // enum: "deadlock" | "connection_flood" | "replication_lag" | "slow_performance" | "disk_full" | "unknown"
  "steps": [
    {
      "order": 1,
      "tool": "check_connections",
      "display": "检查连接池状态",
      "status": "pass",
      "summary": "连接数 45/200，正常",
      "detail": {"total":200,"active":45},
      "duration_ms": 120
    },
    {
      "order": 2,
      "tool": "check_locks",
      "display": "检查锁等待",
      "status": "warning",
      "summary": "发现 3 个锁等待",
      "detail": {"waiting":3,"blocking_trx_id":"82491","blocking_duration_sec":480},
      "duration_ms": 250
    }
  ],
  "conclusion": "事务 #82491 长时间未提交导致锁等待",
  "suggestion": "检查事务 #82491 状态，必要时执行 KILL 82491;",
  "suggestion_is_destructive": true,
  "severity": "error"
}
```

---

## 三、前端核心页面—组件映射

> 依据 PRD §7 项目结构 frontend 目录树、§4.1 架构图三视图、§9 实施路线图各 Phase 前端交付物。

### 3.1 页面路由映射

| 路由 | 页面 | 组件树（缩进表示父子关系） | 状态依赖 | 依据 |
|------|------|--------------------------|---------|------|
| `/` | ChatView（默认首页） | PRD §7 ChatView.vue |
| | ├ `AppLayout` | 全局布局壳 | 无 | PRD §7 AppLayout.vue |
| | ├ `Sidebar` | 侧边导航（连接列表 + 会话列表） | `connectionStore.activeId` | PRD §7 Sidebar.vue |
| | ├ `ConnectionIndicator` | 连接状态指示灯（🟢🟡🔴） | `connectionStore.status` | frontend AGENTS.md §3 |
| | ├ `ChatPanel` | 对话面板容器 | `chatStore.currentSession` | PRD §7 ChatPanel.vue |
| | │  ├ `MessageList` | 消息列表（虚拟滚动） | `chatStore.messages` | PRD §7 MessageList.vue |
| | │  │  └ `MessageBubble`（×N） | 单条消息渲染（分派到子组件） | `message.type` | PRD §7 MessageBubble.vue |
| | │  │     ├ `SqlBlock` | SQL 代码块（当 message.type=sql） | 无 | PRD §7 SqlBlock.vue |
| | │  │     ├ `ResultTable` | 查询结果表格（当 message.type=result） | 无 | PRD §7 ResultTable.vue |
| | │  │     ├ `DiagnosisCard` | 诊断结果卡片（当 message.type=diagnosis） | 无 | PRD §9 Phase 3 |
| | │  │     └ `TroubleshootSteps` | 排查步骤列表（当 message.type=troubleshoot） | 无 | PRD §5.3 |
| | │  └ `InputArea` | 输入区域（自然语言/SQL 切换） | `chatStore.inputMode` | PRD §7 InputArea.vue |
| | └ `ThinkingIndicator` | Agent 思考中动画 + 实时耗时 | `chatStore.isStreaming` | PRD §7 ThinkingIndicator.vue |
| `/connections` | ConnectionView | PRD §7 ConnectionView.vue |
| | ├ `AppLayout` | 同 ChatView | 无 | |
| | ├ `Sidebar` | 同 ChatView | 无 | |
| | ├ `ConnectionList` | 已保存连接列表 | `connectionStore.connections` | PRD §7 ConnectionList.vue |
| | └ `ConnectionForm` | 新建/编辑连接表单 | `connectionStore.editingId` | PRD §7 ConnectionForm.vue |
| |    ├ 基本字段（name/db_type/host/port/database/user/password） | | |
| |    └ SSL 配置折叠区 | | |
| `/reports` | ReportView | PRD §7 ReportView.vue |
| | ├ `AppLayout` | 同 ChatView | 无 | |
| | ├ `Sidebar` | 同 ChatView | 无 | |
| | ├ `ReportList` | 历史报告列表 | `reportStore.reports` | |
| | └ `ReportDetail`（条件渲染） | | |
| |    ├ `HealthScore` | 健康评分环形图（0-100，三色分区） | `reportStore.currentReport.score` | PRD §7 HealthScore.vue |
| |    ├ `CheckItemList` | 检查项列表（按分类分组，🔴🟡🟢图标） | `reportStore.currentReport.categories` | PRD §7 CheckItemList.vue |
| |    ├ `MetricChart` | 指标折线图（QPS/TPS/连接数趋势） | `reportStore.currentReport.metrics` | PRD §7 MetricChart.vue |
| |    └ `ExportButton` | 导出按钮（PDF/HTML） | `reportStore.currentReport.id` | PRD §5.4 |

### 3.2 跨页面共享组件

| 组件 | 位置 | 职责 | 依据 |
|------|------|------|------|
| `AppLayout` | common/ | 全局布局（顶栏 + 侧边栏 + 内容区） | PRD §7 |
| `Sidebar` | common/ | 连接列表 + 会话历史 + 导航 | PRD §7 |
| `ConnectionIndicator` | common/ | 全局连接状态灯（独立于 Sidebar，顶栏常驻） | frontend AGENTS.md §3 |
| `ConfirmDialog` | common/ | 写操作二次确认弹窗（危险色按钮 + 1.5s 冷却） | frontend AGENTS.md §1 |
| `SqlBlock` | sql/ | SQL 语法高亮 + 复制 + 执行/重试按钮 | PRD §7, §9 Phase 2 |
| `ResultTable` | sql/ | 虚拟滚动表格 + 列排序/筛选 + 敏感列掩码 | PRD §7, frontend AGENTS.md §2 |
| `ErrorCard` | common/ | 分级错误展示（ℹ️/⚠️/🛑） | frontend AGENTS.md §3 |

### 3.3 状态管理 Store 划分

| Store | 作用域 | 核心状态 | 持久化策略 | 依据 |
|-------|--------|---------|-----------|------|
| `connectionStore` | 全局 | `connections[]`, `activeId`, `status` | 连接列表 → localStorage（不含密码）；activeId → 会话内存 | PRD §8.2 |
| `chatStore` | 全局 | `currentSession`, `messages[]`, `isStreaming`, `inputMode` | 消息列表 → 会话内存；inputMode → localStorage | PRD §4.2 |
| `reportStore` | 页面级 | `reports[]`, `currentReport` | 无持久化（每次从后端拉取） | PRD §5.4 |

- [ ] MUST `connectionStore` 在页面刷新后从 localStorage 恢复连接列表，但 `activeId` 需重新测试连接有效性（依据 PRD §8.2 会话超时清理策略）
- [ ] MUST `chatStore.messages` 中包含的查询结果预览数据（`data_preview`）在会话关闭 30 分钟后从内存清除

---

## 四、待确认技术决策项

> 以下各项从前端 AGENTS.md 第 §5 章继承，并补充后端视角的候选方案影响分析。

### T-1：API 响应信封格式（继承 frontend AGENTS.md D-7 关联项）

- **来源**：backend AGENTS.md TODO、frontend AGENTS.md 未明确定义
- **候选**：
  - A. `{"code":0,"data":{...},"message":"ok"}` 中台风格
  - B. `{"success":true,"data":{...},"error":null}` RESTful 风格
  - C. 直接数据体 + HTTP 状态码（FastAPI 默认）
- **影响范围**：所有 API 响应 + 前端 `client.ts` 拦截器实现 + 错误处理逻辑
- **默认推荐**：方案 C（FastAPI 原生，最小抽象层）

### T-2：SSE 会话上下文传递方式（继承 frontend AGENTS.md D-7）

- **来源**：PRD 未指定 SSE HTTP 方法
- **候选**：
  - A. `POST` + fetch ReadableStream（可携带完整对话上下文，需手动解析 SSE）
  - B. `GET` + EventSource API（浏览器原生 SSE，无法携带请求体 → 需 URL 参数传递 context）
- **影响范围**：`POST /api/chat/stream` 设计 + 前端 `useSSE.ts` 实现 + 长上下文传递能力
- **默认推荐**：方案 A（支持 POST 携带完整上下文，前端手动解析 SSE 流）

### T-3：用户身份认证方案（继承 frontend AGENTS.md D-8）

- **来源**：PRD §8.3 定义了权限模型但未定义认证机制
- **候选**：A. 无认证 B. JWT C. OAuth2 D. API Key
- **影响范围**：所有 API 鉴权中间件 + 导出水印 uid + 连接凭据隔离 + 历史记录隔离 + `ConnectionConfig.name` 唯一性范围
- **默认推荐**：方案 A（Phase 1-3 MVP 阶段），方案 B（Phase 5 生产化）

### T-4：SQL 审计敏感列判定源（继承 frontend AGENTS.md TODO）

- **来源**：frontend AGENTS.md §2 脱敏规则 + PRD 未定义敏感列自动识别机制
- **候选**：
  - A. 仅列名正则匹配（`password/token/secret/phone/email/id_card`）
  - B. 通过 Adapter 从数据库注释/标签中读取敏感标记（需 DBA 预先标注）
  - C. LLM 根据列名语义推断（增加 LLM 调用 + 延迟）
- **默认推荐**：方案 A（Phase 1-3）+ 方案 B 作为 Phase 4+ 可选增强

### T-5：Monaco Editor 体积对首屏加载的影响（继承 frontend AGENTS.md D-3）

- **来源**：frontend AGENTS.md D-3 候选方案对比
- **候选**：A. Monaco Editor（~5MB）B. CodeMirror 6（~500KB）C. 懒加载 Monaco + 加载时展示占位符
- **影响范围**：PRD §10.1「首屏加载 < 2s」约束 + SQL 编辑器能力（Monaco 的 SQL IntelliSense 更成熟）
- **默认推荐**：方案 C（懒加载 + CodeMirror 6 作为非编辑器页面的轻量配方）

### T-6：暗色模式实现策略（继承 frontend AGENTS.md D-9）

- **来源**：PRD §9 Phase 5 提及暗色模式
- **候选**：A. 手动切换 B. 跟随系统 C. 仅亮色 + 编辑器暗色
- **默认推荐**：方案 C（Phase 1-4），方案 B（Phase 5 正式实现）

### T-7：国际化策略（继承 frontend AGENTS.md D-10）

- **来源**：PRD §10.2「支持中英文自然语言输入」
- **候选**：A. 仅中文 UI B. 中英双语 C. 中文 UI + 英文文档
- **默认推荐**：方案 A（Phase 1-4），预留 i18n 字符串外提（Phase 5）

### T-8：慢查询阈值联动（继承 frontend AGENTS.md §3 TODO）

- **来源**：frontend AGENTS.md §3「慢查询阈值是否与数据库 long_query_time 联动」
- **候选**：
  - A. 仅前端固定阈值（500ms 黄色 / 2s 红色）
  - B. 读取目标数据库 `long_query_time` 配置，动态调整前端阈值
- **默认推荐**：方案 A（Phase 1-3），方案 B（Phase 4+）

### T-9：巡检报告导出格式（源自 PRD §5.4）

<!-- TODO: 需人工确认：PRD §5.4 提及「报告导出（PDF/HTML）」但未指定：
  A. 前端渲染 HTML → 浏览器打印为 PDF
  B. 后端生成 PDF（WeasyPrint / ReportLab）
  C. 后端生成 Markdown → 前端渲染为 HTML/PDF
  默认推荐方案 B（服务端生成，前端下载） -->

### T-10：写操作影响行数预估（源自 PRD §8.1 Layer 3）

<!-- TODO: 需人工确认：PRD §8.1 Layer 3 要求「说明影响范围」但未指定如何获取预估行数：
  A. 后端改写 SQL：先执行 SELECT count(*) 获取影响行数（增加一次查询）
  B. 后端通过 EXPLAIN 估算（不精确）
  C. 直接显示"影响范围未知——请人工评估"
  默认推荐方案 A（安全优先，代价是一次额外查询） -->

---

## 五、契约生效与变更流程

- [ ] 本草案中的 MUST 条款在前后端实现时必须严格遵守，违反视为 Bug
- [ ] SHOULD 条款为推荐实现，若未实现需在代码注释中说明原因并标记 `# DEVIATION: 原因`
- [ ] TODO 标记的决策项必须在 Phase 1 启动前由项目负责人逐一确认并更新本文件
- [ ] API 路径/字段变更时，必须同步更新本文档 + backend AGENTS.md + frontend AGENTS.md + 前端 TypeScript 类型定义
