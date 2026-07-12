"""
SSE 对话流 API 路由。

核心端点 POST /api/chat/stream：
  1. 接收用户消息 + 会话管理 + 连接解析
  2. 构建 AgentState → 执行 LangGraph ReAct Agent 图
  3. LLM 自主决定工具调用 → 图自动路由 agent ↔ tools（ReAct 循环）
  4. 通过 SSE 流式推送 token/tool_call/tool_result/sql/done 事件
  5. 双通道 streaming：messages（用户可见 token 流）+ updates（系统状态增量）

变更（2026-07-04）：
  移除了意图分类步骤（classify_node），Agent 入口直接为 agent_node。
  message_type 改为从 Agent 实际工具调用事后推断。

依据 api-contract §1.2 POST /api/chat/stream（7 种 type 契约表）。
依据 AGENTS.md §SSE 流式格式、§安全与合规红线（密码不落盘）。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.sse_utils import format_sse
from app.agent.state import AgentState
from app.correlation import set_connection_id
from app.database import async_session_factory, get_session
from app.models.connection import ConnectionConfigModel
from app.models.schemas import (
    ChatRequest,
    ConnectionCreateRequest,
    MessageListResponse,
    MessageResponse,
    SessionListResponse,
    SessionRenameRequest,
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
    recent = messages[-(max_messages + 1) : -1]
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
        result = await db.execute(select(SessionModel).where(SessionModel.id == session_id))
        session = result.scalar_one_or_none()
        if session:
            # 更新活跃时间
            session.last_active_at = datetime.now(UTC)
            await db.commit()
            logger.info("续接会话", session_id=session.id, connection_id=connection_id)
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
    logger.info("创建新会话", session_id=session.id, connection_id=connection_id, title=title)
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
    reasoning_content: str | None = None,
    thinking_steps: str | None = None,
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
        agent_trace: Agent 决策轨迹 JSON。
        reasoning_content: LLM 推理/思考过程全文。
        thinking_steps: 思考步骤 JSON 数组（tool_call/tool_result/sql）。

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
        reasoning_content=reasoning_content,
        thinking_steps=thinking_steps,
    )
    db.add(msg)

    # 更新会话的消息计数和活跃时间
    result = await db.execute(select(SessionModel).where(SessionModel.id == session_id))
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
    trace_id: str = "",
) -> dict[str, Any]:
    """从 ORM 读取连接配置，组装为工具调用参数。

    Args:
        db: 数据库会话。
        connection_id: 连接 ID。
        password: 前端传入的连接密码。
        trace_id: 请求追踪 ID（用于角色检测日志链路）。

    Returns:
        包含 db_type, host, port, database, user, password 等字段的 dict。
    """
    result = await db.execute(
        select(ConnectionConfigModel).where(ConnectionConfigModel.id == connection_id)
    )
    conn = result.scalar_one_or_none()
    if conn is None:
        raise ValueError(f"连接 {connection_id} 不存在")

    # SAFETY: 密码仅存于内存，不持久化（AGENTS.md §安全与合规红线）
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
        "extra_params": conn.extra_params,
    }

    # ── 用户角色检测（仅 MySQL，其他数据库暂默认 standard） ──
    # 通过 SHOW GRANTS 自动判定数据库用户实际拥有多少权限
    if conn.db_type == "mysql":
        from app.engine.grant_detector import (
            detect_mysql_role,
            get_cached_role,
            set_cached_role,
        )

        cached = get_cached_role(connection_id, trace_id=trace_id)
        if cached is not None:
            config["user_role"] = cached
            logger.debug(
                "角色取自缓存", connection_id=connection_id, role=cached, trace_id=trace_id
            )
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
            logger.info(
                "角色来自实时检测", connection_id=connection_id, role=role, trace_id=trace_id
            )
    else:
        config["user_role"] = "standard"

    return config


# =============================================================================
# 工具编排——已迁移至 LangGraph Agent 图
#
# 旧 _execute_tool_chain() 函数 (~460行) 已于 2026-07-02 删除。
# 所有工具编排逻辑现在由 app/agent/graph.py 中的 LangGraph StateGraph 处理：
#   agent_node ↔ tools_node（ReAct 循环）→ END
#
# 2026-07-08：stream_mode 从 "values" 改为 "updates"，
#   双通道 streaming（messages + updates），移除 thinking/result 事件。
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
                col_index: dict[str, int] = {c.lower(): i for i, c in enumerate(cols)}
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
                                    f"KILL QUERY {thread_id}",
                                    params={},
                                )
                                killed_count += 1
                                logger.info(
                                    "MySQL KILL QUERY 成功", db_type="mysql", thread_id=thread_id
                                )
                            except Exception as exc:
                                logger.warning(
                                    "MySQL KILL QUERY 失败",
                                    thread_id=thread_id,
                                    error=str(exc)[:200],
                                )
                    logger.info("MySQL KILL QUERY 完成", killed_count=killed_count)
                else:
                    logger.warning("MySQL PROCESSLIST 列名异常", columns=cols)
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
                logger.info("PostgreSQL pg_cancel_backend 完成", cancelled_count=cancelled)
            except Exception as exc:
                logger.warning("PostgreSQL KILL 异常", error=str(exc)[:200])

        elif db_type == "oracle":
            # Oracle: 无法自动获取 SID/SERIAL#，仅记录提示
            logger.info(
                "Oracle KILL QUERY 跳过：需要 SID/SERIAL#，依赖连接关闭后自动回滚", db_type="oracle"
            )

        await adapter.disconnect()
        logger.info("KILL 管理连接已关闭", db_type=db_type)

    except Exception as exc:
        logger.warning(
            "KILL DB 查询失败（网络/权限），连接关闭时将自动回滚",
            db_type=db_type,
            host=host,
            error=str(exc)[:200],
        )


# =============================================================================
# SSE 流引擎
# =============================================================================


def _infer_message_type(state: dict[str, Any]) -> str:
    """根据 Agent 实际调用的工具推断消息类型（事后标注，比预分类更准确）。

    从 trace_iterations 中读取 Agent 实际调用的工具名，映射为 message_type。
    仅用于数据库记录和统计，不影响功能逻辑。

    映射规则：
      - 未调用任何工具 → "general"
      - 调用 run_health_check → "health_check"
      - 调用锁/连接/复制相关工具 → "troubleshoot"
      - 调用 explain/慢查询相关工具 → "diagnosis"
      - 调用 query 相关工具 → "query"
      - 其他 → "general"
    """
    trace_entries = state.get("trace_iterations", [])
    tools_called: set[str] = set()
    for entry in trace_entries:
        for tc in entry.get("tool_calls", []):
            tools_called.add(tc["name"])

    if not tools_called:
        return "general"
    if "run_health_check" in tools_called:
        return "health_check"
    if tools_called & {"check_locks", "check_connections", "check_replication"}:
        return "troubleshoot"
    if tools_called & {"explain_query", "get_slow_queries"}:
        return "diagnosis"
    if tools_called & {"execute_sql", "list_tables", "describe_table"}:
        return "query"
    return "general"


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
            # 记录流开始时间，用于 done 事件的 total_duration_ms 字段
            stream_start_time = time.monotonic()
            # ========== Step 1: 会话管理 ==========
            session = await _get_or_create_session(
                db,
                body.connection_id,
                body.session_id,
                body.message,
            )
            set_connection_id(body.connection_id)

            # 持久化用户消息
            await _save_message(
                db,
                session.id,
                "user",
                body.message,
                message_type=body.mode,
            )

            # ── 获取会话历史（最近 10 条，用于记忆上下文） ──
            conversation_history = await _build_conversation_history(
                db,
                session.id,
                max_messages=10,
            )

            # ========== Step 2: 解析连接配置 ==========
            precheck_trace_id = f"role_{uuid4().hex[:12]}"
            conn_config = await _resolve_connection_config(
                db,
                body.connection_id,
                body.password,
                trace_id=precheck_trace_id,
            )
            logger.info(
                "连接配置已解析",
                connection_id=body.connection_id,
                db_type=conn_config.get("db_type"),
                user_role=conn_config.get("user_role", "N/A"),
                trace_id=precheck_trace_id,
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
                "conversation_history": conversation_history,
                "conn_config": conn_config,
                "run_id": run_id,
                "trace_iterations": [],
            }

            graph = build_agent_graph()
            # accumulated_state：累积各节点状态增量（final_answer、trace_iterations 等）
            # 不同于 "values" 模式的完整状态，"updates" 模式仅返回每个节点的增量
            accumulated_state: dict[str, Any] = {}
            # sse_events 去重计数器：每个节点返回的是全量 sse_events 列表
            # （agent_node/tools_node 从 state 读取全量历史、追加新事件后返回全量），
            # 用 emitted_count 追踪已发射事件数，避免重复发射
            emitted_count = 0
            # 乐观渲染 + 收编：当前 agent 迭代是否看到 tool_call_chunks
            # False → 暂不知类型，content 以 stage="thinking" 乐观渲染
            # True  → 确认工具调用迭代，content 保持 thinking 无需变更
            # is_complete 时仍为 False → 最终回答，发送 stage_change("answer") 触发前端收编
            seen_tool_calls = False
            # reasoning_content 累积器：逐 chunk 收集 LLM 深度推理文本，
            # 流结束后统一保存到数据库（持久化思考过程）
            reasoning_content_parts: list[str] = []
            # 当前 iteration 的 reasoning block 累积器，
            # 在 tool_call_chunks 或 agent node 完成时 flush 到 thinking_steps，
            # 确保每段 reasoning 出现在正确的时间位置
            current_reasoning_block: list[str] = []
            # thinking_steps 累积器：收集 tool_call / tool_result / sql 事件，
            # 保留所有字段用于会话切换后重建思考面板
            thinking_steps_accumulator: list[dict[str, Any]] = []

            # 使用 stream_mode=["updates", "messages"] 双通道流式：
            # "updates" → 每个节点执行后的状态增量 {node_name: state_delta}
            # "messages" → (AIMessageChunk, metadata) 逐 token 流
            # config 传入 thread_id 用于 MemorySaver checkpoint 追踪
            # 注意：LangGraph astream 在联合 stream_mode 时可能返回 2 元组 (mode, data)
            # 或 3 元组 (namespace, mode, data)，取决于是否有子图
            async for stream_item in graph.astream(
                initial_state,
                stream_mode=["updates", "messages"],
                config={"configurable": {"thread_id": session.id}},
            ):
                # ── 解析 astream 输出（兼容 2 元组和 3 元组） ──
                if len(stream_item) == 3:
                    _namespace, mode, data = stream_item
                else:
                    mode, data = stream_item

                # ── 处理 LLM Token 流（乐观渲染 + 收编） ──
                if mode == "messages":
                    if isinstance(data, tuple) and len(data) >= 2:
                        chunk = data[0]
                        meta = data[1] if isinstance(data[1], dict) else {}
                        node_name = str(meta.get("langgraph_node", "") or "")
                        if node_name == "agent":
                            # 1) reasoning_content → 独立的 reasoning SSE 事件
                            reasoning = (
                                chunk.additional_kwargs.get("reasoning_content", "")
                                if isinstance(chunk.additional_kwargs, dict)
                                else ""
                            )
                            if reasoning.strip():
                                yield format_sse(
                                    {
                                        "type": "reasoning",
                                        "content": reasoning,
                                        "agent_run_id": run_id,
                                    }
                                )
                                # 累积 reasoning_content 用于持久化
                                reasoning_content_parts.append(reasoning)
                                # 同时累积到当前 iteration block，用于按边界切分
                                current_reasoning_block.append(reasoning)

                            # 2) tool_call_chunks → 确认工具调用迭代
                            #    触发点 A：首次出现时刷入 pre-tool reasoning block
                            tool_call_chunks = getattr(chunk, "tool_call_chunks", None) or []
                            if tool_call_chunks and not seen_tool_calls:
                                seen_tool_calls = True
                                if current_reasoning_block:
                                    thinking_steps_accumulator.append(
                                        {
                                            "type": "reasoning",
                                            "content": "".join(current_reasoning_block),
                                        }
                                    )
                                    current_reasoning_block = []

                            # 3) content → 乐观渲染为 stage="thinking"
                            content = str(getattr(chunk, "content", "") or "")
                            if content.strip():
                                yield format_sse(
                                    {
                                        "type": "token",
                                        "stage": "thinking",
                                        "content": content,
                                        "agent_run_id": run_id,
                                    }
                                )
                    continue

                # ── mode == "updates"：处理节点状态增量 ──
                # "updates" 模式返回 {node_name: state_delta}，
                # 每个 delta 仅包含该节点返回的字段（增量而非全量）
                # data 在此分支中为 dict[str, dict]（非 str/tuple）
                updates: dict[str, dict[str, Any]] = data  # type: ignore[assignment]
                completed = False
                for node_name, node_output in updates.items():
                    # 检查取消信号（在每个节点增量处理后检查）
                    if cancel_event.is_set():
                        logger.info(
                            "SSE 流被取消（cancel_event 触发）",
                            session_id=session.id,
                            node=node_name,
                        )
                        yield format_sse(
                            {
                                "type": "done",
                                "session_id": session.id,
                                "tokens_used": total_tokens,
                                "agent_run_id": run_id,
                                "total_duration_ms": 0,
                            }
                        )
                        return

                    # 发射本节点的 SSE 事件（tool_call / tool_result / sql / error）
                    # 注意：节点返回的是全量 sse_events 列表（含历史事件），
                    # 必须用 emitted_count 去重，只发射新增部分
                    sse_events: list[dict[str, Any]] = node_output.get("sse_events", [])
                    for msg in sse_events[emitted_count:]:
                        if msg:
                            yield format_sse(msg)
                            # 累积 tool_call / tool_result / sql 用于持久化重建思考面板
                            if msg.get("type") in ("tool_call", "tool_result", "sql"):
                                thinking_steps_accumulator.append(msg)
                    emitted_count = len(sse_events)

                    # 累积状态增量（跳过 messages 和 sse_events：
                    # messages 由 LangGraph add_messages reducer 管理，
                    # sse_events 已即时发射无需累积）
                    for key, value in node_output.items():
                        if key in ("sse_events", "messages"):
                            continue
                        accumulated_state[key] = value

                    logger.info(
                        "节点执行完成",
                        node=node_name,
                        run_id=run_id,
                        delta_keys=list(node_output.keys()),
                        sse_event_count=len(sse_events),
                    )

                    # 重置每个 agent 迭代的标志位
                    # 触发点 B：agent node 完成时 flush 剩余的 reasoning block
                    # 处理两种情况：
                    #   - 无工具调用迭代（纯推理或最终回答）
                    #   - 工具调用迭代中 pre-tool flush 后的残余 reasoning
                    if node_name == "agent":
                        if current_reasoning_block:
                            thinking_steps_accumulator.append(
                                {
                                    "type": "reasoning",
                                    "content": "".join(current_reasoning_block),
                                }
                            )
                            current_reasoning_block = []
                        seen_tool_calls = False

                    # agent_node 最终回答/超上限/工具绑定失败时设置 is_complete
                    if node_output.get("is_complete"):
                        # 乐观渲染收编：未检测到 tool_calls → 最终回答
                        # 发送 stage_change("answer") 触发前端将 stage="thinking"
                        # 的 text 消息收编为 stage="answer"
                        if not seen_tool_calls:
                            yield format_sse(
                                {
                                    "type": "stage_change",
                                    "stage": "answer",
                                    "agent_run_id": run_id,
                                }
                            )
                        completed = True
                        break

                if completed:
                    break

            # ========== Step 4: 保存助手消息 ==========
            # 不再拼接 result 事件摘要（result 事件已移除），
            # 直接使用 agent_node 设置的 final_answer
            assistant_content = accumulated_state.get("final_answer") or "已完成"
            # 构建 Agent Trace（B-31 可观测性）
            trace_iterations = accumulated_state.get("trace_iterations", [])
            agent_trace = (
                json.dumps(
                    {
                        "run_id": run_id,
                        "total_iterations": len(trace_iterations),
                        "iterations": trace_iterations,
                    },
                    ensure_ascii=False,
                )
                if trace_iterations
                else None
            )
            # 拼接完整的 reasoning_content 用于持久化
            full_reasoning = "".join(reasoning_content_parts) if reasoning_content_parts else None
            # 序列化 thinking_steps 用于持久化重建思考面板
            thinking_steps_json = (
                json.dumps(thinking_steps_accumulator, ensure_ascii=False)
                if thinking_steps_accumulator
                else None
            )

            await _save_message(
                db,
                session.id,
                "assistant",
                assistant_content,
                message_type=_infer_message_type(accumulated_state),
                agent_trace=agent_trace,
                reasoning_content=full_reasoning,
                thinking_steps=thinking_steps_json,
            )

            # ========== Step 5: done 事件 ==========
            total_duration_ms = int((time.monotonic() - stream_start_time) * 1000)
            yield format_sse(
                {
                    "type": "done",
                    "session_id": session.id,
                    "tokens_used": total_tokens,
                    "agent_run_id": run_id,
                    "total_iterations": len(accumulated_state.get("trace_iterations", [])),
                    "total_duration_ms": total_duration_ms,  # 整个 SSE 流的总耗时（毫秒）
                }
            )

    except asyncio.CancelledError:
        # B-20 AC-2：asyncio.Task.cancel() 触发 → 捕获 CancelledError
        logger.info(
            "SSE 流任务被取消（CancelledError）", session_id=session.id if session else None
        )
        yield format_sse(
            {
                "type": "done",
                "session_id": session.id if session else "",
                "tokens_used": total_tokens,
                "agent_run_id": locals().get("run_id", ""),
                "total_duration_ms": 0,
            }
        )
        return

    except Exception as exc:
        logger.error("SSE 流异常", error=str(exc)[:200])
        yield format_sse(
            {
                "type": "error",
                "error_code": "AGENT_ERROR",
                "user_message": "AI 服务暂时不可用，请稍后重试",
                "severity": "error",
            }
        )
        if session:
            yield format_sse(
                {
                    "type": "done",
                    "session_id": session.id,
                    "tokens_used": total_tokens,
                    "total_duration_ms": 0,
                }
            )
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
    logger.info(
        "SSE 请求开始",
        connection_id=body.connection_id,
        session_id=body.session_id or "(新会话)",
        message_length=len(body.message),
    )

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
        logger.info("取消 Step 1/5 完成：SSE 流取消标记已设置", session_id=session_id)
    else:
        logger.warning(
            "取消 Step 1/5 跳过：未找到活跃 SSE 流取消标记（会话可能已结束）", session_id=session_id
        )

    # ========== Step 2: 取消 asyncio Task ==========
    # AC-2：调用 asyncio.Task.cancel() 取消当前 session 的 Agent 协程
    logger.info("取消 Step 2/5：取消 Agent 协程（task.cancel()）", session_id=session_id)
    task = _running_tasks.pop(session_id, None)
    if task is not None and not task.done():
        task.cancel()
        logger.info("取消 Step 2/5 完成：Agent 协程已取消", session_id=session_id)
    else:
        logger.warning(
            "取消 Step 2/5 跳过：未找到活跃的 Agent 协程（可能已结束或从未启动）",
            session_id=session_id,
        )

    # ========== Step 3: KILL QUERY ==========
    # AC-3：若工具已在目标数据库执行 SQL，发送 KILL QUERY <connection_id>
    logger.info("取消 Step 3/5：检查是否需要 KILL QUERY", session_id=session_id)
    conn_info = _session_adapter_info.pop(session_id, None)
    if conn_info and conn_info.get("db_type"):
        await _kill_db_query(conn_info)
        logger.info(
            "取消 Step 3/5 完成：KILL QUERY 已发送",
            session_id=session_id,
            db_type=conn_info["db_type"],
        )
    else:
        logger.info("取消 Step 3/5 跳过：无运行中的工具需要 KILL", session_id=session_id)

    # ========== Step 4: 更新 Session 状态 + 回滚 ==========
    # AC-4：回滚未提交事务（KILL 或连接断开时目标 DB 自动回滚）
    # AC-5：更新 Session status 为 "closed"
    logger.info("取消 Step 4/5：更新 Session 状态 + 回滚", session_id=session_id)
    async with async_session_factory() as db:
        try:
            result = await db.execute(select(SessionModel).where(SessionModel.id == session_id))
            session = result.scalar_one_or_none()
            if session:
                session.status = "closed"
                session.last_active_at = datetime.now(UTC)
                await db.commit()
                logger.info(
                    "取消 Step 4/5 完成：Session 状态已更新为 closed", session_id=session_id
                )
            else:
                logger.warning("取消 Step 4/5 跳过：Session 不存在", session_id=session_id)
        except Exception as exc:
            await db.rollback()
            logger.error(
                "取消 Step 4/5 异常：Session 状态更新失败",
                session_id=session_id,
                error=str(exc)[:200],
            )

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
    status: str | None = Query(default=None, description="按状态筛选（active / idle / closed）"),
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(
        default=20, ge=1, le=100, alias="pageSize", description="每页条数（最大 100）"
    ),
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
    logger.info(
        "API 请求开始",
        endpoint="list_sessions",
        connection_id=connection_id,
        status=status,
        page=page,
    )

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
        query.order_by(SessionModel.last_active_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await session.execute(stmt)
    sessions = result.scalars().all()

    items = [SessionResponse.model_validate(s) for s in sessions]

    logger.info("API 请求完成", endpoint="list_sessions", total=total, returned=len(items))
    return SessionListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@sessions_router.get("/api/sessions/{session_id}/messages")
async def list_session_messages(
    session_id: str,
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(
        default=50, ge=1, le=200, alias="pageSize", description="每页条数（最大 200）"
    ),
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
    logger.info("API 请求开始", endpoint="list_session_messages", session_id=session_id, page=page)

    # 验证会话存在（AC-5：跨会话隔离的基础）
    sess_result = await db_session.execute(
        select(SessionModel).where(SessionModel.id == session_id)
    )
    session = sess_result.scalar_one_or_none()
    if session is None:
        logger.warning(
            "API 请求失败",
            endpoint="list_session_messages",
            session_id=session_id,
            reason="not_found",
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "NOT_FOUND",
                "user_message": f"会话 {session_id} 不存在或已删除",
            },
        )

    # AC-5：WHERE session_id = :id 天然保证跨会话隔离
    count_query = (
        select(func.count()).select_from(MessageModel).where(MessageModel.session_id == session_id)
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

    logger.info(
        "API 请求完成",
        endpoint="list_session_messages",
        session_id=session_id,
        total=total,
        returned=len(items),
    )
    return MessageListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@sessions_router.patch("/api/sessions/{session_id}")
async def rename_session(
    session_id: str,
    body: SessionRenameRequest,
    db_session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Any:
    """重命名会话标题（B1）。

    更新会话的 title 和 last_active_at 时间戳。

    Args:
        session_id: 会话 ID。
        body: 请求体，包含新的 title。
        db_session: 数据库会话。

    Returns:
        SessionResponse（更新后的完整会话对象）。

    Raises:
        HTTPException 404: 会话不存在。
    """
    # AC-6：入口日志
    logger.info("API 请求开始", endpoint="rename_session", session_id=session_id, title=body.title)

    # 查找会话
    result = await db_session.execute(select(SessionModel).where(SessionModel.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        logger.warning(
            "API 请求失败", endpoint="rename_session", session_id=session_id, reason="not_found"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "NOT_FOUND",
                "user_message": f"会话 {session_id} 不存在或已删除",
            },
        )

    # 更新标题和最后活跃时间
    session.title = body.title
    session.last_active_at = datetime.now(UTC)

    await db_session.commit()
    await db_session.refresh(session)

    logger.info("API 请求完成", endpoint="rename_session", session_id=session_id, title=body.title)
    return SessionResponse.model_validate(session)


@sessions_router.delete("/api/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    db_session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """删除指定会话及其所有关联消息（B2）。

    消息通过外键 ON DELETE CASCADE 自动删除。
    如果该会话正在活跃流式处理中，先取消流再删除。

    Args:
        session_id: 会话 ID。
        db_session: 数据库会话。

    Raises:
        HTTPException 404: 会话不存在。
    """
    # AC-6：入口日志
    logger.info("API 请求开始", endpoint="delete_session", session_id=session_id)

    # 如果在活跃流中，先取消
    if session_id in _active_streams:
        logger.info("删除活跃流会话，先取消流", session_id=session_id)
        _active_streams[session_id].set()

    # 查找会话
    result = await db_session.execute(select(SessionModel).where(SessionModel.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        logger.warning(
            "API 请求失败", endpoint="delete_session", session_id=session_id, reason="not_found"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "NOT_FOUND",
                "user_message": f"会话 {session_id} 不存在或已删除",
            },
        )

    # 删除会话（消息通过 CASCADE 自动删除）
    await db_session.delete(session)
    await db_session.commit()

    logger.info("API 请求完成", endpoint="delete_session", session_id=session_id)
