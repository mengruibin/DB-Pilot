# 思考过程与最终回答分离 — 实施计划

## 功能概述

当前所有 LLM 输出的 SSE token 事件都写死 `stage: "thinking"`，前端无法区分「推理思考文本」和「最终回答文本」。本方案通过以下三层改造实现精准分离：

1. **启用模型深度思考模式** — 利用 DeepSeek 的 `reasoning_content` 原生能力，将内部推理与对外输出天然分离
2. **乐观渲染 + 状态收编** — 内容到达即渲染，阶段确定后前端无缝收编，无需缓冲或等待
3. **思考过程统一容器** — 所有 thinking/reasoning/tool_call/tool_result 归入降权容器，与最终答案形成视觉双层结构

**参考产品**：Cursor、Claude Code 的思考过程面板设计

---

## 核心目标

| 目标 | 描述 |
|------|------|
| 正确性 | 思考文本不再误显示为最终回答；最终回答不再被折叠到 thinking_group |
| 流式体验 | 最终回答保留打字机效果；思考文本实时流式展示 |
| 零延迟 | 内容到达即渲染，不引入人为缓冲等待 |
| 视觉分度 | 思考容器（低权重灰色）vs 回答区域（正常权重）有明确的视觉层次 |

---

## 执行任务

### Task 1: models.py — 新增 CustomChatOpenAI（保留 reasoning_content）

**文件**: `backend/app/agent/models.py`

**改动**：

1. 新增 `CustomChatOpenAI(ChatOpenAI)` 子类，重写 `_convert_delta_to_message_chunk`：
   ```python
   class CustomChatOpenAI(ChatOpenAI):
       def _convert_delta_to_message_chunk(self, _dict, default_class):
           chunk = super()._convert_delta_to_message_chunk(_dict, default_class)
           reasoning = _dict.get("reasoning_content")
           if reasoning:
               chunk.additional_kwargs = {**chunk.additional_kwargs, "reasoning_content": reasoning}
           return chunk
   ```

2. 构建模型时若配置启用 reasoning，传入 `model_kwargs={"thinking": {"type": "enabled"}}`

**依赖**：`config.py` 新增 `ENABLE_REASONING` 配置项

**验收标准**：
- [ ] `ChatOpenAI` 不再丢弃 `reasoning_content`
- [ ] AIMessageChunk 的 `additional_kwargs["reasoning_content"]` 可读取
- [ ] 不影响非 reasoning 模式下的正常使用

**状态**: ✋ 待实施

---

### Task 2: config.py — 新增推理模式配置

**文件**: `backend/app/config.py`

**改动**：新增两项配置：
```python
ENABLE_REASONING: bool = Field(default=False, description="启用模型深度思考模式")
LLM_REASONING_MODEL: str = Field(default="", description="推理模型名称")
```

**验收标准**：
- [ ] `.env` 中设置 `ENABLE_REASONING=true` 后生效
- [ ] 为空时不影响现有行为

**状态**: ✋ 待实施

---

### Task 3: chat.py — 乐观渲染 SSE 流 + 新增 reasoning 事件

**文件**: `backend/app/api/chat.py`

**改动**：

1. **移除之前的纠偏逻辑**（`seen_tool_calls_in_iter` / `stage_change_sent_for_iter` / `is_answer_confirmed` 三变量）

2. **新增 reasoning 事件**（messages 通道）：
   ```python
   reasoning = chunk.additional_kwargs.get("reasoning_content", "")
   if reasoning.strip():
       yield format_sse({"type": "reasoning", "content": reasoning, "agent_run_id": run_id})
   elif tool_call_chunks := (chunk.tool_call_chunks or []):
       seen_tool_calls_in_iter = True
       # 不发射 token — tool_call 事件由 sse_events 处理
   elif content := str(getattr(chunk, "content", "") or ""):
       yield format_sse({"type": "token", "stage": "thinking", "content": content, "agent_run_id": run_id})
   ```

3. **is_complete 收编**（updates 通道，`agent_node` 节点完成后）：
   ```python
   if node_name == "agent":
       if not seen_tool_calls_in_iter:
           yield format_sse({"type": "stage_change", "stage": "answer", "agent_run_id": run_id})
       # 重置每轮迭代标志
       seen_tool_calls_in_iter = False
   ```

**SSE 事件流示例**：
```
reasoning ← 永远是思考面板
token(stage="thinking") ← 乐观渲染（可能是思考也可能是最终回答）
→ tool_call_chunks 到达 → 确认为思考，无需变更
→ is_complete 到达 + 无 tool_calls → stage_change("answer") → 前端收编
```

**验收标准**：
- [ ] `reasoning_content` token → 正确发射 `{"type": "reasoning", ...}` 事件
- [ ] 普通 content token → 按 `stage: "thinking"` 乐观发射
- [ ] 工具调用迭代 → 不发送 `stage_change`
- [ ] 最终回答迭代 → 在 is_complete 时发送 `stage_change("answer")`
- [ ] 无缓冲延迟，token 到达即发射

**状态**: ✋ 待实施

---

### Task 4: 前端 SSE 事件类型扩展

**文件**:
- `frontend/src/types/chat.ts`
- `frontend/src/composables/useSSE.ts`

**改动**：

