"""添加 reasoning_content 列到 messages 表（持久化 LLM 推理/思考过程）。

新增字段:
  - reasoning_content: Text 列，可选。记录 LLM 深度推理/思考过程的完整文本。
    由 SSE 流式的 reasoning_content 块拼接而成，会话切换后仍保留在思考面板中。

日期: 2026-07-12
前置: 004_add_agent_trace
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "005_add_reasoning_content"
down_revision: str | None = "004_add_agent_trace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """添加 reasoning_content 列到 messages 表。"""
    op.add_column(
        "messages",
        sa.Column(
            "reasoning_content",
            mysql.LONGTEXT(charset="utf8mb4", collation="utf8mb4_bin"),
            nullable=True,
            comment="LLM 推理/思考过程全文：由 SSE 流式 reasoning_content 块拼接而成",
        ),
    )


def downgrade() -> None:
    """移除 reasoning_content 列。"""
    op.drop_column("messages", "reasoning_content")
