"""
健康巡检 SSE 流与报告管理 API 路由。

端点（api-contract §1.4）：
  - POST /api/connections/{id}/health-check  SSE 流式巡检
  - GET  /api/reports                        历史报告列表（分页）
  - GET  /api/reports/{id}                   单份报告详情

依据 api-contract §1.4（health-check SSE + reports CRUD）、§2.5 HealthReport。
"""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status  # noqa: B008
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.sse_utils import format_sse

# 复用 B-20 的 SSE 取消机制（report_id → asyncio.Event）
from app.api.chat import _active_streams, _running_tasks  # type: ignore[attr-defined]  # noqa: F811
from app.database import async_session_factory, get_session
from app.db.factory import AdapterFactory
from app.engine.health_check import HealthCheckEngine
from app.models.connection import ConnectionConfigModel
from app.models.report import ReportModel
from app.models.schemas import (
    ConnectionCreateRequest,
    HealthReportListResponse,
    HealthReportResponse,
)

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["Health Check"])


# SSE 格式化函数统一使用 app.agent.sse_utils.format_sse


# =============================================================================
# 辅助：从 ORM 加载连接配置并创建适配器
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
        session: 内部数据库会话。

    Returns:
        (adapter, config) 元组。

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
        db_type=db_conn.db_type, # type: ignore[arg-type] — Pydantic validator handles str→DBType
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
# SSE 流引擎：健康巡检
# =============================================================================


