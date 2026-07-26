# 通用化 confirm_node 危险操作确认机制

> 状态：✅ 方案已评审通过，待实施
> 创建：2026-07-24
> 作者：@蒙瑞彬

---

## 1. 当前问题

### 1.1 问题描述

`confirm_node`（LangGraph 图中负责写操作确认的节点）的检测逻辑存在硬编码：它只拦截声明了 `needs_write_confirmation` **且** 工具参数中含 `sql` 字段 **且** `is_write_dml(sql)` 返回 `True` 的工具调用。

这导致 `kill_transaction`（声明了 `needs_write_confirmation: True`，但参数只有 `thread_id` 没有 `sql`）**静默绕过确认流程直接执行**，存在安全漏洞。

### 1.2 根因分析

[graph.py:393-405](backend/app/agent/graph.py#L393-L405) 的分类循环：

```python
for tc in tool_calls:
    tool_fn = TOOL_REGISTRY.get(tc["name"])
    extras = getattr(tool_fn, "extras", None) or {}
    if extras.get("needs_write_confirmation"):   # ← kill_transaction 能走到这里
        sql = tc["args"].get("sql", "")           # ← 但这里拿到空字符串
        if sql and is_write_dml(sql):             # ← 空字符串 → False
            writes.append({...})
            continue
    safe.append(tc)  # ← kill_transaction 被当作"安全工具"透传
```

硬编码了三层耦合：
1. 必须声明 `needs_write_confirmation`
2. 必须有 `sql` 参数
3. `is_write_dml(sql)` 必须为 True

任何不满足第 2、3 条的危险工具（当前是 `kill_transaction`，未来可能更多）都会绕过确认。

### 1.3 影响范围

| 工具 | `needs_write_confirmation` | 有 `sql` 参数 | 当前是否被拦截 |
|---|---|---|---|
| `execute_sql`（写 DML） | ✅ | ✅ | ✅ 是 |
| `kill_transaction` | ✅ | ❌（只有 `thread_id`） | ❌ 否（Bug） |

---

## 2. 方案目标

1. **声明即确认**：只要工具在 `extras` 中声明 `needs_write_confirmation: True`，就触发 `interrupt()` 等待用户确认，**无需任何额外条件判断**
2. **分类渲染**：前端按 `confirm_category` 分类渲染不同类型的危险操作，确保用户明确知晓每次操作的后果
3. **未来兼容**：新增危险操作工具只需声明 extras 即可接入确认流程，无需修改 confirm_node

---

## 3. 方案简述

### 3.1 核心思路

```
拆分 execute_sql → execute_readonly_sql + execute_write_sql
        ↓
confirm_node 规则简化为一行：
  needs_write_confirmation == True → 无条件确认
        ↓
前端按 confirm_category 分类渲染：
  sql_write → SQL 代码块
  connection_kill → 红色警告卡片
  generic → key-value 表（降级兜底）
```

### 3.2 架构对比

| | 改前 | 改后 |
|---|---|---|
| confirm_node 规则 | 3 层条件（extras + sql + is_write_dml） | 1 条（extras 即可） |
| SQL 工具数量 | 1 个（execute_sql 涵盖读写） | 2 个（职责分离） |
| 前端确认 UI | 单一 SQL 卡片 | category 驱动，3 种渲染 |
| 新增危险工具成本 | 需改 confirm_node 分类逻辑 | 声明 extras 即可 |

### 3.3 安全纵深

即使 LLM 错把 `INSERT` 发给 `execute_readonly_sql`，`SQLAuditCheck`（`needs_sql_audit`）仍会检测并阻断非 admin 用户，形成双层防护。

---

## 4. 任务拆分

### 任务 1：拆分 execute_sql 工具

**文件**：`backend/app/agent/tools/query.py`

**内容**：
- 将现有 `execute_sql` 函数体重命名为 `_run_sql()`（内部函数，不注册）
- 新增 `execute_readonly_sql` 工具：
  - `@tool(name="execute_readonly_sql", extras={"needs_sql_audit": True, "needs_row_estimation": True})`
  - 文档明确"只读查询，写操作请用 execute_write_sql"
  - 函数体委托给 `_run_sql()`
- 新增 `execute_write_sql` 工具：
  - `@tool(name="execute_write_sql", extras={"needs_write_confirmation": True, "confirm_category": "sql_write"})`
  - 文档明确"写操作，执行前需用户确认"
  - 函数体委托给 `_run_sql()`
  - **不声明 `needs_sql_audit` 和 `needs_row_estimation`**（用户确认本身就是最可靠的安全屏障）

### 任务 2：更新工具注册表

**文件**：`backend/app/agent/tools/registry.py`

**内容**：
- 移除 `execute_sql` 导入
- 新增 `execute_readonly_sql`、`execute_write_sql` 导入
- 更新 `AGENT_TOOLS` 列表（11 个工具）

### 任务 3：重构 confirm_node 为无条件规则

**文件**：`backend/app/agent/graph.py`

**内容**：
- 新增 `_CONN_INJECTED_PARAMS` 常量（连接注入参数白名单）
- 新增 `_build_confirmable_action(tc, extras)` 辅助函数：
  - 过滤连接注入参数，保持 details 干净
  - 自动生成 `description`（人类可读操作描述）
  - 返回 `{tool_call_id, tool, category, description, details}`
- 重写分类循环（移除 `is_write_dml()` 调用）：
  ```python
  for tc in tool_calls:
      extras = getattr(tool_fn, "extras", None) or {}
      if extras.get("needs_write_confirmation"):
          writes.append(_build_confirmable_action(tc, extras))
      else:
          safe.append(tc)
  ```
- 删除 `from app.engine.sql_auditor import is_write_dml` 导入
- 审计日志：`w["sql"]` → `w["description"]`
- 拒绝消息：`"写操作已被用户取消: {sql}"` → `"操作已被用户取消: {description}"`

### 任务 4：标注 kill_transaction 的 confirm_category

**文件**：`backend/app/agent/tools/troubleshoot.py`

**内容**：
- 在 `kill_transaction` 的 `@tool(extras={...})` 中添加 `"confirm_category": "connection_kill"`

### 任务 5：更新 System Prompt

**文件**：`backend/app/agent/graph.py` 中的 `_build_system_prompt()` 函数

**内容**：
- 第 10 条工作原则：`execute_sql` → `execute_write_sql`
- 新增说明：只读查询使用 `execute_readonly_sql`

### 任务 6：更新前端类型定义

**文件**：`frontend/src/types/chat.ts`

**内容**：
- 新增 `ConfirmCategory` 类型：`'sql_write' | 'connection_kill' | 'generic'`
- 新增 `ConfirmableWrite` 接口：`{tool_call_id, tool, category, description, details}`
- `ConfirmRequiredEvent.writes` 从 `Array<{tool_call_id, tool, sql}>` 改为 `ConfirmableWrite[]`

### 任务 7：更新前端 Store

**文件**：`frontend/src/stores/chat.ts`

**内容**：
- `pendingConfirm` 类型更新为 `{ writes: ConfirmableWrite[] } | null`

### 任务 8：重构 WriteConfirmation.vue 为 category 驱动渲染

**文件**：`frontend/src/components/chat/WriteConfirmation.vue`

**内容**：
按 `writes[0].category` 切换三种渲染模式：

| Category | 图标 | 标题 | 内容区 | 按钮文案 | 强调色 |
|---|---|---|---|---|---|
| `sql_write` | 蓝色铅笔 | "需要确认执行写操作" | SQL 语法高亮代码块 | "确认执行" | `--confirm-accent` |
| `connection_kill` | 红色三角警告 | "需要确认终止数据库连接" | 线程详情 key-value 表 + "此操作不可逆"警告行 | "确认终止" | `--confirm-danger` |
| `generic` | 灰色信息圆 | "需要确认执行操作" | 工具名 + 参数 key-value 表 | "确认执行" | `--confirm-generic` |

- 1.5s 冷却机制：所有类别统一保留
- `MessageBubble.vue` 的 `isWaitingApproval` 无需变更（仅依赖 `tool_call_id` 匹配）

---

## 5. 验收标准

### 5.1 功能验证

| 编号 | 场景 | 预期行为 | 优先级 |
|---|---|---|---|
| V1 | 用户请求"终止线程 12345" | 前端弹出红色警告卡片，显示线程详情，"确认终止"按钮 | P0 |
| V2 | 用户确认终止操作 | kill_transaction 正常执行，返回结果 | P0 |
| V3 | 用户取消终止操作 | 返回"操作已被用户取消"，不执行 KILL | P0 |
| V4 | 用户请求"INSERT INTO t VALUES (1)" | 前端弹出蓝色 SQL 卡片，语法高亮，"确认执行"按钮 | P0 |
| V5 | 用户请求"SELECT * FROM t" | 不弹出确认，直接返回查询结果 | P1 |
| V6 | 用户请求"SHOW TABLES" | 不弹出确认，通过 execute_readonly_sql 执行 | P1 |
| V7 | 同批次混合危险+安全工具 | 只读工具与危险工具分离，仅危险操作需确认 | P1 |
| V8 | 全部拒绝后 | LLM 回复告知用户操作已取消 | P1 |
| V9 | 60s 无操作 | 自动拒绝所有待确认操作 | P2 |
| V10 | 未来工具的未知 category | 降级到 generic 渲染（key-value 表） | P2 |

### 5.2 回归验证

| 编号 | 场景 | 预期行为 |
|---|---|---|
| R1 | 现有只读查询（list_tables、describe_table、check_locks 等） | 无异变，正常执行 |
| R2 | explain_query 分析执行计划 | 无异变，正常执行 |
| R3 | run_health_check 20 项巡检 | 无异变，正常执行 |
| R4 | SSE 流式 token 渲染 | 无异变 |
| R5 | 会话历史加载与切换 | 无异变 |

### 5.3 代码质量

- [ ] `is_write_dml()` 调用已从 `confirm_node` 中移除
- [ ] `execute_sql` 工具名已从所有后端文件中移除（`AGENT_TOOLS`、prompt、import）
- [ ] `execute_write_sql` 不声明 `needs_sql_audit` 和 `needs_row_estimation`
- [ ] `execute_readonly_sql` 声明了 `needs_sql_audit` 和 `needs_row_estimation`
- [ ] 审计日志中不再包含 `sql` 字段依赖，改用 `description`
- [ ] 前端确认组件无 `w.sql` 的直接访问，改用 `w.details?.sql`（`sql_write` 分支内）

---

## 6. 任务状态

| 任务 | 文件 | 状态 |
|---|---|---|
| 任务 1：拆分 execute_sql | `backend/app/agent/tools/query.py` | ✅ 已完成 |
| 任务 2：更新工具注册表 | `backend/app/agent/tools/registry.py` | ✅ 已完成 |
| 任务 3：重构 confirm_node | `backend/app/agent/graph.py` | ✅ 已完成 |
| 任务 4：标注 kill_transaction | `backend/app/agent/tools/troubleshoot.py` | ✅ 已完成 |
| 任务 5：更新 System Prompt | `backend/app/agent/graph.py` | ✅ 已完成 |
| 任务 6：更新前端类型 | `frontend/src/types/chat.ts` | ✅ 已完成 |
| 任务 7：更新前端 Store | `frontend/src/stores/chat.ts` | ✅ 已完成 |
| 任务 8：重构确认组件 | `frontend/src/components/chat/WriteConfirmation.vue` | ✅ 已完成 |

### 额外清理

| 文件 | 变更 |
|---|---|
| `backend/app/agent/safety.py` | 更新 `RowEstimationCheck` 引用 (`execute_sql` → `execute_readonly_sql`) |
| `backend/app/api/chat.py` | 更新 `_infer_message_type` 引用 |
| `backend/app/agent/tools/diagnosis.py` | 更新 docstring 引用 |
| `backend/tests/test_graph.py` | 更新测试用例引用 |

---

## 7. 变更历史

| 日期 | 变更 |
|---|---|
| 2026-07-24 | 初始方案创建，团队评审通过 |
| 2026-07-24 | 全部 8 个任务实施完成，128 项后端测试通过，前端 TypeScript 类型检查通过 |
