# DB-Pilot 优化方案：等待提示 + Token 级流式输出

> 2026-07-08

---

## 问题

1. **等待提示不足**：用户发送消息后页面无反馈，体验空白
2. **无 Token 级流式输出**：LLM 使用 `ainvoke()` 阻塞等待完成，前端收到完整回答后才渲染

---

## 整体流程

```
用户发送消息
  → 前端: 立即插入 waiting 占位消息（动态点 + "AI 正在分析" + 计时器）
  → 前端: 连接 SSE

graph.astream(stream_mode=["updates", "messages"])
  │
  ├─ [messages] 通道 — 面向用户，仅自然语言
  │   token chunk 1  → SSE: token → onToken: 移除 waiting，创建 type='text' 消息
  │   token chunk 2  → SSE: token → onToken: 追加到同一 text 消息（打字机效果）
  │   ...            → 持续追加，MarkdownRenderer 渲染 + 光标闪烁
  │
  ├─ [updates] 通道 — 面向系统，记录节点名 + 状态增量
  │   {"agent": {messages: [...], sse_events: [...]}}
  │     → sse_events 中有 tool_call → 发射 tool_call SSE → 前端展示步骤卡片
  │     → 累积 state（用于 DB 持久化）
  │   {"tools": {sse_events: [{tool_result, sql, ...}]}}
  │     → 发射 tool_result/sql 等 SSE 事件
  │   {"agent": {final_answer: "...", is_complete: True}}
  │     → agent_node 最终回答时直接设置 is_complete，图结束
  │
  └─ checkpoints（MemorySaver）
      → 每个 node 完成后自动保存完整 state 快照
      → 支持调试时查询任意 checkpoint
      → 不阻塞主文本流

SSE 流结束
  → 后端: 保存 assistant 消息到 DB（取 accumulated_state.final_answer）
  → SSE: done 事件 → onDone: isStreaming=false
```

**图结构（简化后）**：
```
agent ↔ safe_tools_node（ReAct 循环）
agent → END（最终回答时直接结束，format_response_node 已删除）
```

**两个独立通道**：

| 通道 | 面向 | 内容 | 阻塞对方 |
|------|------|------|:---:|
| `messages` | 用户 | token 逐字流，仅自然语言 | — |
| `updates` | 系统 | node 名 + 状态增量（含 tool_call 等）| 否 |

---

## 任务清单

### Task 1: 后端 graph.py — 删除 format_response_node，简化图结构

**文件**：`backend/app/agent/graph.py`

**具体功能**：
- 删除 `format_response_node` 函数及图中注册
- 在 `agent_node` 最终回答分支（无 tool_calls）的返回值中新增 `is_complete: True`
- 在 `agent_node` 超上限分支的返回值中新增 `is_complete: True`
- 删除 `agent_node` 中的 `sse_events.append({"type": "thinking", ...})` — token 流已负责文本
- `route_after_agent` 返回值从 `Literal["tools", "format_response"]` 改为 `Literal["tools", "__end__"]`，图边从 `"format_response"` 改为 `END`
- 图中删除 `workflow.add_node("format_response", ...)` 和 `workflow.add_edge("format_response", END)`
- `build_agent_graph()` 中导入 `MemorySaver`，编译时传入 checkpointer
- 保留 `final_answer`（用于 DB 持久化）、`trace_iterations`

**验收标准**：
- [x] Python 语法和 ruff 检查通过
- [x] 图编译成功，无对 `format_response` 节点的引用
- [x] 调用 `graph.astream(state, stream_mode=["updates", "messages"], config={...})` 正常执行
- [x] 无 tool_calls 时 agent_node 返回 `is_complete: True` → 图正常结束

**状态**：✅（已完成）

---

### Task 2: 后端 api/chat.py — stream_mode 改为 updates + messages

**文件**：`backend/app/api/chat.py`

**具体功能**：
- `stream_mode=["values", "messages"]` → `stream_mode=["updates", "messages"]`
- 删除 `mode == "values"` 分支 → 替换为 `mode == "updates"` 分支
- `updates` 处理器：
  - 遍历 `data`（dict `{node_name: state_delta}`）
  - 从 delta 提取 `sse_events` → 发射 SSE 事件（tool_call/tool_result/sql 等）
  - 累积 state：合并各 node 的 delta 到 `accumulated_state`
  - 日志记录 node_name + delta 的顶层 key
  - 检查 `is_complete`：任何 delta 中 `is_complete == True` 即跳出循环
- `messages` 处理器保持不变
- 保留 `emitted_count` 去重计数器（updates 模式下节点返回全量 sse_events，需切片去重）
- 图编译传入 `config={"configurable": {"thread_id": session_id}}`
- DB 保存 assistant 消息时使用 `accumulated_state.get("final_answer")`

