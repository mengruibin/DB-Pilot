"""
模型上下文窗口解析与压缩触发阈值推导测试（context-window-plan）。

覆盖：
  1. effective_compact_trigger — 0.5 比率 + floor 下限 + 12K 输出留白上限护栏
  2. resolve_context_window — 显式配置 > 端点探测 > 注册表 > 保守默认 的优先级
  3. resolve_compact_trigger — 显式 AGENT_COMPACT_TRIGGER_TOKENS 优先，否则按窗口推导
  4. _probe_context_window — URL 归一化、上下文字段提取、异常降级 None
"""

from __future__ import annotations

import pytest

from app.agent import context_window as cw
from app.config import settings


@pytest.fixture(autouse=True)
def _reset_resolution() -> None:
    """每个测试重置模块级记忆化窗口（探测只发生一次）。"""
    cw._resolved_window = None
    yield
    cw._resolved_window = None


# =============================================================================
# TestEffectiveCompactTrigger — 触发阈值公式
# =============================================================================


class TestEffectiveCompactTrigger:
    """clamp(window × 0.5, floor=4K, max=window−12K) 纯函数测试。"""

    def test_128k_half(self) -> None:
        assert cw.effective_compact_trigger(128_000) == 64_000

    def test_32k_half(self) -> None:
        assert cw.effective_compact_trigger(32_000) == 16_000

    def test_200k_half(self) -> None:
        assert cw.effective_compact_trigger(200_000) == 100_000

    def test_small_window_floor(self) -> None:
        # 16K：0.5×16K=8K，但 cap=16K−12K=4K → floor 4K 生效
        assert cw.effective_compact_trigger(16_000) == 4_000
        assert cw.effective_compact_trigger(8_000) == 4_000

    def test_headroom_cap_binds(self) -> None:
        # 20K：0.5×20K=10K > 20K−12K=8K → cap 8K（输出留白护栏）
        assert cw.effective_compact_trigger(20_000) == 8_000

    def test_zero_window_floor(self) -> None:
        # 防御：窗口 0 时 floor 兜底
        assert cw.effective_compact_trigger(0) == 4_000


# =============================================================================
# TestResolveContextWindow — 窗口解析优先级
# =============================================================================


class TestResolveContextWindow:
    """显式配置 > 探测 > 注册表 > 默认。"""

    @pytest.mark.asyncio
    async def test_explicit_config_wins(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "LLM_CONTEXT_WINDOW", 32_768)
        monkeypatch.setattr(settings, "LLM_API_URL", "")
        monkeypatch.setattr(settings, "LLM_MODEL", "claude-sonnet-5")
        assert await cw.resolve_context_window() == 32_768

    @pytest.mark.asyncio
    async def test_probe_wins_over_registry(self, monkeypatch) -> None:
        # 自定义端点探测命中 → 即使模型名不在注册表也取探测值
        monkeypatch.setattr(settings, "LLM_CONTEXT_WINDOW", 0)
        monkeypatch.setattr(settings, "LLM_API_URL", "http://vllm:8000/v1")
        monkeypatch.setattr(settings, "LLM_MODEL", "my-private-model")

        async def _fake_probe(*args, **kwargs) -> int:
            return 8_192

        monkeypatch.setattr(cw, "_probe_context_window", _fake_probe)
        assert await cw.resolve_context_window() == 8_192

    @pytest.mark.asyncio
    async def test_probe_none_falls_to_registry(self, monkeypatch) -> None:
        # 探测失败（如 DeepSeek 不暴露 /models）→ 落到注册表
        monkeypatch.setattr(settings, "LLM_CONTEXT_WINDOW", 0)
        monkeypatch.setattr(settings, "LLM_API_URL", "https://api.deepseek.com/v1")
        monkeypatch.setattr(settings, "LLM_MODEL", "deepseek-chat")

        async def _fake_probe(*args, **kwargs):
            return None

        monkeypatch.setattr(cw, "_probe_context_window", _fake_probe)
        assert await cw.resolve_context_window() == 64_000

    @pytest.mark.asyncio
    async def test_registry_hit(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "LLM_CONTEXT_WINDOW", 0)
        monkeypatch.setattr(settings, "LLM_API_URL", "")
        monkeypatch.setattr(settings, "LLM_MODEL", "deepseek-chat")
        assert await cw.resolve_context_window() == 64_000

    @pytest.mark.asyncio
    async def test_registry_most_specific_first(self, monkeypatch) -> None:
        # gpt-4.1 须在 gpt-4 之前匹配（注册表有序）
        monkeypatch.setattr(settings, "LLM_CONTEXT_WINDOW", 0)
        monkeypatch.setattr(settings, "LLM_API_URL", "")
        monkeypatch.setattr(settings, "LLM_MODEL", "gpt-4.1-mini")
        assert await cw.resolve_context_window() == 1_000_000

    @pytest.mark.asyncio
    async def test_claude_family_200k(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "LLM_CONTEXT_WINDOW", 0)
        monkeypatch.setattr(settings, "LLM_API_URL", "")
        monkeypatch.setattr(settings, "LLM_MODEL", "claude-sonnet-5")
        assert await cw.resolve_context_window() == 200_000

    @pytest.mark.asyncio
    async def test_unknown_model_default(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "LLM_CONTEXT_WINDOW", 0)
        monkeypatch.setattr(settings, "LLM_API_URL", "")
        monkeypatch.setattr(settings, "LLM_MODEL", "some-private-model")
        assert await cw.resolve_context_window() == cw._DEFAULT_CONTEXT_WINDOW

    @pytest.mark.asyncio
    async def test_memoized_single_resolution(self, monkeypatch) -> None:
        """记忆化：首次解析后再次调用不重复探测。"""
        monkeypatch.setattr(settings, "LLM_CONTEXT_WINDOW", 0)
        monkeypatch.setattr(settings, "LLM_API_URL", "http://vllm:8000/v1")
        monkeypatch.setattr(settings, "LLM_MODEL", "my-model")
        calls = {"n": 0}

        async def _fake_probe(*args, **kwargs) -> int:
            calls["n"] += 1
            return 8_192

        monkeypatch.setattr(cw, "_probe_context_window", _fake_probe)
        assert await cw.resolve_context_window() == 8_192
        assert await cw.resolve_context_window() == 8_192
        assert calls["n"] == 1, "记忆化后不应重复探测"


