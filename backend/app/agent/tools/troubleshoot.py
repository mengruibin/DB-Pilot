"""
故障排查类 Agent 工具集。

包含 check_connections / check_locks / analyze_locks / kill_transaction /
check_replication 共五个工具。
每个工具用 @tool 装饰，返回 Python dict。
依据 PRD §5.3 故障类型覆盖、AGENTS.md §工具函数返回契约。

工具函数返回契约（AGENTS.md §API 与数据契约）：
  - 所有 @tool 装饰的函数返回 Python 原生类型（dict/list/str）
  - 工具函数内部捕获异常后返回 {"error": "...", "detail": "..."}
  - MUST NOT 向上抛出未处理异常
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Annotated, Any

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
    connection_id: Annotated[str, InjectedToolArg],
    db_type: Annotated[str, InjectedToolArg],
    host: Annotated[str, InjectedToolArg],
    port: Annotated[int, InjectedToolArg],
    database: Annotated[str, InjectedToolArg],
    user: Annotated[str, InjectedToolArg],
    password: Annotated[str, InjectedToolArg],
    ssl_enabled: Annotated[bool, InjectedToolArg] = False,
    ssl_ca_cert: Annotated[str | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    """检查目标数据库的连接池状态，包括活跃/空闲/等待连接数及使用率。
    适用于排查连接池耗尽、连接数异常增长、连接泄漏等场景。

    Returns:
        成功: {
            "status": "pass" | "warning" | "error",  // pass(<=80%), warning(>80%), error(>95%)
            "data": {
                "total_connections": int,         // 最大连接数（max_connections）
                "active_connections": int,        // 当前活跃连接数
                "idle_connections": int,          // 空闲连接数
                "waiting_connections": int,       // 等待中的连接数
                "usage_percent": float,           // 连接使用率（0-100）
                "aborted_connections_rate": float,// 异常连接率
                "sampled_at": str                 // 采样时间（ISO 8601）
            },
            "summary": str
        }
        失败: {"error": str, "detail": str}
    """
    # SAFETY: 只读操作，不修改数据库状态（PRD §8.1 Layer 1）
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
        await adapter.connect(config)

        status_data = await adapter.get_connections_status()
        await adapter.disconnect()

        usage = float(status_data.get("usage_percent", 0))
        status = _classify_usage(usage)

        logger.info(
            "工具执行成功", tool="check_connections", connection_id=connection_id, status=status
        )
        return {
            "status": status,
            "data": status_data,
            "summary": f"连接使用率 {usage:.1f}%（{status}）",
        }
    except Exception as exc:
        return _safe_tool_call("check_connections", exc)


# =============================================================================
# check_locks：检查数据库锁等待状态
# =============================================================================


@tool
async def check_locks(
    connection_id: Annotated[str, InjectedToolArg],
    db_type: Annotated[str, InjectedToolArg],
    host: Annotated[str, InjectedToolArg],
    port: Annotated[int, InjectedToolArg],
    database: Annotated[str, InjectedToolArg],
    user: Annotated[str, InjectedToolArg],
    password: Annotated[str, InjectedToolArg],
    ssl_enabled: Annotated[bool, InjectedToolArg] = False,
    ssl_ca_cert: Annotated[str | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    """检查目标数据库当前的锁等待情况，检测阻塞事务和等待链。
    适用于排查死锁、锁超时、事务阻塞导致性能下降等场景。
    返回完整锁拓扑：持有锁列表 + 等待锁列表，含表名、锁模式、锁类型。

    Returns:
        无锁等待: {
            "status": "pass",
            "total_held": int,
            "total_waiting": 0,
            "held_locks": [],
            "waiting_locks": [],
            "summary": str
        }
        有锁等待: {
            "status": "warning" | "error",  // warning(<=3个等待), error(>3个等待)
            "total_held": int,              // 持有锁的事务数
            "total_waiting": int,           // 等待锁的事务数
            "held_locks": [                 // 持有锁列表（未阻塞其他事务）
                {"transaction_id", "thread_id", "table_name",
                 "index_name", "lock_mode", "lock_type",
                 "elapsed_seconds", "query"}
            ],
            "waiting_locks": [              // 等待锁列表（被阻塞）
                {"transaction_id", "thread_id", "table_name",
                 "lock_mode", "lock_type", "waiting_seconds",
                 "blocking_transaction_id", "blocking_thread_id", "query"}
            ],
            "blocking_trx_id": str,          // 首个阻塞源事务 ID
            "summary": str
        }
        失败: {"error": str, "detail": str}
    """
    # SAFETY: 只读操作，不修改数据库状态（PRD §8.1 Layer 1）
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
        await adapter.connect(config)

        lock_info = await adapter.get_lock_info()
        await adapter.disconnect()

        # 错误透传
        if "error" in lock_info:
            logger.error(
                "工具执行失败",
                tool="check_locks",
                connection_id=connection_id,
                error=lock_info.get("error"),
            )
            return lock_info

        waiting_locks = lock_info.get("waiting_locks", [])
        held_locks = lock_info.get("held_locks", [])
        total_waiting = lock_info.get("total_waiting", len(waiting_locks))
        total_held = lock_info.get("total_held", len(held_locks))
        summary = lock_info.get("summary", "")

        # 有锁等待时 status 至少为 warning
        if total_waiting > 0:
            # 取首个等待事务的阻塞事务 ID
            blocking_id: str = ""
            for lock in waiting_locks:
                bid = lock.get("blocking_transaction_id", "")
                if bid:
                    blocking_id = str(bid)
                    break

            lock_status = "warning" if total_waiting <= 3 else "error"
            logger.info(
                "工具执行成功",
                tool="check_locks",
                connection_id=connection_id,
                status=lock_status,
                total_waiting=total_waiting,
                total_held=total_held,
            )
            return {
                "status": lock_status,
                "total_held": total_held,
                "total_waiting": total_waiting,
                "held_locks": held_locks,
                "waiting_locks": waiting_locks,
                "blocking_trx_id": blocking_id,
                "summary": summary or f"等待事务: {total_waiting}（{lock_status}）",
            }

        logger.info("工具执行成功", tool="check_locks", connection_id=connection_id, status="pass")
        return {
            "status": "pass",
            "total_held": total_held,
            "total_waiting": 0,
            "held_locks": held_locks,
            "waiting_locks": [],
            "blocking_trx_id": "",
            "summary": summary or "无锁等待（pass）",
        }
    except Exception as exc:
        return _safe_tool_call("check_locks", exc)


# =============================================================================
# check_replication：检查主从复制状态
# =============================================================================


@tool
async def check_replication(
    connection_id: Annotated[str, InjectedToolArg],
    db_type: Annotated[str, InjectedToolArg],
    host: Annotated[str, InjectedToolArg],
    port: Annotated[int, InjectedToolArg],
    database: Annotated[str, InjectedToolArg],
    user: Annotated[str, InjectedToolArg],
    password: Annotated[str, InjectedToolArg],
    ssl_enabled: Annotated[bool, InjectedToolArg] = False,
    ssl_ca_cert: Annotated[str | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    """检查目标数据库的主从复制状态，包括复制延迟、IO 线程和 SQL 线程状态。
    适用于排查主从延迟、复制中断等场景。
    若数据库类型不支持复制或当前实例非从库，自动返回 status="skipped"。

    Returns:
        正常:      {"status": "pass",    "delay_seconds": int | null, "summary": str}  // 延迟<=10s
        延迟警告:  {"status": "warning", "delay_seconds": int,       "summary": str}  // 10-60s
        延迟严重:  {"status": "error",   "delay_seconds": int,       "summary": str}  // >60s
        不支持/非主库: {"status": "skipped", "delay_seconds": null,  "summary": str}
        失败: {"error": str, "detail": str}
    """
    # SAFETY: 只读操作，不修改数据库状态（PRD §8.1 Layer 1）
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
        await adapter.connect(config)

        # 检查适配器是否支持复制检测
        capabilities = adapter.get_capabilities()
        if not capabilities.supports_replication:
            await adapter.disconnect()
            logger.info(
                "工具执行成功",
                tool="check_replication",
                connection_id=connection_id,
                status="skipped",
            )
            return {
                "status": "skipped",
                "delay_seconds": None,
                "summary": "不支持复制检测（skipped）",
            }

        repl_status = await adapter.get_replication_status()
        await adapter.disconnect()

        # 若适配器返回 skipped，透传
        if repl_status.get("status") == "skipped":
            logger.info(
                "工具执行成功",
                tool="check_replication",
                connection_id=connection_id,
                status="skipped",
            )
            return {
                "status": "skipped",
                "delay_seconds": None,
                "summary": "复制状态：skipped（非主库实例）",
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

        logger.info(
            "工具执行成功",
            tool="check_replication",
            connection_id=connection_id,
            status=status,
            delay_seconds=delay_sec,
        )
        return {
            "status": status,
            "delay_seconds": delay_sec,
            "summary": (
                f"复制延迟: {delay_sec}s（{status}）"
                if delay_sec is not None
                else f"复制状态: {status}"
            ),
        }
    except Exception as exc:
        return _safe_tool_call("check_replication", exc)


# =============================================================================
# analyze_locks：查询指定事务的锁详情（锁模式、表名、等待链）
# =============================================================================


@tool
async def analyze_locks(
    connection_id: Annotated[str, InjectedToolArg],
    db_type: Annotated[str, InjectedToolArg],
    host: Annotated[str, InjectedToolArg],
    port: Annotated[int, InjectedToolArg],
    database: Annotated[str, InjectedToolArg],
    user: Annotated[str, InjectedToolArg],
    password: Annotated[str, InjectedToolArg],
    transaction_id: str | None = None,
    thread_id: str | None = None,
    user_role: Annotated[str, InjectedToolArg] = "readonly",
    ssl_enabled: Annotated[bool, InjectedToolArg] = False,
    ssl_ca_cert: Annotated[str | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    """查询指定事务或线程的详细锁信息，包括锁定的表名、锁模式（Record/Gap/Next-key）、
    锁类型（S/X）和等待链拓扑。

    Args:
        transaction_id: 要查询的事务 ID（可选，与 thread_id 二选一）。
        thread_id: 要查询的线程 ID（可选，与 transaction_id 二选一）。

    Returns:
        成功: {
            "transaction_id": str,
            "thread_id": str | None,
            "locked_objects": [{"table_name", "index_name", "lock_mode",
                                "lock_type", "record_key"}],
            "wait_chain": [{"transaction_id", "thread_id", "elapsed_seconds",
                            "query", "blocking_transaction_id"}],
            "root_blocker": {...} | None,
            "summary": str,
            "suggestion": str | None
        }
        失败: {"error": str, "detail": str}
    """
    # SAFETY: 只读操作，不修改数据库状态（PRD §8.1 Layer 1）
    if not transaction_id and not thread_id:
        return {"error": "参数错误", "detail": "请提供 transaction_id 或 thread_id"}

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

        # 按 db_type 路由，版本感知由 _analyze_mysql_locks 内部处理
        if db_type == "mysql":
            result = await _analyze_mysql_locks(adapter, transaction_id, thread_id)
        elif db_type == "postgresql":
            result = await _analyze_pg_locks(adapter, transaction_id, thread_id)
        else:
            result = {
                "transaction_id": transaction_id or "",
                "locked_objects": [],
                "wait_chain": [],
                "root_blocker": None,
                "summary": "当前数据库类型不支持细粒度锁分析。",
                "suggestion": (
                    "建议使用 check_locks 工具获取宏观锁等待信息，然后手动查询系统表查看详情。"
                ),
            }

        await adapter.disconnect()
        return result

    except Exception as exc:
        return _safe_tool_call("analyze_locks", exc)


async def _analyze_mysql_locks(
    adapter: Any,
    transaction_id: str | None,
    thread_id: str | None,
) -> dict[str, Any]:
    """分析 MySQL 锁详情，自动按版本选择查询方式。

    版本感知：
      - MySQL 8.0+: performance_schema.data_locks（含锁模式分类）
      - MySQL 5.7:  INFORMATION_SCHEMA.INNODB_LOCKS + INNODB_LOCK_WAITS（有限字段）

    SAFETY: MySQL 适配器的 execute() 将 params dict 转为
    list(params.values()) 列表后传给 cur.execute(sql, list)，
    因此 SQL 中必须使用 %s 位置占位符（而非 %(name)s 命名占位符）。
    dict 的 key 仅维持插入顺序，用于匹配 %s 的位置。
    """
    # 检测 MySQL 版本
    mysql_version = getattr(adapter, "_version_int", 0)
    is_mariadb = getattr(adapter, "is_mariadb", lambda: False)()

    if is_mariadb:
        return {
            "transaction_id": transaction_id or "",
            "locked_objects": [],
            "wait_chain": [],
            "root_blocker": None,
            "summary": "MariaDB 不支持细粒度锁查询",
            "suggestion": "建议使用 check_locks 获取宏观锁等待信息。",
        }

    if mysql_version >= 80000:
        return await _analyze_mysql_locks_v8(adapter, transaction_id, thread_id)
    else:
        return await _analyze_mysql_locks_v57(adapter, transaction_id, thread_id)


async def _analyze_mysql_locks_v8(
    adapter: Any,
    transaction_id: str | None,
    thread_id: str | None,
) -> dict[str, Any]:
    """MySQL 8.0+: 通过 performance_schema.data_locks 查询锁详情。"""
    where_clauses: list[str] = []
    params_dict: dict[str, Any] = {}
    if transaction_id:
        where_clauses.append("ENGINE_TRANSACTION_ID = %s")
        params_dict["trx_id"] = transaction_id
    if thread_id:
        where_clauses.append("pl.ID = %s")
        params_dict["tid"] = int(thread_id) if thread_id.isdigit() else thread_id

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    sql = f"""
        SELECT
            dl.ENGINE_TRANSACTION_ID, dl.OBJECT_SCHEMA, dl.OBJECT_NAME,
            dl.INDEX_NAME, dl.LOCK_TYPE, dl.LOCK_MODE,
            dl.LOCK_STATUS, dl.LOCK_DATA,
            dlw.BLOCKING_ENGINE_TRANSACTION_ID,
            pl.ID AS thread_id, pl.TIME AS elapsed_seconds,
            pl.INFO AS query_text
        FROM performance_schema.data_locks dl
        LEFT JOIN performance_schema.data_lock_waits dlw
            ON dl.ENGINE_TRANSACTION_ID = dlw.REQUESTING_ENGINE_TRANSACTION_ID
        LEFT JOIN information_schema.PROCESSLIST pl
            ON dl.THREAD_ID = pl.ID
        WHERE {where_sql}
        ORDER BY dl.ENGINE_TRANSACTION_ID
    """
    try:
        result = await adapter.execute(sql, params_dict if params_dict else {})
    except Exception:
        return {
            "transaction_id": transaction_id or "",
            "locked_objects": [],
            "wait_chain": [],
            "root_blocker": None,
            "summary": "performance_schema.data_locks 不可访问",
            "suggestion": None,
        }

    locked_objects: list[dict] = []
    wait_chain: list[dict] = []
    seen_trx: set = set()

    for row in result["rows"]:
        trx_id = str(row[0]) if row[0] else ""
        schema = str(row[1]) if row[1] else ""
        table = str(row[2]) if row[2] else ""
        index_name = str(row[3]) if row[3] else ""
        lock_type = str(row[4]) if row[4] else ""
        lock_mode = str(row[5]) if row[5] else ""
        lock_data = str(row[7]) if row[7] else ""
        blocking_trx = str(row[8]) if len(row) > 8 and row[8] else ""
        tid = str(row[9]) if len(row) > 9 and row[9] else ""
        elapsed = int(row[10]) if len(row) > 10 and row[10] else 0
        query = str(row[11]) if len(row) > 11 and row[11] else ""

        table_name = f"{schema}.{table}" if schema and table else (table or schema)

        # 标准化锁模式
        if "GAP" in lock_mode and "REC_NOT_GAP" not in lock_mode:
            mode_desc = "Gap Lock"
        elif "REC_NOT_GAP" in lock_mode:
            mode_desc = "Record Lock"
        elif "INSERT_INTENTION" in lock_mode:
            mode_desc = "Insert Intention"
        else:
            mode_desc = lock_mode

        locked_objects.append(
            {
                "table_name": table_name,
                "index_name": index_name,
                "lock_mode": mode_desc,
                "lock_type": lock_type,
                "record_key": lock_data,
            }
        )

        if trx_id and trx_id not in seen_trx:
            seen_trx.add(trx_id)
            wait_chain.append(
                {
                    "transaction_id": trx_id,
                    "thread_id": tid,
                    "elapsed_seconds": elapsed,
                    "query": query,
                    "blocking_transaction_id": blocking_trx if blocking_trx else None,
                }
            )

    return _build_analyze_result(transaction_id, thread_id, locked_objects, wait_chain)


async def _analyze_mysql_locks_v57(
    adapter: Any,
    transaction_id: str | None,
    thread_id: str | None,
) -> dict[str, Any]:
    """MySQL 5.7: 通过 INFORMATION_SCHEMA.INNODB_LOCKS + INNODB_LOCK_WAITS 查询锁详情。

    注意：INNODB_LOCKS 没有 THREAD_ID，无法直接关联 PROCESSLIST。
    因此返回结果中 thread_id 为空字符串。
    """
    try:
        result = await adapter.execute("""
            SELECT
                il.LOCK_TRX_ID,
                il.LOCK_TABLE,
                il.LOCK_INDEX,
                il.LOCK_TYPE,
                il.LOCK_MODE,
                il.LOCK_DATA,
                iw.BLOCKING_TRX_ID,
                '' AS thread_id,
                0 AS elapsed_seconds,
                '' AS query_text
            FROM INFORMATION_SCHEMA.INNODB_LOCKS il
            LEFT JOIN INFORMATION_SCHEMA.INNODB_LOCK_WAITS iw
                ON il.LOCK_ID = iw.REQUESTED_LOCK_ID
            ORDER BY il.LOCK_TRX_ID
        """)
    except Exception:
        return {
            "transaction_id": transaction_id or "",
            "locked_objects": [],
            "wait_chain": [],
            "root_blocker": None,
            "summary": "INFORMATION_SCHEMA.INNODB_LOCKS 不可访问（可能是权限不足）",
            "suggestion": None,
        }

    locked_objects: list[dict] = []
    wait_chain: list[dict] = []
    seen_trx: set = set()

    for row in result["rows"]:
        trx_id = str(row[0]) if row[0] else ""
        lock_table = str(row[1]) if row[1] else ""
        lock_index = str(row[2]) if row[2] else ""
        lock_type = str(row[3]) if row[3] else ""
        lock_mode = str(row[4]) if row[4] else ""
        lock_data = str(row[5]) if row[5] else ""
        blocking_trx = str(row[6]) if len(row) > 6 and row[6] else ""

        # 过滤：如果指定了 transaction_id，只返回匹配的
        if transaction_id and trx_id != transaction_id:
            continue

        # 标准化锁模式（5.7 的锁模式字段格式：X、S、X,GAP、S,GAP 等）
        mode_desc = _normalize_lock_mode_v57(lock_mode, lock_type)

        locked_objects.append(
            {
                "table_name": lock_table.replace("`", ""),
                "index_name": lock_index or "",
                "lock_mode": mode_desc,
                "lock_type": lock_type,
                "record_key": lock_data or "",
            }
        )

        if trx_id and trx_id not in seen_trx:
            seen_trx.add(trx_id)
            wait_chain.append(
                {
                    "transaction_id": trx_id,
                    "thread_id": "",
                    "elapsed_seconds": 0,
                    "query": "",
                    "blocking_transaction_id": blocking_trx if blocking_trx else None,
                }
            )

    return _build_analyze_result(transaction_id, thread_id, locked_objects, wait_chain)


def _normalize_lock_mode_v57(mode_raw: str, lock_type: str) -> str:
    """标准化 MySQL 5.7 的锁模式描述。"""
    if lock_type == "RECORD":
        if "GAP" in mode_raw:
            return "Gap Lock"
        return "Record Lock"
    if lock_type == "TABLE":
        if "IX" in mode_raw:
            return "意向排他锁(IX)"
        if "IS" in mode_raw:
            return "意向共享锁(IS)"
        if "S" in mode_raw:
            return "共享锁(S)"
        if "X" in mode_raw:
            return "排他锁(X)"
    return mode_raw


def _build_analyze_result(
    transaction_id: str | None,
    thread_id: str | None,
    locked_objects: list[dict],
    wait_chain: list[dict],
) -> dict[str, Any]:
    """从 lock_objects 和 wait_chain 构建统一的 analyze_locks 返回结果。"""
    # 找到根阻塞者
    root_blocker = None
    if wait_chain:
        all_trx = {w["transaction_id"] for w in wait_chain}
        blocked_trx = {
            w["blocking_transaction_id"] for w in wait_chain if w["blocking_transaction_id"]
        }
        root_trx = all_trx - blocked_trx
        if root_trx:
            root_id = list(root_trx)[0]
            for w in wait_chain:
                if w["transaction_id"] == root_id:
                    root_blocker = w
                    break

    # 生成建议
    suggestion = None
    if wait_chain:
        has_gap = any(o["lock_mode"] == "Gap Lock" for o in locked_objects)
        if has_gap:
            suggestion = "存在 Gap Lock，可能是索引设计不当或范围查询导致锁冲突。"
        else:
            suggestion = "建议检查长时间运行的事务，优化 SQL 或增加索引以减少锁冲突。"

    return {
        "transaction_id": transaction_id or "",
        "thread_id": thread_id or "",
        "locked_objects": locked_objects,
        "wait_chain": wait_chain,
        "root_blocker": root_blocker,
        "summary": f"锁定 {len(locked_objects)} 个对象，等待链长度 {len(wait_chain)}",
        "suggestion": suggestion,
    }


async def _analyze_pg_locks(
    adapter: Any,
    transaction_id: str | None,
    thread_id: str | None,
) -> dict[str, Any]:
    """分析 PostgreSQL 的 pg_locks 锁详情。

    SAFETY: PostgreSQL 适配器的 execute() 将 params dict 转为
    list(params.values()) 列表后传给 conn.fetch(sql, *param_values)，
    因此 SQL 中必须使用 $1, $2 位置占位符（而非 %(name)s 命名占位符）。
    """
    pid_filter = ""
    params: dict[str, Any] = {}
    if thread_id:
        pid_filter = " AND a.pid = $1"
        params["pid"] = int(thread_id)

    sql = f"""
        SELECT
            a.pid,
            a.query,
            a.datname,
            l.locktype,
            l.mode,
            l.granted,
            l.relation::regclass::text AS relation_name,
            EXTRACT(EPOCH FROM (NOW() - a.query_start))::int AS elapsed_seconds
        FROM pg_locks l
        JOIN pg_stat_activity a ON a.pid = l.pid
        WHERE a.backend_type = 'client backend'
          AND l.relation IS NOT NULL
          {pid_filter}
        ORDER BY l.granted DESC, elapsed_seconds DESC
    """
    try:
        result = await adapter.execute(sql, params)
    except Exception:
        return {
            "transaction_id": transaction_id or "",
            "locked_objects": [],
            "wait_chain": [],
            "root_blocker": None,
            "summary": "pg_locks 不可访问（可能是权限不足）",
            "suggestion": None,
        }

    locked_objects: list[dict] = []
    for row in result["rows"]:
        locked_objects.append(
            {
                "table_name": str(row[6]) if len(row) > 6 and row[6] else "",
                "index_name": str(row[3]) if len(row) > 3 and row[3] else "",
                "lock_mode": str(row[4]) if len(row) > 4 and row[4] else "",
                "lock_type": "granted" if len(row) > 5 and row[5] else "waiting",
                "record_key": "",
            }
        )

    return {
        "transaction_id": transaction_id or "",
        "thread_id": thread_id or "",
        "locked_objects": locked_objects,
        "wait_chain": [],
        "root_blocker": None,
        "summary": f"锁定 {len(locked_objects)} 个对象",
        "suggestion": None,
    }


# =============================================================================
# kill_transaction：终止指定线程的事务（含确认机制）
# =============================================================================


@tool(extras={"needs_write_confirmation": True, "confirm_category": "connection_kill"})
async def kill_transaction(
    connection_id: Annotated[str, InjectedToolArg],
    db_type: Annotated[str, InjectedToolArg],
    host: Annotated[str, InjectedToolArg],
    port: Annotated[int, InjectedToolArg],
    database: Annotated[str, InjectedToolArg],
    user: Annotated[str, InjectedToolArg],
    password: Annotated[str, InjectedToolArg],
    thread_id: str,
    user_role: Annotated[str, InjectedToolArg] = "readonly",
    ssl_enabled: Annotated[bool, InjectedToolArg] = False,
    ssl_ca_cert: Annotated[str | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    """终止指定线程 ID 的数据库连接（KILL CONNECTION），
    用于紧急处理锁阻塞或长时间运行的事务。
    执行前会查询事务详情并附带风险提示。
    注意：此工具需要用户确认后才能执行。

    Args:
        thread_id: 要终止的线程/连接 ID（必填）。

    Returns:
        执行成功: {
            "success": true,
            "killed_thread_id": str,
            "message": str,
            "verified": bool
        }
        失败: {"error": str, "detail": str}
    """
    # SAFETY: 写操作由 needs_write_confirmation 触发的 confirm_node 管控
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

        # 第一步：查询事务详情（供 LLM 和用户判断）
        detail = {}
        try:
            if db_type == "mysql":
                plist = await adapter.execute(
                    "SELECT ID, USER, HOST, DB, COMMAND, TIME, STATE, INFO "
                    "FROM information_schema.PROCESSLIST WHERE ID = %s",
                    {"tid": thread_id},
                )
                if plist["rows"]:
                    row = plist["rows"][0]
                    detail = {
                        "thread_id": str(row[0]) if len(row) > 0 else "",
                        "user": str(row[1]) if len(row) > 1 else "",
                        "host": str(row[2]) if len(row) > 2 else "",
                        "database": str(row[3]) if len(row) > 3 else "",
                        "command": str(row[4]) if len(row) > 4 else "",
                        "elapsed_seconds": int(row[5]) if len(row) > 5 and row[5] else 0,
                        "state": str(row[6]) if len(row) > 6 else "",
                        "current_sql": str(row[7]) if len(row) > 7 else "",
                    }
            elif db_type == "postgresql":
                pg_result = await adapter.execute(
                    "SELECT pid, usename, application_name, state, "
                    "EXTRACT(EPOCH FROM (NOW() - query_start))::int AS elapsed, "
                    "query FROM pg_stat_activity WHERE pid = $1",
                    {"pid": int(thread_id)},
                )
                if pg_result["rows"]:
                    row = pg_result["rows"][0]
                    detail = {
                        "thread_id": str(row[0]) if len(row) > 0 else "",
                        "user": str(row[1]) if len(row) > 1 else "",
                        "database": str(row[2]) if len(row) > 2 else "",
                        "command": str(row[3]) if len(row) > 3 else "",
                        "elapsed_seconds": int(row[4]) if len(row) > 4 and row[4] else 0,
                        "state": row[3] if len(row) > 3 else "",
                        "current_sql": str(row[5]) if len(row) > 5 else "",
                    }
        except Exception:
            detail = {"thread_id": thread_id, "note": "无法获取事务详情（权限不足）"}

        await adapter.disconnect()

        if not detail:
            return {"error": "未找到该线程", "detail": f"thread_id={thread_id} 不存在"}

        # 第二步：执行 KILL
        kill_adapter = AdapterFactory.create(db_type, config)
        await kill_adapter.connect(config, user_role="admin")

        killed = False
        try:
            if db_type == "mysql":
                await kill_adapter.execute(f"KILL CONNECTION {thread_id}")
                killed = True
            elif db_type == "postgresql":
                await kill_adapter.execute(
                    "SELECT pg_terminate_backend($1)",
                    {"pid": int(thread_id)},
                )
                killed = True
            else:
                return {
                    "error": "不支持的数据库类型",
                    "detail": f"{db_type} 不支持 kill_transaction",
                }
        except Exception as kill_exc:
            return _safe_tool_call("kill_transaction", kill_exc)
        finally:
            await kill_adapter.disconnect()

        # 第三步：验证是否成功
        verified = False
        try:
            verify_adapter = AdapterFactory.create(db_type, config)
            await verify_adapter.connect(config)
            if db_type == "mysql":
                v_result = await verify_adapter.execute(
                    "SELECT ID FROM information_schema.PROCESSLIST WHERE ID = %s",
                    {"tid": thread_id},
                )
                verified = len(v_result["rows"]) == 0
            elif db_type == "postgresql":
                v_result = await verify_adapter.execute(
                    "SELECT pid FROM pg_stat_activity WHERE pid = $1",
                    {"pid": int(thread_id)},
                )
                verified = len(v_result["rows"]) == 0
            await verify_adapter.disconnect()
        except Exception:
            verified = killed  # 无法验证时信任执行结果

        elapsed_str = (
            f"{detail.get('elapsed_seconds', '?')}s" if detail.get("elapsed_seconds") else "?"
        )
        return {
            "success": True,
            "killed_thread_id": thread_id,
            "message": f"线程 {thread_id} 已{'成功' if verified else '尝试'}终止"
            f"（运行时长: {elapsed_str}）",
            "verified": verified,
        }

    except Exception as exc:
        return _safe_tool_call("kill_transaction", exc)


# =============================================================================
# TroubleshootWorkflow：故障排查工作流编排器（B-23）
# =============================================================================


def _generate_diagnosis(results: list[dict[str, Any]]) -> dict[str, Any]:
    """根据工具执行结果生成诊断结论（基于规则，无 LLM 依赖）。

    检查结果列表中的每项，按优先级输出最严重的诊断。
    若全部 pass 则输出健康结论。

    Args:
        results: 工具调用结果列表，每项含 {tool, status, ...}。

    Returns:
        {conclusion, severity, suggestion, suggestion_is_destructive}。
    """
    has_connections_error = False
    has_locks_warning = False
    has_locks_error = False
    has_replication_warning = False
    has_replication_error = False
    lock_waiting_count = 0
    replication_delay = 0

    for r in results:
        tool_name = r.get("tool", "")
        status = r.get("status", "")

        if tool_name == "check_connections" and status == "error":
            has_connections_error = True
        elif tool_name == "check_locks":
            lock_waiting_count = r.get("total_waiting", r.get("waiting_transactions", 0))
            if status == "error":
                has_locks_error = True
            elif status == "warning":
                has_locks_warning = True
        elif tool_name == "check_replication":
            replication_delay = r.get("delay_seconds", 0) or 0
            if status == "error":
                has_replication_error = True
            elif status == "warning":
                has_replication_warning = True

    # 按严重程度降序排列诊断
    if has_connections_error:
        return {
            "conclusion": "数据库连接池即将耗尽，存在大量连接等待",
            "severity": "error",
            "suggestion": "检查是否有连接泄漏，增加 max_connections 或优化应用连接池配置",
            "suggestion_is_destructive": True,
        }
    if has_locks_error:
        return {
            "conclusion": f"检测到严重锁等待（{lock_waiting_count} 个事务在等待）",
            "severity": "error",
            "suggestion": "检查阻塞事务详情，必要时执行 KILL 阻塞会话",
            "suggestion_is_destructive": True,
        }
    if has_replication_error:
        return {
            "conclusion": f"主从复制延迟严重（{replication_delay}s），超过安全阈值",
            "severity": "error",
            "suggestion": "检查从库 IO/SQL 线程状态及网络带宽，评估是否需要扩容从库",
            "suggestion_is_destructive": False,
        }
    if has_locks_warning:
        return {
            "conclusion": f"存在锁等待（{lock_waiting_count} 个事务），性能受到影响",
            "severity": "warning",
            "suggestion": "检查长时间未提交的事务，优化 SQL 执行顺序减少锁冲突",
            "suggestion_is_destructive": False,
        }
    if has_replication_warning:
        return {
            "conclusion": f"主从复制延迟偏高（{replication_delay}s），建议关注",
            "severity": "warning",
            "suggestion": "排查从库负载，检查是否有大事务或慢查询影响复制进度",
            "suggestion_is_destructive": False,
        }

    return {
        "conclusion": "未发现明显异常，数据库运行状态正常",
        "severity": "info",
        "suggestion": None,
        "suggestion_is_destructive": False,
    }


class TroubleshootWorkflow:
    """故障排查工作流编排器（B-23）。

    按照 check_connections → check_locks → check_replication 顺序执行工具，
    处理超时（>10s 跳过）和异常，生成诊断结论。
    依据 PRD §5.3 故障类型覆盖、api-contract §1.5 SSE 事件契约。
    """

    def __init__(self, conn_config: dict[str, Any]) -> None:
        """初始化工作流。

        Args:
            conn_config: 连接配置字典，需包含 db_type, host, port, database,
                        user, password, ssl_enabled, ssl_ca_cert 等键。
        """
        self._conn_config = dict(conn_config)
        self._results: list[dict[str, Any]] = []

    async def run(
        self,
        issue_type: str = "auto",
    ) -> AsyncGenerator[dict[str, Any], None]:
        """执行故障排查工作流，逐条 yield SSE 事件数据。

        支持 "auto" 自动检测（全部检查）。
        每个工具调用用 10s 超时保护（AC-4）。

        Args:
            issue_type: 排查类型（"auto" 自动检测全部项）。

        Yields:
            {"type": "thinking"|"tool_call"|"tool_result"|"skip"|"diagnosis", ...}。
        """
        from app.agent.tools.troubleshoot import (
            check_connections,
            check_locks,
            check_replication,
        )

        # AC-1：自动检测模式下执行全部三项检查
        check_steps = [
            (
                check_connections,
                "check_connections",
                "检查连接池状态...",
                {"connection_id": self._conn_config.get("connection_id", "")},
            ),
            (
                check_locks,
                "check_locks",
                "检查锁等待...",
                {"connection_id": self._conn_config.get("connection_id", "")},
            ),
            (
                check_replication,
                "check_replication",
                "检查主从复制状态...",
                {"connection_id": self._conn_config.get("connection_id", "")},
            ),
        ]

        yield {
            "type": "thinking",
            "content": (f"开始故障排查（{issue_type}）：依次检查连接数、锁等待和复制状态"),
        }

        for tool_fn, tool_name, display, extra_args in check_steps:
            # AC-2：tool_call 事件
            yield {
                "type": "tool_call",
                "tool": tool_name,
                "args": {},
                "display": display,
            }

            # AC-4：构建工具参数字典
            tool_args = {**self._conn_config, **extra_args}

            try:
                # AC-4：10s 超时保护
                start = asyncio.get_event_loop().time()
                tr = await asyncio.wait_for(
                    tool_fn.ainvoke(tool_args),
                    timeout=10,
                )
                elapsed_ms = int(
                    (asyncio.get_event_loop().time() - start) * 1000,
                )

                # 检查工具返回中是否有 error
                if "error" in tr:
                    yield {
                        "type": "skip",
                        "tool": tool_name,
                        "reason": tr.get("error", "工具执行异常"),
                    }
                    continue

                tr["tool"] = tool_name
                tr["duration_ms"] = elapsed_ms
                self._results.append(tr)

                status = tr.get("status", "error")
                summary = f"状态：{status}"

                # AC-2：tool_result 事件
                yield {
                    "type": "tool_result",
                    "tool": tool_name,
                    "summary": summary,
                    "status": status,
                    "duration_ms": elapsed_ms,
                }

                # AC-7：每步记录日志
                logger.info(
                    "故障排查步骤完成", tool=tool_name, status=status, duration_ms=elapsed_ms
                )

            except TimeoutError:
                # AC-4：超时 → skip 事件，继续下一步
                logger.warning("故障排查步骤超时", tool=tool_name, timeout=10)
                yield {
                    "type": "skip",
                    "tool": tool_name,
                    "reason": f"{tool_name} 执行超时（>10s）",
                }
            except Exception as exc:
                # AC-4：异常 → skip 事件
                logger.error("故障排查步骤异常", tool=tool_name, error=str(exc)[:200])
                yield {
                    "type": "skip",
                    "tool": tool_name,
                    "reason": f"{tool_name} 执行异常：{exc}",
                }

        # 生成诊断结论
        diagnosis = _generate_diagnosis(self._results)

        # AC-3：diagnosis 事件
        yield {
            "type": "diagnosis",
            "conclusion": diagnosis["conclusion"],
            "severity": diagnosis["severity"],
            "suggestion": diagnosis["suggestion"],
            "suggestion_is_destructive": diagnosis["suggestion_is_destructive"],
        }

        # AC-7：结论记录日志
        logger.info(
            "故障排查结论", severity=diagnosis["severity"], conclusion=diagnosis["conclusion"][:100]
        )

    def get_results(self) -> list[dict[str, Any]]:
        """获取所有步骤的执行结果列表。"""
        return list(self._results)
