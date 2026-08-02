"""
Agent StateGraph 定义（2026-07 重构：移除了意图分类节点）。

基于 LangGraph 构建 ReAct (Reasoning + Acting) Agent 图。
LLM 自主决定调用哪些工具、以什么顺序调用、何时停止并给出最终回答。

图结构：
  agent_node → confirm_node → safe_tools_node（ReAct 循环）
        │
        ▼
       END

变更说明（2026-07-04）：
  移除了 classify_node / general_node / route_after_classify，原因：
  - 原 5 分类（QUERY/DIAGNOSIS/TROUBLESHOOT/HEALTH_CHECK/GENERAL）中，
    非 GENERAL 分类全部走同一 agent_node，分类结果不影响 Agent 行为
  - 分类步骤增加了额外的 LLM 调用成本，无业务价值
  - Agent（LLM + bind_tools）自身能根据消息内容自主判断是否调用工具

变更说明（2026-07-08）：
  移除了 format_response_node，原因：
  - token 流（stream_mode="messages"）已负责面向用户的文本输出
  - agent_node 直接设置 is_complete: True 结束图，无需中间节点
  - thinking/result SSE 事件已移除，用户通过 token 通道看到打字机效果

变更说明（2026-07-14）：
  移除了旧版 TODO 注释（interrupt() 占位），正式实现写操作确认功能：
  - agent_node 后插入 confirm_node，检测写 DML 并通过 interrupt() 暂停
  - 用户决策后恢复，拒绝的 tool_calls 替换为 ToolMessage
  - graph 实例改为模块级单例（get_agent_graph），支持跨请求恢复
"""

from __future__ import annotations

import logging
import time
from typing import Any, Literal

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from app.agent.models import build_chat_model
from app.agent.state import AgentState
from app.config import settings

logger = structlog.get_logger(__name__)

# =============================================================================
# 最大 ReAct 迭代次数（安全上限，防止无限循环）
# =============================================================================

_MAX_AGENT_ITERATIONS = 20


# =============================================================================
# 路由判断函数
# =============================================================================


def route_after_agent(
    state: AgentState,
) -> Literal["tools", "__end__"]:
    """Agent 决策后路由：继续调用工具 vs 给出最终回答。

    检查 state["messages"] 最后一条 AIMessage.tool_calls（LangChain 标准模式）：
      - 有 tool_calls → 执行工具（ReAct 循环）
      - 无 tool_calls → 格式化响应
    """
    messages = state.get("messages", [])
    last_msg = messages[-1] if messages else None

    if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
        pending_tools = [tc["name"] for tc in last_msg.tool_calls]
        logger.info(
            "路由: 执行工具（ReAct 循环 - AIMessage.tool_calls）",
            run_id=state.get("run_id", ""),
            tools=pending_tools,
            tools_count=len(pending_tools),
            iteration=len(state.get("trace_iterations", [])),
        )
        return "tools"

    logger.info(
        "路由: 最终响应",
        run_id=state.get("run_id", ""),
        answer_length=len(state.get("final_answer") or ""),
        total_iterations=len(state.get("trace_iterations", [])),
    )
    return "__end__"


def route_after_confirm(
    state: AgentState,
) -> Literal["tools", "agent"]:
    """确认后路由：仍有待执行工具 → tools，全部拒绝 → agent 回应。

    从消息列表末尾向前查找最后一个 AIMessage，检查其 tool_calls 是否非空
    来决定路由目标。

    Args:
        state: 当前 AgentState。

    Returns:
        "tools" — 仍有 tool_calls，继续执行 safe_tools_node。
        "agent" — 无 tool_calls（全部拒绝），由 LLM 回应。
    """
    messages = state.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            if msg.tool_calls:
                return "tools"
            break
    return "agent"


# =============================================================================
# 图节点函数
# =============================================================================


