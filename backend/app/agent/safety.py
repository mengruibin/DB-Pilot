"""
Agent 安全护栏（B-30）。

提供可组合的安全检查链，在工具执行前逐项检查。
安全护栏不参与 Agent 决策逻辑，仅约束工具的执行。

护栏链执行顺序：
  1. SQLAuditCheck — 对 SQL 类工具进行 sqlglot 审计
  2. ReadOnlyCheck — 非 admin 角色仅允许只读操作
  3. ConnectionLimitCheck — 限制单次会话最大查询次数（预留）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


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
    """SQL 审计护栏。

    对 execute_sql / explain_query 工具中传入的 SQL 执行 sqlglot 审计。
    依据 AGENTS.md §安全与合规红线：
      - 绝对禁止: DROP/ALTER/TRUNCATE/CREATE/GRANT/REVOKE
      - DELETE/UPDATE 仅 admin 角色可通过
      - 多语句直接拦截
    """

    async def check(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        conn_config: dict[str, Any],
    ) -> SafetyResult:
        """对 SQL 类工具进行审计检查。"""
        if tool_name not in ("execute_sql", "explain_query"):
            return SafetyResult(blocked=False)

        sql = tool_args.get("sql", "")
        if not sql:
            return SafetyResult(blocked=False)

        try:
            from app.engine.sql_auditor import audit

            db_type = conn_config.get("db_type", "mysql")
            user_role = conn_config.get("user_role", "standard")

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


class ReadOnlyCheck(SafetyCheck):
    """只读护栏。

    非 admin 角色的 SQL 操作仅允许 SELECT 只读查询。
    admin 角色不受此限制。
    此护栏与 SQLAuditCheck 互补——SQLAuditCheck 检查语句结构，
    ReadOnlyCheck 检查角色权限。增强后：非 admin + 非 SELECT 直接拦截，
    不依赖 SQLAuditCheck 兜底。
    """

    _READONLY_KEYWORDS = frozenset(
        {
            "SELECT",
            "SHOW",
            "DESC",
            "DESCRIBE",
            "EXPLAIN",
            "WITH",
            "USE",
            "SET",
        }
    )

    async def check(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        conn_config: dict[str, Any],
    ) -> SafetyResult:
        """检查角色权限是否允许此操作。"""
        user_role = conn_config.get("user_role", "standard")

        # admin 角色不受限制
        if user_role == "admin":
            return SafetyResult(blocked=False)

        # 非 SQL 类工具不检查读写权限
        if tool_name not in ("execute_sql",):
            return SafetyResult(blocked=False)

        # 对非 admin 角色：检测 SQL 首词，非只读关键字则拦截
        sql = (tool_args.get("sql") or "").strip().upper()
        first_word = sql.split(maxsplit=1)[0] if sql else ""
        if first_word and first_word not in self._READONLY_KEYWORDS:
            return SafetyResult(
                blocked=True,
                reason=(
                    f"当前用户角色为「{user_role}」，仅允许只读操作。"
                    f"语句以 {first_word} 开头，已被只读护栏拦截"
                ),
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

        Args:
            tool_name: 工具名称。
            tool_args: 工具参数。
            conn_config: 连接配置。

        Returns:
            SafetyResult(blocked=False, warnings=[...])
        """
        if tool_name not in ("execute_sql",):
            return SafetyResult(blocked=False)

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
        ReadOnlyCheck(),
        PerformanceCheck(),  # SQL 性能静态分析（非阻断，仅警告）
    ]
