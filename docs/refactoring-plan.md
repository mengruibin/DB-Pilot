# 后端 Agent 架构 LangChain/LangGraph 标准化重构计划

> 版本: v1.0 · 日期: 2026-07-03 · 状态: 📋 待执行

---

## 一、背景

当前后端使用 LangGraph 的 `StateGraph` 做图编排骨架，但 LLM 调用、工具绑定、工具执行全部通过自研代码实现（`httpx` 直接调 REST API、手动 Schema 转换、手动 `TOOL_REGISTRY.get()` 派发）。这种"用其图，弃其链路"的做法导致：

1. 每次 LLM provider 新增能力（如 streaming tool calls、structured output）都需要手动跟进
2. 消息构建需分别处理 Anthropic/OpenAI 两种格式（200+ 行 `_build_agent_messages`）
3. 工具执行链路（安全护栏、结果脱敏）与 LangGraph 标准 `ToolNode` 隔离，无法复用生态
4. 代码量膨胀：`llm_client.py` 771 行 + `graph.py` 1053 行 = 1800+ 行自研代码

**目标**：全面采用 LangChain/LangGraph 标准封装（Chat 模型、`bind_tools()`、`ToolNode`、`astream_events()`），仅在必须自定义时才自定义，不做无意义的自研。

---

## 二、影响范围概览

### 后端文件变更

| 操作 | 文件 | 说明 |
|------|------|------|
| 🔴 删除 | `app/engine/llm_client.py` | 替换为 LangChain Chat 模型 |
| ✏️ 重写 | `app/agent/graph.py` | 全面采用标准 LangGraph 模式 |
| ✏️ 重构 | `app/agent/tools/registry.py` | 简化 Schema 转换 |
| ✏️ 重构 | `app/agent/state.py` | 引入 MessagesState 标准模式 |
| ✏️ 修改 | `app/agent/tools/query.py` | 移除 conn_config 参数 |
| ✏️ 修改 | `app/agent/tools/diagnosis.py` | 同上 |
| ✏️ 修改 | `app/agent/tools/health.py` | 同上 |
| ✏️ 修改 | `app/agent/tools/troubleshoot.py` | 同上 |
| ✏️ 修改 | `app/agent/safety.py` | 适配 ToolMessage 模式 |
| ✏️ 修改 | `app/api/chat.py` | 适配新图 + 状态字段变更 |
| ✏️ 修改 | `app/api/troubleshoot.py` | 同上 |
| ✏️ 修改 | `app/agent/router.py` | 改用 LangChain Chat 模型 |
| 📄 新增 | `app/agent/models.py` | Chat 模型工厂函数 |
| 📄 新增 | `app/agent/tool_node.py` | SafeToolNode（安全护栏 + 连接注入） |
| ✏️ 修改 | `app/config.py` | 清理 LLM_PROVIDER 相关 |
| ✏️ 修改 | `requirements.txt` | 添加 langchain 相关包 |

### 文档变更

| 文件 | 说明 |
|------|------|
| `backend/AGENTS.md` | 更新 LLM 集成、Agent 架构描述 |
| `backend/backend-tasks.md` | 更新相关任务状态 |
| `docs/api-contract.md` | 如有 SSE 变化则更新 |
| `frontend/FRONTEND_CHANGES.md` | 新增本次变更的前端适配说明 |

---

## 三、任务步骤

### 任务 1：添加 LangChain 依赖并创建 Chat 模型工厂

**状态**：✅ 已完成

**目标**：安装标准 LangChain 包，创建 Chat 模型工厂替代自研 `LLMClient`

**要做的事**：
1. 更新 `requirements.txt`，添加 `langchain>=0.3.0`、`langchain-anthropic>=0.3.0`、`langchain-openai>=0.3.0`
2. 新建 `app/agent/models.py`：
   - `build_chat_model()` — 根据 settings.LLM_PROVIDER 返回 ChatAnthropic 或 ChatOpenAI
   - `build_classifier_model()` — 轻量分类模型
   - 支持自定义 LLM_API_URL（兼容阿里百炼等）

**验收标准**：
- [x] `pip install` 无报错
- [x] `build_chat_model()` 在 anthropic/openai provider 下返回正确类型
- [x] 自定义 API URL 的兼容模式正常初始化

---

### 任务 2：重构 AgentState — 引入 MessagesState 标准模式

**状态**：✅ 已完成

