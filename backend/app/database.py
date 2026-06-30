"""
DB-Pilot 异步 SQLAlchemy 引擎与会话管理。

为内部 SQLite 数据库提供异步引擎、会话工厂和 Base 声明基类。
内部 SQLite 数据库文件位于 backend/data/ 目录（AGENTS.md §安全与合规红线）。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import NullPool, event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# 确保 data/ 目录存在
_data_dir = Path(__file__).resolve().parent.parent.parent / "data"
_data_dir.mkdir(exist_ok=True)


# 异步引擎：使用 NullPool 避免 SQLite 连接池冲突
# 依据 AGENTS.md §技术栈约束：所有 DB I/O 必须原生异步
engine = create_async_engine(
    settings.DATABASE_URL,
    poolclass=NullPool,
    echo=False,  # 生产环境关闭 SQL 回显
    connect_args={"check_same_thread": False},  # SQLite 允许多线程访问
)


# 启用 SQLite 外键约束（PRAGMA foreign_keys = ON）
# SAFETY: 外键确保 session 删除时级联清理 message，防止孤儿数据
@event.listens_for(engine.sync_engine, "connect")
def _enable_sqlite_fk(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

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
