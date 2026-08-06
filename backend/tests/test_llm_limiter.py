"""
LLMLimiter 全局 LLM 并发限流单元/集成测试（llm-concurrency-limit-plan）。

覆盖：
  1. 并发上限 — 低于上限可并行，超上限退化为串行/排队
  2. 排队超时 — 饱和时等待超过 wait_timeout 抛 LLMConcurrencyBusyError
  3. 槽位释放 — 使用完毕释放，后续 acquire 立即成功
  4. agent_node 集成 — 饱和超时时返回 is_complete + LLM_BUSY SSE 错误事件，不执行 ainvoke
"""

from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import patch

import pytest

# 在导入 app 模块前设置测试用环境变量
os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault("LLM_MODEL", "test-model")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///test.db")

from langchain_core.messages import AIMessage

from app.agent.llm_limiter import LLMConcurrencyBusyError, LLMLimiter
from app.agent.state import AgentState

# =============================================================================
# LLMLimiter 单元测试
# =============================================================================


class TestLLMLimiter:
    @pytest.mark.asyncio
    async def test_slot_allows_parallel_under_cap(self):
        """低于上限的并发槽位可全部获得。"""
        limiter = LLMLimiter(max_concurrent=3, wait_timeout=1.0)

        async def _enter(i: int) -> int:
            async with limiter.slot():
                return i

        results = await asyncio.gather(*(_enter(i) for i in range(3)))
        assert sorted(results) == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_slot_serializes_above_cap(self):
        """上限为 1 时，第二个 slot 必须等第一个释放后才进入（退化为串行）。"""
        limiter = LLMLimiter(max_concurrent=1, wait_timeout=5.0)
        holder = limiter.slot()
        await holder.__aenter__()  # 持有唯一槽位

        second_entered = asyncio.Event()

        async def second() -> None:
            async with limiter.slot():
                second_entered.set()

        task = asyncio.create_task(second())
        await asyncio.sleep(0.05)
        assert not second_entered.is_set(), "槽位被占，第二个 slot 不应进入"

        await holder.__aexit__(None, None, None)  # 释放槽位
        await asyncio.wait_for(task, timeout=1.0)
        assert second_entered.is_set(), "释放后第二个 slot 应立即进入"

    @pytest.mark.asyncio
    async def test_slot_timeout_raises_busy(self):
        """饱和时排队超过 wait_timeout 抛 LLMConcurrencyBusyError（而非无限挂起）。"""
        limiter = LLMLimiter(max_concurrent=1, wait_timeout=0.05)
        holder = limiter.slot()
        await holder.__aenter__()  # 占用唯一槽位

        start = time.monotonic()
        with pytest.raises(LLMConcurrencyBusyError):
            async with limiter.slot():
                pass
        elapsed = time.monotonic() - start

        assert elapsed >= 0.04, "应确实等待了超时窗口，而非立即抛错"
        await holder.__aexit__(None, None, None)

    @pytest.mark.asyncio
    async def test_slot_releases_after_exit(self):
        """使用完毕后槽位释放，后续 acquire 立即可用。"""
        limiter = LLMLimiter(max_concurrent=1, wait_timeout=1.0)
        async with limiter.slot():
            pass
        # 释放后立即能再次获取
        async with limiter.slot():
            pass

    @pytest.mark.asyncio
    async def test_concurrency_cap_via_wallclock(self):
        """cap=1 时两个 slot 退化为串行（总耗时 ≈ 2× 单次）。"""
        limiter = LLMLimiter(max_concurrent=1, wait_timeout=5.0)

        async def worker() -> None:
            async with limiter.slot():
                await asyncio.sleep(0.05)

        start = time.monotonic()
        await asyncio.gather(worker(), worker())
        elapsed = time.monotonic() - start
        assert elapsed >= 0.09, f"应退化为串行 ≈100ms，实际 {elapsed * 1000:.1f}ms"

    def test_max_concurrent_clamped_to_one(self):
        """max_concurrent 至少为 1（防止配置 0/负数时信号量无效）。"""
        limiter = LLMLimiter(max_concurrent=0, wait_timeout=1.0)
        assert limiter.max_concurrent == 1


# =============================================================================
# agent_node 集成测试（饱和超时降级路径）
# =============================================================================


class _FakeModel:
    """模拟 LangChain Chat 模型：bind_tools 返回自身，ainvoke 记录是否被调用。"""

    def __init__(self) -> None:
        self.ainvoked = False

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.ainvoked = True
        return AIMessage(content="ok")


def _minimal_state() -> AgentState:
    """构造 agent_node 可执行的最小状态（不触发上下文压缩）。"""
    return {
        "user_message": "测试并发限流",
        "messages": [],
        "trace_iterations": [],
        "sse_events": [],
        "run_id": "test-run",
    }


class TestAgentNodeConcurrencyLimit:
    @pytest.mark.asyncio
    async def test_busy_returns_friendly_error_without_ainvoke(self):
        """LLM 并发饱和超时 → agent_node 返回 is_complete + LLM_BUSY 错误事件，不执行 ainvoke。"""
        from app.agent.graph import agent_node

        fake = _FakeModel()
        # 占满全局限流器：cap=1 且先持有一个槽位
        busy_limiter = LLMLimiter(max_concurrent=1, wait_timeout=0.05)
        holder = busy_limiter.slot()
        await holder.__aenter__()

        try:
            with (
                patch("app.agent.graph.build_chat_model", return_value=fake),
                patch("app.agent.graph.llm_limiter", busy_limiter),
            ):
                result = await agent_node(_minimal_state())
        finally:
            await holder.__aexit__(None, None, None)

        assert result["is_complete"] is True
        error_events = [
            e for e in result["sse_events"] if e.get("error_code") == "LLM_BUSY"
        ]
        assert error_events, f"应包含 LLM_BUSY 错误事件，实际: {result['sse_events']}"
        assert "繁忙" in result["final_answer"]
        assert fake.ainvoked is False, "饱和超时时不应执行 ainvoke"
        trace = result["trace_iterations"]
        assert trace and trace[-1]["status"] == "llm_concurrency_busy"

    @pytest.mark.asyncio
    async def test_free_limiter_calls_ainvoke(self):
        """限流器有空闲槽位时，ainvoke 正常执行（回归：限流不应阻断正常调用）。"""
        from app.agent.graph import agent_node

        fake = _FakeModel()
        free_limiter = LLMLimiter(max_concurrent=1, wait_timeout=5.0)

        with (
            patch("app.agent.graph.build_chat_model", return_value=fake),
            patch("app.agent.graph.llm_limiter", free_limiter),
        ):
            result = await agent_node(_minimal_state())

        assert fake.ainvoked is True, "有空闲槽位时应正常调用 ainvoke"
        # 无工具调用 → 走最终回答路径
        assert result.get("is_complete") is True
        assert "ok" in result["final_answer"]
