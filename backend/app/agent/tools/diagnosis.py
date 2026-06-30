"""
诊断类 Agent 工具集。

包含 explain_query / get_slow_queries 两个工具。
每个工具用 @tool 装饰，返回 Python dict。
依据 PRD §5.2 慢查询分析与执行计划、AGENTS.md §工具函数返回契约。

工具函数返回契约（AGENTS.md §API 与数据契约）：
  - 所有 @tool 装饰的函数返回 Python 原生类型（dict/list/str）
  - 工具函数内部捕获异常后返回 {"error": "...", "detail": "..."}
  - MUST NOT 向上抛出未处理异常
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from app.db.factory import AdapterFactory
from app.engine.sql_auditor import audit
from app.models.schemas import ConnectionCreateRequest


def _build_config(
    connection_id: str,
    db_type: str,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    ssl_enabled: bool = False,
    ssl_ca_cert: str | None = None,
    extra_params: dict | None = None,
) -> ConnectionCreateRequest:
    """从连接参数构建 ConnectionCreateRequest。

    供工具函数内部使用，将散落的连接参数组装为适配器需要的配置对象。
    """
    return ConnectionCreateRequest(
        name=f"conn_{connection_id}",
        db_type=db_type,  # type: ignore[arg-type] — Pydantic validator handles str→DBType
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        ssl_enabled=ssl_enabled,
        ssl_ca_cert=ssl_ca_cert,
        extra_params=extra_params,
    )


def _safe_tool_call(fn_name: str, exc: Exception) -> dict[str, Any]:
    """将工具调用中的异常包装为标准错误响应。

    AGENTS.md §工具函数返回契约：
    工具函数内部捕获异常后返回 {"error": "...", "detail": "..."}
    """
    return {
        "error": f"{fn_name} 执行失败",
        "detail": f"{type(exc).__name__}: {exc}",
    }


# =============================================================================
# explain_query：获取 SQL 执行计划
# =============================================================================


@tool
async def explain_query(
    connection_id: str,
    db_type: str,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    sql: str,
    format: str = "tree",
    ssl_enabled: bool = False,
    ssl_ca_cert: str | None = None,
) -> dict[str, Any]:
    """获取指定 SQL 的执行计划（EXPLAIN）。

    先对 SQL 进行安全审计，通过后调用适配器的 explain() 方法。
    适用于查询性能分析和索引建议场景（PRD §5.2）。

    SAFETY: 所有 SQL 执行前必须经过审计（AGENTS.md §安全与合规红线）。

    Args:
        connection_id: 连接标识符（用于日志追踪）。
        db_type: 数据库类型（mysql / postgresql / oracle）。
        host: 数据库主机地址。
        port: 数据库端口号。
        database: 目标数据库名。
        user: 连接用户名。
        password: 连接密码。
        sql: 要分析的 SQL 语句。
        format: 执行计划格式（tree / json / traditional），各数据库方言自动映射。
        ssl_enabled: 是否启用 SSL。
        ssl_ca_cert: SSL CA 证书（可选）。

    Returns:
        成功：{"explain_output": "...", "format": "tree"}
        不支持：{"error": "该数据库类型不支持 EXPLAIN"}
        审计拦截：{"error": "SQL 审计未通过", "detail": "...", "violations": [...]}
        失败：{"error": "explain_query 执行失败", "detail": "..."}
    """
    # Step 1: SQL 安全审计（AGENTS.md §安全与合规红线）
    # SAFETY: 不跳过 SQL 审计直接执行用户/LLM 生成的 SQL (AGENTS.md §安全与合规红线)
    audit_result = audit(sql, db_type=db_type, user_role="standard")
    if not audit_result.passed:
        return {
            "error": "SQL 审计未通过",
            "detail": "语句包含危险操作，已被拦截",
            "violations": [
                {"type": v.type, "message": v.message} for v in audit_result.violations
            ],
        }

    # Step 2: 建立连接并检查适配器能力
    try:
        config = _build_config(
            connection_id, db_type, host, port, database, user,
            password, ssl_enabled, ssl_ca_cert,
        )
        adapter = AdapterFactory.create(db_type, config)
        await adapter.connect(config)

        # 检查适配器是否支持 EXPLAIN（AGENTS.md §安全与合规红线）
        capabilities = adapter.get_capabilities()
        if not capabilities.supports_explain:
            await adapter.disconnect()
            return {
                "error": "该数据库类型不支持 EXPLAIN",
                "detail": f"适配器 {db_type} 的能力声明中 supports_explain=False",
            }

        # SAFETY: 参数化连接配置，不拼接连接串（AGENTS.md §数据库操作原则）
        result = await adapter.explain(sql)
        await adapter.disconnect()

        return {
            "explain_output": result.get("explain_output", ""),
            "format": format,
        }
    except Exception as exc:
        return _safe_tool_call("explain_query", exc)


# =============================================================================
# get_slow_queries：获取慢查询列表
# =============================================================================


@tool
async def get_slow_queries(
    connection_id: str,
    db_type: str,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    time_range: str = "1h",
    limit: int = 20,
    ssl_enabled: bool = False,
    ssl_ca_cert: str | None = None,
) -> dict[str, Any]:
    """获取目标数据库的慢查询列表。

    调用适配器的 get_slow_queries() 从慢查询日志中读取。
    适用于慢查询分析和性能优化场景（PRD §5.2）。

    若目标数据库未开启慢查询日志，返回空列表并附带 warning，不崩溃。
    （依据 BaseAdapter 契约：{"items": [], "warning": "..."}）

    Args:
        connection_id: 连接标识符。
        db_type: 数据库类型。
        host: 主机地址。
        port: 端口号。
        database: 数据库名。
        user: 用户名。
        password: 密码。
        time_range: 时间范围（1h / 6h / 24h / 7d）。
        limit: 最大返回条数（默认 20，上限 100）。
        ssl_enabled: 是否启用 SSL。
        ssl_ca_cert: SSL CA 证书（可选）。

    Returns:
        成功：{"items": [...], "total": int}
        日志未启用：{"items": [], "warning": "slow_query_log 未启用"}
        失败：{"error": "get_slow_queries 执行失败", "detail": "..."}
    """
    try:
        config = _build_config(
            connection_id, db_type, host, port, database, user,
            password, ssl_enabled, ssl_ca_cert,
        )
        adapter = AdapterFactory.create(db_type, config)
        await adapter.connect(config)

        # SAFETY: 慢查询日志读取为只读操作，不修改数据库状态
        result = await adapter.get_slow_queries(limit=limit, time_range=time_range)
        await adapter.disconnect()

        items = result.get("items", [])
        warning = result.get("warning")

        response: dict[str, Any] = {
            "items": items,
            "total": len(items),
        }
        if warning:
            response["warning"] = warning

        return response
    except Exception as exc:
        return _safe_tool_call("get_slow_queries", exc)
