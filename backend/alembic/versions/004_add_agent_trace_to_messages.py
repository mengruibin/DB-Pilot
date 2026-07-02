"""添加 agent_trace 列到 messages 表（B-31 Agent 可观测性）。

新增字段:
  - agent_trace: JSON 列，可选。记录每次 Agent 运行的完整决策轨迹，
    格式 {"run_id":"...","total_iterations":3,"iterations":[...]}

日期: 2026-07-02
前置: 003_create_reports
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "004_add_agent_trace"
down_revision: str | None = "003_create_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """添加 agent_trace JSON 列到 messages 表。"""
    op.add_column(
        "messages",
        sa.Column(
            "agent_trace",
            mysql.LONGTEXT(charset="utf8mb4", collation="utf8mb4_bin"),
            nullable=True,
            comment="Agent 决策轨迹 JSON: {run_id, total_iterations, iterations:[{...}]}",
        ),
    )


def downgrade() -> None:
    """移除 agent_trace 列。"""
    op.drop_column("messages", "agent_trace")