async def _health_stream(
    connection_id: str,
    check_items: list[str] | None,
    timeout_sec: int,
    password: str | None,
) -> AsyncGenerator[str, None]:
    """健康巡检 SSE 流主引擎——异步生成器，逐条 yield SSE 格式化字符串。

    执行流程：
      1. 加载连接配置 + 创建适配器
      2. HealthCheckEngine 逐项检查
      3. 按结果映射 SSE 事件类型（check_progress/warning/error）
      4. 取消/完成后发送 health_result

    Args:
        connection_id: 目标数据库连接 ID。
        check_items: 检查项列表（["all"] 表示全部）。
        timeout_sec: 巡检总超时（秒）。
        password: 连接密码。

    Yields:
        SSE 格式化的事件字符串。
    """
    report_id = f"rpt_{uuid.uuid4().hex[:8]}"
    cancel_event = asyncio.Event()

    # AC-7：注册取消标记，复用 B-20 取消机制
    _active_streams[report_id] = cancel_event
    current_task = asyncio.current_task()
    if current_task is not None:
        _running_tasks[report_id] = current_task

    db_session: AsyncSession | None = None
    adapter: Any = None

    try:
        # AC-8：入口日志
        logger.info("健康巡检 SSE 开始", connection_id=connection_id,
                     report_id=report_id, check_items=check_items)

        # Step 1: 加载连接配置 + 创建适配器
        async with async_session_factory() as db_session:
            adapter, config = await _load_and_create_adapter(
                connection_id, password, db_session,
            )
            await adapter.connect(config)

            # Step 2: 创建健康巡检引擎
            engine = HealthCheckEngine(adapter)
            start_ts = time.monotonic()

            # 收集所有检查结果，用于构建报告
            all_results: list[dict[str, Any]] = []

            # Step 3: 逐项检查
            async for result in engine.run_checks(check_items):
                # AC-7：每次 yield 后检查取消信号
                if cancel_event.is_set():
                    elapsed = time.monotonic() - start_ts
                    logger.info("健康巡检被取消", connection_id=connection_id,
                                 report_id=report_id, completed=result.get("current", 0))
                    partial_counts = _count_status(all_results)
                    partial_score = _calc_score(partial_counts)

                    yield format_sse({
                        "type": "health_result",
                        "report_id": report_id,
                        "score": partial_score,
                        "summary": (
                            f"巡检中断于第 {result.get('current', 0)}/"
                            f"{result.get('total', 0)} 项"
                        ),
                        "severity_counts": partial_counts,
                    })

                    # 持久化部分报告
                    await _persist_report(
                        db_session, report_id, connection_id,
                        status="cancelled", score=partial_score,
                        duration_sec=elapsed,
                        severity_counts=partial_counts,
                        categories=_build_categories(all_results),
                    )
                    return

                # 收集结果用于报告构建
                all_results.append(dict(result))

                # AC-6：映射 SSE 事件类型
                status_val = result.get("status", "pass")
                current = result.get("current", 1)
                total = result.get("total", 1)

                if status_val == "error":
                    yield format_sse({
                        "type": "check_error",
                        "current": current,
                        "total": total,
                        "item": result.get("item", ""),
                        "status": "error",
                        "value": result.get("value", ""),
                        "threshold": result.get("threshold", ""),
                        "suggestion": result.get("suggestion", ""),
                    })
                elif status_val == "warning":
                    yield format_sse({
                        "type": "check_warning",
                        "current": current,
                        "total": total,
                        "item": result.get("item", ""),
                        "status": "warning",
                        "value": result.get("value", ""),
                        "threshold": result.get("threshold", ""),
                        "suggestion": result.get("suggestion", ""),
                    })
                else:
                    # pass / skipped → check_progress
                    yield format_sse({
                        "type": "check_progress",
                        "current": current,
                        "total": total,
                        "item": result.get("item", ""),
                        "status": status_val,
                    })

                # AC-8：每项检查记录日志
                logger.info("健康检查项完成", endpoint="health_check",
                            item=result.get("item", ""), status=status_val,
                            current=current, total=total)

            # Step 4: 全部完成，构建报告
            elapsed = time.monotonic() - start_ts
            severity = _count_status(all_results)
            score = _calc_score(severity)
            categories = _build_categories(all_results)

            # AC-3：持久化报告
            await _persist_report(
                db_session, report_id, connection_id,
                status="completed", score=score,
                duration_sec=elapsed,
                severity_counts=severity,
                categories=categories,
            )

            # AC-2：发送 health_result 事件
            yield format_sse({
                "type": "health_result",
                "report_id": report_id,
                "score": score,
                "summary": (
                    f"健康巡检完成，评分 {score}/100 — "
                    f"{severity.get('error', 0)} 项异常"
                ),
                "severity_counts": severity,
            })

            logger.info("健康巡检 SSE 完成", connection_id=connection_id,
                        report_id=report_id, score=score,
                        duration_sec=round(elapsed, 2))

    except Exception as exc:
        logger.error("健康巡检 SSE 异常", connection_id=connection_id,
                      report_id=report_id, error=str(exc)[:200])
        yield format_sse({
            "type": "health_result",
            "report_id": report_id,
            "score": 0,
            "summary": f"巡检异常中断：{exc}",
            "severity_counts": {"error": 0, "warning": 0, "pass": 0, "skipped": 0},
        })
    finally:
        # 清理追踪标记
        _active_streams.pop(report_id, None)
        _running_tasks.pop(report_id, None)
        if adapter is not None:
            with suppress(Exception):
                await adapter.disconnect()
        if db_session is not None:
            await db_session.close()


# =============================================================================
# 报告构建辅助
# =============================================================================


def _count_status(results: list[dict[str, Any]]) -> dict[str, int]:
    """统计各状态的数量。"""
    counts: dict[str, int] = {"error": 0, "warning": 0, "pass": 0, "skipped": 0}
    for r in results:
        s = r.get("status", "pass")
        if s in counts:
            counts[s] += 1
    return counts


def _calc_score(severity: dict[str, int]) -> int:
    """计算健康评分（PRD §5.4：skipped 不计入分母）。"""
    total_checked = sum(v for k, v in severity.items() if k != "skipped")
    denominator = total_checked
    score = math.floor((severity.get("pass", 0) / max(denominator, 1)) * 100)
    return score


