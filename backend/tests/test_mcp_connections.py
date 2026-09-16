"""MCP 无头模式与连接解析测试。"""

from __future__ import annotations

import json

import pytest

from app.config import _headless_enabled
from app.mcp_server.connections import ConnectionEntry, parse_connections_env


def test_headless_flag_reads_env(monkeypatch: object) -> None:
    monkeypatch.setenv("DBPILOT_HEADLESS", "1")
    assert _headless_enabled() is True
    monkeypatch.delenv("DBPILOT_HEADLESS")
    assert _headless_enabled() is False


def test_single_dsn_default_name() -> None:
    env = {"DBPILOT_DSN": "mysql://root:secret@127.0.0.1:3306/shop?charset=utf8mb4"}
    reg = parse_connections_env(env)
    assert list(reg) == ["default"]
    e = reg["default"]
    assert isinstance(e, ConnectionEntry)
    assert (e.db_type, e.host, e.port, e.database, e.user, e.password) == (
        "mysql", "127.0.0.1", 3306, "shop", "root", "secret",
    )
    assert e.extra_params == {"charset": "utf8mb4"}


def test_multiple_dsn_json() -> None:
    payload = {
        "shop": "postgresql://u:p@localhost:5432/shop",
        "warehouse": "oracle://scott:tiger@10.0.0.5:1521/ORCLPDB",
    }
    reg = parse_connections_env({"DBPILOT_CONNECTIONS": json.dumps(payload)})
    assert set(reg) == {"shop", "warehouse"}
    assert reg["shop"].db_type == "postgresql"
    assert reg["warehouse"].db_type == "oracle"


def test_to_conn_config_keys() -> None:
    reg = parse_connections_env({"DBPILOT_DSN": "mysql://u:p@h:3306/db"})
    cfg = reg["default"].to_conn_config()
    assert set(cfg) == {
        "connection_id", "db_type", "host", "port", "database",
        "user", "password", "ssl_enabled", "ssl_ca_cert", "user_role",
    }


def test_unsupported_scheme_raises() -> None:
    with pytest.raises(ValueError, match="不受支持"):
        parse_connections_env({"DBPILOT_DSN": "mssql://u:p@h:1433/db"})


def test_ssl_query_params() -> None:
    reg = parse_connections_env(
        {"DBPILOT_DSN": "mysql://u:p@h:3306/db?ssl=1"}
    )
    assert reg["default"].ssl_enabled is True
