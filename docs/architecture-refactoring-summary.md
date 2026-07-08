# 后端 Agent 架构 LangChain/LangGraph 标准化重构复盘

> **作者**: DB-Pilot 团队 · **日期**: 2026-07-03 · **提交**: `dcc5e31`

---

## 目录

1. [概述](#1-概述)
2. [架构对比：旧架构 vs 新架构](#2-架构对比旧架构-vs-新架构)
3. [为什么需要重构](#3-为什么需要重构)
4. [关键设计决策](#4-关键设计决策)
5. [文件变更清单](#5-文件变更清单)
6. [数据流变化](#6-数据流变化)
7. [关键指标对比](#7-关键指标对比)
8. [经验与教训](#8-经验与教训)

---

## 1. 概述

本次重构的核心目标是将 **"用 LangGraph 的骨架，但所有 LLM 交互全自研"** 的混合架构，全面转为 **LangChain/LangGraph 标准生态**。

### 一句话总结

> **移除 771 行自研 LLM 客户端（`llm_client.py`），替换为 3 个 LangChain 标准文件（`models.py` + `tool_node.py` + 简化版 `registry.py`），净减 ~274 行代码，消除 50% 的自研冗余。**

---

## 2. 架构对比：旧架构 vs 新架构

### 2.1 旧架构（重构前）

```
  ┌─────────────────────────────────────────────────────────────────┐
  │                    旧架构：用其图，弃其链路                       │
  │                                                                  │
  │  用户消息                                                        │
  │     │                                                             │
  │     ▼                                                             │
  │  ┌──────────────────────┐                                         │
  │  │   IntentRouter       │                                         │
  │  │   (规则 + LLMClient) │                                         │
  │  └──────────┬───────────┘                                         │
  │             ▼                                                     │
  │  ┌─────────────────────────────────────────────────────────┐     │
  │  │  agent_node（自研逻辑 ~300 行）                           │     │
  │  │                                                          │     │
  │  │  ┌─────────────────┐   ┌──────────────┐   ┌─────────┐  │     │
  │  │  │_build_agent_msg │──▶│LLMClient.chat│──▶│_parse_  │  │     │
  │  │  │200+行手动区分   │   │(771行自研)   │   │text_tool│  │     │
  │  │  │Anthropic/OpenAI │   │              │   │_calls() │  │     │
  │  │  └─────────────────┘   └──────────────┘   └─────────┘  │     │
  │  │                          ▲                            │     │
  │  │                          │ pending_tool_results        │     │
  │  └──────────────────────────┼─────────────────────────────┘     │
  │            │                │                                    │
  │            │ pending_tool_calls                                  │
  │            ▼                │                                    │
  │  ┌──────────────────────┐  │                                    │
  │  │   tools_node         │──┘                                    │
  │  │   (170行手动for循环)  │                                       │
  │  │   TOOL_REGISTRY.get()│                                        │
  │  │   tool_fn.ainvoke()  │                                        │
  │  └──────────────────────┘                                        │
  │            │                                                     │
  │            ▼                                                     │
  │  ┌──────────────────────┐                                         │
  │  │  format_response     │                                         │
  │  └──────────┬───────────┘                                         │
  │             ▼                                                     │
  │        SSE 事件流                                                  │
  └─────────────────────────────────────────────────────────────────┘

  ⚠️ 核心问题：
     ┌──────────────────────────────────────────────────────┐
     │ 消息构建  →  200行 if/elif 手动区分 provider 格式      │
     │ LLM调用   →  771行自研 HTTP 客户端                    │
     │ Schema    →  手动转换 + 过滤列表                       │
     │ 工具执行  →  手动 for 循环派发                         │
     │ 全部自研  →  无法复用 LangChain 生态                   │
     └──────────────────────────────────────────────────────┘
```

### 2.2 新架构（重构后）

```
  ┌─────────────────────────────────────────────────────────────────┐
  │                  新架构：全链路 LangChain 标准                    │
  │                                                                  │
  │  用户消息                                                        │
  │     │                                                             │
  │     ▼                                                             │
  │  ┌──────────────────────┐                                         │
  │  │   IntentRouter       │                                         │
  │  │   (规则 + classifier)│  ◀── build_classifier_model()           │
  │  └──────────┬───────────┘                                         │
  │             ▼                                                     │
  │  ┌─────────────────────────────────────────────────────────┐     │
  │  │  agent_node（LangChain 标准 ~60 行）                      │     │
  │  │                                                          │     │
  │  │  ┌──────────────────────────────────────┐                │     │
  │  │  │ SystemMessage + HumanMessage + ...   │                │     │
  │  │  │          + AIMessage + ToolMessage   │                │     │
  │  │  └──────────────┬───────────────────────┘                │     │
  │  │                 ▼                                         │     │
  │  │  ┌──────────────────────────────────────┐                │     │
  │  │  │ build_chat_model()                   │                │     │
  │  │  │   .bind_tools(AGENT_TOOLS)           │                │     │
  │  │  │   .ainvoke(llm_messages)             │                │     │
  │  │  └──────────────┬───────────────────────┘                │     │
  │  │                 ▼                                         │     │
  │  │  ┌──────────────────────────────────────┐                │     │
  │  │  │ AIMessage                            │                │     │
  │  │  │   ├─ .content: str  (最终回答)       │                │     │
  │  │  │   └─ .tool_calls: [] (工具调用)      │                │     │
  │  │  └──────────────────────────────────────┘                │     │
  │  └──────────────────┬──────────────────────────────────────┘     │
  │            │                                                    │
  │            │ AIMessage.tool_calls  (标准 ToolCall 列表)          │
  │            ▼                                                    │
  │  ┌─────────────────────────────────────────────────────────┐    │
  │  │  SafeToolNode（任务 5：安全 + 注入 + 脱敏）               │    │
  │  │                                                          │    │
  │  │  ① 连接配置注入 ──── conn_config → tool_args              │    │
  │  │  ② 安全护栏检查 ──── SQL 审计 / 只读检查                  │    │
  │  │  ③ 工具执行 ──────── TOOL_REGISTRY → tool_fn.ainvoke()   │    │
  │  │  ④ 结果脱敏 ──────── 敏感列自动掩码                      │    │
  │  │                                                          │    │
  │  │  输出: ToolMessage 列表                                   │    │
  │  └──────────────────────────┬──────────────────────────────┘    │
  │            │ ToolMessage                                        │
  │            ▼                                                    │
  │  ┌─────────────────────────────────────────────────────────┐    │
  │  │  add_messages reducer（标准）                             │    │
  │  │  自动追加 ToolMessage 到 state["messages"] 列表           │    │
  │  └─────────────────────────────────────────────────────────┘    │
  │                                                                  │
  │  无工具 → agent_node 直接设置 is_complete → END（format_response 已移除）     │
  │                                                                  │
  └─────────────────────────────────────────────────────────────────┘

  🟢 关键改进：
     ┌──────────────────────────────────────────────────────┐
     │ 消息构建  →  LangChain 自动处理 provider 格式差异      │
     │ LLM调用   →  ChatAnthropic / ChatOpenAI 标准模型      │
     │ Schema    →  bind_tools() + InjectedToolArg 自动      │
     │ 工具执行  →  SafeToolNode 标准接口                    │
     │ 消息状态  →  add_messages reducer 自动管理            │
     │ SSE 流    →  stream_mode=["updates","messages"]       │
     │             双通道解耦，token 逐字推送到用户            │
     └──────────────────────────────────────────────────────┘
```

### 2.3 文件级架构演进

```
  重构前（文件依赖）：                         重构后（文件依赖）：

  用户请求                                    用户请求
     │                                           │
     ▼                                           ▼
  ┌──────────────┐ 1053 行                   ┌──────────────┐ 543 行
  │  graph.py    │                           │  graph.py    │
  │              │                           │              │
  │  LLMClient ──┤── 771行 ──┐               │ models.py ◄─┤── 88行 ──┐
  │  registry   ─┤── 200行   │               │ tool_node   ─┤── 270行  │
  │  tools/*.py  │           │               │ registry.py ─┤── 30行   │
  │  router.py   │           ▼               │ router.py    │          │
  │  state.py    │    llm_client.py          │ state.py     │          │
  └──────────────┘     (全部自研)             │              │          ▼
                                              │              │  ChatAnthropic
  (自研 LLM 栈)                               │              │  ChatOpenAI
    ↓ 重构后 ↓                                │              │  (LangChain 标准)
                                              │              │
  (标准 LangChain 栈)                         tools/*.py     │
                                               │  InjectedToolArg
                                               ▼
                                          LangChain 标准库
```

---

## 3. 为什么需要重构

### 3.1 痛点分析

```
┌──────┬──────────────────────┬──────────────────────────────────────┬──────────────────────────────┐
│  #   │        痛点           │             具体表现                  │              影响              │
├──────┼──────────────────────┼──────────────────────────────────────┼──────────────────────────────┤
│  1   │ 每个 provider 都要   │ _build_agent_messages 中 200+ 行的    │ 新增 provider 大量编码；      │
│      │ 手动适配             │ if/elif 区分 anthropic/openai 格式    │ streaming/structured output  │
│      │                      │                                       │ 等新能力无法自动获得          │
├──────┼──────────────────────┼──────────────────────────────────────┼──────────────────────────────┤
│  2   │ 工具 Schema 手动转换  │ _tool_to_anthropic_schema()          │ 每次修改工具签名都要同步      │
│      │                      │ _clean_schema_property()              │ 修改 Schema 转换逻辑          │
│      │                      │ _extract_args_schema() 等自研轮子     │                              │
├──────┼──────────────────────┼──────────────────────────────────────┼──────────────────────────────┤
│  3   │ 工具执行与生态隔离    │ tools_node 用 TOOL_REGISTRY.get()    │ 无法复用 LangGraph 生态      │
│      │                      │ + tool_fn.ainvoke() 手动派发          │ (并行执行、错误处理、中断)    │
├──────┼──────────────────────┼──────────────────────────────────────┼──────────────────────────────┤
│  4   │ 代码膨胀             │ llm_client.py 771行 + graph.py 1053  │ 维护负担重，新人上手难        │
│      │                      │ 行 = 1824行自研代码                    │                              │
├──────┼──────────────────────┼──────────────────────────────────────┼──────────────────────────────┤
│  5   │ 消息格式硬编码        │ 手动构造 Anthropic tool_use/tool_    │ 消息格式与 provider 耦合     │
│      │                      │ result 和 OpenAI tool_calls/role:tool │ 无法切换                     │
└──────┴──────────────────────┴──────────────────────────────────────┴──────────────────────────────┘
```

### 3.2 决策分析

```
┌────────────────────────────────────────────────────────────────────┐
│  为什么不自研 LLM 客户端？                                           │
│                                                                     │
│  ┌─ LangChain 已提供标准封装 ─────────────────────────────────────┐  │
│  │ ChatAnthropic / ChatOpenAI 自动处理消息格式差异                 │  │
│  │ 不再需要 if provider=="anthropic": ... elif "openai": ...       │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌─ LangChain 持续跟进行业能力 ──────────────────────────────────┐  │
│  │ streaming tool calls / structured output / parallel tool calls │  │
│  │ 每次 LLM 新能力发布，LangChain 社区先行适配                    │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌─ 社区生态 > 自研轮子 ────────────────────────────────────────┐  │
│  │ ToolNode / InjectedToolArg / add_messages / astream_events     │  │
│  │ 标准化工具执行、参数注入、状态管理、事件监听                    │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
├────────────────────────────────────────────────────────────────────┤
│  为什么保留自定义 SafeToolNode？（不自研 LLM，但自研安全层）          │
│                                                                     │
│  ┌─ 安全护栏是业务强需求 ────────────────────────────────────────┐  │
│  │ SQL 审计（sqlglot 多方言 AST 解析）                            │  │
│  │ 只读检查（拦截 DROP/ALTER/TRUNCATE/CREATE/GRANT）               │  │
│  │ 敏感列脱敏（password/token/email 自动掩码）                     │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌─ LangGraph 标准 ToolNode 不提供安全中间件 ────────────────────┐  │
│  │ 没有钩子机制在工具执行前/后注入安全检查                          │  │
│  │ 所以 SafeToolNode 是 LangGraph 节点函数，而非 ToolNode 子类     │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌─ 但遵守标准消息契约 ───────────────────────────────────────────┐  │
│  │ 输入：AIMessage.tool_calls                                     │  │
│  │ 输出：ToolMessage 列表                                          │  │
│  │ 完全兼容 LangGraph 消息流                                       │  │
│  └──────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────┘
```

---

## 4. 关键设计决策

### 4.1 连接参数注入：`InjectedToolArg`

**问题**：工具函数需要连接参数（`host`、`port`、`password` 等）来连接数据库，但这些参数不应暴露给 LLM。

```
  旧方案：手动 Schema 过滤列表                        新方案：类型标注驱动
  ──────────────────────────                       ──────────────────────

  _INJECTED_CONN_PARAMS = {                        @tool
      "connection_id",                              async def list_tables(
      "db_type",                                        connection_id:
      "host",                                               Annotated[str, InjectedToolArg],
      "port",                                               #         ↑ LLM 不可见 ↑
      "database",                                       db_type:
      "user",                                                 Annotated[str, InjectedToolArg],
      "password",                                     ):
      "ssl_enabled",                                      ...
  }

  需在每个 Schema 转换函数中引用此列表              bind_tools() 自动从 Schema 中排除
  新增参数时容易遗漏更新                             InjectedToolArg 标注的参数
```

### 4.2 SafeToolNode 执行流水线

```
  AIMessage.tool_calls
         │
         ▼
  ┌─────────────────────────────────────────────────────┐
  │ ① 连接配置注入                                      │
  │    conn_config (host/port/user/password) → tool_args │
  │    LLM 不可见这些参数，SafeToolNode 运行时注入        │
  ├─────────────────────────────────────────────────────┤
  │ ② 安全护栏检查                                      │
  │    run_safety_checks(tool_name, tool_args)           │
  │    ├─ SQL 审计（sqlglot 解析 → 拦截危险操作）         │
  │    ├─ 只读检查（非 SELECT 需管理员确认）              │
  │    └─ 连接限额（限制并发连接数）                     │
  │                                                    │
  │    如果 blocked → 返回 ToolMessage(error)            │
  ├─────────────────────────────────────────────────────┤
  │ ③ 工具执行                                          │
  │    TOOL_REGISTRY[tool_name].ainvoke(tool_args)       │
  │    异常捕获 → ToolMessage(error)                     │
  ├─────────────────────────────────────────────────────┤
  │ ④ 结果脱敏                                          │
  │    _sanitize_sensitive_data(result)                  │
  │    password / token / email / id_card → "***"        │
  ├─────────────────────────────────────────────────────┤
  │ ⑤ SSE 事件生成                                      │
  │    sql 事件 / tool_result 事件 → state["sse_events"] │
  └─────────────────────────────────────────────────────┘
         │
         ▼
  ToolMessage 列表
  (add_messages reducer 自动追加到 state["messages"])
```

### 4.3 引擎层迁移（nl2sql / diagnosis）

```
  旧（依赖 LLMClient）：                        新（依赖 LangChain）：

  from app.engine.llm_client import LLMClient    from app.agent.models import build_chat_model
                                                 from langchain_core.messages import SystemMessage,
  client = LLMClient()                                                      HumanMessage

  resp = await client.chat(                      model = build_chat_model(
      messages=[{"role": "user",                     max_tokens=1024,
                 "content": user_prompt}],            timeout=15,
      system=system_prompt,                      )
      max_tokens=1024,
      timeout=15,                                 response = await model.ainvoke([
  )                                                   SystemMessage(content=system_prompt),
                                                      HumanMessage(content=user_prompt),
  llm_response = resp.text                        ])
                                                  llm_response = response.content

  差异：
    自研 HTTP 客户端       → 标准 Chat 模型
    自定义 messages 格式   → SystemMessage / HumanMessage
    resp.text 属性         → response.content 字符串
    Anthropic/OpenAI 分支   → LangChain 自动处理
```

### 4.4 API 兼容性实测发现

```
  阿里百炼 DashScope compatible-mode 端点
  ─────────────────────────────────────────

  qwen-max：                                   qwen-plus：
  ┌───────────────────────────────┐            ┌───────────────────────────────┐
  │ HTTP 200 OK                   │            │ HTTP 200 OK                   │
  │ {                             │            │ {                             │
  │   "finish_reason": "stop",    │            │   "id": "chatcmpl-xxx",       │
  │   "text": "Hello..."          │  ❌        │   "object": "chat.completion",│
  │ }                             │            │   "created": 1783090909,      │
  │                               │            │   "choices": [               │  ✅
  │ DashScope 原生格式             │            │     {                        │
  │ ChatOpenAI 无法解析            │            │       "message": {           │
  └───────────────────────────────┘            │         "content": "",        │
                                               │         "tool_calls": [...]   │
                                               │       },                      │
                                               │       "finish_reason": "tool_ │
                                               │         calls"                │
                                               │     }                         │
                                               │   ],                          │
                                               │   "usage": {...}              │
                                               │ }                             │
                                               │                               │
                                               │ OpenAI 标准格式                │
                                               │ ChatOpenAI 正确解析            │
                                               └───────────────────────────────┘
```

---

## 5. 文件变更清单

### 5.1 新增文件

| 文件 | 行数 | 职责 |
|------|------|------|
| `backend/app/agent/models.py` | 88 | `build_chat_model()` / `build_classifier_model()` 工厂函数 |
| `backend/app/agent/tool_node.py` | 270 | `safe_tools_node` — 安全工具执行节点，含注入/护栏/脱敏 |
| `docs/refactoring-plan.md` | — | 重构计划文档 |
| `frontend/FRONTEND_CHANGES_v2.md` | — | 前端影响清单 |

### 5.2 删除文件

| 文件 | 行数 | 原因 |
|------|------|------|
| `backend/app/engine/llm_client.py` | **-771** | 被 LangChain Chat 模型全面取代 |

### 5.3 修改文件

| 文件 | 行数变化 | 变更内容 |
|------|---------|---------|
| `backend/app/agent/graph.py` | **-510** | agent_node 标准重写，移除 _build_agent_messages 等死代码 |
| `backend/app/agent/tools/registry.py` | **-170** | 移除手动 Schema 过滤，仅保留工具列表 + 注册表 |
| `backend/app/agent/tools/query.py` | -18 | 为连接参数添加 `InjectedToolArg` 标注 |
| `backend/app/agent/tools/diagnosis.py` | -8 | 同上 |
| `backend/app/agent/tools/health.py` | -4 | 同上 |
| `backend/app/agent/tools/troubleshoot.py` | -10 | 同上 |
| `backend/app/agent/state.py` | +35 | 引入 `add_messages` reducer，移除 pending 字段 |
| `backend/app/agent/router.py` | +10 | IntentRouter 改用 BaseChatModel 替代 LLMClient |
| `backend/app/api/chat.py` | +12 | 适配新 AgentState 字段 |
| `backend/app/api/troubleshoot.py` | +12 | 同上 |
| `backend/app/engine/nl2sql.py` | +5 | 迁移到 `build_chat_model()` + 标准消息 |
| `backend/app/engine/diagnosis.py` | +3 | 同上 |
| `backend/requirements.txt` | +3 | 添加 langchain (0.3+)、langchain-anthropic、openai |
| `backend/AGENTS.md` | +12 | 更新 LangChain 集成规范 |
| `backend/backend-tasks.md` | +17 | 添加 B-32 重构任务 |
| `frontend/src/components/chat/MessageBubble.vue` | +6 | 兼容 tool_call 新格式展示 |

---

## 6. 数据流变化

### 6.1 消息格式流

```
  旧架构（自研格式链）：

  用户消息 (str)
      │
      ▼
  ┌─────────────────────────────────────────┐
  │  _build_agent_messages(state)            │
  │                                          │
  │  if provider == "anthropic":             │
  │      msg = {"role": "user",              │
  │             "content": [...]}            │
  │      # 含 tool_use / tool_result 块       │
  │  elif provider == "openai":              │
  │      msg = {"role": "user",              │
  │             "content": "..."}            │
  │      # 含 role:tool 消息                 │
  └──────────────────┬──────────────────────┘
                     │
                     ▼
  ┌─────────────────────────────────────────┐
  │  LLMClient.chat(messages, system)        │
  │                                          │
  │  → httpx POST (自研 HTTP 客户端)          │
  │  → LLMResponse {text, tool_calls}        │
  │  → _parse_text_tool_calls() 手动解析      │
  └──────────────────┬──────────────────────┘
                     │
                     ▼
  ┌─────────────────────────────────────────┐
  │  自定义 ToolCall / pending_tool_calls     │
  │  → tools_node 手动 for 循环               │
  │  → pending_tool_results（自定义 dict）     │
  └─────────────────────────────────────────┘


  新架构（LangChain 标准链）：

  HumanMessage(content=user_message)
      │
      ▼
  ┌─────────────────────────────────────────┐
  │  [SystemMessage(system_text)]           │
  │  + state["messages"]                    │
  │    (HumanMessage / AIMessage /          │
  │     ToolMessage 通过 add_messages       │
  │     reducer 自动管理)                    │
  └──────────────────┬──────────────────────┘
                     │
                     ▼
  ┌─────────────────────────────────────────┐
  │  model.bind_tools(AGENT_TOOLS)          │
  │       .ainvoke(llm_messages)            │
  │                                          │
  │  → ChatAnthropic / ChatOpenAI            │
  │  → AIMessage {content, tool_calls}       │
  │  → tool_calls 是标准 ToolCall 列表        │
  └──────────────────┬──────────────────────┘
                     │
                     ▼
  ┌─────────────────────────────────────────┐
  │  SafeToolNode                            │
  │  → 输入：AIMessage.tool_calls            │
  │  → 执行：注入→审查→调用→脱敏             │
  │  → 输出：ToolMessage 列表                │
  │  → add_messages 自动追加到状态            │
  └─────────────────────────────────────────┘
```

### 6.2 SSE 事件流

```
  SSE 事件类型（2026-07-08 更新：移除 thinking/result，新增 token）

  ┌───────────────────────────────────────────────────────────┐
  │  事件类型      │  触发时机          │  内容                  │
  ├───────────────────────────────────────────────────────────┤
  │  token        │  LLM 逐 token 输出  │  文本片段（打字机效果）│
  │  tool_call    │  LLM 决定调用工具    │  工具名 + 参数          │
  │  tool_result  │  工具执行完成       │  执行结果摘要          │
  │  sql          │  LLM 生成 SQL       │  SQL 语句              │
  │  error        │  异常发生           │  错误信息              │
  │  done         │  流结束             │  token 消耗统计        │
  └───────────────────────────────────────────────────────────┘
  
  设计原则：面向用户的流式渲染（messages 通道的 token 事件）与
           面向系统的状态记录（updates 通道的 sse_events）解耦。
```

---

## 7. 关键指标对比

```
  ┌────────────────────────────┬────────────┬────────────┬───────────┐
  │          指标              │  重构前     │  重构后     │   变化    │
  ├────────────────────────────┼────────────┼────────────┼───────────┤
  │ llm_client.py 代码量       │  771 行    │   0 行     │  -100%   │
  │                           │  (已删除)   │            │          │
  ├────────────────────────────┼────────────┼────────────┼───────────┤
  │ graph.py 代码量            │  1053 行   │  543 行    │  -48%    │
  ├────────────────────────────┼────────────┼────────────┼───────────┤
  │ registry.py 代码量         │  ~200 行   │  ~30 行    │  -85%    │
  ├────────────────────────────┼────────────┼────────────┼───────────┤
  │ 总自研 LLM 代码量          │  ~1824 行  │  ~550 行   │  -70%    │
  │                           │            │ (仅安全层)  │          │
  ├────────────────────────────┼────────────┼────────────┼───────────┤
  │ 消息格式兼容               │ 手写分支   │ LangChain  │ 零维护   │
  │                           │ 200+ 行    │ 自动处理   │          │
  ├────────────────────────────┼────────────┼────────────┼───────────┤
  │ 工具 Schema 管理           │ 手动转换   │ bind_tools │ 零维护   │
  │                           │ +过滤列表   │ +Injected  │          │
  │                           │            │ ToolArg    │          │
  ├────────────────────────────┼────────────┼────────────┼───────────┤
  │ LLM Provider 切换          │ 需改代码   │ 改配置即可 │ 配置驱动 │
  │                           │            │ (anthropic │          │
  │                           │            │  /openai)  │          │
  ├────────────────────────────┼────────────┼────────────┼───────────┤
  │ 测试通过率                 │    —       │ 22/22 通过 │  100%    │
  └────────────────────────────┴────────────┴────────────┴───────────┘
```

---

## 8. 经验与教训

### 8.1 做得好的

```
  ✅ 渐进式重构
     从 Chat 模型工厂 → AgentState → 工具签名 → agent_node
     → SafeToolNode → 图结构 → SSE → 清理
     每个步骤可独立验证，互不阻塞

  ✅ 保留安全层
     不自研 LLM 客户端，但坚持自研安全护栏
     SafeToolNode 在标准 LangGraph 框架内实现 SQL 审计/脱敏
     不为了标准化牺牲业务安全

  ✅ 测试先行
     重构全过程中 pytest 始终保持绿色
     22 项测试持续通过，无回归
```

### 8.2 踩过的坑

```
  ⚠️ 坑 1：InjectedToolArg 的 Schema 可见性
     tool.get_input_schema() 仍返回全部参数（含注入参数）
     这是因为 args_schema 用于内部参数校验
     bind_tools() 层才会正确排除注入参数
     → 理解了这个设计就不算坑

  ⚠️ 坑 2：阿里百炼 API 响应格式不一致
     qwen-max 在同一 compatible-mode 端点返回 DashScope 原生格式
     qwen-plus 却能正确返回 OpenAI 标准格式
     → agent_node 添加 choices:null 降级处理
     → 文档记录：使用 qwen-plus 而非 qwen-max

  ⚠️ 坑 3：add_messages reducer 列表扁平化
     return {"messages": [msg]} 正确
     return {"messages": [[msg1, msg2]]} 嵌套列表导致类型错误
     → add_messages 的 Annotated 类型会 flatten 外层列表
```

### 8.3 后续建议

```
  🔜 建议 1：迁移到 astream_events()
     当前 SSE 流已使用 stream_mode=["updates","messages"] + sse_events 字段
     实现了双通道解耦。未来可进一步迁到 LangGraph 标准事件监听
     （on_chat_model_stream 等），获取更细粒度的 LLM 推理事件

  🔜 建议 2：SafeToolNode 可继承 ToolNode
     若未来需要中断/审批等人机交互功能
     可改为继承 ToolNode 并覆写 _arun_one() 方法
     这样能复用官方的并行执行和错误处理

  🔜 建议 3：补充单元测试
     当前缺少 SafeToolNode 和 models.py 的单元测试
     建议补充以覆盖安全护栏拦截、脱敏等关键路径
```

---

> **附录**：完整重构计划见 [docs/refactoring-plan.md](refactoring-plan.md)，提交记录见 `git log dcc5e31`。
