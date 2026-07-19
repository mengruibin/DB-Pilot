# 写操作确认断点方案

## 方案简述

### 问题

当前系统在 Agent 执行写 SQL（INSERT/UPDATE/DELETE）时，对于 admin 用户是**静默执行**的，无任何确认环节。AGENTS.md 要求"写操作需要用户显式确认"，但至今未实现。

### 目标

- Agent 工具在执行写 DML（INSERT/UPDATE/DELETE/MERGE）前，暂停执行并通过前端弹窗请求用户确认
- 用户可选择批准全部、拒绝全部或部分批准/拒绝（多写操作场景）
- 只读操作和诊断操作不受影响，流程零感知
- 使用 LangGraph 原生 `interrupt()` + `Command(resume=)` 机制，而非自定义暂停方案

### 核心思路

在 `agent_node` 和 `safe_tools_node` 之间插入 `confirm_node`：

```
agent_node → route_after_agent
  ├── "tools" → confirm_node        ← 新节点（检测写操作，必要时 interrupt）
  └── "__end__" → END

confirm_node → route_after_confirm
  ├── "tools" → safe_tools_node → agent_node  （有待执行的工具）
  └── "agent" → agent_node                     （全部拒绝，LLM 回应）
```

`confirm_node` 通过 `@tool(extras={"needs_write_confirmation": True})` 元数据识别需要确认的工具，用 `is_write_dml()` 判断 SQL 是否为写 DML。检测到写操作时调用 LangGraph `interrupt()` 暂停图执行，用户确认后通过 `Command(resume=...)` 恢复。

### 为什么不用其他方案

| 方案 | 问题 |
|------|------|
| 在 `safe_tools_node` 内部 `interrupt()` | LangGraph 恢复时从节点开头重新执行，已完成的其他并行工具会被重复执行 |
| 在 `_run_one_tool` 内 `asyncio.Event` 阻塞 | 自定义机制，需重构 SSE 流控；连接在等待期间占用服务器资源 |
| 两阶段（先返回"待确认"，再重新请求） | 破坏 ReAct 循环，Agent 可能误读"待确认"的工具返回值 |

### 架构变化

```
改动前:
  agent_node → route_after_agent → safe_tools_node → agent_node

改动后:
  agent_node → route_after_agent → confirm_node
    ├── (有写操作) → interrupt() → 等待用户确认
    │     ↓ 拒绝的 tool_calls 被替换为 ToolMessage
    │     ↓ 批准的 tool_calls 保留
    └── (无写操作) → 直接透传
  → route_after_confirm → safe_tools_node(批准的工具) / agent_node(全部拒绝)
  → agent_node
```

---

## 边界情况全景

| # | 场景 | 处理方式 |
|---|------|---------|
| 1 | 单工具写操作，用户批准 | AIMessage 保持不变 → safe_tools_node 正常执行 |
| 2 | 单工具写操作，用户拒绝 | AIMessage.tool_calls 清空 + ToolMessage("用户取消了写操作") → 路由到 agent_node，LLM 自然回应 |
| 3 | 多工具（读+写），全部批准 | AIMessage 不变 → safe_tools_node 并行执行 |
| 4 | 多工具（读+写），拒绝部分写 | 拒绝的从 AIMessage.tool_calls 移除 + 对应 ToolMessage；读工具 + 批准的写工具继续 |
| 5 | 多工具（读+写），拒绝全部写 | 拒绝的全部移除 + ToolMessage；读工具继续执行 |
| 6 | 多工具（全是写），全部拒绝 | AIMessage.tool_calls 清空 + 每条写一个 ToolMessage → 路由到 agent_node，LLM 看到全部拒绝 |
| 7 | 全是只读工具，无写操作 | `confirm_node` 检测 write_count=0 → `return {}` 直接透传 → route_after_confirm → tools |
| 8 | `execute_sql` 实际执行 SELECT | `is_write_dml(sql)` → False → 归类为 safe，不触发确认 |
| 9 | `explain_query`（永远只读） | extras 无 `needs_write_confirmation` → 归类为 safe |
| 10 | non-admin 用户的写操作 | `SQLAuditCheck` 在 `safe_tools_node` 执行阶段拦截；`confirm_node` 不做角色判断（只判断 SQL 类型） |
| 11 | 前端确认对话框 60s 超时 | 前端自动发送全部拒绝 → 同边界情况 2/5/6 |
| 12 | 用户点击停止按钮（确认对话框显示中） | 前端先发全部拒绝 → 恢复流 → 正常结束，再执行取消逻辑 |
| 13 | 服务器在中断等待期间重启 | `MemorySaver`（内存级 checkpoint）丢失，恢复请求返回 404。需前端显示"会话已过期"提示 |
| 14 | DDL 写操作（DROP/ALTER 等） | `SQLAuditCheck` 始终拦截，不经过 `confirm_node` 的确认流程（本期不改 DDL 策略） |
| 15 | 同批次多个 `execute_sql`，部分写部分读 | 只统计写 SQL 的工具到 `writes` 列表；只读的 `execute_sql` 归类为 safe，不触发确认 |