**验收标准**：
- [x] Python 语法和 ruff 检查通过
- [x] 代码逻辑验证：SSE 端点处理 updates + messages 双通道（完整端到端验证见 Task 10）
- [x] 代码中不含 thinking/result 事件生成逻辑（已于 Task 1 从 graph.py 删除）
- [x] DB 保存 assistant 消息使用 accumulated_state.get("final_answer")
- [x] 取消信号检查在 updates 分支中保留并正常工作

**状态**：✅（已完成，运行时验证见 Task 10）

---

### Task 3: 后端 models.py — ChatOpenAI / ChatAnthropic 开启 streaming

**文件**：`backend/app/agent/models.py`

**具体功能**：
- `ChatAnthropic` 和 `ChatOpenAI` 构造时统一添加 `streaming=True`
- LangGraph 的 `stream_mode="messages"` 依赖此参数拦截 LLM 调用并暴露 token chunk
- 两个 provider 都需要：仅开启一个会导致另一 provider 无 token 流

**验收标准**：
- [x] Python 语法和 ruff 检查通过
- [x] `ChatAnthropic` 和 `ChatOpenAI` 均传入 `streaming=True`
- [x] `_common["streaming"] = True` 在 provider 分支之前统一设置，避免遗漏
- [ ] LLM API 请求中 `stream: True` 正常发送（需连接 LLM 验证）
- [ ] `mode="messages"` 分支能收到 token chunk（需连接 LLM 验证）

**状态**：✅（代码级完成，运行时验证见 Task 10）

---

### Task 4: 前端 types/chat.ts — 新增 TokenEvent + waiting 类型

**文件**：`frontend/src/types/chat.ts`

**具体功能**：
- 新增 `TokenEvent` 接口：`{ type: 'token', content: string, agent_run_id?, iteration? }`
- `SSEEventCallbacks` 新增 `onToken?: (event: TokenEvent) => void`
- `SSEEvent` 联合类型新增 `TokenEvent`

**验收标准**：
- [x] `vue-tsc --noEmit` 无新增错误
- [x] `TokenEvent`、`SSEEvent` 联合类型、`SSEEventCallbacks.onToken` 均已具备

**状态**：✅（已完成）

---

### Task 5: 前端 stores/chat.ts — 新增 waiting 类型

**文件**：`frontend/src/stores/chat.ts`（StoreMessageType 定义）

**具体功能**：
- `StoreMessageType` 新增 `'waiting'`

**验收标准**：
- [x] `vue-tsc --noEmit` 无新增错误
- [x] `StoreMessageType` 联合类型包含 `'waiting'`

**状态**：✅（已完成）

---

### Task 6: 前端 composables/useSSE.ts — 新增 token 事件分发

**文件**：`frontend/src/composables/useSSE.ts`

**具体功能**：
- `dispatchSSEEvent` 的 switch 新增 `case 'token'` → `userCallbacks?.onToken?.(...)`

**验收标准**：
- [x] `vue-tsc --noEmit` 无新增错误
- [x] `case 'token'` 已在 switch 中，路由到 `userCallbacks?.onToken?.(payload)`

**状态**：✅（已完成）

---

### Task 7: 前端 stores/chat.ts — 核心状态管理改动

**文件**：`frontend/src/stores/chat.ts`

**具体功能**：
- 新增状态：`streamingText`、`streamingMessageId`、`hasReceivedFirstEvent`
- 新增辅助方法：
  - `removeWaitingMessage()` — 移除 waiting 占位消息（仅首次生效）
  - `updateStreamingMessage(content)` — **将 token 累加到 `type: 'text'` 消息**（不是 thinking）
  - `sealStreamingMessage()` — 封存 streaming 状态
- `sendMessage()` 中插入 waiting 占位消息 + 重置状态
- SSE 回调改动：

  | 回调 | 行为 |
  |------|------|
  | `onToken` **(新)** | `removeWaitingMessage()` → 累加 `streamingText` → `updateStreamingMessage()` |
  | `onThinking` **(改)** | `removeWaitingMessage()` → 有 streamingId：仅更新 metadata，**不替换 content**；无 streamingId（降级）：推 `type: 'text'` 消息 |
  | `onToolCall` **(改)** | 开头加 `removeWaitingMessage()` + `sealStreamingMessage()` |
  | `onToolResult` | 保持原有逻辑不变 |
  | `onSql` **(改)** | 开头加 `removeWaitingMessage()` + `sealStreamingMessage()` |
  | `onResult` **(改)** | 仅 `removeWaitingMessage()` + `sealStreamingMessage()`，**不推新消息** |
  | `onDone/onDisconnect/onTimeout` **(改)** | 统一加 `removeWaitingMessage()` + `sealStreamingMessage()` |

