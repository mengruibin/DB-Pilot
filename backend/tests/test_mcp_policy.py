"""MCP 写策略测试。"""

from __future__ import annotations

from app.agent.security.registry import SECURITY_REGISTRY
from app.mcp_server.policy import (
    WRITE_GATED_TOOLS,
    load_policy,
)


def test_write_gated_tools_match_registry() -> None:
    # registry 中含 CONFIRM 相位的工具 = 写/危险工具
    expect = {
        name
        for name, profile in SECURITY_REGISTRY.items()
        if profile.confirm_ref() is not None
    }
    assert expect == WRITE_GATED_TOOLS
    assert {"execute_write_sql", "execute_write_transaction", "kill_transaction"} <= expect


def test_default_policy_is_readonly() -> None:
    p = load_policy({})
    assert p.allow_write is False
    assert p.effective_user_role == "readonly"


def test_allow_write_flag() -> None:
    assert load_policy({"DBPILOT_ALLOW_WRITE": "1"}).allow_write is True
    assert load_policy({"DBPILOT_ALLOW_WRITE": "true"}).effective_user_role == "admin"
