"""
User ORM 模型。

对应数据库表 `users`，存储 DB-Pilot 平台用户账号。
用户角色（admin/readonly）决定其执行 SQL 的权限范围（由安全链路控制）。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UserModel(Base):
    """平台用户 ORM 模型。

    对应表：users
    - role: admin 可执行写 SQL，readonly 仅读
    - is_active: 管理员可禁用用户
    - password_hash: bcrypt 哈希（不与前端明文密码混淆）
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True,
        default=lambda: f"usr_{uuid.uuid4().hex[:8]}",
    )
    username: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True,
    )
    password_hash: Mapped[str] = mapped_column(
        String(256), nullable=False,
    )
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default="readonly",
        comment="用户角色：admin / readonly",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
        comment="是否启用",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    def __repr__(self) -> str:
        """调试用字符串表示，不暴露密码哈希。"""
        return f"<User id={self.id!r} username={self.username!r} role={self.role!r}>"
