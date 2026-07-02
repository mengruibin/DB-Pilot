"""
Agent 状态定义。

定义 AgentState TypedDict 和 Intent 枚举。
AgentState 由 LangGraph StateGraph 自动维护，每个节点可以读取和更新部分字段。

依据 PRD §6.3 Agent 设计、api-contract §1.2 SSE 事件类型。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, TypedDict


class Intent(StrEnum):
    """用户意图枚举（PRD §6.3 意图路由）。"""
    QUERY = "QUERY"
    """自然语言数据查询（NL2SQL）"""
    DIAGNOSIS = "DIAGNOSIS"
    """SQL 诊断与优化"""
    TROUBLESHOOT = "TROUBLESHOOT"
    """故障自动排查"""
    HEALTH_CHECK = "HEALTH_CHECK"
    """数据库健康巡检"""
    GENERAL = "GENERAL"
    """通用对话（非数据库操作）"""


class AgentState(TypedDict, total=False):
    """LangGraph 图运行时状态（B-30 重设计）。

    每个节点可以读取和更新部分字段，LangGraph 自动合并到状态中。
    total=False 表示所有字段都是可选的，节点只返回需要更新的字段。

    Attributes:
        # ── 输入字段（调用方在创建 state 时填充） ──
        user_message: 用户输入的原始消息文本。
        connection_id: 目标数据库连接 ID。
        session_id: 当前会话 ID。
        password: 连接密码（仅存于内存 state，不持久化）。
        user_role: 用户角色（readonly / standard / admin）。
        conversation_history: 格式化的会话历史文本。

        # ── 中间结果（图节点执行过程中填充） ──
        intent: IntentRouter 分类结果。
        classification_method: 分类方法（keyword / llm / fallback / default）。
        conn_config: 解析后的目标数据库连接配置。
        pending_tool_calls: agent_node 请求的工具调用列表。
            每项: {"id": str, "name": str, "arguments": dict}
        pending_tool_results: tools_node 返回的工具执行结果列表。
            每项: {"tool_name": str, "result": dict | None, "error": str | None}

        # ── 输出字段（图执行结束时填充） ──
        final_answer: Agent 最终自然语言回答。
        is_complete: 图是否执行完毕。

        # ── 可观测性 ──
        run_id: 本次 Agent 运行唯一 ID（UUID 格式）。
        trace_iterations: Agent 决策轨迹（每轮 ReAct 迭代记录）。
        messages: SSE 事件累积列表。
        error: 错误信息（若有）。
    """
    # ── 输入字段 ──
    user_message: str
    connection_id: str
    session_id: str | None
    password: str | None
    user_role: str
    conversation_history: str | None

    # ── 中间结果 ──
    intent: Intent | None
    classification_method: str | None
    conn_config: dict[str, Any] | None
    pending_tool_calls: list[dict[str, Any]]
    pending_tool_results: list[dict[str, Any]]

    # ── 输出字段 ──
    final_answer: str | None
    is_complete: bool

    # ── 可观测性 ──
    run_id: str
    trace_iterations: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    error: str | None
