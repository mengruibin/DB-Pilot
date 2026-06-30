"""
Session 与 Message ORM 模型。

对应数据库表 `sessions` 和 `messages`，存储用户对话会话及消息记录。
字段定义依据 api-contract §2.2 Session / Message 实体。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.sqlite import JSON as SQLITE_JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


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
        SQLITE_JSON, nullable=True, default=None,
    )
    # 错误信息：{"error_code": "...", "user_message": "..."}
    error_info: Mapped[dict | None] = mapped_column(
        SQLITE_JSON, nullable=True, default=None,
    )
    # 消息创建时间
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    # 本条消息的 token 消耗
    tokens_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )

    # ORM 关系
    session: Mapped[SessionModel] = relationship(
        back_populates="messages",
    )

    def __repr__(self) -> str:
        return f"<Message id={self.id!r} role={self.role!r} type={self.message_type!r}>"
