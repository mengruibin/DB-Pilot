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

import re
from typing import Annotated, Any

import structlog
from langchain_core.tools import InjectedToolArg, tool

from app.db.factory import AdapterFactory
from app.engine.data_masking import mask_query_result
from app.engine.sql_error_parser import parse_db_error
from app.models.schemas import ConnectionCreateRequest

# 只读 SQL 语句前缀匹配：匹配其中任一视为不修改数据（SELECT/SHOW/DESC/EXPLAIN 等）
_IS_READONLY_SQL = re.compile(
    r"^\s*(?:SELECT|SHOW|DESC|DESCRIBE|EXPLAIN|WITH|USE|SET)\b",
    re.IGNORECASE,
)

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
    fn_name: str,
    exc: Exception,
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
    logger.error(
        "工具调用失败",
        tool=fn_name,
        connection_id=connection_id,
        database=database,
        error=str(exc)[:200],
    )
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
    user_role: Annotated[str, InjectedToolArg] = "readonly",  # 未显式传入时采用更保守的只读兜底
    ssl_enabled: Annotated[bool, InjectedToolArg] = False,
    ssl_ca_cert: Annotated[str | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    """获取当前数据库中所有表的基本信息（表名、注释、行数估计）。
    适用于浏览数据库结构，或在 NL2SQL 场景中获取完整的表清单作为 Schema 上下文。

    Returns:
        成功: {
            "tables": [
                {
                    "table_name": str,       // 表名
                    "comment": str,          // 表注释（无注释时为空字符串）
                    "row_count_estimate": int  // 行数估计（0 表示未知或空表）
                }
            ],
            "summary": str
        }
        失败: {"error": str, "detail": str}
    """
    # SAFETY: 使用参数化连接配置，不拼接连接串（AGENTS.md §数据库操作原则）
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
        tables = await adapter.get_tables(database)
        await adapter.disconnect()

        logger.info(
            "工具执行成功", tool="list_tables", connection_id=connection_id, table_count=len(tables)
        )
        return {
            "tables": tables,
            "summary": f"找到 {len(tables)} 张表",
        }
    except Exception as exc:
        return _safe_tool_call(
            "list_tables",
            exc,
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
    table_names: list[str],
    user_role: Annotated[str, InjectedToolArg] = "readonly",  # 未显式传入时采用更保守的只读兜底
    ssl_enabled: Annotated[bool, InjectedToolArg] = False,
    ssl_ca_cert: Annotated[str | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    """获取指定表的详细结构，包含列定义和索引信息。
    支持批量查询多张表，建议将需要查询的多张表一并传入以减少工具调用次数。
    适用于 NL2SQL 的 Schema 上下文补充，或查看某张表的具体列类型、索引设计。

    Args:
        table_names: 目标表名列表（必须是当前数据库中已有的表，可通过 list_tables 获取）。

    Returns:
        成功: {
            "tables": [
                {
                    "table_name": str,      // 表名
                    "columns": [
                        {
                            "name": str,          // 列名
                            "type": str,          // 数据类型（如 "varchar(255)", "bigint"）
                            "nullable": bool,     // 是否允许 NULL
                            "is_primary": bool,   // 是否为主键
                            "comment": str        // 列注释
                        }
                    ],
                    "indexes": [
                        {
                            "name": str,          // 索引名
                            "columns": [str],     // 索引包含的列名列表
                            "is_unique": bool,    // 是否唯一索引
                            "type": str           // 索引类型（如 "BTREE", "HASH"）
                        }
                    ],
                    "summary": str
                }
            ],
            "summary": str                      // 总览信息
        }
        失败: {"error": str, "detail": str}
    """
    # SAFETY: 使用参数化连接配置，不拼接连接串
    # 参数校验：空列表直接返回错误
    if not table_names:
        return {
            "error": "describe_table 参数错误",
            "detail": "table_names 不能为空列表，请提供至少一个表名",
        }
    # 去重处理：LLM 可能传入重复表名
    unique_tables = list(dict.fromkeys(table_names))

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

        # 逐表查询，每张表独立 try/except，单表失败不影响其他表
        tables_data: list[dict[str, Any]] = []
        for tbl in unique_tables:
            try:
                columns = await adapter.get_columns(database, tbl)
                indexes = await adapter.get_indexes(database, tbl)
                tables_data.append(
                    {
                        "table_name": tbl,
                        "columns": columns,
                        "indexes": indexes,
                        "summary": f"{tbl}：{len(columns)} 列, {len(indexes)} 个索引",
                    }
                )
            except Exception as tbl_exc:
                # 单表失败已隔离，不影响其他表继续查询
                logger.warning(
                    "描述表结构失败（已隔离）",
                    tool="describe_table",
                    table_name=tbl,
                    error=str(tbl_exc)[:200],
                )
                tables_data.append(
                    {
                        "table_name": tbl,
                        "columns": [],
                        "indexes": [],
                        "error": str(tbl_exc)[:200],
                        "summary": f"{tbl}：查询失败 - {str(tbl_exc)[:100]}",
                    }
                )

        await adapter.disconnect()

        total_tables = len(tables_data)
        successful = sum(1 for t in tables_data if t.get("columns") or t.get("indexes"))
        logger.info(
            "工具执行成功",
            tool="describe_table",
            connection_id=connection_id,
            table_count=total_tables,
            successful=successful,
        )

        # 拼接总览：列出每张表的状态
        table_summaries = "，".join(t["summary"] for t in tables_data)
        return {
            "tables": tables_data,
            "summary": f"共查询 {total_tables} 张表：{table_summaries}",
        }
    except Exception as exc:
        return _safe_tool_call(
            "describe_table",
            exc,
            connection_id=connection_id,
            database=database,
        )


# =============================================================================
# _run_sql：SQL 执行核心实现（内部函数，不注册为工具）
# =============================================================================


async def _run_sql(
    sql: str,
    connection_id: str,
    db_type: str,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    user_role: str = "readonly",
    ssl_enabled: bool = False,
    ssl_ca_cert: str | None = None,
    tool_name: str = "_run_sql",
) -> dict[str, Any]:
    """执行 SQL 语句并返回查询结果（内部函数，由 execute_readonly_sql / execute_write_sql 调用）。

    安全约束：
      - 写操作（INSERT/UPDATE/DELETE）仅在当前用户角色为 admin 时允许
      - readonly 角色仅可执行 SELECT/SHOW/DESC/EXPLAIN 等只读语句
      - 结果中的敏感列（password、token、phone 等）自动以 "***" 掩码
      - 不支持多条语句批处理

    Args:
        sql: 要执行的 SQL 语句。
        tool_name: 调用方工具名（用于日志区分）。

    Returns:
        成功: {
            "columns": [str],            // 结果集列名列表
            "rows": [[any]],             // 结果集数据行
            "total_rows": int,           // 总行数
            "execution_time_ms": int,    // 执行耗时（毫秒）
            "audit_status": "passed",    // 固定值
            "is_readonly": bool,         // true=只读查询，false=写操作
            "summary": str
        }
        执行失败: {
            "error": str,
            "error_type": str,           // 错误类型（如 syntax_error, permission_denied）
            "detail": str,
            "suggestion": str | null,    // 修正建议
            "audit_status": "execution_error"
        }
    """
    # Step 1: 判断本次 SQL 是否为只读（用于返回值和日志）
    is_readonly = bool(_IS_READONLY_SQL.match(sql))

    # Step 2: 执行查询
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
        result = await adapter.execute(sql)
        await adapter.disconnect()

        # Step 3: 敏感列脱敏（T-4：列名正则匹配）
        # 与导出端点共用 app/engine/data_masking.mask_query_result，保证脱敏一致
        _, masked_rows = mask_query_result(result["columns"], result["rows"])

        # 写操作记录额外审计日志
        if not is_readonly:
            sql_type = sql.strip().split()[0].upper() if sql.strip() else "UNKNOWN"
            logger.info(
                "写操作执行成功",
                tool=tool_name,
                connection_id=connection_id,
                sql_type=sql_type,
                affected_rows=result.get("total_rows", 0),
                user_role=user_role,
            )
        else:
            logger.info(
                "工具执行成功",
                tool=tool_name,
                connection_id=connection_id,
                total_rows=result.get("total_rows", 0),
                audit_status="passed",
            )

        summary = (
            f"影响 {result.get('affected_rows', result['total_rows'])} 行"
            if not is_readonly
            else f"返回 {result['total_rows']} 行"
        )
        return {
            "columns": result["columns"],
            "rows": masked_rows,
            "total_rows": result["total_rows"],
            "execution_time_ms": result["execution_time_ms"],
            "audit_status": "passed",
            "is_readonly": is_readonly,
            "summary": summary,
        }

    except Exception as exc:
        # 解析数据库原生错误，返回结构化信息帮助 LLM 自我纠正
        parsed = parse_db_error(exc, db_type, sql)
        logger.warning(
            "SQL 执行失败",
            tool=tool_name,
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
            "audit_status": "execution_error",
        }


# =============================================================================
# execute_readonly_sql：执行只读 SQL 查询
# =============================================================================


@tool(
    extras={
        "needs_sql_audit": True,
        "needs_row_estimation": True,
    },
)
async def execute_readonly_sql(
    sql: str,
    connection_id: Annotated[str, InjectedToolArg],
    db_type: Annotated[str, InjectedToolArg],
    host: Annotated[str, InjectedToolArg],
    port: Annotated[int, InjectedToolArg],
    database: Annotated[str, InjectedToolArg],
    user: Annotated[str, InjectedToolArg],
    password: Annotated[str, InjectedToolArg],
    user_role: Annotated[str, InjectedToolArg] = "readonly",
    ssl_enabled: Annotated[bool, InjectedToolArg] = False,
    ssl_ca_cert: Annotated[str | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    """执行只读 SQL 查询（SELECT / SHOW / DESCRIBE / EXPLAIN / WITH）。
    写操作（INSERT / UPDATE / DELETE）请使用 execute_write_sql 工具。
    安全审计由 tool_node 安全护栏层在工具执行前统一检查。

    Args:
        sql: 要执行的只读 SQL 语句。

    Returns:
        同 _run_sql（成功时返回 columns/rows/total_rows 等字段）。
    """
    return await _run_sql(
        sql, connection_id, db_type, host, port, database,
        user, password, user_role=user_role,
        ssl_enabled=ssl_enabled, ssl_ca_cert=ssl_ca_cert,
        tool_name="execute_readonly_sql",
    )


# =============================================================================
# execute_write_sql：执行写 SQL（需用户确认）
# =============================================================================


@tool(
    extras={
        "needs_write_confirmation": True,
        "confirm_category": "sql_write",
    },
)
async def execute_write_sql(
    sql: str,
    connection_id: Annotated[str, InjectedToolArg],
    db_type: Annotated[str, InjectedToolArg],
    host: Annotated[str, InjectedToolArg],
    port: Annotated[int, InjectedToolArg],
    database: Annotated[str, InjectedToolArg],
    user: Annotated[str, InjectedToolArg],
    password: Annotated[str, InjectedToolArg],
    user_role: Annotated[str, InjectedToolArg] = "readonly",
    ssl_enabled: Annotated[bool, InjectedToolArg] = False,
    ssl_ca_cert: Annotated[str | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    """执行写 SQL 语句（INSERT / UPDATE / DELETE）。
    执行前系统会请求用户确认，用户批准后才会实际执行。
    只读查询（SELECT / SHOW / DESCRIBE / EXPLAIN）请使用 execute_readonly_sql 工具。

    Args:
        sql: 要执行的写 SQL 语句。

    Returns:
        同 _run_sql（成功时返回 columns/rows/total_rows 等字段）。
    """
    return await _run_sql(
        sql, connection_id, db_type, host, port, database,
        user, password, user_role=user_role,
        ssl_enabled=ssl_enabled, ssl_ca_cert=ssl_ca_cert,
        tool_name="execute_write_sql",
    )
