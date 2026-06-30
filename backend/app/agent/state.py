"""
Agent 状态定义。

定义 AgentState TypedDict 和 Intent 枚举。
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


class AgentState(TypedDict):
    """Agent 运行时状态。

    由 LangGraph StateGraph 自动维护，每个 node 可以读取和更新。

    Attributes:
        messages: 对话消息列表（LangChain Message 格式），每个 tool 返回追加到此。
        connection_id: 当前选中的目标数据库连接 ID。
        session_id: 当前会话 ID。
        intent: 用户意图分类结果（由 IntentRouter 填充）。
        user_role: 用户角色（readonly / standard / admin）。
        user_message: 用户输入的原始消息文本。
        generated_sql: 生成的 SQL 语句（NL2SQL 节点填充）。
        sql_executed: 实际执行的 SQL（可能经改写）。
        query_result: 查询执行结果。
        tool_results: 各工具调用的中间结果列表。
        error: 错误信息（若有）。
    """
    messages: list[dict[str, Any]]
    connection_id: str | None
    session_id: str | None
    intent: Intent | None
    user_role: str
    user_message: str
    generated_sql: str | None
    sql_executed: str | None
    query_result: dict[str, Any] | None
    tool_results: list[dict[str, Any]]
    error: str | None
