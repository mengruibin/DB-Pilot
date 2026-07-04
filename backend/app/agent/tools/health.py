"""
健康巡检 Agent 工具。

包含 run_health_check 工具，调用 HealthCheckEngine 执行 20 项检查。
每个工具用 @tool 装饰，返回 Python dict。
依据 PRD §5.4 健康巡检、AGENTS.md §工具函数返回契约。

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
from app.engine.health_check import HealthCheckEngine
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
# run_health_check：执行健康巡检
# =============================================================================


@tool
async def run_health_check(
    connection_id: Annotated[str, InjectedToolArg],
    db_type: Annotated[str, InjectedToolArg],
    host: Annotated[str, InjectedToolArg],
    port: Annotated[int, InjectedToolArg],
    database: Annotated[str, InjectedToolArg],
    user: Annotated[str, InjectedToolArg],
    password: Annotated[str, InjectedToolArg],
    check_items: list[str] | None = None,
    ssl_enabled: Annotated[bool, InjectedToolArg] = False,
    ssl_ca_cert: Annotated[str | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    """对目标数据库执行健康巡检（20 项检查）。

    调用 HealthCheckEngine 逐项执行检查，按类别汇总评分。
    适用于数据库日常巡检和性能评估场景（PRD §5.4）。

    支持按检查项名称过滤：传 ["连接数使用率", "主从延迟"] 仅执行指定项。
    传 None 或 ["all"] 执行全部 20 项检查。

    Args:
        connection_id: 连接标识符。
        db_type: 数据库类型（mysql / postgresql / oracle）。
        host: 数据库主机地址。
        port: 数据库端口号。
        database: 目标数据库名。
        user: 连接用户名。
        password: 连接密码。
        check_items: 要执行的检查项名称列表；None 或 ["all"] 表示全部。
        ssl_enabled: 是否启用 SSL。
        ssl_ca_cert: SSL CA 证书（可选）。

    Returns:
        成功：{"score": int, "severity_counts": {...}, "categories": [...]}
        失败：{"error": "run_health_check 执行失败", "detail": "..."}
    """
    # SAFETY: 所有检查为只读操作（PRD §8.1 Layer 1）
    try:
        config = _build_config(
            connection_id, db_type, host, port, database, user,
            password, ssl_enabled, ssl_ca_cert,
        )
        adapter = AdapterFactory.create(db_type, config)
        await adapter.connect(config)

        engine = HealthCheckEngine(adapter)

        # 收集所有检查结果
        categories: dict[str, list[dict[str, Any]]] = {}
        final_report: dict[str, Any] = {}

        async for result in engine.run_checks(check_items):
            if "score" in result:
                # 汇总报告
                final_report = result
            else:
                # 单项检查结果 → 按类别分组
                cat = result.get("category", "其他")
                if cat not in categories:
                    categories[cat] = []
                categories[cat].append(result)

        await adapter.disconnect()

        # 组装返回结构
        report = {
            "score": final_report.get("score", 100),
            "severity_counts": final_report.get(
                "severity_counts",
                {"error": 0, "warning": 0, "pass": 0, "skipped": 0},
            ),
            "categories": [
                {
                    "name": cat_name,
                    "items": items,
                }
                for cat_name, items in categories.items()
            ],
        }
        score = report["score"]
        severity = report.get("severity_counts", {})
        error_count = severity.get("error", 0)
        warning_count = severity.get("warning", 0)
        pass_count = severity.get("pass", 0)
        report["summary"] = (
            f"健康评分: {score}/100"
            f"（{error_count} 项异常, {warning_count} 项警告, {pass_count} 项通过）"
        )
        logger.info("工具执行成功", tool="run_health_check",
                     connection_id=connection_id, score=score)
        return report
    except Exception as exc:
        return _safe_tool_call("run_health_check", exc)
