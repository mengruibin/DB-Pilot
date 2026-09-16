"""
Agent StateGraph 定义（2026-07 重构：移除了意图分类节点）。

基于 LangGraph 构建 ReAct (Reasoning + Acting) Agent 图。
LLM 自主决定调用哪些工具、以什么顺序调用、何时停止并给出最终回答。

图结构（security-pipeline-north-star-plan 收敛后）：
  agent_node ↔ secure_tools_node（ReAct 循环，三阶段安全流水线内嵌确认）
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
  正式实现写操作确认功能：agent_node 后插入 confirm_node，通过 interrupt() 暂停。
  graph 实例改为模块级单例（get_agent_graph），支持跨请求恢复。

变更说明（2026-08-07）：
  security-pipeline-north-star-plan：删除 confirm_node / route_after_confirm，
  确认逻辑并入 secure_tools_node 的三阶段安全流水线（PRE_CONFIRM → CONFIRM →
  PRE_EXECUTE）。图简化为 agent → tools → agent 两点结构，确认在
  secure_tools_node 内以批量 interrupt 完成。
"""

from __future__ import annotations

import json as _json
import logging
import time
from typing import Any, Literal

import structlog
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
)
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from app.agent.llm_limiter import LLMConcurrencyBusyError, llm_limiter
from app.agent.models import build_chat_model
from app.agent.state import AgentState
from app.config import settings
from app.prompts.agent import build_agent_system_prompt

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

    # 安全保护：safe_tools_node 已强制终止（连续拦截 >= 4 次），跳过 LLM 调用
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
    # enable_reasoning 来自请求体（前端设置项按请求覆盖），随 checkpoint 持久化；
    # 旧 checkpoint / 未传时为 None → build_chat_model 回落 .env 全局配置
    model = build_chat_model(
        max_tokens=2048,
        timeout=60,
        enable_reasoning=state.get("enable_reasoning"),
    )

    # 抑制 httpx/openai 调试日志（避免全量消息体重复打印）
    for _noisy in ("httpx", "httpx._client", "httpx._config", "httpcore", "openai"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)

    from app.agent.tools.registry import AGENT_TOOLS  # noqa: I001

    # ── 上下文压缩决策（context-compression-plan / token-estimation-plan） ──
    # 新轮判定：ReAct 循环内最后一条恒为 AIMessage/ToolMessage，
    # 最后一条是 HumanMessage 说明是当前轮的首次 LLM 决策。
    messages = list(state.get("messages", []))
    is_new_turn = bool(messages) and isinstance(messages[-1], HumanMessage)
    state_updates: dict[str, Any] = {}
    k, new_last_turn_at, update_turn_meta = _effective_window_k(state, is_new_turn)
    if update_turn_meta:
        state_updates.update({"context_window_k": k, "last_turn_at": new_last_turn_at})

    # 懒触发：压缩已生效 → 恒压缩，不再估算（消息只增不减，跨过阈值永不回落；
    # 且此时 last_actual 反映的是压缩后的小输入，当锚点会低估，见 token-estimation-plan）。
    # 否则用"真实锚定 + 增量"估算本次输入，超过阈值才启动压缩（轻量会话零开销）。
    system_text = _build_system_prompt(state)
    counted_chars = _count_input_chars(system_text, messages)
    compression_active = state.get("history_digest") is not None
    if compression_active:
        need_compress = True
    else:
        last_entry = (state.get("trace_iterations") or [None])[-1]
        last_actual = last_entry.get("input_tokens") if last_entry else None
        last_counted = last_entry.get("counted_chars") if last_entry else None
        last_completion = last_entry.get("output_tokens") if last_entry else None
        est_tokens = _estimate_input_tokens(
            system_text,
            messages,
            last_actual=last_actual,
            last_counted=last_counted,
            last_completion=last_completion,
        )
        # 触发阈值：显式 AGENT_COMPACT_TRIGGER_TOKENS 优先，否则按模型上下文窗口×0.5
        # 自动推导（context-window-plan，见 app/agent/context_window.py）。
        from app.agent.context_window import resolve_compact_trigger

        trigger_tokens = await resolve_compact_trigger()
        need_compress = (
            settings.AGENT_CONTEXT_COMPRESS_ENABLED
            and est_tokens > trigger_tokens
        )
    if need_compress:
        history_msgs, _, _ = _partition_turns(messages, k)
        digest, digest_update = _ensure_digest(state, history_msgs)
        state_updates.update(digest_update)
        # ── 压缩可见性：日志 + trace 元数据（observability） ──
        # cache_hit：digest_update 为空 dict 表示命中缓存（历史 id 未变），非空表示重算
        cache_hit = not digest_update
        digest_chars = len(digest) if digest else 0
        digest_turns = len(digest.split("\n")) if digest else 0
        if compression_active:
            # 已压缩会话：恒压缩短路，不再估算（锚点语义一致性，见 token-estimation-plan）
            logger.debug(
                "上下文压缩保持（历史已压缩，恒压缩）",
                run_id=run_id,
                iteration=iteration,
                digest_chars=digest_chars,
                digest_turns=digest_turns,
                cache_hit=cache_hit,
            )
        else:
            # 首次跨过阈值：懒触发启动压缩（一次性事件，最有审计价值）
            logger.info(
                "上下文压缩触发（首次跨过阈值）",
                run_id=run_id,
                iteration=iteration,
                est_tokens=est_tokens,
                trigger_tokens=trigger_tokens,
                counted_chars=counted_chars,
                msg_count=len(messages),
                digest_chars=digest_chars,
                digest_turns=digest_turns,
                cache_hit=cache_hit,
            )
    else:
        digest = None

    # 压缩可见性元数据：随 trace_entry 持久化到 agent_trace（供会话级审计/过滤）
    compression_info: dict[str, Any] = {"active": need_compress}
    if need_compress:
        compression_info.update(
            {
                "first_trigger": not compression_active,
                "digest_chars": digest_chars,
                "digest_turns": digest_turns,
                "cache_hit": cache_hit,
            }
        )

    # ── 构建消息列表：压缩路径为 摘要 + 近K轮 + 当前轮，否则全量 ──
    llm_messages = _build_llm_messages(state, history_digest=digest, window_k=k)

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
        # 全局 LLM 并发限流（llm-concurrency-limit-plan）：排队等待并发槽位，
        # 超时抛 LLMConcurrencyBusy → 转为「AI 服务繁忙」友好降级，而非无限挂起。
        # 全后端唯一 LLM 调用点，此处限流即覆盖全部 LLM 流量。
        async with llm_limiter.slot():
            response = await model_with_tools.ainvoke(llm_messages)
    except LLMConcurrencyBusyError:
        logger.warning(
            "LLM 并发饱和，排队超时",
            run_id=run_id,
            iteration=iteration,
            wait_timeout_seconds=llm_limiter.wait_timeout,
            max_concurrent=llm_limiter.max_concurrent,
        )
        sse_events = list(state.get("sse_events", []))
        sse_events.append(
            {
                "type": "error",
                "error_code": "LLM_BUSY",
                "user_message": "当前同时处理的任务较多，AI 服务繁忙，请稍后再试",
                "severity": "warning",
            }
        )
        return {
            "final_answer": (
                "当前同时处理的任务较多，AI 服务繁忙。"
                "请稍等片刻后重新发送消息。"
            ),
            "is_complete": True,
            "trace_iterations": state.get("trace_iterations", [])
            + [
                {
                    "iteration": iteration,
                    "status": "llm_concurrency_busy",
                    "wait_timeout_seconds": llm_limiter.wait_timeout,
                    "max_concurrent": llm_limiter.max_concurrent,
                    "model": settings.LLM_MODEL,
                    "provider": settings.LLM_PROVIDER,
                }
            ],
            "sse_events": sse_events,
        }
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
        # token-estimation-plan：记录本次输入计数字符，供下轮"真实锚定 + 增量"估算使用
        "counted_chars": counted_chars,
        # context-compression-plan：压缩可见性（active / first_trigger / digest_chars / cache_hit）
        "compression": compression_info,
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
            **state_updates,
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
            **state_updates,
        }


