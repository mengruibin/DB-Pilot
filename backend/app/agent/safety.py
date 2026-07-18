"""
Agent 安全护栏（B-30）。

提供可组合的安全检查链，在工具执行前逐项检查。
安全护栏不参与 Agent 决策逻辑，仅约束工具的执行。

护栏链执行顺序（按 extras 声明动态组装）：
  1. SQLAuditCheck — 对 SQL 类工具进行 sqlglot 审计，含只读角色权限拦截
  2. PerformanceCheck — SQL 静态文本分析（SELECT * / 缺 LIMIT / WHERE 函数），非阻断警告
  3. RowEstimationCheck — EXPLAIN 多维度安全评估（访问方式/行数/成本/额外操作），硬编码规则引擎
  4. ConnectionLimitCheck — 限制单次会话最大查询次数（预留）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.engine.explain_estimator import evaluate, extract_metrics

logger = structlog.get_logger(__name__)


@dataclass
class SafetyResult:
    """安全检查结果。

    Attributes:
        blocked: 是否被拦截。
        reason: 拦截原因（blocked=True 时必填）。
        warnings: 非阻断性警告信息。
    """

    blocked: bool
    reason: str | None = None
    warnings: list[str] = field(default_factory=list)


class SafetyCheck(ABC):
    """安全护栏抽象基类。

    每个子类实现 check() 方法，对工具调用进行特定维度的检查。
    护栏链中任意一个 blocked=True 即拦截，不执行工具。
    """

    @abstractmethod
    async def check(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        conn_config: dict[str, Any],
    ) -> SafetyResult:
        """检查工具调用是否安全。

        Args:
            tool_name: 工具名称。
            tool_args: 工具参数。
            conn_config: 连接配置。

        Returns:
            SafetyResult 包含 blocked 标志和拦截原因。
        """
        ...


class SQLAuditCheck(SafetyCheck):
    """SQL 审计护栏（含只读角色权限拦截）。

    对 execute_sql / explain_query 工具中传入的 SQL 执行 sqlglot 审计，
    同时负责非 admin 角色的只读权限拦截（通过 _ADMIN_ONLY_STATEMENTS）。
    不再需要独立的 ReadOnlyCheck 护栏。

    依据 AGENTS.md §安全与合规红线：
      - 绝对禁止: DROP/ALTER/TRUNCATE/CREATE/GRANT/REVOKE
      - DELETE/UPDATE/INSERT/MERGE 仅 admin 角色可通过
      - 多语句直接拦截
    """

    async def check(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        conn_config: dict[str, Any],
    ) -> SafetyResult:
        """对 SQL 类工具进行审计检查。

        由调用方（tool_node._resolve_checks）根据工具元数据决定是否传入此检查，
        因此不再需要按 tool_name 硬编码过滤。
        """
        sql = tool_args.get("sql", "")
        if not sql:
            return SafetyResult(blocked=False)

        try:
            from app.engine.sql_auditor import audit

            db_type = conn_config.get("db_type", "mysql")
            user_role = conn_config.get("user_role", "readonly")

            result = audit(sql, db_type, user_role=user_role)

            if not result.passed:
                violations_desc = [f"{v.type}: {v.message}" for v in result.violations]
                return SafetyResult(
                    blocked=True,
                    reason=f"SQL 审计未通过: {'; '.join(violations_desc)}",
                )
        except ImportError:
            # 审计模块不可用——放过（有风险，但避免阻断正常使用）
            return SafetyResult(
                blocked=False,
                warnings=["SQL 审计模块不可用——此查询未经安全审计"],
            )
        except Exception as exc:
            # 审计异常——放过但记录警告
            return SafetyResult(
                blocked=False,
                warnings=[f"SQL 审计异常（已放行）: {str(exc)[:100]}"],
            )

        return SafetyResult(blocked=False)


class ConnectionLimitCheck(SafetyCheck):
    """连接限额护栏（预留）。

    限制单次 Agent 会话的最大数据库查询次数。
    当前为预留实现，始终放行。
    Phase 4 将实现具体限制逻辑。
    """

    def __init__(self, max_queries: int = 50) -> None:
        """初始化连接限额护栏。

        Args:
            max_queries: 单次会话最大查询次数。
        """
        self._max_queries = max_queries
        self._query_count: int = 0

    async def check(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        conn_config: dict[str, Any],
    ) -> SafetyResult:
        """检查是否超出查询次数限制。"""
        if tool_name in ("execute_sql", "explain_query"):
            self._query_count += 1
            if self._query_count > self._max_queries:
                return SafetyResult(
                    blocked=True,
                    reason=f"单次会话查询次数已达上限（{self._max_queries}次）",
                )
        return SafetyResult(blocked=False)


# =============================================================================
# PerformanceCheck — SQL 性能静态分析护栏（非阻断，仅警告）
# =============================================================================


def _has_select_star(sql: str) -> bool:
    """检测 SQL 中是否使用了 SELECT *（全列扫描）。

    通过 sqlglot AST 解析判断 SELECT 语句的列是否为 *（Star 表达式）。

    Args:
        sql: 待检测的 SQL 语句。

    Returns:
        是否包含 SELECT *。
    """
    import re

    # 简单正则：匹配 SELECT *（不区分大小写）
    # 排除 SELECT *, COUNT(*), EXISTS(SELECT *) 等场景中的误报
    pattern = r"\bSELECT\s+\*\s"
    return bool(re.search(pattern, sql, re.IGNORECASE))


def _is_select_without_limit(sql: str) -> bool:
    """检测 SELECT 语句是否缺少 LIMIT 限制。

    仅检查 SELECT 语句（非 EXPLAIN/SHOW/DESCRIBE 等）。

    Args:
        sql: 待检测的 SQL 语句。

    Returns:
        是否为缺少 LIMIT 的 SELECT 查询。
    """
    sql_upper = sql.upper().strip()

    # 仅检查 SELECT 语句
    if not sql_upper.startswith("SELECT"):
        return False

    # 已有 LIMIT 子句则不告警
    return "LIMIT" not in sql_upper


def _detect_function_on_column(sql: str) -> str:
    """检测 WHERE 子句中是否对列使用了函数（阻止索引使用）。

    常见模式：WHERE YEAR(col) = 2024, WHERE UPPER(col) = 'X' 等。

    Args:
        sql: 待检测的 SQL 语句。

    Returns:
        检测到的函数名，无问题返回空字符串。
    """
    import re

    # 匹配 WHERE 子句中列上使用函数的模式
    # 例如: WHERE YEAR(create_time) = 2024, WHERE LOWER(name) = 'x'
    # 排除聚合函数场景（COUNT, SUM 等不在 WHERE 中对列使用函数的情况）
    patterns = [
        # WHERE/AND/OR 后跟 FUNC(column_name)
        r"(?:WHERE|AND|OR)\s+\w+\((\w+)\)",
    ]

    sql_upper = sql.upper()
    for p in patterns:
        match = re.search(p, sql_upper, re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


class PerformanceCheck(SafetyCheck):
    """SQL 性能检查护栏（仅警告，不阻断执行）。

    在 SQL 执行前进行静态分析，检测常见的性能反模式：
      - SELECT *（全列扫描，浪费 IO）
      - 无 LIMIT 的 SELECT（可能返回大量数据）
      - WHERE 列上使用函数（阻止索引使用）

    此护栏始终返回 blocked=False，仅通过 warnings 字段提供性能提示。
    Agent 在后续推理中可据此决定是否调用 explain_query 或改写 SQL。
    """

    async def check(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        conn_config: dict[str, Any],
    ) -> SafetyResult:
        """对 SQL 进行静态性能分析，返回非阻断性警告。

        由调用方（tool_node._resolve_checks）根据工具元数据决定是否传入此检查，
        因此不再需要按 tool_name 硬编码过滤。

        Args:
            tool_name: 工具名称。
            tool_args: 工具参数。
            conn_config: 连接配置。

        Returns:
            SafetyResult(blocked=False, warnings=[...])
        """
        sql = tool_args.get("sql", "")
        if not sql:
            return SafetyResult(blocked=False)

        warnings: list[str] = []

        # 检查 1: SELECT *（全列扫描）
        if _has_select_star(sql):
            warnings.append(
                "性能提示: 使用 SELECT * 会扫描所有列，建议明确列出需要的列名以减少 IO 开销"
            )

        # 检查 2: SELECT without LIMIT
        if _is_select_without_limit(sql):
            warnings.append(
                "性能提示: SELECT 查询缺少 LIMIT 限制，可能返回大量数据，建议添加合理的 LIMIT"
            )

        # 检查 3: WHERE 列上使用函数（阻止索引）
        func_col = _detect_function_on_column(sql)
        if func_col:
            warnings.append(
                "性能提示: WHERE 子句中对列使用了函数，这将阻止该列上的索引使用，建议改写查询条件"
            )

        if warnings:
            return SafetyResult(blocked=False, warnings=warnings)

        return SafetyResult(blocked=False)


# =============================================================================
# RowEstimationCheck — EXPLAIN 多维度安全评估
# =============================================================================

# 需要执行 EXPLAIN 检查的 SQL 语句前缀
_DML_PREFIXES = ("SELECT", "INSERT", "UPDATE", "DELETE", "WITH", "MERGE", "REPLACE")


def _is_dml_statement(sql: str) -> bool:
    """判断 SQL 语句是否需要进行 EXPLAIN 检查。

    仅对可能产生大量数据的 DML 语句进行检查，DDL/元数据操作跳过。
    使用简单前缀匹配，轻量快速（不依赖 sqlglot）。

    Args:
        sql: 待检查的 SQL 语句。

    Returns:
        True 表示需要执行 EXPLAIN 检查。
    """
    sql_upper = sql.strip().upper()
    return any(sql_upper.startswith(prefix) for prefix in _DML_PREFIXES)


class RowEstimationCheck(SafetyCheck):
    """EXPLAIN 多维度安全评估护栏（Phase 2）。

    在 SQL 执行前通过 EXPLAIN 提取多维度指标（访问方式、扫描行数、返回行数、
    查询成本、额外操作），用硬编码规则引擎评估，超阈值即阻断。
    不依赖 .env 配置，所有阈值在 explain_estimator.py 中 Code Review 管理。

    该检查仅对 execute_sql 工具生效（DDL 已被 SQLAuditCheck 拦截）。
    EXPLAIN 失败时降级放行，不阻断正常业务。
    """

    async def check(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        conn_config: dict[str, Any],
    ) -> SafetyResult:
        """执行 EXPLAIN 多维度安全评估。

        执行流程：
          1. 空 SQL / 非 DML → 跳过
          2. 创建临时适配器连接 → adapter.explain(sql)
          3. 提取标准化 ExplainMetrics → 规则引擎评估
          4. CRITICAL → 阻断（返回详细评估详情）
             WARNING → 警告但放行
             LOW → 放行

        Args:
            tool_name: 工具名称。
            tool_args: 工具参数。
            conn_config: 连接配置。

        Returns:
            SafetyResult — CRITICAL 时 blocked=True。
        """
        sql = tool_args.get("sql", "")
        if not sql:
            logger.debug("RowEstimationCheck 跳过：空 SQL", tool_name=tool_name)
            return SafetyResult(blocked=False)

        # 仅检查 DML 语句（DDL 已被 SQLAuditCheck 拦截）
        if not _is_dml_statement(sql):
            logger.debug(
                "RowEstimationCheck 跳过：非 DML 语句",
                tool_name=tool_name,
                sql_preview=sql[:80],
            )
            return SafetyResult(blocked=False)

        db_type = conn_config.get("db_type", "mysql")
        logger.info(
            "RowEstimationCheck 开始评估",
            tool_name=tool_name,
            sql_preview=sql[:200],
            db_type=db_type,
            database=conn_config.get("database", ""),
        )

        adapter: Any = None
        explain_raw: str = ""

        # ── Step 1: 创建临时适配器并执行 EXPLAIN ──
        try:
            adapter = await self._create_temp_adapter(conn_config)
            logger.debug(
                "RowEstimationCheck 临时适配器已创建",
                db_type=db_type,
                host=conn_config.get("host"),
                database=conn_config.get("database"),
            )
        except Exception as exc:
            logger.error(
                "RowEstimationCheck 适配器创建失败",
                error=str(exc)[:300],
                db_type=db_type,
                tool_name=tool_name,
                exc_info=True,
            )
            return SafetyResult(
                blocked=False,
                warnings=[f"EXPLAIN 适配器创建失败（{exc}），跳过安全评估，查询已放行"],
            )

        # ── Step 2: 执行 EXPLAIN ──
        try:
            explain_result = await adapter.explain(sql)  # type: ignore[union-attr]
            explain_raw = explain_result.get("explain_output", "")
            logger.debug(
                "RowEstimationCheck EXPLAIN 完成",
                explain_preview=explain_raw[:500],
                explain_format=explain_result.get("format", "unknown"),
            )
        except Exception as exc:
            logger.error(
                "RowEstimationCheck EXPLAIN 执行失败",
                error=str(exc)[:300],
                tool_name=tool_name,
                exc_info=True,
            )
            return SafetyResult(
                blocked=False,
                warnings=[f"EXPLAIN 执行失败（{exc}），跳过安全评估，查询已放行"],
            )
        finally:
            # 确保适配器断开
            if adapter is not None:
                try:  # noqa: SIM105
                    await adapter.disconnect()
                except Exception:
                    pass

        # ── Step 3: 提取标准化指标 ──
        try:
            metrics = extract_metrics(
                explain_output=explain_raw,
                db_type=db_type,
            )
        except Exception as exc:
            logger.error(
                "RowEstimationCheck 指标提取异常",
                error=str(exc)[:200],
                tool_name=tool_name,
                exc_info=True,
            )
            return SafetyResult(
                blocked=False,
                warnings=[f"EXPLAIN 指标提取异常（{exc}），跳过安全评估，查询已放行"],
            )

        if metrics is None:
            logger.warning(
                "RowEstimationCheck 指标提取失败：解析器返回 None",
                tool_name=tool_name,
                explain_raw_preview=explain_raw[:300],
                db_type=db_type,
            )
            return SafetyResult(
                blocked=False,
                warnings=["EXPLAIN 指标提取失败（解析器返回空），跳过安全评估，查询已放行"],
            )

        logger.info(
            "RowEstimationCheck 指标提取成功",
            access_pattern=metrics.access_pattern,
            estimated_rows_examined=metrics.estimated_rows_examined,
            estimated_rows_output=metrics.estimated_rows_output,
            row_width_bytes=metrics.row_width_bytes,
            query_cost=metrics.query_cost,
            extra_operations=metrics.extra_operations,
        )

        # ── Step 4: 规则引擎评估 ──
        decision = evaluate(metrics)

        logger.info(
            "RowEstimationCheck 规则引擎决策",
            allowed=decision.allowed,
            risk_level=decision.risk_level,
            reasons=decision.reasons,
        )

        if not decision.allowed:
            reason = (
                f"[EXPLAIN 安全评估] 查询被阻断\n"
                f"原因: {decision.reasons[0]}\n\n"
                f"评估详情:\n"
                f"  访问方式: {metrics.access_pattern}\n"
                f"  预估扫描行数: {metrics.estimated_rows_examined:,}\n"
                f"  预估返回行数: {metrics.estimated_rows_output:,}\n"
                f"  查询成本: {metrics.query_cost}\n"
                f"  额外操作: {', '.join(metrics.extra_operations) or '无'}\n\n"
                f"请改写 SQL 后重试。"
            )
            logger.warning(
                "RowEstimationCheck 阻断查询",
                tool_name=tool_name,
                reason=decision.reasons[0],
                sql_preview=sql[:200],
                access_pattern=metrics.access_pattern,
                estimated_rows=metrics.estimated_rows_examined,
            )
            return SafetyResult(blocked=True, reason=reason)

        if decision.risk_level == "WARNING":
            logger.info(
                "RowEstimationCheck 发出性能警告",
                tool_name=tool_name,
                warnings=decision.reasons,
                sql_preview=sql[:100],
            )
            return SafetyResult(
                blocked=False,
                warnings=[f"[EXPLAIN 评估] {r}" for r in decision.reasons],
            )

        logger.debug(
            "RowEstimationCheck 评估通过（LOW 风险）",
            tool_name=tool_name,
            sql_preview=sql[:100],
        )
        return SafetyResult(blocked=False)

    async def _create_temp_adapter(self, conn_config: dict) -> object:
        """从连接配置创建临时数据库适配器。

        Args:
            conn_config: 连接配置字典（含 host/port/user/password/db_type 等）。

        Returns:
            数据库适配器实例（已连接）。
        """
        from app.db.factory import AdapterFactory
        from app.models.schemas import ConnectionCreateRequest

        conn_id = conn_config.get("connection_id", "")

        config = ConnectionCreateRequest(
            name=f"est_{conn_id}",
            db_type=conn_config["db_type"],  # type: ignore[arg-type]
            host=conn_config["host"],
            port=conn_config["port"],
            database=conn_config["database"],
            user=conn_config["user"],
            password=conn_config.get("password", ""),
            ssl_enabled=conn_config.get("ssl_enabled", False),
            ssl_ca_cert=conn_config.get("ssl_ca_cert"),
        )

        adapter = AdapterFactory.create(conn_config["db_type"], config)
        await adapter.connect(config, user_role=conn_config.get("user_role", "readonly"))
        return adapter


# =============================================================================
# 护栏链执行
# =============================================================================


async def run_safety_checks(
    tool_name: str,
    tool_args: dict[str, Any],
    conn_config: dict[str, Any],
    checks: list[SafetyCheck] | None = None,
) -> SafetyResult:
    """依次执行安全护栏链。

    返回第一个 blocked=True 的结果，或全部通过时返回 blocked=False。

    Args:
        tool_name: 工具名称。
        tool_args: 工具参数。
        conn_config: 连接配置。
        checks: 护栏列表。None 表示使用默认护栏链。

    Returns:
        SafetyResult — 任意一个护栏 blocked 则拦截。
    """
    if checks is None:
        checks = _default_checks()

    for check in checks:
        result = await check.check(tool_name, tool_args, conn_config)
        if result.blocked:
            return result

    return SafetyResult(blocked=False)


def _default_checks() -> list[SafetyCheck]:
    """返回默认安全护栏链。"""
    return [
        SQLAuditCheck(),
        PerformanceCheck(),  # SQL 性能静态分析（非阻断，仅警告）
    ]
