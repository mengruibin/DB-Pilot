"""
SQL 安全审计引擎。

使用 sqlglot 解析 SQL AST，检测危险操作和多语句注入。
依据 PRD §8.1 SQL 安全五层模型 Layer 1-2：
  Layer 1: 默认只读
  Layer 2: 语法解析检测 DROP/ALTER/TRUNCATE/DELETE/UPDATE 等危险操作

依据 AGENTS.md §安全与合规红线：
  - 绝对禁止：DROP/ALTER/TRUNCATE/CREATE/GRANT/REVOKE
  - DELETE/UPDATE/INSERT/MERGE 仅 admin 角色可执行
  - 多语句直接拦截
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
import structlog
from sqlglot import exp

logger = structlog.get_logger(__name__)


# =============================================================================
# 数据类型
# =============================================================================

@dataclass
class Violation:
    """违规项：记录被拦截的 SQL 违规详情。"""
    type: str  # 违规类型，如 "DDL_STATEMENT"、"DML_WITHOUT_ADMIN"
    message: str  # 面向用户的违规说明
    location: str  # 违规位置（SQL 片段或行号）


@dataclass
class AuditResult:
    """审计结果。"""
    passed: bool  # 是否通过审计
    is_readonly: bool  # 是否为纯只读查询
    violations: list[Violation] = field(default_factory=list)
    """违规列表（passed=True 时为空）"""


# =============================================================================
# 危险操作定义
# =============================================================================

# SAFETY: 绝对禁止的语句类型（AGENTS.md §安全与合规红线）
# 这些操作无论用户角色如何，一律拦截
_FORBIDDEN_STATEMENTS: dict[str, str] = {
    "DROP": "禁止的 DROP 操作：删除数据库对象是危险操作",
    "ALTER": "禁止的 ALTER 操作：修改表结构是危险操作",
    "TRUNCATE": "禁止的 TRUNCATE 操作：清空表数据是不可恢复的操作",
    "CREATE": "禁止的 CREATE 操作：创建数据库对象需人工确认",
    "GRANT": "禁止的 GRANT 操作：权限变更需人工确认",
    "REVOKE": "禁止的 REVOKE 操作：权限变更需人工确认",
}

# 需要 admin 角色的语句类型（非 admin 时拦截）
_ADMIN_ONLY_STATEMENTS: dict[str, str] = {
    "DELETE": "DELETE 操作需要 admin 权限",
    "UPDATE": "UPDATE 操作需要 admin 权限",
    "INSERT": "INSERT 操作需要 admin 权限",
    "MERGE": "MERGE 操作需要 admin 权限",
}

# 数据导出风险模式
_DATA_EXPORT_FUNCTIONS = {
    "INTO OUTFILE": "SELECT ... INTO OUTFILE 存在数据导出风险",
}


# =============================================================================
# sqlglot dialect 映射
# =============================================================================

_DIALECT_MAP: dict[str, str] = {
    "mysql": "mysql",
    "postgresql": "postgres",
    "oracle": "oracle",
}


# =============================================================================
# 核心审计函数
# =============================================================================

def audit(
    sql: str,
    db_type: str = "mysql",
    user_role: str = "readonly",
) -> AuditResult:
    """审计 SQL 语句的安全性。

    解析 SQL 为 AST，检查危险操作。支持多语句检测。
    依据 PRD §8.1 Layer 1-2 安全模型。

    Args:
        sql: 要审计的 SQL 语句。
        db_type: 数据库类型（mysql / postgresql / oracle），影响解析方言。
        user_role: 用户角色（readonly / admin），影响权限判断；未显式传入时默认按只读兜底。

    Returns:
        AuditResult: 审计结果，含 passed 标志、violations 列表、is_readonly 标记。

    Raises:
        ValueError: SQL 语法无法解析时抛出。
    """
    violations: list[Violation] = []
    dialect = _DIALECT_MAP.get(db_type, "mysql")
    sql_upper = sql.strip().upper()

    # Step 1: 文本级危险模式检查（在 sqlglot 解析之前）
    # 这些模式 sqlglot 可能无法正确解析，提前拦截

    # 检查 SELECT ... INTO OUTFILE（AC-4）
    if _has_data_export_pattern(sql_upper):
        violations.append(Violation(
            type="DATA_EXPORT",
            message="SELECT ... INTO OUTFILE 存在数据导出风险，已被拦截",
            location=sql[:80],
        ))

    # Step 2: 多语句检测（AC-5）
    # 若 SQL 包含多条语句，直接拦截
    # SAFETY: 多语句可被用于绕过安全检测
    if not violations:  # 仅在未命中其他规则时检测
        multi_stmt_check = _check_multiple_statements(sql)
        if multi_stmt_check:
            violations.append(multi_stmt_check)

    # 如果已有违规，直接返回（不继续解析）
    if violations:
        return AuditResult(passed=False, is_readonly=False, violations=violations)

    # Step 3: sqlglot AST 解析
    try:
        parsed = sqlglot.parse(sql, dialect=dialect)
    except Exception as exc:
        raise ValueError(f"SQL 语法解析失败：{exc}") from exc

    if not parsed or all(tree is None for tree in parsed):
        raise ValueError("无法解析 SQL 语句，请检查语法")

    # 检查 AST 中的危险操作
    has_ddl = False
    has_dml_non_select = False

    for statement_ast in parsed:
        if statement_ast is None:
            continue

        stmt_type = _get_statement_type(statement_ast)
        violations_for_stmt = _check_statement_type(stmt_type, statement_ast, user_role)
        violations.extend(violations_for_stmt)

        # 标记是否为 DDL 或非 SELECT DML
        if stmt_type in _FORBIDDEN_STATEMENTS:
            has_ddl = True
        elif stmt_type in ("DELETE", "UPDATE"):
            has_dml_non_select = True

    # Step 4: 判定只读
    is_readonly = not has_ddl and not has_dml_non_select and len(violations) == 0

    result = AuditResult(
        passed=len(violations) == 0,
        is_readonly=is_readonly,
        violations=violations,
    )

    if result.passed:
        logger.info("SQL审计通过", sql=sql[:200], db_type=db_type, user_role=user_role)
    else:
        logger.warning("SQL审计拦截", sql=sql[:200], db_type=db_type,
                       user_role=user_role,
                       violations=[v.type for v in result.violations])

    return result


# =============================================================================
# 内部检测函数
# =============================================================================

def _check_multiple_statements(sql: str) -> Violation | None:
    """检测 SQL 是否包含多条语句。

    简单检测：去除字符串字面量后按分号分割统计。
    sqlglot 的 parse() 返回多语句列表，但我们在外层统一处理。
    """
    # sqlglot.parse 返回 list, 长度 > 1 说明有多条语句
    # 但 sqlglot 也会将纯 SELECT 作为单条处理
    # 使用简单启发式：统计顶层分号数量（不在引号内）
    in_single = False
    in_double = False
    stmt_count = 0

    for ch in sql:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == ";" and not in_single and not in_double:
            stmt_count += 1

    # 如果末尾没有分号，但内容非空，也算一条
    if sql.strip() and not sql.strip().endswith(";"):
        stmt_count += 1

    if stmt_count > 1:
        return Violation(
            type="MULTIPLE_STATEMENTS",
            message="检测到多条 SQL 语句，禁止批量执行（存在注入风险）",
            location=f"发现 {stmt_count} 条语句",
        )
    return None


def _get_statement_type(statement: exp.Expr) -> str:
    """从 sqlglot AST 节点中提取语句类型。"""
    # 映射 sqlglot 表达式类型到字符串类型
    if isinstance(statement, exp.Drop):
        return "DROP"
    if isinstance(statement, exp.Alter):
        return "ALTER"
    if isinstance(statement, exp.TruncateTable):
        return "TRUNCATE"
    if isinstance(statement, exp.Create):
        # CREATE TABLE 和 CREATE DATABASE 都是 DDL，但业务上 CREATE TABLE 可能在
        # 管理员确认后执行。安全审计中先统一归类为 CREATE
        return "CREATE"
    if isinstance(statement, exp.Grant):
        return "GRANT"
    if isinstance(statement, exp.Revoke):
        return "REVOKE"
    if isinstance(statement, exp.Delete):
        return "DELETE"
    if isinstance(statement, exp.Update):
        return "UPDATE"
    if isinstance(statement, exp.Insert):
        return "INSERT"
    if isinstance(statement, exp.Merge):
        return "MERGE"
    if isinstance(statement, exp.Select):
        return "SELECT"
    if isinstance(statement, exp.Union):
        return "UNION"
    if isinstance(statement, exp.Describe):
        return "EXPLAIN"
    if isinstance(statement, exp.SetItem):
        return "SET"
    # 默认返回类型名称
    return statement.key.upper() if hasattr(statement, "key") else type(statement).__name__.upper()


def _check_statement_type(
    stmt_type: str,
    statement: exp.Expr,
    user_role: str,
) -> list[Violation]:
    """根据语句类型和用户角色检查是否违规。"""
    found: list[Violation] = []

    # 检查绝对禁止的语句（AC-2）
    if stmt_type in _FORBIDDEN_STATEMENTS:
        msg = _FORBIDDEN_STATEMENTS[stmt_type]
        # 提取被操作的对象名（如表名）
        target = _extract_target_name(statement)
        location = f"{stmt_type} {target}" if target else stmt_type
        found.append(Violation(type=f"DDL_{stmt_type}", message=msg, location=location))

    # 检查需要 admin 角色的语句（AC-3）
    if stmt_type in _ADMIN_ONLY_STATEMENTS and user_role != "admin":
        msg = _ADMIN_ONLY_STATEMENTS[stmt_type]
        target = _extract_target_name(statement)
        location = f"{stmt_type} {target}" if target else stmt_type
        found.append(Violation(type=f"DML_{stmt_type}_NEEDS_ADMIN", message=msg, location=location))

    return found


def _has_data_export_pattern(sql_upper: str) -> bool:
    """文本级检测 SELECT ... INTO OUTFILE（AC-4）。"""
    return "INTO OUTFILE" in sql_upper or "INTO DUMPFILE" in sql_upper


def _extract_target_name(statement: exp.Expr) -> str:
    """从语句中提取操作目标名称（如表名、数据库名）。"""
    try:
        if isinstance(statement, (exp.Drop, exp.Alter, exp.TruncateTable)) and statement.this:
            return statement.this.sql() or ""
        if isinstance(statement, (exp.Delete, exp.Update)) and statement.this:
            return statement.this.sql() or ""
        if isinstance(statement, exp.Create) and statement.this:
            return statement.this.sql() or ""
    except Exception:
        pass
    return ""
