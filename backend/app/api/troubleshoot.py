"""
故障排查 SSE 流 API 路由。

端点（api-contract §1.5）：
  - POST /api/connections/{id}/troubleshoot  SSE 流式故障排查

依据 api-contract §1.5 POST /api/connections/{id}/troubleshoot、§2.6 TroubleshootResult。
依据 AGENTS.md §安全与合规红线（密码不落盘）。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Body, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools.troubleshoot import TroubleshootWorkflow

# 复用 B-20 的 SSE 取消机制
from app.api.chat import _active_streams, _running_tasks  # type: ignore[attr-defined]  # noqa: F811
from app.database import async_session_factory
from app.models.connection import ConnectionConfigModel
from app.models.schemas import TroubleshootRequest
from app.models.session import MessageModel, SessionModel

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["Troubleshoot"])


# =============================================================================
# SSE 格式化
# =============================================================================


def _format_sse(data: dict[str, Any]) -> str:
    """将 dict 格式化为 SSE 消息（与 chat.py 格式一致）。"""
    return f"event: message\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# =============================================================================
# 辅助：从 ORM 加载连接配置
# =============================================================================


async def _resolve_conn_config(
    connection_id: str,
    password: str | None,
    session: AsyncSession,
) -> dict[str, Any]:
    """从 ORM 加载连接配置，组装为工具调用参数字典。

    Args:
        connection_id: 连接 ID。
        password: 前端传入的连接密码。
        session: 内部 SQLite 数据库会话。

    Returns:
        包含 db_type, host, port, database, user, password 等字段的 dict。

    Raises:
        HTTPException: 连接不存在时返回 404。
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

    return {
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


# =============================================================================
# SSE 流引擎：故障排查
# =============================================================================


async def _troubleshoot_stream(
    connection_id: str,
    body: TroubleshootRequest,
) -> AsyncGenerator[str, None]:
    """故障排查 SSE 流主引擎。

    1. 加载连接配置
    2. TroubleshootWorkflow 逐项排查
    3. 发送 thinking/tool_call/tool_result/skip/diagnosis 事件
    4. 若提供了 session_id，将结论写入消息历史

    Args:
        connection_id: 目标数据库连接 ID。
        body: TroubleshootRequest 请求体。

    Yields:
        SSE 格式化的事件字符串。
    """
    stream_id = f"troubleshoot_{connection_id}"
    cancel_event = asyncio.Event()

    # 注册取消标记，复用 B-20 取消机制
    _active_streams[stream_id] = cancel_event
    current_task = asyncio.current_task()
    if current_task is not None:
        _running_tasks[stream_id] = current_task

    logger.info("故障排查 SSE 开始", connection_id=connection_id,
                issue_type=body.issue_type, stream_id=stream_id)

    try:
        # Step 1: 加载连接配置
        async with async_session_factory() as db:
            conn_config = await _resolve_conn_config(
                connection_id, body.password, db,
            )

            # Step 2: 创建排查工作流
            workflow = TroubleshootWorkflow(conn_config)

            # Step 3: 迭代工作流事件
            async for event in workflow.run(issue_type=body.issue_type):
                # 检查取消信号
                if cancel_event.is_set():
                    logger.info("故障排查被取消",
                                connection_id=connection_id,
                                stream_id=stream_id)
                    yield _format_sse({
                        "type": "diagnosis",
                        "conclusion": "故障排查已被取消",
                        "severity": "skipped",
                        "suggestion": None,
                        "suggestion_is_destructive": False,
                    })
                    return

                # 收集 diagnosis 事件用于持久化
                is_diagnosis = event.get("type") == "diagnosis"

                yield _format_sse(event)

                # AC-6：若提供了 session_id，将诊断结论写入消息历史
                if is_diagnosis and body.session_id:
                    await _save_troubleshoot_result(
                        db, body.session_id, event, connection_id,
                    )

            logger.info("故障排查 SSE 完成", connection_id=connection_id,
                        stream_id=stream_id)

    except Exception as exc:
        logger.error("故障排查 SSE 异常", connection_id=connection_id,
                      error=str(exc)[:200])
        yield _format_sse({
            "type": "diagnosis",
            "conclusion": f"故障排查异常中断：{exc}",
            "severity": "error",
            "suggestion": "请稍后重试或检查数据库连接状态",
            "suggestion_is_destructive": False,
        })
    finally:
        _active_streams.pop(stream_id, None)
        _running_tasks.pop(stream_id, None)


# =============================================================================
# 持久化辅助
# =============================================================================


async def _save_troubleshoot_result(
    db: AsyncSession,
    session_id: str,
    diagnosis: dict[str, Any],
    connection_id: str,
) -> None:
    """将诊断结论保存到指定会话的消息历史。

    Args:
        db: 数据库会话。
        session_id: 目标会话 ID。
        diagnosis: 诊断事件字典。
        connection_id: 连接 ID（用于日志）。
    """
    try:
        # 验证会话存在
        result = await db.execute(
            select(SessionModel).where(SessionModel.id == session_id)
        )
        session = result.scalar_one_or_none()
        if session is None:
            logger.warning("会话不存在，跳过持久化",
                           session_id=session_id)
            return

        conclusion = diagnosis.get("conclusion", "")
        severity = diagnosis.get("severity", "info")
        content = f"[故障排查] {conclusion}（严重程度：{severity}）"

        msg = MessageModel(
            session_id=session_id,
            role="assistant",
            content=content,
            message_type="troubleshoot",
            result_preview={
                "conclusion": conclusion,
                "severity": severity,
                "suggestion": diagnosis.get("suggestion"),
                "suggestion_is_destructive": diagnosis.get(
                    "suggestion_is_destructive", False,
                ),
            },
        )
        db.add(msg)

        session.message_count = SessionModel.message_count + 1
        session.last_active_at = datetime.now(UTC)
        await db.commit()

        logger.info("诊断结论已写入会话", session_id=session_id,
                    severity=severity)
    except Exception as exc:
        logger.error("诊断结论持久化失败",
                     session_id=session_id, error=str(exc)[:200])


# =============================================================================
# REST 端点
# =============================================================================


@router.post("/api/connections/{connection_id}/troubleshoot")
async def troubleshoot_stream(
    connection_id: str,
    body: TroubleshootRequest = Body(...),  # noqa: B008
) -> StreamingResponse:
    """触发故障排查工作流，返回 SSE 流。

    按 check_connections → check_locks → check_replication 顺序排查，
    逐条推送 thinking → tool_call → tool_result / skip → diagnosis 事件。

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