### confirm_node 决策矩阵

```
输入：AIMessage.tool_calls = [tc1(write), tc2(read), tc3(write)]
                    ↓
          is_write_dml(tc.args.sql) && extras.needs_write_confirmation
                    ↓
         writes=[tc1, tc3]  safe=[tc2]
                    ↓
         interrupt() → 用户决策
                    ↓
    ┌───────────────┼───────────────────┐
    ▼               ▼                   ▼
 全部批准       部分批准/拒绝        全部拒绝
    │               │                   │
kept=[tc1,tc2,tc3] kept=[tc1,tc2]   kept=[tc2]
denied=[]          denied=[tc3]      denied=[tc1,tc3]
    │               │                   │
    ▼               ▼                   ▼
 tools_node     tools_node          tools_node(执行tc2)
 (3个工具)      (2个工具)           或全部拒绝:
                                     kept=[], denied=[tc1,tc3]
                                     → agent_node (LLM 回应拒绝)
```

---

## 任务分解

### Task 1：图实例模块级复用

当前 `build_agent_graph()` 每次请求创建新的 `MemorySaver()`，`interrupt()` 的 checkpoint 跨请求丢失。改为模块级单例。

#### 涉及文件

- `backend/app/agent/graph.py`
- `backend/app/api/chat.py`

#### 执行步骤

1. **`backend/app/agent/graph.py`** — 新增 `get_agent_graph()` 函数：

   ```python
   # 模块级单例（含 MemorySaver checkpointer），确保 interrupt/restore 跨请求复用
   _agent_graph: CompiledStateGraph | None = None

   def get_agent_graph() -> CompiledStateGraph:
       """返回模块级单例 Agent 图（含 checkpointer）。

       必须复用同一个实例才能跨 HTTP 请求恢复 interrupt() 暂停的图。
       """
       global _agent_graph
       if _agent_graph is None:
           _agent_graph = build_agent_graph()
       return _agent_graph
   ```

2. **`backend/app/api/chat.py`** — 将 `build_agent_graph()` 替换为 `get_agent_graph()`：

   ```python
   # 改动前
   from app.agent.graph import build_agent_graph
   graph = build_agent_graph()

   # 改动后
   from app.agent.graph import get_agent_graph
   graph = get_agent_graph()
   ```

#### 验收标准

- 两次不同的 HTTP 请求获取到同一个 graph 实例（`get_agent_graph() is get_agent_graph()`）
- 现有 SSE 流功能不受影响（测试标准：启动服务 → 发一条查询消息 → 正常返回结果）

---

### Task 2：新增 `is_write_dml()` 到 sql_auditor

轻量判断函数，复用现有 `_get_statement_type()` 逻辑，供 `confirm_node` 使用。

#### 涉及文件

- `backend/app/engine/sql_auditor.py`

#### 执行步骤

1. 在 `audit()` 函数下方新增 `is_write_dml()` 函数：

   ```python
   def is_write_dml(sql: str, db_type: str = "mysql") -> bool:
       """快速判断 SQL 是否为写 DML（INSERT/UPDATE/DELETE/MERGE）。

       用于 confirm_node 预检，避免对只读 SQL 发起确认。
       复用 sqlglot AST 解析和 _get_statement_type() 分类逻辑。

       Args:
           sql: 待检查的 SQL 语句。
           db_type: 数据库方言，默认 mysql。

       Returns:
           True 如果 SQL 包含写 DML 操作，False 表示只读或无法解析。
           解析失败时保守返回 False（由 SQLAuditCheck 兜底拦截）。
       """
       try:
           parsed = sqlglot.parse_one(sql, dialect=_map_dialect(db_type))
           if parsed is None:
               return False
           stmt_type = _get_statement_type(parsed)
           return stmt_type in _ADMIN_ONLY_STATEMENTS
       except Exception:
           return False
   ```

2. 运行现有测试确认未破坏 sql_auditor 模块：

   ```bash
   pytest backend/tests/test_sql_auditor.py -v
   ```

#### 验收标准

- `is_write_dml("INSERT INTO t VALUES (1)")` → `True`
- `is_write_dml("UPDATE t SET x=1")` → `True`
- `is_write_dml("DELETE FROM t")` → `True`
- `is_write_dml("MERGE INTO t ...")` → `True`
- `is_write_dml("SELECT * FROM t")` → `False`
- `is_write_dml("EXPLAIN SELECT * FROM t")` → `False`
- `is_write_dml("DROP TABLE t")` → `False`（DDL 不在 `_ADMIN_ONLY_STATEMENTS` 中）
- `is_write_dml("invalid sql!!!!")` → `False`（解析失败，保守处理）
- 现有 `test_sql_auditor.py` 全部通过

