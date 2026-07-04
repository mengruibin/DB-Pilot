"""
Agent StateGraph 定义（2026-07 重构：移除了意图分类节点）。

基于 LangGraph 构建 ReAct (Reasoning + Acting) Agent 图。
LLM 自主决定调用哪些工具、以什么顺序调用、何时停止并给出最终回答。

图结构：
  agent_node ↔ safe_tools_node（ReAct 循环）
        │
        ▼
  format_response → END

变更说明（2026-07-04）：
  移除了 classify_node / general_node / route_after_classify，原因：
  - 原 5 分类（QUERY/DIAGNOSIS/TROUBLESHOOT/HEALTH_CHECK/GENERAL）中，
    非 GENERAL 分类全部走同一 agent_node，分类结果不影响 Agent 行为
  - 分类步骤增加了额外的 LLM 调用成本，无业务价值
  - Agent（LLM + bind_tools）自身能根据消息内容自主判断是否调用工具

未来扩展（人机交互）：
  safe_tools_node 中检测危险操作 → interrupt() 暂停
    → 前端展示确认对话框 → 用户审批
    → Command(resume=approved) → 继续执行
"""

from __future__ import annotations

import time
from typing import Any, Literal

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.models import build_chat_model
from app.agent.state import AgentState
from app.config import settings

logger = structlog.get_logger(__name__)

# =============================================================================
# 最大 ReAct 迭代次数（安全上限，防止无限循环）
# =============================================================================

_MAX_AGENT_ITERATIONS = 10


# =============================================================================
# 路由判断函数
# =============================================================================

