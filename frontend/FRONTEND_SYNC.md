# 意图分类移除 — 前端同步变更

> 日期：2026-07-04
>
> 关联后端变更：[backend/IMPLEMENTATION_PLAN.md](../backend/IMPLEMENTATION_PLAN.md)
>
> 影响级别：**低（纯清理，无功能影响）**

---

## 变更概要

后端移除了意图分类（`classify_node`），Agent 图入口直接为 `agent_node`。对前端的影响很小——分类信息仅通过 SSE `thinking` 事件的 `reasoning_type="classifying"` 字段传递，该字段为可选，前端已做空值兜底。

---

## 受影响的文件

### 1. `src/types/chat.ts` — 注释更新

**位置：** `ThinkingEvent` 接口（约第 96-105 行）

**变更：**
```diff
- /** 推理类型：planning | observing | concluding | classifying | error_recovery（Agent 架构升级后新增） */
+ /** 推理类型：planning | observing | concluding | error_recovery */
  reasoning_type?: string
```

### 2. `src/stores/chat.ts` — 注释更新

**位置 A：** `StoreMessage` 类型中 `reasoningType` 字段注释（约第 55 行）

```diff
- /** 推理类型：planning | observing | concluding | classifying | error_recovery */
+ /** 推理类型：planning | observing | concluding | error_recovery */
  reasoningType?: string
```

**位置 B：** `onThinking` 回调（约第 246-258 行）— **无需代码变更**，`reasoning_type` 为可选字段，后端停止发送后该值为 `undefined`，前端自动降级。

### 3. `src/components/chat/MessageBubble.vue` — 清理遗留代码

**位置 A：** `reasoningTypeLabel` 函数（约第 61-70 行）

```diff
  function reasoningTypeLabel(type: string): string {
    const labels: Record<string, string> = {
-     classifying: '意图分析',
      planning: '规划中',
      observing: '观察中',
      concluding: '总结中',
      error_recovery: '纠错中',
    }
    return labels[type] || type
  }
```

**位置 B：** CSS（约第 378-381 行）— 可选删除

```diff
- .badge-classifying {
-   background: rgba(96, 165, 250, 0.15);
-   color: var(--accent-blue, #60a5fa);
- }
```

> **注意：** 模板中 `v-if="message.reasoningType"` 守卫已确保 reasoningType 为空时不渲染 badge，删除 CSS 只是清理死代码，不影响运行。

### 4. `src/composables/useSSE.ts` — 无需变更

`thinking` 事件类型保留，分类从未作为独立事件类型存在。

---

## 不受影响的文件

| 文件 | 原因 |
|------|------|
| `src/api/chat.ts` | 无分类相关 API |
| `src/api/client.ts` | 无分类相关逻辑 |
| `src/stores/connection.ts` | 无分类相关逻辑 |
| `src/stores/report.ts` | 无分类相关逻辑 |
| `src/types/chat.ts` 中 `MessageType` | `message_type` 为后端接口字段，前端仅做类型定义，不依赖具体值做渲染决策 |
| 所有其他组件 | 无分类相关引用 |

---

## 行为变化

| 变化点 | 变更前 | 变更后 |
|--------|--------|--------|
| 首个 SSE 事件 | `thinking`（classifying，"分析用户意图：XXX"）| `thinking`（concluding，Agent 最终回答）或 `tool_call` |
| "AI 推理过程" 折叠区标签 | 显示"意图分析" badge | 显示"总结中" badge（或无语义标签，取决于 reasoning_type 值） |
| 响应延迟 | classify LLM（~2s）+ Agent LLM（~2s）| 仅 Agent LLM（~2s），**首响应更快** |

---

## 前端验收标准

- [x] `MessageBubble.vue` 中"意图分析"badge 不再出现
- [x] 遗留的 `.badge-classifying` CSS 已清理
- [x] TypeScript 类型检查通过：`npx vue-tsc --noEmit` ✅
- [ ] 发送"你好"后聊天 UI 正常展示（Agent 回复文本，无异常报错）
- [ ] 发送 SQL 查询后工具调用流程正常展示
