"""
日志配置模块。

使用 structlog 实现结构化日志，支持 JSON（生产）和彩色文本（开发）两种格式。
集成 contextvars 自动注入 trace_id、connection_id 等链路追踪字段。

依据 PRD §8.2、AGENTS.md §安全与合规红线：
  - 敏感字段自动掩盖（password、token、secret 等）
  - trace_id 透传全链路
  - 日志不含完整 SQL 结果行数据
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog
from structlog.typing import EventDict

from app.config import settings

# =============================================================================
# 敏感字段掩盖处理器
# =============================================================================

_SENSITIVE_KEYS: set[str] = {
    "password", "token", "secret", "api_key", "api-key",
    "authorization", "auth", "credential", "private_key",
}


def _mask_sensitive_keys(
    logger: structlog.typing.WrappedLogger,  # noqa: ARG001
    method_name: str,  # noqa: ARG001
    event_dict: EventDict,
) -> EventDict:
    """掩盖事件字典中的敏感字段值。

    对键名匹配 _SENSITIVE_KEYS 的字段，将值替换为 "***"。
    递归处理嵌套字典。
    """
    def _mask(value: Any, depth: int = 0) -> Any:
        if depth > 5:
            return value
        if isinstance(value, dict):
            return {
                k: "***" if k.lower() in _SENSITIVE_KEYS else _mask(v, depth + 1)
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [_mask(v, depth + 1) for v in value]
        return value

    return _mask(event_dict)  # type: ignore[return-value]


# =============================================================================
# structlog 处理器链
# =============================================================================

def _add_timestamp(
    logger: structlog.typing.WrappedLogger,  # noqa: ARG001
    method_name: str,  # noqa: ARG001
    event_dict: EventDict,
) -> EventDict:
    """添加 ISO 8601 时间戳。"""
    from datetime import UTC, datetime
    event_dict["timestamp"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return event_dict


def _add_logger_name(
    logger: structlog.typing.WrappedLogger,  # noqa: ARG001
    method_name: str,  # noqa: ARG001
    event_dict: EventDict,
) -> EventDict:
    """注入 logger 名称到事件字典（structlog 默认不携带）。"""
    # structlog 在处理器中不直接暴露 logger name，我们通过 __name__ 在
    # 调用侧绑定。此处仅确保 level 字段存在
    return event_dict


# =============================================================================
# 配置函数
# =============================================================================


def configure_logging() -> None:
    """配置应用级结构化日志。

    在 FastAPI 应用启动时调用一次。根据 settings.LOG_FORMAT 切换格式：
      - "json": JSON 行输出（生产环境）
      - "text": 带颜色的可读文本（开发环境）

    根据 settings.LOG_LEVEL 控制日志级别。
    如果 settings.LOG_FILE 指定了路径，同时写入文件。
    """
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    log_format = settings.LOG_FORMAT.lower()

    # ── 通用处理器链 ──
    # 这些处理器在所有格式之前执行
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        _add_timestamp,
        _mask_sensitive_keys,
        structlog.stdlib.add_log_level,
        structlog.stdlib.ExtraAdder(),  # 保留 extra 字段
    ]

    if log_format == "json":
        # 生产环境：JSON 行
        processors = shared_processors + [
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]
    else:
        # 开发环境：彩色控制台
        processors = shared_processors + [
            structlog.dev.set_exc_info,
            structlog.dev.ConsoleRenderer(
                colors=sys.stderr.isatty(),
            ),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # ── 配置标准库 logging（structlog 作为底层） ──
    handler: logging.Handler
    if settings.LOG_FILE:
        os.makedirs(os.path.dirname(settings.LOG_FILE) or ".", exist_ok=True)
        handler = logging.FileHandler(settings.LOG_FILE, encoding="utf-8")
    else:
        handler = logging.StreamHandler(sys.stderr)

    handler.setLevel(log_level)

    # 使用 structlog 的标准库桥接
    stdlib_formatter = structlog.stdlib.ProcessorFormatter(
        processors=processors,
    )
    handler.setFormatter(stdlib_formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(handler)

    # 减少第三方库的日志噪音
    for noisy in ("httpx", "httpcore", "urllib3", "aiosqlite"):
        logging.getLogger(noisy).setLevel(max(log_level, logging.WARNING))

    # 记录启动日志
    logger = structlog.get_logger("app.log_setup")
    logger.info(
        "日志系统已初始化",
        level=settings.LOG_LEVEL,
        format=log_format,
        log_file=settings.LOG_FILE or "(stderr)",
    )
