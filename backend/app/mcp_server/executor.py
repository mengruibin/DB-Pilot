"""MCP 无头执行器（EXTENSION: 复用安全流水线编排原语的单工具执行）。

流程：危险工具策略闸门 → PRE_CONFIRM 审计 → PRE_EXECUTE(EXPLAIN 评估) + 执行。
不引入 LangGraph / SSE / interrupt——宿主 Agent（Claude Code/Codex）负责编排与对话。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from typing import Any

import structlog

from app.agent.security.models import (
    SecurityContext,
    SecurityProfile,
    StagePhase,
)
from app.agent.security.orchestrator import run_phase3, run_pre_confirm
from app.agent.security.registry import get_security_profile
from app.mcp_server.policy import Policy

logger = structlog.get_logger(__name__)


def _headless_profile(profile: SecurityProfile) -> SecurityProfile:
    """无头 profile：剔除 CONFIRM（策略替代）与 impact_estimate（无确认卡则不预估）。

    sql_audit(PRE_CONFIRM) 与 row_estimation(PRE_EXECUTE) 保留——安全硬闸不变。
    """
    kept = tuple(
        ref
        for ref in profile.stages
        if ref.phase is not StagePhase.CONFIRM and ref.name != "impact_estimate"
    )
    return SecurityProfile(stages=kept)


def _outcome_text(tool_name: str, outcome: Any) -> str:
    """把单个 ToolOutcome 转成给宿主的文本结果。"""
    kind = getattr(outcome, "kind", None)
    if kind == "executed":
        return outcome.content or ""
    if kind in {"blocked", "re_blocked"}:
        return f"操作被安全策略拦截: {outcome.reason or tool_name}"
    if kind == "not_found":
        return f"工具 '{tool_name}' 未注册"
    return f"工具执行未返回结果: {tool_name}"


async def run_mcp_tool(
    tool_name: str,
    args: Mapping[str, Any],
    conn_config: dict[str, Any],
    policy: Policy,
) -> str:
    """执行单个 MCP 工具调用（无头、无人类确认）。

    Args:
        tool_name: Web 工具名（无 dbpilot_ 前缀）。
        args: 工具对外参数（sql / statements / table_names / thread_id …）。
        conn_config: 连接配置 dict（connections.ConnectionEntry.to_conn_config 产出）。
        policy: 写策略快照。

    Returns:
        str — 工具 JSON 结果文本，或安全拦截 / 策略拒绝说明。

    Raises:
        ValueError: 工具未注册。
    """
    profile = get_security_profile(tool_name)
    if profile.confirm_ref() is not None and not policy.allow_write:
        return policy.gate_message(tool_name)

    run_id = f"mcp_{uuid.uuid4().hex[:12]}"
    tc: dict[str, Any] = {"id": f"tc_{uuid.uuid4().hex[:8]}", "name": tool_name, "args": dict(args)}
    headless = _headless_profile(profile)
    profiles = {tool_name: headless}
    ctx = SecurityContext(
        run_id=run_id,
        session_id=None,
        conn_config=conn_config,
        user_role=conn_config.get("user_role", "readonly"),
        consecutive_blocks=0,
    )
    semaphore = asyncio.Semaphore(1)

    # Phase 1: PRE_CONFIRM（纯 sqlglot 审计；impact_estimate 已剔除）
    blocked = await run_pre_confirm([tc], profiles, conn_config, ctx, semaphore)
    if tc["id"] in blocked:
        result = blocked[tc["id"]]
        logger.warning("MCP 工具被审计拦截", run_id=run_id, tool=tool_name)
        return f"操作被安全策略拦截: {result.reason or tool_name}"

    # Phase 3: PRE_EXECUTE(EXPLAIN 评估) + 执行 + 截断
    outcomes = await run_phase3(
        [tc], profiles, conn_config, ctx, run_id, iteration=0, semaphore=semaphore
    )
    return _outcome_text(tool_name, outcomes[0] if outcomes else None)
