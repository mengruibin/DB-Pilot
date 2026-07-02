"""
统一 LLM 调用客户端。

支持 Anthropic（默认）和 OpenAI 双 provider，非流式/流式调用。
统一返回格式，内置 token 用量统计和结构化日志。

依据 AGENTS.md §LLM 集成：
  - Claude 默认，OpenAI 兼容架构
  - 模型名从 config.py 读取，禁止硬编码
  - 简单分类任务使用 Haiku，复杂任务使用 Opus
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

import httpx
import structlog

from app.config import settings

logger = structlog.get_logger(__name__)


# =============================================================================
# 数据类型
# =============================================================================


@dataclass
class LLMUsage:
    """Token 用量统计。

    Attributes:
        input_tokens: 输入（Prompt）token 数。
        output_tokens: 输出（Completion）token 数。
    """
    input_tokens: int
    output_tokens: int


@dataclass
class ToolCall:
    """LLM 返回的工具调用请求（B-30 Agent 工具调用）。

    Attributes:
        id: 工具调用唯一标识（Anthropic: tool_use.id, OpenAI: tool_call.id）。
        name: 工具名称。
        arguments: 工具参数 dict。
    """
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """LLM 非流式响应。

    Attributes:
        text: 响应文本（tool_calls 非空时可能为空字符串）。
        tool_calls: LLM 请求的工具调用列表（None 表示纯文本响应）。
        usage: Token 用量（API 返回时非 None）。
    """
    text: str
    tool_calls: list[ToolCall] | None = None
    usage: LLMUsage | None = None


# =============================================================================
# 常量
# =============================================================================

_ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"

_OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

_LLM_TIMEOUT_SEC = 30
"""LLM 调用超时（秒）。"""


# =============================================================================
# URL 路径补全工具
# =============================================================================


def _ensure_chat_path(url: str) -> str:
    """确保 OpenAI 兼容 API URL 包含 /chat/completions 路径。

    当使用自定义 LLM_API_URL（如阿里云百炼兼容模式）时，
    用户可能只配置基础路径（.../v1），需要补全聊天补全端点。
    若 URL 已包含完整路径（如默认 _OPENAI_API_URL），则不做修改。
    """
    url = url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url = url + "/chat/completions"
    return url


def _format_messages_for_log(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """格式化消息列表用于 DEBUG 日志（截断内容，避免日志过大）。

    返回每条消息的 role 和 content 前 500 字符预览。
    _mask_sensitive_keys 处理器会自动掩盖 "token"/"secret" 等敏感字段。
    """
    result: list[dict[str, str]] = []
    for msg in messages:
        content = msg.get("content", "")
        role = msg.get("role", "unknown")
        if isinstance(content, str):
            result.append({"role": role, "content_preview": content[:500]})
        else:
            result.append({"role": role, "content_type": type(content).__name__})
    return result


# =============================================================================
# LLMClient
# =============================================================================


class LLMClient:
    """统一 LLM 调用客户端（B-26）。

    根据 settings.LLM_PROVIDER 自动选择 Anthropic 或 OpenAI 后端。
    支持非流式 chat() 和流式 chat_stream() 两种模式。

    用法：
        client = LLMClient()
        # 非流式
        resp = await client.chat([{"role": "user", "content": "Hello"}])
        # 流式
        async for chunk in client.chat_stream([{"role": "user", "content": "Hi"}]):
            if chunk["type"] == "text_delta":
                print(chunk["text"])
    """

    def __init__(
        self,
        provider: Literal["anthropic", "openai"] | None = None,
    ) -> None:
        """初始化客户端。

        Args:
            provider: LLM 提供方。None 表示从 settings.LLM_PROVIDER 读取。
        """
        self._provider: Literal["anthropic", "openai"] = (
            provider or settings.LLM_PROVIDER
        )

    # ================== 非流式 ==================

    async def chat(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 1024,
        timeout: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """非流式 LLM 调用（AC-1）。

        Args:
            messages: 对话消息列表 [{"role": "user", "content": "..."}]。
            system: 系统提示词（仅 Anthropic 支持 system 参数）。
            model: 模型名。None 表示用 settings.LLM_MODEL。
            max_tokens: 最大输出 token 数。
            timeout: 超时秒数。None 表示使用默认值 30s。
            tools: 可选，工具定义的 JSON Schema 列表（B-30 Agent 工具调用）。
                格式：[{"name": "...", "description": "...", "input_schema": {...}}]

        Returns:
            LLMResponse 包含响应文本、可能的工具调用、和用量统计。

        Raises:
            TimeoutError: 调用超时。
            RuntimeError: API 返回错误。
        """
        actual_model = model or settings.LLM_MODEL
        start = time.monotonic()

        # AC-8：入口日志
        prompt_len = sum(len(m.get("content", "")) for m in messages)
        tools_count = len(tools) if tools else 0
        logger.info("LLM 调用开始", provider=self._provider,
                     model=actual_model, prompt_chars=prompt_len,
                     tools_count=tools_count)

        try:
            if self._provider == "anthropic":
                text, tool_calls, usage = await self._call_anthropic(
                    messages, system, actual_model, max_tokens, timeout, tools,
                )
            else:
                text, tool_calls, usage = await self._call_openai(
                    messages, system, actual_model, max_tokens, timeout, tools,
                )

            elapsed_ms = int((time.monotonic() - start) * 1000)

            # AC-8：出口日志
            logger.info("LLM 调用完成", provider=self._provider,
                        model=actual_model, prompt_chars=prompt_len,
                        response_chars=len(text),
                        tool_calls_count=len(tool_calls) if tool_calls else 0,
                        input_tokens=usage.input_tokens if usage else None,
                        output_tokens=usage.output_tokens if usage else None,
                        elapsed_ms=elapsed_ms)

            return LLMResponse(text=text, tool_calls=tool_calls, usage=usage)

        except TimeoutError:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.error("LLM 调用超时", provider=self._provider,
                         model=actual_model, elapsed_ms=elapsed_ms)
            raise
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            # AC-8：异常日志
            logger.error("LLM 调用异常", provider=self._provider,
                         model=actual_model, error=str(exc)[:200],
                         elapsed_ms=elapsed_ms)
            raise

    # ================== 流式 ==================

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 1024,
        timeout: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式 LLM 调用（AC-1, AC-3）。

        每次 yield：
          {"type": "text_delta", "text": "..."} — 文本片段
          {"type": "usage", "input_tokens": N, "output_tokens": N} — 最终用量

        Args:
            messages: 对话消息列表。
            system: 系统提示词。
            model: 模型名。None 表示用 settings.LLM_MODEL。
            max_tokens: 最大输出 token 数。
            timeout: 超时秒数。None 表示使用默认值 30s。

        Yields:
            标准化流式 chunk。
        """
        actual_model = model or settings.LLM_MODEL
        start = time.monotonic()

        prompt_len = sum(len(m.get("content", "")) for m in messages)
        logger.info("LLM 流式调用开始", provider=self._provider,
                     model=actual_model, prompt_chars=prompt_len)

        collected = []
        usage: LLMUsage | None = None

        try:
            if self._provider == "anthropic":
                async for chunk in self._stream_anthropic(
                    messages, system, actual_model, max_tokens, timeout,
                ):
                    if chunk["type"] == "text_delta":
                        collected.append(chunk["text"])
                        yield chunk
                    elif chunk["type"] == "usage":
                        usage = LLMUsage(
                            input_tokens=chunk["input_tokens"],
                            output_tokens=chunk["output_tokens"],
                        )
                        yield chunk
            else:
                async for chunk in self._stream_openai(
                    messages, system, actual_model, max_tokens, timeout,
                ):
                    if chunk["type"] == "text_delta":
                        collected.append(chunk["text"])
                        yield chunk
                    elif chunk["type"] == "usage":
                        usage = LLMUsage(
                            input_tokens=chunk["input_tokens"],
                            output_tokens=chunk["output_tokens"],
                        )
                        yield chunk

            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.info("LLM 流式调用完成", provider=self._provider,
                        model=actual_model, prompt_chars=prompt_len,
                        response_chars=len("".join(collected)),
                        input_tokens=usage.input_tokens if usage else None,
                        output_tokens=usage.output_tokens if usage else None,
                        elapsed_ms=elapsed_ms)

        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.error("LLM 流式调用异常", provider=self._provider,
                         model=actual_model, error=str(exc)[:200],
                         elapsed_ms=elapsed_ms)
            raise

    # ================== Anthropic 实现 ==================

    async def _call_anthropic(
        self,
        messages: list[dict[str, Any]],
        system: str | None,
        model: str,
        max_tokens: int,
        timeout: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[str, list[ToolCall] | None, LLMUsage | None]:
        """Anthropic Messages API 非流式调用（B-30：支持工具调用）。"""
        actual_timeout = timeout or _LLM_TIMEOUT_SEC
        api_url = settings.LLM_API_URL or _ANTHROPIC_API_URL
        headers = {
            "x-api-key": settings.LLM_API_KEY,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            payload["system"] = system
        if tools:
            # Anthropic 工具格式：{"name": ..., "description": ..., "input_schema": ...}
            payload["tools"] = tools

        # B-26：请求报文 DEBUG 日志
        logger.debug("LLM 请求报文",
                     provider=self._provider, model=model,
                     messages_count=len(payload["messages"]),
                     messages=_format_messages_for_log(payload["messages"]),
                     system_preview=system[:500] if system else None,
                     tools_count=len(tools) if tools else 0,
                     api_url=api_url)

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(actual_timeout),
        ) as client:
            try:
                response = await client.post(
                    api_url,
                    headers=headers,
                    content=json.dumps(payload),
                )
            except httpx.TimeoutException as exc:
                raise TimeoutError("Anthropic API 调用超时") from exc

        if response.status_code != 200:
            raise RuntimeError(
                f"Anthropic API 错误（{response.status_code}）："
                f"{response.text[:200]}"
            )

        data = response.json()
        text = _extract_anthropic_text(data)
        tool_calls = _extract_anthropic_tool_calls(data)
        usage = _parse_anthropic_usage(data)

        # B-26：响应报文 DEBUG 日志
        logger.debug("LLM 响应报文",
                     provider=self._provider, model=model,
                     response_preview=text[:1000] if text else None,
                     tool_calls_count=len(tool_calls) if tool_calls else 0,
                     input_tokens=usage.input_tokens if usage else None,
                     output_tokens=usage.output_tokens if usage else None)

        return text, tool_calls, usage

    async def _stream_anthropic(
        self,
        messages: list[dict[str, Any]],
        system: str | None,
        model: str,
        max_tokens: int,
        timeout: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Anthropic Messages API 流式调用。"""
        actual_timeout = timeout or _LLM_TIMEOUT_SEC
        api_url = settings.LLM_API_URL or _ANTHROPIC_API_URL
        headers = {
            "x-api-key": settings.LLM_API_KEY,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
            "stream": True,
        }
        if system:
            payload["system"] = system

        # B-26：流式请求报文 DEBUG 日志
        logger.debug("LLM 流式请求报文",
                     provider=self._provider, model=model,
                     messages_count=len(payload["messages"]),
                     messages=_format_messages_for_log(payload["messages"]),
                     system_preview=system[:500] if system else None,
                     api_url=api_url)

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(actual_timeout),
        ) as client, client.stream(
            "POST", api_url,
            headers=headers,
            content=json.dumps(payload),
        ) as stream:
            if stream.status_code != 200:
                error_text = await stream.aread()
                raise RuntimeError(
                    f"Anthropic API 错误（{stream.status_code}）："
                    f"{error_text[:200]}"
                )

            input_tokens = 0
            output_tokens = 0

            async for line in stream.aiter_lines():
                if not line.startswith("data: "):
                    continue
                event_data = line[6:].strip()
                if event_data == "[DONE]":
                    break

                try:
                    event = json.loads(event_data)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type", "")

                if event_type == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            yield {"type": "text_delta", "text": text}

                elif event_type == "message_delta":
                    usage_data = event.get("usage", {})
                    output_tokens = usage_data.get("output_tokens", 0)

                elif event_type == "message_start":
                    msg = event.get("message", {})
                    usage_data = msg.get("usage", {})
                    input_tokens = usage_data.get("input_tokens", 0)
                    output_tokens = usage_data.get("output_tokens", 0)

            yield {
                "type": "usage",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            }

    # ================== OpenAI 实现 ==================

    async def _call_openai(
        self,
        messages: list[dict[str, Any]],
        system: str | None,
        model: str,
        max_tokens: int,
        timeout: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[str, list[ToolCall] | None, LLMUsage | None]:
        """OpenAI Chat Completions API 非流式调用（B-30：支持工具调用）。"""
        actual_timeout = timeout or _LLM_TIMEOUT_SEC
        api_url = _ensure_chat_path(settings.LLM_API_URL or _OPENAI_API_URL)
        headers = {
            "authorization": f"Bearer {settings.LLM_API_KEY}",
            "content-type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            payload["messages"].insert(0, {"role": "system", "content": system})
        if tools:
            # OpenAI 工具格式需要从 Anthropic 格式转换：
            #   {"name":...,"description":...,"input_schema":{...}}
            # → {"type":"function","function":{"name":...,"description":...,"parameters":{...}}}
            payload["tools"] = _convert_tools_to_openai(tools)

        # B-26：请求报文 DEBUG 日志（LOG_LEVEL=DEBUG 时输出）
        logger.debug("LLM 请求报文",
                     provider=self._provider, model=model,
                     messages_count=len(payload["messages"]),
                     messages=_format_messages_for_log(payload["messages"]),
                     tools_count=len(tools) if tools else 0,
                     api_url=api_url)

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(actual_timeout),
        ) as client:
            try:
                response = await client.post(
                    api_url,
                    headers=headers,
                    content=json.dumps(payload),
                )
            except httpx.TimeoutException as exc:
                raise TimeoutError("OpenAI API 调用超时") from exc

        if response.status_code != 200:
            raise RuntimeError(
                f"OpenAI API 错误（{response.status_code}）："
                f"{response.text[:200]}"
            )

        data = response.json()
        text = _extract_openai_text(data)
        tool_calls = _extract_openai_tool_calls(data)
        usage = _parse_openai_usage(data)

        # B-26：响应报文 DEBUG 日志
        logger.debug("LLM 响应报文",
                     provider=self._provider, model=model,
                     response_preview=text[:1000] if text else None,
                     tool_calls_count=len(tool_calls) if tool_calls else 0,
                     input_tokens=usage.input_tokens if usage else None,
                     output_tokens=usage.output_tokens if usage else None)

        # 诊断：HTTP 200 但无内容时，记录原始响应体
        if not text and not tool_calls:
            err_code = data.get("code") or data.get("error", {}).get("code", "")
            err_msg = data.get("message") or data.get("error", {}).get("message", "")
            if err_code or err_msg:
                logger.warning("OpenAI 返回业务错误",
                               provider=self._provider, model=model,
                               error_code=err_code, error_message=err_msg,
                               raw_response=json.dumps(data, ensure_ascii=False)[:500])
            else:
                logger.warning("OpenAI 响应体无内容",
                               provider=self._provider, model=model,
                               raw_response=json.dumps(data, ensure_ascii=False)[:500],
                               response_keys=list(data.keys()))

        return text, tool_calls, usage

    async def _stream_openai(
        self,
        messages: list[dict[str, Any]],
        system: str | None,
        model: str,
        max_tokens: int,
        timeout: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """OpenAI Chat Completions API 流式调用。"""
        actual_timeout = timeout or _LLM_TIMEOUT_SEC
        api_url = _ensure_chat_path(settings.LLM_API_URL or _OPENAI_API_URL)
        headers = {
            "authorization": f"Bearer {settings.LLM_API_KEY}",
            "content-type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
            "stream": True,
        }
        if system:
            payload["messages"].insert(0, {"role": "system", "content": system})

        # B-26：流式请求报文 DEBUG 日志
        logger.debug("LLM 流式请求报文",
                     provider=self._provider, model=model,
                     messages_count=len(payload["messages"]),
                     messages=_format_messages_for_log(payload["messages"]),
                     api_url=api_url)

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(actual_timeout),
        ) as client, client.stream(
            "POST", api_url,
            headers=headers,
            content=json.dumps(payload),
        ) as stream:
            if stream.status_code != 200:
                error_text = await stream.aread()
                raise RuntimeError(
                    f"OpenAI API 错误（{stream.status_code}）："
                    f"{error_text[:200]}"
                )

            input_tokens = 0
            output_tokens = 0

            async for line in stream.aiter_lines():
                if not line.startswith("data: "):
                    continue
                chunk_data = line[6:].strip()
                if chunk_data == "[DONE]":
                    break

                try:
                    chunk = json.loads(chunk_data)
                except json.JSONDecodeError:
                    continue

                choices = chunk.get("choices", [])
                for choice in choices:
                    delta = choice.get("delta", {})
                    text = delta.get("content", "")
                    if text:
                        yield {"type": "text_delta", "text": text}

                # OpenAI 流式的 usage 在最后一个 chunk 中
                usage_info = chunk.get("usage")
                if usage_info:
                    input_tokens = usage_info.get("prompt_tokens", 0)
                    output_tokens = usage_info.get(
                        "completion_tokens", 0,
                    )

            yield {
                "type": "usage",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            }


# =============================================================================
# 响应解析工具函数
# =============================================================================


def _extract_anthropic_text(data: dict[str, Any]) -> str:
    """从 Anthropic Messages API 响应中提取文本。"""
    content_blocks = data.get("content", [])
    for block in content_blocks:
        if block.get("type") == "text":
            return block.get("text", "")
    return ""


def _parse_anthropic_usage(data: dict[str, Any]) -> LLMUsage | None:
    """从 Anthropic Messages API 响应中解析 token 用量。"""
    usage = data.get("usage")
    if usage:
        return LLMUsage(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )
    return None


def _extract_openai_text(data: dict[str, Any]) -> str:
    """从 OpenAI Chat Completions API 响应中提取文本。

    支持两种格式：
      1. 标准 OpenAI：{"choices": [{"message": {"content": "..."}}]}
      2. 阿里云百炼 DashScope 兼容模式：{"text": "...", "finish_reason": "..."}
    """
    # 格式 1：标准 OpenAI Chat Completions
    choices = data.get("choices", [])
    if choices:
        return choices[0].get("message", {}).get("content", "")
    # 格式 2：阿里云百炼 DashScope 兼容模式
    text = data.get("text", "")
    if text:
        return text
    return ""


def _parse_openai_usage(data: dict[str, Any]) -> LLMUsage | None:
    """从 OpenAI Chat Completions API 响应中解析 token 用量。

    支持两种格式：
      1. 标准 OpenAI：{"usage": {"prompt_tokens": N, "completion_tokens": N}}
      2. 阿里云百炼 DashScope：{"usage": {"input_tokens": N, "output_tokens": N}}
    """
    usage = data.get("usage")
    if usage:
        return LLMUsage(
            input_tokens=usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0) or usage.get("output_tokens", 0),
        )
    return None


# =============================================================================
# Tool Calling 解析（B-30：Agent 工具调用）
# =============================================================================


def _extract_anthropic_tool_calls(data: dict[str, Any]) -> list[ToolCall] | None:
    """从 Anthropic Messages API 响应中提取工具调用（tool_use content blocks）。

    Anthropic 在 content 数组中返回 type="tool_use" 的 block，
    每个包含 id / name / input 字段。
    """
    content_blocks = data.get("content", [])
    tool_calls: list[ToolCall] = []
    for block in content_blocks:
        if block.get("type") == "tool_use":
            tool_calls.append(ToolCall(
                id=block.get("id", ""),
                name=block.get("name", ""),
                arguments=block.get("input", {}),
            ))
    return tool_calls if tool_calls else None


def _extract_openai_tool_calls(data: dict[str, Any]) -> list[ToolCall] | None:
    """从 OpenAI Chat Completions API 响应中提取工具调用。

    OpenAI 在 choices[0].message.tool_calls 中返回工具调用列表，
    每个含 id / function.name / function.arguments（JSON 字符串）。
    """
    choices = data.get("choices", [])
    if not choices:
        return None
    message = choices[0].get("message", {})
    raw_tool_calls = message.get("tool_calls", [])
    if not raw_tool_calls:
        return None
    tool_calls: list[ToolCall] = []
    for tc in raw_tool_calls:
        func = tc.get("function", {})
        arguments_str = func.get("arguments", "{}")
        try:
            arguments = json.loads(arguments_str)
        except (json.JSONDecodeError, TypeError):
            arguments = {}
        tool_calls.append(ToolCall(
            id=tc.get("id", ""),
            name=func.get("name", ""),
            arguments=arguments,
        ))
    return tool_calls if tool_calls else None


def _convert_tools_to_openai(
    tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """将 Anthropic 格式的工具定义转换为 OpenAI 格式。

    Anthropic: {"name": "...", "description": "...", "input_schema": {...}}
    OpenAI: {"type": "function", "function": {"name": "...", "description": "...",
              "parameters": {...}}}
    """
    openai_tools: list[dict[str, Any]] = []
    for tool in tools:
        openai_tools.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {}),
            },
        })
    return openai_tools
