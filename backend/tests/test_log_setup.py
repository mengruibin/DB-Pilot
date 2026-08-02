"""
日志配置层单元测试。

覆盖（2026-08-02 日志可观测性修复）：
  1. _add_layer：从 module 推导层次字段（agent/api/db/server ...）
  2. 真实处理链 _build_shared_processors：注入 module/func_name/lineno + 脱敏
  3. JSONRenderer(ensure_ascii=False)：中文原文输出而非 \\uXXXX 转义
  4. _mask_sensitive_keys：敏感字段掩盖（回归保护）
  5. _utf8_console_stream：Windows GBK 控制台 UTF-8 安全流
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

# 在导入 app 模块前设置测试用环境变量（config.py 启动时校验必填字段）
# 若已在环境中配置则保留原值
os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault("LLM_MODEL", "test-model")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///test.db")

import structlog  # noqa: E402, I001
from structlog.typing import EventDict  # noqa: E402

from app.config import settings  # noqa: E402
from app.log_setup import (  # noqa: E402, I001
    _add_layer,
    _build_shared_processors,
    _utf8_console_stream,
    configure_logging,
)


def _run_chain(event_dict: dict[str, Any]) -> EventDict:
    """手动跑一遍真实处理链，验证各处理器对事件字典的累计效果。"""
    logger = structlog.get_logger("test.log_setup")
    result: EventDict = dict(event_dict)
    for proc in _build_shared_processors():
        result = proc(logger, "info", result)
    return result


# =============================================================================
# 层次字段推导
# =============================================================================


@pytest.mark.parametrize(
    ("module", "expected_layer"),
    [
        ("app.agent.graph", "agent"),
        ("app.agent.tools.query", "agent"),
        ("app.db.mysql", "db"),
        ("app.database", "db"),
        ("app.api.chat", "api"),
        ("app.main", "server"),
        ("app.engine.sql_auditor", "engine"),
        ("app.models.session", "models"),
        ("app.log_setup", "observability"),
        # 未知模块：回退到原始第二段
        ("some.third_party.mod", "third_party"),
    ],
)
def test_add_layer_derivation(module: str, expected_layer: str) -> None:
    """_add_layer 从 module 字段推导正确的层次名。"""
    event: EventDict = {"module": module}
    _add_layer(None, "info", event)
    assert event["layer"] == expected_layer


def test_add_layer_no_module_falls_back_to_unknown() -> None:
    """无 module 字段时回退为 unknown。"""
    event: EventDict = {}
    _add_layer(None, "info", event)
    assert event["layer"] == "unknown"


# =============================================================================
# 真实处理链：调用点信息 + 脱敏
# =============================================================================


def test_chain_injects_callsite_info() -> None:
    """处理链为每条日志注入 module / func_name / lineno。

    注：此处直接手动调用处理器链（绕过 structlog 的 BoundLogger），
    因此 CallsiteParameterAdder 捕获到的调用点是本测试的辅助函数 _run_chain，
    而非更上层的调用者——这符合"处理器从栈帧取直接调用者"的行为。
    """

    def _emit() -> EventDict:
        return _run_chain({"event": "测试日志"})

    event = _emit()
    assert event["module"].endswith("test_log_setup")
    assert isinstance(event["func_name"], str) and event["func_name"]
    assert isinstance(event["lineno"], int) and event["lineno"] > 0
    assert event["layer"]  # 由 module 推导，非空


def test_chain_masks_sensitive_keys() -> None:
    """处理链递归掩盖敏感字段（password/token/secret）。"""
    event = _run_chain(
        {
            "event": "连接配置已解析",
            "conn": {
                "password": "p@ssw0rd",
                "nested": {"token": "abc123", "keep": "visible"},
            },
        }
    )
    assert event["conn"]["password"] == "***"
    assert event["conn"]["nested"]["token"] == "***"
    assert event["conn"]["nested"]["keep"] == "visible"


def test_chain_masks_sensitive_values_from_contextvars() -> None:
    """merge_contextvars 注入的上下文中的敏感字段同样被掩盖。"""
    structlog.contextvars.clear_contextvars()
    try:
        structlog.contextvars.bind_contextvars(password="ctx-secret", trace_id="t-1")
        event = _run_chain({"event": "请求完成"})
        assert event["password"] == "***"
        assert event["trace_id"] == "t-1"  # 非敏感字段保留
    finally:
        structlog.contextvars.clear_contextvars()


# =============================================================================
# JSON 渲染：中文原文输出（乱码修复）
# =============================================================================


def test_json_renderer_outputs_raw_chinese() -> None:
    """ensure_ascii=False 时输出中文原文而非 \\uXXXX 转义。"""
    renderer = structlog.processors.JSONRenderer(ensure_ascii=False)
    out = renderer(None, None, {"event": "空闲会话扫描完毕", "level": "info"})
    assert "空闲会话扫描完毕" in out
    assert "\\u" not in out


def test_json_renderer_default_escapes_unicode() -> None:
    """默认 JSONRenderer 仍转义中文（对比基线）。"""
    renderer = structlog.processors.JSONRenderer()
    out = renderer(None, None, {"event": "中文"})
    assert "\\u4e2d\\u6587" in out


# =============================================================================
# 控制台 UTF-8 安全流
# =============================================================================


def test_utf8_console_stream_skips_non_textio(monkeypatch: pytest.MonkeyPatch) -> None:
    """非 TextIOWrapper（如测试中的 StringIO）原样返回，不包装。"""
    fake = io.StringIO()
    monkeypatch.setattr(sys, "stderr", fake)
    assert _utf8_console_stream() is fake


def test_utf8_console_stream_skips_utf8_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """已是 UTF-8 的流原样返回。"""
    fake = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    monkeypatch.setattr(sys, "stderr", fake)
    assert _utf8_console_stream() is fake


def test_utf8_console_stream_wraps_gbk_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """GBK 控制台流被包装为 UTF-8 编码。"""
    fake = io.TextIOWrapper(io.BytesIO(), encoding="gbk")
    monkeypatch.setattr(sys, "stderr", fake)
    wrapped = _utf8_console_stream()
    assert wrapped is not fake
    assert (wrapped.encoding or "").lower().replace("_", "-") == "utf-8"
    # 包装流可正确写入中文（GBK 原始流编码失败，包装流成功）
    wrapped.write("空闲会话")
    wrapped.flush()
    fake.buffer.seek(0)
    assert fake.buffer.getvalue().decode("utf-8") == "空闲会话"


# =============================================================================
# 端到端：真实 configure_logging() 输出
# =============================================================================


@pytest.fixture
def isolated_log_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """将日志输出重定向到临时文件并调用真实 configure_logging()。

    测试结束后恢复全局 structlog 配置与 root logger 原有 handler，
    避免污染其他测试。
    """
    log_file = tmp_path / "test.log"
    monkeypatch.setattr(settings, "LOG_LEVEL", "DEBUG")
    monkeypatch.setattr(settings, "LOG_FORMAT", "json")
    monkeypatch.setattr(settings, "LOG_FILE", str(log_file))
    monkeypatch.setattr(settings, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "LOG_BACKUP_DAYS", 7)

    old_config = structlog.get_config()
    root = logging.getLogger()
    old_level = root.level
    old_handlers = list(root.handlers)

    configure_logging()

    yield log_file

    # 清理：关闭并移除 configure_logging 添加的 handler，恢复原配置
    for h in list(root.handlers):
        if h not in old_handlers:
            h.close()
            root.removeHandler(h)
    root.setLevel(old_level)
    structlog.configure(**old_config)


def test_configure_logging_writes_attributed_json_file(
    isolated_log_file: Path,
) -> None:
    """真实 configure_logging() 输出的文件：中文原文 + 调用点 + 层次字段。"""

    def _emit() -> None:
        # 模拟业务模块的日志调用
        logger = structlog.get_logger("app.db.mysql")
        logger.info("MySQL 查询完成", duration_ms=12, sql="SELECT 1")

    _emit()

    # 关闭文件 handler 确保数据落盘
    for h in logging.getLogger().handlers:
        h.flush()

    lines = isolated_log_file.read_text(encoding="utf-8").strip().splitlines()
    assert lines, "日志文件应为空"

    # 从文件末尾向前找第一条业务日志（文件头部可能有启动日志）
    event: dict[str, Any] | None = None
    for line in reversed(lines):
        parsed = json.loads(line)
        if parsed.get("event") == "MySQL 查询完成":
            event = parsed
            break
    assert event is not None

    # 中文原文输出（无 \uXXXX 转义）
    assert "\\u" not in isolated_log_file.read_text(encoding="utf-8")

    # 调用点信息 + 层次字段
    # 注意：CallsiteParameterAdder 捕获的是"调用点所在模块"而非 get_logger() 的
    # 名字。生产代码每个文件用 get_logger(__name__)，两者一致；此处日志调用发生在
    # 测试模块内，故 module=test_log_setup（与生产语义一致，仅路径不同）。
    # module→layer 的映射正确性由 test_add_layer_derivation 单测覆盖。
    assert event["module"] == "test_log_setup"
    assert event["func_name"] == "_emit"
    assert isinstance(event["lineno"], int) and event["lineno"] > 0
    assert event["layer"] == "test_log_setup"

    # 业务字段保留
    assert event["duration_ms"] == 12
    assert event["sql"] == "SELECT 1"
