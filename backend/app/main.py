"""
DB-Pilot FastAPI 应用工厂。

依据 AGENTS.md §目录与命名规范：
  - main.py 仅包含应用工厂 + 生命周期钩子，不写业务逻辑
  - API 路由委托给 agent/ 或 engine/，仅做薄路由层

依据 AGENTS.md §API 与数据契约：
  - X-Request-ID 头注入所有响应（UUID v4 格式）
  - SSE 路径：/api/chat/stream；连接管理 CRUD：/api/connections
  - 分页默认 pageSize=20，最大 pageSize=100

链路追踪（Observability）：
  - AccessLogMiddleware 记录每个 HTTP 请求的 method/path/status/duration
  - RequestIDMiddleware 将 X-Request-ID 写入 contextvars 供全链路透传
  - structlog 结构化日志，支持 JSON 输出（生产）和彩色文本（开发）
  - 敏感字段自动掩盖（password、token、secret 等）
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.correlation import set_trace_id
from app.log_setup import configure_logging

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = structlog.get_logger("app.main")


# =============================================================================
# X-Request-ID 中间件
# 每请求分配唯一追踪 ID，写入 contextvars 供全链路日志关联。
# 依据 AGENTS.md §API 与数据契约：所有响应必须携带 X-Request-ID 头。
# =============================================================================

class RequestIDMiddleware:
    """ASGI 中间件：注入 X-Request-ID 头 + 设置链路追踪上下文。

    1. 从请求头 X-Request-ID 读取已有 ID，或生成新的 UUID v4
    2. 将 ID 写入 response 头
    3. 将 ID 设置到 contextvars（trace_id_var），供全链路 structlog 自动绑定
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))

        # 设置链路追踪上下文
        set_trace_id(request_id)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                headers[b"x-request-id"] = request_id.encode()
                message["headers"] = list(headers.items())
            await send(message)

        await self.app(scope, receive, send_with_request_id)


# =============================================================================
# 访问日志中间件
# 记录每个 HTTP 请求的方法、路径、状态码、耗时。
# =============================================================================

class AccessLogMiddleware:
    """ASGI 中间件：记录 HTTP 请求访问日志。

    记录字段：method, path, status_code, duration_ms, trace_id, user_agent
    不记录：请求体内容（可能包含密码）、查询参数中的敏感值
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        start = time.monotonic()
        status_code = 0

        async def send_with_log(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
            await send(message)

        try:
            await self.app(scope, receive, send_with_log)
        except Exception:
            duration_ms = int((time.monotonic() - start) * 1000)
            from app.correlation import get_trace_id
            logger.error(
                "请求异常",
                method=request.method,
                path=request.url.path,
                status_code=status_code or 500,
                duration_ms=duration_ms,
                trace_id=get_trace_id(),
            )
            raise
        else:
            duration_ms = int((time.monotonic() - start) * 1000)
            from app.correlation import get_trace_id
            logger.info(
                "请求完成",
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_ms=duration_ms,
                trace_id=get_trace_id(),
            )


# =============================================================================
# 应用生命周期
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """应用启动/关闭生命周期。

    启动时：
      - 初始化结构化日志系统
      - config.py 已在导入时完成校验，到达此处说明配置合法

    关闭时：
      - 预留清理入口
    """
    # 启动
    configure_logging()
    logger.info("服务启动", version=app.version)

    yield

    # 关闭
    logger.info("服务关闭")
    app.state.started = False


# =============================================================================
# 应用工厂
# =============================================================================

def create_app() -> FastAPI:
    """构建并返回配置好的 FastAPI 应用实例。"""
    app = FastAPI(
        title="DB-Pilot API",
        description="AI 驱动的数据库运维助手 — REST + SSE API",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # ---- 中间件栈（顺序：从上到下包裹） ----

    # 1. 访问日志（最外层：最先收到请求，最后收到响应）
    app.add_middleware(AccessLogMiddleware)

    # 2. X-Request-ID 追踪
    app.add_middleware(RequestIDMiddleware)

    # 3. CORS 跨域
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- 健康检查端点 ----

    @app.get("/api/health", tags=["Health"])
    async def health_check(request: Request):  # noqa: ARG001
        """存活探针——服务运行中返回 200。"""
        return {
            "status": "healthy",
            "version": app.version,
        }

    # ---- 注册路由 ----
    from app.api.connection import router as connection_router
    app.include_router(connection_router)

    return app


# =============================================================================
# 应用实例——uvicorn 通过 "app.main:app" 引用
# =============================================================================

app = create_app()
