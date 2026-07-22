"""
连接管理 CRUD API 路由。

依据 api-contract §1.1：GET/POST/PUT/DELETE /api/connections。
依据 AGENTS.md §API 与数据契约：
  - 分页默认 pageSize=20，最大 pageSize=100
  - password 不出现在任何响应中
  - 所有响应含 X-Request-ID 头（由中间件 B-01 注入）
"""

from __future__ import annotations

import html
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, verify_resource_ownership
from app.database import get_session
from app.db.factory import AdapterFactory
from app.models.connection import ConnectionConfigModel
from app.models.schemas import (
    ConnectionCreateRequest,
    ConnectionListResponse,
    ConnectionResponse,
    ConnectionUpdateRequest,
)
from app.models.user import UserModel

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/connections", tags=["Connections"])


# =============================================================================
# GET /api/connections — 获取连接列表（分页）
# =============================================================================

@router.get("", response_model=ConnectionListResponse)
async def list_connections(
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=20, ge=1, alias="pageSize", description="每页条数（最大 100）"),
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: UserModel = Depends(get_current_user),
) -> Any:
    """获取已保存连接列表。

    依据 api-contract §1.1：分页返回，password 不在响应中。
    pageSize 超过 100 时自动截断为 100（AC-6）。
    """
    # 限制 pageSize 最大 100（验收标准 AC-6）
    actual_page_size = min(page_size, 100)

    # 查询基础语句
    count_q = select(func.count()).select_from(ConnectionConfigModel)
    stmt = select(ConnectionConfigModel)

    # 所有用户只能看到自己创建的连接
    count_q = count_q.where(ConnectionConfigModel.user_id == current_user.id)
    stmt = stmt.where(ConnectionConfigModel.user_id == current_user.id)

    # 查询总数
    total_result = await session.execute(count_q)
    total = total_result.scalar() or 0

    # 添加分页和排序
    stmt = (
        stmt
        .offset((page - 1) * actual_page_size)
        .limit(actual_page_size)
        .order_by(ConnectionConfigModel.created_at.desc())
    )
    result = await session.execute(stmt)
    connections = result.scalars().all()

    items = [ConnectionResponse.model_validate(c) for c in connections]
    logger.info("API 请求完成", endpoint="list_connections",
                 total=total, page=page, page_size=actual_page_size)
    return ConnectionListResponse(
        items=items,
        total=total,
        page=page,
        page_size=actual_page_size,
    )


# =============================================================================
# POST /api/connections — 创建新连接
# =============================================================================

@router.post("", status_code=status.HTTP_201_CREATED, response_model=ConnectionResponse)
async def create_connection(
    body: ConnectionCreateRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: UserModel = Depends(get_current_user),
) -> Any:
    """创建新连接。

    依据 api-contract §1.1：
    - 校验 db_type 枚举、name 1-64、port 默认值
    - password 仅在请求中存在，不持久化到 ORM
    - 返回 201 + 不含 password 的连接详情
    """
    conn_id = f"conn_{uuid.uuid4().hex[:8]}"

    # 创建 ORM 模型（不保存 password）
    db_conn = ConnectionConfigModel(
        id=conn_id,
        name=body.name,
        db_type=str(body.db_type),
        host=body.host,
        port=body.port or 3306,  # 默认端口由 validator 保证
        database=body.database,
        user=body.user,
        ssl_enabled=body.ssl_enabled,
        ssl_ca_cert=body.ssl_ca_cert,
        extra_params=body.extra_params,
        status="unknown",  # 新连接默认未测试
        user_id=current_user.id,  # 关联当前用户
    )
    session.add(db_conn)
    await session.commit()
    await session.refresh(db_conn)

    logger.info("API 请求完成", endpoint="create_connection",
                 connection_id=conn_id, name=body.name)
    return ConnectionResponse.model_validate(db_conn)


# =============================================================================
# GET /api/connections/{id} — 获取连接详情
# =============================================================================

@router.get("/{connection_id}", response_model=ConnectionResponse)
async def get_connection(
    connection_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: UserModel = Depends(get_current_user),
) -> Any:
    """获取单个连接详情。

    依据 api-contract §1.1：
    - 响应中不含 password
    - 连接不存在时返回 404 + {"error_code": "NOT_FOUND", "user_message": "..."}
    """
    result = await session.execute(
        select(ConnectionConfigModel).where(ConnectionConfigModel.id == connection_id)
    )
    db_conn = result.scalar_one_or_none()
    # 不存在或不属于当前用户均返回 404
    await verify_resource_ownership(db_conn, current_user, "连接")
    # 类型安全保障：verify_resource_ownership 在 None 时已抛异常
    assert db_conn is not None
    logger.info("API 请求完成", endpoint="get_connection",
                 connection_id=connection_id)
    return ConnectionResponse.model_validate(db_conn)


