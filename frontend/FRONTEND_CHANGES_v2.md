# 前端改动清单（2026-07-03 LangChain 标准化重构）

> 版本: v2.0 · 日期: 2026-07-03 · 关联后端文档: [docs/refactoring-plan.md](../docs/refactoring-plan.md)

---

## 一、破坏性变更：无

**SSE 事件格式（7 种 type 的 payload 结构）完全向后兼容。**
前端无需任何必须修改即可正常工作。

---

## 二、后端架构变更摘要（供前端了解）

| 变更点 | 旧架构 | 新架构 |
|--------|--------|--------|
| LLM 调用方式 | 自研 `httpx` REST 客户端 | LangChain `ChatAnthropic` / `ChatOpenAI` 标准模型 |
| 工具绑定 | 手动 JSON Schema 转换 | `model.bind_tools()` 标准 API |
| 工具执行 | 手动 `TOOL_REGISTRY.get()` + `ainvoke()` | LangGraph `ToolNode` + 自定义 SafeToolNode |
| 消息管理 | 自定义 `dict` 列表（混合 LLM 消息和 SSE 事件） | 标准 LangChain 消息类型 + 独立 `sse_events` 字段 |
| SSE 事件来源 | `state.messages`（混合 LLM 上下文和前端展示） | `state.sse_events`（专用于前端事件） |

---

## 三、SSE 事件契约（无变化）

以下 8 种 SSE 事件类型和 payload 结构与重构前**完全一致**：

### thinking（Agent 推理过程）
```json
{
  "type": "thinking",
  "content": "用户想看最近7天的数据，我先查一下有哪些表...",
  "agent_run_id": "run_a1b2c3d4e5f6",
  "iteration": 1,
  "reasoning_type": "planning"
}
```

### tool_call（工具调用开始）
```json
{
  "type": "tool_call",
  "tool": "list_tables",
  "args": {},
  "display": "正在执行 list_tables...",
  "agent_run_id": "run_a1b2c3d4e5f6",
  "iteration": 1
}
```

### tool_result（工具执行完成）
```json
{
  "type": "tool_result",
  "tool": "list_tables",
  "summary": "返回 12 张表",
  "duration_ms": 320,
  "agent_run_id": "run_a1b2c3d4e5f6",
  "iteration": 1,
  "safety_checks_passed": true
}
```

### sql（生成的 SQL）
```json
{
  "type": "sql",
  "content": "SELECT * FROM orders WHERE created_at > DATE_SUB(NOW(), INTERVAL 7 DAY)",
  "audit_status": "passed",
  "is_readonly": true,
  "agent_run_id": "run_a1b2c3d4e5f6",
  "iteration": 2
}
```

### result（最终结果）
```json
{
  "type": "result",
  "summary": "最近7天共有 1523 条订单...",
  "tool_calls_made": 2
}
```

### text（纯文本回复）
```json
{
  "type": "text",
  "content": "你好！我是 DB-Pilot 数据库运维助手..."
}
```

### error（错误）
```json
{
  "type": "error",
  "error_code": "AGENT_ERROR",
  "user_message": "AI 服务暂时不可用，请稍后重试",
  "severity": "error"
}
```

### done（流结束）
```json
{
  "type": "done",
  "session_id": "sess_abc123",
  "tokens_used": 0,
  "agent_run_id": "run_a1b2c3d4e5f6",
  "total_iterations": 3
}
```

---

## 四、行为变更（轻微，不影响渲染）

| 变更点 | 旧行为 | 新行为 | 对前端影响 |
|--------|--------|--------|-----------|
| LLM 流式推理频率 | 每轮迭代 1 次 thinking | 可能有更多 thinking 事件（LLM 更频繁输出） | thinking 消息数量增加，现有自适应列表已支持 |
| 工具调用并行 | 串行执行 | 可能并行执行多个独立工具 | 需确保多个 tool_call running 状态并存（当前已有支持） |
| 模型兼容性 | 支持 fallback 文本解析 | 仅支持原生 function calling 模型 | 无影响 |
| thinking 内容 | 中文规划文本 | 同上 | 无变化 |

---

## 五、前端兼容性验证清单

以下检查项帮助前端开发人员确认现有代码无需修改：

- [x] `thinking` 事件解析 — `useSSE.ts` 按 `type === 'thinking'` 分发，无变化
- [x] `tool_call` 事件解析 — 按 `type === 'tool_call'` 分发，`findLastRunningToolCall()` 逻辑不变
- [x] `tool_result` 事件解析 — 更新 tool_call 的 stepStatus 为 done，逻辑不变
- [x] `sql` 事件解析 — 推送 SqlBlock 消息，逻辑不变
- [x] `result` 事件解析 — 推送 ResultTable，逻辑不变
- [x] `error` 事件解析 — 推送 ErrorCard，逻辑不变
- [x] `done` 事件解析 — 停止流式状态，更新 session_id，逻辑不变
- [x] 取消按钮 — `cancelStreaming()` 调用 `POST /api/chat/cancel` + `disconnect()`，逻辑不变
- [x] 超时处理 — 120s 无消息超时，逻辑不变
- [x] MessageBubble.vue 渲染 — 根据 `message.type` 分发组件，类型枚举不变
- [x] chat store — `StoreMessage` 结构不变，所有字段保持

---

## 六、新增可选功能（建议后续迭代，非本次必须）

| 功能 | 说明 | 优先级 |
|------|------|--------|
| thinking 打字机效果 | LLM 更频繁的 thinking 输出可做逐字显示动画 | P2 |
| 并行工具调用卡片 | 多个工具同时执行时，各自独立展示 running 状态 | P3 |
| Agent 调试面板 | 基于 astream_events 的细粒度事件展示每轮决策详情 | P3 |
| 工具调用甘特图 | 可视化每步操作的耗时和时序关系 | P4 |

---

## 七、关键文件参考

| 文件 | 说明 |
|------|------|
| [docs/refactoring-plan.md](../docs/refactoring-plan.md) | 后端重构执行计划 |
| [backend/app/agent/graph.py](../backend/app/agent/graph.py) | LangGraph 图定义 |
| [backend/app/agent/tool_node.py](../backend/app/agent/tool_node.py) | SafeToolNode（安全护栏 + 连接注入） |
| [backend/app/api/chat.py](../backend/app/api/chat.py) | SSE 流引擎 |
| [frontend/src/types/chat.ts](src/types/chat.ts) | SSE 事件 TypeScript 类型定义 |
| [frontend/src/composables/useSSE.ts](src/composables/useSSE.ts) | SSE 流客户端 composable |
| [frontend/src/stores/chat.ts](src/stores/chat.ts) | 对话状态 Store |

---

> **结论**：本次后端重构对前端是**透明**的。SSE 事件格式保持不变，所有字段向后兼容。前端现有代码无需任何修改即可正常运作。
