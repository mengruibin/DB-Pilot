"""
SSE 对话流 API 路由。

核心端点 POST /api/chat/stream：
  1. 接收用户消息 + 会话管理 + 连接解析
  2. 构建 AgentState → 执行 LangGraph ReAct Agent 图
  3. LLM 自主决定工具调用 → 图自动路由 classify → agent ↔ tools
  4. 通过 SSE 流式推送 thinking/tool_call/tool_result/sql/result/done 事件

依据 api-contract §1.2 POST /api/chat/stream（7 种 type 契约表）。
依据 AGENTS.md §SSE 流式格式、§安全与合规红线（密码不落盘）。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.sse_utils import format_sse
from app.agent.state import AgentState, Intent
from app.correlation import set_connection_id
from langchain_core.messages import HumanMessage
from app.database import async_session_factory, get_session
from app.models.connection import ConnectionConfigModel
from app.models.schemas import (
    ChatRequest,
    ConnectionCreateRequest,
    MessageListResponse,
    MessageResponse,
    SessionListResponse,
    SessionResponse,
)
from app.models.session import MessageModel, SessionModel

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/chat", tags=["Chat"])

# 活跃的 SSE 流取消标记（session_id → asyncio.Event）
# 用于 POST /api/chat/cancel 取消指定 session 的活跃流
_active_streams: dict[str, asyncio.Event] = {}
# 活跃的 SSE 流 asyncio Task（session_id → asyncio.Task）
# 用于 POST /api/chat/cancel 调用 task.cancel() 强制中断正在执行的工具调用
_running_tasks: dict[str, asyncio.Task] = {}
# 活跃会话的目标数据库适配器连接信息（session_id → dict）
# 用于取消时执行 KILL QUERY 和回滚
_session_adapter_info: dict[str, dict[str, Any]] = {}


# =============================================================================
# 通用工具函数
# =============================================================================

async def _build_conversation_history(
    db: AsyncSession,
    session_id: str,
    max_messages: int = 10,
) -> str | None:
    """构建会话历史文本，用于 LLM 上下文记忆。

    从数据库中读取当前会话的前序消息，格式化为：
      用户：xxx
      助手：xxx

    Args:
        db: 数据库会话。
        session_id: 当前会话 ID。
        max_messages: 最多读取的消息条数。

    Returns:
        格式化后的对话历史字符串；无历史时返回 None。
    """
    result = await db.execute(
        select(MessageModel)
        .where(MessageModel.session_id == session_id)
        .order_by(MessageModel.created_at.asc())
    )
    messages = result.scalars().all()

    if len(messages) <= 1:
        # 只有当前一条消息（刚建会话），无历史上下文
        return None

    # 取最近 max_messages 条（不含最新一条——当前用户消息已写入但还没处理）
    recent = messages[-(max_messages + 1):-1]
    if not recent:
        return None

    lines: list[str] = []
    for msg in recent:
        role_label = "用户" if msg.role == "user" else "助手"
        # 截断过长的消息体，避免撑爆 LLM 上下文
        content = msg.content[:200] + ("..." if len(msg.content) > 200 else "")
        lines.append(f"{role_label}：{content}")

    return "\n".join(lines)


# =============================================================================
# 会话管理
# =============================================================================

async def _get_or_create_session(
    db: AsyncSession,
    connection_id: str,
    session_id: str | None,
    message: str,
) -> SessionModel:
    """获取已有会话或创建新会话。

    Args:
        db: 数据库会话。
        connection_id: 目标数据库连接 ID。
        session_id: 已有会话 ID（None 表示新建）。
        message: 用户消息，新建时用于生成 title。

    Returns:
        SessionModel 实例。
    """
    if session_id:
        result = await db.execute(
            select(SessionModel).where(SessionModel.id == session_id)
        )
        session = result.scalar_one_or_none()
        if session:
            # 更新活跃时间
            session.last_active_at = datetime.now(UTC)
            await db.commit()
            logger.info("续接会话", session_id=session.id,
                        connection_id=connection_id)
            return session

    # 创建新会话
    title = message.strip()[:30] + ("..." if len(message.strip()) > 30 else "")
    session = SessionModel(
        connection_id=connection_id,
        title=title or "新会话",
        status="active",
        message_count=0,
        tokens_used_total=0,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    logger.info("创建新会话", session_id=session.id,
                connection_id=connection_id, title=title)
    return session


async def _save_message(
    db: AsyncSession,
    session_id: str,
    role: str,
    content: str,
    message_type: str = "natural_language",
    sql_generated: str | None = None,
    sql_executed: str | None = None,
    result_preview: dict | None = None,
    error_info: dict | None = None,
    tokens_used: int = 0,
    agent_trace: str | None = None,
) -> MessageModel:
    """保存消息到数据库。

    Args:
        db: 数据库会话。
        session_id: 会话 ID。
        role: 角色（user / assistant / system）。
        content: 消息内容。
        message_type: 消息类型。
        sql_generated: LLM 生成的原始 SQL。
        sql_executed: 实际执行的 SQL。
        result_preview: 结果预览（JSON，最多 20 行）。
        error_info: 错误信息。
        tokens_used: token 消耗。

    Returns:
        MessageModel 实例。
    """
    msg = MessageModel(
        session_id=session_id,
        role=role,
        content=content,
        message_type=message_type,
        sql_generated=sql_generated,
        sql_executed=sql_executed,
        result_preview=result_preview,
        error_info=error_info,
        tokens_used=tokens_used,
        agent_trace=agent_trace,
    )
    db.add(msg)

    # 更新会话的消息计数和活跃时间
    result = await db.execute(
        select(SessionModel).where(SessionModel.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session:
        # FIXED: 使用实例属性自增，而非类属性（类属性永远是默认值0）
        session.message_count = (session.message_count or 0) + 1
        session.last_active_at = datetime.now(UTC)
        session.tokens_used_total = (session.tokens_used_total or 0) + tokens_used

    await db.commit()
    await db.refresh(msg)
    return msg


# =============================================================================
# 连接配置解析
# =============================================================================

async def _resolve_connection_config(
    db: AsyncSession,
    connection_id: str,
    password: str | None,
) -> dict[str, Any]:
    """从 ORM 读取连接配置，组装为工具调用参数。

    Args:
        db: 数据库会话。
        connection_id: 连接 ID。
        password: 前端传入的连接密码。

    Returns:
        包含 db_type, host, port, database, user, password 等字段的 dict。
    """
    result = await db.execute(
        select(ConnectionConfigModel).where(
            ConnectionConfigModel.id == connection_id
        )
    )
    conn = result.scalar_one_or_none()
    if conn is None:
        raise ValueError(f"连接 {connection_id} 不存在")

    # SAFETY: 密码仅存于内存，不持久化（AGENTS.md §安全与合规红线）
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
        "extra_params": conn.extra_params,
    }


# =============================================================================
# 工具编排——已迁移至 LangGraph Agent 图
#
# 旧 _execute_tool_chain() 函数 (~460行) 已于 2026-07-02 删除。
# 所有工具编排逻辑现在由 app/agent/graph.py 中的 LangGraph StateGraph 处理：
#   classify_node → route → agent_node ↔ tools_node → format_response
# =============================================================================

async def _kill_db_query(conn_info: dict[str, Any]) -> None:
    """取消目标数据库上的活跃查询（KILL QUERY）。

    根据数据库类型执行对应的取消/杀查询操作：
    - MySQL: SHOW PROCESSLIST 查找 → KILL QUERY <thread_id>
    - PostgreSQL: pg_cancel_backend(<pid>)
    - Oracle: 不自动杀查询（需 SID/SERIAL#），仅记录日志

    创建独立的管理连接执行 KILL，不干扰原工具适配器连接。
    依据 api-contract §1.2 POST /api/chat/cancel：若工具已在目标数据库
    执行 SQL，发送 KILL QUERY。

    Args:
        conn_info: 连接配置信息（db_type, host, port, database, user, password 等）。
    """
    db_type = conn_info.get("db_type", "")
    host = conn_info.get("host", "")
    port = conn_info.get("port", 3306)
    database = conn_info.get("database", "")
    user = conn_info.get("user", "")
    password = conn_info.get("password", "")
    ssl_enabled = conn_info.get("ssl_enabled", False)
    ssl_ca_cert = conn_info.get("ssl_ca_cert")

    from app.db.factory import AdapterFactory

    # 构建连接配置，用于创建管理适配器
    config = ConnectionCreateRequest(
        name="_kill_temp",
        db_type=db_type,
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        ssl_enabled=ssl_enabled,
        ssl_ca_cert=ssl_ca_cert,
    )

    try:
        # SAFETY: 创建独立短生命周期适配器用于 KILL，用后即释放
        adapter = AdapterFactory.create(db_type, config)
        await adapter.connect(config)

        if db_type == "mysql":
            # MySQL: 查找当前用户的活跃查询并按线程 ID 逐个 KILL
            try:
                result = await adapter.execute("SHOW PROCESSLIST", params={})
                cols = result.get("columns", [])
                rows = result.get("rows", [])

                # 构建列名→索引映射，兼容不同 MySQL 版本
                col_index: dict[str, int] = {
                    c.lower(): i for i, c in enumerate(cols)
                }
                id_idx = col_index.get("id", -1)
                user_idx = col_index.get("user", -1)
                cmd_idx = col_index.get("command", -1)

                if id_idx >= 0 and user_idx >= 0:
                    killed_count = 0
                    for row in rows:
                        if id_idx >= len(row) or user_idx >= len(row):
                            continue
                        # 只杀当前用户 + 非 Sleep 的线程
                        is_our_user = row[user_idx] == user
                        is_sleep = cmd_idx >= 0 and cmd_idx < len(row) and row[cmd_idx] == "Sleep"
                        if is_our_user and not is_sleep:
                            thread_id = row[id_idx]
                            try:
                                await adapter.execute(
                                    f"KILL QUERY {thread_id}", params={},
                                )
                                killed_count += 1
                                logger.info("MySQL KILL QUERY 成功",
                                            db_type="mysql", thread_id=thread_id)
                            except Exception as exc:
                                logger.warning("MySQL KILL QUERY 失败",
                                               thread_id=thread_id,
                                               error=str(exc)[:200])
                    logger.info("MySQL KILL QUERY 完成",
                                killed_count=killed_count)
                else:
                    logger.warning("MySQL PROCESSLIST 列名异常",
                                   columns=cols)
            except Exception as exc:
                logger.warning("MySQL KILL 异常", error=str(exc)[:200])

        elif db_type == "postgresql":
            # PostgreSQL: 取消当前用户的活跃查询
            try:
                result = await adapter.execute(
                    "SELECT pg_cancel_backend(pid) "
                    "FROM pg_stat_activity "
                    "WHERE usename = $1 "
                    "  AND state = 'active' "
                    "  AND pid <> pg_backend_pid()",
                    params={"username": user},
                )
                cancelled = len(result.get("rows", []))
                logger.info("PostgreSQL pg_cancel_backend 完成",
                            cancelled_count=cancelled)
            except Exception as exc:
                logger.warning("PostgreSQL KILL 异常", error=str(exc)[:200])

        elif db_type == "oracle":
            # Oracle: 无法自动获取 SID/SERIAL#，仅记录提示
            logger.info("Oracle KILL QUERY 跳过：需要 SID/SERIAL#，"
                        "依赖连接关闭后自动回滚", db_type="oracle")

        await adapter.disconnect()
        logger.info("KILL 管理连接已关闭", db_type=db_type)

    except Exception as exc:
        logger.warning("KILL DB 查询失败（网络/权限），连接关闭时将自动回滚",
                       db_type=db_type, host=host, error=str(exc)[:200])


# =============================================================================
# SSE 流引擎
# =============================================================================

async def _stream_events(
    body: ChatRequest,
) -> AsyncGenerator[str, None]:
    """SSE 事件流主引擎——异步生成器，逐条 yield SSE 格式化字符串。

    执行流程（PRD §4.2 Agent 工作流 6 步）：
      Step 1: 创建/续接 Session，持久化用户消息
      Step 2: Intent Router 分类
      Step 3: 路由到对应引擎（工具编排）
      Step 4: 调用工具，流式推送事件
      Step 5: 格式化响应
      Step 6: 保存助手消息，发送 done

    Args:
        body: ChatRequest 请求体。

    Yields:
        SSE 格式化的事件字符串。
    """
    session: SessionModel | None = None
    assistant_content_parts: list[str] = []
    total_tokens = 0
    cancel_event = asyncio.Event()

    # 注册取消标记 + 追踪当前 asyncio Task（B-20：用于 task.cancel()）
    if body.session_id:
        _active_streams[body.session_id] = cancel_event
        current = asyncio.current_task()
        if current is not None:
            _running_tasks[body.session_id] = current

    db: AsyncSession | None = None
    try:
        # 创建独立数据库会话（SSE 流期间不能持有 FastAPI 注入的依赖事务）
        async with async_session_factory() as db:
            # ========== Step 1: 会话管理 ==========
            session = await _get_or_create_session(
                db, body.connection_id, body.session_id, body.message,
            )
            set_connection_id(body.connection_id)

            # 持久化用户消息
            await _save_message(
                db, session.id, "user", body.message,
                message_type=body.mode,
            )

            # ── 获取会话历史（最近 10 条，用于记忆上下文） ──
            conversation_history = await _build_conversation_history(
                db, session.id, max_messages=10,
            )

            # ========== Step 2: 解析连接配置 ==========
            conn_config = await _resolve_connection_config(
                db, body.connection_id, body.password,
            )

            # 存储适配器连接信息，供取消时 KILL QUERY（AC-3）
            if body.session_id:
                _session_adapter_info[body.session_id] = {
                    "db_type": conn_config["db_type"],
                    "host": conn_config["host"],
                    "port": conn_config["port"],
                    "database": conn_config["database"],
                    "user": conn_config["user"],
                    "password": conn_config.get("password", ""),
                    "ssl_enabled": conn_config.get("ssl_enabled", False),
                    "ssl_ca_cert": conn_config.get("ssl_ca_cert"),
                }

            # ========== Step 3: 构建 AgentState + 执行 LangGraph 图 ==========
            # 引入图构建
            from app.agent.graph import build_agent_graph

            run_id = f"run_{uuid4().hex[:12]}"
            initial_state: AgentState = {
                "messages": [HumanMessage(content=body.message)],
                "sse_events": [],
                "user_message": body.message,
                "connection_id": body.connection_id,
                "session_id": session.id,
                "password": body.password,
                "user_role": "standard",
                "conversation_history": conversation_history,
                "conn_config": conn_config,
                "run_id": run_id,
                "trace_iterations": [],
            }

            graph = build_agent_graph()
            emitted_count = 0

            # 使用 stream_mode="values" 获取每次节点执行后的完整状态
            async for state in graph.astream(initial_state, stream_mode="values"):
                # 检查取消信号
                if cancel_event.is_set():
                    logger.info("SSE 流被取消（cancel_event 触发）",
                                session_id=session.id)
                    yield format_sse({
                        "type": "done",
                        "session_id": session.id,
                        "tokens_used": total_tokens,
                        "agent_run_id": run_id,
                    })
                    return

                # 发射新增的 SSE 事件
                sse_events: list[dict[str, Any]] = state.get("sse_events", [])  # type: ignore[assignment]
                for msg in sse_events[emitted_count:]:
                    if msg:
                        yield format_sse(msg)
                        # 收集助手回复片段
                        if msg.get("type") == "result":
                            assistant_content_parts.append(
                                msg.get("summary", "")
                            )
                emitted_count = len(sse_events)

                # 检查图是否执行完毕
                if state.get("is_complete"):
                    break

            # ========== Step 4: 保存助手消息 ==========
            assistant_content = "\n".join(filter(None, assistant_content_parts))
            if not assistant_content:
                assistant_content = state.get("final_answer") or "已完成"
            # 构建 Agent Trace（B-31 可观测性）
            trace_iterations = state.get("trace_iterations", [])
            agent_trace = json.dumps({
                "run_id": run_id,
                "total_iterations": len(trace_iterations),
                "iterations": trace_iterations,
            }, ensure_ascii=False) if trace_iterations else None

            await _save_message(
                db, session.id, "assistant", assistant_content,
                message_type=state.get("intent", Intent.GENERAL).value.lower(),
                agent_trace=agent_trace,
            )

            # ========== Step 5: done 事件 ==========
            yield format_sse({
                "type": "done",
                "session_id": session.id,
                "tokens_used": total_tokens,
                "agent_run_id": run_id,
                "total_iterations": len(state.get("trace_iterations", [])),
            })

    except asyncio.CancelledError:
        # B-20 AC-2：asyncio.Task.cancel() 触发 → 捕获 CancelledError
        logger.info("SSE 流任务被取消（CancelledError）",
                    session_id=session.id if session else None)
        yield format_sse({
            "type": "done",
            "session_id": session.id if session else "",
            "tokens_used": total_tokens,
            "agent_run_id": run_id if 'run_id' in dir() else "",
        })
        return

    except Exception as exc:
        logger.error("SSE 流异常", error=str(exc)[:200])
        yield format_sse({
            "type": "error",
            "error_code": "AGENT_ERROR",
            "user_message": "AI 服务暂时不可用，请稍后重试",
            "severity": "error",
        })
        if session:
            yield format_sse({
                "type": "done",
                "session_id": session.id,
                "tokens_used": total_tokens,
            })
    finally:
        # 清理所有追踪标记（AC-5：回滚由 DB 连接断开时自动完成）
        if body.session_id:
            _active_streams.pop(body.session_id, None)
            _running_tasks.pop(body.session_id, None)
            _session_adapter_info.pop(body.session_id, None)
        if db:
            await db.close()
        logger.info("SSE 流结束", session_id=session.id if session else None)


# =============================================================================
# REST 端点
# =============================================================================


@router.post("/stream")
async def chat_stream(body: ChatRequest) -> StreamingResponse:
    """SSE 流式对话端点。

    接收用户消息，通过 SSE 流式返回 Agent 推理过程和执行结果。
    依据 api-contract §1.2 POST /api/chat/stream。

    Args:
        body: ChatRequest 请求体。

    Returns:
        StreamingResponse（Content-Type: text/event-stream）。
    """
    logger.info("SSE 请求开始",
                connection_id=body.connection_id,
                session_id=body.session_id or "(新会话)",
                message_length=len(body.message))

    return StreamingResponse(
        _stream_events(body),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 Nginx 缓冲
        },
    )


@router.post("/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_chat(
    session_id: str = Body(..., embed=True, description="要取消的会话 ID"),
) -> None:
    """取消进行中的 SSE 流（B-20 完整实现）。

    执行步骤（AC-1~AC-7，全程记录结构化日志）：
      1. 设置 SSE 流取消标记（AC-6：→ _stream_events 发送 done 事件）
      2. 调用 asyncio.Task.cancel() 取消 Agent 协程（AC-2）
      3. 若工具已在目标数据库执行 SQL，发送 KILL QUERY（AC-3）
      4. 回滚未提交事务（AC-4：连接断开/杀查询时自动回滚）
      5. 更新 Session status 为 "closed"（AC-5）

    Args:
        session_id: 要取消的会话 ID。

    Returns:
        204 No Content（AC-1：无响应体）。
    """
    # AC-7：取消操作全程记录结构化日志
    logger.info("====== 操作取消：开始 ======", session_id=session_id)

    # ========== Step 1: 设置 SSE 流取消标记 ==========
    # AC-6：取消标记触发 _stream_events 发送 done 事件而非 error
    logger.info("取消 Step 1/5：设置 SSE 流取消标记", session_id=session_id)
    cancel_event = _active_streams.get(session_id)
    if cancel_event:
        cancel_event.set()
        logger.info("取消 Step 1/5 完成：SSE 流取消标记已设置",
                    session_id=session_id)
    else:
        logger.warning("取消 Step 1/5 跳过：未找到活跃 SSE 流取消标记"
                       "（会话可能已结束）", session_id=session_id)

    # ========== Step 2: 取消 asyncio Task ==========
    # AC-2：调用 asyncio.Task.cancel() 取消当前 session 的 Agent 协程
    logger.info("取消 Step 2/5：取消 Agent 协程（task.cancel()）",
                session_id=session_id)
    task = _running_tasks.pop(session_id, None)
    if task is not None and not task.done():
        task.cancel()
        logger.info("取消 Step 2/5 完成：Agent 协程已取消",
                    session_id=session_id)
    else:
        logger.warning("取消 Step 2/5 跳过：未找到活跃的 Agent 协程"
                       "（可能已结束或从未启动）", session_id=session_id)

    # ========== Step 3: KILL QUERY ==========
    # AC-3：若工具已在目标数据库执行 SQL，发送 KILL QUERY <connection_id>
    logger.info("取消 Step 3/5：检查是否需要 KILL QUERY",
                session_id=session_id)
    conn_info = _session_adapter_info.pop(session_id, None)
    if conn_info and conn_info.get("db_type"):
        await _kill_db_query(conn_info)
        logger.info("取消 Step 3/5 完成：KILL QUERY 已发送",
                    session_id=session_id, db_type=conn_info["db_type"])
    else:
        logger.info("取消 Step 3/5 跳过：无运行中的工具需要 KILL",
                    session_id=session_id)

    # ========== Step 4: 更新 Session 状态 + 回滚 ==========
    # AC-4：回滚未提交事务（KILL 或连接断开时目标 DB 自动回滚）
    # AC-5：更新 Session status 为 "closed"
    logger.info("取消 Step 4/5：更新 Session 状态 + 回滚",
                session_id=session_id)
    async with async_session_factory() as db:
        try:
            result = await db.execute(
                select(SessionModel).where(SessionModel.id == session_id)
            )
            session = result.scalar_one_or_none()
            if session:
                session.status = "closed"
                session.last_active_at = datetime.now(UTC)
                await db.commit()
                logger.info("取消 Step 4/5 完成：Session 状态已更新为 closed",
                            session_id=session_id)
            else:
                logger.warning("取消 Step 4/5 跳过：Session 不存在",
                               session_id=session_id)
        except Exception as exc:
            await db.rollback()
            logger.error("取消 Step 4/5 异常：Session 状态更新失败",
                         session_id=session_id, error=str(exc)[:200])

    # 清理可能残留的追踪数据
    _active_streams.pop(session_id, None)
    _session_adapter_info.pop(session_id, None)

    logger.info("====== 操作取消：完成 ======", session_id=session_id)
    # AC-1: 返回 204 No Content（无响应体）


# =============================================================================
# 会话管理 API（B-24）
# 独立 router，无前缀，路径为 /api/sessions
# =============================================================================

sessions_router = APIRouter(tags=["Sessions"])


@sessions_router.get("/api/sessions")
async def list_sessions(
    connection_id: str | None = Query(default=None, description="按连接 ID 筛选"),
    status: str | None = Query(default=None, description='按状态筛选（active / idle / closed）'),
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize",
                           description="每页条数（最大 100）"),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Any:
    """获取会话列表（分页，AC-3）。

    支持按 connection_id 和 status 筛选。
    按最后活跃时间降序排列。

    Args:
        connection_id: 可选，按连接 ID 筛选。
        status: 可选，按状态筛选（active / idle / closed）。
        page: 页码。
        page_size: 每页条数。
        session: 数据库会话。

    Returns:
        SessionListResponse（分页会话列表）。
    """
    # AC-6：入口日志
    logger.info("API 请求开始", endpoint="list_sessions",
                connection_id=connection_id, status=status, page=page)

    # 构建查询
    query = select(SessionModel)
    count_query = select(func.count()).select_from(SessionModel)

    if connection_id:
        query = query.where(SessionModel.connection_id == connection_id)
        count_query = count_query.where(
            SessionModel.connection_id == connection_id,
        )
    if status:
        query = query.where(SessionModel.status == status)
        count_query = count_query.where(SessionModel.status == status)

    # 总数
    total_result = await session.execute(count_query)
    total = total_result.scalar() or 0

    # 分页（按 last_active_at 降序）
    stmt = (
        query
        .order_by(SessionModel.last_active_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await session.execute(stmt)
    sessions = result.scalars().all()

    items = [SessionResponse.model_validate(s) for s in sessions]

    logger.info("API 请求完成", endpoint="list_sessions",
                total=total, returned=len(items))
    return SessionListResponse(
        items=items, total=total, page=page, page_size=page_size,
    )


@sessions_router.get("/api/sessions/{session_id}/messages")
async def list_session_messages(
    session_id: str,
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=50, ge=1, le=200, alias="pageSize",
                           description="每页条数（最大 200）"),
    db_session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Any:
    """获取指定会话的消息历史（分页，AC-4, AC-5）。

    返回消息列表，result_preview 仅返回前 20 行预览数据。
    跨会话隔离由 WHERE session_id = :id 保证。

    Args:
        session_id: 会话 ID。
        page: 页码。
        page_size: 每页条数。
        db_session: 数据库会话。

    Returns:
        MessageListResponse（分页消息列表）。

    Raises:
        HTTPException 404: 会话不存在。
    """
    # AC-6：入口日志
    logger.info("API 请求开始", endpoint="list_session_messages",
                session_id=session_id, page=page)

    # 验证会话存在（AC-5：跨会话隔离的基础）
    sess_result = await db_session.execute(
        select(SessionModel).where(SessionModel.id == session_id)
    )
    session = sess_result.scalar_one_or_none()
    if session is None:
        logger.warning("API 请求失败", endpoint="list_session_messages",
                       session_id=session_id, reason="not_found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "NOT_FOUND",
                "user_message": f"会话 {session_id} 不存在或已删除",
            },
        )

    # AC-5：WHERE session_id = :id 天然保证跨会话隔离
    count_query = (
        select(func.count())
        .select_from(MessageModel)
        .where(MessageModel.session_id == session_id)
    )
    total_result = await db_session.execute(count_query)
    total = total_result.scalar() or 0

    stmt = (
        select(MessageModel)
        .where(MessageModel.session_id == session_id)
        .order_by(MessageModel.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db_session.execute(stmt)
    messages = result.scalars().all()

    # AC-4：限制 result_preview 仅返回前 20 行预览数据
    items = []
    for m in messages:
        msg_dict = m.__dict__.copy()
        # if has result_preview, limit to 20 rows
        preview = m.result_preview
        if preview and isinstance(preview, dict) and "rows" in preview:
            preview = dict(preview)
            preview["rows"] = preview["rows"][:20]
        msg_dict["result_preview"] = preview
        items.append(MessageResponse.model_validate(msg_dict))

    logger.info("API 请求完成", endpoint="list_session_messages",
                session_id=session_id, total=total, returned=len(items))
    return MessageListResponse(
        items=items, total=total, page=page, page_size=page_size,
    )
