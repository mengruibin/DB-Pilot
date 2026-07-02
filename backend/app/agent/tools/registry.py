"""
Agent 工具注册中心（B-30）。

统一管理所有 Agent 可调用工具：
  - AGENT_TOOLS: 工具函数列表
  - TOOL_REGISTRY: 工具名称 → 工具函数映射
  - get_tool_schemas(): 将 LangChain @tool 格式转换为 Anthropic JSON Schema 格式

新增工具只需在此文件中注册即可，无需修改 graph.py 或其他代码。
"""

from __future__ import annotations

from typing import Any

from app.agent.tools.diagnosis import explain_query, get_slow_queries
from app.agent.tools.health import run_health_check
from app.agent.tools.query import describe_table, list_tables, run_query
from app.agent.tools.troubleshoot import (
    check_connections,
    check_locks,
    check_replication,
)

# =============================================================================
# 工具注册表
# =============================================================================

AGENT_TOOLS = [
    # 查询类工具
    list_tables,        # 获取数据库中所有表
    describe_table,     # 获取指定表的结构
    run_query,          # 执行只读 SQL 查询
    # 诊断类工具
    get_slow_queries,   # 获取慢查询日志
    explain_query,      # 分析 SQL 执行计划
    # 故障排查类工具
    check_connections,  # 检查连接池状态
    check_locks,        # 检查锁等待情况
    check_replication,  # 检查主从复制状态
    # 健康巡检类工具
    run_health_check,   # 执行 20 项健康巡检
]

# 工具名称 → 工具函数的快速查找映射
TOOL_REGISTRY: dict[str, Any] = {
    tool.name: tool for tool in AGENT_TOOLS
}


# =============================================================================
# JSON Schema 转换
# =============================================================================


def get_tool_schemas(
    tools: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """将 LangChain @tool 函数列表转换为 Anthropic tool calling JSON Schema 格式。

    Anthropic 工具格式：
      {"name": "tool_name", "description": "...", "input_schema": {...}}

    OpenAI 格式转换由 LLMClient._convert_tools_to_openai() 负责。

    Args:
        tools: 工具函数列表。None 表示使用默认 AGENT_TOOLS。

    Returns:
        JSON Schema 格式的工具定义列表。
    """
    tools = tools or AGENT_TOOLS
    schemas: list[dict[str, Any]] = []
    for tool_fn in tools:
        schema = _tool_to_anthropic_schema(tool_fn)
        schemas.append(schema)
    return schemas


def _tool_to_anthropic_schema(tool_fn: Any) -> dict[str, Any]:
    """将单个 LangChain @tool 函数转换为 Anthropic 工具 Schema。

    从 @tool 装饰器中提取：
      - name: 函数名
      - description: 函数 docstring（首段）
      - input_schema: 参数类型注解推导

    Args:
        tool_fn: LangChain @tool 装饰的函数（BaseTool 实例）。

    Returns:
        Anthropic 格式的工具定义。
    """
    # LangChain BaseTool 的属性
    if hasattr(tool_fn, "__name__"):
        name = getattr(tool_fn, "name", tool_fn.__name__)
    else:
        name = str(tool_fn)
    description = getattr(tool_fn, "description", "")
    if not description and hasattr(tool_fn, "__doc__") and tool_fn.__doc__:
        # 取 docstring 第一段作为描述
        description = tool_fn.__doc__.strip().split("\n\n")[0]

    # 从工具函数的 args_schema 中提取参数 Schema
    input_schema = _extract_args_schema(tool_fn)

    return {
        "name": name,
        "description": description,
        "input_schema": input_schema,
    }


def _extract_args_schema(tool_fn: Any) -> dict[str, Any]:
    """从 LangChain @tool 函数的 args_schema 中提取 JSON Schema。

    优先使用 LangChain 自动生成的 args_schema（基于类型注解的 Pydantic model），
    回退时手动构建基本的 JSON Schema。

    Args:
        tool_fn: LangChain @tool 装饰的函数。

    Returns:
        JSON Schema 格式的参数定义。
    """
    # 尝试使用 LangChain 自动生成的 args_schema
    args_schema = getattr(tool_fn, "args_schema", None)
    if args_schema is not None:
        try:
            schema = args_schema.model_json_schema()
            # 移除不必要字段
            schema.pop("title", None)
            schema.pop("description", None)
            return {
                "type": "object",
                "properties": schema.get("properties", {}),
                "required": schema.get("required", []),
            }
        except Exception:
            pass

    # 回退：手动构建基本 Schema
    # 根据函数签名中的已知参数推导
    default_properties: dict[str, Any] = {
        "connection_id": {
            "type": "string",
            "description": "目标数据库连接 ID",
        },
        "db_type": {
            "type": "string",
            "description": "数据库类型（mysql / postgresql / oracle）",
        },
        "database": {
            "type": "string",
            "description": "数据库名称",
        },
        "sql": {
            "type": "string",
            "description": "要执行的 SQL 语句（仅限只读 SELECT）",
        },
        "table_name": {
            "type": "string",
            "description": "表名",
        },
        "time_range": {
            "type": "string",
            "description": "时间范围（如 1h / 24h）",
        },
        "limit": {
            "type": "integer",
            "description": "返回数量上限",
        },
        "format": {
            "type": "string",
            "description": "输出格式（tree / json）",
        },
        "check_items": {
            "type": "array",
            "items": {"type": "string"},
            "description": "检查项列表（['all'] 表示全部）",
        },
        "issue_type": {
            "type": "string",
            "description": "问题类型（auto / connection / lock / replication）",
        },
    }

    return {
        "type": "object",
        "properties": default_properties,
        "required": ["connection_id"],
    }
