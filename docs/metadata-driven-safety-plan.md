# 元数据驱动的安全护栏方案

## 方案简述

### 问题

Agent 工具的安全护栏存在两个问题：

1. **硬编码工具名列表** — `SQLAuditCheck` 和 `RowEstimationCheck` 内部通过 `tool_name not in ("execute_sql", "explain_query")` 判断是否执行检查。新增需要审计的工具必须同步修改 safety.py，违反开闭原则，也增加了遗漏风险。

2. **双重审计** — `execute_sql` 和 `explain_query` 的 SQL 审计在安全护栏层（`safety.py:94`）和工具函数内部（`query.py:359` / `diagnosis.py:113`）各执行一次，浪费 CPU 资源。

### 目标

- 安全检查完全由安全层负责，工具函数内部不再重复审计
- 工具通过元数据声明自己的安全需求，新增工具无需修改安全层代码
- 不移除非 Agent 路径（REST API、NL2SQL 引擎）的审计调用

### 核心思路

利用 LangChain `@tool` 装饰器原生支持的 `extras: dict[str, Any]` 参数，工具在定义时声明自己的安全需求。`_run_one_tool` 在运行时根据元数据动态解析需要执行的安全检查列表，只对声明了需求的工具执行对应检查。

### 架构变化

```
改动前:
  _run_one_tool
    1. 注入连接参数
    2. run_safety_checks(对所有工具) → 内部靠 tool_name 硬编码决定是否干活
    3. TOOL_REGISTRY 查找
    4. 调用工具(工具内部又 audit() 一次)
    5. 构造 SSE

改动后:
  _run_one_tool
    1. 注入连接参数
    2. TOOL_REGISTRY 查找 ← 提前到检查之前
    3. 读取 tool_fn.extras → _resolve_checks() 决定哪些检查需要
    4. run_safety_checks(checks=applicable) ← 只跑相关检查
    5. 调用工具(不再有内部 audit)
    6. 构造 SSE
```

---

## 任务分解

### Task 1：为工具添加 extras 元数据

在需要 SQL 审计的工具函数上添加 `@tool(extras=...)` 参数。

#### 涉及文件

- `backend/app/agent/tools/query.py`
- `backend/app/agent/tools/diagnosis.py`

#### 执行步骤