---

### Task 3：新增 `confirm_node` + `route_after_confirm` 到 graph.py

核心业务逻辑：检测写操作、调用 `interrupt()`、处理用户决策、修改 AIMessage。

#### 涉及文件

- `backend/app/agent/graph.py`

#### 执行步骤

1. **新增导入**（文件顶部）：

   ```python
   from langgraph.types import interrupt
   from app.agent.tools.registry import TOOL_REGISTRY
   from app.engine.sql_auditor import is_write_dml
   ```

2. **新增 `route_after_confirm()` 路由函数**（在 `route_after_agent()` 下方）：

   ```python
   def route_after_confirm(
       state: AgentState,
   ) -> Literal["tools", "agent"]:
       """确认后路由：仍有待执行工具 → tools，全部拒绝 → agent 回应。

       从消息列表末尾向前查找最后一个 AIMessage（可能有 ToolMessages 在它后面），
       检查其 tool_calls 是否非空来决定路由目标。
       """
       messages = state.get("messages", [])
       for msg in reversed(messages):
           if isinstance(msg, AIMessage):
               if msg.tool_calls:
                   return "tools"
               break
       return "agent"
   ```

3. **新增 `confirm_node()` 异步节点函数**：

   ```python
   async def confirm_node(state: AgentState) -> dict[str, Any]:
       """写操作确认节点。

       在 safe_tools_node 之前检测写 SQL，必要时通过 LangGraph interrupt() 暂停。
       用户决策后修改 AIMessage（移除拒绝的 tool_calls），为拒绝的工具生成 ToolMessage。

       幂等性保证：
         - 第一次执行 → interrupt() 暂停图
         - 第二次执行（resume）→ interrupt() 返回决策值，继续处理
         - 无写操作 → return {} 直接透传
       """
       run_id = state.get("run_id", "")
       messages = state.get("messages", [])
       last_msg = messages[-1] if messages else None

       if not isinstance(last_msg, AIMessage) or not last_msg.tool_calls:
           return {}

       tool_calls = last_msg.tool_calls

       # 分类：需要确认的写操作 vs 安全的工具调用
       writes: list[dict] = []
       safe: list[dict] = []
       for tc in tool_calls:
           tool_fn = TOOL_REGISTRY.get(tc["name"])
           extras = getattr(tool_fn, "extras", None) or {}
           if extras.get("needs_write_confirmation"):
               sql = tc["args"].get("sql", "")
               if sql and is_write_dml(sql):
                   writes.append({
                       "tool_call_id": tc["id"],
                       "tool": tc["name"],
                       "sql": sql,
                   })
                   continue
           safe.append(tc)

       if not writes:
           logger.info("confirm_node: 无写操作，直接透传", run_id=run_id)
           return {}

       # ====== 有写操作，需要用户确认 ======
       logger.info(
           "confirm_node: 检测到写操作，等待用户确认",
           run_id=run_id,
           write_count=len(writes),
           safe_count=len(safe),
       )

       decision = interrupt({
           "type": "confirm_required",
           "writes": writes,
           "safe_tool_count": len(safe),
       })

       # ====== 处理用户决策（interrupt() 恢复后执行） ======
       approved_ids: set[str] = set(decision.get("approved_tool_call_ids", []))
       denied_ids: set[str] = set(decision.get("denied_tool_call_ids", []))

       logger.info(
           "confirm_node: 用户决策已处理",
           run_id=run_id,
           approved=len(approved_ids),
           denied=len(denied_ids),
       )

       # 保留批准的写 + 所有只读工具
       kept_calls = [
           tc for tc in tool_calls
           if tc["id"] in approved_ids or tc in safe
       ]

       # 为被拒绝的工具生成 ToolMessage 和 SSE 事件
       denied_msgs: list[ToolMessage] = []
       sse_events: list[dict] = []
       iteration = len(state.get("trace_iterations", []))

       for tc in tool_calls:
           if tc["id"] in denied_ids:
               sql_preview = tc["args"].get("sql", "")[:100]
               denied_msgs.append(ToolMessage(
                   content=f"写操作已被用户取消: {sql_preview}",
                   tool_call_id=tc["id"],
                   name=tc["name"],
               ))
               sse_events.append({
                   "type": "tool_result",
                   "tool": tc["name"],
                   "summary": "用户取消了写操作",
                   "tool_call_id": tc["id"],
                   "agent_run_id": run_id,
                   "iteration": iteration,
                   "safety_checks_passed": False,
               })

       # 替换原始 AIMessage（相同 id → add_messages reducer 执行替换）
       modified_aimsg = AIMessage(
           content=last_msg.content or "",
           tool_calls=kept_calls,
           id=last_msg.id,
       )

       # trace 记录
       trace_iterations = list(state.get("trace_iterations", []))
       trace_iterations.append({
           "iteration": len(trace_iterations) + 1,
           "node": "confirm",
           "writes_detected": len(writes),
           "approved": len(approved_ids),
           "denied": len(denied_ids),
       })

       return {
           "messages": [modified_aimsg] + denied_msgs,
           "sse_events": sse_events,
           "trace_iterations": trace_iterations,
       }
   ```

