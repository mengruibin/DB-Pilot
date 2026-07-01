"""创建 reports 表

依据 api-contract §2.5 HealthReport 实体定义。
- reports 表：存储健康巡检报告（评分、分类检查项列表）
- categories 列以 JSON 格式存储完整的检查项嵌套结构
- severity_counts 列以 JSON 格式存储严重级别计数

Revision ID: 003_create_reports
Revises: 002_create_sessions_messages
Create Date: 2026-06-30
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "003_create_reports"
down_revision: str | None = "002_create_sessions_messages"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """创建 reports 表。"""
    op.create_table(
        "reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "connection_id", sa.String(36), nullable=False, index=True,
            comment="关联的目标数据库连接 ID",
        ),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="completed",
            comment="报告状态：completed / cancelled / partial",
        ),
        sa.Column("score", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "generated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "duration_sec", sa.Float, nullable=False, server_default="0.0",
        ),
        sa.Column(
            "severity_counts", sa.JSON, nullable=True,
            comment="严重级别计数 JSON：{error, warning, pass, skipped}",
        ),
        sa.Column(
            "categories", sa.JSON, nullable=True,
            comment="检查项分类列表 JSON",
        ),
    )


def downgrade() -> None:
    """回滚：删除 reports 表。"""
    op.drop_table("reports")
