"""MCP 服务构建与工具注册（EXTENSION: DB-Pilot 无头 MCP 入口）。

stdio transport（本地，供 Claude Code / Codex 等宿主以子进程方式拉起）。
Web 端（FastAPI/LangGraph/SSE）与本模块完全独立，互不影响。

单文件 ≤200 行（backend/AGENTS.md）：13 个既有 dbpilot_* 包装函数的注册
拆在同包 server_tools.py::register_tools（build_server 内 import 后注册）。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP

from app.mcp_server.connections import parse_connections_env
from app.mcp_server.executor import run_mcp_tool
from app.mcp_server.policy import load_policy

logger = structlog.get_logger(__name__)

_INSTRUCTIONS = (
    "你是 DB-Pilot：一个 AI 数据库运维助手，通过 MCP 工具连接 MySQL/PostgreSQL/Oracle。\n"
    "规则：1) 先调 dbpilot_list_connections 确认目标库与只读/写模式；2) 查询前优先用 "
    "dbpilot_list_tables / dbpilot_describe_table 确认表结构；3) 只读 SQL 走 "
    "dbpilot_execute_readonly_sql，禁止在其中执行写语句；4) 写操作需要服务端开启 "
    "DBPILOT_ALLOW_WRITE，被拦截时不要反复重试，向用户说明原因。"
)


def _resolve_connection(
    registry: Mapping[str, Any],
    default_name: str | None,
    raw_name: str,
) -> tuple[Any, str | None]:
    """按 connection 参数解析连接条目。返回 (entry, error_text)。"""
    name = raw_name.strip() or (default_name or "default")
    entry = registry.get(name)
    if entry is None:
        available = ", ".join(sorted(registry)) or "(无)"
        return None, (
            f"未知连接 '{name}'。可用连接: {available}。"
            "可用 dbpilot_list_connections 查看，或在调用参数中传 connection=<名字>。"
        )
    return entry, None


def _resolve_default_name(
    registry: Mapping[str, Any],
    env: Mapping[str, str] | None = None,
) -> str:
    """决定工具未传 connection 参数时使用的默认连接名。

    优先 DBPILOT_DEFAULT_CONNECTION；未设置时取连接注册表的第一个键。

    Args:
        registry: 连接注册表（parse_connections_env 产出）。
        env: 环境变量映射；None 时读 os.environ。真实启动路径（run_server →
             build_server()）传的就是 None，所以这里必须自己兜到 os.environ，
             否则该配置项会被静默忽略（历史缺陷）。

    Returns:
        默认连接名；注册表为空时返回 "default"。
    """
    env_map = env if env is not None else os.environ
    explicit = (env_map.get("DBPILOT_DEFAULT_CONNECTION") or "").strip()
    return explicit or next(iter(registry), "default")


def build_server(env: Mapping[str, str] | None = None) -> FastMCP:
    """构建 FastMCP 服务（解析 env → 注册 dbpilot_* 工具与发现工具）。

    Args:
        env: 环境变量映射（测试注入用）；None 时读 os.environ。

    Returns:
        已注册 dbpilot_* 工具的 FastMCP 实例。
    """
    registry = parse_connections_env(env)
    policy = load_policy(env)
    default_name = _resolve_default_name(registry, env)

    mcp = FastMCP("db-pilot", instructions=_INSTRUCTIONS)

    async def _call(web_name: str, args: dict[str, Any], connection: str) -> str:
        """包装执行：解析连接 → 策略角色 → run_mcp_tool；异常兜底为文本。"""
        entry, err = _resolve_connection(registry, default_name, connection)
        if err is not None:
            return err
        conn_config = entry.to_conn_config(user_role=policy.effective_user_role)
        try:
            return await run_mcp_tool(web_name, args, conn_config, policy)
        except Exception as exc:  # noqa: BLE001 — 工具契约不允许抛
            logger.exception("MCP 工具执行异常", tool=web_name)
            return f"工具执行失败: {type(exc).__name__}: {exc}"

    # ── 发现工具（无 DB 副作用） ──

    @mcp.tool(
        name="dbpilot_list_connections",
        description=(
            "列出 MCP 服务可用的数据库连接（连接名/类型/库名/是否只读模式）。"
            "开始任何操作前先调用它以确认目标库与权限模式。"
        ),
    )
    async def dbpilot_list_connections() -> str:
        rows = []
        for name, entry in sorted(registry.items()):
            rows.append(
                {
                    "name": name,
                    "db_type": entry.db_type,
                    "host": entry.host,
                    "database": entry.database,
                    "readonly": not policy.allow_write,
                }
            )
        return json.dumps({"connections": rows}, ensure_ascii=False)

    # ── 13 个既有工具：包装函数在同包 server_tools.py（签名即 MCP 输入 Schema） ──
    from app.mcp_server.server_tools import register_tools

    register_tools(mcp, _call)
    return mcp


def run_server() -> None:
    """stdio 入口（被 __main__ 调用）。

    MCP stdio 下 stdout 只能承载 JSON-RPC 消息，日志混入会破坏协议流，
    因此启用与 Web 同款日志配置（结构化日志全部写 stderr + UTF-8 包装）。
    """
    from app.log_setup import configure_logging

    configure_logging()
    mcp = build_server()
    mcp.run(transport="stdio")
