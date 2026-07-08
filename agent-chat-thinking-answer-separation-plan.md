# 思考过程与最终回答分离渲染 — 任务清单

## 方案概述

所有 SSE `token` 事件新增 `stage` 字段（默认 `"thinking"`），后端确认最终回答后发送 `stage_change` 事件，前端按 `stage` 实时区分渲染区域。

```
思考阶段 token(stage:thinking) → tool_call → tool_result → ... →
回答阶段 token(stage:thinking) → stage_change → 前端重组(回答区+折叠) →
done(total_duration_ms)
```

---

## 任务 1：后端 — `token` 事件新增 `stage` 字段

**文件**：`backend/app/api/chat.py`（约 615-624 行）

**执行**：
- 在 `messages` 流处理分支中，`token` 事件 JSON 新增 `"stage": "thinking"` 字段
- 添加注释说明默认值为 thinking，最终回答由 stage_change 事件修正

**验收标准**：
- 启动后端，发送任意查询，观察 SSE 原始输出中每个 `token` 事件均包含 `"stage": "thinking"`
- `tool_call`、`tool_result`、`done` 等事件不受影响

**状态**：✅ 已完成

---

## 任务 2：后端 — 新增 `stage_change` 事件

**文件**：`backend/app/api/chat.py`（约 677 行 `is_complete` 检测点）

**执行**：
- 在 updates 流处理中，`node_output.get("is_complete")` 为 `True` 时，先 yield 一条 `stage_change` 事件再 break
- 事件格式：`{"type": "stage_change", "stage": "answer", "agent_run_id": run_id}`
- 使用 `format_sse()` 序列化

**验收标准**：
- 发送需要工具调用的查询，SSE 输出中 `done` 事件前出现 `stage_change` 事件
- `stage_change` 的 `stage` 字段为 `"answer"`
- 发送简单问候（无工具调用），同样出现 `stage_change`（因为 `is_complete` 仍会被设置）

**状态**：✅ 已完成

---

## 任务 3：后端 — `done` 事件新增 `total_duration_ms`

**文件**：`backend/app/api/chat.py`

**执行**：
- 在 `_stream_events()` 函数开头记录 `stream_start_time = time.monotonic()`
- 正常完成路径的 `done` 事件新增 `"total_duration_ms": int((time.monotonic() - stream_start_time) * 1000)`
- 取消路径的 `done` 事件（约 641 行）新增 `"total_duration_ms": 0`
- 异常路径的 `done` 事件（约 730、751 行）新增 `"total_duration_ms": 0`

**验收标准**：
- 正常完成的查询，`done` 事件中 `total_duration_ms` > 0 且数值合理（与主观等待时间一致）
- 取消流式后，`done` 事件中 `total_duration_ms` = 0
- `import time` 已存在于文件顶部（如不存在则添加）

**状态**：✅ 已完成

---

## 任务 4：前端类型定义 — 新增 `stage`、`stage_change`、`thinking_group` 类型

**文件**：`frontend/src/types/chat.ts`

**执行**：
- `TokenEvent` 接口新增 `stage: 'thinking' | 'answer'` 字段
- 新增 `StageChangeEvent` 接口：`{ type: 'stage_change', stage: 'answer', agent_run_id?: string }`
- `DoneEvent` 接口新增 `total_duration_ms?: number`
- `SSEEvent` 联合类型新增 `| StageChangeEvent`
- `SSEEventCallbacks` 接口新增 `onStageChange?: (event: StageChangeEvent) => void`
- `StoreMessageType` 联合类型新增 `| 'thinking_group'`
- 新增导出接口 `ThinkingGroupData: { steps: StoreMessage[], stepCount: number, totalDurationMs: number }`
- `StoreMessage` 接口新增 `stage?: 'thinking' | 'answer'` 和 `thinkingGroup?: ThinkingGroupData`

**验收标准**：
- `npx vue-tsc --noEmit` 类型检查通过
- 所有新增类型可被其他文件正常 import

**状态**：✅ 已完成

---

## 任务 5：前端 SSE 客户端 — 新增 `stage_change` 事件分派

**文件**：`frontend/src/composables/useSSE.ts`（约 100-131 行 `dispatchSSEEvent`）

