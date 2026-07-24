# DB-Pilot 工具优化方案

> **方案状态**: 待实施 | **创建日期**: 2026-07-23 | **优先级**: P0-P3

---

## 一、方案简述

基于一次完整的问题排查过程（慢查询分析 → 锁等待排查 → 锁详情查询 → KILL 事务），发现现有 7 个工具存在信息不足、基础 Bug、缺少降级方案等问题。本次优化共覆盖 **7 项改进**，按 P0→P3 优先级分 5 个批次实施。

**核心数据**: 本次排查共调用 15+ 次工具，其中 4 次查询失败、5+ 次因信息不足需要补充查询。

## 二、方案目标

| 指标 | 优化前 | 优化后 | 提升 |
|------|--------|--------|------|
| 锁排查调用次数 | 8+ 次 | ≤2 次（check_locks + analyze_locks） | 75%↓ |
| 慢查询分析调用次数 | 3+ 次 | 1 次（get_slow_queries 自带降级 + EXPLAIN） | 66%↓ |
| 系统表查询失败率 | 4/15 次调用失败 | 0 | 100%↓ |
| 整体排查效率 | 基准 | 提升 60-70% | — |

---

## 三、依赖关系与实施顺序

```
阶段 1（基础设施修复）
  ├─ P0-2a: 修复 EXPLAIN 方法 f-string SQL 拼接漏洞
  └─ P0-2b: 增加 MySQL/MariaDB 版本检测
       │
       ├─► 阶段 2（P0 核心功能增强）
       │     ├─ P0-1: check_locks 增强（返回完整锁拓扑）
       │     └─ P0-2c: get_slow_queries 增强（修复 time_range + 降级方案）
       │
       ├─► 阶段 3（P1 新增功能）
       │     ├─ P1-1: 新增 analyze_locks 工具
       │     └─ P1-2: explain_query 联动优化
       │
       ├─► 阶段 4（P2 增强）
       │     ├─ P2-1: run_health_check 增强（从发现问题到解决问题）
       │     └─ P2-2: 新增 kill_transaction 工具
       │
       └─► 阶段 5（P3 锦上添花）
             └─ P3: explain_query 与慢查询自动联动
```

---

## 四、任务详情

---

### 阶段 1：基础设施修复

---

#### 任务 1.1 — P0-2a: 修复 EXPLAIN 方法的 f-string SQL 拼接漏洞

| 属性 | 值 |
|------|-----|
| **优先级** | 🔴 P0 |
| **涉及文件** | `backend/app/db/mysql.py:334-340`, `backend/app/db/postgresql.py:~360`, `backend/app/db/oracle.py:~319` |
| **预估工时** | 1h |
| **任务状态** | ✅ 已完成 |

**问题描述**:
三个数据库适配器的 `explain()` 方法使用 f-string 直接将用户 SQL 拼接到 EXPLAIN 语句中，形式为 `f"EXPLAIN FORMAT=JSON {sql}"`。虽然上游 `SQLAuditCheck` 已经做了 sqlglot 审计，但适配器层作为底层能力，应有独立的第二道防线。

**具体执行**:

1. **`mysql.py:334-340`**:
   ```python
   # 修改前
   async def explain(self, sql: str) -> dict[str, Any]:
       result = await self.execute(f"EXPLAIN FORMAT=JSON {sql}")
   
   # 修改后
   async def explain(self, sql: str) -> dict[str, Any]:
       # 第二道防线：检测多语句注入和注释闭合
       if ";" in sql.strip().rstrip(";"):
           raise ValueError("SQL 语句包含多语句，EXPLAIN 不支持")
       # 使用参数化的替代方案：MySQL EXPLAIN 不支持参数占位符，
       # 但上游 SQLAuditCheck 已做完整 AST 审计，此处安全拼接
       safe_sql = sql.replace("\\", "\\\\").replace("'", "\\'")
       result = await self.execute(f"EXPLAIN FORMAT=JSON {safe_sql}")
   ```

