"""
SecureToolsNode（security-pipeline-north-star-plan / 任务 B2）。

LangGraph 图节点函数——安全流水线的图侧入口（薄委托层）。

节点职责（三阶段安全流水线编排全部委托给 security/orchestrator.py）：
  1. 从 state["messages"] 提取最后一条 AIMessage.tool_calls
  2. 按工具名查 SECURITY_REGISTRY 构建 SecurityProfile 映射
  3. 构建 SecurityContext（含入口 consecutive_blocks）
  4. 委托 run_security_pipeline 执行三阶段编排：
     Phase 1 PRE_CONFIRM（并行纯审计）→ Phase 2 CONFIRM（批量 interrupt）
     → Phase 3 PRE_EXECUTE + 执行（并行）

相比旧 safe_tools_node 的架构改进：
  - 安全策略单一事实源：SECURITY_REGISTRY（不再读工具 extras / 字符串特征）
  - 图收敛为 agent → tools → agent（确认逻辑并入本节点，不再有独立 confirm_node）
  - 工具执行原语迁入 security/orchestrator.py
"""

from __future__ import annotations

from typing import Any

import structlog
from langchain_core.messages import AIMessage

from app.agent.security.models import SecurityContext
from app.agent.security.orchestrator import run_security_pipeline
from app.agent.security.registry import get_security_profile
from app.agent.state import AgentState

logger = structlog.get_logger(__name__)


async def secure_tools_node(state: AgentState) -> dict[str, Any]:
    """标准 LangGraph 节点：安全执行 AIMessage 中的工具调用（三阶段流水线）。

    从 state["messages"] 中提取最后一条 AIMessage.tool_calls，
    委托 security.orchestrator.run_security_pipeline 完成：
      PRE_CONFIRM（并行审计）→ CONFIRM（interrupt 确认）→ PRE_EXECUTE + 执行（并行）。
    单个工具异常不会影响其他工具的执行（Phase 3 按工具隔离）。

    interrupt() 在 orchestrator 内被调用：首遍抛 GraphInterrupt 终止节点、
    resume 遍从本节点重跑（PRE_CONFIRM 重放 + Phase 3 恰一次）。

    Args:
        state: 当前 AgentState，需含 messages, conn_config。

    Returns:
        orchestrator 返回的更新 dict（messages / sse_events / consecutive_blocks，
        强制终止时附 is_complete / final_answer）。
    """
    run_id = state.get("run_id", "")
    conn_config: dict[str, Any] = state.get("conn_config") or {}
    iteration = len(state.get("trace_iterations", []))
    session_id = state.get("session_id")
    sse_events = list(state.get("sse_events", []))

    # ── 从消息列表中提取 tool_calls ──
    all_messages = state.get("messages", [])
    last_msg = all_messages[-1] if all_messages else None

    if not isinstance(last_msg, AIMessage) or not last_msg.tool_calls:
        logger.warning(
            "secure_tools_node 被调用但无 AIMessage.tool_calls",
            run_id=run_id,
            last_msg_type=type(last_msg).__name__ if last_msg else None,
        )
        return {"sse_events": sse_events}

    tool_calls = last_msg.tool_calls

    logger.info(
        "图节点执行: tools（安全流水线）",
        run_id=run_id,
        iteration=iteration,
        tools_count=len(tool_calls),
        tools=[tc["name"] for tc in tool_calls],
    )

    # ── 构建 profile 映射（安全策略单一事实源） ──
    profiles = {tc["name"]: get_security_profile(tc["name"]) for tc in tool_calls}

    # ── 构建安全上下文（consecutive_blocks 为入口值） ──
    ctx = SecurityContext(
        run_id=run_id,
        session_id=session_id,
        conn_config=conn_config,
        user_role=conn_config.get("user_role", "readonly"),
        consecutive_blocks=state.get("consecutive_blocks", 0),
    )

    # ── 委托三阶段编排 ──
    return await run_security_pipeline(
        tool_calls=tool_calls,
        profiles=profiles,
        conn_config=conn_config,
        ctx=ctx,
        run_id=run_id,
        iteration=iteration,
        session_id=session_id,
        initial_sse=sse_events,
    )