# =============================================================================
# 构建 StateGraph
# =============================================================================


def build_agent_graph(
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """构建 Agent 状态图（2026-07 重构：移除了意图分类 + format_response）。

    图结构（security-pipeline-north-star-plan 收敛后）：
      agent → route_after_agent（条件边）
        ├── "tools" → secure_tools_node（三阶段安全流水线：PRE_CONFIRM → CONFIRM
        │                 → PRE_EXECUTE + 执行）→ agent（ReAct 循环，固定边）
        └── "__end__" → END（agent_node 直接设置 is_complete: True）

    外部通过 graph.astream(initial_state, stream_mode=["updates", "messages"]) 执行，
    从 state["sse_events"] 读取 SSE 事件并序列化为 SSE 流。

    AsyncPostgresSaver checkpointer 通过 PostgreSQL 持久化保存 state 快照，
    支持进程重启后恢复中断的图（interrupt/resume 跨重启）。

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

    变更（2026-08-07）：
      security-pipeline-north-star-plan：删除 confirm_node / route_after_confirm，
      确认逻辑并入 secure_tools_node（三阶段安全流水线）。节点集 = {agent, tools}。

    Args:
        checkpointer: 可选的 checkpointer 实例。为 None 时使用 MemorySaver
            （内存态，测试/未初始化场景）。生产环境由 init_checkpointer()
            创建 AsyncPostgresSaver 并重建图。

    Returns:
        编译后的 LangGraph 图实例。
    """
    from langgraph.checkpoint.memory import MemorySaver  # noqa: I001

    from app.agent.tool_node import secure_tools_node  # noqa: I001

    workflow = StateGraph(AgentState)

    # ── 注册节点 ──
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", secure_tools_node)

    # ── 入口：直接进入 Agent ──
    workflow.set_entry_point("agent")

    # ── agent → 条件路由（ReAct 循环决策） ──
    workflow.add_conditional_edges(
        "agent",
        route_after_agent,
        {
            "tools": "tools",  # agent → tools（三阶段安全流水线）
            "__end__": END,
        },
    )

    # ── tools → agent（结果返回，继续决策；全拦/全拒自然回 agent 由 LLM 回应） ──
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


# 工具 schema（bind_tools）的固定开销估算，不计入 messages 但占输入
_TOOL_SCHEMA_TOKEN_EST = 3_000


def _partition_turns(
    messages: list,
    keep_recent_turns: int = 2,
) -> tuple[list, list, list]:
    """按 HumanMessage 切分消息列表：(远古历史, 近 K 轮窗口, 当前轮)。

    当前轮 = 最后一个 HumanMessage 及其后全部消息（逐字保留，当前问题的事实依据）。
    窗口   = 最后一个 HumanMessage 往前数 keep_recent_turns 个 HumanMessage 起的已完成轮次。
    历史   = 窗口之前的更早轮次（将进摘要）。

    按轮次切而非按条数滑动窗口：保住 tool_call/tool_result 配对完整性，
    避免切散消息序列（OpenAI/Anthropic 要求工具调用与结果成对出现）。

    Args:
        messages: LangGraph 消息列表。
        keep_recent_turns: 近轮窗口大小（0 = 不保留任何已完成轮次，全部进历史）。

    Returns:
        (history, window, current) 三个消息列表，无重叠、无遗漏。
    """
    human_idxs = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    if not human_idxs:
        return [], [], list(messages)
    last = human_idxs[-1]
    window_start = human_idxs[max(0, len(human_idxs) - 1 - keep_recent_turns)]
    return (
        list(messages[:window_start]),
        list(messages[window_start:last]),
        list(messages[last:]),
    )


def _fmt_tool_call(tc: ToolCall, max_arg_chars: int) -> str:
    """渲染单次工具调用为 `name(args)`（带标签事件日志范式，context-compression-plan）。

    连接参数（InjectedToolArg）不在 args 里，LLM 写的只有 sql / table_names / 枚举 / 短 ID。
    字符串参数超 max_arg_chars 截断加省略号（实测 SQL avg 115 / max 244，正常不截，兜尾部）；
    列表逐元素截断；其他类型原样。

    Args:
        tc: AIMessage.tool_calls 中的单条调用（含 name / args）。
        max_arg_chars: 单个字符串参数的最大字符数。

    Returns:
        渲染文本：`execute_readonly_sql(sql="SELECT ... ")` 或裸名 `check_connections`。
    """
    name = tc.get("name", "?")
    args = tc.get("args") or {}
    if not args:
        return name

    def _cap(v: object) -> object:
        if isinstance(v, str) and len(v) > max_arg_chars:
            return v[:max_arg_chars] + "…"
        if isinstance(v, list):
            return [_cap(x) for x in v]
        return v

    parts = []
    for k, v in args.items():
        capped = _cap(v)
        if isinstance(capped, str):
            parts.append(f'{k}="{capped}"')
        else:
            parts.append(f"{k}={capped}")
    return f"{name}({', '.join(parts)})"


def _build_history_digest(
    history: list,
    max_turns: int | None = None,
    max_q_chars: int | None = None,
    max_conclusion_chars: int | None = None,
    max_arg_chars: int | None = None,
) -> str:
    """把远古历史压缩为逐轮摘要（抽取式，零 LLM 调用，确定性可单测）。

    每轮保留：用户问题（截断）、按实际调用顺序列出的每次工具调用（含参数有界预览，
    来自 AIMessage.tool_calls）、助手最终结论（最后一个无 tool_calls 的 AIMessage，截断）。
    丢弃全部 ToolMessage 内容（最占上下文的部分）。

    排版对齐业界范式（context-compression-plan §2.3）：
      - 内层带标签事件日志：`用户：… | 工具：name(args), name(args) | 结论：…`
        工具调用不折叠、保留顺序与重复，参数按 _fmt_tool_call 有界预览；
      - 外层 REFERENCE ONLY 框定（头/尾标记）由 _build_llm_messages 负责，本函数只出逐轮行。

    Args:
        history: 远古轮次消息列表（_partition_turns 的返回值之一）。
        max_turns: 摘要最多保留的轮次数（默认读配置 AGENT_DIGEST_MAX_TURNS）。
        max_q_chars: 每轮用户问题最大字符数（默认读配置）。
        max_conclusion_chars: 每轮结论最大字符数（默认读配置）。
        max_arg_chars: 单个工具参数最大字符数（默认读配置 AGENT_DIGEST_MAX_ARG_CHARS）。

    Returns:
        逐行摘要文本：`N. 用户：… | 工具：… | 结论：…`
    """
    if max_turns is None:
        max_turns = settings.AGENT_DIGEST_MAX_TURNS
    if max_q_chars is None:
        max_q_chars = settings.AGENT_DIGEST_MAX_Q_CHARS
    if max_conclusion_chars is None:
        max_conclusion_chars = settings.AGENT_DIGEST_MAX_CONCLUSION_CHARS
    if max_arg_chars is None:
        max_arg_chars = settings.AGENT_DIGEST_MAX_ARG_CHARS

    # 按 HumanMessage 切分为轮次列表
    turns: list[list] = []
    for m in history:
        if isinstance(m, HumanMessage):
            turns.append([m])
        else:
            if not turns:
                turns.append([])
            turns[-1].append(m)
    turns = [t for t in turns if t][-max_turns:]  # 只保留最近 N 轮，最旧丢弃

    lines: list[str] = []
    for i, turn in enumerate(turns, 1):
        # 用户问题（首条 HumanMessage 的 content）
        q = ""
        for m in turn:
            if isinstance(m, HumanMessage):
                q = m.content if isinstance(m.content, str) else _content_preview(m.content)
                break
        q = (q[:max_q_chars] + "…") if len(q) > max_q_chars else q
        # 工具调用（按实际顺序列出每次含参数，不折叠——带标签事件日志范式）
        tools: list[str] = []
        for m in turn:
            if isinstance(m, AIMessage) and m.tool_calls:
                tools.extend(_fmt_tool_call(tc, max_arg_chars) for tc in m.tool_calls)
        # 结论：最后一个无 tool_calls 的 AIMessage（即最终回答）
        conclusion = ""
        for m in reversed(turn):
            if isinstance(m, AIMessage) and not m.tool_calls and m.content:
                conclusion = (
                    m.content if isinstance(m.content, str) else _content_preview(m.content)
                )
                break
        if conclusion and len(conclusion) > max_conclusion_chars:
            conclusion = conclusion[:max_conclusion_chars] + "…"
        if not conclusion:
            # 无最终回答：轮以工具结果/工具调用收尾 → run 被中断，回答未持久化到
            # checkpoint（chat.py 提前断流导致最终 checkpoint 未合并，见 root cause），
            # 用显式标记区分，避免误导为"本就没有结论"。
            last_m = turn[-1] if turn else None
            if isinstance(last_m, ToolMessage) or (
                isinstance(last_m, AIMessage) and getattr(last_m, "tool_calls", None)
            ):
                conclusion = "（未完成，被中断）"
            else:
                conclusion = "（无）"
        lines.append(
            f"{i}. 用户：{q or '（无）'} | 工具：{', '.join(tools) or '无'}"
            f" | 结论：{conclusion}"
        )
    return "\n".join(lines)


def _effective_window_k(
    state: AgentState,
    is_new_turn: bool,
    now: float | None = None,
) -> tuple[int, float | None, bool]:
    """计算本轮生效的近轮窗口 K 与需要持久化的轮次元数据。

    闲置衰减（context-compression-plan）：新轮且距上一轮超过
    AGENT_IDLE_DECAY_MINUTES → K=0（数据已过时 + prompt cache 已过期 + 可重查），
    否则 K=配置值。非新轮沿用 state 中已持久化的 context_window_k。

    Args:
        state: 当前 AgentState。
        is_new_turn: 是否新轮（最后一条消息是 HumanMessage）。
        now: 当前时间戳（测试可注入）。

    Returns:
        (k, new_last_turn_at, should_update)：
          - should_update 仅新轮为 True，调用方据此持久化 context_window_k / last_turn_at
          - 非新轮返回 (state 中 k, None, False)，不更新任何元数据
    """
    if not is_new_turn:
        k = state.get("context_window_k", settings.AGENT_KEEP_RECENT_TURNS)
        return k, None, False
    now = now if now is not None else time.time()
    last_turn_at = state.get("last_turn_at")
    idle_seconds = (now - last_turn_at) if last_turn_at is not None else 0.0
    k = (
        0
        if idle_seconds > settings.AGENT_IDLE_DECAY_MINUTES * 60
        else settings.AGENT_KEEP_RECENT_TURNS
    )
    return k, now, True


def _count_msg_chars(m: Any) -> int:
    """单条消息的"请求级"字符计数（供 token 估算与增量差值复用）。

    覆盖真实进入请求的内容：
      - content 为 str → len
      - content 为 list（多模态块）→ 各块 text 字段长度
      - content 为其他（None 等）→ 0
      - tool_calls 参数 → JSON 序列化长度
      - additional_kwargs（DeepSeek reasoning_content 等）→ 字符串 len / list、dict JSON 长度

    Args:
        m: LangChain 消息（HumanMessage / AIMessage / ToolMessage / ...）。

    Returns:
        该消息进入请求后估算占用的字符数（≥ 0）。
    """
    total = 0
    c = getattr(m, "content", None)
    if isinstance(c, str):
        total += len(c)
    elif isinstance(c, list):
        for block in c:
            if isinstance(block, dict):
                total += len(str(block.get("text", "")))
            else:
                total += len(str(block))
    else:
        total += len(str(c or ""))
    # tool_calls 参数（AIMessage）
    for tc in getattr(m, "tool_calls", None) or []:
        total += len(_json.dumps(tc.get("args", {}), ensure_ascii=False))
    # additional_kwargs（DeepSeek reasoning_content 等）
    for v in (getattr(m, "additional_kwargs", None) or {}).values():
        if isinstance(v, (list, dict)):
            total += len(_json.dumps(v, ensure_ascii=False))
        elif isinstance(v, str):
            total += len(v)
    return total


def _count_input_chars(system_text: str, messages: list) -> int:
    """修正漏计的输入字符总数（system + 全部消息），与 API input_tokens 口径对齐。"""
    return len(system_text) + sum(_count_msg_chars(m) for m in messages)


def _estimate_input_tokens(
    system_text: str,
    messages: list,
    last_actual: int | None = None,
    last_counted: int | None = None,
    last_completion: int | None = None,
) -> int:
    """估算下一次 LLM 调用的输入 token 数（用于懒触发阈值判断）。

    真实锚定 + 增量（token-estimation-plan）：
      est = last_actual（上次真实 input，含当时全部历史）
          + last_completion（上次真实 output，= 上次回答回发历史的 token）
          + max(0, counted - last_counted - completion_chars) × rate
      completion_chars 剔除"上次回答"避免与 last_completion 重复计；
      剩余增量（本轮工具结果 + 用户输入）按真实反推的 token/字符率 rate 折算。

    冷启动（无锚点 / provider 不返回 usage / 旧 checkpoint 无 counted_chars）：
      回退启发式 counted // AGENT_ESTIMATE_CHARS_PER_TOKEN + schema 开销。

    Args:
        system_text: System Prompt 文本。
        messages: 当前完整消息列表。
        last_actual: 上次调用真实 input_tokens（trace_entry["input_tokens"]）。
        last_counted: 上次调用输入计数字符（trace_entry["counted_chars"]）。
        last_completion: 上次调用真实 output_tokens（trace_entry["output_tokens"]）。

    Returns:
        估算的输入 token 数（≥ 1）。
    """
    counted = _count_input_chars(system_text, messages)
    # 有真实锚点：主体用真实值，只对增量做最小化估算
    if (
        last_actual is not None
        and last_counted is not None
        and last_actual > 0
        and last_counted > 0
    ):
        # 上次回答（messages[-2]）字符从增量剔除——已用真实 completion 记账
        completion_chars = _count_msg_chars(messages[-2]) if len(messages) >= 2 else 0
        delta = max(0, counted - last_counted - completion_chars)
        # 真实反推的 token/字符率（扣除固定 schema 开销），下限为启发式率防低估增量
        rate = (last_actual - _TOOL_SCHEMA_TOKEN_EST) / last_counted
        floor_rate = 1.0 / max(1, settings.AGENT_ESTIMATE_CHARS_PER_TOKEN)
        rate = max(floor_rate, rate)
        return max(1, last_actual + (last_completion or 0) + int(delta * rate))
    # 冷启动：启发式
    chars_per_token = max(1, settings.AGENT_ESTIMATE_CHARS_PER_TOKEN)
    return max(1, counted // chars_per_token + _TOOL_SCHEMA_TOKEN_EST)


def _ensure_digest(state: AgentState, history_msgs: list) -> tuple[str | None, dict[str, Any]]:
    """摘要缓存：历史最后一条消息 id 未变则复用，否则重算。

    返回 (摘要文本, 需持久化到 checkpoint 的更新 dict)。摘要为空时返回 (None, {})。
    缓存键为 history_msgs[-1].id：
      - 新轮到来 → 历史增长 → id 变化 → 只重算一次
      - 同一轮 ReAct 迭代内 id 稳定 → 复用缓存，不重复生成
      - interrupt/resume 后 checkpoint 中的摘要与 id 保持有效

    Args:
        state: 当前 AgentState（读取 history_digest / digest_upto_msg_id 缓存）。
        history_msgs: _partition_turns 返回的远古历史消息列表。

    Returns:
        (digest, update_dict)：digest 为 None 表示无历史可摘要。
    """
    if not history_msgs:
        return None, {}
    cached = state.get("history_digest")
    src = state.get("digest_upto_msg_id")
    # 防御：src 为 None（消息无 id 等异常情况）时不命中缓存，宁可不缓存、每轮重算，保证正确性。
    # 真实图流程中消息经 add_messages reducer 都会被分配稳定 id，此分支不会触发。
    if cached is not None and src is not None and src == history_msgs[-1].id:
        return cached, {}
    digest = _build_history_digest(history_msgs)
    return digest, {"history_digest": digest, "digest_upto_msg_id": history_msgs[-1].id}


def _build_system_prompt(state: AgentState) -> str:
    """构建 Agent System Prompt（只含不变内容）。

    角色 / 工作原则 / 连接上下文 / 硬性约束。会话历史一律经
    _build_llm_messages 以 user 角色消息注入（含压缩摘要、DB 兜底历史），
    不写入 system（context-compression-plan）：
      - system 是最强指令通道，放时序性历史会让模型把"过去发生过的工具调用"
        误当成当前必须执行的指令，产生幻觉复现；
      - system 保持逐字节稳定，压缩状态翻转不重写完整文本，可调试、可回滚。

    提示词模板与运行时变量注入见 app/prompts/agent.py 的
    build_agent_system_prompt()（角色/工作原则为静态常量，连接上下文
    与连续拦截警告为运行时变量）。

    Args:
        state: 当前 AgentState。

    Returns:
        完整 system prompt 字符串。
    """
    conn_config: dict[str, Any] = state.get("conn_config") or {}
    return build_agent_system_prompt(
        db_type=conn_config.get("db_type", "未知"),
        database=conn_config.get("database", "未知"),
        host=conn_config.get("host", ""),
        port=conn_config.get("port", ""),
        consecutive_blocks=state.get("consecutive_blocks", 0),
    )


def _build_llm_messages(
    state: AgentState,
    history_digest: str | None = None,
    window_k: int | None = None,
) -> list:
    """构建发送给 LangChain Chat 模型的消息列表（任务 4：标准化重构）。

    使用标准消息类型：
      - SystemMessage: Agent 角色和工作原则（只含不变内容，见 _build_system_prompt）
      - HumanMessage: 压缩摘要（user 通道）与用户提问
      - 历史 messages: state["messages"] 中的 HumanMessage/AIMessage/ToolMessage 序列

    上下文压缩路径（context-compression-plan）：
      - history_digest 非 None → 摘要作为独立 HumanMessage 注入，
        远古轮历史被摘要替代，只发送近 K 轮窗口 + 当前轮逐字消息
      - history_digest 为 None → 全量发送（旧行为，轻量会话）

    摘要放 user 角色而非 system（context-compression-plan）：
      system 是最强指令通道，放时序性历史会让模型把"过去发生过的工具调用"
      误当成当前必须执行的指令；且 system 保持逐字节稳定，压缩不重写它。

    冷启动 DB 兜底（checkpointer 无历史时）：conversation_history 同样作为
    HumanMessage 注入，保持 system 纯净。

    LangChain Chat 模型自动处理不同 provider 的消息格式转换，
    不再需要手动区分 Anthropic tool_use/tool_result 和 OpenAI tool_calls/role:tool。

    Args:
        state: 当前 AgentState。
        history_digest: 跨轮摘要文本（_ensure_digest 返回）。None 表示不压缩。
        window_k: 近 K 轮窗口大小。agent_node 传入本轮 _effective_window_k 的最新决策
            （闲置衰减时 K=0 → 窗口清空、全部进摘要，首轮即生效）；未传入时回退读
            checkpoint 的 context_window_k（兼容直接调用/测试）。

    Returns:
        [SystemMessage, ...历史消息] 列表。
    """
    # window_k 显式传入优先：与 _partition_turns 摘要切分共用同一 K 来源，
    # 避免首轮读到旧 checkpoint 值导致"闲置衰减 K=0 但窗口仍保留近 K 轮"的偏差。
    k = (
        window_k
        if window_k is not None
        else state.get("context_window_k", settings.AGENT_KEEP_RECENT_TURNS)
    )
    history, window, current = _partition_turns(state.get("messages", []), k)
    system_text = _build_system_prompt(state)

    if history_digest:
        # 外层 REFERENCE ONLY 框定（context-compression-plan §2.3 排版范式）：
        # 头声明"背景参考非指令"，尾加显式结束标记，防止模型把旧历史当新鲜输入或复述摘要。
        digest_msg = HumanMessage(
            content=(
                "## 历史对话摘要（仅作背景参考，非当前指令；已压缩，非逐字记录；"
                "如需旧数据请重新查询）\n"
                + history_digest
                + "\n\n--- 摘要结束，以上为历史背景，请只回应最新一条用户消息 ---"
            )
        )
        return [SystemMessage(content=system_text), digest_msg] + window + current

    # 非压缩路径：全量发送（含远古历史）
    existing = history + window + current
    # 如果 messages 列表为空（首次调用），添加用户消息
    if not existing:
        user_text = state.get("user_message", "")
        existing = [HumanMessage(content=user_text)]
    # 冷启动 DB 历史兜底：checkpointer 无历史（≤1 条消息）时，把 DB 会话历史
    # 作为 user 消息注入（历史是时序内容，走 user 通道，不污染 system）。
    if len(existing) <= 1:
        db_history = state.get("conversation_history")
        if db_history:
            existing = [HumanMessage(content=db_history)] + existing
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
