"""
Alembic 迁移环境配置（异步 SQLite 支持）。

需要 aiosqlite 驱动来连接 SQLite+aiosqlite URL。
内部 SQLite 数据库文件置于 backend/data/ 目录（AGENTS.md §安全与合规红线）。
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base

# Alembic Config 对象
config = context.config

# 配置日志（仅在 alembic.ini 包含日志配置时）
if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except KeyError:
        pass

# 元数据目标
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：仅生成 SQL 脚本，不连接数据库。"""
    url = config.get_main_option("sqlalchemy.url")
    assert url is not None, "alembic.ini 中缺少 sqlalchemy.url 配置"
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    """在给定连接上执行迁移。"""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """异步在线模式：创建异步引擎并执行迁移。"""
    url = config.get_main_option("sqlalchemy.url")
    assert url is not None, "alembic.ini 中缺少 sqlalchemy.url 配置"
    engine = create_async_engine(url, poolclass=None)

    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await engine.dispose()


def run_migrations_online() -> None:
    """在线模式入口：启动异步事件循环执行迁移。"""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
