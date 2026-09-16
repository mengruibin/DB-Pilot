"""MCP 工具元数据：名称映射 / 描述 / 参数清单（EXTENSION）。

对外工具统一加 dbpilot_ 前缀，避免宿主同时挂载多个 MCP server 撞名。
参数清单动态取自 LangChain 工具 args_schema，并在 visible_params 里按
InjectedToolArg 标记剔除注入参数（本版本 langchain-core 仍会把注入参数
保留在 args_schema.model_fields，并不会自动排除）。
描述为人工维护（agent 选工具依赖描述质量，不走自动生成）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.tools import InjectedToolArg

from app.agent.tools.registry import TOOL_REGISTRY

_PREFIX = "dbpilot_"


@dataclass(frozen=True)
class MCPToolMeta:
    """单个工具的 MCP 元数据。"""

    web_name: str
    mcp_name: str
    description: str


_TOOL_DESCRIPTIONS: dict[str, str] = {
    "list_tables": "列出当前数据库所有表（表名/注释/行数估算）。开始分析库结构前先调用。",
    "describe_table": (
        "获取指定表的结构（列、类型、索引、软删除标识等），参数 table_names 传表名数组。"
        "写 SQL 前用它确认字段名。"
    ),
    "execute_readonly_sql": (
        "在目标库执行只读 SQL（仅 SELECT/SHOW/EXPLAIN/USE/SET 等，禁止写语句）。"
        "执行前自动过 sqlglot 审计与 EXPLAIN 数据量评估，违规/超大数据量会被拦截并返回原因。"
        "结果已截断（≤100 行/40K 字符）。"
    ),
    "execute_write_sql": (
        "执行写 SQL（INSERT/UPDATE/DELETE）。属危险操作：仅在 MCP 服务以 "
        "DBPILOT_ALLOW_WRITE=1 启动时可用；无 WHERE 全表更新/删除、红线 DDL "
        "(DROP/ALTER/TRUNCATE/CREATE/GRANT/REVOKE) 一律被审计硬拦。返回实际影响行数。"
    ),
    "execute_write_transaction": (
        "单次调用内原子执行多条写 SQL（BEGIN→逐条→COMMIT/失败 ROLLBACK），"
        "仅 INSERT/UPDATE/DELETE，任一条违规整批拦截。DBPILOT_ALLOW_WRITE=1 时可用。"
    ),
    "explain_query": (
        "对目标 SQL 生成 EXPLAIN 执行计划并解读（format: tree/json/traditional）。"
        "做查询性能诊断前先调用。"
    ),
    "get_slow_queries": (
        "获取慢查询（time_range: 1h/6h/24h/7d；limit 条数；include_explain 是否附执行计划）。"
        "慢日志关闭时自动降级 performance_schema。"
    ),
    "check_connections": "检查数据库当前连接池状态（总数/活跃/空闲/上限等）。",
    "check_locks": "检查当前锁等待（返回完整锁拓扑：held/waiting、锁模式、锁类型）。",
    "check_replication": (
        "检查主从复制状态（延迟/错误等）。排查复制异常前先调用。"
    ),
    "analyze_locks": (
        "查询指定事务/线程的详细锁信息（锁模式、等待链、根阻塞者）。"
        "transaction_id 或 thread_id 至少传一个。"
    ),
    "kill_transaction": (
        "终止指定线程/事务的连接（thread_id 必填）。危险操作：仅在 DBPILOT_ALLOW_WRITE=1 "
        "时可用，将立即断开该连接。"
    ),
    "run_health_check": (
        "执行 20 项健康巡检（连接/慢查询/锁/复制/容量等，含关联分析与修复建议）。"
        "check_items 可选，限制只跑指定检查项。"
    ),
}

TOOL_META: dict[str, MCPToolMeta] = {
    name: MCPToolMeta(
        web_name=name,
        mcp_name=f"{_PREFIX}{name}",
        description=_TOOL_DESCRIPTIONS[name],
    )
    for name in TOOL_REGISTRY
}


def web_to_mcp_name(web_name: str) -> str:
    """Web 工具名 → MCP 工具名（dbpilot_ 前缀）。"""
    return f"{_PREFIX}{web_name}"


def mcp_to_web_name(mcp_name: str) -> str:
    """MCP 工具名 → Web 工具名（去前缀，未知名字返回原样便于报错）。"""
    return mcp_name[len(_PREFIX):] if mcp_name.startswith(_PREFIX) else mcp_name


def visible_params(web_name: str) -> set[str]:
    """工具对外可见参数名。

    本版本 langchain-core 不会把 InjectedToolArg 注入参数自动排除出
    args_schema.model_fields，需按字段元数据里的注入标记手动剔除
    （连接参数 + user_role），只留 LLM 真正需要填写的参数。
    """
    tool = TOOL_REGISTRY[web_name]
    schema = getattr(tool, "args_schema", None)
    if schema is None:
        return set()
    return {
        name
        for name, field in schema.model_fields.items()
        if not _is_injected(field)
    }


def _is_injected(field: Any) -> bool:
    """字段是否为运行时注入参数（签名用 Annotated[..., InjectedToolArg] 标注）。"""
    return any(m is InjectedToolArg for m in field.metadata)
