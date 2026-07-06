"""
数据库 SQL 执行错误解析器。

解析各数据库（MySQL/PostgreSQL/Oracle）的原生错误消息，
转换为结构化、带纠错指导的 ParsedError，帮助 LLM 精准定位问题并自我纠正。

错误码参考：
  MySQL:    https://dev.mysql.com/doc/refman/8.0/en/error-reference.html
  PostgreSQL: https://www.postgresql.org/docs/current/errcodes-appendix.html
  Oracle:   https://docs.oracle.com/en/database/oracle/oracle-database/19/errmg/
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class ErrorType(StrEnum):
    """SQL 执行错误类型枚举。"""
    TABLE_NOT_FOUND = "TABLE_NOT_FOUND"        # 表/视图不存在
    COLUMN_NOT_FOUND = "COLUMN_NOT_FOUND"      # 字段不存在
    SYNTAX_ERROR = "SYNTAX_ERROR"              # SQL 语法错误
    PERMISSION_DENIED = "PERMISSION_DENIED"    # 权限不足
    UNKNOWN = "UNKNOWN"                        # 未识别的错误


@dataclass
class ParsedError:
    """解析后的结构化错误信息。

    Attributes:
        error_type: 错误类型枚举。
        detail: 面向 LLM 的中文错误描述，包含具体的表名/字段名。
        suggestion: 纠错指引，引导 LLM 使用正确的工具修正错误。
        original: 原始异常消息（保留供日志排查）。
    """
    error_type: ErrorType
    detail: str
    suggestion: str
    original: str


# =============================================================================
# 各数据库错误匹配规则
# =============================================================================

def _parse_mysql_error(error_msg: str) -> tuple[ErrorType, str, str] | None:
    """解析 MySQL 错误消息。

    MySQL 错误格式示例：
      (1146, "Table 'db.users' doesn't exist")
      (1054, "Unknown column 'email' in 'field list'")
      (1064, "You have an error in your SQL syntax...")
      (1142, "SELECT command denied to user...")
    """
    # 提取错误码
    code_match = re.search(r'\((\d{4}),\s*"', error_msg)
    code = int(code_match.group(1)) if code_match else 0

    # 1146: 表不存在
    if code == 1146:
        table_match = re.search(r"Table ['\"](.+?)['\"]", error_msg)
        table_name = table_match.group(1) if table_match else "未知"
        return (
            ErrorType.TABLE_NOT_FOUND,
            f"表 '{table_name}' 在当前数据库中不存在",
            "请使用 list_tables 查看所有可用的表名，然后用 describe_table 确认表结构",
        )

    # 1054: 字段不存在
    if code == 1054:
        col_match = re.search(r"Unknown column ['\"](.+?)['\"]", error_msg)
        col_name = col_match.group(1) if col_match else "未知"
        return (
            ErrorType.COLUMN_NOT_FOUND,
            f"字段 '{col_name}' 在表中不存在",
            f"请使用 describe_table 查看相关表的完整字段列表，确认 '{col_name}' 的正确字段名",
        )

    # 1064: 语法错误
    if code == 1064:
        return (
            ErrorType.SYNTAX_ERROR,
            "SQL 语法错误，数据库无法解析该语句",
            "请检查 SQL 语法是否正确，确认关键字、引号、括号是否匹配",
        )

    # 1142: 权限不足
    if code == 1142:
        return (
            ErrorType.PERMISSION_DENIED,
            "当前用户对此表没有操作权限",
            "请联系管理员授予相应权限，或使用有权限的表进行查询",
        )

    return None


def _parse_postgresql_error(error_msg: str) -> tuple[ErrorType, str, str] | None:
    """解析 PostgreSQL 错误消息。

    PostgreSQL 错误格式示例：
      relation "users" does not exist
      column "email" does not exist
      syntax error at or near "FROM"
      permission denied for table users
    """
    # 表/视图不存在
    relation_match = re.search(r'relation ["](.+?)["] does not exist', error_msg)
    if relation_match:
        table_name = relation_match.group(1)
        return (
            ErrorType.TABLE_NOT_FOUND,
            f"表/视图 '{table_name}' 在当前数据库中不存在",
            "请使用 list_tables 查看所有可用的表名，然后用 describe_table 确认表结构",
        )

    # 字段不存在
    col_match = re.search(r'column ["](.+?)["] does not exist', error_msg)
    if col_match:
        col_name = col_match.group(1)
        return (
            ErrorType.COLUMN_NOT_FOUND,
            f"字段 '{col_name}' 在表中不存在",
            f"请使用 describe_table 查看相关表的完整字段列表，确认 '{col_name}' 的正确字段名",
        )

    # 语法错误
    if "syntax error" in error_msg.lower():
        return (
            ErrorType.SYNTAX_ERROR,
            "SQL 语法错误，数据库无法解析该语句",
            "请检查 SQL 语法是否正确，确认关键字、引号、括号是否匹配",
        )

    # 权限不足
    if "permission denied" in error_msg.lower():
        return (
            ErrorType.PERMISSION_DENIED,
            "当前用户对此对象没有操作权限",
            "请联系管理员授予相应权限",
        )

    return None


def _parse_oracle_error(error_msg: str) -> tuple[ErrorType, str, str] | None:
    """解析 Oracle 错误消息。

    Oracle 错误格式示例：
      ORA-00942: table or view does not exist
      ORA-00904: "EMAIL": invalid identifier
      ORA-00900: invalid SQL statement
      ORA-01031: insufficient privileges
    """
    # 提取 ORA 错误码
    code_match = re.search(r'ORA-(\d{5})', error_msg)
    code = code_match.group(1) if code_match else ""

    # ORA-00942: 表或视图不存在
    if code == "00942":
        return (
            ErrorType.TABLE_NOT_FOUND,
            "表或视图在当前数据库中不存在",
            "请使用 list_tables 查看所有可用的表名，然后用 describe_table 确认表结构",
        )

    # ORA-00904: 标识符无效（通常是字段名错误）
    if code == "00904":
        id_match = re.search(r'"(.+?)"', error_msg)
        col_name = id_match.group(1) if id_match else "未知"
        return (
            ErrorType.COLUMN_NOT_FOUND,
            f"字段 '{col_name}' 在表中不存在或标识符无效",
            f"请使用 describe_table 查看相关表的完整字段列表，确认 '{col_name}' 的正确字段名",
        )

    # ORA-00900: 无效 SQL 语句
    if code == "00900":
        return (
            ErrorType.SYNTAX_ERROR,
            "SQL 语句无效，数据库无法解析",
            "请检查 SQL 语法是否正确",
        )

    # ORA-01031: 权限不足
    if code == "01031":
        return (
            ErrorType.PERMISSION_DENIED,
            "当前用户权限不足",
            "请联系管理员授予相应权限",
        )

    return None


# =============================================================================
# 解析器注册表
# =============================================================================

# 解析器函数签名：接收错误消息字符串，返回 (ErrorType, detail, suggestion) 或 None
_ParserFn = Callable[[str], tuple[ErrorType, str, str] | None]

_PARSERS: dict[str, _ParserFn] = {
    "mysql": _parse_mysql_error,
    "postgresql": _parse_postgresql_error,
    "oracle": _parse_oracle_error,
}


# =============================================================================
# 公共 API
# =============================================================================

def parse_db_error(
    exc: Exception,
    db_type: str = "mysql",
    sql: str = "",
) -> ParsedError:
    """解析数据库执行异常，返回结构化的错误信息。

    根据 db_type 选择对应的错误码匹配规则，
    从异常消息中提取表名、字段名等关键信息。
    无法识别时返回 ErrorType.UNKNOWN。

    Args:
        exc: 数据库执行时抛出的异常（通常为 ValueError，由适配器包装）。
        db_type: 数据库类型（mysql / postgresql / oracle）。
        sql: 出错的原始 SQL 语句（可选，用于日志和上下文）。

    Returns:
        ParsedError — 包含 error_type、detail、suggestion、original。
    """
    error_msg = str(exc)

    # 选择对应数据库的解析器
    parser = _PARSERS.get(db_type)
    if parser is not None:
        result = parser(error_msg)
        if result is not None:
            error_type, detail, suggestion = result
            return ParsedError(
                error_type=error_type,
                detail=detail,
                suggestion=suggestion,
                original=error_msg[:500],
            )

    # 兜底：未识别的错误，保留原始信息
    return ParsedError(
        error_type=ErrorType.UNKNOWN,
        detail=f"数据库执行出错: {error_msg[:300]}",
        suggestion=(
            "请分析错误信息，确认 SQL 是否正确，"
            "必要时使用 list_tables 和 describe_table 确认表名和字段名"
        ),
        original=error_msg[:500],
    )
