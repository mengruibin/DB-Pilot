"""
Agent 工具注册中心（任务 3 精简：移除手动 Schema 过滤）。

统一管理所有 Agent 可调用工具：
  - AGENT_TOOLS: 工具函数列表（LangChain BaseTool 对象）
  - TOOL_REGISTRY: 工具名称 → 工具函数映射

连接参数通过 @tool InjectedToolArg 标注自动从 bind_tools() Schema 中排除，
不再需要手动 _INJECTED_CONN_PARAMS 过滤。
SafeToolNode 在运行时自动注入连接配置。

新增工具只需在此文件中注册即可，无需修改 graph.py 或其他代码。
"""

from __future__ import annotations

from typing import Any

from app.agent.tools.diagnosis import explain_query, get_slow_queries
from app.agent.tools.health import run_health_check
from app.agent.tools.query import (
    describe_table,
    execute_readonly_sql,
    execute_write_sql,
    list_tables,
)
from app.agent.tools.troubleshoot import (
    analyze_locks,
    check_connections,
    check_locks,
    check_replication,
    kill_transaction,
)

# =============================================================================
# 工具注册表
# =============================================================================

AGENT_TOOLS = [
    # 查询类工具
    list_tables,  # 获取数据库中所有表
    describe_table,  # 获取指定表的结构
    execute_readonly_sql,  # 执行只读 SQL 查询（SELECT / SHOW / EXPLAIN 等）
    execute_write_sql,  # 执行写 SQL（INSERT / UPDATE / DELETE，需用户确认）
    # 诊断类工具
    get_slow_queries,  # 获取慢查询日志
    explain_query,  # 分析 SQL 执行计划
    # 故障排查类工具
    check_connections,  # 检查连接池状态
    check_locks,  # 检查锁等待情况
    analyze_locks,  # 查询指定事务的详细锁信息
    kill_transaction,  # 终止指定线程的事务（需用户确认）
    check_replication,  # 检查主从复制状态
    # 健康巡检类工具
    run_health_check,  # 执行 20 项健康巡检
]

# 工具名称 → 工具函数的快速查找映射
TOOL_REGISTRY: dict[str, Any] = {tool.name: tool for tool in AGENT_TOOLS}
