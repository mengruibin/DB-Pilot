"""创建 sessions 和 messages 表

依据 api-contract §2.2 Session / Message 实体定义。
- sessions 表：对话会话元数据
- messages 表：消息记录，session_id -> sessions.id 外键级联删除

Revision ID: 002_create_sessions_messages
Revises: 001_create_connections
Create Date: 2026-06-30
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002_create_sessions_messages"
down_revision: str | None = "001_create_connections"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """创建 sessions 和 messages 表。"""

    # ---- sessions 表 ----
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("connection_id", sa.String(36), nullable=True),
        sa.Column("title", sa.String(128), nullable=False, server_default="新会话"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "last_active_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="active",
            comment="会话状态：active / idle / closed",
        ),
        sa.Column(
            "message_count", sa.Integer, nullable=False, server_default="0",
        ),
        sa.Column(
            "tokens_used_total", sa.Integer, nullable=False, server_default="0",
        ),
    )

    # ---- messages 表 ----
    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id", sa.String(36),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column(
            "role", sa.String(16), nullable=False,
            comment="消息角色：user / assistant / system",
        ),
        sa.Column("content", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "message_type", sa.String(32), nullable=False,
            server_default="natural_language",
            comment="消息类型：natural_language / sql / diagnosis / troubleshoot / health_check",
        ),
        sa.Column("sql_generated", sa.Text, nullable=True),
        sa.Column("sql_executed", sa.Text, nullable=True),
        sa.Column(
            "result_preview", sa.JSON, nullable=True,
            comment="查询结果预览 JSON（最多 20 行）",
        ),
        sa.Column(
            "error_info", sa.JSON, nullable=True,
            comment="错误信息 JSON：{error_code, user_message}",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("tokens_used", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    """回滚：删除 messages 和 sessions 表。"""
    op.drop_table("messages")
    op.drop_table("sessions")