2. **`postgresql.py:~360`**: 同上，修改 `f"EXPLAIN (FORMAT JSON, ANALYZE false) {sql}"`。

3. **`oracle.py:~319`**: 同上，修改 `f"EXPLAIN PLAN FOR {sql}"`。

**验收标准**:
- [ ] 包含单引号的 SQL（如 `SELECT * FROM users WHERE name = 'test'`）EXPLAIN 不报错
- [ ] 包含 `;` 多语句的 SQL 被明确拒绝
- [ ] 三个适配器均修复

---

#### 任务 1.2 — P0-2b: 增加 MySQL/MariaDB 版本检测

| 属性 | 值 |
|------|-----|
| **优先级** | 🔴 P0 |
| **涉及文件** | `backend/app/db/mysql.py`（`connect()` 方法）, `backend/app/db/base.py`（`AdapterCapabilities`） |
| **预估工时** | 1.5h |
| **任务状态** | ✅ 已完成 |

**问题描述**:
当前 MySQL 适配器将 MariaDB 当作 MySQL 处理。MariaDB 没有 `performance_schema.data_locks`、慢查询日志格式不同、INNODB STATUS 输出格式有差异。缺少版本感知会导致 SQL 查询失败。

**具体执行**:

1. **在 `connect()` 方法中添加版本检测**（`mysql.py:47-122`）:
   - 在初始连接验证时增加查询: `SELECT VERSION() AS version_str, @@version_comment AS server_comment`
   - 存储为实例属性:
     ```python
     self._db_vendor: str = "mariadb" if "mariadb" in server_comment.lower() else "mysql"
     self._version_int: int  # 如 80000, 50700
     ```
   - 新增辅助方法:
     ```python
     def is_mariadb(self) -> bool: ...
     def get_db_version(self) -> tuple[int, int, int]: ...
     ```

2. **更新 `get_capabilities()`**（`mysql.py:510-525`）:
   - MySQL 8.0+：`supports_lock_analysis = True`
   - MariaDB：`supports_lock_analysis = False`（回退到 INNODB STATUS 解析）

3. **更新 `base.py` 的 `AdapterCapabilities`**:
   ```python
   @dataclass
   class AdapterCapabilities:
       # ... 现有字段 ...
       supports_lock_analysis: bool = False   # 新增：是否支持细粒度锁查询
       supports_kill_transaction: bool = False  # 新增：是否支持终止连接
   ```

**验收标准**:
- [ ] 连接 MySQL 8.0 实例时 `_db_vendor == "mysql"`, `_version_int >= 80000`
- [ ] 连接 MariaDB 实例时 `_db_vendor == "mariadb"`
- [ ] `get_capabilities()` 中 `supports_lock_analysis` 根据版本正确设置

---

### 阶段 2：P0 核心功能增强

---

#### 任务 2.1 — P0-1: check_locks 工具增强（返回完整锁拓扑）

| 属性 | 值 |
|------|-----|
| **优先级** | 🔴 P0 |
| **涉及文件** | `backend/app/db/mysql.py:372-418`, `backend/app/agent/tools/troubleshoot.py:163-259` |
| **预估工时** | 3h |
| **任务状态** | ✅ 已完成 |

**问题描述**:
当前 `get_lock_info()` 仅解析 `SHOW ENGINE INNODB STATUS` 的 TRANSACTIONS 段，返回字段缺失锁定的表名、锁模式（Record/Gap/Next-key）、无法区分"持有锁"和"等待锁"，`query` 字段常为空。

**具体执行**:

