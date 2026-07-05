"""
NL2SQL 引擎——自然语言转 SQL。

核心流程（PRD §5.1）：
  1. 注入 Schema 上下文构建 Prompt
  2. 调用 LLM（通过 build_chat_model 标准 Chat 模型）生成 SQL
  3. 解析 LLM 响应提取 SQL
  4. 经 sql_auditor.audit() 安全校验
  5. 校验通过后返回 {sql, explanation}

依据 PRD §5.1 NL2SQL 流程、api-contract §1.2 SSE `sql` 事件。
"""

from __future__ import annotations

import re
import time
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.models import build_chat_model
from app.engine.sql_auditor import audit
from app.prompts.diagnosis import rule_based_analyze
from app.prompts.nl2sql import build_nl2sql_prompt

logger = structlog.get_logger(__name__)


# =============================================================================
# 常量
# =============================================================================

# LLM 调用通过统一的 LLMClient（B-26），不再在此处硬编码 API URL。

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


def _get_dialect_label(db_type: str) -> str:
    """获取数据库类型的显示标签。"""
    return _DIALECT_LABELS.get(db_type, db_type.upper())


# =============================================================================
# SQL 性能审计（NL2SQL 生成后自动 EXPLAIN 分析）
# =============================================================================


async def _performance_audit_sql(
    adapter: Any,
    sql: str,
    db_type: str = "mysql",
) -> list[str]:
    """对生成的 SQL 执行 EXPLAIN 并通过规则引擎分析性能问题。

    此函数是非阻断性的——所有异常静默捕获，
    性能审计失败绝不阻塞 SQL 执行。

    Args:
        adapter: 活跃的数据库适配器实例（已连接）。
        sql: 待审计的 SQL 语句。
        db_type: 数据库类型（mysql / postgresql / oracle）。

    Returns:
        性能警告列表，无问题时返回空列表。
    """
    try:
        # 检查适配器是否支持 EXPLAIN
        capabilities = adapter.get_capabilities()
        if not capabilities.supports_explain:
            logger.debug("数据库适配器不支持 EXPLAIN，跳过性能审计", db_type=db_type)
            return []

        # 执行 EXPLAIN 获取执行计划
        explain_result = await adapter.explain(sql)
        explain_output = (
            explain_result.get("explain_output", "")
            if isinstance(explain_result, dict)
            else str(explain_result)
        )

        if not explain_output:
            return []

        # 使用规则引擎分析 EXPLAIN 输出（无 LLM 调用，即时分析）
        analysis = rule_based_analyze(explain_output, sql)
        bottleneck = analysis.get("bottleneck", "")

        # 无瓶颈或分析异常时返回空列表
        if bottleneck in ("未发现明显瓶颈", "分析过程异常", ""):
            return []

        # 构建警告信息
        suggestion = analysis.get("suggestion", "")
        warnings = [
            f"性能警告: {bottleneck}。建议: {suggestion}",
        ]

        logger.info(
            "NL2SQL 性能审计发现瓶颈",
            sql_preview=sql[:100],
            bottleneck=bottleneck,
        )
        return warnings

    except Exception as exc:
        # 性能审计失败绝不阻塞主流程
        logger.debug(
            "NL2SQL 性能审计异常（已忽略）",
            error=str(exc)[:200],
        )
        return []




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
    conversation_history: str | None = None,
    previous_error: str | None = None,
    previous_sql: str | None = None,
    adapter: Any | None = None,
) -> dict[str, Any]:
    """将自然语言转换为 SQL 查询。

    执行流程（PRD §5.1）：
      Step 1: 构建 Prompt（Schema 上下文注入）
      Step 2: 调用 LLM 生成 SQL
      Step 3: 从响应中提取 SQL
      Step 4: sql_auditor.audit() 安全校验
      Step 5: （可选）EXPLAIN 性能审计（当提供 adapter 时）
      Step 6: 返回结构化结果

    SAFETY: 所有 LLM 生成的 SQL 执行前必须经过审计
    （AGENTS.md §安全与合规红线）。

    Args:
        natural_language: 用户的自然语言查询。
        schema_context: Schema 上下文（来自 B-10 元数据 API）。
        connection_id: 连接标识符（用于日志追踪）。
        db_type: 数据库类型（mysql / postgresql / oracle）。
        conversation_history: 可选的会话历史文本，注入 Prompt
            用于理解指代和省略上下文。
        previous_error: 上一次 SQL 执行的错误信息，用于重试时让 LLM
            根据错误修正 SQL。
        previous_sql: 上一次生成的 SQL，与 previous_error 配合使用。
        adapter: 可选的数据库适配器实例（已连接）。
            提供后将自动对生成的 SQL 执行 EXPLAIN 性能审计。

    Returns:
        成功：{"sql": "...", "explanation": "...",
              "audit_status": "passed", "is_readonly": true,
              "performance_warnings": [...]}
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
            conversation_history=conversation_history,
            previous_error=previous_error,
            previous_sql=previous_sql,
        )

        # Step 2: 调用 LLM（通过标准 LangChain Chat 模型）
        logger.info("NL2SQL 开始生成", query=natural_language[:100],
                     connection_id=connection_id, db_type=db_type)
        start_time = time.monotonic()
        model = build_chat_model(max_tokens=_DEFAULT_MAX_TOKENS, timeout=_LLM_TIMEOUT_SEC)
        response = await model.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ])
        llm_response = response.content if isinstance(response.content, str) else str(response.content)
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

        # Step 5: 性能审计（可选，当提供 adapter 时自动执行 EXPLAIN 分析）
        # 此步骤是非阻断性的——性能审计失败不影响 SQL 的正常使用
        performance_warnings: list[str] = []
        if adapter is not None:
            performance_warnings = await _performance_audit_sql(
                adapter=adapter,
                sql=sql,
                db_type=db_type,
            )

        # Step 6: 返回结果
        logger.info("NL2SQL 生成成功", connection_id=connection_id,
                     elapsed_ms=round(elapsed * 1000),
                     audit_status="passed", is_readonly=audit_result.is_readonly,
                     perf_warnings=len(performance_warnings))
        return {
            "sql": sql,
            "explanation": explanation,
            "audit_status": "passed",
            "is_readonly": audit_result.is_readonly,
            "performance_warnings": performance_warnings,
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
