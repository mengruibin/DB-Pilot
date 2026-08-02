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

import io
import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

import structlog
from structlog.processors import CallsiteParameter, CallsiteParameterAdder
from structlog.typing import EventDict

from app.config import settings

# =============================================================================
# 敏感字段掩盖处理器
# =============================================================================

_SENSITIVE_KEYS: set[str] = {
    "password",
    "token",
    "secret",
    "api_key",
    "api-key",
    "authorization",
    "auth",
    "credential",
    "private_key",
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


# =============================================================================
# 层次字段处理器
# 从 CallsiteParameterAdder 注入的 module 字段推导粗粒度层次，
# 便于按 app.api / app.agent / app.db ... 过滤日志。
# =============================================================================

_LAYER_MAP: dict[str, str] = {
    "main": "server",
    "api": "api",
    "agent": "agent",
    "engine": "engine",
    "db": "db",
    "database": "db",
    "models": "models",
    "auth": "auth",
    "log_setup": "observability",
    "correlation": "observability",
}


def _add_layer(
    logger: structlog.typing.WrappedLogger,  # noqa: ARG001
    method_name: str,  # noqa: ARG001
    event_dict: EventDict,
) -> EventDict:
    """从 module 字段推导日志所属层次（agent / api / db / engine ...）。

    模块路径形如 "app.agent.graph"，取第二段映射为可读层次名；
    未知模块（第三方库）回退到原始第二段，非模块来源回退为 "unknown"。
    """
    module = event_dict.get("module", "")
    if not module:
        event_dict["layer"] = "unknown"
        return event_dict
    parts = module.split(".")
    seg = parts[1] if len(parts) > 1 else parts[0]
    event_dict["layer"] = _LAYER_MAP.get(seg, seg)
    return event_dict


# =============================================================================
# 配置函数
# =============================================================================


def _utf8_console_stream() -> Any:
    """返回 UTF-8 安全的控制台输出流。

    Windows 默认控制台为 GBK（cp936），向 stderr 写入中文会乱码或触发
    UnicodeEncodeError。此处将 stderr 包装为 UTF-8 + backslashreplace 的流。
    - 已是 UTF-8 或非 TextIOWrapper（如测试中的 StringIO）时原样返回
    """
    stream = sys.stderr
    if stream is None or not isinstance(stream, io.TextIOWrapper):
        return stream
    encoding = (stream.encoding or "").lower().replace("_", "-")
    if encoding in ("utf-8", "utf8"):
        return stream
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        return stream
    try:
        return io.TextIOWrapper(buffer, encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):
        return stream


def _build_shared_processors() -> list[structlog.typing.Processor]:
    """构建通用预处理处理器链（在渲染之前对所有日志执行）。

    同时用于：
      1) structlog.configure() 中的全局处理器链
      2) ProcessorFormatter.foreign_pre_chain（处理非 structlog 来源日志）

    处理链职责：
      - merge_contextvars：自动注入 trace_id / session_id 等 contextvars 字段
      - add_log_level：补充 level 字段
      - _add_timestamp：补充 UTC 时间戳
      - _mask_sensitive_keys：脱敏 password/token/secret 等
      - CallsiteParameterAdder：注入调用点信息（模块名/函数名/行号）
      - _add_layer：从 module 推导层次字段（必须在 CallsiteParameterAdder 之后）
      - format_exc_info：异常堆栈
    """
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        _add_timestamp,
        _mask_sensitive_keys,
        CallsiteParameterAdder(
            [
                CallsiteParameter.MODULE,
                CallsiteParameter.FUNC_NAME,
                CallsiteParameter.LINENO,
            ]
        ),
        _add_layer,
        structlog.processors.format_exc_info,
    ]


def configure_logging() -> None:
    """配置应用级结构化日志，同时输出到控制台和每日轮转文件。

    在 FastAPI 应用启动时调用一次。
    - 控制台：stderr，格式由 LOG_FORMAT 决定（text=彩色/ json=JSON行）
    - 文件： 按日轮转，始终输出 JSON 格式（便于程序解析）

    structlog 处理链设计：
      - 全局处理器（structlog.configure）：处理元数据（时间戳、脱敏、异常信息），
        最后通过 ProcessorFormatter.wrap_for_formatter 将事件字典交给 handler 的 formatter
      - Handler formatter：各自使用不同的最终渲染器
        （ConsoleRenderer → 控制台 / JSONRenderer → 文件）
      - foreign_pre_chain：处理非 structlog 来源的日志（如第三方库的 warn/error）
    """
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    log_format = settings.LOG_FORMAT.lower()
    log_dir = Path(settings.LOG_DIR)

    # ── 通用预处理处理器（在渲染之前对所有日志执行） ──
    shared_processors = _build_shared_processors()

    # ── 配置 structlog 全局处理器链 ──
    # 注意：不包含渲染器！渲染交给 handler 级别的 formatter。
    # wrap_for_formatter 将事件字典传递给 handler 的 ProcessorFormatter。
    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # ── 构建 handler 级别的 formatter ──
    # 控制台：LOG_FORMAT=text 用彩色控制台，json 用 JSON 行
    if log_format == "json":
        console_renderer: structlog.typing.Processor = structlog.processors.JSONRenderer(
            ensure_ascii=False,
        )
    else:
        console_renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    console_formatter = structlog.stdlib.ProcessorFormatter(
        processor=console_renderer,
        foreign_pre_chain=shared_processors,
    )

    # 文件：始终使用 JSON（避免 ANSI 转义码污染日志文件）
    # ensure_ascii=False：日志文件中直接输出中文，便于人工 grep/阅读
    file_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(ensure_ascii=False),
        foreign_pre_chain=shared_processors,
    )

    # ── 配置标准库 logging ──
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # 移除现有 handler（防止重复添加，当配置变更重载时）
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)

    # 1. 控制台 handler（stderr，UTF-8 安全流防止 Windows GBK 控制台中文乱码）
    console_handler = logging.StreamHandler(_utf8_console_stream())
    console_handler.setLevel(log_level)
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # 2. 文件 handler（每日轮转）
    log_dir.mkdir(parents=True, exist_ok=True)
    if settings.LOG_FILE:
        log_path = str(Path(settings.LOG_FILE))
    else:
        log_path = str(log_dir / "db-pilot.log")
    # 使用 TimedRotatingFileHandler：每天凌晨轮转，保留 N 天
    # 轮转后文件命名为：db-pilot.log.2026-07-02，当前日志始终写入 db-pilot.log
    file_handler = TimedRotatingFileHandler(
        filename=log_path,
        when="midnight",
        interval=1,
        backupCount=settings.LOG_BACKUP_DAYS,
        encoding="utf-8",
        delay=False,
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    # 减少第三方库的日志噪音——强制 WARNING，即使 LOG_LEVEL=DEBUG 也不输出
    for noisy in (
        "httpx",
        "httpx._client",
        "httpx._config",
        "httpcore",
        "httpcore._async",
        "httpcore._sync",
        "openai",
        "openai._base_client",
        "urllib3",
        "aiosqlite",
        "aiomysql",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)
        # 禁止传播到根日志器，避免被 structlog foreign_pre_chain 格式化输出
        logging.getLogger(noisy).propagate = False

    # 记录启动日志
    logger = structlog.get_logger("app.log_setup")
    logger.info(
        "日志系统已初始化",
        level=settings.LOG_LEVEL,
        format=log_format,
        console="stderr",
        log_file=log_path,
        backup_days=settings.LOG_BACKUP_DAYS,
    )
