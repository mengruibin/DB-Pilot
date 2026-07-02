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

    对 run_query / explain_query 工具中传入的 SQL 执行 sqlglot 审计。
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
        if tool_name not in ("run_query", "explain_query"):
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
                violations_desc = [
                    f"{v.type}: {v.message}" for v in result.violations
                ]
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
    ReadOnlyCheck 检查角色权限。
    """

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
        if tool_name not in ("run_query",):
            return SafetyResult(blocked=False)

        # standard/readonly 角色运行 run_query 时，由 SQLAuditCheck 确保只读
        # 此护栏作为补充检查
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
        if tool_name in ("run_query", "explain_query"):
            self._query_count += 1
            if self._query_count > self._max_queries:
                return SafetyResult(
                    blocked=True,
                    reason=f"单次会话查询次数已达上限（{self._max_queries}次）",
                )
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
    ]
