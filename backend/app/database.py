"""
DB-Pilot 异步 SQLAlchemy 引擎与会话管理。

为内部数据库（MySQL/SQLite）提供异步引擎、会话工厂和 Base 声明基类。
数据库连接串通过 settings.DATABASE_URL 从环境变量读取。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# 异步引擎（依据 AGENTS.md §技术栈约束：所有 DB I/O 必须原生异步）
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,  # 生产环境关闭 SQL 回显
)

# 异步会话工厂
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """SQLAlchemy 声明基类，所有 ORM 模型继承此类。"""


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖注入：获取异步数据库会话。"""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
