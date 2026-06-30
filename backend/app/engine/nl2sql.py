"""
NL2SQL 引擎——自然语言转 SQL。

核心流程（PRD §5.1）：
  1. 注入 Schema 上下文构建 Prompt
  2. 调用 LLM 生成 SQL
  3. 解析 LLM 响应提取 SQL
  4. 经 sql_auditor.audit() 安全校验
  5. 校验通过后返回 {sql, explanation}

依据 PRD §5.1 NL2SQL 流程、api-contract §1.2 SSE `sql` 事件。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
import structlog

from app.config import settings
from app.engine.sql_auditor import audit
from app.prompts.nl2sql import build_nl2sql_prompt

logger = structlog.get_logger(__name__)


# =============================================================================
# 常量
# =============================================================================

_ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
"""Anthropic Messages API 版本号"""

_DEFAULT_MAX_TOKENS = 1024
"""LLM 响应的最大 token 数"""

_LLM_TIMEOUT_SEC = 15
"""LLM 调用超时时间（s），依据 AC-6 验收标准"""

# =============================================================================
# 方言映射（用于 Prompt 中的方言声明）
# =============================================================================

_DIALECT_LABELS: dict[str, str] = {
    "mysql": "MySQL",
    "postgresql": "PostgreSQL",
    "oracle": "Oracle",
}


# =============================================================================
# LLM 调用
# =============================================================================


def _get_dialect_label(db_type: str) -> str:
    """获取数据库类型的显示标签。"""
    return _DIALECT_LABELS.get(db_type, db_type.upper())


async def _call_llm(
    system_prompt: str,
    user_prompt: str,
) -> str:
    """调用 Anthropic Messages API 获取 LLM 响应。

    使用 httpx 异步客户端直接调用 Anthropic API。
    （B-26 LLM Client 就绪后应重构为使用统一客户端）

    Args:
        system_prompt: 系统提示（角色定义 + 规则）。
        user_prompt: 用户提示（Schema + 查询）。

    Returns:
        LLM 响应的文本内容。

    Raises:
        TimeoutError: LLM 调用超时（>LLM_TIMEOUT_SEC）。
        RuntimeError: LLM API 返回错误或无法获取有效响应。
    """
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
            raise TimeoutError("NL2SQL generation timeout") from exc

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
# SQL 提取
# =============================================================================


def _extract_sql(text: str) -> tuple[str, str]:
    """从 LLM 响应中提取 SQL 和解释。

    支持的格式：
      - ```sql\n...\n``` markdown 代码块
      - 纯文本中的 SQL 语句（无代码块时的 fallback）

    Args:
        text: LLM 响应文本。

    Returns:
        (sql, explanation) 二元组。提取失败时 sql 为空字符串。
    """
    # AC-4: 从 markdown 代码块中提取 SQL（正则 ```sql ... ```）
    match = re.search(
        r"```sql\s*\n(.*?)```",
        text,
        re.DOTALL,
    )
    if match:
        sql = match.group(1).strip()
        # SQL 代码块之后的文本作为解释
        after_sql = text[match.end():].strip()
        return sql, after_sql

    # Fallback：如果 SQL 在 ``` 中但不含 sql 标记
    match = re.search(
        r"```\s*\n(.*?)```",
        text,
        re.DOTALL,
    )
    if match:
        sql = match.group(1).strip()
        after_sql = text[match.end():].strip()
        return sql, after_sql

    # 无代码块时，将整段文本作为 SQL 尝试
    # SAFETY: 后续 audit() 会拦截危险操作
    logger.info("NL2SQL 未找到SQL代码块", response_length=len(text))
    return "", text


# =============================================================================
# 核心公开函数
# =============================================================================


async def generate_sql(
    natural_language: str,
    schema_context: dict,
    connection_id: str,
    db_type: str = "mysql",
) -> dict[str, Any]:
    """将自然语言转换为 SQL 查询。

    执行流程（PRD §5.1）：
      Step 1: 构建 Prompt（Schema 上下文注入）
      Step 2: 调用 LLM 生成 SQL
      Step 3: 从响应中提取 SQL
      Step 4: sql_auditor.audit() 安全校验
      Step 5: 返回结构化结果

    SAFETY: 所有 LLM 生成的 SQL 执行前必须经过审计
    （AGENTS.md §安全与合规红线）。

    Args:
        natural_language: 用户的自然语言查询。
        schema_context: Schema 上下文（来自 B-10 元数据 API）。
        connection_id: 连接标识符（用于日志追踪）。
        db_type: 数据库类型（mysql / postgresql / oracle）。

    Returns:
        成功：{"sql": "...", "explanation": "...",
              "audit_status": "passed", "is_readonly": true}
        审计拦截：{"sql": "...", "explanation": "...",
                  "audit_status": "blocked",
                  "violations": [...]}
        失败：{"error": "NL2SQL generation timeout"}
              {"error": "NL2SQL generation failed",
               "detail": "..."}
    """
    try:
        # Step 1: 构建 Prompt
        # SAFETY: Prompt 中无实际数据行（AGENTS.md §数据隐私）
        dialect_label = _get_dialect_label(db_type)
        system_prompt, user_prompt = build_nl2sql_prompt(
            dialect=dialect_label,
            schema_context=schema_context,
            user_query=natural_language,
        )

        # Step 2: 调用 LLM
        logger.info("NL2SQL 开始生成", query=natural_language[:100],
                     connection_id=connection_id, db_type=db_type)
        start_time = time.monotonic()
        llm_response = await _call_llm(system_prompt, user_prompt)
        elapsed = time.monotonic() - start_time

        # Step 3: 提取 SQL
        sql, explanation = _extract_sql(llm_response)

        if not sql:
            logger.warning("NL2SQL 未生成SQL", connection_id=connection_id,
                           query=natural_language[:100], elapsed_ms=round(elapsed * 1000))
            return {
                "error": "NL2SQL generation failed",
                "detail": "LLM 响应中未生成有效的 SQL",
            }

        # Step 4: SQL 安全审计
        # SAFETY: 不跳过 SQL 审计（AGENTS.md §安全与合规红线）
        audit_result = audit(sql, db_type=db_type, user_role="standard")

        if not audit_result.passed:
            logger.warning("NL2SQL 审计拦截", connection_id=connection_id,
                           sql=sql[:200],
                           violations=[v.type for v in audit_result.violations])
            return {
                "sql": sql,
                "explanation": explanation,
                "audit_status": "blocked",
                "is_readonly": False,
                "violations": [
                    {"type": v.type, "message": v.message}
                    for v in audit_result.violations
                ],
            }

        # Step 5: 返回结果
        logger.info("NL2SQL 生成成功", connection_id=connection_id,
                     elapsed_ms=round(elapsed * 1000),
                     audit_status="passed", is_readonly=audit_result.is_readonly)
        return {
            "sql": sql,
            "explanation": explanation,
            "audit_status": "passed",
            "is_readonly": audit_result.is_readonly,
        }

    except TimeoutError:
        # AC-6: LLM 调用超时 15s
        logger.warning("NL2SQL 超时", connection_id=connection_id,
                       query=natural_language[:100])
        return {
            "error": "NL2SQL generation timeout",
            "detail": "LLM 调用超过 15 秒未响应，请重试或简化查询",
        }
    except Exception as exc:
        return {
            "error": "NL2SQL generation failed",
            "detail": f"{type(exc).__name__}: {exc}",
        }
