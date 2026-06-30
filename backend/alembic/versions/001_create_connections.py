"""创建 connections 表

依据 api-contract §2.1 ConnectionConfig 实体定义。
注意：password 字段不在 ORM 模型中（AGENTS.md §安全与合规红线：密码不落盘）。

Revision ID: 001_create_connections
Revises: None
Create Date: 2026-06-30
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001_create_connections"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """创建 connections 表。

    字段定义严格遵循 api-contract §2.1 ConnectionConfig 实体。
    password 字段不存在于此表中——仅内存持有，不入库。
    """
    op.create_table(
        "connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, comment="连接名称"),
        sa.Column("db_type", sa.String(16), nullable=False, comment="数据库类型"),
        sa.Column("host", sa.String(255), nullable=False, comment="主机地址"),
        sa.Column("port", sa.Integer, nullable=False, comment="端口"),
        sa.Column("database", sa.String(128), nullable=False, comment="数据库名"),
        sa.Column("user", sa.String(128), nullable=False, comment="用户名"),
        # 注意：没有 password 列
        sa.Column(
            "ssl_enabled", sa.Boolean, nullable=False, server_default="0",
            comment="是否启用 SSL",
        ),
        sa.Column(
            "ssl_ca_cert", sa.Text, nullable=True, default=None,
            comment="SSL CA 证书 PEM",
        ),
        sa.Column(
            "extra_params", sa.JSON, nullable=True, default=None,
            comment="额外连接参数",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False, comment="创建时间",
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False, comment="更新时间",
        ),
        sa.Column(
            "last_tested_at", sa.DateTime(timezone=True), nullable=True,
            default=None, comment="最后测试时间",
        ),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="unknown",
            comment="连接状态",
        ),
    )


def downgrade() -> None:
    """回滚：删除 connections 表。"""
    op.drop_table("connections")