**目标**：Agent 状态使用 LangGraph 标准消息列表格式（AIMessage/HumanMessage/ToolMessage），分离 LLM 消息和前端 SSE 事件

**要做的事**：
1. 修改 `app/agent/state.py`：
   - messages 字段使用 `add_messages` reducer
   - 新增 `sse_events: list[dict]` 字段（前端 SSE 事件）
   - 移除 `pending_tool_calls`、`pending_tool_results`

**验收标准**：
- [ ] AgentState 兼容 LangGraph 标准消息追加语义
- [ ] 图编译无类型错误
- [ ] sse_events 与 messages 职责分离

---

### 任务 3：重构工具定义 — 连接配置隐式注入

**状态**：✅ 已完成

**目标**：工具签名中移除连接配置参数暴露，由 SafeToolNode 在执行时自动注入

**实现方式**：使用 LangChain 标准 `InjectedToolArg` 标注连接参数，`bind_tools()` 自动从 Schema 中排除这些参数。工具签名保留连接参数（用于内部 `_build_config()`），但 LLM 不可见。

**要做的事**：
1. 修改 4 个工具文件（query.py/diagnosis.py/troubleshoot.py/health.py），为所有连接参数添加 `Annotated[str, InjectedToolArg]` 标注
2. 移除 `registry.py` 中的 `_INJECTED_CONN_PARAMS`、`_clean_schema_property()`、`get_tool_schemas()`、`_tool_to_anthropic_schema()` 等手动 Schema 过滤代码
3. `graph.py` 改用 `model.bind_tools(AGENT_TOOLS)` 直接绑定 BaseTool 对象
4. 连接配置通过 `tool_args` 由 SafeToolNode 在运行时注入（无需改动）

**验收标准**：
- [x] LLM 调用 bind_tools() 时不会看到连接参数（`list_tables` Schema 为空，`run_query` 只有 `sql`）
- [x] 工具执行时连接参数自动注入（SafeToolNode 注入后调用 `tool_fn.ainvoke()` 正常传递）
- [x] bind_tools() 自动处理 Anthropic/OpenAI Schema 转换（直接传入 BaseTool 对象）
- [x] 移除手动过滤代码后无 breakage（所有 9 个工具注册正常，图编译无错误）

---

### 任务 4：重写 agent_node — 使用 Chat 模型 + bind_tools

**状态**：✅ 已完成

**目标**：agent_node 使用 LangChain 标准 API（`model.bind_tools()` + `model.ainvoke()`）

**要做的事**：
1. 重写 `agent_node`：
   - 使用 `build_chat_model()` + `model.bind_tools(AGENT_TOOLS)`
   - 使用 SystemMessage 替代字符串 system prompt
   - 解析 `AIMessage.tool_calls` 替代自定义 `ToolCall`
2. 移除：`_build_agent_messages()`、`_parse_text_tool_calls()`、`_find_tool_use_id()`、`json_safe_str()`

**验收标准**：
- [ ] 使用 `model.bind_tools()` 绑定工具
- [ ] 移除 `_build_agent_messages()` 和 `_parse_text_tool_calls()`
- [ ] 消息格式由 LangChain 自动处理

---

### 任务 5：创建 SafeToolNode — 安全护栏 + 结果脱敏

**状态**：✅ 已完成

**目标**：在标准 ToolNode 基础上注入安全检查和脱敏

**要做的事**：
1. 新建 `app/agent/tool_node.py`：
   - 读取 `AIMessage.tool_calls`
   - 注入 conn_config
   - 执行安全护栏
   - 执行工具
   - 敏感数据脱敏
   - 返回 `ToolMessage` 列表
2. 替换 `graph.py` 中的 `tools_node`

**验收标准**：
- [ ] 安全护栏在工具执行前调用
- [ ] conn_config 自动注入
- [ ] 结果自动脱敏
- [ ] 返回标准 ToolMessage 对象

---

### 任务 6：重构图结构 — 标准化节点 + 条件路由

**状态**：✅ 已完成

**目标**：build_agent_graph() 使用标准 LangGraph 模式

**要做的事**：
1. 替换 agent_node（任务 4）、tools_node（任务 5）
2. 更新 route_after_agent：检查 AIMessage.tool_calls 而非 state["pending_tool_calls"]
3. 保留 classify_node、general_node、format_response_node

**验收标准**：
- [ ] 图编译无错误
- [ ] ReAct 循环正常工作
- [ ] 安全上限 _MAX_AGENT_ITERATIONS 仍然生效

