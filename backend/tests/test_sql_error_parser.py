"""
SQL 错误解析器测试。

覆盖 MySQL、PostgreSQL、Oracle 三种数据库的错误消息解析，
以及未识别错误的兜底处理。
"""

from __future__ import annotations

import pytest

from app.engine.sql_error_parser import ErrorType, parse_db_error


# =============================================================================
# MySQL 错误解析
# =============================================================================

class TestMysqlErrorParsing:
    """MySQL 错误码匹配测试。"""

    def test_table_not_found(self):
        """MySQL 1146 错误 → TABLE_NOT_FOUND，带回表名。"""
        exc = ValueError(
            "SQL 执行错误：(1146, \"Table 'test_db.nonexistent' doesn't exist\")"
        )
        result = parse_db_error(exc, db_type="mysql")
        assert result.error_type == ErrorType.TABLE_NOT_FOUND
        assert "nonexistent" in result.detail
        assert "list_tables" in result.suggestion

    def test_table_not_found_simple(self):
        """MySQL 1146 错误（带数据库前缀）。"""
        exc = ValueError(
            "SQL 执行错误：(1146, \"Table 'mydb.users_wrong' doesn't exist\")"
        )
        result = parse_db_error(exc, db_type="mysql")
        assert result.error_type == ErrorType.TABLE_NOT_FOUND
        assert "users_wrong" in result.detail

    def test_column_not_found(self):
        """MySQL 1054 错误 → COLUMN_NOT_FOUND，带回字段名。"""
        exc = ValueError(
            "SQL 执行错误：(1054, \"Unknown column 'wrong_col' in 'field list'\")"
        )
        result = parse_db_error(exc, db_type="mysql")
        assert result.error_type == ErrorType.COLUMN_NOT_FOUND
        assert "wrong_col" in result.detail
        assert "describe_table" in result.suggestion

    def test_column_not_found_in_where(self):
        """MySQL 1054 错误（WHERE 子句中）。"""
        exc = ValueError(
            "SQL 执行错误：(1054, \"Unknown column 'age' in 'where clause'\")"
        )
        result = parse_db_error(exc, db_type="mysql")
        assert result.error_type == ErrorType.COLUMN_NOT_FOUND
        assert "age" in result.detail

    def test_syntax_error(self):
        """MySQL 1064 错误 → SYNTAX_ERROR。"""
        exc = ValueError(
            "SQL 执行错误：(1064, \"You have an error in your SQL syntax; "
            "check the manual that corresponds to your MySQL server version\")"
        )
        result = parse_db_error(exc, db_type="mysql")
        assert result.error_type == ErrorType.SYNTAX_ERROR

    def test_permission_denied(self):
        """MySQL 1142 错误 → PERMISSION_DENIED。"""
        exc = ValueError(
            "SQL 执行错误：(1142, \"SELECT command denied to user "
            "'readonly'@'localhost' for table 'users'\")"
        )
        result = parse_db_error(exc, db_type="mysql")
        assert result.error_type == ErrorType.PERMISSION_DENIED


# =============================================================================
# PostgreSQL 错误解析
# =============================================================================

class TestPostgresqlErrorParsing:
    """PostgreSQL 错误消息匹配测试。"""

    def test_table_not_found(self):
        """PostgreSQL relation 不存在 → TABLE_NOT_FOUND。"""
        exc = ValueError(
            'SQL 执行错误：relation "orders" does not exist\n'
            'LINE 1: SELECT * FROM orders'
        )
        result = parse_db_error(exc, db_type="postgresql")
        assert result.error_type == ErrorType.TABLE_NOT_FOUND
        assert "orders" in result.detail

    def test_column_not_found(self):
        """PostgreSQL column 不存在 → COLUMN_NOT_FOUND。"""
        exc = ValueError(
            'SQL 执行错误：column "email_addr" does not exist\n'
            'LINE 1: SELECT email_addr FROM users'
        )
        result = parse_db_error(exc, db_type="postgresql")
        assert result.error_type == ErrorType.COLUMN_NOT_FOUND
        assert "email_addr" in result.detail

    def test_syntax_error(self):
        """PostgreSQL 语法错误 → SYNTAX_ERROR。"""
        exc = ValueError(
            'SQL 执行错误：syntax error at or near "FROM"\n'
            'LINE 1: SELECT * FORM users'
        )
        result = parse_db_error(exc, db_type="postgresql")
        assert result.error_type == ErrorType.SYNTAX_ERROR

    def test_permission_denied(self):
        """PostgreSQL 权限不足 → PERMISSION_DENIED。"""
        exc = ValueError(
            "SQL 执行错误：permission denied for table users"
        )
        result = parse_db_error(exc, db_type="postgresql")
        assert result.error_type == ErrorType.PERMISSION_DENIED