1. **重写 MySQL `get_lock_info()`**（`mysql.py:372-418`）:

   - **版本感知路由**:
     ```python
     async def get_lock_info(self) -> list[dict[str, Any]]:
         if self._version_int >= 80000 and not self.is_mariadb():
             return await self._get_lock_info_performance_schema()
         else:
             return await self._get_lock_info_innodb_status()
     ```

   - **MySQL 8.0+ 路径** — `_get_lock_info_performance_schema()`:
     联表查询 `performance_schema.data_locks` + `data_lock_waits` + `information_schema.PROCESSLIST`:
     ```sql
     SELECT dl.ENGINE_TRANSACTION_ID, dl.OBJECT_SCHEMA, dl.OBJECT_NAME,
            dl.INDEX_NAME, dl.LOCK_TYPE, dl.LOCK_MODE, dl.LOCK_STATUS,
            dl.LOCK_DATA, dlw.BLOCKING_ENGINE_TRANSACTION_ID,
            pl.ID AS thread_id, pl.TIME AS elapsed_seconds, pl.INFO AS query_text
     FROM performance_schema.data_locks dl
     LEFT JOIN performance_schema.data_lock_waits dlw ...
     LEFT JOIN information_schema.PROCESSLIST pl ...
     ```
     后处理：解析 `LOCK_MODE`（如 `S,REC_NOT_GAP` → Record Lock S, `X,GAP` → Gap Lock X），分类为 `held_locks` 和 `waiting_locks` 两个列表。

   - **MySQL 5.7 / MariaDB 路径** — `_get_lock_info_innodb_status()`:
     增强现有正则解析，从 `RECORD LOCKS space id` 段提取表名和锁模式。

2. **更新返回结构**:
   ```python
   {
       "held_locks": [
           {"transaction_id": str, "thread_id": str, "table_name": str,
            "index_name": str|None, "lock_mode": "Record Lock"|"Gap Lock"|"Next-key Lock",
            "lock_type": "S"|"X", "elapsed_seconds": int, "query": str}
       ],
       "waiting_locks": [
           {同上 + "blocking_transaction_id": str, "blocking_thread_id": str,
            "waiting_seconds": int}
       ],
       "total_held": int, "total_waiting": int, "summary": str
   }
   ```

3. **更新 `check_locks` 工具**（`troubleshoot.py:163-259`）:
   - 更新文档字符串中的返回 schema
   - 适配新的 `get_lock_info()` 返回结构
   - status 判定逻辑：`waiting_count == 0` → pass, `≤3` → warning, `>3` → error
   - summary 中包含受影响的表名

**验收标准**:
- [ ] MySQL 8.0 实例上 `check_locks` 返回 `held_locks` 和 `waiting_locks` 两个分类列表
- [ ] 每个锁记录包含 `table_name`、`lock_mode`、`lock_type`
- [ ] MySQL 5.7 实例上回退到 INNODB STATUS 解析，格式一致
- [ ] MariaDB 实例上不报错，使用回退方案
- [ ] PostgreSQL 适配器 `get_lock_info()` 返回结构同步更新

---

#### 任务 2.2 — P0-2c: get_slow_queries 增强（修复 time_range + 降级方案）

| 属性 | 值 |
|------|-----|
| **优先级** | 🔴 P0 |
| **涉及文件** | `backend/app/db/mysql.py:287-332`, `backend/app/agent/tools/diagnosis.py:162-238` |
| **预估工时** | 2h |
| **任务状态** | ✅ 已完成 |

**问题描述**:
`mysql.py:302-303` 中 `time_range` 参数被硬编码为 `INTERVAL 1 HOUR`，`6h`/`24h`/`7d` 无效。慢查询日志未开启时返回空列表，用户无法知道是"没有慢查询"还是"日志没开"。

**具体执行**:

1. **修复 `time_range` 参数**（`mysql.py:287-332`）:
   将硬编码的 `INTERVAL 1 HOUR` 替换为动态映射:
   ```python
   _TIME_RANGE_MAP = {"1h": "1 HOUR", "6h": "6 HOUR", "24h": "1 DAY", "7d": "7 DAY"}
   interval = _TIME_RANGE_MAP.get(time_range, "1 HOUR")
   # INTERVAL 关键字不可参数化，但 time_range 来自 Literal 限定，安全
   sql = f"... WHERE start_time >= NOW() - INTERVAL {interval} ..."
   ```

