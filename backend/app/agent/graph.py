"""
Agent StateGraph 定义。

定义 Agent 工作流的节点和条件边。
依据 PRD §4.2 Agent 工作流 6 步：
  Step 1: 用户输入 → Step 2: Intent Router → Step 3: 路由到 Engine
  → Step 4: 调用工具 → Step 5: LLM 分析 → Step 6: SSE 返回

节点序列：
  classify → route_to_engine（条件边）→ [nl2sql|diagnosis|troubleshoot|healthcheck|general]
  → format_response
"""

from __future__ import annotations

from typing import Any, Literal

import structlog
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.router import IntentRouter
from app.agent.state import AgentState, Intent
from app.engine.llm_client import LLMClient

logger = structlog.get_logger(__name__)

# =============================================================================
# 路由判断函数
# =============================================================================

def route_to_engine(state: AgentState) -> Literal[
    "nl2sql_node",
    "diagnosis_node",
    "troubleshoot_node",
    "healthcheck_node",
    "general_node",
]:
    """根据 intent 选择对应的处理引擎节点（条件边）。"""
    intent = state.get("intent")
    if intent == Intent.QUERY:
        return "nl2sql_node"
    if intent == Intent.DIAGNOSIS:
        return "diagnosis_node"
    if intent == Intent.TROUBLESHOOT:
        return "troubleshoot_node"
    if intent == Intent.HEALTH_CHECK:
        return "healthcheck_node"
    return "general_node"


# =============================================================================
# 节点函数
# =============================================================================

async def classify_node(state: AgentState) -> dict[str, Any]:
    """Step 2: 意图分类节点。

    调用 IntentRouter 对用户消息进行分类，将结果写入 state.intent。
    低置信度时通过 LLMClient 调用轻量模型分类。
    """
    router = IntentRouter(llm_client=LLMClient())
    user_message = state.get("user_message", "")
    intent = await router.classify(user_message)

    logger.info("图节点执行", node="classify_node", intent=intent.value)

    # 将用户消息追加到 messages
    new_messages = list(state.get("messages", []))
    new_messages.append({
        "type": "thinking",
        "content": f"分析用户意图：{intent.value}",
    })

    return {
        "intent": intent,
        "messages": new_messages,
    }


def nl2sql_node(state: AgentState) -> dict[str, Any]:
    """NL2SQL 查询处理节点。

    调用工具：list_tables → describe_table → run_query。
    实际工具调用由 B-13/B-17 实现，当前为骨架。
    """
    intent = state.get("intent", "")
    logger.info("图节点执行", node="nl2sql_node", intent=intent.value if intent else "unknown")

    messages = list(state.get("messages", []))
    messages.append({
        "type": "thinking",
        "content": "正在理解用户的查询需求并生成 SQL...",
    })
    return {
        "messages": messages,
        "tool_results": [],
    }


def diagnosis_node(state: AgentState) -> dict[str, Any]:
    """SQL 诊断与优化节点。

    调用工具：get_slow_queries → explain_query。
    实际工具调用由 B-14/B-18 实现。
    """
    intent = state.get("intent", "")
    logger.info("图节点执行", node="diagnosis_node", intent=intent.value if intent else "unknown")

    messages = list(state.get("messages", []))
    messages.append({
        "type": "thinking",
        "content": "正在分析数据库性能问题...",
    })
    return {
        "messages": messages,
        "tool_results": [],
    }


def troubleshoot_node(state: AgentState) -> dict[str, Any]:
    """故障排查节点。

    调用工具：check_connections → check_locks → check_replication。
    实际工具调用由 B-15 实现。
    """
    intent = state.get("intent", "")
    intent_val = intent.value if intent else "unknown"
    logger.info("图节点执行", node="troubleshoot_node", intent=intent_val)

    messages = list(state.get("messages", []))
    messages.append({
        "type": "thinking",
        "content": "正在诊断数据库故障...",
    })
    return {
        "messages": messages,
        "tool_results": [],
    }


def healthcheck_node(state: AgentState) -> dict[str, Any]:
    """健康巡检节点。

    调用 HealthCheckEngine 执行 20 项检查。
    实际检查逻辑由 B-16 实现。
    """
    intent = state.get("intent", "")
    logger.info("图节点执行", node="healthcheck_node", intent=intent.value if intent else "unknown")

    messages = list(state.get("messages", []))
    messages.append({
        "type": "thinking",
        "content": "正在执行数据库健康巡检...",
    })
    return {
        "messages": messages,
        "tool_results": [],
    }


def general_node(state: AgentState) -> dict[str, Any]:
    """通用对话节点。

    处理非数据库操作类问题（如帮助信息、闲聊）。
    """
    intent = state.get("intent", "")
    logger.info("图节点执行", node="general_node", intent=intent.value if intent else "unknown")

    messages = list(state.get("messages", []))
    messages.append({
        "type": "thinking",
        "content": "正在准备回复...",
    })
    messages.append({
        "type": "result",
        "content": "我是 DB-Pilot 数据库运维助手。我可以帮助您：\n"
        "1. 自然语言查询数据（NL2SQL）\n"
        "2. SQL 性能诊断与优化\n"
        "3. 数据库故障排查\n"
        "4. 数据库健康巡检\n"
        "请描述您的问题或选择一个数据库连接开始。",
    })
    return {
        "messages": messages,
        "tool_results": [],
    }


def format_response(state: AgentState) -> dict[str, Any]:
    """Step 6: 格式化最终响应。

    将工具调用结果和 LLM 输出组装为 SSE 事件序列。
    实际格式化逻辑由 B-19 SSE 端点实现。
    """
    logger.info("图节点执行", node="format_response")

    messages = list(state.get("messages", []))
    # 添加 done 标记
    messages.append({
        "type": "done",
        "session_id": state.get("session_id", ""),
        "tokens_used": 0,
    })
    return {"messages": messages, "error": None}


# =============================================================================
# 构建 StateGraph
# =============================================================================

def build_agent_graph() -> CompiledStateGraph:
    """构建 Agent 状态图。

    节点顺序（PRD §4.2）：
      classify → route_to_engine（条件边）
        ├── nl2sql_node → format_response
        ├── diagnosis_node → format_response
        ├── troubleshoot_node → format_response
        ├── healthcheck_node → format_response
        └── general_node → format_response
    """
    workflow = StateGraph(AgentState)

    # 注册节点
    workflow.add_node("classify", classify_node)
    workflow.add_node("nl2sql_node", nl2sql_node)
    workflow.add_node("diagnosis_node", diagnosis_node)
    workflow.add_node("troubleshoot_node", troubleshoot_node)
    workflow.add_node("healthcheck_node", healthcheck_node)
    workflow.add_node("general_node", general_node)
    workflow.add_node("format_response", format_response)

    # 设置入口
    workflow.set_entry_point("classify")

    # 条件边：classify → 按意图路由到对应引擎
    workflow.add_conditional_edges(
        "classify",
        route_to_engine,
        {
            "nl2sql_node": "nl2sql_node",
            "diagnosis_node": "diagnosis_node",
            "troubleshoot_node": "troubleshoot_node",
            "healthcheck_node": "healthcheck_node",
            "general_node": "general_node",
        },
    )

    # 各引擎节点 → format_response
    workflow.add_edge("nl2sql_node", "format_response")
    workflow.add_edge("diagnosis_node", "format_response")
    workflow.add_edge("troubleshoot_node", "format_response")
    workflow.add_edge("healthcheck_node", "format_response")
    workflow.add_edge("general_node", "format_response")

    # format_response → END
    workflow.add_edge("format_response", END)

    return workflow.compile()