# =============================================================================
# TestResolveCompactTrigger — 触发阈值解析
# =============================================================================


class TestResolveCompactTrigger:
    """显式 AGENT_COMPACT_TRIGGER_TOKENS > 0 优先，否则按窗口推导。"""

    @pytest.mark.asyncio
    async def test_explicit_trigger_wins(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "AGENT_COMPACT_TRIGGER_TOKENS", 12_345)
        assert await cw.resolve_compact_trigger() == 12_345

    @pytest.mark.asyncio
    async def test_auto_derive_from_window(self, monkeypatch) -> None:
        # claude-sonnet-5 → 注册表 200K → 触发 = 100K
        monkeypatch.setattr(settings, "AGENT_COMPACT_TRIGGER_TOKENS", 0)
        monkeypatch.setattr(settings, "LLM_CONTEXT_WINDOW", 0)
        monkeypatch.setattr(settings, "LLM_API_URL", "")
        monkeypatch.setattr(settings, "LLM_MODEL", "claude-sonnet-5")
        assert await cw.resolve_compact_trigger() == 100_000


# =============================================================================
# TestProbeContextWindow — /models 端点探测
# =============================================================================


class TestProbeContextWindow:
    """URL 归一化、上下文字段提取、异常降级。"""

    def _install_fake_client(self, monkeypatch, resp_data: dict, *, raise_on_get: bool = False):
        """用假 httpx.AsyncClient 替换真实客户端，返回捕获的请求 URL。"""
        captured: dict = {}

        class _FakeResp:
            def raise_for_status(self) -> None:
                if raise_on_get:
                    raise RuntimeError("boom")

            def json(self) -> dict:
                return resp_data

        class _FakeClient:
            def __init__(self, **kwargs) -> None:
                pass

            async def __aenter__(self) -> _FakeClient:
                return self

            async def __aexit__(self, *args) -> bool:
                return False

            async def get(self, url: str) -> _FakeResp:
                captured["url"] = url
                return _FakeResp()

        monkeypatch.setattr(cw.httpx, "AsyncClient", _FakeClient)
        return captured

    @pytest.mark.asyncio
    async def test_probe_url_normalization_and_max_model_len(self, monkeypatch) -> None:
        # base_url 含 /chat/completions → 归一化到 /v1/models，读取 max_model_len
        resp_data = {"data": [{"id": "my-model", "max_model_len": 32_768}]}
        captured = self._install_fake_client(monkeypatch, resp_data)
        result = await cw._probe_context_window(
            "http://host:8000/v1/chat/completions", "my-model"
        )
        assert result == 32_768
        assert captured["url"] == "http://host:8000/v1/models"

    @pytest.mark.asyncio
    async def test_probe_alternate_field(self, monkeypatch) -> None:
        # 无 max_model_len 时回退 context_length
        resp_data = {"data": [{"id": "my-model", "context_length": 16_384}]}
        self._install_fake_client(monkeypatch, resp_data)
        result = await cw._probe_context_window("http://host:8000/v1", "my-model")
        assert result == 16_384

    @pytest.mark.asyncio
    async def test_probe_messages_suffix(self, monkeypatch) -> None:
        # Anthropic /v1/messages → 归一化到 /v1/models，读取 context_window
        resp_data = {"data": [{"id": "my-model", "context_window": 200_000}]}
        captured = self._install_fake_client(monkeypatch, resp_data)
        result = await cw._probe_context_window(
            "https://api.anthropic.com/v1/messages", "my-model"
        )
        assert result == 200_000
        assert captured["url"] == "https://api.anthropic.com/v1/models"

    @pytest.mark.asyncio
    async def test_probe_no_matching_model(self, monkeypatch) -> None:
        resp_data = {"data": [{"id": "other-model", "max_model_len": 32_768}]}
        self._install_fake_client(monkeypatch, resp_data)
        result = await cw._probe_context_window("http://host:8000/v1", "my-model")
        assert result is None

    @pytest.mark.asyncio
    async def test_probe_exception_degrades_to_none(self, monkeypatch) -> None:
        self._install_fake_client(monkeypatch, {}, raise_on_get=True)
        result = await cw._probe_context_window("http://host:8000/v1", "my-model")
        assert result is None