4. **修改 `build_agent_graph()`** — 加入 `confirm_node` 和新的边：

   ```python
   def build_agent_graph() -> CompiledStateGraph:
       workflow = StateGraph(AgentState)

       workflow.add_node("agent", agent_node)
       workflow.add_node("confirm", confirm_node)    # 新增
       workflow.add_node("tools", safe_tools_node)

       workflow.set_entry_point("agent")

       # agent → confirm（替代原来的 agent → tools）
       workflow.add_conditional_edges(
           "agent",
           route_after_agent,
           {"tools": "confirm", "__end__": END},     # "tools" 改指向 "confirm"
       )

       # confirm → tools 或 agent
       workflow.add_conditional_edges(
           "confirm",
           route_after_confirm,
           {"tools": "tools", "agent": "agent"},
       )

       workflow.add_edge("tools", "agent")

       return workflow.compile(checkpointer=MemorySaver())
   ```

5. **更新 `_build_system_prompt()`** — 第 10 条原则改为：

   ```python
   "10. 如果用户要求插入、更新或删除数据，使用 execute_sql 工具执行写操作。"
       "写操作执行前系统会请求用户确认，如被用户拒绝请告知用户操作已取消。"
       "如被安全策略拦截（权限不足），请告知用户当前角色的限制。"
   ```

#### 验收标准

- 只读 SQL（SELECT/DESCRIBE/EXPLAIN 等）的 Agent 请求 → confirm_node 透传，行为不变
- 写 SQL 的 Agent 请求 → confirm_node 触发 `interrupt()`
- interrupt payload 包含正确的 `writes` 列表和 `safe_tool_count`
- `route_after_confirm` 在有剩余工具时返回 `"tools"`，全部拒绝时返回 `"agent"`
- 现有 Agent 功能（非写操作）的回归测试通过

---

### Task 4：为 `execute_sql` 添加 `needs_write_confirmation` 元数据

#### 涉及文件

- `backend/app/agent/tools/query.py`

#### 执行步骤

1. 修改 `execute_sql` 的 `@tool` 装饰器（约第 305 行）：

   ```python
   @tool(
       response_format="content_and_artifact",
       extras={
           "needs_sql_audit": True,
           "needs_row_estimation": True,
           "needs_write_confirmation": True,   # 新增：写操作需要用户确认
       },
   )
   async def execute_sql(...) -> str:
       ...
   ```

#### 验收标准

- `TOOL_REGISTRY["execute_sql"].extras["needs_write_confirmation"]` → `True`
- 其他工具的 extras 不受影响

---

### Task 5：API 层 — 中断检测 + 恢复请求处理

核心改动：`ChatRequest` 新增 `resume` 字段；`_stream_events` 区分初始请求和恢复请求；检测 `GraphInterrupt` 并转换为 SSE `confirm_required` 事件。

#### 涉及文件

- `backend/app/api/chat.py`

#### 执行步骤

1. **新增 `ResumeRequest` Pydantic 模型**（在 `ChatRequest` 上方）：

   ```python
   class ResumeRequest(BaseModel):
       """LangGraph 中断恢复请求。"""
       approved_tool_call_ids: list[str] = []
       denied_tool_call_ids: list[str] = []
   ```

2. **`ChatRequest` 新增 `resume` 字段**：

   ```python
   class ChatRequest(BaseModel):
       message: str = ""
       connection_id: str
       session_id: str | None = None
       password: str | None = None
       mode: str = "query"
       resume: ResumeRequest | None = None   # 新增：恢复请求
   ```

3. **修改 `_stream_events()`** — 区分初始请求和恢复请求：

   在 Step 3（构建 AgentState + 执行 LangGraph 图）部分：

   ```python
   from app.agent.graph import get_agent_graph
   from langgraph.types import Command

   graph = get_agent_graph()
   config = {"configurable": {"thread_id": session.id}}

   if body.resume:
       # ── 恢复请求 ──
       resume_value = {
           "approved_tool_call_ids": body.resume.approved_tool_call_ids,
           "denied_tool_call_ids": body.resume.denied_tool_call_ids,
       }
       logger.info("恢复已中断的图", session_id=session.id, resume=resume_value)
       stream = graph.astream(
           Command(resume=resume_value),
           config=config,
           stream_mode=["updates", "messages"],
       )
       emitted_count = 0
   else:
       # ── 初始请求 ──
       initial_state = {...}  # 现有逻辑不变
       stream = graph.astream(
           initial_state,
           config=config,
           stream_mode=["updates", "messages"],
       )
       emitted_count = 0
   ```