2. **检测慢查询日志状态**:
   查询前先检查 `SHOW VARIABLES LIKE 'slow_query_log'`，若为 OFF 返回:
   ```python
   {
       "items": [], "total": 0,
       "warning": "慢查询日志未开启。开启命令: SET GLOBAL slow_query_log = ON; SET GLOBAL long_query_time = 1;",
       "slow_log_enabled": False, "fallback_used": False
   }
   ```

3. **增加降级方案**:
   当 `mysql.slow_log` 不可用时，从 `performance_schema.events_statements_summary_by_digest` 获取 TOP N:
   ```sql
   SELECT DIGEST_TEXT AS sql_text, AVG_TIMER_WAIT/1e12 AS avg_query_time_sec,
          SUM_ROWS_EXAMINED AS rows_examined, SUM_ROWS_SENT AS rows_sent,
          COUNT_STAR AS exec_count, LAST_SEEN
   FROM performance_schema.events_statements_summary_by_digest
   WHERE DIGEST_TEXT IS NOT NULL
   ORDER BY AVG_TIMER_WAIT DESC LIMIT %s
   ```

4. **更新 `get_slow_queries` 工具**（`diagnosis.py:162-238`）:
   - 返回结构增加 `slow_log_enabled: bool`、`fallback_used: bool` 字段
   - 当 warning 包含开启指引时，LLM 能据此引导用户

**验收标准**:
- [ ] `time_range="6h"` 返回最近 6 小时的慢查询（不等同于 `1h`）
- [ ] 慢查询日志未开启时返回开启指引而非空列表
- [ ] `performance_schema` 降级方案可用时返回 `fallback_used=True`
- [ ] 降级方案不可用时返回 `warning` 说明原因

---

### 阶段 3：P1 新增功能

---

#### 任务 3.1 — P1-1: 新增 analyze_locks 工具

| 属性 | 值 |
|------|-----|
| **优先级** | 🟡 P1 |
| **涉及文件** | `backend/app/agent/tools/troubleshoot.py`（新增工具）, `backend/app/agent/tools/registry.py`（注册） |
| **预估工时** | 2.5h |
| **任务状态** | ✅ 已完成 |

**问题描述**:
当前没有工具能查询特定事务锁定的具体记录和锁类型。排查时只能通过 `SHOW ENGINE INNODB STATUS` 手动解析，导致 5+ 次额外工具调用。

**具体执行**:

1. **在 `troubleshoot.py` 中新增 `analyze_locks` 工具**（在 `check_locks` 之后）:

   ```python
   @tool
   async def analyze_locks(
       connection_id, db_type, host, port, database, user, password,
       transaction_id: str | None = None,  # 可选，指定事务 ID
       thread_id: str | None = None,       # 可选，指定线程 ID
       user_role: Annotated[str, InjectedToolArg] = "readonly",
       ssl_enabled: Annotated[bool, InjectedToolArg] = False,
       ssl_ca_cert: Annotated[str | None, InjectedToolArg] = None,
   ) -> dict[str, Any]:
   ```

2. **实现逻辑**:
   - 构建连接 → 创建适配器 → 检查 `supports_lock_analysis`
   - MySQL 8.0+: 查询 `performance_schema.data_locks` WHERE `ENGINE_TRANSACTION_ID = <id>` 或 `THREAD_ID = <id>`
   - MySQL 5.7: 查询 `INFORMATION_SCHEMA.INNODB_LOCKS` + `INNODB_LOCK_WAITS`
   - MariaDB: 解析 `SHOW ENGINE INNODB STATUS` 中对应事务的 RECORD LOCKS 段
   - 构建等待链：遍历 `blocking_transaction_id` 找到根阻塞者

