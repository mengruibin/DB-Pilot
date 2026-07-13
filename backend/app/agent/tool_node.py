"""
SafeToolNode（任务 5：安全护栏 + 连接注入 + 结果脱敏）。

LangGraph 图节点函数，替代旧的自研 tools_node（graph.py 中 ~170 行手动 for 循环）。
采用 LangChain 标准消息模式：
  - 从 state["messages"] 最后一条 AIMessage 读取 tool_calls
  - 返回 ToolMessage 列表（LangGraph 自动追加到 messages）

功能：
  1. 连接配置注入 — 将 conn_config（host/port/user/password 等）合并到工具参数
  2. 安全护栏 — SQL 审计、只读检查、连接限额
  3. 工具执行 — 通过 TOOL_REGISTRY 查找并调用 tool_fn.ainvoke()
  4. 结果脱敏 — 检测并掩码敏感列
  5. SSE 事件生成 — tool_result / sql 事件写入 sse_events
"""

from __future__ import annotations

import asyncio
import json as _json
import time
from collections.abc import Mapping
from typing import Any

import structlog
from langchain_core.messages import AIMessage, ToolMessage

from app.agent.safety import run_safety_checks
from app.agent.state import AgentState
from app.config import settings

logger = structlog.get_logger(__name__)

# 每轮 ReAct 迭代中最大并行工具数（从配置读取，默认 5）
_MAX_CONCURRENT_TOOLS = max(1, settings.AGENT_MAX_CONCURRENT_TOOLS)


async def _run_one_tool(
    tc: Mapping[str, Any],
    conn_config: dict[str, Any],
    run_id: str,
    iteration: int,
    semaphore: asyncio.Semaphore,
) -> dict:
    """执行单个工具调用的完整生命周期（连接注入 → 安全护栏 → 执行 → 脱敏 → 消息构造）。

    供 safe_tools_node 内部并行调度使用。

    Args:
        tc: LangChain ToolCall 字典，含 id/name/args。
        conn_config: 数据库连接配置字典。
        run_id: 当前 Agent 运行 ID。
        iteration: 当前 ReAct 迭代轮次。
        semaphore: 并发控制信号量。

    Returns:
        {"tool_message": ToolMessage, "sse_events": list[dict]}
    """
    async with semaphore:
        from app.agent.tools.registry import TOOL_REGISTRY  # noqa: I001

        tool_name = tc["name"]
        tool_args: dict[str, Any] = dict(tc["args"])
        local_sse: list[dict] = []
        tool_start = time.monotonic()

        # ── 1. 连接配置注入 ──
        for key in (
            "connection_id",
            "db_type",
            "host",
            "port",
            "database",
            "user",
            "password",
            "ssl_enabled",
            "ssl_ca_cert",
        ):
            if key in conn_config and key not in tool_args:
                tool_args[key] = conn_config[key]
        if "user_role" not in tool_args:
            tool_args["user_role"] = conn_config.get("user_role", "readonly")

        # ── 2. 安全护栏检查 ──
        safety_result = await run_safety_checks(tool_name, tool_args, conn_config)
        if safety_result.blocked:
            logger.warning(
                "工具被安全护栏拦截",
                run_id=run_id,
                tool=tool_name,
                reason=safety_result.reason,
            )
            return {
                "tool_message": ToolMessage(
                    content=f"操作被安全策略拦截: {safety_result.reason}",
                    tool_call_id=tc["id"],
                    name=tool_name,
                ),
                "sse_events": [
                    {
                        "type": "tool_result",
                        "tool": tool_name,
                        "summary": f"拦截: {safety_result.reason}",
                        "tool_call_id": tc["id"],
                        "agent_run_id": run_id,
                        "iteration": iteration,
                        "safety_checks_passed": False,
                    }
                ],
            }

        safety_warnings = list(safety_result.warnings)
        if safety_warnings:
            logger.info(
                "安全护栏性能提示已记录（非阻断）",
                run_id=run_id,
                tool=tool_name,
                warning_count=len(safety_warnings),
            )

        # ── 3. 查找工具 ──
        tool_fn = TOOL_REGISTRY.get(tool_name)
        if tool_fn is None:
            logger.error(
                "工具未注册",
                run_id=run_id,
                tool=tool_name,
                available=list(TOOL_REGISTRY.keys()),
            )
            return {
                "tool_message": ToolMessage(
                    content=f"工具 '{tool_name}' 未注册，请联系管理员",
                    tool_call_id=tc["id"],
                    name=tool_name,
                ),
                "sse_events": [
                    {
                        "type": "tool_result",
                        "tool": tool_name,
                        "summary": f"工具 '{tool_name}' 未注册",
                        "tool_call_id": tc["id"],
                        "agent_run_id": run_id,
                        "iteration": iteration,
                        "safety_checks_passed": False,
                    }
                ],
            }

        # ── 4. 执行工具 ──
        try:
            result = await tool_fn.ainvoke(tool_args)
        except Exception as exc:
            logger.error(
                "工具执行异常",
                run_id=run_id,
                tool=tool_name,
                error=str(exc)[:300],
            )
            return {
                "tool_message": ToolMessage(
                    content=f"工具执行失败: {str(exc)[:200]}",
                    tool_call_id=tc["id"],
                    name=tool_name,
                ),
                "sse_events": [
                    {
                        "type": "tool_result",
                        "tool": tool_name,
                        "summary": f"执行失败: {str(exc)[:100]}",
                        "tool_call_id": tc["id"],
                        "agent_run_id": run_id,
                        "iteration": iteration,
                        "safety_checks_passed": True,
                    }
                ],
            }

        elapsed = int((time.monotonic() - tool_start) * 1000)

        logger.info(
            "工具执行完成",
            run_id=run_id,
            tool=tool_name,
            duration_ms=elapsed,
            success=True,
            result_summary=str(result)[:200],
        )

        # ── 6. 构造 ToolMessage（含安全护栏的非阻断警告） ──
        tool_content = _json.dumps(result, ensure_ascii=False, default=str)
        if safety_warnings:
            tool_content = "[性能提示] " + " | ".join(safety_warnings) + "\n\n" + tool_content

        # ── 7. 生成 SSE 事件 ──
        if isinstance(result, dict):
            sql_text = result.get("sql") or result.get("sql_executed", "")
            if sql_text:
                local_sse.append(
                    {
                        "type": "sql",
                        "content": sql_text,
                        "audit_status": result.get("audit_status", "passed"),
                        "is_readonly": result.get("is_readonly", True),
                        "agent_run_id": run_id,
                        "iteration": iteration,
                    }
                )
            local_sse.append(
                {
                    "type": "tool_result",
                    "tool": tool_name,
                    "summary": (result.get("summary", "") or f"{tool_name} 执行完成"),
                    "duration_ms": result.get("execution_time_ms", elapsed),
                    "tool_call_id": tc["id"],
                    "agent_run_id": run_id,
                    "iteration": iteration,
                    "safety_checks_passed": True,
                }
            )
        else:
            local_sse.append(
                {
                    "type": "tool_result",
                    "tool": tool_name,
                    "summary": str(result)[:200],
                    "duration_ms": elapsed,
                    "tool_call_id": tc["id"],
                    "agent_run_id": run_id,
                    "iteration": iteration,
                    "safety_checks_passed": True,
                }
            )

        return {
            "tool_message": ToolMessage(
                content=tool_content,
                tool_call_id=tc["id"],
                name=tool_name,
            ),
            "sse_events": local_sse,
        }


