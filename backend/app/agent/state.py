"""
Agent 状态定义。

定义 AgentState TypedDict 和 Intent 枚举。
AgentState 由 LangGraph StateGraph 自动维护，每个节点可以读取和更新部分字段。

重构（任务 2）：
  - messages 字段使用 add_messages reducer（LangGraph 标准消息追加语义）
  - 新增 sse_events 字段（前端 SSE 事件，与 LLM 消息分离）
  - 移除 pending_tool_calls / pending_tool_results（由 AIMessage.tool_calls / ToolMessage 替代）

依据 PRD §6.3 Agent 设计、api-contract §1.2 SSE 事件类型。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


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
    """LangGraph 图运行时状态。

    total=False 表示所有字段都是可选的，节点只返回需要更新的字段。

    Attributes:
        # ── 标准 LangGraph 消息（add_messages reducer 自动追加） ──
        messages: LangChain 消息列表（HumanMessage / AIMessage / ToolMessage）。
            LangGraph 的 add_messages reducer 自动处理追加和去重。

        # ── 前端 SSE 事件（独立于 LLM 上下文，专用于前端展示） ──
        sse_events: 前端 SSE 事件累积列表。
            图节点写入 type="thinking"/"tool_call"/"tool_result"/"sql"/"result" 等事件。
            API 层从 state["sse_events"] 读取并序列化为 SSE 流。

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

        # ── 输出字段（图执行结束时填充） ──
        final_answer: Agent 最终自然语言回答。
        is_complete: 图是否执行完毕。

        # ── 可观测性 ──
        run_id: 本次 Agent 运行唯一 ID（UUID 格式）。
        trace_iterations: Agent 决策轨迹（每轮 ReAct 迭代记录）。
        error: 错误信息（若有）。
    """
    # ── 标准 LangGraph 消息 ──
    messages: Annotated[list[Any], add_messages]

    # ── 前端 SSE 事件 ──
    sse_events: list[dict[str, Any]]

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

    # ── 输出字段 ──
    final_answer: str | None
    is_complete: bool

    # ── 可观测性 ──
    run_id: str
    trace_iterations: list[dict[str, Any]]
    error: str | None
