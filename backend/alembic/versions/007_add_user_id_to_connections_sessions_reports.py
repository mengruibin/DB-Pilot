"""为 connections / sessions / reports 表添加 user_id 列。

实现按用户维度的数据隔离（RBAC）：
- admin 用户可查看所有数据
- 普通用户只可查看自己创建的数据（user_id = 当前用户）
- 历史遗留数据的 user_id 为 NULL，仅管理员可见
- 用户删除后其数据的 user_id 被置为 NULL（ON DELETE SET NULL），不级联删除

Revision ID: 007_add_user_id
Revises: fe9deb6eba99
Create Date: 2026-07-22
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "007_add_user_id"
down_revision: str | None = "fe9deb6eba99"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """为三张数据表添加 user_id 列（nullable，兼容历史数据）。"""
    # connections 表：关联创建此连接的用户
    op.add_column(
        "connections",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
            comment="创建此连接的用户 ID（NULL = 历史遗留数据）",
        ),
    )
    # sessions 表：关联创建此会话的用户
    op.add_column(
        "sessions",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
            comment="创建此会话的用户 ID（NULL = 历史遗留数据）",
        ),
    )
    # reports 表：关联创建此报告的用户
    op.add_column(
        "reports",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
            comment="创建此报告的用户 ID（NULL = 历史遗留数据）",
        ),
    )


def downgrade() -> None:
    """回滚：删除三张表的 user_id 列。"""
    op.drop_column("reports", "user_id")
    op.drop_column("sessions", "user_id")
    op.drop_column("connections", "user_id")
