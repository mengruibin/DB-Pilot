"""
查询类 Agent 工具集。

包含 list_tables / describe_table / run_query 三个工具。
每个工具用 @tool 装饰，返回 Python dict。
依据 PRD §6.3 工具注册表、AGENTS.md §工具函数返回契约。

工具函数返回契约（AGENTS.md §API 与数据契约）：
  - 所有 @tool 装饰的函数返回 Python 原生类型（dict/list/str）
  - 工具函数内部捕获异常后返回 {"error": "...", "detail": "..."}
  - MUST NOT 向上抛出未处理异常
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from langchain_core.tools import InjectedToolArg, tool

from app.db.factory import AdapterFactory
from app.engine.sql_auditor import audit
from app.engine.sql_error_parser import parse_db_error
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


def _safe_tool_call(
    fn_name: str, exc: Exception,
    connection_id: str | None = None,
    database: str | None = None,
) -> dict[str, Any]:
    """将工具调用中的异常包装为标准错误响应。

    AGENTS.md §工具函数返回契约：
    工具函数内部捕获异常后返回 {"error": "...", "detail": "..."}

    Args:
        fn_name: 工具函数名。
        exc: 捕获的异常。
        connection_id: 数据库连接 ID（用于日志关联）。
        database: 目标数据库名（用于日志关联）。
    """
    logger.error("工具调用失败",
                 tool=fn_name,
                 connection_id=connection_id,
                 database=database,
                 error=str(exc)[:200])
    return {
        "error": f"{fn_name} 执行失败",
        "detail": f"{type(exc).__name__}: {exc}",
    }


# =============================================================================
# list_tables：列出数据库中的表
# =============================================================================

@tool
async def list_tables(
    connection_id: Annotated[str, InjectedToolArg],
    db_type: Annotated[str, InjectedToolArg],
    host: Annotated[str, InjectedToolArg],
    port: Annotated[int, InjectedToolArg],
    database: Annotated[str, InjectedToolArg],
    user: Annotated[str, InjectedToolArg],
    password: Annotated[str, InjectedToolArg],
    user_role: Annotated[str, InjectedToolArg] = "standard",
    ssl_enabled: Annotated[bool, InjectedToolArg] = False,
    ssl_ca_cert: Annotated[str | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    """列出目标数据库中的所有表。

    调用适配器的 get_tables() 获取表名、注释和行数估计。
    适用于 NL2SQL 的 Schema 上下文注入和用户快速浏览数据库结构。

    Args:
        connection_id: 连接标识符（用于日志追踪）。
        db_type: 数据库类型（mysql / postgresql / oracle）。
        host: 数据库主机地址。
        port: 数据库端口号。
        database: 目标数据库名。
        user: 连接用户名。
        password: 连接密码。
        user_role: 用户角色（readonly / standard / admin）。
        ssl_enabled: 是否启用 SSL。
        ssl_ca_cert: SSL CA 证书 PEM（可选）。

    Returns:
        成功：{"tables": [{"table_name": "...", "comment": "...", ...}]}
        失败：{"error": "list_tables 执行失败", "detail": "..."}
    """
    # SAFETY: 使用参数化连接配置，不拼接连接串（AGENTS.md §数据库操作原则）
    try:
        config = _build_config(
            connection_id, db_type, host, port, database, user,
            password, ssl_enabled, ssl_ca_cert,
        )
        adapter = AdapterFactory.create(db_type, config)
        await adapter.connect(config, user_role=user_role)
        tables = await adapter.get_tables(database)
        await adapter.disconnect()

        logger.info("工具执行成功", tool="list_tables",
                     connection_id=connection_id, table_count=len(tables))
        return {
            "tables": tables,
            "summary": f"找到 {len(tables)} 张表",
        }
    except Exception as exc:
        return _safe_tool_call(
            "list_tables", exc,
            connection_id=connection_id,
            database=database,
        )


# =============================================================================
# describe_table：获取表结构和索引
# =============================================================================

@tool
async def describe_table(
    connection_id: Annotated[str, InjectedToolArg],
    db_type: Annotated[str, InjectedToolArg],
    host: Annotated[str, InjectedToolArg],
    port: Annotated[int, InjectedToolArg],
    database: Annotated[str, InjectedToolArg],
    user: Annotated[str, InjectedToolArg],
    password: Annotated[str, InjectedToolArg],
    table_name: str,
    user_role: Annotated[str, InjectedToolArg] = "standard",
    ssl_enabled: Annotated[bool, InjectedToolArg] = False,
    ssl_ca_cert: Annotated[str | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    """获取指定表的详细结构信息（列信息 + 索引信息）。

    同时调用适配器的 get_columns() 和 get_indexes()，
    适用于 NL2SQL 获取 Schema 上下文和查询优化分析。

    Args:
        connection_id: 连接标识符。
        db_type: 数据库类型。
        host: 主机地址。
        port: 端口号。
        database: 数据库名。
        user: 用户名。
        password: 密码。
        table_name: 目标表名。
        user_role: 用户角色（readonly / standard / admin）。
        ssl_enabled: 是否启用 SSL。
        ssl_ca_cert: SSL CA 证书（可选）。

    Returns:
        成功：{"columns": [...], "indexes": [...]}
        失败：{"error": "describe_table 执行失败", "detail": "..."}
    """
    # SAFETY: 使用参数化连接配置，不拼接连接串
    try:
        config = _build_config(
            connection_id, db_type, host, port, database, user,
            password, ssl_enabled, ssl_ca_cert,
        )
        adapter = AdapterFactory.create(db_type, config)
        await adapter.connect(config, user_role=user_role)
        columns = await adapter.get_columns(database, table_name)
        indexes = await adapter.get_indexes(database, table_name)
        await adapter.disconnect()

        logger.info("工具执行成功", tool="describe_table",
                     connection_id=connection_id, table_name=table_name,
                     column_count=len(columns), index_count=len(indexes))
        return {
            "columns": columns,
            "indexes": indexes,
            "summary": f"{table_name}：{len(columns)} 列, {len(indexes)} 个索引",
        }
    except Exception as exc:
        return _safe_tool_call(
            "describe_table", exc,
            connection_id=connection_id,
            database=database,
        )


# =============================================================================
# run_query：执行只读 SQL 查询
# =============================================================================

@tool
async def run_query(
    connection_id: Annotated[str, InjectedToolArg],
    db_type: Annotated[str, InjectedToolArg],
    host: Annotated[str, InjectedToolArg],
    port: Annotated[int, InjectedToolArg],
    database: Annotated[str, InjectedToolArg],
    user: Annotated[str, InjectedToolArg],
    password: Annotated[str, InjectedToolArg],
    sql: str,
    user_role: Annotated[str, InjectedToolArg] = "standard",
    ssl_enabled: Annotated[bool, InjectedToolArg] = False,
    ssl_ca_cert: Annotated[str | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    """执行只读 SQL 查询并返回结果。

    执行流程：
      1. 调用 sql_auditor.audit() 审计 SQL 安全性
      2. 审计通过后调用适配器 execute() 执行查询
      3. 对敏感列进行脱敏处理（T-4：列名正则匹配）

    SAFETY: 所有 SQL 执行前必须经过审计（AGENTS.md §安全与合规红线）。

    Args:
        connection_id: 连接标识符。
        db_type: 数据库类型。
        host: 主机地址。
        port: 端口号。
        database: 数据库名。
        user: 用户名。
        password: 密码。
        sql: 要执行的 SQL 语句。
        user_role: 用户角色（readonly / standard / admin）。
        ssl_enabled: 是否启用 SSL。
        ssl_ca_cert: SSL CA 证书（可选）。

    Returns:
        成功：{"columns": [...], "rows": [...], "execution_time_ms": int,
              "audit_status": "passed"}
        审计拦截：{"error": "SQL 审计未通过", "detail": "...", "violations": [...]}
        失败：{"error": "run_query 执行失败", "detail": "..."}
    """
    # Step 1: SQL 安全审计（AGENTS.md §安全与合规红线）
    # SAFETY: 不跳过 SQL 审计直接执行用户/LLM 生成的 SQL
    audit_result = audit(sql, db_type=db_type, user_role=user_role)
    if not audit_result.passed:
        logger.warning("工具审计拦截", tool="run_query", sql=sql[:200],
                        connection_id=connection_id)
        return {
            "error": "SQL 审计未通过",
            "detail": "语句包含危险操作，已被拦截",
            "violations": [
                {"type": v.type, "message": v.message} for v in audit_result.violations
            ],
            "audit_status": "blocked",
        }

    # Step 2: 执行查询
    try:
        config = _build_config(
            connection_id, db_type, host, port, database, user,
            password, ssl_enabled, ssl_ca_cert,
        )
        adapter = AdapterFactory.create(db_type, config)
        await adapter.connect(config, user_role=user_role)
        result = await adapter.execute(sql)
        await adapter.disconnect()

        # Step 3: 敏感列脱敏（T-4：列名正则匹配）
        # 对列名匹配敏感模式的列进行 *** 替换
        sensitive_patterns = [
            "password", "token", "secret", "key", "auth",
            "credit", "card", "ssn", "id_card", "phone",
            "email", "cert",
        ]
        masked_columns: list[bool] = []
        for col in result["columns"]:
            is_sensitive = any(p in col.lower() for p in sensitive_patterns)
            masked_columns.append(is_sensitive)

        # 对敏感列逐行脱敏
        masked_rows = []
        for row in result["rows"]:
            masked_row = [
                "***" if masked_columns[i] else row[i]
                for i in range(len(row))
            ]
            masked_rows.append(masked_row)

        logger.info("工具执行成功", tool="run_query",
                     connection_id=connection_id,
                     total_rows=result.get("total_rows", 0),
                     audit_status="passed")
        return {
            "columns": result["columns"],
            "rows": masked_rows,
            "total_rows": result["total_rows"],
            "execution_time_ms": result["execution_time_ms"],
            "audit_status": "passed",
            "is_readonly": True,
            "summary": f"返回 {result['total_rows']} 行",
        }

    except Exception as exc:
        # 解析数据库原生错误，返回结构化信息帮助 LLM 自我纠正
        parsed = parse_db_error(exc, db_type, sql)
        logger.warning(
            "SQL 执行失败",
            tool="run_query",
            connection_id=connection_id,
            database=database,
            error_type=parsed.error_type,
            detail=parsed.detail,
        )
        return {
            "error": f"SQL 执行失败：{parsed.detail}",
            "error_type": parsed.error_type,
            "detail": parsed.detail,
            "suggestion": parsed.suggestion,
            "sql": sql,
            "audit_status": "execution_error",
        }
