"""
模型上下文窗口解析 + 压缩触发阈值推导（context-window-plan）。

窗口来源按业界主流三层解析（对齐 LiteLLM model_prices_and_context_window.json /
vLLM /v1/models）：
  1. 显式配置 LLM_CONTEXT_WINDOW > 0 → 直接采用（最高优先级，私有模型兜底）
  2. 自定义端点探测：LLM_API_URL 非空时 GET {base}/models 读取 max_model_len 等字段
  3. 内置模型注册表：已知模型名 → 上下文窗口（近似值，可被配置覆盖）
  4. 保守默认 64K（未知模型）

压缩触发阈值：AGENT_COMPACT_TRIGGER_TOKENS > 0 时显式覆盖；否则按
effective_compact_trigger(window) = clamp(window × 0.5, 4K, window − 12K) 推导。

0.5 倍率的依据（与业务确认，见 context-window-plan）：
  - 压缩检查在每次 LLM 调用之前（agent_node 先估算 est → est>trigger 先压缩 → 再调
    模型），模型实际看到的输入 ≤ trigger，预留空间只需覆盖输出(max_tokens=2048) +
    推理 + 估算误差，无需为单轮增量预留 → 0.4 偏保守；
  - DB-Pilot 的查询结果即工作记忆（摘要丢弃 ToolMessage），0.5×128K=64K 让典型诊断
    会话（15-40K）不压缩、结果保持逐字可用，只有真正 64K+ 的重会话才压缩；
  - 线上现状 60K/128K ≈ 0.47 已在生产验证，0.5 是其取整；估算器对 JSON/英文偏
    "高估字符"（安全方向），50% 余量下溢出概率可忽略。
"""

from __future__ import annotations

import httpx
import structlog

from app.config import settings

logger = structlog.get_logger(__name__)

# ── 触发阈值常量 ──
_COMPACT_TRIGGER_RATIO = 0.5          # 触发 = 窗口 × 0.5
_MIN_COMPACT_TRIGGER = 4_000          # 下限：超小窗口不立即压缩
_COMPACT_HEADROOM_RESERVE = 12_000    # 上限护栏：触发时至少留 12K 给输出+推理+误差

# ── 未知模型保守默认 ──
_DEFAULT_CONTEXT_WINDOW = 64_000

# ── 内置模型注册表（有序，按特异性优先匹配；近似值，可被 LLM_CONTEXT_WINDOW 覆盖）──
_MODEL_CONTEXT_WINDOW: list[tuple[str, int]] = [
    ("claude", 200_000),          # Anthropic Sonnet/Opus/Haiku 5 系
    ("gpt-4.1", 1_000_000),       # GPT-4.1 / 4.1-mini（1M）
    ("gpt-4o", 128_000),
    ("gpt-4", 128_000),
    ("gpt-3.5", 16_000),
    ("deepseek-reasoner", 64_000),
    ("deepseek-chat", 64_000),
    ("deepseek", 64_000),
    ("qwen", 128_000),            # 阿里百炼 qwen-max 等
    ("glm", 128_000),             # 智谱 GLM-4
]

# 模块级记忆化：探测只发生一次（进程内窗口不变）
_resolved_window: int | None = None


def effective_compact_trigger(window: int) -> int:
    """按模型上下文窗口推导压缩触发阈值（纯函数，可单测）。

    公式：clamp(window × 0.5, floor=4K, max=window − 12K)。
    floor 防止超小窗口一进来就压缩；max 保证触发时至少留 12K 给
    输出(2048) + 推理 + 估算误差。

    Args:
        window: 模型上下文窗口（token）。

    Returns:
        触发阈值 token 数。
    """
    return max(
        _MIN_COMPACT_TRIGGER,
        min(int(window * _COMPACT_TRIGGER_RATIO), window - _COMPACT_HEADROOM_RESERVE),
    )


async def _probe_context_window(base_url: str, model: str) -> int | None:
    """探测 OpenAI 兼容 /models 端点，读取模型上下文窗口（best-effort）。

    先归一化 URL（剥离 /chat/completions /messages /completions 后缀再拼 /models），
    按模型 id 匹配后依次尝试 max_model_len / context_length / max_context_length
    / n_ctx / context_window。任何异常静默返回 None（降级，不阻断启动）。
    遵守 AGENTS.md §禁止项：使用 httpx 异步客户端。

    Args:
        base_url: 自定义 LLM API URL。
        model: 当前模型名。

    Returns:
        探测到的上下文窗口 token，或 None（探测失败/无匹配）。
    """
    base = base_url.rstrip("/")
    for suffix in ("/chat/completions", "/messages", "/completions"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    models_url = base.rstrip("/") + "/models"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(models_url)
            resp.raise_for_status()
            data = resp.json()
        entries = data.get("data", []) if isinstance(data, dict) else []
        target = model.lower()
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("id", "").lower() != target:
                continue
            for key in (
                "max_model_len",
                "context_length",
                "max_context_length",
                "n_ctx",
                "context_window",
            ):
                val = entry.get(key)
                if isinstance(val, int) and val > 0:
                    return val
        return None
    except Exception:
        logger.debug("模型上下文窗口探测失败，降级", url=models_url, model=model)
        return None


async def resolve_context_window() -> int:
    """解析模型上下文窗口（显式配置 > 探测 > 注册表 > 默认），进程内只解析一次。

    Returns:
        上下文窗口 token 数。
    """
    global _resolved_window
    if _resolved_window is not None:
        return _resolved_window

    # 1) 显式配置（最高优先级，私有模型兜底）
    if settings.LLM_CONTEXT_WINDOW > 0:
        _resolved_window = settings.LLM_CONTEXT_WINDOW
        logger.info("模型上下文窗口：显式配置", context_window=_resolved_window)
        return _resolved_window

    # 2) 自定义端点探测（vLLM /models → max_model_len 等）
    if settings.LLM_API_URL:
        probed = await _probe_context_window(settings.LLM_API_URL, settings.LLM_MODEL)
        if probed:
            _resolved_window = probed
            logger.info("模型上下文窗口：端点探测", context_window=_resolved_window)
            return _resolved_window

    # 3) 内置注册表
    model_lower = settings.LLM_MODEL.lower()
    for key, value in _MODEL_CONTEXT_WINDOW:
        if key in model_lower:
            _resolved_window = value
            logger.info(
                "模型上下文窗口：注册表", model=settings.LLM_MODEL, context_window=_resolved_window
            )
            return _resolved_window

    # 4) 保守默认（未知模型）
    _resolved_window = _DEFAULT_CONTEXT_WINDOW
    logger.warning(
        "模型上下文窗口：未知模型使用默认值（建议配置 LLM_CONTEXT_WINDOW）",
        model=settings.LLM_MODEL,
        context_window=_resolved_window,
    )
    return _resolved_window


async def resolve_compact_trigger() -> int:
    """解析压缩触发阈值：显式 AGENT_COMPACT_TRIGGER_TOKENS > 0 优先，否则按窗口推导。

    Returns:
        触发阈值 token 数。
    """
    if settings.AGENT_COMPACT_TRIGGER_TOKENS > 0:
        return settings.AGENT_COMPACT_TRIGGER_TOKENS
    window = await resolve_context_window()
    return effective_compact_trigger(window)