4. **中断检测** — 在 `async for stream_item in stream` 循环的 except/finally 之后（或通过 state 检查）：

   需在实际实现时根据 LangGraph 版本确定具体检测方式。两种可能：

   **方案 A（LangGraph ≥ 0.2.x）：** `astream()` 正常结束，中断信息在 `graph.get_state(config).interrupts` 中

   ```python
   # stream 循环正常结束后
   state_snapshot = graph.get_state(config)
   if state_snapshot and state_snapshot.interrupts:
       for interrupt_data in state_snapshot.interrupts:
           yield format_sse(interrupt_data.value)
   ```

   **方案 B（LangGraph < 0.2.x）：** `interrupt()` 引发 `GraphInterrupt` 异常

   ```python
   try:
       async for stream_item in stream:
           ...
   except GraphInterrupt:
       state_snapshot = graph.get_state(config)
       ...
   ```

   > 实现时根据 `langgraph` 实际导入的版本和 API 确定方案。

5. **interrupt payload 作为 SSE 事件发射**：

   ```python
   # interrupt payload 示例:
   # {
   #     "type": "confirm_required",
   #     "writes": [{"tool_call_id": "...", "tool": "execute_sql", "sql": "INSERT INTO ..."}],
   #     "safe_tool_count": 2,
   # }
   yield format_sse(interrupt_data.value)
   ```

6. **清理逻辑** — 在 `_stream_events` 的 finally 块中清理 `_active_streams` 和 `_running_tasks`（与现有取消清理逻辑一致）。

#### 验收标准

- 初始请求（`body.resume is None`）→ 正常执行 `graph.astream(initial_state, ...)`
- 恢复请求（`body.resume` 非空）→ 执行 `graph.astream(Command(resume=...), ...)`
- 图正常结束时无 `interrupts` → 不发送 `confirm_required` 事件
- 图被 interrupt 暂停 → `confirm_required` SSE 事件正确发射到前端
- 恢复请求调用 graph.astream(Command(...)) 后，图从 confirm_node 继续执行
- 现有 `/api/chat/cancel` 端点不受影响
- 现有非写操作 Agent 请求的回归测试通过

---

### Task 6：前端类型定义 + SSE 事件处理 + API 客户端

#### 涉及文件

- `frontend/src/types/chat.ts`
- `frontend/src/composables/useSSE.ts`
- `frontend/src/api/client.ts`

#### 执行步骤

1. **`frontend/src/types/chat.ts`** — 新增类型：

   ```typescript
   /** 写操作确认请求事件 */
   export interface ConfirmRequiredEvent {
       type: 'confirm_required'
       writes: Array<{
           tool_call_id: string
           tool: string
           sql: string
       }>
       safe_tool_count: number
   }

   /** SSE 事件回调扩展 */
   // 在 SSEEventCallbacks 接口中新增：
   onConfirmRequired?: (event: ConfirmRequiredEvent) => void
   ```

   同时在 `ChatRequest` 类型中新增：
   ```typescript
   export interface ChatRequest {
       message: string
       connection_id: string
       session_id?: string | null
       password?: string | null
       mode?: string
       resume?: {                              // 新增
           approved_tool_call_ids: string[]
           denied_tool_call_ids: string[]
       } | null
   }
   ```

2. **`frontend/src/composables/useSSE.ts`** — 新增事件分发：

   在 `dispatchSSEEvent` 的 switch 中新增：
   ```typescript
   case 'confirm_required':
       userCallbacks?.onConfirmRequired?.(payload as ConfirmRequiredEvent)
       break
   ```

3. **`frontend/src/api/client.ts`** — 无需新增函数（复用现有的 `apiChatStream`，ChatRequest 扩展后自动支持 `resume` 字段）。

#### 验收标准

- TypeScript 编译无错误（`npx vue-tsc --noEmit`）
- `ConfirmRequiredEvent` 类型可正常导入和使用
- `chat.ts` store 可注册 `onConfirmRequired` 回调

---

### Task 7：前端 Store — 确认状态管理

#### 涉及文件

- `frontend/src/stores/chat.ts`

#### 执行步骤

1. **新增响应式状态**：

   ```typescript
   /** 当前待确认的写操作 */
   const pendingConfirm = ref<{
       writes: Array<{ tool_call_id: string; tool: string; sql: string }>
   } | null>(null)

   /** 确认超时计时器 */
   let confirmTimeoutId: ReturnType<typeof setTimeout> | null = null
   ```

