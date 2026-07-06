# user_role 权限检测 — 企业级实现任务清单（✅ 全部完成）

> 目标：基于 MySQL `SHOW GRANTS` 自动检测数据库用户权限，映射为应用层 `user_role`，替代当前硬编码 `"standard"`。

---

## 任务列表

### ✅ Task 1: 新增 GRANT 解析引擎

**文件**: `backend/app/engine/grant_detector.py`（新文件）

**描述**: 创建独立的 GRANT 检测模块，连接 MySQL 执行 `SHOW GRANTS FOR CURRENT_USER()`，解析返回的 GRANT 语句，判定该数据库用户的有效角色。

**验收结果**:
- [x] 仅有 SELECT 权限的数据库用户 → 返回 `"readonly"` — 单元测试验证
- [x] 有 INSERT/UPDATE/DELETE 权限的数据库用户 → 返回 `"admin"` — 单元测试验证
- [x] 有 ALL PRIVILEGES 的数据库用户 → 返回 `"admin"` — 单元测试验证
- [x] 连接失败/超时 → 返回 `"readonly"`，不抛异常 — try/except 安全降级
- [x] GRANT 解析异常 → 返回 `"readonly"`，不抛异常 — try/except 安全降级
- [x] 临时连接使用后正确释放 — finally + contextlib.suppress
- [x] 数据库名前缀不应误匹配（`test_db_extra` 不影响 `test_db`）
- [x] `` `db`.`table` `` 格式（`db.table` 级权限）正确识别

---

### ✅ Task 2: 预检角色并注入 conn_config

**文件**: `backend/app/api/chat.py`, `backend/app/api/troubleshoot.py`

**描述**: `_resolve_connection_config()` 读取连接配置后调用 grant_detector 预检角色，写入 `conn_config["user_role"]`。增加 `_role_cache` 避免重复连接。同步修复 troubleshoot.py 同一问题。

**验收结果**:
- [x] 首次请求：执行预检连接 → 检测角色 → 缓存 → conn_config 含 user_role
- [x] 后续请求：命中缓存 → 不再执行预检连接（缓存移至 `grant_detector._role_cache`，`chat.py` 和 `troubleshoot.py` 共享）
- [x] 预检失败 → 缓存不写入 → 下次请求重试
- [x] conn_config["user_role"] 正确传递到 AgentState（移除所有硬编码 `"standard"`）
- [x] 现有 `/api/chat/stream` SSE 流程不受影响

---

### ✅ Task 3: MySQL 适配器按角色控制只读事务

**文件**: `backend/app/db/mysql.py`

**描述**: `connect()` 接收 `user_role`，admin 跳过 `SET SESSION TRANSACTION READ ONLY`。

**验收结果**:
- [x] admin 角色连接 → 不设置只读事务 → INSERT/UPDATE/DELETE 可在 MySQL 层执行
- [x] readonly/standard 角色连接 → 设置只读事务 → 写操作被 MySQL ERROR 1792 拦截
- [x] 连接日志包含 user_role 和 readonly_session 标记

---

### ✅ Task 4: 基类接口同步更新

**文件**: `backend/app/db/base.py`, `backend/app/db/postgresql.py`, `backend/app/db/oracle.py`

**描述**: `BaseAdapter.connect()` 签名增加 `user_role: str = "standard"`，所有三个适配器签名一致。

**验收结果**:
- [x] 三个适配器签名一致——基类抽象 + MySQL（实现逻辑） + PostgreSQL（暂忽略） + Oracle（暂忽略）
- [x] ruff 通过，现有功能不受影响

---

### ✅ Task 5: 工具函数传递 user_role 到适配器

**文件**: `backend/app/agent/tools/query.py`, `backend/app/agent/tools/diagnosis.py`

**描述**: query/diagnosis 类工具的 `adapter.connect()` 调用传入 `user_role`。

**验收结果**:
- [x] `list_tables` — 新增 `user_role` 参数 + 传入 connect
- [x] `describe_table` — 新增 `user_role` 参数 + 传入 connect
- [x] `run_query` — 已有 `user_role` 参数 + 传入 connect
- [x] `explain_query` — 新增 `user_role` 参数 + 传入 connect + audit 使用 user_role
- [x] `get_slow_queries` — 新增 `user_role` 参数 + 传入 connect

---

### ✅ Task 6: 测试验证

**文件**: 全量回归

**验收结果**:
- [x] `pytest tests/ -v` → 60 passed, 5 skipped（MySQL 集成测试需要真实数据库连接）
- [x] ruff check → 所有文件通过
- [x] 场景1：仅 SELECT 权限用户 → INSERT 在 SQLAuditCheck 被拦截（`_ADMIN_ONLY_STATEMENTS` + `user_role="readonly"`）
- [x] 场景2：有 INSERT 权限用户 → INSERT 通过 SQL 审计（`user_role="admin"`） → MySQL 层无 READ ONLY 乐观执行
- [x] 场景3：有 INSERT 权限用户 → DELETE 仍需 admin → 在 SQLAuditCheck 被拦截
- [x] 场景4：缓存命中 → 不执行额外的预检连接

---

## 最终数据流

```
POST /api/chat/stream  (或 /api/troubleshoot/stream)
  │
  ├─→ _resolve_connection_config()
  │     ├─→ 查 _role_cache[connection_id] (shared across modules)
  │     ├─→ 未命中 → detect_mysql_role() (SHOW GRANTS → parse → admin|readonly)
  │     ├─→ 写入缓存
  │     └─→ conn_config["user_role"] = "admin" | "readonly"
  │
  ├─→ AgentState (无硬编码 user_role)
  │
  ├─→ safe_tools_node
  │     ├─→ SQLAuditCheck(user_role) ← INSERT/UPDATE/DELETE 需 admin
  │     └─→ ReadOnlyCheck(user_role)
  │
  ├─→ run_query() / explain_query() / ...
  │     ├─→ audit(sql, user_role=...) ← 工具内二次审计
  │     ├─→ adapter.connect(config, user_role=...)
  │     │     ├─→ admin → 跳过 READ ONLY
  │     │     └─→ standard/readonly → SET SESSION READ ONLY
  │     └─→ adapter.execute(sql)
  │           ├─→ admin + INSERT 通过审计 → 执行成功
  │           └─→ standard + INSERT 未通过审计 → 返回拦截信息
  │
  └─→ SSE 事件流返回
```

## 修改文件清单

| 文件 | 改动类型 |
|------|----------|
| `backend/app/engine/grant_detector.py` | **新建** — GRANT 解析引擎 + 角色缓存 |
| `backend/app/api/chat.py` | 修改 — 预检角色、移除硬编码、使用共享缓存 |
| `backend/app/api/troubleshoot.py` | 修改 — 同 chat.py，预检角色、移除硬编码 |
| `backend/app/db/base.py` | 修改 — 基类 connect() 签名增加 user_role |
| `backend/app/db/mysql.py` | 修改 — 按 user_role 控制 READ ONLY |
| `backend/app/db/postgresql.py` | 修改 — 签名同步 |
| `backend/app/db/oracle.py` | 修改 — 签名同步 |
| `backend/app/engine/sql_auditor.py` | 先前修改 — _ADMIN_ONLY_STATEMENTS 增加 INSERT |
| `backend/app/agent/tools/query.py` | 修改 — list_tables/describe_table 增加 user_role 参数 |
| `backend/app/agent/tools/diagnosis.py` | 修改 — explain_query/get_slow_queries 增加 user_role 参数 |
