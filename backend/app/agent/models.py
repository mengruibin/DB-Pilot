"""
LangChain Chat 模型工厂（任务 1：替换自研 LLMClient）。

根据 settings.LLM_PROVIDER 返回对应的 LangChain Chat 模型实例，
支持 Anthropic 官方 API、OpenAI 官方 API、以及阿里云百炼 DashScope 兼容模式。

用法：
    from app.agent.models import build_chat_model, build_classifier_model

    # 主力模型（复杂推理任务）
    model = build_chat_model()
    model_with_tools = model.bind_tools(tools)

    # 分类模型（轻量快速）
    classifier = build_classifier_model()
"""

from __future__ import annotations

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from app.config import settings


def build_chat_model(
    *,
    model: str | None = None,
    max_tokens: int = 2048,
    timeout: int = 60,
) -> BaseChatModel:
    """构建主力 Chat 模型实例（复杂推理任务用）。

    根据 settings.LLM_PROVIDER 自动选择 Anthropic 或 OpenAI 后端。
    两者都通过 model.bind_tools() 支持工具调用。

    Args:
        model: 模型名称。None 表示使用 settings.LLM_MODEL。
        max_tokens: 最大输出 token 数（默认 2048）。
        timeout: 请求超时秒数（默认 60s）。

    Returns:
        BaseChatModel 实例（ChatAnthropic 或 ChatOpenAI）。

    Raises:
        ValueError: LLM_PROVIDER 配置值无效。
    """
    actual_model = model or settings.LLM_MODEL
    provider = settings.LLM_PROVIDER

    _common: dict = {
        "model": actual_model,
        "api_key": settings.LLM_API_KEY,
        "base_url": settings.LLM_API_URL or None,
        "max_tokens": max_tokens,
        "timeout": float(timeout),
    }

    if provider == "anthropic":
        return ChatAnthropic(**_common)  # pyright: ignore[reportCallIssue]

    elif provider == "openai":
        _common["temperature"] = 0.0
        return ChatOpenAI(**_common)  # pyright: ignore[reportCallIssue]

    else:
        raise ValueError(
            f"不支持的 LLM_PROVIDER: {provider}，"
            f"仅支持 'anthropic' 或 'openai'"
        )


def build_classifier_model() -> BaseChatModel:
    """构建意图分类用轻量 Chat 模型。

    使用 settings.LLM_CLASSIFIER_MODEL（默认 Haiku 级别），
    短超时、低 max_tokens，适合快速分类任务。

    Returns:
        BaseChatModel 实例。
    """
    return build_chat_model(
        model=settings.LLM_CLASSIFIER_MODEL,
        max_tokens=50,
        timeout=10,
    )
