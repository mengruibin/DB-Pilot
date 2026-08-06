"""
全局 LLM 调用并发限流器（llm-concurrency-limit-plan）。

背景：
  项目原本对 LLM 调用没有任何并发限制——并发 N 个请求 = N 个 LLM 调用同时打到
  provider（每个请求内 ReAct 循环每轮还新发一次调用），可能触发 provider 429 限流，
  token 成本也无上限。

设计要点：
  - 全后端唯一的 LLM 调用点在 graph.py agent_node 的 ainvoke（chat / troubleshoot /
    confirm 恢复流均经过它），因此只需在 LLMLimiter.slot() 这一个接入点限流，
    即可覆盖全部 LLM 流量。
  - 单进程 uvicorn 部署（无 Docker / 无 --workers），模块级共享信号量即全局硬上限。
  - 限的是「并发在途数」（同时进行的请求数），不是 RPM（每分钟请求数）。
  - 饱和时排队等待：超过 wait_timeout 秒仍未获得槽位则抛 LLMConcurrencyBusyError，
    由调用方转为「AI 服务繁忙」友好降级，而非无限挂起。

区别于 tool_node.py 的 Semaphore：那里是每次 safe_tools_node 调用新建的局部信号量，
只限制单轮 ReAct 迭代内并行工具数，不能跨会话/跨轮次限制全局并发。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.config import settings


class LLMConcurrencyBusyError(Exception):
    """LLM 并发饱和且排队超时——调用方应转为「AI 服务繁忙」友好提示。"""


class LLMLimiter:
    """全局 LLM 并发限流器（进程级单例，模块底部 llm_limiter）。"""

    def __init__(self, max_concurrent: int, wait_timeout: float) -> None:
        # 模块级共享信号量：跨会话/跨轮次全局生效
        self.max_concurrent = max(1, int(max_concurrent))
        self.wait_timeout = float(wait_timeout)
        self._semaphore = asyncio.Semaphore(self.max_concurrent)

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """获取一个 LLM 并发槽位，调用期间持有，退出自动释放。

        Raises:
            LLMConcurrencyBusyError: 排队等待超过 wait_timeout 秒仍未获得槽位。
        """
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(), timeout=self.wait_timeout
            )
        except TimeoutError as exc:
            raise LLMConcurrencyBusyError(
                f"LLM 并发饱和：排队等待超过 {self.wait_timeout}s 未获得槽位"
            ) from exc
        try:
            yield
        finally:
            self._semaphore.release()


# 模块级单例——导入时按配置初始化（参照 tool_node._MAX_CONCURRENT_TOOLS 模式）
llm_limiter = LLMLimiter(
    max_concurrent=settings.AGENT_MAX_CONCURRENT_LLM,
    wait_timeout=settings.AGENT_LLM_WAIT_TIMEOUT_SECONDS,
)
