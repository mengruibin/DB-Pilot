"""连接注册表解析（EXTENSION: MCP 无头服务专用，与 Web connections 表解耦）。

环境变量：
  - DBPILOT_CONNECTIONS: JSON 对象 {"name": "dsn", ...}（多库，优先级高）
  - DBPILOT_DSN:         单库连接串（名字记为 "default"）
  - DBPILOT_DEFAULT_CONNECTION: 工具未传 connection 参数时的默认连接名

DSN 示例：mysql://root:secret@127.0.0.1:3306/shop
          postgresql://u:p@localhost:5432/shop
          oracle://scott:tiger@10.0.0.5:1521/ORCLPDB
query 参数：ssl=1|true → ssl_enabled=True；ssl_ca=<pem 路径> → 读文件为
ssl_ca_cert；其余参数并入 extra_params。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.engine.url import make_url

_SCHEME_MAP: dict[str, str] = {
    "mysql": "mysql",
    "mysql+aiomysql": "mysql",
    "postgresql": "postgresql",
    "postgres": "postgresql",
    "postgresql+asyncpg": "postgresql",
    "oracle": "oracle",
    "oracle+oracledb": "oracle",
}

_SSL_TRUE = {"1", "true", "yes", "on"}


@dataclass
class ConnectionEntry:
    """单条目标库连接（与 Web ConnectionCreateRequest 语义对齐，不落盘）。"""

    name: str
    db_type: str
    host: str
    port: int
    database: str
    user: str
    password: str
    ssl_enabled: bool = False
    ssl_ca_cert: str | None = None
    extra_params: dict[str, Any] = field(default_factory=dict)

    def to_conn_config(self, user_role: str = "readonly") -> dict[str, Any]:
        """组装 orchestrator 工具执行所需的连接配置 dict（键与 _INJECTED_KEYS 对齐）。"""
        return {
            "connection_id": f"mcp_{self.name}",
            "db_type": self.db_type,
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": self.user,
            "password": self.password,
            "ssl_enabled": self.ssl_enabled,
            "ssl_ca_cert": self.ssl_ca_cert,
            "user_role": user_role,
        }


def _parse_one(name: str, dsn: str) -> ConnectionEntry:
    url = make_url(dsn)
    scheme = (url.drivername or "").lower()
    db_type = _SCHEME_MAP.get(scheme)
    if db_type is None:
        raise ValueError(
            f"连接 '{name}' 的 scheme 不受支持: {scheme!r} "
            f"(支持 mysql/postgresql/oracle)"
        )
    if not url.host or not url.database or not url.username:
        raise ValueError(f"连接 '{name}' 的 DSN 缺少 host/database/user")

    ssl_enabled = False
    ssl_ca_cert: str | None = None
    extra: dict[str, Any] = {}
    for key, val in (url.query.items() if url.query else []):
        if key == "ssl":
            ssl_enabled = str(val).lower() in _SSL_TRUE
        elif key == "ssl_ca":
            pem = Path(val).read_text(encoding="utf-8")
            ssl_ca_cert = pem
        else:
            extra[key] = val

    return ConnectionEntry(
        name=name,
        db_type=db_type,
        host=url.host,
        port=url.port or _default_port(db_type),
        database=url.database,
        user=url.username,
        password=url.password or "",
        ssl_enabled=ssl_enabled,
        ssl_ca_cert=ssl_ca_cert,
        extra_params=extra,
    )


def _default_port(db_type: str) -> int:
    return {"mysql": 3306, "postgresql": 5432, "oracle": 1521}[db_type]


def parse_connections_env(
    env: Mapping[str, str] | None = None,
) -> dict[str, ConnectionEntry]:
    """从环境变量解析连接注册表（纯函数，便于测试）。

    Args:
        env: 环境变量映射；None 时读 os.environ。

    Returns:
        {连接名: ConnectionEntry}，按 DBPILOT_CONNECTIONS > DBPILOT_DSN 解析。

    Raises:
        ValueError: DSN 非法 / scheme 不支持 / ssl_ca 文件不存在。
    """
    env = dict(env if env is not None else os.environ)
    raw_json = (env.get("DBPILOT_CONNECTIONS") or "").strip()
    raw_single = (env.get("DBPILOT_DSN") or "").strip()

    registry: dict[str, ConnectionEntry] = {}
    if raw_json:
        payload = json.loads(raw_json)
        if not isinstance(payload, dict):
            raise ValueError("DBPILOT_CONNECTIONS 必须是 JSON 对象")
        for name, dsn in payload.items():
            registry[str(name)] = _parse_one(str(name), str(dsn))
    elif raw_single:
        registry["default"] = _parse_one("default", raw_single)

    if not registry:
        raise ValueError(
            "未配置任何数据库连接：请设置 DBPILOT_DSN 或 DBPILOT_CONNECTIONS"
        )
    return registry