async def agent_node(state: AgentState) -> dict[str, Any]:
    """Step 3: LLM 决策节点——Agent 图的核心（任务 4 重写：LangChain 标准 API）。

    使用 LangChain Chat 模型 + bind_tools() 替代自研 LLMClient：
      - model.bind_tools(tools) → LangChain 自动处理 Schema 转换
      - model.ainvoke(messages) → 返回 AIMessage（含 .tool_calls）
      - SystemMessage 替代字符串 system prompt

    LLM 决定：
      - 调用工具：AIMessage.tool_calls 非空 → 图自动路由到 safe_tools_node
      - 给出回答：AIMessage.content → 图路由到 END（agent_node 设置 is_complete: True）

    Args:
        state: 当前 AgentState，需含 user_message, conn_config, messages。

    Returns:
        包含 messages（AIMessage）和/或 final_answer、sse_events 的更新 dict。
    """
    run_id = state.get("run_id", "")
    iteration = len(state.get("trace_iterations", [])) + 1

    # 安全保护：safe_tools_node 已强制终止（连续拦截 >= 5 次），跳过 LLM 调用
    if state.get("is_complete"):
        logger.info(
            "agent_node 检测到 safe_tools_node 已强制终止，跳过 LLM 调用",
            run_id=run_id,
            consecutive_blocks=state.get("consecutive_blocks", 0),
        )
        return {}

    # 安全上限：迭代次数超过上限时强制终止
    if iteration > _MAX_AGENT_ITERATIONS:
        logger.warning(
            "Agent 达到最大迭代次数，强制终止",
            run_id=run_id,
            max_iterations=_MAX_AGENT_ITERATIONS,
        )
        return {
            "final_answer": (
                "抱歉，当前问题分析步骤较多，已超出我的处理上限。请尝试简化问题或分步提问。"
            ),
            "is_complete": True,
            "trace_iterations": state.get("trace_iterations", [])
            + [
                {
                    "iteration": iteration,
                    "status": "max_iterations_exceeded",
                }
            ],
        }

    logger.info(
        "图节点执行: agent（LLM 决策 - LangChain 标准模式）",
        run_id=run_id,
        iteration=iteration,
    )

    # ── 构建 LangChain Chat 模型 + bind_tools ──
    model = build_chat_model(max_tokens=2048, timeout=60)

    # 抑制 httpx/openai 调试日志（避免全量消息体重复打印）
    for _noisy in ("httpx", "httpx._client", "httpx._config", "httpcore", "openai"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)

    from app.agent.tools.registry import AGENT_TOOLS  # noqa: I001

    # ── 构建消息列表：SystemMessage + 对话历史 ──
    llm_messages = _build_llm_messages(state)

    # ── 记录本轮输入增量（避免重复打印全量历史） ──
    if settings.AGENT_DEBUG:
        existing = state.get("messages", [])
        if existing:
            # iteration > 1：取最后 2 条（上轮 AIMessage + ToolMessage）
            delta = existing[-2:]
            logger.debug(
                "Agent 本轮输入增量",
                run_id=run_id,
                iteration=iteration,
                delta=[_fmt_delta_msg(m) for m in delta],
            )
        else:
            # iteration == 1：首次调用，记录用户消息
            logger.debug(
                "Agent 首次输入",
                run_id=run_id,
                iteration=iteration,
                user_message=state.get("user_message", "")[:200],
            )

    node_start = time.monotonic()

    # SAFETY: 某些 OpenAI 兼容 API（如阿里百炼）可能不完全支持 bind_tools()
    # 的标准工具绑定格式，导致返回 choices: null。
    # 此处尝试标准绑定，失败后降级到无工具绑定模式（纯文本回复）。
    try:
        model_with_tools = model.bind_tools(AGENT_TOOLS)
        response = await model_with_tools.ainvoke(llm_messages)
    except ValueError as exc:
        error_str = str(exc)
        if "null value for 'choices'" in error_str:
            logger.error(
                "LLM API 工具绑定失败：模型不支持标准 function calling",
                run_id=run_id,
                error=error_str[:300],
                provider=settings.LLM_PROVIDER,
                model=settings.LLM_MODEL,
            )
            sse_events = list(state.get("sse_events", []))
            sse_events.append(
                {
                    "type": "error",
                    "error_code": "TOOL_BINDING_FAILED",
                    "user_message": "当前模型不支持工具调用，无法执行数据库操作",
                    "severity": "warning",
                }
            )
            return {
                "final_answer": (
                    f"当前模型（{settings.LLM_MODEL}）不支持工具调用能力，"
                    "无法执行数据库查询、诊断等操作。\n\n"
                    "请切换为支持 function calling 的模型（如 qwen-plus、claude-sonnet 等），"
                    "或联系管理员配置兼容的 LLM 提供商。"
                ),
                "is_complete": True,
                "trace_iterations": state.get("trace_iterations", [])
                + [
                    {
                        "iteration": iteration,
                        "status": "tool_binding_failed",
                        "error": str(exc)[:200],
                        "model": settings.LLM_MODEL,
                        "provider": settings.LLM_PROVIDER,
                    }
                ],
                "sse_events": sse_events,
            }
        raise

    elapsed_ms = int((time.monotonic() - node_start) * 1000)

    # ── 提取 token 用量（多 provider 兼容，始终记录，不依赖 AGENT_DEBUG） ──
    token_usage = _extract_token_usage(response)
    input_tokens = token_usage.get("input_tokens", 0)
    output_tokens = token_usage.get("output_tokens", 0)

    # ── 记录本轮决策轨迹 ──
    trace_entry: dict[str, Any] = {
        "iteration": iteration,
        "duration_ms": elapsed_ms,
        # token 用量记入轨迹顶层：随 agent_trace 持久化，供会话级汇总/审计
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    if settings.AGENT_DEBUG:
        trace_entry["debug"] = {
            "model": settings.LLM_MODEL,
            "system_prompt_length": len(state.get("user_message", "")),
            "messages_count": len(llm_messages),
            "tools_count": len(AGENT_TOOLS),
        }
    trace_iterations = list(state.get("trace_iterations", []))
    sse_events = list(state.get("sse_events", []))

    # AIMessage.tool_calls 是 LangChain 标准 ToolCall 列表
    if response.tool_calls:
        # LLM 决定调用工具
        logger.info(
            "Agent 决策: 调用工具",
            run_id=run_id,
            iteration=iteration,
            tools=[tc["name"] for tc in response.tool_calls],
            duration_ms=elapsed_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        trace_entry["tool_calls"] = [
            {"id": tc["id"], "name": tc["name"], "arguments": tc["args"]}
            for tc in response.tool_calls
        ]
        trace_iterations.append(trace_entry)

        # 写入 SSE 事件到 sse_events（分离于 LLM 消息上下文）
        for tc in response.tool_calls:
            sse_events.append(
                {
                    "type": "tool_call",
                    "tool": tc["name"],
                    "args": tc["args"],
                    "display": f"正在执行 {tc['name']}...",
                    "tool_call_id": tc["id"],  # 用于并行执行时前后端关联
                    "agent_run_id": run_id,
                    "iteration": iteration,
                }
            )

        return {
            "messages": [response],  # add_messages reducer 自动追加 AIMessage
            "trace_iterations": trace_iterations,
            "sse_events": sse_events,
        }
    else:
        # LLM 给出最终回答（无工具调用）
        final_text = (
            response.content
            if isinstance(response.content, str) and response.content.strip()
            else "抱歉，我无法处理您的请求，请换个方式描述问题。"
        )

        logger.info(
            "Agent 决策: 最终回答",
            run_id=run_id,
            iteration=iteration,
            text_length=len(final_text),
            total_iterations=iteration,
            duration_ms=elapsed_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        trace_entry["final_answer"] = True
        trace_iterations.append(trace_entry)

        # 不再发送 thinking SSE 事件：token 流（stream_mode="messages"）
        # 已负责面向用户的逐字文本输出，"thinking" 事件已从协议中移除

        return {
            "messages": [response],  # AIMessage（无 tool_calls）
            "final_answer": final_text,
            "is_complete": True,
            "trace_iterations": trace_iterations,
            "sse_events": sse_events,
        }


# =============================================================================
# 危险操作确认节点（通用化：needs_write_confirmation 即确认，无额外条件）
# =============================================================================

# 连接注入参数集合（confirm_node 阶段尚未注入，过滤以保持 details 干净）
_CONN_INJECTED_PARAMS = frozenset(
    {
        "connection_id",
        "db_type",
        "host",
        "port",
        "database",
        "user",
        "password",
        "ssl_enabled",
        "ssl_ca_cert",
        "user_role",
    }
)


def _build_confirmable_action(tc: Any, extras: dict[str, Any]) -> dict[str, Any]:
    """从工具调用构建通用"需确认操作"记录。

    needs_write_confirmation == True → 无条件确认，
    不再检查 sql 或 is_write_dml()。新增工具只需声明 extras 即可接入确认流程。

    Args:
        tc: LangChain ToolCall 字典（含 id/name/args）。
        extras: 工具声明的 extras 字典。

    Returns:
        {tool_call_id, tool, category, description, details}。
    """
    tool_name = tc["name"]
    raw_args = dict(tc["args"])

    # 过滤连接注入参数（保持 description/details 干净）
    key_args: dict[str, Any] = {k: v for k, v in raw_args.items() if k not in _CONN_INJECTED_PARAMS}

    # 自动生成人类可读描述
    parts: list[str] = []
    for k, v in key_args.items():
        v_str = str(v)
        if len(v_str) > 80:
            v_str = v_str[:77] + "..."
        parts.append(f"{k}={v_str}")
    arg_desc = ", ".join(parts)
    description = tool_name + (f": {arg_desc}" if arg_desc else "")

    return {
        "tool_call_id": tc["id"],
        "tool": tool_name,
        "category": extras.get("confirm_category", "generic"),
        "description": description,
        "details": key_args,
    }


async def confirm_node(state: AgentState) -> dict[str, Any]:
    """通用危险操作确认节点——声明了 needs_write_confirmation 的工具即触发确认。

    图结构位置：agent_node → confirm_node → safe_tools_node

    逻辑：
    1. 读取最后一条 AIMessage.tool_calls
    2. 通过 TOOL_REGISTRY.extras 判断哪些工具声明了 needs_write_confirmation
    3. 声明了该标志的工具 → interrupt() 暂停图，等待用户决策
    4. 用户决策后（interrupt() 返回）：
       - 拒绝的 tool_calls → 从 AIMessage 移除 + 生成 ToolMessage
       - 批准的 tool_calls → 保留（含所有未声明标志的工具）
    5. 无任何工具声明 needs_write_confirmation → 直接透传 return {}

    与旧逻辑的区别：
      - 不再依赖 sql 参数或 is_write_dml() 判断
      - 不再区分"写 SQL"和"非 SQL 危险操作"
      - 新增工具只需声明 needs_write_confirmation 即可接入确认流程

    幂等性保证：
      - 第一次执行：interrupt() 暂停，等待决策
      - 第二次执行（resume）：interrupt() 返回决策值，继续处理
      - 无危险操作时：return {} 直接透传，无副作用

    Returns:
        包含 messages（替换后的 AIMessage + ToolMessage 列表）和 sse_events 的更新 dict。
        无危险操作时返回 {}（无修改）。
    """
    from app.agent.tools.registry import TOOL_REGISTRY  # noqa: I001

    run_id = state.get("run_id", "")
    messages = state.get("messages", [])
    last_msg = messages[-1] if messages else None

    if not isinstance(last_msg, AIMessage) or not last_msg.tool_calls:
        return {}

    tool_calls = last_msg.tool_calls

    # ── 分类：声明了 needs_write_confirmation 的需确认，其余安全 ──
    writes: list[dict[str, Any]] = []
    safe: list[Any] = []
    for tc in tool_calls:
        tool_fn = TOOL_REGISTRY.get(tc["name"])
        extras = getattr(tool_fn, "extras", None) or {}
        if extras.get("needs_write_confirmation"):
            writes.append(_build_confirmable_action(tc, extras))
        else:
            safe.append(tc)

    if not writes:
        logger.info("confirm_node: 无需确认的操作，直接透传", run_id=run_id)
        return {}

    # ====== 有危险操作，需要用户确认 ======
    # ── 审计日志：逐条打印每条操作详情 ──
    logger.warning(
        "【安全审计】检测到危险操作，等待用户确认",
        run_id=run_id,
        write_count=len(writes),
        safe_count=len(safe),
    )
    for w in writes:
        logger.warning(
            "【安全审计】操作详情",
            run_id=run_id,
            tool_call_id=w["tool_call_id"],
            tool=w["tool"],
            category=w["category"],
            description=w["description"],
        )
    # 控制台打印（运维审计追踪）
    print(f"\n{'=' * 60}")
    print(f"[SECURITY AUDIT] 检测到 {len(writes)} 条危险操作 | 同时携带 {len(safe)} 条安全工具")
    for w in writes:
        print(f"  ├─ [{w['tool']}]({w['category']}) {w['description'][:120]}")
    if safe:
        print(f"  └─ 安全工具 {len(safe)} 个: {[s['name'] for s in safe]}")
    else:
        print(f"  └─ 无安全工具")
    print(f"{'=' * 60}\n")

    # interrupt() 第一次执行：暂停图，将 payload 返回给调用方
    # interrupt() 第二次执行（resume）：返回 Command(resume=...) 中的值
    decision = interrupt(
        {
            "type": "confirm_required",
            "writes": writes,
            "safe_tool_count": len(safe),
            "session_id": state.get("session_id", ""),  # 供前端恢复请求时使用
        }
    )

    # ====== 处理用户决策（interrupt() 恢复后执行） ======
    approved_ids: set[str] = set(decision.get("approved_tool_call_ids", []))
    denied_ids: set[str] = set(decision.get("denied_tool_call_ids", []))

    # ── 审计日志：用户决策结果 ──
    logger.warning(
        "【安全审计】用户决策已处理",
        run_id=run_id,
        approved_count=len(approved_ids),
        denied_count=len(denied_ids),
    )
    for w in writes:
        tool_call_id = w["tool_call_id"]
        if tool_call_id in approved_ids:
            logger.warning(
                "【安全审计】操作已批准",
                run_id=run_id,
                tool_call_id=tool_call_id,
                tool=w["tool"],
                description=w["description"],
            )
        elif tool_call_id in denied_ids:
            logger.warning(
                "【安全审计】操作已拒绝",
                run_id=run_id,
                tool_call_id=tool_call_id,
                tool=w["tool"],
                description=w["description"],
            )
    # 控制台打印
    print(f"\n{'=' * 60}")
    print(f"[SECURITY AUDIT] 用户决策结果: 批准 {len(approved_ids)} / 拒绝 {len(denied_ids)}")
    for w in writes:
        tid = w["tool_call_id"]
        status = (
            "✅ 已批准"
            if tid in approved_ids
            else ("❌ 已拒绝" if tid in denied_ids else "⏭️ 未处理")
        )
        print(f"  {status} [{w['tool']}]({w['category']}) {w['description'][:120]}")
    print(f"{'=' * 60}\n")

    # 保留批准的写 + 所有安全工具
    kept_calls = [tc for tc in tool_calls if tc["id"] in approved_ids or tc in safe]

    # 为被拒绝的工具生成 ToolMessage 和 SSE 事件
    denied_msgs: list[ToolMessage] = []
    sse_events: list[dict] = []
    iteration = len(state.get("trace_iterations", []))

    for tc in tool_calls:
        if tc["id"] in denied_ids:
            # 从 writes 中查找对应的描述
            desc = next(
                (w["description"] for w in writes if w["tool_call_id"] == tc["id"]),
                tc["name"],
            )
            denied_msgs.append(
                ToolMessage(
                    content=f"操作已被用户取消: {desc}",
                    tool_call_id=tc["id"],
                    name=tc["name"],
                )
            )
            sse_events.append(
                {
                    "type": "tool_result",
                    "tool": tc["name"],
                    "summary": "用户取消了操作",
                    "tool_call_id": tc["id"],
                    "agent_run_id": run_id,
                    "iteration": iteration,
                    "safety_checks_passed": False,
                }
            )

    # 替换原始 AIMessage（相同 id → add_messages reducer 进行替换而非追加）
    modified_aimsg = AIMessage(
        content=last_msg.content or "",
        tool_calls=kept_calls,
        id=last_msg.id,
    )

    # trace 记录
    trace_iterations = list(state.get("trace_iterations", []))
    trace_iterations.append(
        {
            "iteration": len(trace_iterations) + 1,
            "node": "confirm",
            "writes_detected": len(writes),
            "approved": len(approved_ids),
            "denied": len(denied_ids),
        }
    )

    return {
        "messages": [modified_aimsg] + denied_msgs,
        "sse_events": sse_events,
        "trace_iterations": trace_iterations,
    }


# =============================================================================
# 构建 StateGraph
# =============================================================================


def build_agent_graph(
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """构建 Agent 状态图（2026-07 重构：移除了意图分类 + format_response）。

    图结构：
      agent → route_after_agent（条件边）
        ├── "tools" → confirm_node（写操作确认）
        │     ├── "tools" → safe_tools_node → agent（ReAct 循环）
        │     └── "agent" → agent（全部拒绝后 LLM 回应）
        └── "__end__" → END（agent_node 直接设置 is_complete: True）

    外部通过 graph.astream(initial_state, stream_mode=["updates", "messages"]) 执行，
    从 state["sse_events"] 读取 SSE 事件并序列化为 SSE 流。

    AsyncPostgresSaver checkpointer 通过 PostgreSQL 持久化保存 state 快照，
    支持进程重启后恢复中断的图（confirm/resume 跨重启）。

    变更（2026-07-04）：
      移除了 classify_node（原本在入口之前做 5 分类），
      移除了 general_node（原本处理 GENERAL 快速路径），
      移除了 IntentRouter（LLM 分类调用）。
      Agent 入口直接为 agent_node，LLM 自主判断是否调用工具。

    变更（2026-07-08）：
      移除了 format_response_node（thinking/result SSE 事件已废弃），
      agent_node 在最终回答和超上限时直接设置 is_complete: True，
      图编译时传入 MemorySaver checkpointer 用于状态快照。

    变更（2026-07-14）：
      新增 confirm_node（写操作确认），位于 agent_node 和 safe_tools_node 之间。
      agent → route → confirm → route → tools/agent

    变更（2026-07-31）：
      MemorySaver → AsyncRedisSaver → AsyncPostgresSaver
      （checkpointer-redis-migration-plan）。最终选择 PostgreSQL 持久化，
      社区 Redis 包依赖 RedisJSON/RediSearch 模块，普通 Redis 无法运行。

    Args:
        checkpointer: 可选的 checkpointer 实例。为 None 时使用 MemorySaver
            （内存态，测试/未初始化场景）。生产环境由 init_checkpointer()
            创建 AsyncPostgresSaver 并重建图。

    Returns:
        编译后的 LangGraph 图实例。
    """
    from langgraph.checkpoint.memory import MemorySaver  # noqa: I001

    from app.agent.tool_node import safe_tools_node  # noqa: I001

    workflow = StateGraph(AgentState)

    # ── 注册节点 ──
    workflow.add_node("agent", agent_node)
    workflow.add_node("confirm", confirm_node)
    workflow.add_node("tools", safe_tools_node)

    # ── 入口：直接进入 Agent ──
    workflow.set_entry_point("agent")

    # ── agent → 条件路由（ReAct 循环决策） ──
    workflow.add_conditional_edges(
        "agent",
        route_after_agent,
        {
            "tools": "confirm",  # agent → confirm（写操作检查）
            "__end__": END,
        },
    )

    # ── confirm → tools 或 agent ──
    workflow.add_conditional_edges(
        "confirm",
        route_after_confirm,
        {
            "tools": "tools",  # 有工具 → 执行
            "agent": "agent",  # 全部拒绝 → LLM 回应
        },
    )

    # ── tools → agent（结果返回，继续决策） ──
    workflow.add_edge("tools", "agent")

    # ── 编译图（AsyncRedisSaver 持久化 checkpoint 到 Redis） ──
    if checkpointer is None:
        # 默认使用 MemorySaver（内存态）：
        # - 测试场景不需要外部依赖
        # - 生产环境由 init_checkpointer() 创建 AsyncPostgresSaver 并重建图
        checkpointer = MemorySaver()
    return workflow.compile(checkpointer=checkpointer)


# =============================================================================
# 模块级图实例单例（含 AsyncPostgresSaver checkpointer）
# =============================================================================

_agent_graph: CompiledStateGraph | None = None
_checkpointer: AsyncPostgresSaver | None = None
# 泛型参数化必须为 AsyncConnection[DictRow]（与 connection_class 一致），
# 否则 AsyncPostgresSaver.__init__ 类型不兼容（Conn 要求 DictRow）
_pool: AsyncConnectionPool[AsyncConnection[DictRow]] | None = None


def get_agent_graph() -> CompiledStateGraph:
    """返回模块级单例 Agent 图（含 PG checkpointer）。

    必须复用同一个实例才能跨 HTTP 请求恢复 interrupt() 暂停的图。
    AsyncPostgresSaver 通过 PostgreSQL 持久化 checkpoint，支持进程重启后恢复。

    Returns:
        编译后的 LangGraph 图实例（模块级缓存）。
    """
    global _agent_graph
    if _agent_graph is None:
        # 若 init_checkpointer() 尚未执行（如测试环境），退化为 MemorySaver
        _agent_graph = build_agent_graph(_checkpointer)
    return _agent_graph


def get_checkpointer() -> AsyncPostgresSaver | None:
    """获取模块级 AsyncPostgresSaver 实例。"""
    return _checkpointer


async def init_checkpointer() -> None:
    """初始化 PG checkpointer（checkpointer-redis-migration-plan Task 5）。

    在 FastAPI 启动时调用：
      1. 创建 psycopg AsyncConnectionPool
      2. 构建 AsyncPostgresSaver 并 setup()（建表）
      3. 用 PG saver 重建 Agent 图
    """
    global _agent_graph, _checkpointer, _pool
    if _pool is not None:
        return  # 已初始化

    if not settings.CHECKPOINT_DB_URL:
        logger.warning("CHECKPOINT_DB_URL 未配置，checkpoint 使用 MemorySaver（重启丢失）")
        return

    try:
        _pool = AsyncConnectionPool(
            settings.CHECKPOINT_DB_URL,
            open=False,
            timeout=30,
            max_size=10,
            # 显式参数化泛型为 AsyncConnection[DictRow]：
            # AsyncPostgresSaver.__init__ 期望 Conn = AsyncConnection[DictRow]，
            # 且内部 _cursor 按列名访问行。仅靠 kwargs["row_factory"] 不改
            # 泛型推断（默认 TupleRow），必须用 connection_class 绑定。
            connection_class=AsyncConnection[DictRow],
            kwargs={
                # autocommit 必须开启：LangGraph 迁移建索引用 CREATE INDEX CONCURRENTLY，
                # 该语句不能在事务块内执行（checkpointer-redis-migration-plan）
                "autocommit": True,
                # 关闭服务端预处理语句缓存，与官方 from_conn_string 配置一致
                "prepare_threshold": 0,
                # 行工厂 dict_row，与 connection_class 保持一致
                "row_factory": dict_row,
            },
        )
        await _pool.open()
        _checkpointer = AsyncPostgresSaver(_pool)
        await _checkpointer.setup()  # 建表 + 迁移（幂等）
        # 用 PG saver 重建图
        _agent_graph = build_agent_graph(_checkpointer)
        logger.info(
            "PG checkpointer 初始化完成",
            db_url=settings.CHECKPOINT_DB_URL.split("@")[-1],
        )
    except Exception as exc:
        logger.error("PG checkpointer 初始化失败，降级为 MemorySaver", error=str(exc))
        if _pool is not None:
            await _pool.close()
            _pool = None
        _checkpointer = None


async def close_checkpointer() -> None:
    """关闭 PG 连接池（checkpointer-redis-migration-plan Task 5）。

    应在 FastAPI shutdown 事件中调用以释放 PostgreSQL 连接。
    """
    global _agent_graph, _checkpointer, _pool
    if _pool is not None:
        await _pool.close()
    _pool = None
    _checkpointer = None
    _agent_graph = None


# =============================================================================
# 内部辅助函数
# =============================================================================


def _build_system_prompt(state: AgentState) -> str:
    """构建 Agent System Prompt。

    注入可用工具列表、当前数据库连接上下文和对话历史。

    Args:
        state: 当前 AgentState。

    Returns:
        完整 system prompt 字符串。
    """
    conn_config: dict[str, Any] = state.get("conn_config") or {}
    db_type = conn_config.get("db_type", "未知")
    database = conn_config.get("database", "未知")
    host = conn_config.get("host", "")
    port = conn_config.get("port", "")
    # 如果 checkpointer 已有完整历史（messages 列表 > 1），
    # 说明 LLM 能从结构化消息列表中获取完整上下文，无需 DB 文本注入。
    # DB conversation_history 仅作为冷启动 fallback（全新会话 / 迁移后首次）。
    # checkpointer-redis-migration-plan Task 4
    existing_messages = state.get("messages", [])
    if len(existing_messages) > 1:
        conversation_history = "（已有完整对话上下文，详见消息历史）"
    else:
        conversation_history = state.get("conversation_history") or "（无历史）"

    # ── 连续拦截警告：当 execute_readonly_sql 反复被 EXPLAIN 安全评估拦截时提醒 LLM ──
    consecutive_blocks = state.get("consecutive_blocks", 0)
    consecutive_block_warning = ""
    if consecutive_blocks >= 2:
        consecutive_block_warning = (
            f"\n\n## ⚠️ 重要警告：你已连续 {consecutive_blocks} 次因数据量过大被 EXPLAIN 安全评估拦截\n"
            "这不是 SQL 写法问题，而是查询本身需要处理的数据量超过安全阈值。\n"
            "**请不要再调用 execute_readonly_sql 工具**。\n"
            "改为直接向用户说明：该查询预估扫描数据量过大，无法在当前安全限制下执行，"
            "并建议用户缩小查询范围（添加 WHERE 条件、使用 LIMIT、聚合查询）"
            "或在数据库客户端中手动执行。\n"
            "如果刚刚收到的工具返回消息已包含具体的 EXPLAIN 评估详情和 SQL 语句，"
            "请将其直接转述给用户，不需要再次调用任何数据库工具。"
        )

    return (
        "你是 DB-Pilot，一个专业的数据库运维 AI 助手。\n\n"
        "## 当前连接上下文\n"
        f"- 数据库类型: {db_type}\n"
        f"- 数据库名: {database}\n"
        f"- 连接地址: {host}:{port}\n\n"
        "## 工作原则\n"
        "1. 先理解用户问题 → 选择最合适的工具 → 观察结果 → 决定下一步\n"
        "2. 用最少工具完成目标，不要无意义地遍历所有表\n"
        "3. 执行 SQL 查询前必须先使用 describe_table 了解相关表的结构和索引信息。"
        "   如果涉及多张表，请将表名一次性传入 table_names 参数批量查询，减少工具调用次数\n"
        "4. 对于复杂查询或数据量大的表，考虑使用 explain_query 检查执行计划\n"
        "5. 如果 EXPLAIN 结果或工具返回的性能提示显示全表扫描、文件排序、临时表等问题，"
        "应改写 SQL 或建议优化方案\n"
        "6. 每次工具调用后分析结果，根据结果决定是否需要更多信息\n"
        "7. **并行执行提示**: 当需要调用多个相互独立的工具时"
        "（如同时查询多张表的基本信息、同时检查锁和连接状态、"
        "同时分析多个慢查询），请将所有工具调用一次性返回。"
        "系统会并行执行它们，显著提升整体响应速度。\n"
        "8. 最终用中文给出清晰完整的总结回答\n"
        "9. 如果工具返回错误，分析原因并尝试换一种方式解决\n"
        "10. 如果用户要求插入、更新或删除数据，使用 execute_write_sql 工具执行写操作。"
        "写操作执行前系统会请求用户确认，如被用户拒绝请告知用户操作已取消。"
        "只读查询（SELECT / SHOW / DESCRIBE / EXPLAIN）请使用 execute_readonly_sql 工具。"
        f"{consecutive_block_warning}\n"
        "## 对话历史\n"
        f"{conversation_history}"
    )


def _build_llm_messages(state: AgentState) -> list:
    """构建发送给 LangChain Chat 模型的消息列表（任务 4：标准化重构）。

    使用标准消息类型：
      - SystemMessage: Agent 角色和工作原则
      - 历史 messages: state["messages"] 中的 HumanMessage/AIMessage/ToolMessage 序列

    LangChain Chat 模型自动处理不同 provider 的消息格式转换，
    不再需要手动区分 Anthropic tool_use/tool_result 和 OpenAI tool_calls/role:tool。

    Args:
        state: 当前 AgentState。

    Returns:
        [SystemMessage, ...历史消息] 列表。
    """
    system_text = _build_system_prompt(state)

    # 如果 messages 列表为空（首次调用），添加用户消息
    existing = list(state.get("messages", []))
    if not existing:
        user_text = state.get("user_message", "")
        existing = [HumanMessage(content=user_text)]

    return [SystemMessage(content=system_text)] + existing


def _extract_token_usage(response: Any) -> dict[str, int]:
    """从 LLM 响应中提取 token 用量，兼容多 provider 的 metadata 格式。

    兼容格式（按优先级）：
      1. response.usage_metadata       — LangChain 新版的标准化字段
      2. response_metadata.token_usage — OpenAI 系（含 DeepSeek/GLM 等兼容 API）
      3. response_metadata.usage       — Anthropic 系

    提取不到时返回全 0，不抛异常（部分 provider 不返回用量信息）。
    """
    # 1. LangChain 新版标准化字段
    usage_metadata = getattr(response, "usage_metadata", None)
    if isinstance(usage_metadata, dict) and usage_metadata:
        return {
            "input_tokens": int(usage_metadata.get("input_tokens") or 0),
            "output_tokens": int(usage_metadata.get("output_tokens") or 0),
        }

    # 2/3. provider 特定 metadata
    metadata = getattr(response, "response_metadata", None) or {}
    usage = metadata.get("token_usage") or metadata.get("usage")
    if isinstance(usage, dict):
        return {
            "input_tokens": int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
            "output_tokens": int(
                usage.get("output_tokens") or usage.get("completion_tokens") or 0
            ),
        }
    return {"input_tokens": 0, "output_tokens": 0}


def _content_preview(content: str | list | dict | None, max_len: int = 200) -> str:
    """安全提取消息内容的字符串预览。

    LangChain 消息的 content 类型为 str | list[str | dict]，在 Python 层面
    str + "..." 在 list 类型时运行时会报错，因此统一转为 str 再截取。
    """
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        # 拼接文本块，处理多模态内容列表
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(item.get("text", str(item)))
        text = " ".join(parts) if parts else str(content)
    else:
        text = str(content or "")
    return (text[:max_len] + "...") if len(text) > max_len else text


def _fmt_delta_msg(msg: Any) -> dict[str, Any]:
    """格式化增量消息为简洁的日志结构。

    将 LangChain 消息转为可用于 structlog 的字典，
    仅保留关键信息（工具名、参数摘要、结果预览）。
    """
    if isinstance(msg, AIMessage):
        if msg.tool_calls:
            return {
                "role": "assistant",
                "tool_calls": [{"name": tc["name"], "args": tc["args"]} for tc in msg.tool_calls],
            }
        return {
            "role": "assistant",
            "content_preview": _content_preview(msg.content),
        }
    if isinstance(msg, ToolMessage):
        return {
            "role": "tool",
            "tool_call_id": msg.tool_call_id,
            "content_preview": _content_preview(msg.content),
        }
    if isinstance(msg, HumanMessage):
        return {
            "role": "user",
            "content_preview": _content_preview(msg.content),
        }
    if isinstance(msg, SystemMessage):
        return {
            "role": "system",
            "content_preview": _content_preview(msg.content),
        }
    return {"role": type(msg).__name__, "preview": str(msg)[:200]}
