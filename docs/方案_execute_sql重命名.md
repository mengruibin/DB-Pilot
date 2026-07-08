# 方案：消除 `run_query` 工具命名歧义，让 LLM 正确执行写操作

> 文档版本：v1.0
> 创建日期：2026-07-06
> 状态：待实施

---

## 一、背景与问题

### 问题描述

用户发消息"帮我在用户表插入一个名为小孟的记录"，LLM 的决策轨迹为：

```
list_tables → describe_table → SELECT 查询现有数据 → "我当前的工具仅支持只读查询"
```

LLM 在最后一步放弃了，因为它读取到 `run_query` 工具的 LangChain docstring 首句为：

```
"""执行只读 SQL 查询并返回结果。"""
```

"只读"二字让 LLM 认为该工具不能执行 INSERT/UPDATE/DELETE。

### 实际能力

但系统的真实能力是：当 `user_role=admin` 时：
- **SQLAuditCheck** 放行 INSERT/UPDATE/DELETE
- **ReadOnlyCheck** 对 admin 角色不拦截 （后续于 2026-07-08 移除 ReadOnlyCheck 类，功能合并至 SQLAuditCheck）
- **MySQL 适配器** 对 admin 角色跳过 `SET SESSION TRANSACTION READ ONLY`

**问题不在安全层，而在 LLM 通信层**——工具名称和描述告诉 LLM 的信息 ≠ 工具实际能力。

### 根因

| 层 | 问题 |
|---|---|
| 工具名 | `run_query` 中的 "query" 暗示只读查询 |
| 工具描述首句 | 写死 "执行只读 SQL 查询" |
| 返回值 | `is_readonly` 始终硬编码为 `True` |
| SSE 事件 | 同前，始终 `True` |
| 系统 Prompt | 完全没有写操作相关的指引 |

---

## 二、方案选型

### 选项对比

| 维度 | 选项 A：重命名 `execute_sql` ✅ | 选项 B：仅改描述 | 选项 C：拆两个工具 |
|------|------|------|------|
| 清晰度 | 最高，名实一致 | 名称仍说"query" | 最高但复杂 |
| 改动量 | 中（~8 处） | 小 | 大 |
| LLM 友好度 | 高 | 中（小模型可能仍依名称推断） | 中（多一个选择） |
| 安全性 | 不变 | 不变 | 最高但代价大 |

### 推荐：选项 A

重命名 `run_query` → `execute_sql`，名称和描述准确反映工具能力。安全层负责强制策略，LLM 层只负责诚实地描述能力。

---

## 三、设计要点

### 3.1 工具名

`run_query` → `execute_sql`

LangChain 默认从函数名派生 tool name，重命名函数即可。

### 3.2 返回值 `is_readonly`

不再硬编码，改为**动态判断实际 SQL 类型**：

```python
_IS_READONLY_SQL = re.compile(
    r"^\s*(?:SELECT|SHOW|DESC|DESCRIBE|EXPLAIN|WITH|USE|SET)\b",
    re.IGNORECASE,
)
"is_readonly": bool(_IS_READONLY_SQL.match(sql)),
```

SELECT → `true`，INSERT → `false`，DDL 在审计层已被拦截。

### 3.3 SSE 事件 `is_readonly`

`tool_node.py` 中从 `result.get("is_readonly", True)` 读取，不再写死 `True`。

### 3.4 ReadOnlyCheck 增强

从 stub（始终返回 `blocked=False`）变为**实际检查**——非 admin 角色执行非 SELECT 语句时拦截，形成第二道防线。

> **后续变更**：ReadOnlyCheck 类已在 2026-07-08 重构中移除，其功能已合并至 SQLAuditCheck（通过 `_ADMIN_ONLY_STATEMENTS` 覆盖非 admin 写操作）。

### 3.5 写操作日志

写操作成功时额外记录：`sql_type`（INSERT/UPDATE/DELETE）、`affected_rows`、`user_role`，便于审计追踪。

### 3.6 系统 Prompt

增加两条写操作指引，让 LLM 明确知道：
- 它可以执行写操作
- 写操作受 user_role 控制
- 被拦截时应告知用户权限不足

---

## 四、改动清单

### 任务 1：重命名工具函数（`query.py`）

| 项 | 说明 |
|---|---|
| 文件 | `backend/app/agent/tools/query.py` |
| 改动 | 函数名 `run_query` → `execute_sql`，docstring 首句改为"执行 SQL 语句并返回结果"，增加角色权限说明 |
| 子改动 | 增加 `_IS_READONLY_SQL` 正则，`is_readonly` 动态化，`summary` 区分"返回 N 行"/"影响 N 行"，写操作日志 |
| 验收 | `ruff check` 通过，`pytest` 通过，函数名正确注册为 `execute_sql` |

### 任务 2：更新工具注册（`registry.py`）

| 项 | 说明 |
|---|---|
| 文件 | `backend/app/agent/tools/registry.py` |
| 改动 | import 和 AGENT_TOOLS 列表中 `run_query` → `execute_sql` |
| 验收 | 启动时无 ImportError |

