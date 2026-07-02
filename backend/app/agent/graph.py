"""
Agent StateGraph 定义（B-30 重写）。

基于 LangGraph 构建 ReAct (Reasoning + Acting) Agent 图。
LLM 自主决定调用哪些工具、以什么顺序调用、何时停止并给出最终回答。

图结构：
  classify → route_by_intent（条件边）
    ├── GENERAL / 高置信度快速路径 → general_node → END
    └── 其他 → agent_node
                │
                └── agent_node ↔ tools_node（ReAct 循环）
                      │
                      ▼
                format_response → END

未来扩展（人机交互）：
  tools_node 中检测危险操作 → interrupt() 暂停
    → 前端展示确认对话框 → 用户审批
    → Command(resume=approved) → 继续执行

依据 PRD §4.2 Agent 工作流：
  Step 1: classify → Step 2: route → Step 3-4: agent loop → Step 5-6: format + done
"""

from __future__ import annotations

import time
from typing import Any, Literal

import structlog
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.router import IntentRouter
from app.agent.state import AgentState, Intent
from app.config import settings
from app.engine.llm_client import LLMClient

logger = structlog.get_logger(__name__)

# =============================================================================
# 最大 ReAct 迭代次数（安全上限，防止无限循环）
# =============================================================================

_MAX_AGENT_ITERATIONS = 10


# =============================================================================
# 路由判断函数
# =============================================================================


def route_after_classify(
    state: AgentState,
) -> Literal["general", "agent"]:
    """分类后路由：高置信度快速路径 vs Agent 自主决策。

    GENERAL 意图 + keyword 高置信度 → 快速路径（省 LLM 调用）
    其他所有情况 → agent_node（LLM 自主决策工具调用）
    """
    intent = state.get("intent")
    method = state.get("classification_method", "")

    # 快速路径：仅限 GENERAL 意图且关键词高置信度（≥0.8）
    if intent == Intent.GENERAL and method == "keyword":
        logger.info("路由: 快速路径（GENERAL + keyword）", intent=intent.value)
        return "general"

    logger.info("路由: Agent 自主决策", intent=intent.value if intent else "unknown")
    return "agent"


def route_after_agent(
    state: AgentState,
) -> Literal["tools", "format_response"]:
    """Agent 决策后路由：继续调用工具 vs 给出最终回答。

    agent_node 返回了 pending_tool_calls → 执行工具
    agent_node 返回了 final_answer → 格式化响应
    """
    pending = state.get("pending_tool_calls", [])
    if pending:
        return "tools"
    return "format_response"


# =============================================================================
# 图节点函数
# =============================================================================


async def classify_node(state: AgentState) -> dict[str, Any]:
    """Step 1-2: 意图分类节点。

    调用 IntentRouter 对用户消息进行分类：
      - 第1层：关键词正则匹配（权重 0.5~0.9）
      - 第2层：LLM（Haiku 轻量模型）回退分类
    结果写入 state.intent 和 state.classification_method。

    Args:
        state: 当前 AgentState（需含 user_message, conversation_history）。

    Returns:
        包含 intent 和 classification_method 的更新 dict。
    """
    run_id = state.get("run_id", "")
    router = IntentRouter(llm_client=LLMClient())
    user_message = state.get("user_message", "")
    conversation_history = state.get("conversation_history")

    node_start = time.monotonic()
    intent = await router.classify(
        user_message,
        conversation_history=conversation_history,
    )
    elapsed_ms = int((time.monotonic() - node_start) * 1000)

    classification_method = getattr(router, "_last_method", "unknown")

    # 可观测性：结构化日志（中文注释，便于国内运维排查）
    logger.info(
        "图节点执行: classify（意图分类）",
        run_id=run_id,
        intent=intent.value,
        classification_method=classification_method,
        duration_ms=elapsed_ms,
    )

    return {
        "intent": intent,
        "classification_method": classification_method,
        "messages": list(state.get("messages", [])) + [{
            "type": "thinking",
            "content": f"分析用户意图：{intent.value}",
            "agent_run_id": run_id,
            "reasoning_type": "classifying",
        }],
    }


