# 前端改动清单（2026-07-02 Agent 架构升级）

> 此文档整理本次后端 Agent 架构升级对前端的影响，供前端开发人员同步修改。
> 版本: v1.0 · 日期: 2026-07-02

---

## 一、破坏性变更：无

**所有 SSE 事件新增字段均为可选，不删除或重命名现有字段。** 前端未升级时仍可按旧逻辑解析，不受影响。

---

## 二、SSE 事件契约变更

### 2.1 `thinking` 事件

| | 旧行为 | 新行为 |
|---|--------|--------|
| 内容 | 静态中文文本（如"正在获取数据库结构信息..."） | LLM 流式推理输出（逐 token 推送的真实 AI 思考过程） |
| 出现频率 | 每种 Intent 固定 1-3 次 | 每轮 ReAct 迭代可能出现 1 次 |

**新增可选字段：**

```diff
{
  "type": "thinking",
- "content": "正在获取数据库结构信息..."
+ "content": "用户想看最近7天的订单数据，我需要先调用 list_tables 查看有哪些表...",
+ "agent_run_id": "run_a1b2c3d4e5f6",   // 本次 Agent 运行唯一 ID
+ "iteration": 1,                         // 当前 ReAct 第几轮
+ "reasoning_type": "planning"            // planning | observing | concluding | error_recovery
}
```

**前端适配建议：**
- `thinking` 内容不再是固定文本，建议以打字机效果或可折叠区域展示
- 如果之前按 `content === "正在获取..."` 做了硬编码判断，改为按 `reasoning_type` 判断
- 同一轮对话中可能出现多次 `thinking` 事件（多轮迭代）

### 2.2 `tool_call` 事件

```diff
{
  "type": "tool_call",
  "tool": "get_slow_queries",
  "args": {"time_range": "1h", "limit": 20},
  "display": "正在执行 get_slow_queries...",
+ "agent_run_id": "run_a1b2c3d4e5f6",   // 新增
+ "iteration": 1                          // 新增
}
```

### 2.3 `tool_result` 事件

```diff
{
  "type": "tool_result",
  "tool": "get_slow_queries",
  "summary": "返回 3 条慢查询记录",
  "duration_ms": 1200,
+ "agent_run_id": "run_a1b2c3d4e5f6",   // 新增
+ "iteration": 1,                         // 新增
+ "safety_checks_passed": true            // 新增: 安全护栏检查结果
}
```

### 2.4 `sql` 事件

```diff
{
  "type": "sql",
  "content": "SELECT COUNT(*) FROM orders WHERE created_at > DATE_SUB(NOW(), INTERVAL 7 DAY)",
  "audit_status": "passed",
  "is_readonly": true,
+ "agent_run_id": "run_a1b2c3d4e5f6",   // 新增
+ "iteration": 2                          // 新增
}
```

### 2.5 `done` 事件

```diff
{
  "type": "done",
  "session_id": "sess_abc123",
  "tokens_used": 0,
+ "agent_run_id": "run_a1b2c3d4e5f6",   // 新增
+ "total_iterations": 3,                  // 新增: Agent 总共推理了几轮
+ "trace_summary": {                      // 新增: 决策链路摘要（用于调试面板）
+   "tools_called": ["get_slow_queries", "explain_query"],
+   "total_duration_ms": 4520
+ }
}
```

---

## 三、行为变更

| 变更点 | 旧行为 | 新行为 | 前端适配 |
|--------|--------|--------|---------|
| 工具调用顺序 | 固定（代码硬编码），前端可以预设步骤数量 | 动态（LLM 决定），同一问题两次可能走不同路径 | 步骤展示改为**自适应列表**——收到一条 `tool_call` 追加一项 |
| 工具调用次数 | 每种 Intent 固定步数（QUERY=4 步 等） | 动态，Agent 可能调用 1~10 次工具 | 进度展示改为"已完成 N 步"，而非"N/4" |
| `thinking` 内容 | 静态中文文本 | LLM 流式推理文本 | 改为打字机效果或可折叠区域，初始折叠 |
| `result` 总结 | 简洁（如"查询完成：返回 1523 行"） | LLM 生成的完整自然语言总结 | 无影响，自然渲染即可 |
| 跨工具组合 | 不支持（"查慢查询并分析表索引"会失败） | Agent 自主组合任意工具 | 无影响，前端正常展示即可 |

---

## 四、新增可选功能（非必须，建议后续迭代）

| 功能 | 说明 | 优先级 |
|------|------|--------|
| Agent 调试面板 | 展示每轮 ReAct 的完整决策链（reasoning → tool_call → tool_result） | P2 |
| 工具调用时间线 | 可视化展示 Agent 每步操作的耗时和依赖关系 | P3 |
| 一键回放 | 基于 `trace_summary` 回放整个 Agent 决策过程 | P3 |

---

## 五、前端兼容性检查清单

- [x] `thinking` 事件按动态内容渲染（非固定文本匹配）——自动展示，且新增 `reasoning_type` 标签（意图分析/规划中/观察中/总结中/纠错中）
- [x] 支持同一轮对话中多次 `thinking` → `tool_call` → `tool_result` 循环——每个事件独立入消息队列，自适应展示
- [x] `done` 事件中 `trace_summary` 为新增可选字段，缺失时不 crash——Store 中通过 `last.traceSummary = event.trace_summary` 可选赋值
- [x] `agent_run_id` 为新增可选字段，缺失时不影响渲染——所有字段均通过 `?.` 可选链访问
- [x] 取消按钮在 Agent 执行过程中始终可用——已有 `cancelStreaming()` 实现
- [ ] SSE 连接中断重连后，能从 `agent_run_id` 判断是否需要重新发起请求——建议后续迭代实现自动重连逻辑

---

## 六、关键文件参考

| 文件 | 说明 |
|------|------|
| [docs/api-contract.md](../docs/api-contract.md) §1.2 | SSE 事件类型契约（已更新） |
| [backend/app/agent/graph.py](../backend/app/agent/graph.py) | LangGraph 图节点定义（SSE 事件来源） |
| [backend/app/api/chat.py](../backend/app/api/chat.py) | `_stream_events()` SSE 流引擎 |
| [frontend/AGENTS.md](AGENTS.md) | 前端开发规范（已更新） |
