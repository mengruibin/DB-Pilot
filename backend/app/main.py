"""
DB-Pilot FastAPI 应用工厂。

依据 AGENTS.md §目录与命名规范：
  - main.py 仅包含应用工厂 + 生命周期钩子，不写业务逻辑
  - API 路由委托给 agent/ 或 engine/，仅做薄路由层

依据 AGENTS.md §API 与数据契约：
  - X-Request-ID 头注入所有响应（UUID v4 格式）
  - SSE 路径：/api/chat/stream；连接管理 CRUD：/api/connections
  - 分页默认 pageSize=20，最大 pageSize=100
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send


# =============================================================================
# X-Request-ID 中间件
# 每请求分配唯一追踪 ID，便于全链路日志追踪和前端调试。
# 依据 AGENTS.md §API 与数据契约：所有响应必须携带 X-Request-ID 头。
# =============================================================================

class RequestIDMiddleware:
    """ASGI 中间件：向每个响应注入 X-Request-ID（UUID v4 格式）。

    如果请求已携带 X-Request-ID 头，则复用该值（适用于网关透传）；
    否则生成一个新的 UUID v4。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # 仅处理 HTTP 请求，忽略 WebSocket 等其他协议
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        # 复用已有 ID 或生成新 UUID
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))

        async def send_with_request_id(message: Message) -> None:
            # 拦截 HTTP 响应头写入，注入 X-Request-ID
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                headers[b"x-request-id"] = request_id.encode()
                message["headers"] = list(headers.items())
            await send(message)

        await self.app(scope, receive, send_with_request_id)


# =============================================================================
# 应用生命周期
# 依据 AGENTS.md §目录与命名规范：生命周期钩子集中在 main.py。
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """应用启动/关闭生命周期。

    启动时：
      - config.py 的模块级单例已在导入时完成校验，到达此处说明配置合法。

    关闭时：
      - 预留清理入口（连接池关闭、后台任务取消等）。
    """
    # 启动
    app.state.started = True

    yield

    # 关闭
    app.state.started = False


# =============================================================================
# 应用工厂
# =============================================================================

def create_app() -> FastAPI:
    """构建并返回配置好的 FastAPI 应用实例。

    依据 AGENTS.md §目录与命名规范：
      - FastAPI() 构造函数只在这里调用一次。
      - 业务逻辑放在 agent/ 和 engine/ 中。
    """
    app = FastAPI(
        title="DB-Pilot API",
        description="AI 驱动的数据库运维助手 — REST + SSE API",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # ---- 中间件栈 ----

    # 1. X-Request-ID 追踪（AGENTS.md §API 与数据契约）
    app.add_middleware(RequestIDMiddleware)

    # 2. CORS 跨域（按 .env.example 中 CORS_ORIGINS 配置）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- 健康检查端点（无认证，Phase 1 默认） ----

    @app.get("/api/health", tags=["Health"])
    async def health_check(request: Request):
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