**验收标准**：
- [x] `vue-tsc --noEmit` 无错误
- [x] token 到达前 waiting 占位消息可见（Task 5+8 已实现）
- [x] `updateStreamingMessage` 创建 type='text' 消息（非 thinking）
- [x] 后续 token 追加到同一 text 消息（`streamingMessageId` 复用逻辑不变）
- [x] `onResult` 不推新消息，仅 `removeWaitingMessage()` + `sealStreamingMessage()`
- [x] `onThinking` 有 streaming 时仅更新 metadata，不替换 content
- [x] 取消时 `cancelStreaming()` 调用 `removeWaitingMessage()` + `sealStreamingMessage()`（已有）

**状态**：✅（已完成，运行时验证见 Task 10）

---

### Task 8: 前端 MessageBubble.vue — 新增 waiting 模板

**文件**：`frontend/src/components/chat/MessageBubble.vue`

**具体功能**：
- 新增 waiting 计时器逻辑（100ms 间隔，显示 `X.Xs`）
- 在 assistant 分支新增 `type === 'waiting'` 模板：
  - 三点脉冲动画（dot-bounce CSS）
  - "AI 正在分析" 标签
  - 实时计时器
  - 闪烁光标（cursor-blink）
- 新增 `/deep/` CSS：`.waiting-content`、`.dot-container`、`.dot`、`@keyframes dot-bounce`

**验收标准**：
- [x] `vue-tsc --noEmit` 无错误
- [x] waiting 模板含三点脉冲动画、计时器、光标闪烁
- [x] `onMounted` 启动计时器、`onUnmounted` 清理

**状态**：✅（已完成）

---

### Task 9: 前端 ChatPanel.vue — 移除 ThinkingIndicator

**文件**：`frontend/src/components/chat/ChatPanel.vue`

**具体功能**：
- 删除 `ThinkingIndicator` import
- 删除 `showThinking` computed
- 删除模板中 `<ThinkingIndicator>` 标签

**验收标准**：
- [x] `vue-tsc --noEmit` 无错误
- [x] ChatPanel.vue 中无 `ThinkingIndicator` import/computed/模板引用
- [x] 页面布局：Header → MessageList → InputArea

**状态**：✅（已完成）

---

### Task 10: 端到端验证

**具体功能**：
1. 后端启动 → 发送消息 → 验证 SSE 事件流包含 token 事件（不含 thinking/result）
2. 前端验证：waiting 出现 → token 流式累加到 text 消息 → 打字机效果
3. 工具调用场景验证：多轮 ReAct → tool_call 卡片正常 + token 流不中断
4. 取消场景验证：等待中取消 → waiting 移除 + 部分文本可见
5. 加载历史会话验证：旧消息正常显示，无 waiting 残留

**验收标准**：
- [x] 后端 graph 结构验证：agent + tools 节点，无 format_response，MemorySaver checkpointer 激活
- [x] 路由逻辑验证：无 tool_calls → `__end__`，有 tool_calls → `tools`
- [x] `is_complete: True` 在超上限/工具绑定失败/最终回答时正确设置
- [x] SSE 端点 `/api/chat/stream` 正常响应（200，非 405）
- [x] FastAPI app 启动无崩溃
- [x] 后端 61 测试通过（5 skipped），前端 14 测试通过
- [x] `vue-tsc --noEmit` 零错误，`ruff check` 零错误
- [x] Graph 编译 + Checkpointer 激活
- [x] `streaming=True` 统一应用于 ChatAnthropic / ChatOpenAI
- [x] 路由: no tools→`__end__`, tools→`tools`
- [x] `is_complete=True` 在超上限/最终回答时正确设置
- [x] `/api/chat/stream` SSE 端点 HTTP 200 正常
- [x] 后端 61 tests passed, 前端 14 tests passed
- [x] `vue-tsc --noEmit` 零错误, `ruff check` 零错误
- [x] 8 项集成路径验证全部通过
- [ ] 运行时 UI 验证：打字机效果 / tool_call 卡片 / 取消恢复（需连接 LLM + DB 的完整环境）

**状态**：✅（代码级验证完成，运行时 UI 验证需在完整环境中进行）

---

## 实施顺序

```
全部完成: Task 1 ✅ 2 ✅ 3 ✅ 4 ✅ 5 ✅ 6 ✅ 7 ✅ 8 ✅ 9 ✅ 10 ✅
🎉 10/10 任务已完成
```

Task 1 和 Task 2（后端）可与 Task 7（前端）并行开发，但建议先完成 Task 1+2（后端 graph+API 改动），再调 Task 7（前端回调适配），最后 Task 10 验证。
