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

from app.engine.sql_auditor import audit, check_write_scope


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


class TestCheckWriteScope:
    """check_write_scope：UPDATE/DELETE 操作范围静态分析（write-where-guard-plan）。"""

    def test_delete_no_where_is_full_table(self):
        """无 WHERE 的 DELETE → 等价全表。"""
        result = check_write_scope("DELETE FROM t", db_type="mysql")
        assert result.is_full_table is True
        assert result.target_table == "t"
        assert result.has_where is False

    def test_update_no_where_is_full_table(self):
        """无 WHERE 的 UPDATE → 等价全表。"""
        result = check_write_scope("UPDATE t SET a = 1", db_type="mysql")
        assert result.is_full_table is True

    def test_constant_where_is_full_table(self):
        """WHERE 纯常量（不含列引用）→ 等价全表。"""
        for sql in (
            "DELETE FROM t WHERE 1 = 1",
            "UPDATE t SET a = 1 WHERE TRUE",
            "DELETE FROM t WHERE 'a' = 'a'",
            "DELETE FROM t WHERE 1 = 1 AND 'x' = 'x'",
            "DELETE FROM t WHERE NOW() > '2020-01-01'",
        ):
            result = check_write_scope(sql, db_type="mysql")
            assert result.is_full_table is True, f"常量 WHERE 未判全表: {sql}"

    def test_column_where_is_not_full_table(self):
        """含列引用的 WHERE → 数据相关，非全表。"""
        for sql in (
            "DELETE FROM t WHERE id = 1",
            "UPDATE t SET a = 1 WHERE id = 1",
            "DELETE FROM t WHERE id > 0",  # 近全表但数据相关，静态无法判定 → 接受
            "DELETE FROM t WHERE deleted_at IS NOT NULL",  # 软删除清理合法写法
            "DELETE FROM t WHERE id IN (SELECT id FROM x)",  # 子查询 → 数据相关
        ):
            result = check_write_scope(sql, db_type="mysql")
            assert result.is_full_table is False, f"含列 WHERE 误判全表: {sql}"
            assert result.has_where is True

    def test_non_update_delete_not_applicable(self):
        """非 UPDATE/DELETE（INSERT/SELECT/SHOW）→ 范围检查不适用。"""
        for sql in ("INSERT INTO t VALUES (1)", "SELECT * FROM t", "SHOW TABLES"):
            result = check_write_scope(sql, db_type="mysql")
            assert result.is_update_or_delete is False
            assert result.is_full_table is False

    def test_multi_statement_defensive_pass(self):
        """多语句 → 防御性放行（类型兜底由语句类型白名单 fail-closed 负责）。"""
        result = check_write_scope("DELETE FROM t; DELETE FROM t2", db_type="mysql")
        assert result.is_update_or_delete is False
        assert result.is_full_table is False

    def test_parse_error_defensive_pass(self):
        """解析失败（不完整 WHERE）→ 防御性放行。"""
        result = check_write_scope("DELETE FROM t WHERE", db_type="mysql")
        assert result.is_full_table is False

    def test_dialects_consistent(self):
        """三方言行为一致（含 OR 恒真绕过）。"""
        for db_type in ("mysql", "postgresql", "oracle"):
            assert check_write_scope("DELETE FROM t", db_type=db_type).is_full_table is True
            assert (
                check_write_scope("DELETE FROM t WHERE id = 1", db_type=db_type).is_full_table
                is False
            )
            # OR 恒真绕过三方言一致拦截
            assert (
                check_write_scope(
                    "DELETE FROM t WHERE 1 = 1 OR id = 1", db_type=db_type
                ).is_full_table
                is True
            )

    # ── 布尔化简（write-where-guard-plan v2）：OR 恒真传播 / AND 恒真吸收 ──

    def test_or_constant_bypass_blocked(self):
        """OR 恒真分支污染整式（1=1 OR id=1）→ 等价全表（布尔化简后恒真）。"""
        for sql in (
            "DELETE FROM t WHERE 1 = 1 OR id = 1",
            "DELETE FROM t WHERE id = 1 OR 1 = 1",
            "DELETE FROM t WHERE 1 = 1 OR id = 1 OR y = 2",
            "DELETE FROM t WHERE (1 = 1 OR id = 1) OR y = 1",
        ):
            result = check_write_scope(sql, db_type="mysql")
            assert result.is_full_table is True, f"OR 恒真未判全表: {sql}"

    def test_or_constant_false_simplified(self):
        """恒假 OR 含列 → 化简为真实条件（1=2 OR id=1 ≡ id=1）→ 放行。"""
        result = check_write_scope("DELETE FROM t WHERE 1 = 2 OR id = 1", db_type="mysql")
        assert result.is_full_table is False
        assert result.where_is_constant is False

    def test_or_absorbed_by_and(self):
        """内层 OR 恒真被外层 AND 吸收（≡ 其余条件）→ 放行。"""
        for sql in (
            "DELETE FROM t WHERE 1 = 1 AND id = 1",
            "DELETE FROM t WHERE (1 = 1 OR id = 1) AND y = 1",
        ):
            result = check_write_scope(sql, db_type="mysql")
            assert result.is_full_table is False, f"AND 吸收恒真误判全表: {sql}"

    def test_idempotent_or_and(self):
        """幂等律（X OR X = X / X AND X = X）化简后仍含列 → 放行。"""
        for sql in (
            "DELETE FROM t WHERE id = 1 OR id = 1",
            "DELETE FROM t WHERE id = 1 AND id = 1",
        ):
            result = check_write_scope(sql, db_type="mysql")
            assert result.is_full_table is False, f"幂等化简后误判全表: {sql}"

    def test_constant_false_where_blocked(self):
        """恒假条件（WHERE 1=2 / NOT(1=1)）→ 保守拦截（与既有纯常量策略一致）。"""
        for sql in (
            "DELETE FROM t WHERE 1 = 2",
            "DELETE FROM t WHERE NOT (1 = 1)",
        ):
            result = check_write_scope(sql, db_type="mysql")
            assert result.is_full_table is True, f"恒假未拦截: {sql}"

    def test_null_is_null_constant(self):
        """NULL IS NULL 为常量恒真 → 拦截。"""
        result = check_write_scope("DELETE FROM t WHERE NULL IS NULL", db_type="mysql")
        assert result.is_full_table is True
