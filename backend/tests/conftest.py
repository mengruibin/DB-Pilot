"""
pytest 共享 Fixtures。

包含 mysql_test_config fixture，从环境变量 TEST_MYSQL_URL 读取测试数据库配置。
若未配置，所有集成测试自动 SKIP。
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from app.models.schemas import ConnectionCreateRequest


def _parse_mysql_url(url: str) -> dict[str, Any]:
    """解析 mysql://user:password@host:port/database 格式 URL。

    Args:
        url: MySQL 连接 URL。

    Returns:
        {host, port, user, password, database} 字典。
    """
    # 去除协议前缀
    rest = url.removeprefix("mysql://").removeprefix("MySQL://")

    # 分离认证信息 @ 主机部分
    user_pass, host_part = rest.split("@", 1) if "@" in rest else ("root", rest)

    # 解析用户名和密码
    user, password = user_pass.split(":", 1) if ":" in user_pass else (user_pass, "")

    # 解析主机、端口、数据库
    if "/" in host_part:
        host_port, database = host_part.split("/", 1)
    else:
        host_port, database = host_part, "test"

    if ":" in host_port:
        host, port_str = host_port.split(":", 1)
        port = int(port_str)
    else:
        host = host_port
        port = 3306

    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": database,
    }


@pytest.fixture(scope="session")
def mysql_test_config() -> ConnectionCreateRequest | None:
    """从 TEST_MYSQL_URL 环境变量加载 MySQL 测试配置。

    若 TEST_MYSQL_URL 未设置，返回 None（测试用例应 SKIP）。
    URL 格式：mysql://user:password@host:port/database
    示例：mysql://root:secret@127.0.0.1:3306/test

    Returns:
        ConnectionCreateRequest 或 None。
    """
    url = os.environ.get("TEST_MYSQL_URL")
    if not url:
        pytest.skip("TEST_MYSQL_URL not configured — 跳过 MySQL 集成测试")
        return None

    params = _parse_mysql_url(url)
    return ConnectionCreateRequest(
        name="test_mysql",
        db_type="mysql",  # type: ignore[arg-type] — Pydantic validator handles str→DBType
        host=params["host"],
        port=params["port"],
        database=params["database"],
        user=params["user"],
        password=params["password"],
    )