1. **types/chat.ts**:
   - 新增 `ReasoningEvent` 接口
   - `SSEEventCallbacks` 新增 `onReasoning?: (event: ReasoningEvent) => void`
   - `StoreMessageType` 新增 `'reasoning'`
   - `StoreMessage` 的 `stage` 字段文档更新

2. **useSSE.ts**: dispatch switch 新增 `case 'reasoning'`

**验收标准**：
- [ ] TypeScript 类型检查通过
- [ ] SSE `reasoning` 事件正确分发到 `onReasoning` 回调
- [ ] 前端无新增类型错误

**状态**: ✋ 待实施

---

### Task 5: chat.ts — 状态收编逻辑

**文件**: `frontend/src/stores/chat.ts`

**改动**：

1. **新增 `onReasoning` 回调**：
   ```typescript
   onReasoning: (event: ReasoningEvent) => {
     removeWaitingMessage()
     sealStreamingMessage()
     messages.value.push({
       id: nextMsgId(),
       sessionId: currentSessionId.value ?? '',
       role: 'assistant',
       type: 'reasoning',
       content: event.content,
       agentRunId: event.agent_run_id,
       createdAt: new Date().toISOString(),
     })
   }
   ```

2. **重写 `onStageChange`**（状态收编核心）：
   ```typescript
   onStageChange: (_event: StageChangeEvent) => {
     const newStage = (_event as any).stage as 'thinking' | 'answer'
     if (newStage === currentStage.value) return

     if (newStage === 'answer') {
       // 收编：向前扫描，将本 turn 内所有 text(stage=thinking) 改为 answer
       for (let i = messages.value.length - 1; i >= 0; i--) {
         const m = messages.value[i]
         if (m.role !== 'assistant') break
         if (m.type === 'text' && m.stage === 'thinking') {
           m.stage = 'answer'
         }
       }
       messages.value = [...messages.value]
       sealStreamingMessage()
       currentStage.value = 'answer'
     } else {
       // thinking 降级（罕用，兼容纠偏）
       sealStreamingMessage()
       currentStage.value = 'thinking'
     }
   }
   ```

**验收标准**：
- [ ] `onReasoning` 正确创建 reasoning 类型消息
- [ ] 最终回答阶段触发 `stage_change("answer")` 后，前面的 text 消息 stage 正确收编为 answer
- [ ] 收编后消息从思考面板移动到主聊天区（通过 MessageList 分组联动）
- [ ] `postProcessTurn` 逻辑不变

**状态**: ✋ 待实施

---

### Task 6: 前端 UI 渲染 — reasoning 消息 + 收编联动

**文件**:
- `frontend/src/components/chat/MessageBubble.vue`
- `frontend/src/components/chat/MessageList.vue`

**改动**：

1. **MessageBubble.vue**:
   - 新增 `type: 'reasoning'` 渲染（`.reasoning-item` class）
   - 光标 `cursor-blink` 条件：`isStreaming && isLast && message.type === 'text' && message.stage !== 'thinking'`
   - 保留已有的 `.thinking-phase-item` 降权样式

2. **MessageList.vue**:
   - `isThinkingPhaseMessage` 扩展：
     ```typescript
     function isThinkingPhaseMessage(msg: StoreMessage): boolean {
       if (['waiting', 'reasoning', 'tool_call', 'tool_result', 'sql'].includes(msg.type)) return true
       if (msg.type === 'text' && msg.stage === 'thinking') return true
       return false
     }
     ```

**验收标准**：
- [ ] reasoning 消息在思考容器内渲染，字体/颜色/透明度为降权样式
- [ ] 思考过程中不显示打字光标
- [ ] 收编后 text(stage=answer) 正确退出思考容器，进入正常渲染区
- [ ] 思考容器与回答区域视觉效果有明确区别

**状态**: ✋ 待实施

---

### Task 7: 回归清理 — 移除之前临时方案残留

**文件**:
- `backend/app/api/chat.py` — 移除之前的 `seen_tool_calls_in_iter` / `stage_change_sent_for_iter` / `is_answer_confirmed` 字段
- `frontend/src/stores/chat.ts` — 简化 `updateStreamingMessage`（保留 Task 6 已做的响应式修复）

**验收标准**：
- [ ] 无 dead code
- [ ] ruff check 通过
- [ ] vue-tsc --noEmit 通过

**状态**: ✋ 待实施

---

## 任务依赖关系

```
Task 1 (models.py) ──┐
                     ├──→ Task 3 (chat.py 后端)
Task 2 (config.py) ──┘        │
                               ▼
Task 4 (types + SSE) ──→ Task 5 (chat.ts) ──→ Task 6 (UI)
                                                 │
                                                 ▼
                                            Task 7 (清理)
```

## 任务状态汇总

| Task | 文件 | 状态 |
|------|------|------|
| Task 1 | `backend/app/agent/models.py` | ✅ 已完成 |
| Task 2 | `backend/app/config.py` | ✅ 已完成 |
| Task 3 | `backend/app/api/chat.py` | ✅ 已完成 |
| Task 4 | `frontend/src/types/chat.ts`, `useSSE.ts` | ✅ 已完成 |
| Task 5 | `frontend/src/stores/chat.ts` | ✅ 已完成 |
| Task 6 | `frontend/src/components/chat/MessageBubble.vue`, `MessageList.vue` | ✅ 已完成 |
| Task 7 | 回归清理 | ✅ 已完成 |