1. `backend/app/agent/tools/query.py:306` — `execute_sql` 的 `@tool` 装饰器改为：

   ```python

2. `backend/app/agent/tools/diagnosis.py:76` — `explain_query` 的 `@tool` 装饰器改为：

   ```python
   @tool(extras={"needs_sql_audit": True})
   async def explain_query(
   ```

3. 其他工具（`list_tables`、`describe_table`、`get_slow_queries`、`check_connections`、`check_locks`、`check_replication`、`run_health_check`）**不添加 extras** — 它们使用硬编码只读 SQL，默认跳过安全检查。

#### 验收标准


---

### Task 2：在 tool_node.py 中实现元数据驱动的检查解析

在 `_run_one_tool` 中，**将 TOOL_REGISTRY 查找提前到安全护栏检查之前**，然后根据工具元数据决定跑哪些检查。

#### 涉及文件

- `backend/app/agent/tool_node.py`

#### 执行步骤

1. **新增导入** — 从 `app.agent.safety` 导入 `SQLAuditCheck`、`RowEstimationCheck` 和相关函数（它们目前可能通过 `run_safety_checks` 间接依赖，需要确认导入路径）。

2. **新增 `_resolve_checks(tool_fn)` 函数**：

   ```python
   def _resolve_checks(tool_fn: BaseTool) -> list[SafetyCheck]:
       """根据工具元数据解析需要执行的安全检查列表。

       工具通过 @tool(extras={...}) 声明安全需求，
       此函数将其映射为具体的 SafetyCheck 实例列表。
       未声明需求的工具返回空列表（跳过安全检查）。

       Args:
           tool_fn: BaseTool 实例。

       Returns:
           需要执行的 SafetyCheck 列表（可能为空）。
       """
       extras = getattr(tool_fn, "extras", None) or {}
       checks: list[SafetyCheck] = []
       if extras.get("needs_sql_audit"):
           checks.append(SQLAuditCheck())
   ```

3. **重排 `_run_one_tool` 中的执行顺序**：

   将 TOOL_REGISTRY 查找（原第 122 行左右）移到安全护栏检查（原第 85 行左右）**之前**：

   ```python
   # ── 1. 连接配置注入 ──
   for key in (...):
       ...
   
   # ── 2. 查找工具（提前到安全检查之前） ──
   tool_fn = TOOL_REGISTRY.get(tool_name)
   if tool_fn is None:
       return { ... error ... }
   
   # ── 3. 根据元数据解析安全检查列表 ──
   applicable_checks = _resolve_checks(tool_fn)
   
   # ── 4. 安全护栏检查 ──
   safety_result = await run_safety_checks(
       tool_name, tool_args, conn_config, checks=applicable_checks
   )
   if safety_result.blocked:
       return { ... blocked ... }
   ```

   > 注意：原来未注册工具的检查在 safety check 之后，现在提前到之前。
   > 行为不变：未注册工具仍然返回错误，只是更快（少跑一次 safety check）。

4. **处理未注册工具**：移除了原来的 TOOL_REGISTRY 查找代码块（原 120-140 行附近），由新的第 2 步统一处理。

#### 验收标准

- `execute_sql` 调用时：`_resolve_checks` 返回 `[SQLAuditCheck(), RowEstimationCheck()]`（注：PerformanceCheck 已移除）
- `explain_query` 调用时：`_resolve_checks` 返回 `[SQLAuditCheck()]`
- `list_tables` 调用时：`_resolve_checks` 返回 `[]`（空列表 — 跳过所有检查）
- 未注册工具：直接返回错误，不执行安全检查

---

### Task 3：简化 safety.py 中的检查器

删除 `SQLAuditCheck` 和 `RowEstimationCheck` 中的硬编码 `tool_name` 过滤，因为它们现在只会在有声明需求的工具上被执行。

#### 涉及文件

- `backend/app/agent/safety.py`

#### 执行步骤

1. **`SQLAuditCheck.check()`**（第 74-115 行）— 删除第 81-82 行：

   ```python
   # 删除这两行：
   if tool_name not in ("execute_sql", "explain_query"):
       return SafetyResult(blocked=False)
   ```

   保留后面的 SQL 判空检查（`if not sql: return SafetyResult(blocked=False)`）作为防御性编程。

2. **`RowEstimationCheck.check()`**（第 239-286 行）— 删除第 255-256 行：

   ```python
   # 删除这两行：
   if tool_name not in ("execute_sql",):
       return SafetyResult(blocked=False)
   ```

   保留后面的 SQL 判空和性能检查逻辑。

#### 验收标准

- 两个 check 方法不再引用任何工具名常量
- 传入 `sql=""` 时仍返回 `blocked=False`（判空保护还在）
- 功能行为无变化（因为调用方只在必要时才传入这些 check）

---

### Task 4：删除工具函数内部的重复 audit() 调用

`execute_sql` 和 `explain_query` 内部各自调用了 `audit()`。由于安全层（Task 3 调整后的 `SQLAuditCheck`）已在上游拦截危险 SQL，工具函数内部的审计是冗余的。

#### 涉及文件

- `backend/app/agent/tools/query.py`
- `backend/app/agent/tools/diagnosis.py`

#### 执行步骤

1. **`backend/app/agent/tools/query.py`** — 删除第 357-373 行的 SQL 审计代码块。具体来说，从 `# Step 1: SQL 安全审计` 注释开始到 `is_readonly = bool(_IS_READONLY_SQL.match(sql))` 之前，替换为只保留 `is_readonly` 判断：

   ```python
   # Step 1: SQL 安全审计（由上游安全护栏层负责）
   # SAFETY: 不再在此重复审计，由 tool_node._run_one_tool 中的
   # SQLAuditCheck 在工具执行前统一拦截。
   # 此处仅做只读/写操作判断，用于返回值和日志。
   is_readonly = bool(_IS_READONLY_SQL.match(sql))
   ```

2. **`backend/app/agent/tools/diagnosis.py`** — 删除第 111-123 行的 SQL 审计代码块：

   ```python
   # Step 1: SQL 安全审计（由上游安全护栏层负责）
   # SAFETY: 不再在此重复审计，由 tool_node._run_one_tool 中的
   # SQLAuditCheck 在工具执行前统一拦截。
   ```

   > 注意：`explain_query` 中 `audit()` 的返回值用于判断是否拦截，审计拦截后返回 `{"error": ..., "violations": ...}`。删除后，这个拦截逻辑也一并移除（由上游替代）。

3. **检查 `query.py` 顶部的 `audit` 导入**（第 23 行：`from app.engine.sql_auditor import audit`）：
   - `execute_sql` 删除 `audit()` 调用后，`query.py` 中是否还有其他地方使用 `audit`？
   - 经检查：只有第 359 行用了 `audit()`。删除后可以移除该导入。
   - `diagnosis.py` 同理：只有第 113 行用了 `audit()`。删除后移除导入。

   > **例外**：如果导入还有其他用途（如类型注解），则保留。实际确认后再决定。

#### 验收标准

- `execute_sql` 调用 `SELECT 1`：正常返回结果
- `execute_sql` 调用 `DROP TABLE users`：安全层拦截返回 `ToolMessage(content="操作被安全策略拦截: ...")`，工具函数内部不再返回 `{"audit_status": "blocked"}` 的 dict
- `explain_query` 同理

---

### Task 5：验证测试

#### 执行步骤

1. 运行全部后端测试：

   ```bash
   pytest backend/tests/ -v
   ```

2. 重点检查与工具执行和审计相关的测试：

   ```bash
   pytest backend/tests/test_tool_node.py -v
   pytest backend/tests/test_sql_auditor.py -v
   ```

3. 检查是否有测试 mock 了 `audit()` 并依赖它被调用两次的模式 — 如果有，相应调整。

#### 验收标准

- 所有现有测试通过
- 没有测试因删除重复 audit 而失败（测试不应依赖"被调用了两次"这个内部实现细节）

---

## 任务状态

| 任务 | 文件 | 状态 | 备注 |
|------|------|------|------|
| Task 1：添加 extras 元数据 | `query.py`, `diagnosis.py` | ✅ 已完成 | 只改两行 @tool 装饰器 |
| Task 2：元数据驱动的检查解析 | `tool_node.py` | ✅ 已完成 | 核心改动：重排顺序 + _resolve_checks |
| Task 3：简化 safety.py 检查器 | `safety.py` | ✅ 已完成 | 删除约 4 行硬编码过滤 |
| Task 4：删除工具内部重复 audit | `query.py`, `diagnosis.py` | ✅ 已完成 | 删除 audit() 调用 + 对应导入 |
| Task 5：验证测试 | — | ✅ 已完成 | 74 passed, 5 skipped |

## 不修改的文件

| 文件 | 原因 |
|------|------|
| `app/api/query.py` | 非 Agent 路径（REST API），保留自有 audit |
| `app/engine/nl2sql.py` | 非 Agent 路径（NL2SQL 引擎），保留自有 audit |
| `app/engine/sql_auditor.py` | `audit()` 函数本身不变，只是减少调用方 |
| `app/agent/tools/troubleshoot.py` | 工具使用硬编码只读 SQL，不加 extras |
| `app/agent/tools/health.py` | 同上 |
| `app/agent/tools/registry.py` | 注册逻辑不变 |
| `app/agent/graph.py` | 图结构不变 |
| `app/agent/models.py` | 不涉及 |
| `app/agent/state.py` | 不涉及 |

## 风险与注意事项

1. **安全层是唯一拦截点** — 删除工具内部 audit 后，`execute_sql` / `explain_query` 的安全性完全依赖 `_run_one_tool` 中的 `SQLAuditCheck`。如果有人绕过 `safe_tools_node` 直接调工具函数，会失去审计保护。但当前 LangGraph 架构中所有工具调用都经过 `safe_tools_node`，绕过需要刻意修改 graph.py。

2. **非 Agent 路径不受影响** — REST API（`api/query.py`）和 NL2SQL 引擎（`nl2sql.py`）各自保留自己的 `audit()` 调用，不受此次改动影响。

3. **新增工具流程** — 新增需要 SQL 审计的工具时，只需在 `@tool(extras={"needs_sql_audit": True})` 中声明，无需修改 safety.py。
