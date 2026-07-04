"""
Session 与 Message ORM 模型。

对应数据库表 `sessions` 和 `messages`，存储用户对话会话及消息记录。
字段定义依据 api-contract §2.2 Session / Message 实体。
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import suppress
from datetime import UTC, datetime

import structlog
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func, select
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, async_session_factory

logger = structlog.get_logger(__name__)


class SessionModel(Base):
    """对话会话 ORM 模型。

    对应表：sessions
    当会话空闲超过 30 分钟后，status 自动变为 "closed"（由 B-24 后台任务处理）。
    """

    __tablename__ = "sessions"

    # 主键：sess_<8hex> 格式
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True,
        default=lambda: f"sess_{uuid.uuid4().hex[:8]}",
    )
    # 关联的目标数据库连接 ID
    connection_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, default=None,
    )
    # 会话标题，由首条用户消息自动截取前 30 字符
    title: Mapped[str] = mapped_column(
        String(128), nullable=False, default="新会话",
    )
    # 会话创建时间
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    # 最后活跃时间
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    # 会话状态：active / idle / closed
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active",
    )
    # 消息总数（冗余字段，避免 COUNT 查询）
    message_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    # 累计 token 消耗
    tokens_used_total: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )

    # ORM 关系（不映射为列）
    messages: Mapped[list[MessageModel]] = relationship(
        back_populates="session", cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Session id={self.id!r} status={self.status!r}>"


class MessageModel(Base):
    """消息 ORM 模型。

    对应表：messages
    result_preview 仅存储最多 20 行预览数据（契约 §2.2 约束）。
    """

    __tablename__ = "messages"

    # 主键：msg_<8hex> 格式
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True,
        default=lambda: f"msg_{uuid.uuid4().hex[:8]}",
    )
    # 关联的会话 ID（外键，级联删除）
    # SAFETY: CASCADE DELETE 确保会话删除时消息自动清理
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # 消息角色：user / assistant / system
    role: Mapped[str] = mapped_column(
        String(16), nullable=False,
    )
    # 消息内容（纯文本）
    content: Mapped[str] = mapped_column(
        Text, nullable=False, default="",
    )
    # 消息类型：natural_language / sql / diagnosis / troubleshoot / health_check
    message_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="natural_language",
    )
    # LLM 生成的原始 SQL
    sql_generated: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None,
    )
    # 实际执行的 SQL（可能经改写）
    sql_executed: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None,
    )
    # 查询结果预览（JSON，最多 20 行），AGENTS.md §数据隐私
    result_preview: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, default=None,
    )
    # 错误信息：{"error_code": "...", "user_message": "..."}
    error_info: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, default=None,
    )
    # 消息创建时间
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    # 本条消息的 token 消耗
    tokens_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    # Agent 决策轨迹（JSON 字符串，仅 assistant 消息）
    # 记录每轮 ReAct 迭代的 reasoning/tool_calls/results/safety_checks
    # 格式：{"run_id":"run_xxx","total_iterations":3,"iterations":[...]}
    # 存储为 Text 类型（数据库不支持原生 JSON），应用层负责序列化/反序列化
    agent_trace: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None,
    )

    # ORM 关系
    session: Mapped[SessionModel] = relationship(
        back_populates="messages",
    )

    def __repr__(self) -> str:
        return f"<Message id={self.id!r} role={self.role!r} type={self.message_type!r}>"


# =============================================================================
# SessionManager：会话生命周期管理（B-24）
# =============================================================================


class SessionManager:
    """会话生命周期管理器。

    负责后台扫描并关闭超时空闲会话（30min 无活跃）。
    依据 PRD §8.2：会话空闲 30 分钟后自动 status = "closed"。
    依据 AGENTS.md §安全与合规红线：会话超时清理。
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        """启动后台空闲会话清理任务（由 app lifespan 调用）。"""
        self._stop_event.clear()
        self._task = asyncio.create_task(self._cleanup_loop())
        logger.info("SessionManager 已启动")

    async def stop(self) -> None:
        """停止后台清理任务（由 app lifespan 调用）。"""
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        logger.info("SessionManager 已停止")

    async def _cleanup_loop(self) -> None:
        """后台循环：每 60s 扫描一次空闲会话（AC-1）。"""
        while not self._stop_event.is_set():
            try:
                await self._close_idle_sessions()
                # 等待 60s，或被 stop_event 中断
                await asyncio.wait_for(self._stop_event.wait(), timeout=60)
            except TimeoutError:
                continue  # 超时 = 60s 到达，继续下一轮扫描
            except Exception:
                logger.exception("SessionManager 清理循环异常")
                await asyncio.sleep(60)

    async def _close_idle_sessions(self) -> None:
        """查询并关闭超时空闲会话（AC-2）。

        扫描 `status="active"` 且 `last_active_at < NOW() - 30min` 的会话，
        更新 status 为 "closed"。
        """
        from datetime import timedelta

        cutoff = datetime.now(UTC) - timedelta(minutes=30)

        async with async_session_factory() as db:
            try:
                result = await db.execute(
                    select(SessionModel).where(
                        SessionModel.status == "active",
                        SessionModel.last_active_at < cutoff,
                    )
                )
                sessions = result.scalars().all()

                for s in sessions:
                    s.status = "closed"
                    s.last_active_at = datetime.now(UTC)

                await db.commit()

                if sessions:
                    # AC-6：可观测性——记录清理数量和 session ID 列表
                    logger.info("空闲会话已清理", count=len(sessions),
                                ids=[s.id for s in sessions])
                else:
                    logger.debug("空闲会话扫描完毕，无超时会话")
            except Exception:
                await db.rollback()
                logger.exception("空闲会话清理异常")
