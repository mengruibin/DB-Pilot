# Agent 并行工具执行 — 实施计划

## 方案流程简述

### 现状

当前 Agent 的 `safe_tools_node` 在收到 LLM 的多个 `tool_calls` 时是**串行执行**的——`for tc in tool_calls:` 循环逐个 `await tool_fn.ainvoke(tool_args)`，总耗时 = 各工具耗时之和。

### 目标流程

```
一轮 ReAct 迭代中:

  agent_node
    │  LLM 发出 N 个 tool_calls（如 check_connections + check_locks + check_replication）
    │
    ▼
  safe_tools_node
    │                          ┌─ _run_one_tool(tc1) ─── check_connections
    │  asyncio.gather(         ├─ _run_one_tool(tc2) ─── check_locks
    │    return_exceptions=True ├─ _run_one_tool(tc3) ─── check_replication
    │  )                       └─ ... (并发执行，Semaphore 限制并发数为 5)
    │
    ▼  结果按 tool_calls 原始顺序组装（gather 保持顺序）
    │
    ▼
  agent_node（下一轮）

总耗时 ≈ 最慢的工具耗时（而非串行之和）
```

### 核心设计原则

| 原则 | 说明 |
|------|------|
| **保持 SSE 事件顺序** | `asyncio.gather()` 返回结果保持输入顺序，和串行完全相同 |
| **错误隔离** | `return_exceptions=True`，单个工具失败不影响其他工具 |
| **并发上限** | `asyncio.Semaphore(5)` 防止 DB 连接池耗尽 |
| **向后兼容** | 前端匹配回退到 tool 名+状态，旧 SSE 事件无 `tool_call_id` 仍可工作 |
| **启动透明** | LLM 仅发射一个 tool_call 时无并行，行为不变 |

---

## 最终目标

| 目标 | 描述 |
|------|------|
| 响应提速 | 独立工具并行执行，整体响应时间从"串行之和"降为"最慢工具耗时" |
| 依赖安全 | ReAct 循环天然保障依赖工具跨迭代串行，同一轮内的工具天然独立 |
| 前端无感 | SSE 事件顺序不变，前端无需做结构性改动 |
| 结果一致 | ToolMessage 在 `messages[]` 中的顺序与串行完全一致，LLM 推理不受影响 |

### 典型提速场景

| 场景 | 串行 | 并行 | 加速比 |
|------|------|------|--------|
| check_connections(0.5s) + check_locks(0.8s) + check_replication(1.2s) | 2.5s | 1.2s | ~2x |
| list_tables(0.3s) + get_slow_queries(1.0s) | 1.3s | 1.0s | ~1.3x |
| 同时分析多个慢查询（各 0.5s × 3） | 1.5s | 0.5s | ~3x |

---

## 任务具体执行

### Task 1: 后端核心并行化

**文件**: `backend/app/agent/tool_node.py`

**改动一：提取 `_run_one_tool` 内部协程**

将当前 `for tc in tool_calls:` 循环体（约第 76-249 行）提取为异步内部函数：

```python
async def _run_one_tool(tc: dict, conn_config: dict, run_id: str,
                        iteration: int, semaphore: asyncio.Semaphore) -> dict:
    """执行单个工具调用的完整生命周期。
    
    Returns:
        {"tool_message": ToolMessage, "sse_events": list[dict]}
    """
    async with semaphore:
        tool_name = tc["name"]
        tool_args = dict(tc["args"])
        local_sse: list[dict] = []
        tool_start = time.monotonic()

        # ── 1. 连接配置注入 ──（同现有逻辑）
        for key in ("connection_id", "db_type", "host", ...):
            if key in conn_config and key not in tool_args:
                tool_args[key] = conn_config[key]
        if "user_role" not in tool_args:
            tool_args["user_role"] = conn_config.get("user_role", "standard")

        # ── 2. 安全护栏 ──
        safety_result = await run_safety_checks(tool_name, tool_args, conn_config)
        if safety_result.blocked:
            # ... 构造拦截 ToolMessage + SSE，直接 return ...

        safety_warnings = list(safety_result.warnings)

        # ── 3. 工具查找 ──
        tool_fn = TOOL_REGISTRY.get(tool_name)
        if tool_fn is None:
            # ... 构造未注册 ToolMessage + SSE，直接 return ...

        # ── 4. 执行工具 ──
        try:
            result = await tool_fn.ainvoke(tool_args)
        except Exception as exc:
            # ... 构造异常 ToolMessage + SSE，直接 return ...

        elapsed = int((time.monotonic() - tool_start) * 1000)
        result = _sanitize_sensitive_data(result)

        # ── 5. 构造 ToolMessage + SSE 事件 ──
        tool_content = _json.dumps(result, ensure_ascii=False, default=str)
        if safety_warnings:
            tool_content = "[性能提示] " + " | ".join(safety_warnings) + "\n\n" + tool_content
        tool_message = ToolMessage(content=tool_content, tool_call_id=tc["id"], name=tool_name)

        # SSE 事件构造（含 tool_call_id）
        if isinstance(result, dict):
            sql_text = result.get("sql") or result.get("sql_executed", "")
            if sql_text:
                local_sse.append({"type": "sql", "content": sql_text, ...})
            local_sse.append({"type": "tool_result", "tool": tool_name,
                              "tool_call_id": tc["id"], ...})
        else:
            local_sse.append({"type": "tool_result", "tool": tool_name,
                              "tool_call_id": tc["id"], ...})

        return {"tool_message": tool_message, "sse_events": local_sse}
```

