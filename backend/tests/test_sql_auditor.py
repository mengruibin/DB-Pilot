"""
SQL 安全审计引擎基线测试。

依据 AGENTS.md §验证与测试要求：
  1. 合法 SELECT → 通过
  2. DROP TABLE → 拒绝
  3. DELETE FROM → 拒绝（未确认模式）
  4. SELECT ... INTO OUTFILE → 拒绝（数据导出风险）
  5. 多语句 SELECT 1; DROP TABLE users; → 拒绝

同时覆盖验收标准的其他要求。
"""

from __future__ import annotations

from app.engine.sql_auditor import audit


class TestSqlAuditorBaseline:
    """5 条基线测试（AGENTS.md §验证与测试要求）。"""

    def test_select_passes(self):
        """基线 1：合法 SELECT 应通过审计。"""
        result = audit("SELECT * FROM users WHERE id = 1", db_type="mysql")
        assert result.passed is True
        assert result.is_readonly is True
        assert len(result.violations) == 0

    def test_drop_table_blocked(self):
        """基线 2：DROP TABLE 应被拒绝。"""
        result = audit("DROP TABLE users", db_type="mysql")
        assert result.passed is False
        assert result.is_readonly is False
        # 应包含 DDL 违规
        assert any("DROP" in v.type for v in result.violations)

    def test_delete_blocked_without_admin(self):
        """基线 3：DELETE FROM 应被拒绝（非 admin / 只读兜底角色）。"""
        result = audit("DELETE FROM users WHERE id = 1", db_type="mysql", user_role="readonly")
        assert result.passed is False
        assert result.is_readonly is False
        assert any("DELETE" in v.type for v in result.violations)

    def test_select_into_outfile_blocked(self):
        """基线 4：SELECT ... INTO OUTFILE 应被拒绝。"""
        result = audit(
            "SELECT * INTO OUTFILE '/tmp/export.csv' FROM users",
            db_type="mysql",
        )
        assert result.passed is False
        assert any("DATA_EXPORT" in v.type for v in result.violations)

    def test_multi_statement_blocked(self):
        """基线 5：多语句应被拒绝。"""
        result = audit("SELECT 1; DROP TABLE users;", db_type="mysql")
        assert result.passed is False
        assert any("MULTIPLE_STATEMENTS" in v.type for v in result.violations)


class TestSqlAuditorExtended:
    """扩展测试：覆盖验收标准其他要求。"""

    def test_alter_table_blocked(self):
        """ALTER TABLE 应被拒绝。"""
        result = audit("ALTER TABLE users ADD COLUMN age INT", db_type="mysql")
        assert result.passed is False
        assert any("ALTER" in v.type for v in result.violations)

    def test_truncate_blocked(self):
        """TRUNCATE 应被拒绝。"""
        result = audit("TRUNCATE TABLE users", db_type="mysql")
        assert result.passed is False
        assert any("TRUNCATE" in v.type for v in result.violations)

    def test_create_database_blocked(self):
        """CREATE DATABASE 应被拒绝。"""
        result = audit("CREATE DATABASE new_db", db_type="mysql")
        assert result.passed is False
        assert any("CREATE" in v.type for v in result.violations)

    def test_grant_revoke_blocked(self):
        """GRANT/REVOKE 应被拒绝。"""
        result = audit("GRANT SELECT ON users TO readonly", db_type="mysql")
        assert result.passed is False
        assert any("GRANT" in v.type for v in result.violations)

        result = audit("REVOKE SELECT ON users FROM readonly", db_type="mysql")
        assert result.passed is False
        assert any("REVOKE" in v.type for v in result.violations)

    def test_update_blocked_without_admin(self):
        """UPDATE 在 non-admin 角色时应被拒绝。"""
        result = audit(
            "UPDATE users SET name = 'test' WHERE id = 1",
            db_type="mysql",
            user_role="readonly",
        )
        assert result.passed is False
        assert any("UPDATE" in v.type for v in result.violations)

    def test_update_allowed_for_admin(self):
        """UPDATE 在 admin 角色时应通过（仅提示非拦截）。"""
        # 根据 AC-3：DELETE/UPDATE 在 user_role != "admin" 时拦截
        # admin 角色不拦截
        result = audit(
            "UPDATE users SET name = 'test' WHERE id = 1",
            db_type="mysql",
            user_role="admin",
        )
        assert result.passed is True

    def test_delete_allowed_for_admin(self):
        """DELETE 在 admin 角色时应通过。"""
        result = audit(
            "DELETE FROM users WHERE id = 1",
            db_type="mysql",
            user_role="admin",
        )
        assert result.passed is True

    def test_select_with_complex_expression(self):
        """复杂 SELECT 应通过审计。"""
        result = audit(
            "SELECT u.name, COUNT(o.id) as cnt "
            "FROM users u "
            "JOIN orders o ON u.id = o.user_id "
            "WHERE o.created_at > NOW() - INTERVAL 1 DAY "
            "GROUP BY u.name "
            "ORDER BY cnt DESC "
            "LIMIT 10",
            db_type="mysql",
        )
        assert result.passed is True
        assert result.is_readonly is True

    def test_postgresql_dialect(self):
        """PostgreSQL 方言解析。"""
        result = audit("SELECT * FROM users WHERE id = $1", db_type="postgresql")
        assert result.passed is True

    def test_oracle_dialect(self):
        """Oracle 方言解析。"""
        result = audit("SELECT * FROM dual", db_type="oracle")
        assert result.passed is True

    def test_audit_result_contains_is_readonly(self):
        """AuditResult 应包含 is_readonly 标记（AC-8）。"""
        result = audit("SELECT 1", db_type="mysql")
        assert hasattr(result, "is_readonly")
        assert result.is_readonly is True

        result = audit("DROP TABLE users", db_type="mysql")
        assert result.is_readonly is False

    def test_multiple_drops_in_one(self):
        """单条语句中的多个 DROP（如 DROP TABLE IF EXISTS）应拦截。"""
        result = audit("DROP TABLE IF EXISTS users", db_type="mysql")
        assert result.passed is False
        assert any("DROP" in v.type for v in result.violations)
