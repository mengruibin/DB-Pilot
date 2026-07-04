# 意图分类移除 — 后端实施计划

> 创建日期：2026-07-04
>
> 动机：当前意图分类（5 分类 + LLM 回退）在流程中不起实际作用——所有非 GENERAL 意图都进入同一个 `agent_node`，Agent 绑定全部 9 个工具自主决策。分类步骤只增加了 API 成本和延迟，无业务价值。

---

## 目标

完全移除 `classify_node` 和 `IntentRouter`，简化 LangGraph 图为：

```
之前: classify → route → general/agent → tools ↔ agent → format → END
之后:           agent ↔ tools → format → END
```

Agent 直接处理所有用户消息（包括问候/闲聊），LLM 自己判断是否调用工具。

---

## 任务清单

| # | 任务 | 状态 | 文件 | 预估改动 |
|---|------|------|------|----------|
| 1 | 重写 Agent 图：移除 classify 节点和 general 快捷路径 | ✅ 已完成 | `app/agent/graph.py` | ~80 行删减 + 重构 |
| 2 | 移除 IntentRouter（删除文件） | ✅ 已完成 | `app/agent/router.py` | 删除整个文件 |
| 3 | 简化 AgentState：移除 intent / classification_method，废弃 Intent 枚举 | ✅ 已完成 | `app/agent/state.py` | ~20 行修改 |
| 4 | 移除 build_classifier_model() | ✅ 已完成 | `app/agent/models.py` | ~15 行删除 |
| 5 | 废弃 LLM_CLASSIFIER_MODEL 配置项 | ✅ 已完成 | `app/config.py` | 1 行修改 |
| 6 | 适配 chat.py：message_type 从 Agent 实际工具调用推断 | ✅ 已完成 | `app/api/chat.py` | ~20 行修改 |
| 7 | 清理 sse_utils 中 classify 相关逻辑 | ✅ 已完成（无需改动） | `app/agent/sse_utils.py` | 按需 |
| 8 | 编写单元测试：新 graph 流程 / message_type 推断 / 向后兼容 | ✅ 已完成 | `tests/test_graph.py`（新建） | ~80 行新增 |
| 9 | 运行全量测试套件，确保无回归 | ✅ 已完成 | 全部 | — |
|   | **总计** | **9/9 已完成** | — | — |

---

## 各任务详细说明

### 任务 1：重写 Agent 图 (`graph.py`)

**删除：**
- `classify_node()` 函数（第 112-158 行）
- `route_after_classify()` 函数（第 55-72 行）
- `general_node()` 函数（第 352-392 行）
- `from app.agent.router import IntentRouter`（第 37 行）
- `from app.agent.models import ..., build_classifier_model`（第 36 行）

**修改 `build_agent_graph()`：**
```python
def build_agent_graph() -> CompiledStateGraph:
    # 新图结构：agent → tools ↔ agent → format → END
    workflow = StateGraph(AgentState)

    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", safe_tools_node)
    workflow.add_node("format_response", format_response_node)

    workflow.set_entry_point("agent")  # 直接进入 Agent（原为 classify）

    workflow.add_conditional_edges(
        "agent",
        route_after_agent,
        {"tools": "tools", "format_response": "format_response"},
    )
    workflow.add_edge("tools", "agent")
    workflow.add_edge("format_response", END)

    return workflow.compile()
```

**不变：**
- `agent_node()` — 无需修改
- `safe_tools_node` — 无需修改
- `format_response_node()` — 无需修改
- `route_after_agent()` — 无需修改
- `_build_system_prompt()` / `_build_llm_messages()` — 无需修改

### 任务 2：删除 `router.py`

删除 `backend/app/agent/router.py` 整个文件。该文件仅被 `graph.py` 中的 `classify_node` 引用，classify_node 已删除。

### 任务 3：简化 AgentState (`state.py`)

**移除字段：**
- `intent: Intent | None`（第 89 行）
- `classification_method: str | None`（第 90 行）

**废弃 Intent 枚举：**
```python
class Intent(StrEnum):
    """（已废弃 2026-07）意图分类已从 Agent 图中移除。
    保留枚举定义仅用于数据库历史记录的 message_type 兼容。
    """
    ...
```

**注意：** `IntEnum` 需保留以兼容 `chat.py` 中导入和数据库已有记录的 message_type 值。

### 任务 4：移除 build_classifier_model (`models.py`)

删除 `build_classifier_model()` 函数（第 74-87 行）。
删除模块 docstring 中与之相关的示例代码行。

