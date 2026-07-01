"""
链路追踪上下文（Correlation ID）。

通过 contextvars 在异步调用链中透传 trace_id 和 connection_id，
确保同一次请求的所有日志可以关联。

依据 PRD §8.2、AGENTS.md §安全与合规红线：
  - trace_id 用于全链路日志关联（不包含敏感信息）
  - connection_id 标识当前操作的数据库连接（用于审计）

用法：
  from app.correlation import get_trace_id, set_trace_id

  set_trace_id("uuid-xxx")
  logger.info("操作完成", trace_id=get_trace_id())
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

# =============================================================================
# ContextVars（异步上下文安全）
# =============================================================================

_trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")
"""当前请求/任务的追踪 ID（对应 X-Request-ID）。"""

_connection_id_var: ContextVar[str | None] = ContextVar(
    "connection_id", default=None,
)
"""当前操作的数据库连接 ID，None 表示未关联连接。"""


# =============================================================================
# Getter / Setter
# =============================================================================

def get_trace_id() -> str:
    """获取当前上下文的追踪 ID。

    Returns:
        追踪 ID 字符串，无追踪上下文时返回空字符串。
    """
    return _trace_id_var.get()


def set_trace_id(trace_id: str) -> None:
    """设置当前上下文的追踪 ID。

    Args:
        trace_id: 追踪 ID（通常为 UUID v4）。
    """
    _trace_id_var.set(trace_id)


def get_connection_id() -> str | None:
    """获取当前上下文关联的数据库连接 ID。

    Returns:
        连接 ID 或 None。
    """
    return _connection_id_var.get()


def set_connection_id(connection_id: str | None) -> None:
    """设置当前上下文关联的数据库连接 ID。

    Args:
        connection_id: 数据库连接 ID，None 表示清除关联。
    """
    _connection_id_var.set(connection_id)


# =============================================================================
# 日志上下文辅助
# =============================================================================

def get_log_context() -> dict[str, Any]:
    """获取当前所有追踪上下文字段，供日志绑定使用。

    Returns:
        包含 trace_id 和 connection_id 的字典（仅含非空值）。
    """
    ctx: dict[str, Any] = {}
    tid = get_trace_id()
    if tid:
        ctx["trace_id"] = tid
    cid = get_connection_id()
    if cid:
        ctx["connection_id"] = cid
    return ctx