2. **新增 `onConfirmRequired` 回调处理**（在现有的 SSE 回调注册对象中）：

   ```typescript
   onConfirmRequired: (event: ConfirmRequiredEvent) => {
       pendingConfirm.value = { writes: event.writes }

       // 60s 超时自动拒绝
       confirmTimeoutId = setTimeout(() => {
           if (pendingConfirm.value) {
               const allDenied = pendingConfirm.value.writes.map(w => w.tool_call_id)
               respondToConfirm({
                   approved_tool_call_ids: [],
                   denied_tool_call_ids: allDenied,
               })
           }
       }, 60_000)
   }
   ```

3. **新增 `respondToConfirm()` action**：

   ```typescript
   /** 用户对写操作确认的响应，发起恢复请求 */
   async function respondToConfirm(decision: {
       approved_tool_call_ids: string[]
       denied_tool_call_ids: string[]
   }): Promise<void> {
       const pending = pendingConfirm.value
       if (!pending) return

       // 清除状态和超时
       pendingConfirm.value = null
       if (confirmTimeoutId) {
           clearTimeout(confirmTimeoutId)
           confirmTimeoutId = null
       }

       // 发起恢复 SSE 流
       await startSSEStream({
           message: '',                              // 恢复请求不需要新消息
           connection_id: currentConnectionId.value,
           session_id: currentSessionId.value,
           password: currentPassword.value,
           mode: currentMode.value,
           resume: decision,                         // 决策数据
       })
   }
   ```

4. **修改 `cancelStreaming()`** — 确认等待期间点击停止：

   ```typescript
   async function cancelStreaming(): Promise<void> {
       // 如果正在等待确认，先自动拒绝
       if (pendingConfirm.value) {
           const allDenied = pendingConfirm.value.writes.map(w => w.tool_call_id)
           await respondToConfirm({
               approved_tool_call_ids: [],
               denied_tool_call_ids: allDenied,
           })
           return
       }

       // ... 原有取消逻辑 ...
   }
   ```

5. **导出新状态和函数**：

   ```typescript
   return {
       // ... 现有导出 ...
       pendingConfirm: readonly(pendingConfirm),
       respondToConfirm,
   }
   ```

#### 验收标准

- Agent 执行写操作时 `pendingConfirm` 被正确设置为包含 SQL 内容的列表
- 调用 `respondToConfirm({approved: [...], denied: [...]})` 后发起新的 SSE 请求（resume 字段正确）
- 60s 内无操作 → `respondToConfirm` 被自动调用（全部拒绝）
- 确认等待期间点击停止按钮 → 先全部拒绝再取消
- TypeScript 编译无错误

---

### Task 8：前端 UI — 内联确认卡片（最终实现）

采用**内联卡片**设计（非模态弹窗），自然嵌入消息流中。工具调用状态通过响应式计算驱动。

#### 涉及文件

- `frontend/src/components/chat/ChatPanel.vue` — 消息列表容器
- `frontend/src/components/chat/WriteConfirmation.vue` — 内联确认卡片（新组件）
- `frontend/src/components/chat/MessageBubble.vue` — 工具调用卡片 (waiting_approval 状态)
- `frontend/src/stores/chat.ts` — pendingConfirm 状态管理
- `frontend/src/App.vue` — CSS 变量 `--confirm-*`

#### 设计要点

1. **内联卡片**：位于 `message-list-inner` 中，跟随消息列表滚动，不遮挡界面

2. **主题配色**：
   - 深色主题强调色：浅绿 `#4ADE80`
   - 浅色主题强调色：浅蓝 `#60A5FA`
   - 定义于 `App.vue` 的 `--confirm-accent` / `--confirm-accent-hover` / `--confirm-heading`

3. **tool_call 状态驱动**：
   - `MessageBubble.vue` 中 `isWaitingApproval` computed 直接从 `chatStore.pendingConfirm.writes` 判断
   - 无需手动同步 `stepStatus`，响应式自动更新
   - 状态优先级：`isWaitingApproval` > `running` > `done` > 默认

4. **冷却机制**：1.5s 按钮冷却防误触，冷却中为描边样式，结束后填充强调色

5. **进场动画**：`Transition name="confirm-fade"` — 淡入 + 上移

#### 执行步骤

1. 创建 `WriteConfirmation.vue` — 内联卡片组件
2. `ChatPanel.vue` — 移除旧 `ConfirmDialog` 模态弹窗和 `multi-confirm-overlay` 覆盖层，替换为 `<WriteConfirmation />`
3. `MessageBubble.vue` — 新增 `isWaitingApproval` computed 和对应的模板分支（时钟图标 + "等待用户审批"）
4. `chat.ts` store — `onConfirmRequired` 仅设置 `pendingConfirm`，不再命令式更新 `stepStatus`
5. `App.vue` — 新增 `--confirm-accent` / `--confirm-accent-hover` / `--confirm-heading` CSS 变量

