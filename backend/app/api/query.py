"""
直接查询与诊断 REST API 路由。

三个端点（api-contract §1.3）：
  - POST /api/connections/{id}/query     执行只读 SQL（经审计后执行）
  - POST /api/connections/{id}/explain   获取 SQL 执行计划 + LLM 分析
  - GET  /api/connections/{id}/slow-queries  获取慢查询列表（分页）

依据 api-contract §1.3（query/explain/slow-queries 三个端点）。
依据 AGENTS.md §安全与合规红线（SQL 审计拦截返回 400 而非 500）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.db.factory import AdapterFactory
from app.models.connection import ConnectionConfigModel
from app.models.schemas import (
    ConnectionCreateRequest,
    ExplainRequest,
    QueryRequest,
    SlowQueryListResponse,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/connections", tags=["Query & Diagnosis"])


# =============================================================================
# 通用辅助：从 ORM 加载连接配置 + 创建适配器
# =============================================================================

async def _load_and_create_adapter(
    connection_id: str,
    password: str | None,
    session: AsyncSession,
) -> tuple[Any, ConnectionCreateRequest]:
    """从 ORM 加载连接配置并创建目标数据库适配器实例。

    Args:
        connection_id: 连接 ID。
        password: 前端传入的连接密码（非持久化）。
        session: 内部 SQLite 数据库会话。

    Returns:
        (adapter, config) 元组，调用方负责 connect/disconnect。

    Raises:
        HTTPException: 连接不存在时返回 404。
    """
    result = await session.execute(
        select(ConnectionConfigModel).where(
            ConnectionConfigModel.id == connection_id
        )
    )
    db_conn = result.scalar_one_or_none()
    if db_conn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "NOT_FOUND",
                "user_message": f"连接 {connection_id} 不存在或已删除",
            },
        )

    # SAFETY: 密码仅存于内存，不持久化（AGENTS.md §安全与合规红线）
    config = ConnectionCreateRequest(
        name=db_conn.name,
        db_type=db_conn.db_type,
        host=db_conn.host,
        port=db_conn.port,
        database=db_conn.database,
        user=db_conn.user,
        password=password or "",
        ssl_enabled=db_conn.ssl_enabled,
        ssl_ca_cert=db_conn.ssl_ca_cert,
        extra_params=db_conn.extra_params,
    )

    adapter = AdapterFactory.create(config.db_type, config)
    return adapter, config


# =============================================================================
# POST /api/connections/{id}/query — 执行只读 SQL（直接模式）
# =============================================================================


@router.post("/{connection_id}/query")
async def execute_query(
    connection_id: str,
    body: QueryRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """执行只读 SQL 查询（直接模式）。

    1. 加载连接配置
    2. SQL 安全审计（AC-2：拦截返回 400 而非 500）
    3. 在目标数据库上执行
    4. 返回 QueryResult

    Args:
        connection_id: 连接 ID。
        body: QueryRequest 请求体。
        session: 内部 SQLite 数据库会话。

    Returns:
        QueryResult（{columns, rows, total_rows, execution_time_ms, is_readonly, audit_status}）。

    Raises:
        HTTPException 400: SQL 审计拦截。
        HTTPException 404: 连接不存在。
        HTTPException 408: 查询超时。
        HTTPException 502: 数据库不可达。
    """
    # AC-6：入口日志
    logger.info("API 请求开始", endpoint="query",
                connection_id=connection_id, sql_length=len(body.sql))

    try:
        adapter, config = await _load_and_create_adapter(
            connection_id, body.password, session,
        )

        # AC-1：所有 SQL 经过 sql_auditor.audit() 校验后执行
        from app.engine.sql_auditor import audit

        audit_result = audit(body.sql, db_type=config.db_type)
        if not audit_result.passed:
            violation = audit_result.violations[0]
            # AC-2：审计拦截以 HTTP 400 返回（非 500）
            logger.warning("SQL审计拦截", endpoint="query",
                           connection_id=connection_id,
                           violation_type=violation.type)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error_code": "SQL_AUDIT_BLOCKED",
                    "user_message": violation.message,
                    "sql_highlight": violation.location,
                    "severity": "warning",
                },
            )

        logger.info("SQL审计通过", endpoint="query",
                    connection_id=connection_id, is_readonly=audit_result.is_readonly)

        # 连接目标数据库
        await adapter.connect(config)

        # AC-1：执行查询（带超时保护）
        try:
            result = await asyncio.wait_for(
                adapter.execute(body.sql, body.params),
                timeout=body.max_execution_ms / 1000,
            )
        except TimeoutError:
            logger.warning("查询超时", endpoint="query",
                           connection_id=connection_id,
                           max_execution_ms=body.max_execution_ms)
            raise HTTPException(
                status_code=status.HTTP_408_REQUEST_TIMEOUT,
                detail={
                    "error_code": "EXECUTION_TIMEOUT",
                    "user_message": f"查询超时（{body.max_execution_ms}ms），"
                                    "建议添加索引或缩小查询范围",
                    "killed": True,
                },
            ) from None

        await adapter.disconnect()

        # 直接返回 adapter.execute() 的结果（已符合契约 QueryResult 格式）
        logger.info("API 请求完成", endpoint="query",
                    connection_id=connection_id,
                    rows_returned=result.get("total_rows", 0),
                    execution_time_ms=result.get("execution_time_ms", 0))
        return result

    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("API 请求失败", endpoint="query",
                       connection_id=connection_id, error=str(exc)[:200])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error_code": "DB_UNREACHABLE",
                "user_message": f"数据库执行错误：{exc}",
            },
        ) from exc


# =============================================================================
# POST /api/connections/{id}/explain — 获取 SQL 执行计划 + LLM 分析
# =============================================================================


@router.post("/{connection_id}/explain")
async def execute_explain(
    connection_id: str,
    body: ExplainRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """获取 SQL 执行计划并进行分析（AC-3）。

    1. 加载连接配置
    2. 调用适配器 explain() 获取执行计划
    3. LLM 分析瓶颈（异常时降级到规则引擎）
    4. 返回 {explain_output, parsed, format}

    Args:
        connection_id: 连接 ID。
        body: ExplainRequest 请求体。
        session: 内部 SQLite 数据库会话。

    Returns:
        {explain_output, parsed, format}。
    """
    logger.info("API 请求开始", endpoint="explain",
                connection_id=connection_id, sql_length=len(body.sql))

    try:
        adapter, config = await _load_and_create_adapter(
            connection_id, body.password, session,
        )

        await adapter.connect(config)

        # 检查适配器是否支持 EXPLAIN
        caps = adapter.get_capabilities()
        if not caps.supports_explain:
            await adapter.disconnect()
            logger.warning("EXPLAIN 不支持", endpoint="explain",
                           connection_id=connection_id,
                           db_type=config.db_type)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error_code": "FEATURE_NOT_SUPPORTED",
                    "user_message": f"{config.db_type} 不支持 EXPLAIN",
                },
            )

        # AC-3：调用适配器 explain()
        explain_result = await adapter.explain(body.sql)
        await adapter.disconnect()

        explain_output = explain_result.get("explain_output", "")
        explain_fmt = explain_result.get("format", "text")

        # AC-3：LLM 分析执行计划（异常时降级到规则引擎）
        from app.engine.diagnosis import analyze_explain
        from app.prompts.diagnosis import rule_based_analyze

        try:
            parsed = await analyze_explain(
                explain_output=explain_output,
                sql=body.sql,
                db_type=config.db_type,
            )
            analysis_source = "llm"
        except Exception:
            logger.info("诊断分析降级到规则引擎", endpoint="explain",
                        connection_id=connection_id)
            parsed = rule_based_analyze(
                explain_output=explain_output,
                sql=body.sql,
            )
            analysis_source = "rule"

        logger.info("API 请求完成", endpoint="explain",
                    connection_id=connection_id,
                    format=explain_fmt,
                    analysis_source=analysis_source)
        return {
            "explain_output": explain_output,
            "parsed": parsed,
            "format": explain_fmt,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("API 请求失败", endpoint="explain",
                       connection_id=connection_id, error=str(exc)[:200])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error_code": "DB_UNREACHABLE",
                "user_message": f"执行计划分析失败：{exc}",
            },
        ) from exc


# =============================================================================
# GET /api/connections/{id}/slow-queries — 获取慢查询列表（分页）
# =============================================================================


@router.get("/{connection_id}/slow-queries")
async def list_slow_queries(
    connection_id: str,
    time_range: str = Query(default="1h", description="时间范围（1h/6h/24h/7d）"),
    limit: int = Query(default=20, ge=1, le=100, description="最大返回条数"),
    sort_by: str = Query(default="query_time", description="排序字段"),
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(
        default=20, ge=1, le=100, alias="pageSize",
        description="每页条数（最大 100）",
    ),
    password: str | None = Query(default=None, description="连接密码"),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SlowQueryListResponse:
    """获取慢查询列表（分页）。

    1. 加载连接配置
    2. 调用适配器 get_slow_queries()
    3. 若未启用慢日志，返回 200 + warning（AC-5）
    4. 否则内存分页后返回

    Args:
        connection_id: 连接 ID。
        time_range: 时间范围。
        limit: 最大返回条数。
        sort_by: 排序字段。
        page: 页码。
        page_size: 每页条数。
        password: 连接密码。
        session: 内部 SQLite 数据库会话。

    Returns:
        SlowQueryListResponse（分页慢查询列表）。
    """
    logger.info("API 请求开始", endpoint="slow_queries",
                connection_id=connection_id,
                time_range=time_range, limit=limit, page=page)

    try:
        adapter, config = await _load_and_create_adapter(
            connection_id, password, session,
        )

        await adapter.connect(config)

        # AC-4：调用适配器获取慢查询
        result = await adapter.get_slow_queries(limit=limit, time_range=time_range)
        await adapter.disconnect()

        # AC-5：若慢查询日志未启用，返回 200 + warning
        if "warning" in result:
            logger.warning("慢查询日志不可用", endpoint="slow_queries",
                           connection_id=connection_id,
                           warning=result["warning"])
            return SlowQueryListResponse(
                items=[],
                total=0,
                page=1,
                page_size=page_size,
                warning=result["warning"],
            )

        # AC-4：内存分页
        all_items = result.get("items", [])
        total = result.get("total", len(all_items))
        start = (page - 1) * page_size
        end = start + page_size
        page_items = all_items[start:end]

        # Pydantic 自动将 dict 列表验证为 SlowQueryItem 列表
        logger.info("API 请求完成", endpoint="slow_queries",
                    connection_id=connection_id,
                    item_count=len(page_items),
                    total=total)
        return SlowQueryListResponse(
            items=page_items,  # type: ignore[arg-type]
            total=total,
            page=page,
            page_size=page_size,
            warning=None,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("API 请求失败", endpoint="slow_queries",
                       connection_id=connection_id, error=str(exc)[:200])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error_code": "DB_UNREACHABLE",
                "user_message": f"获取慢查询失败：{exc}",
            },
        ) from exc
