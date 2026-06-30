"""
故障排查类 Agent 工具集。

包含 check_connections / check_locks / check_replication 三个工具。
每个工具用 @tool 装饰，返回 Python dict。
依据 PRD §5.3 故障类型覆盖、AGENTS.md §工具函数返回契约。

工具函数返回契约（AGENTS.md §API 与数据契约）：
  - 所有 @tool 装饰的函数返回 Python 原生类型（dict/list/str）
  - 工具函数内部捕获异常后返回 {"error": "...", "detail": "..."}
  - MUST NOT 向上抛出未处理异常
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from app.db.factory import AdapterFactory
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


def _classify_usage(usage_percent: float) -> str:
    """根据连接使用率判定健康状态。

    Args:
        usage_percent: 当前连接使用率（0-100）。

    Returns:
        "pass"（≤80%）、"warning"（>80%）、"error"（>95%）。
    """
    if usage_percent > 95:
        return "error"
    if usage_percent > 80:
        return "warning"
    return "pass"


# =============================================================================
# check_connections：检查数据库连接池状态
# =============================================================================


@tool
async def check_connections(
    connection_id: str,
    db_type: str,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    ssl_enabled: bool = False,
    ssl_ca_cert: str | None = None,
) -> dict[str, Any]:
    """检查目标数据库的连接池状态。

    调用适配器的 get_connections_status() 获取连接数指标，
    根据使用率判定健康状态（PRD §5.3）。
    适用于排查连接池耗尽、连接数异常增长等场景。

    Args:
        connection_id: 连接标识符（用于日志追踪）。
        db_type: 数据库类型（mysql / postgresql / oracle）。
        host: 数据库主机地址。
        port: 数据库端口号。
        database: 目标数据库名。
        user: 连接用户名。
        password: 连接密码。
        ssl_enabled: 是否启用 SSL。
        ssl_ca_cert: SSL CA 证书（可选）。

    Returns:
        成功：{"status": "pass|warning|error", "data": {...ConnectionStatus}}
        失败：{"error": "check_connections 执行失败", "detail": "..."}
    """
    # SAFETY: 只读操作，不修改数据库状态（PRD §8.1 Layer 1）
    try:
        config = _build_config(
            connection_id, db_type, host, port, database, user,
            password, ssl_enabled, ssl_ca_cert,
        )
        adapter = AdapterFactory.create(db_type, config)
        await adapter.connect(config)

        status_data = await adapter.get_connections_status()
        await adapter.disconnect()

        usage = float(status_data.get("usage_percent", 0))
        status = _classify_usage(usage)

        return {
            "status": status,
            "data": status_data,
        }
    except Exception as exc:
        return _safe_tool_call("check_connections", exc)


# =============================================================================
# check_locks：检查数据库锁等待状态
# =============================================================================


@tool
async def check_locks(
    connection_id: str,
    db_type: str,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    ssl_enabled: bool = False,
    ssl_ca_cert: str | None = None,
) -> dict[str, Any]:
    """检查目标数据库的锁等待情况。

    调用适配器的 get_lock_info() 获取当前锁等待信息，
    识别阻塞事务（PRD §5.3 死锁检测）。
    适用于排查死锁、锁超时、事务阻塞等场景。

    Args:
        connection_id: 连接标识符。
        db_type: 数据库类型。
        host: 主机地址。
        port: 端口号。
        database: 数据库名。
        user: 用户名。
        password: 密码。
        ssl_enabled: 是否启用 SSL。
        ssl_ca_cert: SSL CA 证书（可选）。

    Returns:
        成功：{"status": "pass|warning|error",
              "waiting_transactions": int,
              "blocking_trx_id": "..."}
        失败：{"error": "check_locks 执行失败", "detail": "..."}
    """
    # SAFETY: 只读操作，不修改数据库状态（PRD §8.1 Layer 1）
    try:
        config = _build_config(
            connection_id, db_type, host, port, database, user,
            password, ssl_enabled, ssl_ca_cert,
        )
        adapter = AdapterFactory.create(db_type, config)
        await adapter.connect(config)

        locks = await adapter.get_lock_info()
        await adapter.disconnect()

        waiting_count = len(locks)

        # 有锁等待时 status 至少为 warning
        if waiting_count > 0:
            # 取首个等待事务的阻塞事务 ID
            blocking_id: str = ""
            for lock in locks:
                bid = lock.get("blocking_transaction_id", "")
                if bid:
                    blocking_id = str(bid)
                    break

            return {
                "status": "warning" if waiting_count <= 3 else "error",
                "waiting_transactions": waiting_count,
                "blocking_trx_id": blocking_id,
                "locks": locks,
            }

        return {
            "status": "pass",
            "waiting_transactions": 0,
            "blocking_trx_id": "",
        }
    except Exception as exc:
        return _safe_tool_call("check_locks", exc)


# =============================================================================
# check_replication：检查主从复制状态
# =============================================================================


@tool
async def check_replication(
    connection_id: str,
    db_type: str,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    ssl_enabled: bool = False,
    ssl_ca_cert: str | None = None,
) -> dict[str, Any]:
    """检查目标数据库的主从复制状态。

    先检查适配器能力声明是否支持复制检测，
    不支持时返回 status="skipped"（PRD §5.3）。
    适用于排查主从延迟、复制中断等场景。

    Args:
        connection_id: 连接标识符。
        db_type: 数据库类型。
        host: 主机地址。
        port: 端口号。
        database: 数据库名。
        user: 用户名。
        password: 密码。
        ssl_enabled: 是否启用 SSL。
        ssl_ca_cert: SSL CA 证书（可选）。

    Returns:
        成功：{"status": "pass|warning|error|skipped",
              "delay_seconds": int|None}
        失败：{"error": "check_replication 执行失败", "detail": "..."}
    """
    # SAFETY: 只读操作，不修改数据库状态（PRD §8.1 Layer 1）
    try:
        config = _build_config(
            connection_id, db_type, host, port, database, user,
            password, ssl_enabled, ssl_ca_cert,
        )
        adapter = AdapterFactory.create(db_type, config)
        await adapter.connect(config)

        # 检查适配器是否支持复制检测
        capabilities = adapter.get_capabilities()
        if not capabilities.supports_replication:
            await adapter.disconnect()
            return {
                "status": "skipped",
                "delay_seconds": None,
            }

        repl_status = await adapter.get_replication_status()
        await adapter.disconnect()

        # 若适配器返回 skipped，透传
        if repl_status.get("status") == "skipped":
            return {
                "status": "skipped",
                "delay_seconds": None,
            }

        delay = repl_status.get("delay_seconds")
        delay_sec: int | None = int(delay) if delay is not None else None

        # 根据延迟秒数判定状态（PRD §5.3）
        if delay_sec is None:
            status = "warning"
        elif delay_sec > 60:
            status = "error"
        elif delay_sec > 10:
            status = "warning"
        else:
            status = "pass"

        return {
            "status": status,
            "delay_seconds": delay_sec,
        }
    except Exception as exc:
        return _safe_tool_call("check_replication", exc)
