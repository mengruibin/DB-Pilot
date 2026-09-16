"""dbpilot_* 工具包装注册（EXTENSION 拆分：server.py 单文件超 200 行，AGENTS.md 上限）。

每个包装函数的**签名即 FastMCP 输入 Schema**（显式类型化字段/默认值，
禁止 **kwargs 泛型包装）；@mcp.tool 显式传 dbpilot_* 名。
函数体只做：转发可见参数 + connection 选择，真正执行委托给
build_server 传入的 _call 闭包（解析连接 → 策略角色 → run_mcp_tool 安全流水线）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from app.mcp_server.schema import TOOL_META

# _call(web_name, args, connection) -> 工具结果文本
_CallFn = Callable[[str, dict[str, Any], str], Awaitable[str]]


def register_tools(mcp: FastMCP, _call: _CallFn) -> None:
    """向 FastMCP 实例注册全部既有 dbpilot_* 包装工具。

    Args:
        mcp: build_server 内创建的 FastMCP 实例（已注册 dbpilot_list_connections）。
        _call: 执行委托闭包（build_server 内解析连接 → 策略角色 → run_mcp_tool）。
    """
    @mcp.tool(name="dbpilot_list_tables", description=TOOL_META["list_tables"].description)
    async def dbpilot_list_tables(connection: str = "") -> str:
        return await _call("list_tables", {}, connection)

    @mcp.tool(name="dbpilot_describe_table", description=TOOL_META["describe_table"].description)
    async def dbpilot_describe_table(table_names: list[str], connection: str = "") -> str:
        return await _call("describe_table", {"table_names": table_names}, connection)

    @mcp.tool(
        name="dbpilot_execute_readonly_sql",
        description=TOOL_META["execute_readonly_sql"].description,
    )
    async def dbpilot_execute_readonly_sql(sql: str, connection: str = "") -> str:
        return await _call("execute_readonly_sql", {"sql": sql}, connection)

    @mcp.tool(
        name="dbpilot_execute_write_sql",
        description=TOOL_META["execute_write_sql"].description,
    )
    async def dbpilot_execute_write_sql(sql: str, connection: str = "") -> str:
        return await _call("execute_write_sql", {"sql": sql}, connection)

    @mcp.tool(
        name="dbpilot_execute_write_transaction",
        description=TOOL_META["execute_write_transaction"].description,
    )
    async def dbpilot_execute_write_transaction(
        statements: list[str], connection: str = ""
    ) -> str:
        return await _call("execute_write_transaction", {"statements": statements}, connection)

    @mcp.tool(name="dbpilot_explain_query", description=TOOL_META["explain_query"].description)
    async def dbpilot_explain_query(
        sql: str,
        format: Literal["tree", "json", "traditional"] = "tree",
        connection: str = "",
    ) -> str:
        return await _call("explain_query", {"sql": sql, "format": format}, connection)

    @mcp.tool(
        name="dbpilot_get_slow_queries",
        description=TOOL_META["get_slow_queries"].description,
    )
    async def dbpilot_get_slow_queries(
        time_range: Literal["1h", "6h", "24h", "7d"] = "1h",
        limit: int = 20,
        include_explain: bool = False,
        connection: str = "",
    ) -> str:
        return await _call(
            "get_slow_queries",
            {"time_range": time_range, "limit": limit, "include_explain": include_explain},
            connection,
        )

    @mcp.tool(
        name="dbpilot_check_connections",
        description=TOOL_META["check_connections"].description,
    )
    async def dbpilot_check_connections(connection: str = "") -> str:
        return await _call("check_connections", {}, connection)

    @mcp.tool(name="dbpilot_check_locks", description=TOOL_META["check_locks"].description)
    async def dbpilot_check_locks(connection: str = "") -> str:
        return await _call("check_locks", {}, connection)

    @mcp.tool(
        name="dbpilot_check_replication",
        description=TOOL_META["check_replication"].description,
    )
    async def dbpilot_check_replication(connection: str = "") -> str:
        return await _call("check_replication", {}, connection)

    @mcp.tool(name="dbpilot_analyze_locks", description=TOOL_META["analyze_locks"].description)
    async def dbpilot_analyze_locks(
        transaction_id: str | None = None,
        thread_id: str | None = None,
        connection: str = "",
    ) -> str:
        args: dict[str, Any] = {}
        if transaction_id is not None:
            args["transaction_id"] = transaction_id
        if thread_id is not None:
            args["thread_id"] = thread_id
        return await _call("analyze_locks", args, connection)

    @mcp.tool(
        name="dbpilot_kill_transaction",
        description=TOOL_META["kill_transaction"].description,
    )
    async def dbpilot_kill_transaction(thread_id: str, connection: str = "") -> str:
        return await _call("kill_transaction", {"thread_id": thread_id}, connection)

    @mcp.tool(
        name="dbpilot_run_health_check",
        description=TOOL_META["run_health_check"].description,
    )
    async def dbpilot_run_health_check(
        check_items: list[str] | None = None, connection: str = ""
    ) -> str:
        args: dict[str, Any] = {}
        if check_items is not None:
            args["check_items"] = check_items
        return await _call("run_health_check", args, connection)