**执行**：
- 在 `switch (type)` 中新增 `case 'stage_change':` 分支
- 调用 `userCallbacks.onStageChange?.(data as StageChangeEvent)`

**验收标准**：
- 在浏览器 DevTools 中监听 SSE 事件，`stage_change` 类型事件可被正确解析并触发回调
- 其他事件类型不受影响

**状态**：✅ 已完成

---

## 任务 6：前端 Store — 新增双区渲染状态机

**文件**：`frontend/src/stores/chat.ts`

**执行**：
- State 区域新增：
  - `currentStage` — `ref<'thinking' | 'answer'>('thinking')`
  - `turnStartIndex` — `ref(-1)`，指向当前轮 user 消息索引
- `sendMessage()` 中 `addUserMessage(text)` 之后：
  - 重置 `currentStage.value = 'thinking'`
  - 设置 `turnStartIndex.value = messages.value.length - 1`
- `onToken` 回调：根据 `currentStage` 区分处理（当前统一用 `updateStreamingMessage`，但标记 `stage` 字段给后续分组使用）
- 新增 `onStageChange` 回调：
  - 封存当前思考区流式文本（`sealStreamingMessage()`）
  - 切换 `currentStage.value = 'answer'`
  - 清空 `streamingText` / `streamingMessageId` 为回答区流式做准备
  - 将最后一条思考区 text 消息的 `stage` 更新为 `'answer'`
- `sendMessage()` 的 SSE 回调中注册 `onStageChange`

**验收标准**：
- 流式过程中 `currentStage` 初始为 `thinking`
- `stage_change` 到达后 `currentStage` 切换为 `answer`
- 思考区和回答区的 text 消息携带正确的 `stage` 字段
- `turnStartIndex` 正确指向本轮 user 消息

**状态**：✅ 已完成

---

## 任务 7：前端 Store — 新增 `postProcessTurn()` 分组函数

**文件**：`frontend/src/stores/chat.ts`

**执行**：
- 新增 `postProcessTurn(totalDurationMs: number)` 函数，放在 `sendMessage()` 之前
- 逻辑：
  1. 从 `turnStartIndex` 定位本轮消息范围
  2. 检查是否有 `tool_call`/`tool_result` → 无则跳过
  3. 以 `stage === 'answer'` 的 text 消息为边界
  4. 边界之前的 `text`/`tool_call`/`tool_result`/`sql` → 归入 `thinkingSteps[]`
  5. 边界之后的 → 保留在 `keepMessages[]`
  6. `error`/`diagnosis` 等 → 始终保留在 keepMessages
  7. 构建 `StoreMessage(type: 'thinking_group')`，包含 `ThinkingGroupData`
  8. 用 `messages.value = [...before, groupMsg, ...keepMessages]` 重建数组
  9. 重置 `turnStartIndex`、`currentStage`
- `onDone` 回调中调用 `postProcessTurn(event.total_duration_ms ?? 0)`
- `clearMessages()`、`startNewSession()`、`switchToSession()` 中重置 `turnStartIndex` 和 `currentStage`

**验收标准**：
- 有工具调用的查询完成后，消息列表中出现 `thinking_group` 类型消息
- thinking_group 的 `steps` 包含所有思考文本和工具步骤
- thinking_group 的 `stepCount` 等于工具调用次数
- thinking_group 的 `totalDurationMs` 与 `done` 事件一致
- 最终回答（`stage === 'answer'` 的 text）保留在 thinking_group 之后
- 纯文本回答不生成 thinking_group
- `error` 消息不被折叠到 thinking_group 中
- 历史消息加载不触发分组

**状态**：✅ 已完成

---

## 任务 8：前端新组件 — `ThinkingGroup.vue`

**文件**：`frontend/src/components/chat/ThinkingGroup.vue`（新建）

**执行**：
- 创建单文件组件，Props：
  - `steps: StoreMessage[]`（必填）
  - `stepCount: number`（必填）
  - `totalDurationMs: number`（必填）
  - `defaultExpanded: boolean`（可选，默认 `false`）
