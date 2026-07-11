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

    Returns:
        无锁等待: {
            "status": "pass",
            "waiting_transactions": 0,
            "blocking_trx_id": "",
            "locks": [],
            "summary": str
        }
        有锁等待: {
            "status": "warning" | "error",   // warning(<=3个), error(>3个)
            "waiting_transactions": int,
            "blocking_trx_id": str,           // 首个阻塞事务的 ID
            "locks": [
                {
                    "transaction_id": str,         // 事务 ID
                    "elapsed_seconds": int,        // 已等待时长（秒）
                    "state": str,                  // 状态（如 "LOCK WAIT"）
                    "query": str,                  // 当前执行的查询
                    "blocking_transaction_id": str,// 阻塞该事务的事务 ID
                    "thread_id": str               // MySQL 线程 ID
                }
            ],
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

            lock_status = "warning" if waiting_count <= 3 else "error"
            logger.info(
                "工具执行成功",
                tool="check_locks",
                connection_id=connection_id,
                status=lock_status,
                waiting_transactions=waiting_count,
            )
            return {
                "status": lock_status,
                "waiting_transactions": waiting_count,
                "blocking_trx_id": blocking_id,
                "locks": locks,
                "summary": f"等待事务: {waiting_count}（{lock_status}）",
            }

        logger.info("工具执行成功", tool="check_locks", connection_id=connection_id, status="pass")
        return {
            "status": "pass",
            "waiting_transactions": 0,
            "blocking_trx_id": "",
            "summary": "无锁等待（pass）",
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
            lock_waiting_count = r.get("waiting_transactions", 0)
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