### 任务 5：废弃 LLM_CLASSIFIER_MODEL (`config.py`)

```python
LLM_CLASSIFIER_MODEL: str = Field(
    default="claude-haiku-4-5-20251001",
    description="（已废弃 2026-07）意图分类已移除，不再调用 LLM。保留此项仅为向后兼容。",
)
```

### 任务 6：适配 chat.py — message_type 推断

**现状（第 529 行）：**
```python
message_type=state.get("intent", Intent.GENERAL).value.lower(),
```

**改为从 Agent 实际工具调用推断：**
```python
def _infer_message_type(state: AgentState) -> str:
    """根据 Agent 实际调用的工具推断消息类型（事后标注，比预分类更准确）。"""
    trace = state.get("trace_iterations", [])
    tools_called: set[str] = set()
    for entry in trace:
        for tc in entry.get("tool_calls", []):
            tools_called.add(tc["name"])

    if not tools_called:
        return "general"  # 纯文本回复，未调用任何工具
    if "run_health_check" in tools_called:
        return "health_check"
    if tools_called & {"check_locks", "check_connections", "check_replication"}:
        return "troubleshoot"
    if tools_called & {"explain_query", "get_slow_queries"}:
        return "diagnosis"
    if tools_called & {"run_query", "list_tables", "describe_table"}:
        return "query"
    return "general"
```

使用处改为：
```python
message_type = _infer_message_type(state)
await _save_message(
    db, session.id, "assistant", assistant_content,
    message_type=message_type,
    agent_trace=agent_trace,
)
```

### 任务 7：清理 sse_utils

检查 `app/agent/sse_utils.py` 中是否有 classify 相关的格式化逻辑，如有则清理。

### 任务 8：编写单元测试

新建 `backend/tests/test_graph.py`：

| 测试项 | 说明 |
|--------|------|
| `test_graph_entry_point` | 验证图入口为 agent_node，非 classify_node |
| `test_graph_structure` | 验证图中不存在 classify 和 general 节点 |
| `test_agent_handles_greeting` | "你好" → Agent 正常回复（文本，无工具调用）|
| `test_agent_handles_query` | "查一下 users 表" → Agent 调用工具 → 返回结果 |
| `test_infer_message_type_general` | 无工具调用 → "general" |
| `test_infer_message_type_query` | 调用 run_query → "query" |
| `test_infer_message_type_diagnosis` | 调用 explain_query → "diagnosis" |
| `test_infer_message_type_troubleshoot` | 调用 check_locks → "troubleshoot" |
| `test_infer_message_type_health` | 调用 run_health_check → "health_check" |
| `test_backward_compat` | 确认旧 Intent 枚举值仍可序列化 |

### 任务 9：运行全量测试

```bash
cd backend
ruff check backend/app/
pytest backend/tests/ -v
```

---

## 验收标准

- [x] ~~`classify_node` 从 LangGraph 图中完全移除~~ → 入口直接是 `agent_node`
- [x] ~~`general_node` 移除，问候/闲聊由 Agent 正常处理（LLM 文本回复，不调工具）~~
- [x] ~~`IntentRouter` 类及 `router.py` 文件删除~~
- [x] ~~`build_classifier_model()` 函数移除~~
- [x] ~~`LLM_CLASSIFIER_MODEL` 配置项标记废弃~~
- [x] ~~`state.py` 中 `intent` / `classification_method` 字段移除~~
- [x] ~~`chat.py` 中 `message_type` 从 Agent 实际工具调用推断~~
- [x] ~~后端日志中不再出现 `LLM 意图分类请求`~~
- [x] ~~"你好" 消息仅触发 1 次 LLM 调用（Agent），而非 2 次（分类 + Agent）~~
- [x] ~~"查一下 users 表" 正常走 Agent ReAct 流程，正确调用工具~~
- [x] ~~全量测试通过，无回归~~

---

## 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| 问候消息多消耗 1 次 Agent LLM 调用 | 确定 | 低（替换原来 1 次分类 LLM，总次数不变） | Agent 对问候的回复极短（~50 tokens），成本可忽略 |
| message_type 推断不覆盖所有工具组合 | 低 | 低（message_type 仅用于统计，不影响功能） | 默认 fallback 为 "general" |
| 已有数据库记录中 message_type 值前后不一致 | 低 | 极低 | 历史数据不受影响，新增数据使用推断值 |
| 图结构变更导致 Agent 异常 | 低 | 中 | 全量测试覆盖 |
