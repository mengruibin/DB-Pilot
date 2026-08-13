"""
SQL 结果集 LIMIT 下推单元测试。

覆盖 apply_read_limit 纯函数（进程内存防护，见 db/base.py）：
  1. 只读 SELECT 自动追加 LIMIT max+1（MySQL / PostgreSQL / Oracle FETCH FIRST）
  2. 顶层已有有界 LIMIT → 原样返回；超大 LIMIT → 收敛
  3. FOR UPDATE / WITH CTE / UNION / 子查询 LIMIT 等边界
  4. 非 SELECT / 解析失败 / 占位符 LIMIT → fail-safe 原样返回
  5. STATEMENT_MAX_RESULT_ROWS 常量语义
"""

from __future__ import annotations

from app.db.base import (
    STATEMENT_MAX_RESULT_ROWS,
    apply_read_limit,
)


class TestAppendLimit:
    """无 LIMIT 的只读 SELECT 应追加 LIMIT max+1。"""

    def test_mysql_simple_select(self) -> None:
        """基线：MySQL 单表 SELECT 追加 LIMIT 1001。"""
        out, rewritten = apply_read_limit("SELECT id, name FROM users", "mysql")
        assert rewritten is True
        assert out == f"SELECT id, name FROM users LIMIT {STATEMENT_MAX_RESULT_ROWS + 1}"

    def test_postgres_simple_select(self) -> None:
        """PostgreSQL 单表 SELECT 追加 LIMIT 1001。"""
        out, rewritten = apply_read_limit("SELECT * FROM t", "postgres")
        assert rewritten is True
        assert out.endswith(f"LIMIT {STATEMENT_MAX_RESULT_ROWS + 1}")

    def test_oracle_uses_fetch_first(self) -> None:
        """Oracle 用 FETCH FIRST n ROWS ONLY 而非 LIMIT。"""
        out, rewritten = apply_read_limit("SELECT * FROM t", "oracle")
        assert rewritten is True
        assert f"FETCH FIRST {STATEMENT_MAX_RESULT_ROWS + 1} ROWS ONLY" in out

    def test_trailing_semicolon(self) -> None:
        """末尾分号不影响重写（输出无分号残留）。"""
        out, rewritten = apply_read_limit("SELECT * FROM t WHERE a=1;", "mysql")
        assert rewritten is True
        assert "LIMIT" in out
        assert not out.rstrip().endswith(";")

    def test_max_rows_override(self) -> None:
        """max_rows 参数可覆盖默认阈值。"""
        out, rewritten = apply_read_limit("SELECT * FROM t", "mysql", max_rows=10)
        assert rewritten is True
        assert out == "SELECT * FROM t LIMIT 11"

    def test_with_cte(self) -> None:
        """WITH CTE 的 SELECT 追加 LIMIT。"""
        sql = "WITH c AS (SELECT id FROM a) SELECT * FROM c"
        out, rewritten = apply_read_limit(sql, "mysql")
        assert rewritten is True
        limit = STATEMENT_MAX_RESULT_ROWS + 1
        expected = f"WITH c AS (SELECT id FROM a) SELECT * FROM c LIMIT {limit}"
        assert out == expected

    def test_union(self) -> None:
        """UNION 顶层追加 LIMIT。"""
        sql = "SELECT a FROM t1 UNION SELECT b FROM t2"
        out, rewritten = apply_read_limit(sql, "mysql")
        assert rewritten is True
        assert out.endswith(f"LIMIT {STATEMENT_MAX_RESULT_ROWS + 1}")

    def test_subquery_limit_untouched(self) -> None:
        """子查询自带 LIMIT 不动，外层追加 LIMIT。"""
        sql = "SELECT * FROM t WHERE id IN (SELECT id FROM x LIMIT 5)"
        out, rewritten = apply_read_limit(sql, "mysql")
        assert rewritten is True
        assert "(SELECT id FROM x LIMIT 5)" in out
        assert out.endswith(f"LIMIT {STATEMENT_MAX_RESULT_ROWS + 1}")