- 模板：
  - `.thinking-group` 容器
  - `button.tg-header` — 头部：chevron SVG（展开时 rotate-90）+ 🧠 + "思考过程" + `{{ stepCount }}步` 徽章 + 等宽字体耗时（右对齐）
  - `Transition[name="fade-slide"]` → `div.tg-body(v-show="expanded")`
  - 内部 `v-for` 遍历 steps：
    - `text` → `div.tg-thinking-text` 渲染 MarkdownRenderer（小字号、降透明度）
    - `tool_call` → `div.tg-tool-row`（🔧 图标 + tool 名称 + 参数摘要）
    - `tool_result` → `div.tg-tool-row`（✅ + tool 名称 + summary + durationMs 徽章）
    - `sql` → SqlBlock 组件（紧凑模式）
- 样式（scoped）：
  - 复用全局 CSS 变量
  - 复用 `fade-slide` 过渡动画（参考 `ThinkingIndicator.vue:179-192`）
  - 步骤文字 12px、`color: var(--text-tertiary)`、`opacity: 0.85`
  - 头部徽章 `background: rgba(129,140,248,0.1)`、`color: var(--accent-purple)`
  - 等宽字体耗时 `font-family: var(--font-mono)`、`font-size: 11px`、`margin-left: auto`

**验收标准**：
- 组件可独立渲染，传入 mock 数据验证
- 默认折叠，点击头部展开/折叠，chevron 旋转动画
- 展开/折叠过渡使用 `fade-slide` 动画
- 思考文本以低对比度样式渲染
- 工具步骤行正确区分 running/done 状态图标
- 耗时格式为 `X.Xs` 或 `XmX.Xs`
- 深色主题下样式协调

**状态**：✅ 已完成

---

## 任务 9：前端 MessageBubble — 新增 `thinking_group` 分派

**文件**：`frontend/src/components/chat/MessageBubble.vue`

**执行**：
- 导入 `ThinkingGroup` 组件
- 在 `thinking` 模板块之后新增 `thinking_group` 分支：
  ```html
  <template v-if="message.type === 'thinking_group' && message.thinkingGroup">
    <ThinkingGroup
      :steps="message.thinkingGroup.steps"
      :step-count="message.thinkingGroup.stepCount"
      :total-duration-ms="message.thinkingGroup.totalDurationMs"
    />
  </template>
  ```

**验收标准**：
- 有工具调用的查询完成后，thinking_group 消息渲染为 ThinkingGroup 组件
- 折叠的思考过程位于最终回答上方
- 其他消息类型渲染不受影响

**状态**：✅ 已完成

---

## 任务 10：端到端集成测试

**执行**：
1. 启动后端：`cd backend && uvicorn app.main:app --reload --port 8000`
2. 启动前端：`cd frontend && npm run dev`
3. 功能测试用例：

| # | 测试场景 | 预期结果 |
|---|---------|---------|
| 1 | 发送"查询 users 表结构和所有记录" | 思考过程流式 → 工具调用 → 回答流式 → 思考折叠，总耗时显示 |
| 2 | 发送"你好" | 纯文本回答，无折叠组 |
| 3 | 点击思考过程头部 | 展开显示思考文本和工具步骤，chevron 旋转 |
| 4 | 再次点击 | 折叠，过渡动画流畅 |
| 5 | 流式中途点停止按钮 | 正常取消，已累积内容正确分组 |
| 6 | 切换到历史会话 | 历史消息正常显示，无错误的折叠组 |
| 7 | 发送需要 3+ 轮工具调用的复杂查询 | 多步全部在折叠组内，步数正确 |

4. 运行现有测试确认无回归：
   - `pytest backend/tests/ -v`
   - `cd frontend && npm test`
   - `cd frontend && npx vue-tsc --noEmit`

**验收标准**：所有测试用例通过，现有测试无回归，类型检查无报错

**状态**：✅ 已完成

---

## 任务依赖关系

```
任务1 ──┐
任务2 ──┤
任务3 ──┼──→ 任务6 ──→ 任务7 ──→ 任务8 ──→ 任务9 ──→ 任务10
任务4 ──┤
任务5 ──┘
```

- 任务 1-5 可并行执行（后端 1-3、前端类型 4、SSE 客户端 5）
- 任务 6 依赖 1-5（需要完整的事件类型和回调）
- 任务 7 依赖 6（需要 state 变量和 onStageChange 回调）
- 任务 8 依赖 7（需要 ThinkingGroupData 和 StoreMessage 类型）
- 任务 9 依赖 8
- 任务 10 依赖全部