---

### 任务 7：重构 SSE 事件流

**状态**：✅ 已完成

**目标**：SSE 流从 `sse_events` 字段读取事件

**要做的事**：
1. 修改 `chat.py` 和 `troubleshoot.py`：
   - 初始状态中添加 `HumanMessage` 到 messages
   - 从 `state["sse_events"]` 读取事件而非 `state["messages"]`
   - 移除 `pending_tool_calls`/`pending_tool_results` 的初始状态构建
2. 修改各个图节点，将 SSE 事件写入 `sse_events`

**验收标准**：
- [ ] 7 种 SSE 事件类型全部正常发射
- [ ] 前端完全向后兼容
- [ ] 取消机制仍工作

---

### 任务 8：重构 IntentRouter — 改用 LangChain Chat 模型

**状态**：✅ 已完成

**目标**：IntentRouter 不再依赖自研 LLMClient

**要做的事**：
1. 修改 `router.py`：接受 BaseChatModel 代替 LLMClient
2. 修改 `classify_node`：使用 build_classifier_model()
3. LangChain 模型直接调用 `.ainvoke()` 做分类

**验收标准**：
- [ ] 意图分类功能正常（keyword 优先 + LLM 回退）
- [ ] 不再依赖 LLMClient

---

### 任务 9：清理与移除

**状态**：✅ 已完成

**目标**：删除 Agent 架构中已废弃的自研代码引用

**已完成**：
- [x] `graph.py` 移除 `LLMClient` import 和依赖
- [x] `router.py` 移除 `LLMClient` import 和依赖
- [x] Agent 图不再引用自研 `LLMClient`
- [x] `graph.py` 移除了 `_build_agent_messages()`、`_parse_text_tool_calls()`、`_find_tool_use_id()`、`json_safe_str()`、`_sanitize_sensitive_data()`（旧版）、旧 `tools_node()` 等所有死代码（-500 行）
- [x] `registry.py` 移除了 `get_tool_schemas()`、`_tool_to_anthropic_schema()`、`_INJECTED_CONN_PARAMS`、`_clean_schema_property()`、`_extract_args_schema()`、`get_tools_for_binding()` 等手动 Schema 过滤代码
- [x] `nl2sql.py` + `diagnosis.py` 从旧 `LLMClient` 迁移到 `build_chat_model()`（LangChain 标准）
- [x] **`app/engine/llm_client.py` 已删除**（-771 行自研代码）

---

### 任务 10：更新后端文档

**状态**：✅ 已完成

**目标**：同步架构变更到项目文档

**要做的事**：
1. 更新 `backend/AGENTS.md`：LangChain Chat 模型标准、SafeToolNode 描述、工具定义规范
2. 更新 `backend/backend-tasks.md`：添加本次重构任务状态

**验收标准**：
- [x] AGENTS.md 反映实际技术栈（LangChain Chat 模型 + bind_tools + SafeToolNode + InjectedToolArg）
- [x] 新开发者按文档能正确理解架构
- [x] backend-tasks.md 已添加 B-32 重构任务条目

---

### 任务 11：前端改动清单

**状态**：✅ 已完成

**目标**：整理前端影响，输出独立文档（见 `FRONTEND_CHANGES_v2.md`）

---

## 四、执行顺序

```
任务 1 (Chat 模型工厂)
  ├── 任务 2 (State 重构)
  ├── 任务 8 (IntentRouter)
  └── 任务 3 (工具签名更新)
        └── 任务 4 (agent_node 重写)
              └── 任务 5 (SafeToolNode)
                    └── 任务 6 (图结构重构)
                          └── 任务 7 (SSE 事件流)
                                └── 任务 9 (清理)
                                      └── 任务 10 (文档) + 任务 11 (前端清单)
```

---

## 五、验证方案

### 集成测试
- [ ] POST /api/chat/stream — 完整 Agent 对话流程
- [ ] POST /api/connections/{id}/troubleshoot — 故障排查流程
- [ ] SSE 事件类型完整性（7 种 type 全部出现）
- [ ] 取消机制验证

### 回归检查
- [ ] Anthropic provider 正常
- [ ] OpenAI/百炼 provider 正常
- [ ] 密码不落盘
- [ ] SQL 审计正常
- [ ] 敏感数据脱敏正常
- [ ] Agent trace 信息完整
- [ ] 日志格式正常