def route_after_agent(
    state: AgentState,
) -> Literal["tools", "format_response"]:
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
    return "format_response"


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
      - 给出回答：AIMessage.content → 图自动路由到 format_response

    Args:
        state: 当前 AgentState，需含 user_message, conn_config, messages。

    Returns:
        包含 messages（AIMessage）和/或 final_answer、sse_events 的更新 dict。
    """
    run_id = state.get("run_id", "")
    iteration = len(state.get("trace_iterations", [])) + 1

    # 安全上限：迭代次数超过上限时强制终止
    if iteration > _MAX_AGENT_ITERATIONS:
        logger.warning(
            "Agent 达到最大迭代次数，强制终止",
            run_id=run_id,
            max_iterations=_MAX_AGENT_ITERATIONS,
        )
        return {
            "final_answer": (
                "抱歉，当前问题分析步骤较多，已超出我的处理上限。"
                "请尝试简化问题或分步提问。"
            ),
            "trace_iterations": state.get("trace_iterations", []) + [{
                "iteration": iteration,
                "status": "max_iterations_exceeded",
            }],
        }

    logger.info(
        "图节点执行: agent（LLM 决策 - LangChain 标准模式）",
        run_id=run_id,
        iteration=iteration,
    )

    # ── 构建 LangChain Chat 模型 + bind_tools ──
    model = build_chat_model(max_tokens=2048, timeout=60)

    from app.agent.tools.registry import AGENT_TOOLS  # noqa: I001

    # ── 构建消息列表：SystemMessage + 对话历史 ──
    llm_messages = _build_llm_messages(state)

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
            sse_events.append({
                "type": "error",
                "error_code": "TOOL_BINDING_FAILED",
                "user_message": "当前模型不支持工具调用，无法执行数据库操作",
                "severity": "warning",
            })
            return {
                "final_answer": (
                    f"当前模型（{settings.LLM_MODEL}）不支持工具调用能力，"
                    "无法执行数据库查询、诊断等操作。\n\n"
                    "请切换为支持 function calling 的模型（如 qwen-plus、claude-sonnet 等），"
                    "或联系管理员配置兼容的 LLM 提供商。"
                ),
                "trace_iterations": state.get("trace_iterations", []) + [{
                    "iteration": iteration,
                    "status": "tool_binding_failed",
                    "error": str(exc)[:200],
                    "model": settings.LLM_MODEL,
                    "provider": settings.LLM_PROVIDER,
                }],
                "sse_events": sse_events,
            }
        raise

    elapsed_ms = int((time.monotonic() - node_start) * 1000)

    # ── 记录本轮决策轨迹 ──
    trace_entry: dict[str, Any] = {
        "iteration": iteration,
        "duration_ms": elapsed_ms,
    }
    if settings.AGENT_DEBUG:
        trace_entry["debug"] = {
            "model": settings.LLM_MODEL,
            "system_prompt_length": len(state.get("user_message", "")),
            "messages_count": len(llm_messages),
            "tools_count": len(AGENT_TOOLS),
            "input_tokens": (
                response.response_metadata.get("token_usage", {}).get("input_tokens", 0)
                if hasattr(response, "response_metadata") else 0
            ),
            "output_tokens": (
                response.response_metadata.get("token_usage", {}).get("output_tokens", 0)
                if hasattr(response, "response_metadata") else 0
            ),
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
        )

        trace_entry["tool_calls"] = [
            {"id": tc["id"], "name": tc["name"], "arguments": tc["args"]}
            for tc in response.tool_calls
        ]
        trace_iterations.append(trace_entry)

        # 写入 SSE 事件到 sse_events（分离于 LLM 消息上下文）
        for tc in response.tool_calls:
            sse_events.append({
                "type": "tool_call",
                "tool": tc["name"],
                "args": tc["args"],
                "display": f"正在执行 {tc['name']}...",
                "agent_run_id": run_id,
                "iteration": iteration,
            })

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
        )

        trace_entry["final_answer"] = True
        trace_iterations.append(trace_entry)

        # 写入 SSE thinking 事件（LLM 最终思考结果）
        sse_events.append({
            "type": "thinking",
            "content": final_text[:500] + ("..." if len(final_text) > 500 else ""),
            "agent_run_id": run_id,
            "iteration": iteration,
            "duration_ms": elapsed_ms,
            "reasoning_type": "concluding",
        })

        return {
            "messages": [response],  # AIMessage（无 tool_calls）
            "final_answer": final_text,
            "trace_iterations": trace_iterations,
            "sse_events": sse_events,
        }



def format_response_node(state: AgentState) -> dict[str, Any]:
    """Step 6: 最终响应格式化节点。

    将 Agent 的最终回答封装为 SSE 兼容的消息格式。
    累积所有消息到 state.messages 列表，
    外部 SSE 流引擎据此发送 done 事件。

    Args:
        state: 当前 AgentState，需含 final_answer。

    Returns:
        包含 messages 和 is_complete 的更新 dict。
    """
    run_id = state.get("run_id", "")
    final_answer = state.get("final_answer") or ""
    # 从 trace_iterations 中统计实际调用的工具数（替代已移除的 pending_tool_results）
    trace_entries = state.get("trace_iterations", [])
    tools_called = sum(
        1 for entry in trace_entries if entry.get("tool_calls")
    )

    logger.info(
        "图节点执行: format_response（格式化响应）",
        run_id=run_id,
        answer_length=len(final_answer),
        tools_called=tools_called,
        total_iterations=len(trace_entries),
    )

    sse_events = list(state.get("sse_events", []))
    sse_events.append({
        "type": "result",
        "summary": final_answer,
        "tool_calls_made": tools_called,
    })

    logger.info(
        "图节点执行完成: format_response（格式化响应）",
        run_id=run_id,
        answer_preview=final_answer[:200] if final_answer else "(空)",
        tools_called=tools_called,
    )

    return {
        "sse_events": sse_events,
        "is_complete": True,
    }


# =============================================================================
# 构建 StateGraph
# =============================================================================


def build_agent_graph() -> CompiledStateGraph:
    """构建 Agent 状态图（2026-07 重构：移除了意图分类）。

    图结构：
      agent → route_after_agent（条件边）
        ├── "tools" → safe_tools_node → agent（ReAct 循环）
        └── "format_response" → format_response_node → END

    外部通过 graph.astream(initial_state, stream_mode="values") 执行，
    从 state["sse_events"] 读取 SSE 事件并序列化为 SSE 流。

    变更（2026-07-04）：
      移除了 classify_node（原本在入口之前做 5 分类），
      移除了 general_node（原本处理 GENERAL 快速路径），
      移除了 IntentRouter（LLM 分类调用）。
      Agent 入口直接为 agent_node，LLM 自主判断是否调用工具。

    Returns:
        编译后的 LangGraph 图，可直接执行。
    """
    from app.agent.tool_node import safe_tools_node  # noqa: I001

    workflow = StateGraph(AgentState)

    # ── 注册节点 ──
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", safe_tools_node)
    workflow.add_node("format_response", format_response_node)

    # ── 入口：直接进入 Agent ──
    workflow.set_entry_point("agent")

    # ── agent → 条件路由（ReAct 循环决策） ──
    workflow.add_conditional_edges(
        "agent",
        route_after_agent,
        {
            "tools": "tools",
            "format_response": "format_response",
        },
    )

    # ── tools → agent（结果返回，继续决策） ──
    workflow.add_edge("tools", "agent")

    # ── format_response → END ──
    workflow.add_edge("format_response", END)

    return workflow.compile()


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
    conversation_history = state.get("conversation_history") or "（无历史）"

    return (
        "你是 DB-Pilot，一个专业的数据库运维 AI 助手。\n\n"
        "## 当前连接上下文\n"
        f"- 数据库类型: {db_type}\n"
        f"- 数据库名: {database}\n"
        f"- 连接地址: {host}:{port}\n\n"
        "## 工作原则\n"
        "1. 先理解用户问题 → 选择最合适的工具 → 观察结果 → 决定下一步\n"
        "2. 用最少工具完成目标，不要无意义地遍历所有表\n"
        "3. 执行 SQL 查询前必须先了解相关表的结构\n"
        "4. 每次工具调用后分析结果，根据结果决定是否需要更多信息\n"
        "5. 最终用中文给出清晰完整的总结回答\n"
        "6. 如果工具返回错误，分析原因并尝试换一种方式解决\n\n"
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
