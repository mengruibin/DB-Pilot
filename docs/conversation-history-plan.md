# 对话历史功能设计文档

> **版本**: v1.0  
> **创建日期**: 2026-07-05  
> **状态**: 待审批

---

## 1. 概述

### 1.1 背景

当前 DB-Pilot 侧边栏为主导航提供三个静态入口（对话 / 连接 / 报告），但对话页面不支持查看和切换历史会话。用户每次刷新页面或清空对话后，之前的会话内容无法找回。后端已具备完整的 Session/Message 数据模型和基础 API（列表/详情），但前端缺少会话管理 UI 和交互逻辑。

### 1.2 目标

在侧边栏「对话」导航项下方增加**对话历史列表**，用户可以：

- 查看所有历史会话（按最后活跃时间降序）
- 点击某个历史会话，加载其消息并继续对话
- 创建新对话
- 重命名会话标题
- 删除不再需要的会话

### 1.3 用户故事

| 编号 | 故事 | 优先级 |
|------|------|--------|
| US1 | 作为 DBA，我希望在侧边栏看到最近的对话列表，以便快速切换上下文 | P0 |
| US2 | 作为 DBA，我希望点击历史对话后能看到之前的完整聊天记录 | P0 |
| US3 | 作为 DBA，我希望在历史对话中继续提问，新消息能追加到该会话中 | P0 |
| US4 | 作为 DBA，我希望点击「新对话」按钮开始一个全新的会话 | P0 |
| US5 | 作为 DBA，我希望能重命名会话标题，便于后续查找 | P1 |
| US6 | 作为 DBA，我希望能删除不需要的会话 | P1 |

---

## 2. 现状分析

### 2.1 后端已有能力