**改动二：`safe_tools_node` 主体改为并行调度**

```python
# 并发上限
_MAX_CONCURRENT_TOOLS = 5

async def safe_tools_node(state: AgentState) -> dict[str, Any]:
    # ... preamble 同现有逻辑 ...
    
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_TOOLS)
    
    results = await asyncio.gather(
        *[_run_one_tool(tc, conn_config, run_id, iteration, semaphore)
          for tc in tool_calls],
        return_exceptions=True,
    )
    
    tool_messages: list[ToolMessage] = []
    sse_events_total = list(sse_events)
    
    for i, r in enumerate(results):
        if isinstance(r, BaseException):
            tc = tool_calls[i]
            tool_messages.append(ToolMessage(
                content=f"工具执行异常: {str(r)[:200]}",
                tool_call_id=tc["id"], name=tc["name"],
            ))
            sse_events_total.append({
                "type": "tool_result", "tool": tc["name"],
                "summary": f"执行异常: {str(r)[:100]}",
                "tool_call_id": tc["id"],
                "agent_run_id": run_id, "iteration": iteration,
                "safety_checks_passed": True,
            })
        else:
            tool_messages.append(r["tool_message"])
            sse_events_total.extend(r["sse_events"])
    
    return {"messages": tool_messages, "sse_events": sse_events_total}
```

> **注意**：`_sanitize_sensitive_data` 函数在文件末尾定义，保持不动。

---

### Task 2: 后端 SSE 协议增加 `tool_call_id`

**文件**: `backend/app/agent/graph.py`

在 `agent_node` 中 `tool_call` 事件的构造处（约第 273 行）：

```python
sse_events.append({
    "type": "tool_call",
    "tool": tc["name"],
    "args": tc["args"],
    "display": f"正在执行 {tc['name']}...",
    "tool_call_id": tc["id"],          # ← 新增
    "agent_run_id": run_id,
    "iteration": iteration,
})
```

**文件**: `backend/app/agent/tool_node.py`

在 Task 1 的 `_run_one_tool` 中，所有 `tool_result` SSE 事件构造处均增加 `"tool_call_id": tc["id"]`。

---

### Task 3: 前端类型扩展

**文件**: `frontend/src/types/chat.ts`

```typescript
export interface ToolCallEvent {
  type: 'tool_call'
  tool: string
  args: Record<string, unknown>
  display: string
  tool_call_id?: string          // ← 新增
  agent_run_id?: string
  iteration?: number
}

export interface ToolResultEvent {
  type: 'tool_result'
  tool: string
  summary: string
  duration_ms: number
  tool_call_id?: string          // ← 新增
  agent_run_id?: string
  iteration?: number
  safety_checks_passed?: boolean
}
```

---

### Task 4: 前端匹配逻辑重构

**文件**: `frontend/src/stores/chat.ts`

```typescript
// 新增：按 tool_call_id 查找
function findToolCallById(toolCallId: string): StoreMessage | undefined {
  return messages.value.find(
    (m) => m.type === 'tool_call' && m.toolCallId === toolCallId && m.stepStatus === 'running'
  )
}

// onToolCall: 保存 toolCallId
onToolCall: (event: ToolCallEvent) => {
  // ... 同现有逻辑 ...
  messages.value.push({
    // ... 同现有逻辑 ...
    toolCallId: event.tool_call_id,     // ← 新增
    stepStatus: 'running',
  })
}

// onToolResult: 按 tool_call_id 匹配（优先），回退到按 tool 名匹配
onToolResult: (event: ToolResultEvent) => {
  let toolCall: StoreMessage | undefined
  
  // 优先按 tool_call_id 精确匹配（并行安全）
  if (event.tool_call_id) {
    toolCall = findToolCallById(event.tool_call_id)
  }
  // 回退：按 tool 名 + running 状态匹配（向后兼容无 tool_call_id 的旧事件）
  if (!toolCall && event.tool) {
    toolCall = messages.value.find(
      (m) => m.type === 'tool_call' && m.tool === event.tool && m.stepStatus === 'running'
    )
  }
  
  if (toolCall) {
    toolCall.stepStatus = 'done'
    toolCall.durationMs = event.duration_ms
  }
  
  // ... 推送 tool_result 消息（同现有逻辑） ...
}
```

> 注：`StoreMessage` 接口需在约第 77 行增加 `toolCallId?: string` 字段。

---

### Task 5: LLM 提示词更新

**文件**: `backend/app/agent/graph.py` — `_build_system_prompt()`