def _build_categories(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """将平铺的检查结果按 category 分组为嵌套结构。

    依据 api-contract §2.5 HealthReport.categories 格式：
    [{name: "连接", items: [{name, status, value, threshold, suggestion, is_destructive}]}]
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        cat = r.get("category", "其他")
        if cat not in grouped:
            grouped[cat] = []
        grouped[cat].append({
            "name": r.get("item", ""),
            "status": r.get("status", "pass"),
            "value": r.get("value"),
            "threshold": r.get("threshold"),
            "suggestion": r.get("suggestion"),
            "is_destructive": False,
        })

    return [
        {"name": cat, "items": items}
        for cat, items in grouped.items()
    ]


async def _persist_report(
    db_session: AsyncSession,
    report_id: str,
    connection_id: str,
    status: str,
    score: int,
    duration_sec: float,
    severity_counts: dict[str, int],
    categories: list[dict[str, Any]],
) -> None:
    """持久化健康巡检报告到 reports 表。"""
    report = ReportModel(
        id=report_id,
        connection_id=connection_id,
        status=status,
        score=score,
        generated_at=datetime.now(UTC),
        duration_sec=round(duration_sec, 2),
        severity_counts=severity_counts,
        categories=categories,
    )
    db_session.add(report)
    await db_session.commit()
    logger.info("健康巡检报告已持久化", report_id=report_id, status=status, score=score)


# =============================================================================
# REST 端点
# =============================================================================


@router.post("/api/connections/{connection_id}/health-check")
async def health_check_stream(
    connection_id: str,
    check_items: list[str] | None = Body(  # noqa: B008
        default=["all"], embed=True,
        description='检查项列表（["all"] 表示全部 20 项）',
    ),
    timeout_sec: int = Body(  # noqa: B008
        default=30, ge=10, le=120, embed=True,
        description="巡检总超时（秒），默认 30s",
    ),
    password: str | None = Body(  # noqa: B008
        default=None, embed=True,
        description="连接密码（由前端 localStorage 持有）",
    ),
) -> StreamingResponse:
    """触发健康巡检，返回 SSE 流。

    逐项推送 check_progress / check_warning / check_error 事件，
    最终以 health_result 事件结束。

    Args:
        connection_id: 目标数据库连接 ID。
        check_items: 检查项列表。
        timeout_sec: 巡检超时（秒）。
        password: 连接密码。

    Returns:
        StreamingResponse（Content-Type: text/event-stream）。
    """
    logger.info("健康巡检请求开始", connection_id=connection_id,
                check_items=check_items, timeout_sec=timeout_sec)

    return StreamingResponse(
        _health_stream(connection_id, check_items, timeout_sec, password),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/reports", response_model=HealthReportListResponse)
async def list_reports(
    connection_id: str | None = Query(default=None, description="按连接 ID 筛选"),
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(
        default=20, ge=1, le=100, alias="pageSize",
        description="每页条数（最大 100）",
    ),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Any:
    """获取健康巡检报告历史列表（分页）。"""
    logger.info("API 请求开始", endpoint="list_reports",
                connection_id=connection_id, page=page)

    # 构建查询
    query = select(ReportModel)
    count_query = select(func.count()).select_from(ReportModel)

    if connection_id:
        query = query.where(ReportModel.connection_id == connection_id)
        count_query = count_query.where(
            ReportModel.connection_id == connection_id,
        )

    # 总数
    total_result = await session.execute(count_query)
    total = total_result.scalar() or 0

    # 分页数据（按生成时间降序）
    stmt = (
        query
        .order_by(ReportModel.generated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await session.execute(stmt)
    reports = result.scalars().all()

    items = [HealthReportResponse.model_validate(r) for r in reports]

    logger.info("API 请求完成", endpoint="list_reports",
                total=total, returned=len(items))
    return HealthReportListResponse(
        items=items, total=total, page=page, page_size=page_size,
    )


@router.get("/api/reports/{report_id}", response_model=HealthReportResponse)
async def get_report(
    report_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Any:
    """获取单份健康巡检报告详情。"""
    logger.info("API 请求开始", endpoint="get_report", report_id=report_id)

    result = await session.execute(
        select(ReportModel).where(ReportModel.id == report_id)
    )
    report = result.scalar_one_or_none()

    if report is None:
        logger.warning("API 请求失败", endpoint="get_report",
                       report_id=report_id, reason="not_found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "NOT_FOUND",
                "user_message": f"报告 {report_id} 不存在或已删除",
            },
        )

    logger.info("API 请求完成", endpoint="get_report",
                report_id=report_id)
    return HealthReportResponse.model_validate(report)


# =============================================================================
# GET /api/reports/{id}/export — 导出巡检报告（B-25）
# =============================================================================


@router.get("/api/reports/{report_id}/export")
async def export_report(
    report_id: str,
    format: str = Query(default="html", alias="format", description="导出格式：html / pdf"),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Any:
    """导出健康巡检报告（HTML/PDF）。

    HTML 使用 Jinja2 内联模板渲染，含健康评分环形图和分类检查项列表。
    PDF 由 WeasyPrint 从 HTML 转换生成。
    底部含 `Generated by DB-Pilot | <timestamp> UTC` 水印。

    Args:
        report_id: 报告 ID。
        format: 导出格式（html / pdf）。
        session: 数据库会话。

    Returns:
        format=html: HTML 响应（text/html）。
        format=pdf: PDF 文件下载（application/pdf）。

    Raises:
        HTTPException 404: 报告不存在。
        HTTPException 400: 不支持的格式。
    """
    # AC-6：入口日志
    start_ts = time.monotonic()
    logger.info("API 请求开始", endpoint="export_report",
                report_id=report_id, format=format)

    # 加载报告
    result = await session.execute(
        select(ReportModel).where(ReportModel.id == report_id)
    )
    report = result.scalar_one_or_none()
    if report is None:
        logger.warning("API 请求失败", endpoint="export_report",
                       report_id=report_id, reason="not_found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "NOT_FOUND",
                "user_message": f"报告 {report_id} 不存在或已删除",
            },
        )

    # 计算环形图参数
    score = min(max(report.score or 0, 0), 100)
    circumference = 2 * 3.14159 * 50  # r=50 的圆周长
    score_circumference = circumference * score / 100

    # 评分颜色
    if score >= 80:
        score_color = "#27ae60"
        score_grade = "健康"
    elif score >= 60:
        score_color = "#f39c12"
        score_grade = "警告"
    else:
        score_color = "#e74c3c"
        score_grade = "严重"

    # Jinja2 渲染 HTML
    from jinja2 import Environment, FileSystemLoader

    template_dir = Path(__file__).resolve().parent.parent / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)))
    template = env.get_template("report.html")

    from datetime import UTC, datetime

    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")

    html = template.render(
        report=report,
        score_color=score_color,
        score_grade=score_grade,
        score_circumference=score_circumference,
        generated_at=generated_at,
    )

    # 响应
    if format == "html":
        elapsed_ms = int((time.monotonic() - start_ts) * 1000)
        logger.info("API 请求完成", endpoint="export_report",
                    report_id=report_id, format="html",
                    elapsed_ms=elapsed_ms)
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content=html)

    if format == "pdf":
        try:
            from weasyprint import HTML  # type: ignore[import-untyped]

            pdf_bytes = HTML(string=html).write_pdf()
            elapsed_ms = int((time.monotonic() - start_ts) * 1000)
            logger.info("API 请求完成", endpoint="export_report",
                        report_id=report_id, format="pdf",
                        elapsed_ms=elapsed_ms, size_bytes=len(pdf_bytes))
            from fastapi.responses import Response
            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f'attachment; filename="report_{report_id}.pdf"',
                },
            )
        except ImportError:
            logger.error("WeasyPrint 未安装，无法导出 PDF")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error_code": "FEATURE_NOT_AVAILABLE",
                    "user_message": "PDF 导出功能需要服务端安装 WeasyPrint，请使用 HTML 格式",
                },
            ) from None

    logger.warning("API 请求失败", endpoint="export_report",
                   report_id=report_id, format=format, reason="unsupported_format")
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "error_code": "INVALID_PARAM",
            "user_message": f"不支持的导出格式：{format}，仅支持 html / pdf",
        },
    )