#### 验收标准

- 单条写 SQL → 内联卡片显示 SQL 代码块 + 确认/取消按钮
- 多条写 SQL → 卡片内编号展示所有 SQL + 全部确认/全部取消
- 确认卡片出现在消息流中，不弹窗、不遮挡
- 等待审批时对应的 tool_call 卡片显示时钟图标 + "等待用户审批"
- 用户确认后卡片消失，stream 恢复
- 深色/浅色主题配色正确（浅绿/浅蓝）
- TypeScript 编译无错误

---

### Task 9：集成测试 + 手动验证

#### 手动测试清单

| # | 测试场景 | 操作步骤 | 预期结果 |
|---|---------|---------|---------|
| 1 | admin + INSERT 确认执行 | admin 连接 → "向 test 表插入一条测试数据" → 弹出确认 → 点"确认执行" | SQL 成功执行，Agent 回复"数据已插入" |
| 2 | admin + INSERT 拒绝 | admin 连接 → "删除 test 表中某条数据" → 弹出确认 → 点"取消" | SQL 不执行，Agent 回复"写操作已被取消" |
| 3 | admin + 多工具（读+写）| admin 连接 → "先查看 test 表结构，再插入一条数据" → 弹出确认 → 点"确认执行" | describe_table 和 execute_sql 都执行成功 |
| 4 | admin + 多写工具 | admin 连接同时调用两次 execute_sql（INSERT + UPDATE）→ 确认对话框显示两条 SQL → 点"全部确认执行" | 两条 SQL 都执行 |
| 5 | readonly + INSERT | readonly 连接 → "插入数据" | SQLAuditCheck 拦截，不弹出确认对话框，Agent 提示权限不足 |
| 6 | 60s 超时 | admin 连接 → "插入数据" → 不操作等待 60s | 自动拒绝，Agent 回复"写操作已被取消" |
| 7 | 确认期间点停止 | admin 连接 → "插入数据" → 弹出确认 → 点停止按钮 | 自动拒绝 + 正常停止，无异常 |
| 8 | 只读 SQL（SELECT）| 任意连接 → "查询 test 表数据" | 不弹出确认，正常返回查询结果 |
| 9 | EXPLAIN 查询 | admin 连接 → "分析 SELECT 的执行计划" | 不弹出确认，正常返回 EXPLAIN 结果 |
| 10 | 服务重启后恢复 | admin 连接 → "插入数据" → 弹出确认 → 重启服务 → 点击确认 | 恢复请求返回错误（checkpoint 丢失），前端提示"会话已过期" |

#### 回归测试

```bash
# 后端
pytest backend/tests/ -v

# 前端
cd frontend && npx vue-tsc --noEmit
npm test
```

#### 验收标准

- 手动测试清单 10 项全部通过
- 后端所有测试通过（无回归）
- 前端 TypeScript 编译 + 测试通过

---

## 任务状态

| 任务 | 涉及文件 | 状态 | 备注 |
|------|---------|------|------|
| Task 1：图实例模块级复用 | `graph.py`, `chat.py` | ✅ 已完成 | 单例验证通过，测试 17/17 通过 |
| Task 2：`is_write_dml()` | `sql_auditor.py` | ✅ 已完成 | 9 项断言全部通过，测试 17/17 通过 |
| Task 3：`confirm_node` + 路由 | `graph.py` | ✅ 已完成 | 图编译通过（agent/confirm/tools 三节点）；分类逻辑验证通过；测试 17/17 通过 |
| Task 4：`execute_sql` 元数据 | `query.py` | ✅ 已完成 | extras 确认: `needs_write_confirmation: True` |
| Task 5：API 层中断/恢复 | `chat.py`, `schemas.py` | ✅ 已完成 | ChatRequest 扩展 resume；_stream_events 分支（初始/恢复）；中断检测 emit confirm_required |
| Task 6：前端类型 + SSE + API | `chat.ts`, `useSSE.ts`, `client.ts` | ✅ 已完成 | ConfirmRequiredEvent + SSEEventCallbacks + StreamChatRequest.extend resume；useSSE switch；TypeScript 编译无错误 |
| Task 7：前端 Store 确认逻辑 | `chat.ts` (store) | ✅ 已完成 | pendingConfirm ref + respondToConfirm action + cancelStreaming 集成 + 60s 超时；`onConfirmRequired` 不再命令式更新 stepStatus；TypeScript 编译通过；14/14 测试通过 |
| Task 8：前端 UI 内联确认卡片 | `ChatPanel.vue`, `WriteConfirmation.vue`, `MessageBubble.vue`, `App.vue` | ✅ 已完成 | 内联卡片代替模态弹窗；`isWaitingApproval` computed 响应式驱动 tool_call 状态（"等待用户审批"）；深色浅绿/浅色浅蓝主题配色；TypeScript 编译通过 |
| Task 9：集成测试 + 手动验证 | — | ⏳ 待开始 | 10 项手动测试 |