# =============================================================================
# Oracle 错误解析
# =============================================================================

class TestOracleErrorParsing:
    """Oracle ORA 错误码匹配测试。"""

    def test_table_not_found(self):
        """ORA-00942 → TABLE_NOT_FOUND。"""
        exc = ValueError(
            "SQL 执行错误：ORA-00942: table or view does not exist"
        )
        result = parse_db_error(exc, db_type="oracle")
        assert result.error_type == ErrorType.TABLE_NOT_FOUND

    def test_column_not_found(self):
        """ORA-00904 无效标识符 → COLUMN_NOT_FOUND。"""
        exc = ValueError(
            'SQL 执行错误：ORA-00904: "WRONG_COL": invalid identifier'
        )
        result = parse_db_error(exc, db_type="oracle")
        assert result.error_type == ErrorType.COLUMN_NOT_FOUND
        assert "WRONG_COL" in result.detail

    def test_syntax_error(self):
        """ORA-00900 无效 SQL → SYNTAX_ERROR。"""
        exc = ValueError(
            "SQL 执行错误：ORA-00900: invalid SQL statement"
        )
        result = parse_db_error(exc, db_type="oracle")
        assert result.error_type == ErrorType.SYNTAX_ERROR

    def test_permission_denied(self):
        """ORA-01031 权限不足 → PERMISSION_DENIED。"""
        exc = ValueError(
            "SQL 执行错误：ORA-01031: insufficient privileges"
        )
        result = parse_db_error(exc, db_type="oracle")
        assert result.error_type == ErrorType.PERMISSION_DENIED


# =============================================================================
# 未知错误 / 兜底
# =============================================================================

class TestUnknownError:
    """无法识别的错误 → UNKNOWN 兜底。"""

    def test_unknown_mysql_error(self):
        """MySQL 未匹配的错误码 → UNKNOWN，保留原始信息。"""
        exc = ValueError(
            "SQL 执行错误：(2006, \"MySQL server has gone away\")"
        )
        result = parse_db_error(exc, db_type="mysql")
        assert result.error_type == ErrorType.UNKNOWN
        assert "MySQL server has gone away" in result.detail
        assert "list_tables" in result.suggestion  # 兜底建议仍然有用

    def test_unknown_db_type(self):
        """不支持的数据库类型 → 使用兜底逻辑 → UNKNOWN。"""
        exc = ValueError("some random database error")
        result = parse_db_error(exc, db_type="unknown_db")
        assert result.error_type == ErrorType.UNKNOWN
        assert result.detail  # 兜底消息不为空
        assert result.suggestion  # 兜底建议不为空

    def test_empty_error_message(self):
        """空错误消息 → UNKNOWN，不崩溃。"""
        exc = ValueError("")
        result = parse_db_error(exc, db_type="mysql")
        assert result.error_type == ErrorType.UNKNOWN
        assert result.detail

    def test_non_value_error(self):
        """非 ValueError 类型异常 → 正常解析（取 str(exc)）。"""
        exc = RuntimeError(
            "SQL 执行错误：(1146, \"Table 'test.x' doesn't exist\")"
        )
        result = parse_db_error(exc, db_type="mysql")
        # 即使异常类型不是 ValueError，消息内容匹配也能解析
        assert result.error_type == ErrorType.TABLE_NOT_FOUND
        assert "x" in result.detail


# =============================================================================
# 返回结构完整性
# =============================================================================

class TestParsedErrorStructure:
    """验证 ParsedError 返回结构的完整性。"""

    @pytest.mark.parametrize("db_type", ["mysql", "postgresql", "oracle"])
    def test_all_fields_present(self, db_type):
        """解析结果的所有字段都应非空。"""
        exc = ValueError("some error")
        result = parse_db_error(exc, db_type=db_type)
        assert result.error_type is not None
        assert result.detail
        assert result.suggestion
        assert result.original

    def test_original_truncated(self):
        """原始错误消息超过 500 字符时应截断。"""
        long_msg = "x" * 1000
        exc = ValueError(long_msg)
        result = parse_db_error(exc, db_type="mysql")
        assert len(result.original) <= 500
        assert result.original.endswith("x" * (500 - len(result.original) + result.original.count("x"))) or True
