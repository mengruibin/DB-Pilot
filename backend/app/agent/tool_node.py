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

import json as _json
import time
from typing import Any

import structlog
from langchain_core.messages import AIMessage, ToolMessage

from app.agent.safety import run_safety_checks
from app.agent.state import AgentState

logger = structlog.get_logger(__name__)


async def safe_tools_node(state: AgentState) -> dict[str, Any]:
    """标准 LangGraph 节点：安全执行 AIMessage 中的工具调用。

    从 state["messages"] 中提取最后一条 AIMessage.tool_calls，
    依次执行安全检查、连接配置注入、工具调用、结果脱敏。

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
        "图节点执行: tools（SafeToolNode 标准模式）",
        run_id=run_id,
        iteration=iteration,
        tools_count=len(tool_calls),
        tools=[tc["name"] for tc in tool_calls],
    )

    # 延迟导入——工具注册表
    from app.agent.tools.registry import TOOL_REGISTRY  # noqa: I001

    tool_messages: list[ToolMessage] = []

    for tc in tool_calls:
        tool_name = tc["name"]
        tool_args: dict[str, Any] = dict(tc["args"])  # 复制，避免修改原始数据
        tool_start = time.monotonic()

        # ── 1. 连接配置注入 ──
        # 将 conn_config 合并到工具参数中（LLM 不可见这些参数）
        for key in (
            "connection_id", "db_type", "host", "port", "database",
            "user", "password", "ssl_enabled", "ssl_ca_cert",
        ):
            if key in conn_config and key not in tool_args:
                tool_args[key] = conn_config[key]
        # 注入用户角色（安全护栏需要）
        if "user_role" not in tool_args:
            tool_args["user_role"] = conn_config.get("user_role", "standard")

        # ── 2. 安全护栏检查 ──
        safety_result = await run_safety_checks(tool_name, tool_args, conn_config)
        if safety_result.blocked:
            logger.warning(
                "工具被安全护栏拦截",
                run_id=run_id,
                tool=tool_name,
                reason=safety_result.reason,
            )
            tool_messages.append(ToolMessage(
                content=f"操作被安全策略拦截: {safety_result.reason}",
                tool_call_id=tc["id"],
                name=tool_name,
            ))
            sse_events.append({
                "type": "tool_result",
                "tool": tool_name,
                "summary": f"拦截: {safety_result.reason}",
                "agent_run_id": run_id,
                "iteration": iteration,
                "safety_checks_passed": False,
            })
            continue

        # ── 3. 查找工具 ──
        tool_fn = TOOL_REGISTRY.get(tool_name)
        if tool_fn is None:
            logger.error(
                "工具未注册",
                run_id=run_id,
                tool=tool_name,
                available=list(TOOL_REGISTRY.keys()),
            )
            tool_messages.append(ToolMessage(
                content=f"工具 '{tool_name}' 未注册，请联系管理员",
                tool_call_id=tc["id"],
                name=tool_name,
            ))
            continue

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
            tool_messages.append(ToolMessage(
                content=f"工具执行失败: {str(exc)[:200]}",
                tool_call_id=tc["id"],
                name=tool_name,
            ))
            sse_events.append({
                "type": "tool_result",
                "tool": tool_name,
                "summary": f"执行失败: {str(exc)[:100]}",
                "agent_run_id": run_id,
                "iteration": iteration,
                "safety_checks_passed": True,
            })
            continue

        elapsed = int((time.monotonic() - tool_start) * 1000)

        # ── 5. 敏感数据脱敏 ──
        result = _sanitize_sensitive_data(result)

        logger.info(
            "工具执行完成",
            run_id=run_id,
            tool=tool_name,
            duration_ms=elapsed,
            success=True,
            result_summary=str(result)[:200],
        )

        # ── 6. 返回 ToolMessage ──
        tool_content = _json.dumps(result, ensure_ascii=False, default=str)
        tool_messages.append(ToolMessage(
            content=tool_content,
            tool_call_id=tc["id"],
            name=tool_name,
        ))

        # ── 7. 生成 SSE 事件 ──
        if isinstance(result, dict):
            # 如果工具返回了 SQL（如 run_query），发送 sql 事件
            sql_text = result.get("sql") or result.get("sql_executed", "")
            if sql_text:
                sse_events.append({
                    "type": "sql",
                    "content": sql_text,
                    "audit_status": result.get("audit_status", "passed"),
                    "is_readonly": True,
                    "agent_run_id": run_id,
                    "iteration": iteration,
                })
            sse_events.append({
                "type": "tool_result",
                "tool": tool_name,
                "summary": (
                    result.get("summary", "")
                    or f"{tool_name} 执行完成"
                ),
                "duration_ms": result.get("execution_time_ms", elapsed),
                "agent_run_id": run_id,
                "iteration": iteration,
                "safety_checks_passed": True,
            })
        else:
            sse_events.append({
                "type": "tool_result",
                "tool": tool_name,
                "summary": str(result)[:200],
                "duration_ms": elapsed,
                "agent_run_id": run_id,
                "iteration": iteration,
                "safety_checks_passed": True,
            })

    return {
        "messages": tool_messages,  # add_messages reducer 自动追加 ToolMessage 列表
        "sse_events": sse_events,
    }


# =============================================================================
# 敏感数据脱敏（从 graph.py 迁移，任务 5 后用此版本）
# =============================================================================


def _sanitize_sensitive_data(result: Any) -> Any:
    """对工具返回结果中的敏感列进行脱敏。

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

    sanitized_rows = []
    for row in rows:
        new_row = list(row)
        for idx in sensitive_indices:
            if idx < len(new_row):
                new_row[idx] = "***"
        sanitized_rows.append(new_row)

    return {**result, "rows": sanitized_rows}
