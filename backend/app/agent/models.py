"""
LangChain Chat 模型工厂。

根据 settings.LLM_PROVIDER 返回对应的 LangChain Chat 模型实例，
支持 Anthropic 官方 API、OpenAI 官方 API、以及阿里云百炼 DashScope 兼容模式。

用法：
    from app.agent.models import build_chat_model

    # 主力模型（复杂推理任务）
    model = build_chat_model()
    model_with_tools = model.bind_tools(tools)

变更（2026-07-04）：
    移除了 build_classifier_model()——意图分类已从图中移除，
    不再需要专用的分类模型。

变更（2026-07-09）：
    新增 CustomChatOpenAI 子类，保留 DeepSeek/Ollama 等第三方 API 返回的
    reasoning_content 字段到 AIMessageChunk.additional_kwargs 中，
    使 chat.py 能将推理内容作为独立的 SSE reasoning 事件流式传输。
"""

from __future__ import annotations

from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from app.config import settings


class CustomChatOpenAI(ChatOpenAI):
    """ChatOpenAI 子类 —— 保留 reasoning_content 到 additional_kwargs。

    langchain-openai 的 _convert_delta_to_message_chunk（模块级函数）仅读取
    delta.content 和 delta.tool_calls，丢弃了 delta.reasoning_content（DeepSeek/
    Ollama 等第三方 API 返回）。

    正确覆写点是 _convert_chunk_to_generation_chunk：这是 ChatOpenAI 的类方法，
    内部调用 _convert_delta_to_message_chunk 转换 delta → AIMessageChunk，
    之后从原始 chunk dict 中补注入 reasoning_content。
    """

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ):
        """在父类转换后，从原始 delta 中补充 reasoning_content。"""
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if generation_chunk is None:
            return None

        # 从原始 API chunk 中提取 reasoning_content（DeepSeek/Ollama 等返回）
        choices = chunk.get("choices", [])
        if choices:
            delta = choices[0].get("delta", {}) or {}
            reasoning = delta.get("reasoning_content", "")
            if (
                reasoning
                and isinstance(reasoning, str)
                and reasoning.strip()
                and hasattr(generation_chunk, "message")
                and generation_chunk.message
            ):
                # 注入到 AIMessageChunk.additional_kwargs
                generation_chunk.message.additional_kwargs = {
                    **generation_chunk.message.additional_kwargs,
                    "reasoning_content": reasoning,
                }

        return generation_chunk


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

    _common: dict[str, Any] = {
        "model": actual_model,
        "api_key": settings.LLM_API_KEY,
        "base_url": settings.LLM_API_URL or None,
        "max_tokens": max_tokens,
        "timeout": float(timeout),
    }

    # streaming=True 是必须的：LangGraph stream_mode="messages" 依赖它来
    # 拦截 LLM 内部流式调用并将 token chunk 暴露给 astream 迭代器。
    # ChatAnthropic 和 ChatOpenAI 都支持此参数。
    _common["streaming"] = True

    if provider == "anthropic":
        return ChatAnthropic(**_common)  # pyright: ignore[reportCallIssue]

    elif provider == "openai":
        _common["temperature"] = 0.0

        # 启用深度思考模式 → 使用 CustomChatOpenAI 保留 reasoning_content
        # 注：thinking 模式需通过模型名（如 deepseek-reasoner）或 API 端配置开启，
        # model_kwargs 在部分 langchain-openai 版本中不兼容 extra_body 传递
        if settings.ENABLE_REASONING:
            return CustomChatOpenAI(**_common)  # pyright: ignore[reportCallIssue]

        return ChatOpenAI(**_common)  # pyright: ignore[reportCallIssue]

    else:
        raise ValueError(
            f"不支持的 LLM_PROVIDER: {provider}，"
            f"仅支持 'anthropic' 或 'openai'"
        )