# =============================================================================
# PUT /api/connections/{id} — 更新连接
# =============================================================================

@router.put("/{connection_id}", response_model=ConnectionResponse)
async def update_connection(
    connection_id: str,
    body: ConnectionUpdateRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: UserModel = Depends(get_current_user),
) -> Any:
    """更新连接配置。

    password 仅在传入非空时更新内存（不写入 ORM）。
    依据 AGENTS.md §安全与合规红线：密码不落盘。
    """
    result = await session.execute(
        select(ConnectionConfigModel).where(ConnectionConfigModel.id == connection_id)
    )
    db_conn = result.scalar_one_or_none()
    # 不存在或不属于当前用户均返回 404
    await verify_resource_ownership(db_conn, current_user, "连接")
    assert db_conn is not None

    # 更新非空字段（password 特殊处理——不入库）
    update_data = body.model_dump(exclude_unset=True, exclude={"password"})
    for field, value in update_data.items():
        setattr(db_conn, field, value)

    # 更新时间戳
    db_conn.updated_at = datetime.now(UTC)

    await session.commit()
    await session.refresh(db_conn)
    logger.info("API 请求完成", endpoint="update_connection",
                 connection_id=connection_id)
    return ConnectionResponse.model_validate(db_conn)


# =============================================================================
# DELETE /api/connections/{id} — 删除连接
# =============================================================================

@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: UserModel = Depends(get_current_user),
) -> None:
    """删除连接。

    返回 204 No Content。
    级联关闭该连接的所有活跃 Session（由 ORM 或 B-24 后台任务处理）。
    """
    result = await session.execute(
        select(ConnectionConfigModel).where(ConnectionConfigModel.id == connection_id)
    )
    db_conn = result.scalar_one_or_none()
    # 不存在或不属于当前用户均返回 404
    await verify_resource_ownership(db_conn, current_user, "连接")
    assert db_conn is not None

    await session.delete(db_conn)
    await session.commit()
    logger.info("API 请求完成", endpoint="delete_connection",
                 connection_id=connection_id)
    # 返回 204（无响应体）


# =============================================================================
# POST /api/connections/{id}/test — 测试连接可用性
# =============================================================================

@router.post("/{connection_id}/test")
async def test_connection(
    connection_id: str,
    password: str | None = Body(default=None, embed=True),
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: UserModel = Depends(get_current_user),
) -> dict:
    """测试连接可用性。

    使用独立短生命周期连接，测试后立即释放。
    成功时更新 last_tested_at + status=healthy，
    失败时更新 status=unreachable（AC-4, AC-5）。
    错误信息不包含密码（frontend AGENTS.md §2）。
    """

    # 从数据库获取连接配置
    result = await session.execute(
        select(ConnectionConfigModel).where(ConnectionConfigModel.id == connection_id)
    )
    db_conn = result.scalar_one_or_none()
    # 不存在或不属于当前用户均返回 404
    await verify_resource_ownership(db_conn, current_user, "连接")
    assert db_conn is not None

    # 构建连接配置（密码从前端传入，不持久化）
    test_config = ConnectionCreateRequest(
        name=db_conn.name,
        db_type=db_conn.db_type,  # type: ignore[arg-type] — Pydantic validator handles str→DBType
        host=db_conn.host,
        port=db_conn.port,
        database=db_conn.database,
        user=db_conn.user,
        password=password or "",
        ssl_enabled=db_conn.ssl_enabled,
        ssl_ca_cert=db_conn.ssl_ca_cert,
        extra_params=db_conn.extra_params,
    )

    # SAFETY: 使用独立短生命周期连接，测试后立即释放（AC-3）
    try:
        start = time.monotonic()
        adapter = AdapterFactory.create(test_config.db_type, test_config)

        # ── 用户角色：来自当前登录用户的 role 字段 ──
        user_role = current_user.role

        await adapter.connect(test_config, user_role=user_role)
        await adapter.disconnect()
        latency_ms = int((time.monotonic() - start) * 1000)

        # 更新成功状态
        db_conn.status = "healthy"
        db_conn.last_tested_at = datetime.now(UTC)
        await session.commit()

        caps = adapter.get_capabilities()
        logger.info("API 请求完成", endpoint="test_connection",
                     connection_id=connection_id, success=True,
                     latency_ms=latency_ms,
                     user_role=user_role)
        return {
            "success": True,
            "latency_ms": latency_ms,
            "version": "connected",
            "user_role": user_role,
            "capabilities": {
                "supports_explain": caps.supports_explain,
                "supports_slow_query_log": caps.supports_slow_query_log,
                "supports_replication": caps.supports_replication,
                "supports_table_spaces": caps.supports_table_spaces,
            },
        }

    except Exception as exc:
        # SAFETY: 错误信息不包含密码（frontend AGENTS.md §2）
        db_conn.status = "unreachable"
        db_conn.last_tested_at = datetime.now(UTC)
        await session.commit()

        logger.warning("API 请求完成", endpoint="test_connection",
                        connection_id=connection_id, success=False,
                        error=str(exc)[:200])

        return {
            "success": False,
            "error_code": "DB_UNREACHABLE",
            "user_message": f"无法连接到 {test_config.host}:{test_config.port} "
            f"— 请检查网络、用户名和密码",
        }


