"""MCP 工具元数据与 schema 测试。"""

from __future__ import annotations

from app.agent.tools.registry import TOOL_REGISTRY
from app.mcp_server.schema import (
    TOOL_META,
    mcp_to_web_name,
    visible_params,
    web_to_mcp_name,
)

# 已核对的对外可见参数（见实施计划背景表）
EXPECTED_VISIBLE = {
    "list_tables": set(),
    "describe_table": {"table_names"},
    "execute_readonly_sql": {"sql"},
    "execute_write_sql": {"sql"},
    "execute_write_transaction": {"statements"},
    "explain_query": {"sql", "format"},
    "get_slow_queries": {"time_range", "limit", "include_explain"},
    "check_connections": set(),
    "check_locks": set(),
    "check_replication": set(),
    "analyze_locks": {"transaction_id", "thread_id"},
    "kill_transaction": {"thread_id"},
    "run_health_check": {"check_items"},
}


def test_every_web_tool_is_covered() -> None:
    assert set(TOOL_META) == set(TOOL_REGISTRY)


def test_mcp_names_prefixed_and_roundtrip() -> None:
    for web_name in TOOL_META:
        mcp_name = web_to_mcp_name(web_name)
        assert mcp_name.startswith("dbpilot_")
        assert mcp_to_web_name(mcp_name) == web_name


def test_visible_params_match_signatures() -> None:
    for web_name, expect in EXPECTED_VISIBLE.items():
        assert visible_params(web_name) == expect, web_name


def test_descriptions_not_empty() -> None:
    for meta in TOOL_META.values():
        assert meta.mcp_name.startswith("dbpilot_")
        assert len(meta.description) >= 20


def test_server_registers_prefixed_tools(monkeypatch: object) -> None:
    from app.mcp_server.server import build_server

    monkeypatch.setenv(
        "DBPILOT_CONNECTIONS",
        '{"shop": "mysql://u:p@127.0.0.1:3306/shop"}',
    )
    mcp = build_server()
    tool_names = {t.name for t in mcp._tool_manager.list_tools()}  # mcp>=1.9 内部 API
    assert "dbpilot_list_connections" in tool_names
    for web_name in TOOL_META:
        assert web_to_mcp_name(web_name) in tool_names
    assert len(tool_names) == len(TOOL_META) + 1


def test_default_connection_follows_env(monkeypatch: object) -> None:
    """DBPILOT_DEFAULT_CONNECTION 必须生效——含真实启动路径 env=None（历史缺陷回归）。"""
    from app.mcp_server.server import _resolve_default_name

    registry = {"alpha": object(), "beta": object()}
    # 显式传入 env（测试路径）
    assert _resolve_default_name(registry, {"DBPILOT_DEFAULT_CONNECTION": "beta"}) == "beta"
    # 未设置 → 取注册表第一个键
    assert _resolve_default_name(registry, {}) == "alpha"
    # env=None（run_server → build_server 的真实路径）也必须读到 os.environ
    monkeypatch.setenv("DBPILOT_DEFAULT_CONNECTION", "beta")
    assert _resolve_default_name(registry, None) == "beta"
    monkeypatch.delenv("DBPILOT_DEFAULT_CONNECTION")
    assert _resolve_default_name(registry, None) == "alpha"
    # 空白值视为未设置
    assert _resolve_default_name(registry, {"DBPILOT_DEFAULT_CONNECTION": "  "}) == "alpha"


def test_run_server_uses_stderr_logging_and_stdio(monkeypatch: object) -> None:
    """stdio transport 下日志必须走 stderr（stdout 只允许承载 JSON-RPC 消息）。"""
    import types

    from app.mcp_server import server as server_mod

    called: dict[str, object] = {}
    monkeypatch.setattr(
        "app.log_setup.configure_logging",
        lambda: called.setdefault("logging", True),
    )
    fake = types.SimpleNamespace(
        run=lambda **kw: called.setdefault("transport", kw.get("transport"))
    )
    monkeypatch.setattr(server_mod, "build_server", lambda *a, **k: fake)

    server_mod.run_server()
    assert called.get("logging") is True
    assert called.get("transport") == "stdio"