async def agent_node(state: AgentState) -> dict[str, Any]:
    """Step 3: LLM 决策节点——Agent 图的核心。

    调用主力模型（带工具定义），由 LLM 决定：
      - 调用工具：返回 tool_calls → 图自动路由到 tools_node
      - 给出回答：返回 final_answer → 图自动路由到 format_response

    这个节点本身不发送 SSE 事件——事件由 astream_events 在外层捕获。
    节点只负责更新 state，将决策结果写入 pending_tool_calls 或 final_answer。

    Args:
        state: 当前 AgentState，需含 user_message, conn_config,
               conversation_history, pending_tool_results。

    Returns:
        包含 pending_tool_calls（工具调用列表）或 final_answer（最终回答）的更新 dict。
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
        "图节点执行: agent（LLM 决策）",
        run_id=run_id,
        iteration=iteration,
    )

    # ── 构建 LLM 消息列表 ──
    llm_client = LLMClient()

    # system prompt: Agent 的角色和工作原则
    system_prompt = _build_system_prompt(state)

    # messages: 用户消息 + 前序工具调用历史
    messages = _build_agent_messages(state)

    # 工具定义：从注册表中获取 JSON Schema 列表
    from app.agent.tools.registry import AGENT_TOOLS, get_tool_schemas  # noqa: I001
    tool_schemas = get_tool_schemas(AGENT_TOOLS)

    node_start = time.monotonic()

    response = await llm_client.chat(
        messages=messages,
        system=system_prompt,
        tools=tool_schemas,
        model=None,  # 使用默认主力模型 settings.LLM_MODEL
        max_tokens=2048,
        timeout=60,
    )

    elapsed_ms = int((time.monotonic() - node_start) * 1000)

    # ── 记录本轮决策轨迹 ──
    trace_entry: dict[str, Any] = {
        "iteration": iteration,
        "duration_ms": elapsed_ms,
    }
    # AGENT_DEBUG 模式：额外记录 LLM prompt 摘要和 token 消耗（B-31 可观测性）
    if settings.AGENT_DEBUG:
        trace_entry["debug"] = {
            "model": settings.LLM_MODEL,
            "system_prompt_length": len(system_prompt),
            "messages_count": len(messages),
            "tools_count": len(tool_schemas),
            "input_tokens": response.usage.input_tokens if response.usage else 0,
            "output_tokens": response.usage.output_tokens if response.usage else 0,
        }
    trace_iterations = list(state.get("trace_iterations", []))

    if response.tool_calls:
        # LLM 决定调用工具
        logger.info(
            "Agent 决策: 调用工具",
            run_id=run_id,
            iteration=iteration,
            tools=[tc.name for tc in response.tool_calls],
            duration_ms=elapsed_ms,
        )

        trace_entry["tool_calls"] = [
            {"name": tc.name, "arguments": tc.arguments}
            for tc in response.tool_calls
        ]
        trace_iterations.append(trace_entry)

        # 写入 SSE 事件
        messages = list(state.get("messages", []))
        for tc in response.tool_calls:
            messages.append({
                "type": "tool_call",
                "tool": tc.name,
                "args": tc.arguments,
                "display": f"正在执行 {tc.name}...",
                "agent_run_id": run_id,
                "iteration": iteration,
            })

        return {
            "pending_tool_calls": [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in response.tool_calls
            ],
            "trace_iterations": trace_iterations,
            "messages": messages,
        }
    else:
        # LLM 给出最终回答
        final_text = response.text or "抱歉，我无法处理您的请求，请换个方式描述问题。"

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
        messages = list(state.get("messages", []))
        if final_text:
            thinking_event: dict[str, Any] = {
                "type": "thinking",
                "content": final_text[:500] + ("..." if len(final_text) > 500 else ""),
                "agent_run_id": run_id,
                "iteration": iteration,
                "reasoning_type": "concluding",
            }
            # AGENT_DEBUG 模式：附加完整 prompt/response 调试信息
            if settings.AGENT_DEBUG:
                thinking_event["debug"] = {
                    "model": settings.LLM_MODEL,
                    "input_tokens": response.usage.input_tokens if response.usage else 0,
                    "output_tokens": response.usage.output_tokens if response.usage else 0,
                    "full_response": final_text[:2000],
                }
            messages.append(thinking_event)

        return {
            "final_answer": final_text,
            "pending_tool_calls": [],
            "trace_iterations": trace_iterations,
            "messages": messages,
        }


async def tools_node(state: AgentState) -> dict[str, Any]:
    """Step 4: 工具执行节点。

    执行 agent_node 请求的工具调用，包含安全护栏：
      1. SQL 审计检查（run_query / explain_query 中的 SQL）
      2. 只读连接检查
      3. 执行工具
      4. 敏感数据脱敏
      5. 收集结果到 pending_tool_results

    被安全护栏拦截的工具不执行，但将错误信息返回给 agent_node，
    LLM 可以据此调整策略（如修改 SQL、换用其他工具）。

    未来扩展（人机交互）：
      检测到危险操作时调用 interrupt() 暂停图执行。

    Args:
        state: 当前 AgentState，需含 pending_tool_calls, conn_config。

    Returns:
        包含 pending_tool_results 和清空 pending_tool_calls 的更新 dict。
    """
    run_id = state.get("run_id", "")
    pending_calls = state.get("pending_tool_calls", [])
    conn_config: dict[str, Any] = state.get("conn_config") or {}
    iteration = len(state.get("trace_iterations", []))

    logger.info(
        "图节点执行: tools（工具执行）",
        run_id=run_id,
        iteration=iteration,
        tools_count=len(pending_calls),
        tools=[tc["name"] for tc in pending_calls],
    )

    # 延迟导入——工具注册表和工具函数
    from app.agent.tools.registry import TOOL_REGISTRY

    results: list[dict[str, Any]] = []
    for tc in pending_calls:
        tool_name = tc["name"]
        tool_args = tc.get("arguments", {})
        tool_start = time.monotonic()

    # ── 安全护栏 ──
        from app.agent.safety import run_safety_checks
        safety_result = await run_safety_checks(tool_name, tool_args, conn_config)
        if safety_result.blocked:
            logger.warning(
                "工具被安全护栏拦截",
                run_id=run_id,
                tool=tool_name,
                reason=safety_result.reason,
            )
            results.append({
                "tool_name": tool_name,
                "error": f"操作被安全策略拦截: {safety_result.reason}",
                "result": None,
            })
            continue

        # ── 查找并执行工具 ──
        tool_fn = TOOL_REGISTRY.get(tool_name)
        if tool_fn is None:
            logger.error(
                "工具未注册",
                run_id=run_id,
                tool=tool_name,
                available=list(TOOL_REGISTRY.keys()),
            )
            results.append({
                "tool_name": tool_name,
                "error": f"工具 '{tool_name}' 未注册，请联系管理员",
                "result": None,
            })
            continue

        try:
            # 合并连接配置参数和 LLM 传入的工具参数
            result = await tool_fn.ainvoke({
                **conn_config,
                **tool_args,
            })
        except Exception as exc:
            logger.error(
                "工具执行异常",
                run_id=run_id,
                tool=tool_name,
                error=str(exc)[:300],
            )
            results.append({
                "tool_name": tool_name,
                "error": f"工具执行失败: {str(exc)[:200]}",
                "result": None,
            })
            continue

        elapsed = int((time.monotonic() - tool_start) * 1000)

        # ── 敏感数据脱敏 ──
        result = _sanitize_sensitive_data(result)

        logger.info(
            "工具执行完成",
            run_id=run_id,
            tool=tool_name,
            duration_ms=elapsed,
            success="error" not in result,
            result_summary=str(result)[:200],
        )

        results.append({
            "tool_name": tool_name,
            "result": result,
            "error": result.get("error") if isinstance(result, dict) else None,
        })

    # 写入 SSE tool_result / sql 事件
    messages = list(state.get("messages", []))
    for r in results:
        if r.get("error"):
            messages.append({
                "type": "tool_result",
                "tool": r["tool_name"],
                "summary": r["error"][:200],
                "agent_run_id": run_id,
                "iteration": iteration,
            })
        else:
            result_data = r.get("result", {})
            if isinstance(result_data, dict):
                # 如果工具返回了 SQL（如 run_query），额外发送 sql 事件
                sql_text = result_data.get("sql") or result_data.get("sql_executed", "")
                if sql_text:
                    messages.append({
                        "type": "sql",
                        "content": sql_text,
                        "audit_status": result_data.get("audit_status", "passed"),
                        "is_readonly": True,
                        "agent_run_id": run_id,
                        "iteration": iteration,
                    })
                messages.append({
                    "type": "tool_result",
                    "tool": r["tool_name"],
                    "summary": (
                        result_data.get("summary", "")
                        or f"返回 {result_data.get('total_rows', 0)} 行"
                    ),
                    "duration_ms": result_data.get("execution_time_ms", 0),
                    "agent_run_id": run_id,
                    "iteration": iteration,
                })
            else:
                messages.append({
                    "type": "tool_result",
                    "tool": r["tool_name"],
                    "summary": str(result_data)[:200],
                    "agent_run_id": run_id,
                    "iteration": iteration,
                })

    return {
        "pending_tool_results": results,
        "pending_tool_calls": [],  # 清空待执行列表
        "messages": messages,
    }


def general_node(state: AgentState) -> dict[str, Any]:
    """快速路径节点：处理简单通用对话。

    不走 Agent 循环，直接返回帮助文本。
    仅在 IntentRouter 高置信度判为 GENERAL 时触发。

    Args:
        state: 当前 AgentState。

    Returns:
        包含 final_answer 和 is_complete 的更新 dict。
    """
    run_id = state.get("run_id", "")
    logger.info("图节点执行: general（快速路径）", run_id=run_id)

    help_text = (
        "我是 DB-Pilot 数据库运维助手。我可以帮助您：\n"
        "1. 自然语言查询数据（NL2SQL）\n"
        "2. SQL 性能诊断与优化\n"
        "3. 数据库故障排查\n"
        "4. 数据库健康巡检\n"
        "请描述您的问题或选择一个数据库连接开始。"
    )

    messages = list(state.get("messages", []))
    messages.append({
        "type": "text",
        "content": help_text,
    })

    return {
        "final_answer": help_text,
        "is_complete": True,
        "messages": messages,
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
    tool_results = state.get("pending_tool_results", [])

    logger.info(
        "图节点执行: format_response（格式化响应）",
        run_id=run_id,
        answer_length=len(final_answer),
        tools_called=len(tool_results),
        total_iterations=len(state.get("trace_iterations", [])),
    )

    messages = list(state.get("messages", []))
    messages.append({
        "type": "result",
        "summary": final_answer,
        "tool_calls_made": len(tool_results),
    })

    return {
        "messages": messages,
        "is_complete": True,
    }


# =============================================================================
# 构建 StateGraph
# =============================================================================


def build_agent_graph() -> CompiledStateGraph:
    """构建 Agent 状态图（B-30 重写）。

    图结构：
      classify → route_by_intent（条件边）
        ├── "general" → general_node → END
        └── "agent" → agent_node
                        │
                        └── agent_node ↔ tools_node（ReAct 循环）
                              │
                              ▼
                        format_response → END

    外部通过 graph.astream_events(initial_state, version="v2") 执行，
    监听 on_chat_model_stream / on_tool_start / on_tool_end / on_chain_end
    事件来推送 SSE。

    Returns:
        编译后的 LangGraph 图，可直接执行。
    """
    workflow = StateGraph(AgentState)

    # ── 注册节点 ──
    workflow.add_node("classify", classify_node)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tools_node)
    workflow.add_node("general", general_node)
    workflow.add_node("format_response", format_response_node)

    # ── 入口：classify ──
    workflow.set_entry_point("classify")

    # ── classify → 条件路由 ──
    workflow.add_conditional_edges(
        "classify",
        route_after_classify,
        {
            "general": "general",
            "agent": "agent",
        },
    )

    # ── general → END（快速路径） ──
    workflow.add_edge("general", END)

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
        "## 可用工具\n"
        "你可以通过调用以下工具来完成用户的任务：\n"
        "- **list_tables**: 获取数据库中的所有表（含注释和估算行数）\n"
        "- **describe_table**: 获取指定表的结构（列定义、索引）\n"
        "- **run_query**: 执行只读 SQL 查询（自动审计和脱敏）\n"
        "- **get_slow_queries**: 获取慢查询日志\n"
        "- **explain_query**: 分析 SQL 执行计划\n"
        "- **check_connections**: 检查数据库连接池状态\n"
        "- **check_locks**: 检查锁等待情况\n"
        "- **check_replication**: 检查主从复制状态\n"
        "- **run_health_check**: 执行 20 项数据库健康巡检\n\n"
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


def _build_agent_messages(state: AgentState) -> list[dict[str, Any]]:
    """构建发送给 LLM 的消息列表。

    包含：
      - 用户原始消息（首次）
      - 前序工具调用结果（后续迭代）
      按照 Anthropic/OpenAI 原生 tool calling 格式组织。

    Args:
        state: 当前 AgentState。

    Returns:
        [{"role": "user", "content": "..."}, {"role": "assistant", ...}, ...]
    """
    messages: list[dict[str, Any]] = []

    # 用户消息
    messages.append({
        "role": "user",
        "content": state.get("user_message", ""),
    })

    # 前序工具调用历史（从 trace_iterations 中提取）
    trace = state.get("trace_iterations", [])
    for entry in trace:
        tool_calls = entry.get("tool_calls", [])
        if tool_calls:
            # assistant 消息：包含 tool_use 请求
            messages.append({
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tc.get("id", f"tc_{i}"),
                        "name": tc["name"],
                        "input": tc["arguments"],
                    }
                    for i, tc in enumerate(tool_calls)
                ],
            })

    # 前序工具执行结果（从 pending_tool_results 中提取）
    prev_results = state.get("pending_tool_results", [])
    if prev_results:
        tool_result_blocks = []
        for i, pr in enumerate(prev_results):
            # 查找对应的 tool_call id
            tool_result_blocks.append({
                "type": "tool_result",
                "tool_use_id": _find_tool_use_id(trace, pr.get("tool_name", ""), i),
                "content": json_safe_str(pr.get("result", {})),
            })
        messages.append({
            "role": "user",
            "content": tool_result_blocks,
        })

    return messages


def _find_tool_use_id(
    trace: list[dict[str, Any]],
    tool_name: str,
    index: int,
) -> str:
    """从 trace 中查找指定工具的 tool_use ID。

    Args:
        trace: 决策轨迹列表。
        tool_name: 工具名称。
        index: 当有多个同名工具时的索引。

    Returns:
        tool_use ID 字符串。
    """
    for entry in trace:
        for tc in entry.get("tool_calls", []):
            if tc.get("name") == tool_name:
                return tc.get("id", f"tc_unknown_{index}")
    return f"tc_unknown_{index}"


def json_safe_str(obj: Any) -> str:
    """将对象安全转换为 JSON 字符串（用于工具结果传递）。"""
    import json as _json
    try:
        return _json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return str(obj)


def _sanitize_sensitive_data(result: Any) -> Any:
    """对工具返回结果中的敏感数据进行脱敏。

    检查结果中的 columns 字段，对匹配敏感模式的列进行掩码处理。
    敏感模式：password, passwd, pwd, secret, token, api_key,
             phone, mobile, email, id_card, ssn。

    Args:
        result: 工具返回的原始结果 dict。

    Returns:
        脱敏后的结果 dict。
    """
    if not isinstance(result, dict):
        return result

    columns = result.get("columns", [])
    rows = result.get("rows", [])
    if not columns or not rows:
        return result

    # 中文注释：识别需要脱敏的敏感列索引
    sensitive_patterns = [
        "password", "passwd", "pwd", "secret", "token", "api_key",
        "phone", "mobile", "email", "id_card", "ssn",
    ]
    sensitive_indices: list[int] = []
    for i, col_name in enumerate(columns):
        col_lower = col_name.lower() if isinstance(col_name, str) else ""
        if any(pattern in col_lower for pattern in sensitive_patterns):
            sensitive_indices.append(i)

    if not sensitive_indices:
        return result

    # 脱敏：将敏感列的数据替换为 "***"
    sanitized_rows = []
    for row in rows:
        new_row = list(row)
        for idx in sensitive_indices:
            if idx < len(new_row):
                new_row[idx] = "***"
        sanitized_rows.append(new_row)

    return {**result, "rows": sanitized_rows}