# =============================================================================
# GET /api/connections/{id}/metadata — 获取 Schema 元数据
# =============================================================================

@router.get("/{connection_id}/metadata")
async def get_metadata(
    connection_id: str,
    password: str = Body(..., embed=True),
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: UserModel = Depends(get_current_user),
) -> JSONResponse:
    """获取数据库 Schema 元数据。

    返回 databases、tables、columns、indexes 结构。
    设置 Cache-Control: private, max-age=300（5 分钟前端缓存）。
    """
    # 从数据库获取连接配置
    result = await session.execute(
        select(ConnectionConfigModel).where(ConnectionConfigModel.id == connection_id)
    )
    db_conn = result.scalar_one_or_none()
    # 不存在或不属于当前用户均返回 404
    await verify_resource_ownership(db_conn, current_user, "连接")
    assert db_conn is not None

    # 构建连接配置
    config = ConnectionCreateRequest(
        name=db_conn.name,
        db_type=db_conn.db_type,  # type: ignore[arg-type] — Pydantic validator handles str→DBType
        host=db_conn.host,
        port=db_conn.port,
        database=db_conn.database,
        user=db_conn.user,
        password=password,
        ssl_enabled=db_conn.ssl_enabled,
        ssl_ca_cert=db_conn.ssl_ca_cert,
        extra_params=db_conn.extra_params,
    )

    def _sanitize(text: str | None) -> str:
        """XSS 安全处理：转义 HTML 特殊字符（AC-4）。"""
        return html.escape(text or "")

    try:
        adapter = AdapterFactory.create(config.db_type, config)
        await adapter.connect(config)

        # 获取数据库列表
        databases = await adapter.get_databases()

        # 获取每个数据库的表、列、索引
        tables_result = []
        for db_name in databases[:50]:  # 限制数据库数量
            db_tables = await adapter.get_tables(db_name)
            for tbl in db_tables:
                schema_table = tbl["table_name"]
                columns = await adapter.get_columns(db_name, schema_table)
                indexes = await adapter.get_indexes(db_name, schema_table)

                sanitized_columns = [
                    {
                        "name": _sanitize(c["name"]),
                        "type": _sanitize(c["type"]),
                        "nullable": c["nullable"],
                        "is_primary": c["is_primary"],
                        "comment": _sanitize(c["comment"]),
                    }
                    for c in columns
                ]

                sanitized_indexes = [
                    {
                        "name": _sanitize(idx["name"]),
                        "columns": [_sanitize(c) for c in idx["columns"]],
                        "is_unique": idx["is_unique"],
                        "type": _sanitize(idx["type"]),
                    }
                    for idx in indexes
                ]

                tables_result.append({
                    "database": _sanitize(db_name),
                    "table_name": _sanitize(schema_table),
                    "comment": _sanitize(tbl.get("comment", "")),
                    "row_count_estimate": tbl.get("row_count_estimate", 0),
                    "columns": sanitized_columns,
                    "indexes": sanitized_indexes,
                })

        await adapter.disconnect()

        result_data = {
            "databases": [_sanitize(d) for d in databases],
            "tables": tables_result,
        }

        logger.info("API 请求完成", endpoint="get_metadata",
                     connection_id=connection_id,
                     databases=len(result_data.get("databases", [])),
                     tables=len(result_data.get("tables", [])))
        response = JSONResponse(content=result_data)
        response.headers["Cache-Control"] = "private, max-age=300"
        return response

    except Exception as exc:
        logger.warning("API 请求失败", endpoint="get_metadata",
                        connection_id=connection_id, error=str(exc)[:200])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error_code": "DB_UNREACHABLE",
                "user_message": f"无法获取元数据：{exc}",
            },
        ) from exc
