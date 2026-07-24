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

from typing import Annotated, Any, Literal

import structlog
from langchain_core.tools import InjectedToolArg, tool

from app.db.factory import AdapterFactory
from app.models.schemas import ConnectionCreateRequest

logger = structlog.get_logger(__name__)


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
    logger.error("工具调用失败", tool=fn_name, error=str(exc)[:200])
    return {
        "error": f"{fn_name} 执行失败",
        "detail": f"{type(exc).__name__}: {exc}",
    }


# =============================================================================
# explain_query：获取 SQL 执行计划
# =============================================================================


@tool(extras={"needs_sql_audit": True})
async def explain_query(
    connection_id: Annotated[str, InjectedToolArg],
    db_type: Annotated[str, InjectedToolArg],
    host: Annotated[str, InjectedToolArg],
    port: Annotated[int, InjectedToolArg],
    database: Annotated[str, InjectedToolArg],
    user: Annotated[str, InjectedToolArg],
    password: Annotated[str, InjectedToolArg],
    sql: str,
    format: Literal["tree", "json", "traditional"] = "tree",
    user_role: Annotated[str, InjectedToolArg] = "readonly",  # 未显式传入时采用更保守的只读兜底
    ssl_enabled: Annotated[bool, InjectedToolArg] = False,
    ssl_ca_cert: Annotated[str | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    """前提条件：先执行 execute_sql 确保 SQL 语法正确；
    若查看特定表的执行计划，建议先调用 describe_table 批量了解相关表结构。

    获取指定 SQL 语句的执行计划（EXPLAIN），用于查询性能分析和索引优化建议。
    三种格式（tree/json/traditional）由目标数据库方言自动映射，实际支持的格式取决于数据库类型。

    Args:
        sql: 要分析的 SQL 语句。
        format: 执行计划输出格式（tree / json / traditional）。

    Returns:
        成功: {
            "explain_output": str,  // 执行计划文本或 JSON
            "format": str,          // 实际使用的格式（与请求参数一致）
            "summary": str
        }
        不支持: {"error": str, "detail": str}
        失败: {"error": str, "detail": str}
    """
    # Step 1: SQL 安全审计由上游 tool_node._run_one_tool 中的
    # SQLAuditCheck 在工具执行前统一拦截，此处不再重复审计。
    # SAFETY: 参数化连接配置，不拼接连接串（AGENTS.md §数据库操作原则）

    # Step 2: 建立连接并检查适配器能力
    try:
        config = _build_config(
            connection_id,
            db_type,
            host,
            port,
            database,
            user,
            password,
            ssl_enabled,
            ssl_ca_cert,
        )
        adapter = AdapterFactory.create(db_type, config)
        await adapter.connect(config, user_role=user_role)

        # 检查适配器是否支持 EXPLAIN（AGENTS.md §安全与合规红线）
        capabilities = adapter.get_capabilities()
        if not capabilities.supports_explain:
            await adapter.disconnect()
            logger.warning(
                "工具不支持", tool="explain_query", connection_id=connection_id, db_type=db_type
            )
            return {
                "error": "该数据库类型不支持 EXPLAIN",
                "detail": f"适配器 {db_type} 的能力声明中 supports_explain=False",
            }

        # SAFETY: 参数化连接配置，不拼接连接串（AGENTS.md §数据库操作原则）
        result = await adapter.explain(sql)
        await adapter.disconnect()

        logger.info(
            "工具执行成功", tool="explain_query", connection_id=connection_id, sql=sql[:100]
        )
        return {
            "explain_output": result.get("explain_output", ""),
            "format": format,
            "summary": f"已生成执行计划（{format} 格式）",
        }
    except Exception as exc:
        return _safe_tool_call("explain_query", exc)


# =============================================================================
# get_slow_queries：获取慢查询列表
# =============================================================================


@tool
async def get_slow_queries(
    connection_id: Annotated[str, InjectedToolArg],
    db_type: Annotated[str, InjectedToolArg],
    host: Annotated[str, InjectedToolArg],
    port: Annotated[int, InjectedToolArg],
    database: Annotated[str, InjectedToolArg],
    user: Annotated[str, InjectedToolArg],
    password: Annotated[str, InjectedToolArg],
    time_range: Literal["1h", "6h", "24h", "7d"] = "1h",
    limit: int = 20,
    include_explain: bool = False,
    user_role: Annotated[str, InjectedToolArg] = "readonly",
    ssl_enabled: Annotated[bool, InjectedToolArg] = False,
    ssl_ca_cert: Annotated[str | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    """获取目标数据库在指定时间范围内的慢查询列表（含 SQL 文本、执行耗时、锁等待等）。
    适用于慢查询分析和查询性能优化。
    若目标数据库未开启慢查询日志，返回空列表并附带开启指引；同时自动降级到
    performance_schema 等替代方案。

    Args:
        time_range: 查询时间范围（1h=最近1小时 / 6h=最近6小时 / 24h=最近24小时 / 7d=最近7天）。
        limit: 最大返回条数（默认 20，上限 100）。
        include_explain: 是否自动对慢查询执行 EXPLAIN（最多前 5 条最慢查询）。

    Returns:
        成功（日志开启）: {
            "items": [{ "sql_text", "query_time_sec", "lock_time_sec",
                        "rows_examined", "rows_sent", "executed_at",
                        "explain_result"（可选） }],
            "total": int,
            "summary": str,
            "slow_log_enabled": true,
            "fallback_used": false
        }
        日志未启用（降级成功）: {
            "items": [...],
            "total": int,
            "warning": str,         // 降级说明
            "slow_log_enabled": false,
            "fallback_used": true
        }
        完全不可用: {"items": [], "warning": str, "total": 0, "summary": str,
                      "slow_log_enabled": bool, "fallback_used": bool}
        失败: {"error": str, "detail": str}
    """
    try:
        config = _build_config(
            connection_id,
            db_type,
            host,
            port,
            database,
            user,
            password,
            ssl_enabled,
            ssl_ca_cert,
        )
        adapter = AdapterFactory.create(db_type, config)
        await adapter.connect(config, user_role=user_role)

        # SAFETY: 慢查询日志读取为只读操作，不修改数据库状态
        result = await adapter.get_slow_queries(
            limit=limit,
            time_range=time_range,
            include_explain=include_explain,
        )
        await adapter.disconnect()

        items = result.get("items", [])
        warning = result.get("warning")
        slow_log_enabled = result.get("slow_log_enabled", True)
        fallback_used = result.get("fallback_used", False)

        # 构建摘要
        summary_parts = [f"找到 {len(items)} 条慢查询"]
        if fallback_used:
            summary_parts.append("（使用降级数据源）")
        if warning:
            summary_parts.append(f" 提示: {warning[:80]}")

        response: dict[str, Any] = {
            "items": items,
            "total": len(items),
            "summary": "".join(summary_parts),
            "slow_log_enabled": slow_log_enabled,
            "fallback_used": fallback_used,
        }
        if warning:
            response["warning"] = warning

        logger.info(
            "工具执行成功",
            tool="get_slow_queries",
            connection_id=connection_id,
            total=len(items),
            slow_log_enabled=slow_log_enabled,
            fallback_used=fallback_used,
        )
        return response
    except Exception as exc:
        return _safe_tool_call("get_slow_queries", exc)