### 任务 3：更新安全护栏（`safety.py`）

| 项 | 说明 |
|---|---|
| 文件 | `backend/app/agent/safety.py` |
| 改动 | 4 处 `tool_name in ("run_query", ...)` → `"execute_sql"`；ReadOnlyCheck 从 stub 增强为实际检查（后于 2026-07-08 移除合并至 SQLAuditCheck） |
| 验收 | admin 放行 INSERT，readonly 拦截 INSERT，DDL 始终拦截 |

### 任务 4：修复 SSE 事件（`tool_node.py`）

| 项 | 说明 |
|---|---|
| 文件 | `backend/app/agent/tool_node.py` |
| 改动 | 第 204 行 `"is_readonly": True` → `"is_readonly": result.get("is_readonly", True)` |
| 验收 | SSE sql 事件的 `is_readonly` 正确反映 SQL 类型 |

### 任务 5：更新消息类型推断（`chat.py`）

| 项 | 说明 |
|---|---|
| 文件 | `backend/app/api/chat.py` |
| 改动 | 第 465 行 `"run_query"` → `"execute_sql"` |
| 验收 | `_infer_message_type` 正确识别 execute_sql 为 "query" 类型 |

### 任务 6：更新系统 Prompt（`graph.py`）

| 项 | 说明 |
|---|---|
| 文件 | `backend/app/agent/graph.py` |
| 改动 | `_build_system_prompt()` 增加写操作指引（第 9~10 条原则） |
| 验收 | LLM 收到写操作请求时主动调用 execute_sql |

### 任务 7：更新测试（`test_graph.py`）

| 项 | 说明 |
|---|---|
| 文件 | `backend/tests/test_graph.py` |
| 改动 | 4 处 `"run_query"` → `"execute_sql"` |
| 验收 | 4 项测试全部通过 |

### 任务 8：更新前端注释（`chat.ts`）

| 项 | 说明 |
|---|---|
| 文件 | `frontend/src/api/chat.ts` |
| 改动 | 第 27 行注释 "执行只读 SQL" → "执行 SQL" |
| 验收 | 纯注释变更，无功能影响 |

---

## 五、安全分析

该方案**不降低安全性**。实施后安全链仍为：

```
LLM 决定写操作 → execute_sql 被调用
  ├── Step 1: SQLAuditCheck ─── 拦截 DDL（所有角色）
  │                             拦截 DML 非 admin 用户
  ├── Step 2: [已移除] ReadOnlyCheck 曾在此，功能已合并至 Step 1
  ├── Step 3: PerformanceCheck ─ 非阻断，仅警告
  ├── Step 4: 工具内 audit() ── 第二道审计
  └── Step 5: MySQL READ ONLY ── 数据库级兜底
```

改动只是移除了 LLM 通信层的"人工栅栏"，让 LLM 可以**尝试**写操作，是否放行完全由安全链决定。

---

## 六、验证标准

### 6.1 自动化

```bash
cd backend
ruff check app/agent/tools/query.py app/agent/safety.py app/agent/tool_node.py app/api/chat.py app/agent/graph.py
pytest tests/ -v
```

### 6.2 端到端场景

| 场景 | 输入 | 预期行为 | 验证方式 |
|------|------|---------|---------|
| 1. admin + INSERT | "帮我在 users 表插入一个名为小孟的记录" | LLM 调用 execute_sql，INSERT 执行成功，前端显示 🟡 "此操作将修改数据"，返回 `is_readonly=false`，`summary="影响 1 行"` | 日志可见 `写操作执行成功` |
| 2. readonly + INSERT | 同上，角色为 readonly | SQLAuditCheck 拦截（ReadOnlyCheck 已合并至此），返回 `audit_status="blocked"` | LLM 告诉用户权限不足 |
| 3. 所有角色 + DDL | "帮我把 users 表删掉" | SQLAuditCheck 拦截 DROP，返回 `audit_status="blocked"` | 行为不变 |
| 4. 所有角色 + SELECT | "查询 users 表" | `is_readonly=true`，正常返回数据 | 行为不变 |

---

## 七、任务跟踪

| # | 任务 | 文件 | 状态 |
|---|------|------|------|
| 1 | 重命名工具函数 + 更新描述/返回值/日志 | `query.py` | ✅ 已完成 |
| 2 | 更新工具注册 | `registry.py` | ✅ 已完成 |
| 3 | 更新安全护栏引用 + 增强 ReadOnlyCheck | `safety.py` | ✅ 已完成（后于 2026-07-08 移除合并至 SQLAuditCheck） |
| 4 | 修复 SSE 事件 is_readonly | `tool_node.py` | ✅ 已完成 |
| 5 | 更新消息类型推断 | `chat.py` | ✅ 已完成 |
| 6 | 更新系统 Prompt | `graph.py` | ✅ 已完成 |
| 7 | 更新测试 | `test_graph.py` | ✅ 已完成 |
| 8 | 更新前端注释 | `frontend/src/api/chat.ts` | ✅ 已完成 |

**状态标记**：⏳ 待实施 → 🔄 实施中 → ✅ 已完成
