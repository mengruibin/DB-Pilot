"""MCP 无头执行器测试（mock 编排原语，不连真实库）。"""

from __future__ import annotations

import os
from typing import Any

import pytest

from app.mcp_server import executor
from app.mcp_server.connections import ConnectionEntry
from app.mcp_server.policy import Policy


def _entry() -> ConnectionEntry:
    return ConnectionEntry(
        name="default", db_type="mysql", host="127.0.0.1",
        port=3306, database="db", user="u", password="p",
    )


async def test_write_tool_denied_in_readonly_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []

    async def _fake_pre_confirm(*a: Any, **k: Any) -> dict[str, Any]:
        called.append("pre")
        return {}

    async def _fake_phase3(*a: Any, **k: Any) -> list[Any]:
        called.append("phase3")
        return []

    monkeypatch.setattr(executor, "run_pre_confirm", _fake_pre_confirm)
    monkeypatch.setattr(executor, "run_phase3", _fake_phase3)

    policy = Policy(allow_write=False)
    text = await executor.run_mcp_tool(
        "execute_write_sql", {"sql": "UPDATE t SET a=1 WHERE id=1"},
        _entry().to_conn_config(user_role=policy.effective_user_role),
        policy,
    )
    assert "安全策略拦截" in text
    assert called == []  # 只读模式不允许写：不得触碰 DB/审计


async def test_readonly_tool_executed_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_pre_confirm(*a: Any, **k: Any) -> dict[str, Any]:
        return {}

    class _Outcome:
        kind = "executed"
        content = '{"tables": [{"table_name": "users"}], "summary": "找到 1 张表"}'

    async def _fake_phase3(*a: Any, **k: Any) -> list[Any]:
        return [_Outcome()]

    monkeypatch.setattr(executor, "run_pre_confirm", _fake_pre_confirm)
    monkeypatch.setattr(executor, "run_phase3", _fake_phase3)

    policy = Policy(allow_write=False)
    text = await executor.run_mcp_tool(
        "list_tables", {}, _entry().to_conn_config(user_role="readonly"), policy,
    )
    assert text == _Outcome.content


async def test_blocked_path(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_pre_confirm(*a: Any, **k: Any) -> dict[str, Any]:
        from app.agent.security.models import StageResult

        # 返回按 run_mcp_tool 实际生成的 tool_call id 索引（非固定 "tc_1"）
        return {a[0][0]["id"]: StageResult(blocked=True, reason="审计拦截: DDL 红线")}

    monkeypatch.setattr(executor, "run_pre_confirm", _fake_pre_confirm)

    policy = Policy(allow_write=False)
    text = await executor.run_mcp_tool(
        "execute_readonly_sql", {"sql": "DROP TABLE t"},
        _entry().to_conn_config(user_role="readonly"),
        policy,
    )
    assert "审计拦截" in text


@pytest.mark.integration
async def test_executor_against_real_db() -> None:
    """端到端冒烟：需本地库 env（DBPILOT_DSN）且 DBPILOT_ALLOW_WRITE=1。

    验证 list_tables 执行成功、execute_write_sql 在允许写时放行、只读模式拒绝。
    """
    dsn = os.getenv("DBPILOT_DSN")
    if not dsn:
        pytest.skip("未设置 DBPILOT_DSN，跳过真实库集成用例")
    from app.mcp_server.connections import parse_connections_env
    from app.mcp_server.executor import run_mcp_tool
    from app.mcp_server.policy import Policy

    entry = parse_connections_env(os.environ)["default"]

    # 1) 只读模式拒绝写
    ro = Policy(allow_write=False)
    denied = await run_mcp_tool(
        "execute_write_sql", {"sql": "UPDATE 1"},  # 仅测闸门：不应执行到 SQL
        entry.to_conn_config(user_role=ro.effective_user_role), ro,
    )
    assert "安全策略拦截" in denied

    # 2) 允许写模式下列表
    rw = Policy(allow_write=True)
    tables = await run_mcp_tool(
        "list_tables", {}, entry.to_conn_config(user_role="admin"), rw,
    )
    assert "tables" in tables

    # 3) 红线 DDL 仍被审计硬拦（即使允许写）
    ddl = await run_mcp_tool(
        "execute_write_sql", {"sql": "DROP TABLE IF EXISTS db_pilot_mcp_smoke"},
        entry.to_conn_config(user_role="admin"), rw,
    )
    assert "拦截" in ddl or "block" in ddl.lower()