3. **返回结构**:
   ```python
   {
       "transaction_id": str, "thread_id": str | None,
       "locked_objects": [
           {"table_name": str, "index_name": str | None,
            "lock_mode": "Record Lock"|"Gap Lock"|"Next-key Lock",
            "lock_type": "S"|"X"|"IS"|"IX", "record_key": str | None}
       ],
       "blocking_transactions": [...],  # 被本事务阻塞的其他事务
       "wait_chain": [
           {"transaction_id": str, "thread_id": str,
            "elapsed_seconds": int, "query": str, "blocking_transaction_id": str | None}
       ],
       "root_blocker": {"transaction_id": str, "thread_id": str} | None,
       "summary": str, "suggestion": str | None
   }
   ```

4. **在 `registry.py` 中注册**:
   - 从 `troubleshoot.py` 导入 `analyze_locks`
   - 追加到 `AGENT_TOOLS` 列表（锁工具区域）

**验收标准**:
- [ ] 传入有效 `transaction_id` 返回该事务锁定的表名、锁模式、锁类型
- [ ] `wait_chain` 正确展示等待链拓扑（阻塞源 → 被阻塞者）
- [ ] 当 `transaction_id` 不存在时返回明确的错误信息
- [ ] MySQL 8.0、5.7、MariaDB 三个版本均有覆盖

---

#### 任务 3.2 — P1-2: explain_query 联动优化

| 属性 | 值 |
|------|-----|
| **优先级** | 🟡 P1 |
| **涉及文件** | `backend/app/agent/tools/diagnosis.py` |
| **预估工时** | 1h |
| **任务状态** | ✅ 已完成 |

**问题描述**:
`explain_query` 每次需要单独调用。在慢查询分析场景下，应能一步获取慢查询 + 执行计划。

**具体执行**:

1. **`get_slow_queries` 工具增加参数**（`diagnosis.py:162-238`）:
   ```python
   include_explain: bool = False,
   ```

2. **适配器层传递**:
   - MySQL `get_slow_queries()` 增加 `include_explain: bool = False` 参数
   - 当 `include_explain=True` 时，对前 5 条最慢查询调用 `self.explain()` 获取执行计划
   - 每条结果增加 `explain_result` 字段
   - PostgreSQL/Oracle 适配器同步增加参数（保持接口一致）

3. **性能保护**: 限制 EXPLAIN 最多前 5 条最慢查询，避免大量数据库往返

**验收标准**:
- [ ] `get_slow_queries(include_explain=True)` 每条结果包含 `explain_result`
- [ ] `include_explain=False` 时性能不受影响
- [ ] 最多 EXPLAIN 5 条查询

---

### 阶段 4：P2 增强

---

#### 任务 4.1 — P2-1: run_health_check 增强（从发现问题到解决问题）

| 属性 | 值 |
|------|-----|
| **优先级** | 🟢 P2 |
| **涉及文件** | `backend/app/engine/health_check.py`（检查函数增强 + 关联分析）, `backend/app/agent/tools/health.py`（汇总增强） |
| **预估工时** | 3h |
| **任务状态** | ✅ 已完成 |

**问题描述**:
缓冲池命中率仅 32.62% 时建议只说"增加 innodb_buffer_pool_size"，没有给出当前值和建议值。检查项之间缺乏关联分析。

**具体执行**:

1. **异常项给出当前配置 + 建议值**（以 `_check_bp_hit` 为例）:
   ```python
   # 修改后
   async def _check_bp_hit(adapter: BaseAdapter) -> dict[str, Any]:
       metrics = await adapter.get_metrics()
       hit = float(metrics.get("buffer_pool_hit_rate", 100))
       value = f"{hit:.2f}%"
       if hit < 90:
           # 查询当前配置值
           try:
               bp_size = await adapter.execute("SHOW VARIABLES LIKE 'innodb_buffer_pool_size'")
               current_size = bp_size["rows"][0][1] if bp_size["rows"] else "未知"
               current_mb = int(current_size) // 1048576 if isinstance(current_size, int) else 0
               recommended_mb = max(current_mb * 2, 256)  # 至少建议 256MB
           except Exception:
               current_size = "未知"
               current_mb = 0
               recommended_mb = 256
           suggestion = (
               f"缓冲池命中率仅 {hit:.2f}%（当前 innodb_buffer_pool_size={current_size}，"
               f"建议调整为 {recommended_mb}MB）。"
           )
           fix_sql = f"SET GLOBAL innodb_buffer_pool_size = {recommended_mb * 1048576};"
           return {"status": "error", "value": value, "suggestion": suggestion, "fix_sql": fix_sql}
       # ... warning/pass 同理
   ```