class TestExistingLimit:
    """顶层已有限制时的行为。"""

    def test_bounded_limit_unchanged(self) -> None:
        """LIMIT ≤ max 已足够有界 → 原样返回（不改写）。"""
        sql = "SELECT * FROM t LIMIT 50"
        out, rewritten = apply_read_limit(sql, "mysql")
        assert rewritten is False
        assert out == sql

    def test_bounded_limit_with_offset_unchanged(self) -> None:
        """带 OFFSET 的有界 LIMIT 原样返回。"""
        sql = "SELECT * FROM t ORDER BY id LIMIT 10 OFFSET 20"
        out, rewritten = apply_read_limit(sql, "mysql")
        assert rewritten is False
        assert out == sql

    def test_large_limit_clamped(self) -> None:
        """LIMIT 超过 max → 收敛到 max+1。"""
        out, rewritten = apply_read_limit("SELECT * FROM t LIMIT 5000", "mysql")
        assert rewritten is True
        assert f"LIMIT {STATEMENT_MAX_RESULT_ROWS + 1}" in out

    def test_limit_at_boundary_unchanged(self) -> None:
        """LIMIT 恰好等于 max → 视为有界，原样返回。"""
        sql = f"SELECT * FROM t LIMIT {STATEMENT_MAX_RESULT_ROWS}"
        out, rewritten = apply_read_limit(sql, "mysql")
        assert rewritten is False
        assert out == sql

    def test_limit_placeholder_unchanged(self) -> None:
        """占位符 LIMIT（?/%s）无法安全判断 → 原样返回。"""
        sql = "SELECT * FROM t LIMIT ?"
        out, rewritten = apply_read_limit(sql, "mysql")
        assert rewritten is False
        assert out == sql


class TestForUpdate:
    """锁定子句（FOR UPDATE / FOR SHARE）时 LIMIT 位置正确。"""

    def test_mysql_for_update(self) -> None:
        """MySQL LIMIT 必须位于 FOR UPDATE 之前。"""
        out, rewritten = apply_read_limit("SELECT * FROM t FOR UPDATE", "mysql")
        assert rewritten is True
        assert out.index("LIMIT") < out.index("FOR UPDATE")

    def test_mysql_for_share(self) -> None:
        """MySQL LIMIT 必须位于 FOR SHARE 之前。"""
        out, rewritten = apply_read_limit("SELECT * FROM t WHERE x=1 FOR SHARE", "mysql")
        assert rewritten is True
        assert out.index("LIMIT") < out.index("FOR SHARE")

    def test_postgres_for_update(self) -> None:
        """PostgreSQL LIMIT 位于 FOR UPDATE 之前。"""
        out, rewritten = apply_read_limit("SELECT * FROM t FOR UPDATE", "postgres")
        assert rewritten is True
        assert out.index("LIMIT") < out.index("FOR UPDATE")

    def test_oracle_for_update(self) -> None:
        """Oracle FETCH FIRST 位于 FOR UPDATE 之前。"""
        out, rewritten = apply_read_limit("SELECT * FROM t FOR UPDATE", "oracle")
        assert rewritten is True
        assert out.index("FETCH FIRST") < out.index("FOR UPDATE")

    def test_for_update_bounded_limit_unchanged(self) -> None:
        """FOR UPDATE + 已有有界 LIMIT → 原样返回。"""
        sql = "SELECT * FROM t FOR UPDATE LIMIT 50"
        out, rewritten = apply_read_limit(sql, "mysql")
        assert rewritten is False
        assert out == sql


class TestFailSafe:
    """无法安全改写的场景 → (原 SQL, False) 原样放行。"""

    @staticmethod
    def _assert_unchanged(sql: str, dialect: str) -> None:
        out, rewritten = apply_read_limit(sql, dialect)
        assert rewritten is False
        assert out == sql

    def test_insert(self) -> None:
        """INSERT 不追加 LIMIT。"""
        self._assert_unchanged("INSERT INTO t VALUES (1)", "mysql")

    def test_update(self) -> None:
        """UPDATE 不追加 LIMIT。"""
        self._assert_unchanged("UPDATE t SET a=1", "mysql")

    def test_show(self) -> None:
        """SHOW 语句不追加 LIMIT。"""
        self._assert_unchanged("SHOW TABLES", "mysql")

    def test_set(self) -> None:
        """SET 语句不追加 LIMIT。"""
        self._assert_unchanged("SET SESSION TRANSACTION READ ONLY", "mysql")

    def test_explain(self) -> None:
        """EXPLAIN 语句不追加 LIMIT（结果集有界）。"""
        self._assert_unchanged("EXPLAIN SELECT * FROM t", "mysql")

    def test_mysql_percent_placeholder(self) -> None:
        """MySQL %s 参数化查询解析失败 → 原样返回。"""
        self._assert_unchanged("SELECT * FROM t WHERE a = %s", "mysql")

    def test_oracle_bind_placeholder(self) -> None:
        """Oracle :1 绑定参数解析失败 → 原样返回。"""
        self._assert_unchanged("SELECT * FROM t WHERE id = :1", "oracle")

    def test_malformed_sql(self) -> None:
        """非法 SQL 解析失败 → 原样返回。"""
        self._assert_unchanged("SELECT BROKEN FROM", "mysql")

    def test_empty_sql(self) -> None:
        """空 / 空白 SQL → 原样返回。"""
        self._assert_unchanged("", "mysql")
        self._assert_unchanged("   ", "mysql")


class TestConstant:
    """阈值常量语义。"""

    def test_constant_value(self) -> None:
        """上限 1000：给 truncate_result_for_llm（100 行/40K 字符）留出削减空间。"""
        assert STATEMENT_MAX_RESULT_ROWS == 1000
