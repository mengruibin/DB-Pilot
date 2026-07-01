"""
健康巡检报告 ORM 模型。

对应数据库表 `reports`，存储健康巡检的完整结果。
字段定义依据 api-contract §2.5 HealthReport。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ReportModel(Base):
    """健康巡检报告 ORM 模型。

    对应表：reports
    存储每次健康巡检的完整结果（评分、分类检查项列表等）。
    类别数据以 JSON 格式存储（categories 列），包含完整的检查项嵌套结构。
    """

    __tablename__ = "reports"

    # 主键：rpt_<8hex> 格式
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True,
        default=lambda: f"rpt_{uuid.uuid4().hex[:8]}",
    )
    # 关联的目标数据库连接 ID
    connection_id: Mapped[str] = mapped_column(
        String(36), nullable=False, index=True,
    )
    # 报告状态：completed / cancelled / partial
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="completed",
        comment="报告状态：completed / cancelled / partial",
    )
    # 健康评分（0-100）
    score: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    # 报告生成时间
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    # 巡检总耗时（秒）
    duration_sec: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0,
    )
    # 严重级别计数：{error: N, warning: N, pass: N, skipped: N}
    severity_counts: Mapped[dict[str, int] | None] = mapped_column(
        JSON, nullable=True, default=None,
        comment="严重级别计数 JSON：{error, warning, pass, skipped}",
    )
    # 检查项分类列表 JSON（含每项的完整结构）
    categories: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True, default=None,
        comment="检查项分类列表 JSON",
    )

    def __repr__(self) -> str:
        return (f"<Report id={self.id!r} status={self.status!r} "
                f"score={self.score}>")