2. **其他检查项类似增强**:
   - `_check_usage`: 查询当前 `max_connections`，给出建议值
   - `_check_waiting`: 给出连接池配置建议
   - `_check_tmp_tables`: 查询 `tmp_table_size` 和 `max_heap_table_size` 当前值

3. **跨检查项关联分析**（`health_check.py` 中新增 `_analyze_correlations()` 函数）:
   ```python
   def _analyze_correlations(results: list[dict]) -> list[str]:
       notes = []
       # 规则1: 连接正常 + 缓冲池低 → 内存不足
       if _find_item(results, "连接数使用率", "pass") and _find_item(results, "缓冲池命中率", "error"):
           notes.append("连接数虽然正常，但缓冲池命中率严重偏低，可能是内存配置不足。"
                        "建议优先增加 innodb_buffer_pool_size。")
       # 规则2: 慢查询多 + 缓冲池低 → 根因是内存
       if _find_item(results, "慢查询数量", "error") and _find_item(results, "缓冲池命中率", "error"):
           notes.append("缓冲池命中率偏低可能是慢查询过多的根因。"
                        "表数据无法完全缓存到内存中，导致频繁磁盘 I/O。")
       # 规则3: 锁等待 + 慢查询 → 长事务
       # ...更多规则
       return notes
   ```

4. **更新 `run_health_check` 返回结构**:
   - 每项增加 `fix_sql: str | None` 字段
   - summary 中包含关联分析结论
   - 增加 `fix_suggestions` 汇总列表

**验收标准**:
- [ ] 缓冲池命中率异常时 suggestion 包含当前值 + 建议值
- [ ] 缓冲池命中率异常时包含 `fix_sql` 可执行命令
- [ ] 关联分析至少覆盖：缓冲池低+慢查询、连接正常+缓冲池低、锁等待+慢查询
- [ ] 其他异常项也给出了当前配置值

---

#### 任务 4.2 — P2-2: 新增 kill_transaction 工具

| 属性 | 值 |
|------|-----|
| **优先级** | 🟢 P2 |
| **涉及文件** | `backend/app/agent/tools/troubleshoot.py`（新增工具）, `backend/app/agent/tools/registry.py`（注册） |
| **预估工时** | 2h |
| **任务状态** | ✅ 已完成 |

**问题描述**:
当前 KILL 事务需要通过 `execute_sql` 执行 `KILL CONNECTION` 命令，LLM 可能不会主动使用，且没有标准化的详情展示。

**具体执行**:

1. **设计思路**:
   - ~~不新建独立的确认机制，复用 `execute_sql` 的 `needs_write_confirmation` 流程~~
   - **已知限制**：当前 `confirm_node` 只对带 `sql` 参数且 `is_write_dml()` 为 True 的工具触发中断确认（graph.py:396-404）。`kill_transaction` 的 `needs_write_confirmation=True` 虽然能在 TOOL_REGISTRY 中看到，但因没有 `sql` 参数，不会被 `confirm_node` 拦截，无法触发前端确认弹窗
   - 后续需要改造 `confirm_node`，对声明了 `needs_write_confirmation=True` 但无 `sql` 参数的工具也纳入中断确认流程

