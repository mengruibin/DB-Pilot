"""
ConnectionConfig ORM 模型。

对应数据库表 `connections`，存储用户配置的目标数据库连接信息。
依据 AGENTS.md §安全与合规红线：password 字段不入库，仅内存持有。
字段定义依据 api-contract §2.1 ConnectionConfig 实体。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ConnectionConfigModel(Base):
    """目标数据库连接配置 ORM 模型。

    注意：password 字段不存在于此模型中——密码仅在请求中传递，存于内存。
    依据 AGENTS.md §安全与合规红线：密码不写入磁盘。

    对应表：connections，字段与 api-contract §2.1 ConnectionConfig 一致。
    """

    __tablename__ = "connections"

    # 主键：conn_<8hex> 格式，避免自增 ID 枚举攻击
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # 连接名称，1-64 字符
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # 数据库类型：mysql / postgresql / oracle
    db_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # 主机地址（IP 或域名）
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    # 端口号，1-65535
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    # 目标数据库名
    database: Mapped[str] = mapped_column(String(128), nullable=False)
    # 连接用户名
    user: Mapped[str] = mapped_column(String(128), nullable=False)
    # 注意：没有 password 列——SAFETY: 密码不落盘（AGENTS.md §安全与合规红线）
    # 是否启用 SSL/TLS 加密连接
    ssl_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # SSL CA 证书 PEM 格式内容（可选）
    ssl_ca_cert: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # 额外连接参数（JSON 格式，sa.JSON 兼容 MySQL/SQLite/PostgreSQL）
    extra_params: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        default=None,
    )
    # 记录创建时间
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    # 记录更新时间
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )
    # 最后测试时间
    last_tested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
    )
    # 连接状态：unknown / healthy / unreachable / degraded，默认 unknown
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unknown",
    )

    def __repr__(self) -> str:
        """调试用字符串表示，不暴露敏感信息。"""
        return f"<ConnectionConfig id={self.id!r} name={self.name!r} db_type={self.db_type!r}>"