| 组件 | 文件 | 状态 |
|------|------|------|
| `SessionModel` (ORM) | [backend/app/models/session.py](backend/app/models/session.py) | ✅ 已有，字段齐全 |
| `MessageModel` (ORM) | [backend/app/models/session.py](backend/app/models/session.py) | ✅ 已有，含 FK 级联 |
| `SessionResponse` (Pydantic) | [backend/app/models/schemas.py](backend/app/models/schemas.py) | ✅ 已有 |
| `MessageResponse` (Pydantic) | [backend/app/models/schemas.py](backend/app/models/schemas.py) | ✅ 已有 |
| `GET /api/sessions` | [backend/app/api/chat.py:752](backend/app/api/chat.py#L752) | ✅ 已有（分页、筛选） |
| `GET /api/sessions/{id}/messages` | [backend/app/api/chat.py:816](backend/app/api/chat.py#L816) | ✅ 已有（分页） |
| `POST /api/chat/stream` 支持 `session_id` | [backend/app/api/chat.py](backend/app/api/chat.py) | ✅ 已有（续接会话） |
| `POST /api/chat/cancel` | [backend/app/api/chat.py](backend/app/api/chat.py) | ✅ 已有 |
| `PATCH /api/sessions/{id}` | — | ❌ 缺失 |
| `DELETE /api/sessions/{id}` | — | ❌ 缺失 |

### 2.2 前端已有能力

| 组件 | 文件 | 状态 |
|------|------|------|
| Sidebar 导航组件 | [frontend/src/components/common/Sidebar.vue](frontend/src/components/common/Sidebar.vue) | ✅ 已有（纯导航） |
| chatStore (`sessions` 数组、`currentSessionId`) | [frontend/src/stores/chat.ts](frontend/src/stores/chat.ts) | ⚠️ 字段存在但从未填充 |
| useSSE composable | [frontend/src/composables/useSSE.ts](frontend/src/composables/useSSE.ts) | ✅ 已有 |
| API client (`http.get/post/put/delete`) | [frontend/src/api/client.ts](frontend/src/api/client.ts) | ✅ 已有 |
| Session/Message TypeScript 类型 | [frontend/src/types/chat.ts](frontend/src/types/chat.ts) | ✅ 已有 |
| ChatView / ChatPanel | [frontend/src/views/ChatView.vue](frontend/src/views/ChatView.vue) | ✅ 已有 |

---

## 3. 整体架构设计

### 3.1 数据流

```
┌─────────────────────────────────────────────────────────┐
│  Sidebar                                                │
│  ┌─────────────────┐                                    │
│  │ 主导航           │                                    │
│  │  对话 | 连接 | 报告 │                                  │
│  ├─────────────────┤                                    │
│  │ 对话历史  [+新对话]│  点击会话 → chatStore.switchToSession()
│  │ ┌─────────────┐ │                                    │
│  │ │ 会话 A       │ │  GET /api/sessions/{id}/messages   │
│  │ │ 3分钟前      │ │  → 转换为 StoreMessage[]          │
│  │ │ 12条消息     │ │  → 渲染到 MessageList             │
│  │ ├─────────────┤ │                                    │
│  │ │ 会话 B       │ │                                    │
│  │ │ 昨天        │ │                                    │
│  │ │ 8条消息     │ │                                    │
│  │ └─────────────┘ │                                    │
│  └─────────────────┘                                    │
└─────────────────────────────────────────────────────────┘

发送消息:
  InputArea → ChatPanel.handleSend()
    → chatStore.sendMessage(connectionId, text, mode)
      → POST /api/chat/stream { session_id, ... }
        → 后端创建/续接会话，保存消息
        → SSE done 事件返回 session_id
          → chatStore 更新 currentSessionId
          → chatStore.fetchSessions() 刷新列表
```

### 3.2 会话生命周期

```
[新对话] → currentSessionId = null
    ↓ 用户发送第一条消息
[后端创建会话] → done 事件返回 session_id
    ↓ 用户继续在该会话中发消息
[续接会话] → 每条消息追加到 messages[]，session_id 不变
    ↓ 用户点击另一个历史会话
[切换会话] → 从 API 加载消息，替换 messages[]
    ↓ 30分钟无活动
[自动关闭] → SessionManager 将状态设为 "closed"
    ↓ 用户手动删除
[删除] → sessions[] 中移除，切换到新会话或最近会话
```

---

## 4. 任务清单（后端）

### B1: 新增 PATCH /api/sessions/{id} — 重命名会话

> **状态**: ✅ 已完成

**文件**: [backend/app/api/chat.py](backend/app/api/chat.py)（在 `sessions_router` 中新增）

**描述**: 允许前端重命名会话标题。

**请求**:
```json
PATCH /api/sessions/{session_id}
{ "title": "新的会话标题" }
```

**响应**: `200 OK` → `SessionResponse`

**错误处理**:
- `404`: 会话不存在
- `422`: title 为空或超过 128 字符

**验收标准**:
- [ ] 请求体 `title` 字段必填，1-128 字符
- [ ] 会话不存在时返回 404 + 结构化错误 `{error_code: "NOT_FOUND", user_message: "..."}`
- [ ] 成功时返回更新后的完整 `SessionResponse`
- [ ] 入口/出口日志记录（与现有 `list_sessions` 风格一致）
- [ ] 更新 `last_active_at` 时间戳

---

### B2: 新增 DELETE /api/sessions/{id} — 删除会话

> **状态**: ✅ 已完成

**文件**: [backend/app/api/chat.py](backend/app/api/chat.py)（在 `sessions_router` 中新增）

**描述**: 删除指定会话及其所有关联消息（数据库级联删除）。

**请求**:
```json
DELETE /api/sessions/{session_id}
```

**响应**: `204 No Content`

**错误处理**:
- `404`: 会话不存在

**验收标准**:
- [ ] 会话存在时成功删除，返回 204
- [ ] 会话不存在时返回 404 + 结构化错误
- [ ] 关联消息通过 FK CASCADE 自动删除（无需手动处理）
- [ ] 入口/出口日志记录
- [ ] 如果该会话正在被 SSE 流式处理中，需取消流（调用 `_cancel_session_stream()`）

---

### B3: （可选增强）会话列表增加最后一条消息预览

**文件**: [backend/app/api/chat.py](backend/app/api/chat.py)

**描述**: 在 `GET /api/sessions` 的 `SessionResponse` 中增加 `last_message_preview` 字段，显示最后一条用户消息的前 50 个字符。

**验收标准**:
- [ ] `SessionResponse` 新增可选字段 `last_message_preview: str | None`
- [ ] 列表查询时通过子查询或 JOIN 获取每个会话的最后一条用户消息
- [ ] 预览截断为 50 字符（超出加 `...`）
- [ ] 性能：不产生 N+1 查询问题（使用子查询批量获取）

> **注意**: B3 为可选增强，前端可以通过 `title` 字段（后端已用消息前 30 字符作为标题）满足基本需求。建议先跳过 B3，降低首期复杂度。

---

## 5. 任务清单（前端）

### F1: 创建会话 API 层

> **状态**: ✅ 已完成
> 已新建 `api/session.ts`，包含 getSessions/getSessionMessages/renameSession/deleteSession。
> 同时在 `types/chat.ts` 中补充了 SessionListResponse/MessageListResponse 类型。**文件**: 新建 `frontend/src/api/session.ts`

**描述**: 封装会话相关的 HTTP 请求。

**函数列表**:

```typescript
// GET /api/sessions — 获取会话列表
getSessions(params?: {
  connection_id?: string
  status?: string
  page?: number
  pageSize?: number
}): Promise<SessionListResponse>

// GET /api/sessions/{id}/messages — 获取会话消息
getSessionMessages(sessionId: string, params?: {
  page?: number
  pageSize?: number
}): Promise<MessageListResponse>

// PATCH /api/sessions/{id} — 重命名会话
renameSession(sessionId: string, title: string): Promise<SessionResponse>

// DELETE /api/sessions/{id} — 删除会话
deleteSession(sessionId: string): Promise<void>
```

**验收标准**:
- [ ] 所有函数使用 `http` 单例（与现有 `api/chat.ts` 风格一致）
- [ ] TypeScript 类型完整，利用已有的 `Session`、`Message`、`SessionListResponse`、`MessageListResponse`
- [ ] 参数支持 `pageSize` camelCase 映射（后端序列化别名已处理）

---

### F2: 扩展 chatStore — 会话管理 Actions

> **状态**: ✅ 已完成
> 已添加 fetchSessions/switchToSession/startNewSession/renameSession/deleteSession 五个 actions
> 及 messageResponseToStoreMessage 转换函数。onDone 回调中自动触发会话列表刷新。**文件**: [frontend/src/stores/chat.ts](frontend/src/stores/chat.ts)

**描述**: 在现有 `useChatStore` 中新增会话管理相关的 state 和 actions。

**新增 State**:
```typescript
/** 会话列表加载状态 */
const sessionsLoading = ref(false)
/** 消息历史加载状态 */
const messagesLoading = ref(false)
```

**新增 Actions**:

| Action | 描述 | 实现要点 |
|--------|------|----------|
| `fetchSessions(connectionId?)` | 从 API 加载会话列表 | 调用 `getSessions()`，按 `last_active_at` 降序排列（后端已排序），更新 `sessions` ref |
| `switchToSession(sessionId)` | 切换到指定会话 | 1. 设置 `messagesLoading = true` 2. 调用 `getSessionMessages()` 3. 转换 `MessageResponse[]` → `StoreMessage[]` 4. 替换 `messages` 5. 设置 `currentSessionId` 6. 设置 `messagesLoading = false` |
| `startNewSession()` | 开始新对话 | 清空 `messages`，设置 `currentSessionId = null` |
| `renameSession(id, title)` | 重命名会话 | 调用 `renameSession()` API，更新本地 `sessions` 数组中对应项的 `title` |
| `deleteSession(id)` | 删除会话 | 调用 `deleteSession()` API，从 `sessions` 中移除；如果删除的是当前会话，自动调用 `startNewSession()` 或切换到最近的会话 |

**历史消息 → StoreMessage 转换逻辑**（`switchToSession` 内部）:

```typescript
function messageResponseToStoreMessage(msg: MessageResponse): StoreMessage {
  const base = {
    id: msg.id,
    sessionId: msg.session_id,
    role: msg.role as 'user' | 'assistant',
    content: msg.content,
    createdAt: msg.created_at,
  }
  
  // 根据 message_type 映射到 StoreMessageType
  switch (msg.message_type) {
    case 'sql':
      return { ...base, type: 'sql', sqlContent: msg.sql_generated ?? undefined }
    case 'diagnosis':
      return { ...base, type: 'diagnosis', /* findings 从 content 解析 */ }
    default:
      return { ...base, type: 'text' }
  }
}
```

> **注意**: 历史消息不包含 `thinking`、`tool_call`、`tool_result` 等 ReAct 过程细节（这些只在 SSE 流中实时推送），历史回放仅展示最终结果（text/sql/result）。

**验收标准**:
- [ ] `fetchSessions` 在页面加载、新消息完成（done 事件）、删除会话后自动触发
- [ ] `switchToSession` 在加载消息时显示 loading 状态，ChatPanel 展示骨架屏或 spinner
- [ ] `switchToSession` 加载失败时显示错误提示，不清空当前消息
- [ ] `startNewSession` 正确重置状态，不影响侧边栏会话列表
- [ ] `renameSession` 成功后本地即时更新，失败时回滚
- [ ] `deleteSession` 有二次确认（通过 NaiveUI `NPopconfirm` 或对话框）
- [ ] 删除当前会话后，自动切换到最近会话或新会话

---

### F3: 重构 Sidebar — 增加对话历史区域

> **状态**: ✅ 已完成
> 拆分为 SessionList.vue + SessionItem.vue 两个子组件。
> Sidebar.vue 增加 SessionList 条件渲染（仅在 `/` 路由且有活跃连接时显示）。
> 覆盖加载中（骨架屏）、空列表、错误、正常四种状态。

**文件**: [frontend/src/components/common/Sidebar.vue](frontend/src/components/common/Sidebar.vue)

**描述**: 在导航列表下方增加「对话历史」区块，展示会话列表。

**布局结构**:
```
┌──────────────┐
│  DB-Pilot    │ ← brand（不变）
├──────────────┤
│  对话         │ ← 主导航（不变）
│  连接         │
│  报告         │
├──────────────┤
│ 对话历史  [+] │ ← 新增：标题行 + 新对话按钮
│ ┌──────────┐ │
│ │会话A  ··· │ │ ← 新增：会话列表项
│ │3分钟前    │ │
│ ├──────────┤ │
│ │会话B  ··· │ │
│ │昨天       │ │
│ ├──────────┤ │
│ │会话C  ··· │ │
│ │7月3日    │ │
│ └──────────┘ │
│               │ ← 可滚动区域
├──────────────┤
│  设置         │ ← 底部（不变）
└──────────────┘
```

**组件职责拆分**:

有两种方案可选：

| 方案 | 描述 | 优缺点 |
|------|------|--------|
| **A: 单文件扩展** | 直接在 `Sidebar.vue` 中添加历史列表逻辑 | 简单直接，但文件会变大（当前 178 行） |
| **B: 拆分子组件** | 新建 `SessionList.vue` 组件，在 Sidebar 中引入 | 职责清晰，可复用，文件保持可控 |

**推荐方案 B**，拆分为：
- `Sidebar.vue` — 布局容器（品牌 + 主导航 + SessionList + 设置）
- `SessionList.vue` (新建) — 对话历史列表
- `SessionItem.vue` (新建) — 单个会话条目

**SessionItem 交互设计**:

| 交互 | 触发方式 | 行为 |
|------|----------|------|
| 选中会话 | 单击 | 高亮当前项，调用 `chatStore.switchToSession(id)` |
| 重命名 | 双击标题 / 右键菜单 | 标题变为可编辑 input，回车确认，Esc 取消 |
| 删除 | 悬停出现删除按钮 / 右键菜单 | `NPopconfirm` 二次确认后删除 |
| 新对话 | 点击标题行 `+` 按钮 | 调用 `chatStore.startNewSession()` |

**状态覆盖**:

| 状态 | UI 表现 |
|------|---------|
| **加载中** | 显示 3-5 个骨架屏条目（灰色脉冲动画） |
| **空列表** | 显示 "暂无对话记录" 提示文字 + 引导去发送第一条消息 |
| **错误** | 显示 "加载失败" + 重试按钮 |
| **正常** | 会话列表，当前选中项高亮 |

**验收标准**:
- [ ] 仅在「对话」路由（`/`）下显示对话历史区域，其他路由不显示
- [ ] 点击「新对话」按钮，清空聊天区域，取消历史列表中的选中状态
- [ ] 点击会话条目，切换到该会话并高亮选中态（左侧 3px teal 指示条，与主导航 active 风格一致）
- [ ] 当前活跃会话在列表中有视觉区分（背景色 + 指示条）
- [ ] 会话标题过长时截断 + 省略号（CSS `text-overflow: ellipsis`）
- [ ] 时间显示使用相对时间格式（"刚刚" / "3分钟前" / "昨天" / "7月3日"）
- [ ] 会话条目悬停时显示重命名/删除操作按钮
- [ ] 删除操作有二次确认
- [ ] 空状态/加载中/错误状态均有合理的 UI 反馈
- [ ] 列表高度自适应，超出时滚动（`overflow-y: auto`）
- [ ] 发送新消息后，会话列表自动刷新（`fetchSessions`）

---

### F4: ChatPanel 适配 — 支持历史消息展示

> **状态**: ✅ 已完成
> 面板标题动态显示当前会话名或"新会话"；
> 「清空」改为「新对话」（NPopconfirm 确认后调用 startNewSession）；
> 新增历史消息加载中状态（NSpin + 提示文字）。

**文件**: [frontend/src/components/chat/ChatPanel.vue](frontend/src/components/chat/ChatPanel.vue)

**描述**: ChatPanel 当前通过 SSE 事件实时构建消息列表。需要适配以支持：
1. 从 API 加载的历史消息直接渲染
2. 在历史会话中继续发送消息时，新消息追加到已有列表

**改动点**:

1. **面板标题**: 显示当前会话标题（来自 `chatStore.currentSession?.title`），替代固定的"对话"文字
2. **清空按钮语义调整**: 在历史会话中，"清空"应变为"新对话"（或保留清空但增加新对话入口）
3. **消息混合渲染**: `MessageList` 无需改动——它只接收 `messages` 数组。关键是确保 `switchToSession` 加载的历史消息格式与 SSE 实时消息格式兼容（`StoreMessage` 接口已定义完整）

**验收标准**:
- [ ] 从历史会话加载的消息正确渲染（文本、SQL 块、结果表格）
- [ ] 在历史会话中发送新消息，新消息追加到已有消息列表末尾
- [ ] 切换到新会话后，面板标题恢复默认
- [ ] 加载历史消息时，ChatPanel 显示 loading 状态（不是空白）
- [ ] 空会话（无消息）显示正常的空状态

---

### F5: 时间格式化工具函数

> **状态**: ✅ 已完成
> 已新建 `utils/time.ts`，包含 formatRelativeTime 函数。
> 覆盖 刚刚/分钟前/小时前/天前/月日/年月日 六种格式，含边界处理。**文件**: 新建 `frontend/src/utils/time.ts`（或在现有 utils 中添加）

**描述**: 相对时间格式化，用于侧边栏会话列表的时间显示。

```typescript
/**
 * 格式化为相对时间显示
 * - < 1分钟: "刚刚"
 * - < 1小时: "X分钟前"
 * - < 24小时: "X小时前"
 * - < 7天: "X天前"
 * - 同年: "M月D日"
 * - 跨年: "YYYY年M月D日"
 */
export function formatRelativeTime(isoString: string): string
```

**验收标准**:
- [ ] 覆盖所有时间区间
- [ ] 处理无效/空输入（返回空字符串）
- [ ] 可编写单元测试

---

### F6: 路由及初始化逻辑

> **状态**: ✅ 已完成
> ChatView.vue 中新增 onMounted 时按活跃连接加载会话列表；
> watch activeId 变化自动切换会话列表。**文件**: [frontend/src/router.ts](frontend/src/router.ts)（如存在）、[frontend/src/views/ChatView.vue](frontend/src/views/ChatView.vue)

**描述**: 确保进入对话页面时自动加载会话列表。

**改动点**:
- 在 `ChatView.vue` 的 `onMounted` 中调用 `chatStore.fetchSessions()`
- 如果有活跃连接，按 `connection_id` 过滤会话列表

**验收标准**:
- [ ] 进入对话页面时自动加载会话列表
- [ ] 如果存在活跃连接，只加载该连接的会话
- [ ] 连接切换时重新加载会话列表
- [ ] 刷新页面后会话列表恢复

---

## 6. 文件变更清单

| 操作 | 文件路径 | 说明 |
|------|----------|------|
| ✏️ 修改 | `backend/app/api/chat.py` | 新增 PATCH/DELETE 端点（~40 行） |
| ✏️ 修改 | `backend/app/models/schemas.py` | 可选：SessionResponse 增加 last_message_preview 字段 |
| ✨ 新建 | `frontend/src/api/session.ts` | 会话 API 封装（~50 行） |
| ✏️ 修改 | `frontend/src/stores/chat.ts` | 新增 session 管理 actions（~120 行） |
| ✏️ 修改 | `frontend/src/components/common/Sidebar.vue` | 集成 SessionList 组件（~20 行变更） |
| ✨ 新建 | `frontend/src/components/common/SessionList.vue` | 对话历史列表组件（~150 行） |
| ✨ 新建 | `frontend/src/components/common/SessionItem.vue` | 单条会话条目组件（~100 行） |
| ✏️ 修改 | `frontend/src/components/chat/ChatPanel.vue` | 标题适配 + loading 状态（~20 行变更） |
| ✏️ 修改 | `frontend/src/views/ChatView.vue` | onMounted 初始化会话列表（~10 行变更） |
| ✨ 新建 | `frontend/src/utils/time.ts` | 相对时间格式化工具（~30 行） |

---

## 7. 实施顺序（依赖关系）

```
Phase 1 (后端):
  B1 (PATCH 端点) → B2 (DELETE 端点)
  这两个独立，可并行开发

Phase 2 (前端基础):
  F5 (时间工具) → F1 (API 层) → F2 (Store actions)
  F5 和 F1 可并行

Phase 3 (前端 UI):
  F3 (SessionList + SessionItem 组件) → F3 Sidebar.vue 集成
  F4 (ChatPanel 适配) 可与 F3 并行

Phase 4 (集成):
  F6 (路由初始化)
  端到端联调测试
```

---

## 8. 验收测试场景

| 场景 | 步骤 | 预期结果 |
|------|------|----------|
| **查看历史列表** | 1. 发送几条消息 2. 刷新页面 | 侧边栏显示之前的会话，按时间降序 |
| **继续历史对话** | 1. 点击一个历史会话 2. 发送新消息 | 原消息加载显示，新消息追加到末尾 |
| **新建对话** | 1. 点击 + 按钮 2. 发送消息 | 创建新会话，列表中新增一条 |
| **重命名会话** | 1. 双击会话标题 2. 输入新名称 3. 回车 | 标题即时更新，刷新后保持 |
| **删除会话** | 1. 点击删除 2. 确认 | 会话从列表消失，关联消息被清除 |
| **删除当前会话** | 1. 选中会话 A 2. 删除会话 A | 切换到新对话状态或最近会话 |
| **空状态** | 1. 新用户，从未发过消息 | 侧边栏显示"暂无对话记录" |
| **加载失败** | 1. 断网后进入对话页面 | 显示"加载失败"+ 重试按钮 |
| **流式消息后刷新** | 1. 发送消息等待完成 2. 观察侧边栏 | 会话列表自动更新，标题/时间/消息数正确 |

---

## 9. 风险与注意事项

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 历史消息不含 ReAct 过程细节 | 回看历史时看不到 Agent 推理过程 | 产品上可接受——历史只需结果。未来可扩展 `agent_trace` 渲染 |
| 会话列表数据量大（>1000条） | 侧边栏渲染性能下降 | 使用虚拟滚动（与 MessageList 相同策略），分页加载 |
| 活跃会话正在流式处理中被删除 | 前端状态不一致 | 删除前先取消 SSE 流，后端 B2 任务中处理此边界 |
| Sidebar 宽度 220px 显示会话标题有限 | 长标题显示不全 | 标题生成时限制 30 字符；CSS 截断 + hover tooltip 显示全称 |
| 多标签页同时操作同一会话 | 数据不一致 | 不处理此场景——DB-Pilot 为单用户工具，非协作场景 |