在现有规则 6 后插入：

```
7. **并行执行提示**: 当需要调用多个相互独立的工具时
（如同时查询多张表的基本信息、同时检查锁和连接状态、
同时分析多个慢查询），请将所有工具调用一次性返回。
系统会并行执行它们，显著提升整体响应速度。
```

后续规则编号顺延：7 → 8, 8 → 9, 9 → 10。

---

### Task 6: 配置项（可选）

**文件**: `backend/app/config.py`

```python
# Agent 每轮最大并行工具数
AGENT_MAX_CONCURRENT_TOOLS: int = 5
```

`tool_node.py` 中引用：

```python
from app.config import settings
_MAX_CONCURRENT_TOOLS = getattr(settings, 'AGENT_MAX_CONCURRENT_TOOLS', 5)
```

---

### Task 7: 单元测试

**文件**: `backend/tests/test_tool_node.py`（新建）

```python
"""SafeToolNode 并行执行测试。"""

class TestSafeToolsNodeParallel:
    """验证并行执行语义。"""

    async def test_parallel_execution(self):
        """3 个 tool_calls 应并发执行而非串行。"""
        ...

    async def test_error_isolation(self):
        """单工具失败不阻塞其他工具。"""
        ...

    async def test_semaphore_caps_concurrency(self):
        """Semaphore 限制并发数。"""
        ...

    async def test_sse_event_contains_tool_call_id(self):
        """所有 tool_result SSE 事件包含 tool_call_id。"""
        ...

    async def test_tool_message_order_matches_tool_calls(self):
        """ToolMessage 列表顺序与 tool_calls 输入顺序一致。"""
        ...
```

---

## 验收标准

| # | 验收项 | 验证方式 | 预期结果 |
|---|--------|----------|----------|
| 1 | 并行执行 | 发"同时检查连接池、锁等待、复制状态" | 总耗时 ≈ 最慢单项（不再累加） |
| 2 | 工具调用日志 | 观察后端日志 | 同轮内多个工具的日志交错输出 |
| 3 | 前端 tool_call 卡片 | 观察前端 UI | 多个 tool_call 卡片同时显示 spinner |
| 4 | 前端 tool_result 匹配 | 观察前端 UI | 每个 tool_call 的 spinner 正确转为 checkmark |
| 5 | 事件顺序正确 | SSE 事件流日志 | tool_call 和 tool_result 顺序一致 |
| 6 | 依赖工具串行 | 发"列出表后描述 orders 表" | LLM 先调用 list_tables，下一轮调用 describe_table |
| 7 | 错误隔离 | 一个工具抛异常 | 其他工具正常返回结果 |
| 8 | 并发上限 | LLM 发射 10 个 tool_calls | 同时执行的工具不超过 5 个 |
| 9 | SQL 审计 | 并行执行 execute_sql | 每个 SQL 独立通过审计，危险 SQL 正常拦截 |
| 10 | 取消流程 | 点击取消按钮 | SSE 流终止，所有进行中的工具协程取消 |

---

## 任务状态

| Task | 描述 | 文件 | 状态 |
|------|------|------|------|
| 1 | 后端核心并行化（`_run_one_tool` + `gather` + Semaphore） | `tool_node.py` | ✅ 已完成 |
| 2 | SSE 协议增加 `tool_call_id` | `graph.py`, `tool_node.py` | ✅ 已完成 |
| 3 | 前端类型扩展 | `types/chat.ts` | ✅ 已完成 |
| 4 | 前端匹配逻辑重构 | `stores/chat.ts` | ✅ 已完成 |
| 5 | LLM 提示词更新 | `graph.py` | ✅ 已完成 |
| 6 | 配置项（可选） | `config.py` | ✅ 已完成 |
| 7 | 单元测试 | `tests/test_tool_node.py` | ✅ 已完成 |
| — | 调测验收 | — | ⏳ 待执行 |

状态标记：✅ 已完成 / 🔄 进行中 / ⏳ 待执行 / 🚫 阻塞 / ❌ 已取消

---

## 风险评估

### 🔴 中高风险

| 风险 | 原因 | 缓解措施 |
|------|------|----------|
| **DB 连接数飙升** | 多个工具同时 `connect()` | Semaphore(5) + MySQL 默认 100+ 连接上限 |
| **慢工具拖慢整轮** | `gather()` 等待全部完成 | 并行后时长 ≈ 最慢工具，比串行累加更优 |

### 🟡 低风险

| 风险 | 原因 | 缓解 |
|------|------|------|
| 日志交错 | 并行日志行交错 | `run_id`+`tool_name` 字段可过滤 |
| 内存峰值 | 多结果集同时驻留 | 工具返回数据量小，`execute_sql` 有行数限制 |
| LLM 不批量 | 提示词仅建议 | 正确性不变，仅可能无性能提升 |

### ✅ 无风险

SSE 事件顺序不变（`gather` 保持输入顺序）→ 前端无感知；安全检查、脱敏、事务均独立隔离。
