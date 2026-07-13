"""
故障排查 SSE 流 API 路由（Phase 4 统一——复用 LangGraph Agent 图）。

端点（api-contract §1.5）：
  - POST /api/connections/{id}/troubleshoot  SSE 流式故障排查

2026-07-02 重构：不再使用独立的 TroubleshootWorkflow，改为复用
app/agent/graph.py 的 LangGraph Agent 图。Agent 自主决定调用
check_connections / check_locks / check_replication 等工具。

依据 api-contract §1.5、AGENTS.md §安全与合规红线（密码不落盘）。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

import structlog
from fastapi import APIRouter, Body, HTTPException, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.sse_utils import format_sse
from app.agent.state import AgentState

# 复用 B-20 的 SSE 取消机制
from app.api.chat import _active_streams, _running_tasks  # type: ignore[attr-defined]  # noqa: F811
from app.database import async_session_factory
from app.models.connection import ConnectionConfigModel
from app.models.schemas import TroubleshootRequest

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["Troubleshoot"])


# =============================================================================
# 辅助：从 ORM 加载连接配置
# =============================================================================


async def _resolve_conn_config(
    connection_id: str,
    password: str | None,
    session: AsyncSession,
    trace_id: str = "",
) -> dict[str, Any]:
    """从 ORM 加载连接配置，组装为工具调用参数字典。

    Args:
        connection_id: 连接 ID。
        password: 连接密码。
        session: 数据库会话。
        trace_id: 请求追踪 ID（用于角色检测日志链路）。
    """
    result = await session.execute(
        select(ConnectionConfigModel).where(
            ConnectionConfigModel.id == connection_id
        )
    )
    conn = result.scalar_one_or_none()
    if conn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "NOT_FOUND",
                "user_message": f"连接 {connection_id} 不存在或已删除",
            },
        )

    config: dict[str, Any] = {
        "connection_id": connection_id,
        "db_type": conn.db_type,
        "host": conn.host,
        "port": conn.port,
        "database": conn.database,
        "user": conn.user,
        "password": password or "",
        "ssl_enabled": conn.ssl_enabled or False,
        "ssl_ca_cert": conn.ssl_ca_cert,
    }

    # ── 用户角色检测（仅 MySQL） ──
    # 共享 chat.py 同一份 grant_detector 缓存
    if conn.db_type == "mysql":
        from app.engine.grant_detector import (
            detect_mysql_role,
            get_cached_role,
            set_cached_role,
        )

        cached = get_cached_role(connection_id, trace_id=trace_id)
        if cached is not None:
            config["user_role"] = cached
            logger.debug("角色取自缓存",
                         connection_id=connection_id, role=cached,
                         trace_id=trace_id)
        else:
            role = await detect_mysql_role(
                host=conn.host,
                port=conn.port,
                user=conn.user,
                password=password or "",
                database=conn.database,
                ssl_enabled=conn.ssl_enabled or False,
                ssl_ca_cert=conn.ssl_ca_cert,
                trace_id=trace_id,
            )
            set_cached_role(connection_id, role, trace_id=trace_id)
            config["user_role"] = role
            logger.info("角色来自实时检测",
                        connection_id=connection_id, role=role,
                        trace_id=trace_id)
    else:
        config["user_role"] = "readonly"

    return config


# =============================================================================
# SSE 流引擎：故障排查（Agent 图驱动）
# =============================================================================


async def _troubleshoot_stream(
    connection_id: str,
    body: TroubleshootRequest,
) -> AsyncGenerator[str, None]:
    """故障排查 SSE 流——由 LangGraph Agent 图自主决策工具调用。

    不再硬编码 check_connections → check_locks → check_replication，
    而是将排查任务交给 Agent，LLM 根据上下文灵活选择排查策略。
    """
    stream_id = f"troubleshoot_{connection_id}"
    cancel_event = asyncio.Event()
    _active_streams[stream_id] = cancel_event
    current_task = asyncio.current_task()
    if current_task is not None:
        _running_tasks[stream_id] = current_task

    # 根据 issue_type 构建排查指令
    issue_prompts: dict[str, str] = {
        "connection": "帮我检查数据库连接状态",
        "lock": "帮我检查数据库锁等待情况",
        "replication": "帮我检查数据库主从复制状态",
        "auto": "帮我排查数据库故障，依次检查连接状态、锁等待、主从复制情况",
    }
    user_message = issue_prompts.get(
        body.issue_type or "auto",
        f"帮我排查数据库问题：{body.issue_type}",
    )

    logger.info("故障排查 SSE 开始（Agent 图驱动）",
                connection_id=connection_id,
                issue_type=body.issue_type,
                stream_id=stream_id)

    try:
        async with async_session_factory() as db:
            precheck_trace_id = f"role_{uuid4().hex[:12]}"
            conn_config = await _resolve_conn_config(
                connection_id, body.password, db,
                trace_id=precheck_trace_id,
            )
            logger.info("连接配置已解析",
                        connection_id=connection_id,
                        db_type=conn_config.get("db_type"),
                        user_role=conn_config.get("user_role", "N/A"),
                        trace_id=precheck_trace_id)

            # ── 构建 AgentState + 执行 LangGraph 图 ──
            from app.agent.graph import build_agent_graph

            run_id = f"run_{uuid4().hex[:12]}"
            initial_state: AgentState = {
                "messages": [HumanMessage(content=user_message)],
                "sse_events": [],
                "user_message": user_message,
                "connection_id": connection_id,
                "session_id": body.session_id,
                "password": body.password,
                "conversation_history": None,
                "conn_config": conn_config,
                "run_id": run_id,
                "trace_iterations": [],
            }

            graph = build_agent_graph()
            emitted_count = 0

            async for state in graph.astream(initial_state, stream_mode="values"):
                if cancel_event.is_set():
                    yield format_sse({
                        "type": "result",
                        "summary": "故障排查已被取消",
                        "agent_run_id": run_id,
                    })
                    yield format_sse({
                        "type": "done",
                        "session_id": body.session_id or "",
                        "agent_run_id": run_id,
                    })
                    return

                sse_events: list[dict[str, Any]] = state.get("sse_events", [])  # type: ignore[assignment]
                for msg in sse_events[emitted_count:]:
                    if msg:
                        yield format_sse(msg)
                emitted_count = len(sse_events)

                if state.get("is_complete"):
                    break

            # done 事件
            trace_iterations = state.get("trace_iterations", [])
            yield format_sse({
                "type": "done",
                "session_id": body.session_id or "",
                "tokens_used": 0,
                "agent_run_id": run_id,
                "total_iterations": len(trace_iterations),
            })

            logger.info("故障排查 SSE 完成（Agent 图驱动）",
                        connection_id=connection_id, stream_id=stream_id,
                        iterations=len(trace_iterations))

    except Exception as exc:
        logger.error("故障排查 SSE 异常", connection_id=connection_id,
                      error=str(exc)[:200])
        yield format_sse({
            "type": "error",
            "error_code": "AGENT_ERROR",
            "user_message": "故障排查异常中断，请稍后重试",
            "severity": "error",
        })
        yield format_sse({
            "type": "done",
            "session_id": body.session_id or "",
        })
    finally:
        _active_streams.pop(stream_id, None)
        _running_tasks.pop(stream_id, None)


# =============================================================================
# REST 端点
# =============================================================================


@router.post("/api/connections/{connection_id}/troubleshoot")
async def troubleshoot_stream(
    connection_id: str,
    body: TroubleshootRequest = Body(...),  # noqa: B008
) -> StreamingResponse:
    """触发故障排查，Agent 自主决策工具调用，SSE 流返回结果。

    Args:
        connection_id: 目标数据库连接 ID。
        body: TroubleshootRequest 请求体。

    Returns:
        StreamingResponse（Content-Type: text/event-stream）。
    """
    logger.info("故障排查请求开始", connection_id=connection_id,
                issue_type=body.issue_type,
                has_context=bool(body.context))

    return StreamingResponse(
        _troubleshoot_stream(connection_id, body),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