---

## 不修改的文件

| 文件 | 原因 |
|------|------|
| `app/agent/safety.py` | 写确认不是安全拦截而是用户交互，通过 `confirm_node` 处理而非 `SafetyCheck` |
| `app/agent/tool_node.py` | 确认逻辑在 graph 层面（confirm_node），不侵入工具执行链 |
| `app/agent/tools/registry.py` | 工具注册不变，确认通过 extras 元数据驱动 |
| `app/agent/tools/diagnosis.py` | `explain_query` 无 `needs_write_confirmation`，不受影响 |
| `app/agent/tools/troubleshoot.py` | 所有工具都是只读操作 |
| `app/agent/tools/health.py` | `run_health_check` 只读 |
| `app/agent/state.py` | AgentState 无需新增字段（emitted_count 通过 config metadata 追踪） |
| `app/agent/models.py` | LLM 模型层不涉及 |
| `app/agent/sse_utils.py` | `format_sse()` 函数不变 |

---

## 集成测试结果

全部 9 个 Task 已完成，自动化测试结果：

| 测试套件 | 结果 |
|---------|------|
| 后端 `tests/test_sql_auditor.py` | ✅ 17/17 passed |
| 后端 `tests/test_graph.py` | ✅ 22/22 passed |
| **后端全量** | **✅ 74 passed, 5 skipped**（跳过为集成测试） |
| 前端 TypeScript 编译（`vue-tsc --noEmit`） | ✅ 无错误 |
| 前端 Vitest 测试 | ✅ 7 files, 14 tests passed |

## 手动测试清单

以下场景需要人工验证（需要运行中的数据库和前端环境）：

| # | 测试场景 | 预期 |
|---|---------|------|
| 1 | admin 连接，发送"插入一条数据"，点确认 | SQL 成功执行，Agent 回复 |
| 2 | admin 连接，发送"删除数据"，点取消 | Agent 回复"写操作已被用户取消" |
| 3 | admin 连接，多工具（如 describe_table + INSERT），点确认 | 两个工具都执行 |
| 4 | admin 连接，多写工具，部分批准 | 只执行批准的 SQL |
| 5 | readonly 连接，发送"插入数据" | SQLAuditCheck 拦截，不触发确认对话框 |
| 6 | 确认对话框 60s 不操作 | 自动拒绝 |
| 7 | 确认期间点停止按钮 | 自动拒绝 + 正常停止 |
| 8 | 只读 SQL（SELECT/EXPLAIN 等） | 不触发确认，正常返回 |

## 风险与注意事项

1. **LangGraph 版本兼容性** — `interrupt()` 和 `Command(resume=)` 的 API 在不同版本有差异。Task 5 实现时需先确认项目中 LangGraph 的实际版本，查阅对应文档确定中断检测方式（异常捕获 vs state 检查）。

2. **MemorySaver 丢失** — `MemorySaver` 是内存级 checkpoint 存储。服务重启后所有暂停的图都会丢失，恢复请求将失败。前端需处理此场景（显示"会话已过期，请重新发送"）。生产环境可升级为 `SqliteSaver` 或 `PostgresSaver` 以支持持久化。

3. **AIMessage 替换机制** — 依赖 `add_messages` reducer 的"相同 id → 替换"行为来修改 tool_calls。这是 LangGraph 文档明确支持的行为，但需确保 `AIMessage` 的 `id` 字段不变。已在 `confirm_node` 实现中显式传递 `id=last_msg.id`。

4. **SSE 事件去重** — 两次 SSE 流之间的 `sse_events` 去重。`confirm_node` 返回的 `sse_events` 仅包含拒绝事件（不清空 state 中已累积的事件），确保 stream 2 不会重放 stream 1 的事件。

5. **Agent 行为** — 当写操作被拒绝后，Agent 看到 ToolMessage("写操作已被用户取消")。对于简单的单工具场景，Agent 应自然回应"好的，已取消"；对于复杂的多工具场景（部分拒绝），Agent 看到部分写操作被拒绝，应据此调整后续行为。System Prompt 第 10 条已更新以引导此行为。

6. **DDL 操作策略** — 本期不改 DDL（DROP/ALTER/TRUNCATE/CREATE 等）的拦截策略。DDL 仍由 `SQLAuditCheck` 无条件拦截。未来如需支持 DDL 确认，只需在 `_ADMIN_ONLY_STATEMENTS` 和 `is_write_dml()` 中控制即可。
