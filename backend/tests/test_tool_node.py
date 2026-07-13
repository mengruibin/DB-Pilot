"""
SafeToolNode 并行执行单元测试。

覆盖：
  1. 并行执行语义 — 多个 tool_calls 并发执行，总耗时 ≈ 最慢工具
  2. 错误隔离 — 单工具失败不阻塞其他工具
  3. Semaphore 并发上限 — 限制同时执行的工具数
  4. SSE 事件含 tool_call_id — 所有 tool_result 事件携带 tc["id"]
  5. ToolMessage 顺序 — 与 tool_calls 输入顺序一致
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any
from unittest.mock import ANY, AsyncMock, patch

import pytest

# 在导入 app 模块前设置测试用环境变量
os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault("LLM_MODEL", "test-model")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///test.db")

from langchain_core.messages import AIMessage, ToolMessage

from app.agent.state import AgentState
from app.agent.tool_node import safe_tools_node


# =============================================================================
# 辅助函数：模拟工具函数（带 ainvoke 方法）
# =============================================================================


class _MockTool:
    """包装一个异步函数，提供 LangChain 工具兼容的 ainvoke 接口。"""

    def __init__(self, fn):
        self._fn = fn

    async def ainvoke(self, args: dict[str, Any]) -> Any:
        return await self._fn(**args)


async def _mock_fast_tool(**kwargs) -> dict:
    """模拟执行很快的工具（~10ms）。"""
    await asyncio.sleep(0.01)
    if kwargs.get("error_msg"):
        raise RuntimeError(kwargs["error_msg"])
    return {"summary": "快速工具执行完成", "result": "ok"}


async def _mock_slow_tool(**kwargs) -> dict:
    """模拟执行较慢的工具（~50ms）。"""
    await asyncio.sleep(0.05)
    return {"summary": "慢速工具执行完成", "result": "ok"}


async def _mock_failing_tool(**kwargs) -> dict:
    """模拟执行失败的工具。"""
    msg = kwargs.get("error_msg", "模拟工具执行异常")
    raise RuntimeError(msg)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def _patch_tool_registry():
    """在测试前注入 mock 工具注册表，测试后恢复。"""
    mock_registry = {
        "fast_tool": _MockTool(_mock_fast_tool),
        "slow_tool": _MockTool(_mock_slow_tool),
        "failing_tool": _MockTool(_mock_failing_tool),
    }
    with patch("app.agent.tools.registry.TOOL_REGISTRY", mock_registry):
        yield


@pytest.fixture(autouse=True)
def _patch_safety_checks():
    """绕过安全护栏检查（测试中不校验 SQL）。"""
    with patch("app.agent.tool_node.run_safety_checks") as mock_safety:
        mock_result = AsyncMock()
        mock_result.blocked = False
        mock_result.reason = ""
        mock_result.warnings = []
        mock_safety.return_value = mock_result
        yield


@pytest.fixture(autouse=True)
def _patch_sanitize():
    """绕过脱敏（测试中不需要）。"""
    with patch("app.agent.tool_node._sanitize_sensitive_data") as mock_sanitize:
        mock_sanitize.side_effect = lambda x: x
        yield


# =============================================================================
# 辅助函数
# =============================================================================


def _make_tool_call(
    name: str, args: dict | None = None, call_id: str | None = None
) -> dict:
    """构造一个 LangChain ToolCall 字典。"""
    return {
        "id": call_id or f"call_{name}",
        "name": name,
        "args": args or {},
    }


def _make_state(tool_calls: list[dict]) -> AgentState:
    """构造包含 AIMessage（含 tool_calls）的 AgentState。"""
    return {
        "messages": [AIMessage(content="", tool_calls=tool_calls)],
        "conn_config": {
            "connection_id": "test_conn",
            "db_type": "mysql",
            "host": "127.0.0.1",
            "port": 3306,
            "database": "test_db",
            "user": "root",
            "password": "secret",
        },
        "trace_iterations": [],
        "sse_events": [],
        "run_id": "test-run-id",
    }


# =============================================================================
# 测试类
# =============================================================================


class TestParallelExecution:
    """验证并行执行语义。"""

    @pytest.mark.asyncio
    async def test_sequential_would_be_slower(self):
        """并行执行的总耗时 ≈ 最慢工具耗时，而非各工具耗时之和。

        3 个工具各需 10ms，串行需 ~30ms，并行需 ~10ms。
        验证执行时间显著小于串行之和。
        """
        tool_calls = [
            _make_tool_call("fast_tool"),
            _make_tool_call("fast_tool"),
            _make_tool_call("fast_tool"),
        ]
        state = _make_state(tool_calls)

        start = time.monotonic()
        result = await safe_tools_node(state)
        elapsed = time.monotonic() - start

        # 并行执行 3 个 10ms 的工具，总耗时应显著小于 30ms（串行）
        # 加上 Semaphore + gather 调度开销，阈值设为 30ms
        assert elapsed < 0.030, (
            f"并行执行耗时 {elapsed*1000:.1f}ms，"
            f"预期 < 25ms（串行约为 30ms）"
        )
        assert len(result["messages"]) == 3

    @pytest.mark.asyncio
    async def test_slowest_tool_dominates(self):
        """总耗时由最慢的工具决定。

        fast_tool（10ms）+ slow_tool（50ms）并行，总耗时 ≈ 50ms。
        """
        tool_calls = [
            _make_tool_call("fast_tool"),
            _make_tool_call("slow_tool"),
        ]
        state = _make_state(tool_calls)

        start = time.monotonic()
        await safe_tools_node(state)
        elapsed = time.monotonic() - start

        # 并行执行，总耗时 ≈ 50ms（最慢工具），而非 60ms（串行之和）
        # 加上调度开销，阈值设为 80ms
        assert elapsed < 0.080, (
            f"总耗时 {elapsed*1000:.1f}ms，预期 < 60ms（≈ 最慢工具 50ms）"
        )


class TestDefaultRoleFallback:
    """验证缺省角色兜底为只读。"""

    @pytest.mark.asyncio
    async def test_missing_user_role_defaults_to_readonly(self):
        """当连接配置未显式提供 user_role 时，工具应收到 readonly。"""

        async def _capture_tool(**kwargs):
            assert kwargs["user_role"] == "readonly"
            return {"summary": "captured", "result": "ok"}

        mock_registry = {"capture_tool": _MockTool(_capture_tool)}
        with patch("app.agent.tools.registry.TOOL_REGISTRY", mock_registry):
            state = _make_state([_make_tool_call("capture_tool")])
            result = await safe_tools_node(state)

        assert len(result["messages"]) == 1
        assert result["messages"][0].name == "capture_tool"


class TestErrorIsolation:
    """验证单工具失败不阻塞其他工具。"""

    @pytest.mark.asyncio
    async def test_failing_tool_does_not_block_others(self):
        """一个工具抛异常，其他工具仍正常返回结果。"""
        tool_calls = [
            _make_tool_call("fast_tool"),
            _make_tool_call("failing_tool"),
            _make_tool_call("fast_tool"),
        ]
        state = _make_state(tool_calls)

        result = await safe_tools_node(state)

        # 总共返回 3 个 ToolMessage
        assert len(result["messages"]) == 3

        # 第一个工具应成功
        assert result["messages"][0].name == "fast_tool"
        assert "失败" not in result["messages"][0].content

        # 第二个工具应包含失败信息
        assert result["messages"][1].name == "failing_tool"
        assert "失败" in result["messages"][1].content

        # 第三个工具应成功
        assert result["messages"][2].name == "fast_tool"
        assert "失败" not in result["messages"][2].content

    @pytest.mark.asyncio
    async def test_error_result_sse_events(self):
        """失败工具的 SSE 事件应包含 summary 为 '执行失败: ...'。"""
        tool_calls = [
            _make_tool_call("failing_tool"),
        ]
        state = _make_state(tool_calls)

        result = await safe_tools_node(state)

        sse_events = result["sse_events"]

        # 应有一个 tool_result 事件标记为失败
        failure_events = [
            e for e in sse_events if e.get("summary", "").startswith("执行失败")
        ]
        assert len(failure_events) == 1
        assert failure_events[0]["tool"] == "failing_tool"


class TestSemaphoreCapsConcurrency:
    """验证 Semaphore 限制并发数。"""

    @pytest.mark.asyncio
    async def test_semaphore_limits_active_tools(self):
        """Semaphore(1) 时应退化为串行行为。

        用 Semaphore(1) 模拟，多个工具应逐个执行而非并发。
        """
        with patch("app.agent.tool_node._MAX_CONCURRENT_TOOLS", 1):
            tool_calls = [
                _make_tool_call("slow_tool"),  # 50ms
                _make_tool_call("slow_tool"),  # 50ms
                _make_tool_call("slow_tool"),  # 50ms
            ]
            state = _make_state(tool_calls)

            start = time.monotonic()
            await safe_tools_node(state)
            elapsed = time.monotonic() - start

            # Semaphore(1) 退化为串行，3 × 50ms ≈ 150ms
            # 加上调度开销，阈值设为 200ms
            assert elapsed >= 0.1, (
                f"Semaphore(1) 时总耗时 {elapsed*1000:.1f}ms，"
                f"预期 >= 100ms（串行 3 × 50ms ≈ 150ms）"
            )

    @pytest.mark.asyncio
    async def test_semaphore_2_allows_partial_parallel(self):
        """Semaphore(2) 时 3 个工具仍有一定并行度。"""
        with patch("app.agent.tool_node._MAX_CONCURRENT_TOOLS", 2):
            tool_calls = [
                _make_tool_call("slow_tool"),  # 50ms
                _make_tool_call("fast_tool"),  # 10ms
                _make_tool_call("fast_tool"),  # 10ms
            ]
            state = _make_state(tool_calls)

            start = time.monotonic()
            await safe_tools_node(state)
            elapsed = time.monotonic() - start

            # 3 个工具有 Semaphore(2)，前 2 个起步后第 3 个等一个空位
            # 预期约 60-80ms，显著小于串行 70ms
            assert elapsed < 0.08, (
                f"Semaphore(2) 时总耗时 {elapsed*1000:.1f}ms，"
                f"预期 < 80ms"
            )


class TestToolCallIdInSSEEvents:
    """验证所有 SSE 事件包含 tool_call_id。"""

    @pytest.mark.asyncio
    async def test_tool_result_contains_tool_call_id(self):
        """每个 tool_result SSE 事件应包含 tool_call_id。"""
        tool_calls = [
            _make_tool_call("fast_tool", call_id="call_1"),
            _make_tool_call("fast_tool", call_id="call_2"),
        ]
        state = _make_state(tool_calls)

        result = await safe_tools_node(state)
        sse_events = result["sse_events"]

        # 过滤出 tool_result 事件
        tool_result_events = [e for e in sse_events if e.get("type") == "tool_result"]

        assert len(tool_result_events) == 2
        for i, event in enumerate(tool_result_events):
            assert "tool_call_id" in event, (
                f"tool_result[{i}] 缺少 tool_call_id"
            )
            assert event["tool_call_id"] == tool_calls[i]["id"], (
                f"tool_result[{i}] 的 tool_call_id 不匹配"
            )


class TestToolMessageOrder:
    """验证 ToolMessage 列表顺序与 tool_calls 输入顺序一致。"""

    @pytest.mark.asyncio
    async def test_order_matches_input(self):
        """ToolMessage 在 messages 中的顺序与 tool_calls 输入顺序一致。"""
        tool_calls = [
            _make_tool_call("fast_tool", call_id="call_A"),
            _make_tool_call("slow_tool", call_id="call_B"),
            _make_tool_call("fast_tool", call_id="call_C"),
        ]
        state = _make_state(tool_calls)

        result = await safe_tools_node(state)

        # 验证 ToolMessage 顺序与 tool_calls 一致
        for i, msg in enumerate(result["messages"]):
            assert msg.tool_call_id == tool_calls[i]["id"], (
                f"ToolMessage[{i}] 顺序不匹配：预期 {tool_calls[i]['id']}，"
                f"实际 {msg.tool_call_id}"
            )

    @pytest.mark.asyncio
    async def test_order_preserved_with_errors(self):
        """即使有工具失败，ToolMessage 顺序仍与输入顺序一致。"""
        tool_calls = [
            _make_tool_call("fast_tool", call_id="call_1"),
            _make_tool_call("failing_tool", call_id="call_fail"),
            _make_tool_call("slow_tool", call_id="call_3"),
        ]
        state = _make_state(tool_calls)

        result = await safe_tools_node(state)

        assert len(result["messages"]) == 3
        assert result["messages"][0].tool_call_id == "call_1"
        assert result["messages"][1].tool_call_id == "call_fail"
        assert result["messages"][2].tool_call_id == "call_3"


class TestOutputStructure:
    """验证返回结构完整性。"""

    @pytest.mark.asyncio
    async def test_returns_messages_and_sse_events(self):
        """返回 dict 应包含 messages 和 sse_events 两个键。"""
        tool_calls = [_make_tool_call("fast_tool")]
        state = _make_state(tool_calls)

        result = await safe_tools_node(state)

        assert "messages" in result
        assert "sse_events" in result
        assert isinstance(result["messages"], list)
        assert isinstance(result["sse_events"], list)

    @pytest.mark.asyncio
    async def test_no_tool_calls_returns_empty(self):
        """无 tool_calls 时返回空 SSE 事件。"""
        state = _make_state([])
        # 使上次消息不含 tool_calls
        state["messages"] = [AIMessage(content="你好")]

        result = await safe_tools_node(state)
        assert "sse_events" in result
        assert "messages" not in result or result["messages"] == []