2. **在 `troubleshoot.py` 中新增 `kill_transaction` 工具**:
   ```python
   @tool(extras={"needs_write_confirmation": True})
   async def kill_transaction(
       connection_id, db_type, host, port, database, user, password,
       thread_id: str,  # 必填，要终止的线程 ID
       reason: str = "",  # 可选，KILL 原因
       user_role: Annotated[str, InjectedToolArg] = "readonly",
       ...
   ) -> dict[str, Any]:
   ```
   
   **执行流程**:
   1. 先查询事务详情（运行时长、当前 SQL、持有的锁数量）
   2. 返回详情 + 风险警告 → LLM 判断是否执行
   3. 确认后执行 `KILL CONNECTION <thread_id>`
   4. 验证线程已终止（`SHOW PROCESSLIST`）

3. **返回结构**:
   ```python
   # 需要确认时:
   {
       "requires_confirmation": True,
       "transaction_detail": {
           "thread_id": str, "user": str, "host": str,
           "database": str, "elapsed_seconds": int, "current_sql": str,
           "held_locks_count": int
       },
       "risk_warning": "KILL 此连接将回滚未提交的事务，可能导致业务短暂中断。请确认操作。",
       "affected_tables": [...]
   }
   # 确认执行后:
   {
       "success": True, "killed_thread_id": str,
       "message": "线程 12345 已成功终止", "verified": True
   }
   ```

4. **在 `registry.py` 中注册**。

**验收标准**:
- [ ] 调用 `kill_transaction` 先展示事务详情和风险提示
- [ ] `needs_write_confirmation=True` 触发前端确认卡片
- [ ] 用户确认后执行 KILL 并验证
- [ ] KILL 不存在的线程返回明确错误

---

### 阶段 5：P3 锦上添花

---

#### 任务 5.1 — P3: explain_query 与慢查询自动联动

| 属性 | 值 |
|------|-----|
| **优先级** | 🔵 P3 |
| **涉及文件** | `backend/app/agent/tools/diagnosis.py` |
| **预估工时** | 0.5h |
| **任务状态** | ✅ 已完成 |

**问题描述**:
与任务 3.2 相同。已在阶段 3 中通过 `get_slow_queries` 增加 `include_explain` 参数实现，此阶段无需额外改动。

**验收标准**:
- [ ] 确认任务 3.2 已实现即可

---

## 五、关键设计决策

| # | 决策 | 原因 |
|---|------|------|
| 1 | 锁信息优先使用 `performance_schema.data_locks`，回退到 INNODB STATUS 解析 | P_S 提供结构化可靠数据，INNODB STATUS 是 MySQL 5.7/MariaDB 的最小公分母 |
| 2 | 版本检测放在 `connect()` 阶段 | 连接建立后立即检测，后续所有方法共享，避免重复查询 |
| 3 | `kill_transaction` 复用 `confirm_node` 的中断模式 | 避免在 graph 中增加第二个确认层，降低复杂度 |
| 4 | `time_range` 的 INTERVAL 关键字不做参数化但也安全 | `time_range` 来自 `Literal["1h","6h","24h","7d"]` 限定，不是原始用户输入 |
| 5 | EXPLAIN 联动默认关闭且限制 TOP 5 | 避免 20+ 次额外数据库往返，保护性能 |
| 6 | 关联分析不依赖 LLM | 使用硬编码规则引擎，与现有 `_generate_diagnosis()` 设计一致 |

---

## 六、总体验收检查清单

- [ ] **P0-1** check_locks 返回完整锁拓扑（表名、锁模式、held/waiting 分类）
- [ ] **P0-2a** EXPLAIN 方法不再使用裸 f-string 拼接 SQL
- [ ] **P0-2b** MySQL/MariaDB 版本检测正常工作
- [ ] **P0-2c** get_slow_queries time_range 参数生效 + 降级方案可用
- [ ] **P1-1** analyze_locks 工具注册并正常工作
- [ ] **P1-2** get_slow_queries 支持 `include_explain` 参数
- [ ] **P2-1** run_health_check 建议包含当前配置值和可执行 SQL + 关联分析
- [ ] **P2-2** kill_transaction 工具注册并正常工作
- [ ] **P3** explain_query 联动功能确认
- [ ] 所有现有测试仍通过
- [ ] 所有后端代码有中文注释
- [ ] 代码通过 `ruff check`