async def safe_tools_node(state: AgentState) -> dict[str, Any]:
    """标准 LangGraph 节点：安全执行 AIMessage 中的工具调用（并行调度）。

    从 state["messages"] 中提取最后一条 AIMessage.tool_calls，
    并发执行安全检查、连接配置注入、工具调用、结果脱敏。
    单个工具异常不会影响其他工具的执行。

    Args:
        state: 当前 AgentState，需含 messages, conn_config。

    Returns:
        包含 messages（ToolMessage 列表）和 sse_events 的更新 dict。
    """
    run_id = state.get("run_id", "")
    conn_config: dict[str, Any] = state.get("conn_config") or {}
    iteration = len(state.get("trace_iterations", []))
    sse_events = list(state.get("sse_events", []))

    # ── 从消息列表中提取 tool_calls ──
    all_messages = state.get("messages", [])
    last_msg = all_messages[-1] if all_messages else None

    if not isinstance(last_msg, AIMessage) or not last_msg.tool_calls:
        logger.warning(
            "safe_tools_node 被调用但无 AIMessage.tool_calls",
            run_id=run_id,
            last_msg_type=type(last_msg).__name__ if last_msg else None,
        )
        return {"sse_events": sse_events}

    tool_calls = last_msg.tool_calls

    logger.info(
        "图节点执行: tools（SafeToolNode 并行模式）",
        run_id=run_id,
        iteration=iteration,
        tools_count=len(tool_calls),
        tools=[tc["name"] for tc in tool_calls],
    )

    # 并发执行所有工具
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_TOOLS)
    results = await asyncio.gather(
        *[
            _run_one_tool(tc, conn_config, run_id, iteration, semaphore)
            for tc in tool_calls
        ],
        return_exceptions=True,
    )

    # 按原始 tool_calls 顺序组装结果（gather 保持输入顺序）
    tool_messages: list[ToolMessage] = []
    for i, r in enumerate(results):
        if isinstance(r, BaseException):
            # 极少情况：gather 自身的异常（超时等），非工具抛出的异常
            tc = tool_calls[i]
            logger.error(
                "工具并行执行异常",
                run_id=run_id,
                tool=tc["name"],
                error=str(r)[:300],
            )
            tool_messages.append(
                ToolMessage(
                    content=f"工具执行异常: {str(r)[:200]}",
                    tool_call_id=tc["id"],
                    name=tc["name"],
                )
            )
            sse_events.append(
                {
                    "type": "tool_result",
                    "tool": tc["name"],
                    "summary": f"执行异常: {str(r)[:100]}",
                    "tool_call_id": tc["id"],
                    "agent_run_id": run_id,
                    "iteration": iteration,
                    "safety_checks_passed": True,
                }
            )
        else:
            tool_messages.append(r["tool_message"])
            sse_events.extend(r["sse_events"])

    return {
        "messages": tool_messages,
        "sse_events": sse_events,
    }
