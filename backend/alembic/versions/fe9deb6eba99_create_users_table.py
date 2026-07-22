"""创建 users 表（平台用户账号）。

新增表:
  - users: 存储 DB-Pilot 平台用户（username 唯一，password_hash 存 bcrypt）

日期: 2026-07-21
前置: 006_add_thinking_steps
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "fe9deb6eba99"
down_revision: str | None = "006_add_thinking_steps"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建 users 表。"""
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "username",
            sa.String(64),
            unique=True,
            nullable=False,
            index=True,
            comment="用户名，唯一",
        ),
        sa.Column(
            "password_hash",
            sa.String(256),
            nullable=False,
            comment="bcrypt 密码哈希",
        ),
        sa.Column(
            "role",
            sa.String(16),
            nullable=False,
            server_default="readonly",
            comment="用户角色：admin / readonly",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
            comment="是否启用",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """删除 users 表。"""
    op.drop_table("users")
