"""
Alembic 迁移环境配置（异步在线 + 离线模式）。

从 settings.DATABASE_URL 读取实际连接串（支持 mysql+aiomysql 或 sqlite+aiosqlite）。
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.config import settings as app_settings
from app.database import Base

# Alembic Config 对象
config = context.config

# 配置日志（仅在 alembic.ini 包含日志配置时）
if config.config_file_name is not None:
    with suppress(KeyError):
        fileConfig(config.config_file_name)

# 元数据目标
target_metadata = Base.metadata


def _get_database_url() -> str:
    """获取数据库连接串（优先 settings，回退 alembic.ini）。"""
    url = app_settings.DATABASE_URL
    if not url:
        url = config.get_main_option("sqlalchemy.url", "")
    assert url, "DATABASE_URL 未配置（检查 .env 或 alembic.ini）"
    return url


def run_migrations_offline() -> None:
    """离线模式：仅生成 SQL 脚本，不连接数据库。"""
    url = _get_database_url()
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
    """异步在线模式：从 settings 获取 URL 并执行迁移。"""
    url = _get_database_url()
    engine = create_async_engine(url)

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
