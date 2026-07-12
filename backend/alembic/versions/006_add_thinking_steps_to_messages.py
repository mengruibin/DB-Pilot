"""添加 thinking_steps 列到 messages 表（持久化思考步骤）。

新增字段:
  - thinking_steps: Text 列，可选。记录 tool_call、tool_result、sql 等思考步骤
   的完整 SSE 事件数组，用于刷新后重建思考面板。

日期: 2026-07-12
前置: 005_add_reasoning_content
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "006_add_thinking_steps"
down_revision: str | None = "005_add_reasoning_content"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """添加 thinking_steps 列到 messages 表。"""
    op.add_column(
        "messages",
        sa.Column(
            "thinking_steps",
            mysql.LONGTEXT(charset="utf8mb4", collation="utf8mb4_bin"),
            nullable=True,
            comment="Thinking phase steps (tool_call, tool_result, sql) for reconstructing thinking panel",
        ),
    )


def downgrade() -> None:
    """移除 thinking_steps 列。"""
    op.drop_column("messages", "thinking_steps")
