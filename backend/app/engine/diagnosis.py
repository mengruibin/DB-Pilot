"""
诊断引擎——LLM 解析 EXPLAIN 输出 + 规则降级。

核心流程（PRD §5.2）：
  1. 构建 EXPLAIN 分析 Prompt
  2. 调用 LLM 分析执行计划
  3. 解析 LLM 响应为结构化瓶颈信息
  4. LLM 不可用时降级到规则引擎
  5. 返回 {bottleneck, suggestion, estimated_improvement, is_destructive}

依据 PRD §5.2、api-contract §1.3 POST /api/connections/{id}/explain。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
import structlog

from app.config import settings
from app.prompts.diagnosis import build_diagnosis_prompt, rule_based_analyze

logger = structlog.get_logger(__name__)


# =============================================================================
# 常量
# =============================================================================

_ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"

_LLM_TIMEOUT_SEC = 15
"""LLM 调用超时时间（与 B-17 NL2SQL 一致）"""

_DEFAULT_MAX_TOKENS = 1024


# =============================================================================
# LLM 调用（与 B-17 保持一致模式，B-26 就绪后可重构为统一客户端）
# =============================================================================


async def _call_llm(
    system_prompt: str,
    user_prompt: str,
) -> str:
    """调用 Anthropic Messages API 分析 EXPLAIN 输出。"""
    headers = {
        "x-api-key": settings.LLM_API_KEY,
        "anthropic-version": _ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    payload = {
        "model": settings.LLM_MODEL,
        "max_tokens": _DEFAULT_MAX_TOKENS,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": user_prompt},
        ],
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(_LLM_TIMEOUT_SEC)) as client:
        try:
            response = await client.post(
                _ANTHROPIC_API_URL,
                headers=headers,
                content=json.dumps(payload),
            )
        except httpx.TimeoutException as exc:
            raise TimeoutError("Diagnosis LLM analysis timeout") from exc

    if response.status_code != 200:
        raise RuntimeError(
            f"LLM API 返回错误（{response.status_code}）：{response.text[:200]}"
        )

    data = response.json()
    content_blocks = data.get("content", [])
    for block in content_blocks:
        if block.get("type") == "text":
            return block.get("text", "")

    raise RuntimeError("LLM 响应中未找到文本内容")


# =============================================================================
# LLM 响应解析
# =============================================================================


def _extract_json(text: str) -> dict[str, Any] | None:
    """从 LLM 响应文本中提取 JSON 对象。

    支持格式：
      - ```json\n{...}\n```
      - ```\n{...}\n```
      - 纯文本中的 {......} 对象

    Args:
        text: LLM 响应的文本。

    Returns:
        解析成功的 dict，或 None 表示解析失败。
    """
    # 尝试从 markdown 代码块中提取 JSON
    match = re.search(
        r"```(?:json)?\s*\n(\{.*?\})\s*\n```",
        text,
        re.DOTALL,
    )
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Fallback：尝试从纯文本中提取 JSON 对象
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return None


def _normalize_analysis(result: dict[str, Any]) -> dict[str, Any]:
    """规范 LLM 分析结果的字段。"""
    bottleneck = str(result.get("bottleneck", "") or "")
    suggestion = str(result.get("suggestion", "") or "")
    estimated = str(result.get("estimated_improvement", "") or "")
    is_destructive = bool(result.get("is_destructive", False))

    # AC-2: suggestion 中 is_destructive=True 时附加 # SUGGESTION 标记
    if is_destructive and "# SUGGESTION" not in suggestion:
        suggestion = f"{suggestion}\n# SUGGESTION — 此建议会修改数据库结构或数据"

    return {
        "bottleneck": bottleneck,
        "suggestion": suggestion,
        "estimated_improvement": estimated,
        "is_destructive": is_destructive,
    }


# =============================================================================
# 核心公开函数
# =============================================================================


async def analyze_explain(
    explain_output: str,
    sql: str,
    db_type: str = "mysql",
) -> dict[str, Any]:
    """分析 EXPLAIN 执行计划，识别性能瓶颈并提供优化建议。

    执行流程（PRD §5.2）：
      1. 构建 Prompt 并调用 LLM 分析
      2. 解析 LLM 响应为结构化瓶颈信息
      3. LLM 不可用时降级到规则引擎（AC-4）
      4. 对 destructive 建议添加 # SUGGESTION 标记（AC-2）

    SAFETY: 分析过程中不执行任何 SQL，仅读取执行计划文本。

    Args:
        explain_output: EXPLAIN 执行计划输出文本。
        sql: 被分析的原始 SQL 语句。
        db_type: 数据库类型（mysql / postgresql / oracle），影响方言和规则匹配。

    Returns:
        成功：{"bottleneck": "...", "suggestion": "...",
              "estimated_improvement": "...", "is_destructive": bool}
        降级（LLM 不可用）：同上，suggestion 含"请人工分析"说明。
        失败：{"error": "...", "detail": "..."}
    """
    try:
        # Step 1: 构建 Prompt 并调用 LLM
        logger.info("诊断 LLM 开始分析", sql=sql[:100], db_type=db_type)
        start_time = time.monotonic()
        system_prompt, user_prompt = build_diagnosis_prompt(
            explain_output=explain_output,
            sql=sql,
            db_type=db_type,
        )

        llm_response = await _call_llm(system_prompt, user_prompt)
        elapsed = time.monotonic() - start_time

        # Step 2: 解析 LLM 响应
        parsed = _extract_json(llm_response)
        if parsed:
            bottleneck = parsed.get("bottleneck", "")[:100]
            logger.info("诊断 LLM 分析成功", sql=sql[:100],
                        bottleneck=bottleneck,
                        elapsed_ms=round(elapsed * 1000))
            return _normalize_analysis(parsed)

        # Step 3: LLM 返回了有效文本但未包含可解析 JSON → 降级到规则引擎
        logger.warning("诊断 LLM 响应格式异常，降级到规则引擎",
                       sql=sql[:100], db_type=db_type)
        fallback = rule_based_analyze(explain_output, sql)
        fallback["bottleneck"] = (
            f"{fallback['bottleneck']}（LLM 响应格式异常，使用规则分析）"
        )
        return fallback

    except (TimeoutError, RuntimeError):
        # AC-4: LLM 不可用时降级到规则引擎
        logger.warning("诊断 LLM 不可用，降级到规则引擎",
                       sql=sql[:100], db_type=db_type)
        fallback = rule_based_analyze(explain_output, sql)
        fallback["bottleneck"] = (
            f"{fallback['bottleneck']}（LLM 服务暂不可用，使用规则分析）"
        )
        return fallback
    except Exception as exc:
        # 兜底：即使规则引擎也不可用，返回原始信息
        logger.error("诊断引擎异常", sql=sql[:100], error=str(exc)[:200])
        return {
            "bottleneck": "分析过程异常",
            "suggestion": f"请人工分析以下 EXPLAIN 输出：\n{explain_output[:300]}",
            "estimated_improvement": "N/A",
            "is_destructive": False,
            "error": f"{type(exc).__name__}: {exc}",
            "detail": "诊断引擎异常，已返回原始 EXPLAIN 输出",
        }
