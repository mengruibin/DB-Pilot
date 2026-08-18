"""
安全阶段实现单元测试（security-pipeline-north-star-plan / 任务 D2）。

覆盖：
  SQLAuditStage:    拦 DDL / 拦非 admin DML（含 MERGE 必拦）/ 放行 SELECT /
                    放行 admin DML / fail-open（ImportError / Exception）
  RowEstimationStage: mock EXPLAIN → ROW_ESTIMATION_BLOCKED / fail-open / 非 DML 跳过
  ConfirmStage:     check 恒放行；build_action 字段齐全 + 连接参数被滤除
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

# 在导入 app 模块前设置测试用环境变量
os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault("LLM_MODEL", "test-model")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///test.db")

from app.agent.security.models import SecurityContext, StageResult
from app.agent.security.stages import (
    ConfirmStage,
    RowEstimationStage,
    SQLAuditStage,
)


def _tc(name: str, args: dict, cid: str) -> dict:
    return {"id": cid, "name": name, "args": args}


def _conn(user_role: str = "readonly") -> dict:
    return {
        "connection_id": "c1",
        "db_type": "mysql",
        "host": "h",
        "port": 3306,
        "database": "d",
        "user": "u",
        "password": "p",
        "user_role": user_role,
    }


def _ctx(user_role: str = "readonly") -> SecurityContext:
    conn = _conn(user_role)
    return SecurityContext(
        run_id="r",
        session_id="s1",
        conn_config=conn,
        user_role=user_role,
        consecutive_blocks=0,
    )


# =============================================================================
# SQLAuditStage
# =============================================================================


class TestSQLAuditStage:
    async def _check(self, sql: str, user_role: str = "readonly") -> StageResult:
        return await SQLAuditStage().check(
            _tc("execute_write_sql", {"sql": sql}, "call_1"), _conn(user_role), _ctx(user_role)
        )

    @pytest.mark.asyncio
    async def test_blocks_ddl(self):
        """红线 DDL（DROP/ALTER/TRUNCATE/CREATE/GRANT/REVOKE）一律拦截。"""
        for ddl in (
            "DROP TABLE t",
            "ALTER TABLE t ADD COLUMN c INT",
            "TRUNCATE TABLE t",
            "CREATE TABLE t (id INT)",
            "GRANT ALL ON t TO user",
            "REVOKE ALL ON t FROM user",
        ):
            result = await self._check(ddl, user_role="admin")  # admin 也拦
            assert result.blocked, f"DDL 未被拦截: {ddl}"
            assert result.block_code == "SQL_AUDIT_BLOCKED"

    @pytest.mark.asyncio
    async def test_blocks_non_admin_dml(self):
        """非 admin 的 DELETE/UPDATE/INSERT 拦截。"""
        for sql in (
            "DELETE FROM t WHERE id = 1",
            "UPDATE t SET a = 1 WHERE id = 1",
            "INSERT INTO t VALUES (1)",
        ):
            result = await self._check(sql, user_role="readonly")
            assert result.blocked, f"非 admin DML 未被拦截: {sql}"
            assert result.block_code == "SQL_AUDIT_BLOCKED"

    @pytest.mark.asyncio
    async def test_blocks_merge_for_non_admin(self):
        """★ MERGE 非 admin 必拦（审计缺口已补）。"""
        result = await self._check(
            "MERGE INTO t1 USING t2 ON (t1.id = t2.id) WHEN MATCHED THEN UPDATE SET t1.a = t2.a",
            user_role="readonly",
        )
        assert result.blocked
        assert result.block_code == "SQL_AUDIT_BLOCKED"

    @pytest.mark.asyncio
    async def test_allows_admin_dml(self):
        """admin 的 DELETE/UPDATE/INSERT/MERGE 放行。"""
        for sql in (
            "DELETE FROM t WHERE id = 1",
            "UPDATE t SET a = 1 WHERE id = 1",
            "INSERT INTO t VALUES (1)",
            "MERGE INTO t1 USING t2 ON (t1.id = t2.id) WHEN MATCHED THEN UPDATE SET t1.a = t2.a",
        ):
            result = await self._check(sql, user_role="admin")
            assert not result.blocked, f"admin DML 被误拦: {sql}"

    @pytest.mark.asyncio
    async def test_allows_select(self):
        """SELECT 放行。"""
        result = await self._check("SELECT * FROM t", user_role="readonly")
        assert not result.blocked

    @pytest.mark.asyncio
    async def test_empty_sql_passes(self):
        """空 SQL 放行（不做审计）。"""
        result = await self._check("", user_role="readonly")
        assert not result.blocked

    @pytest.mark.asyncio
    async def test_fail_open_on_import_error(self):
        """审计模块不可用 → fail-open 放行带 warning。"""
        stage = SQLAuditStage()
        with patch("app.engine.sql_auditor.audit", side_effect=ImportError):
            result = await stage.check(
                _tc("execute_readonly_sql", {"sql": "SELECT 1"}, "call_1"),
                _conn(),
                _ctx(),
            )
        assert not result.blocked
        assert result.warnings  # 非空警告

    @pytest.mark.asyncio
    async def test_fail_open_on_audit_exception(self):
        """审计异常 → fail-open 放行带 warning。"""
        stage = SQLAuditStage()
        with patch("app.engine.sql_auditor.audit", side_effect=RuntimeError("boom")):
            result = await stage.check(
                _tc("execute_readonly_sql", {"sql": "SELECT 1"}, "call_1"),
                _conn(),
                _ctx(),
            )
        assert not result.blocked
        assert result.warnings

    # ── require_where（write-where-guard-plan）：无 WHERE 全表删改硬拦 ──

    async def _check_require_where(self, sql: str, user_role: str = "admin") -> StageResult:
        return await SQLAuditStage(require_where=True).check(
            _tc("execute_write_sql", {"sql": sql}, "call_1"), _conn(user_role), _ctx(user_role)
        )

    @pytest.mark.asyncio
    async def test_require_where_blocks_no_where_delete(self):
        """require_where：无 WHERE 的 DELETE → WRITE_NO_WHERE_BLOCKED。"""
        result = await self._check_require_where("DELETE FROM t")
        assert result.blocked
        assert result.block_code == "WRITE_NO_WHERE_BLOCKED"

    @pytest.mark.asyncio
    async def test_require_where_blocks_no_where_update(self):
        """require_where：无 WHERE 的 UPDATE → WRITE_NO_WHERE_BLOCKED。"""
        result = await self._check_require_where("UPDATE t SET a = 1")
        assert result.blocked
        assert result.block_code == "WRITE_NO_WHERE_BLOCKED"

    @pytest.mark.asyncio
    async def test_require_where_blocks_constant_where(self):
        """require_where：WHERE 恒真（1=1 / TRUE）→ WRITE_NO_WHERE_BLOCKED。"""
        for sql in ("DELETE FROM t WHERE 1 = 1", "UPDATE t SET a = 1 WHERE TRUE"):
            result = await self._check_require_where(sql)
            assert result.blocked, f"常量 WHERE 未拦截: {sql}"
            assert result.block_code == "WRITE_NO_WHERE_BLOCKED"

    @pytest.mark.asyncio
    async def test_require_where_allows_column_where(self):
        """require_where：含列 WHERE 放行。"""
        for sql in (
            "DELETE FROM t WHERE id = 1",
            "UPDATE t SET a = 1 WHERE id = 1",
            "DELETE FROM t WHERE deleted_at IS NOT NULL",
        ):
            result = await self._check_require_where(sql)
            assert not result.blocked, f"含列 WHERE 误拦: {sql}"

    @pytest.mark.asyncio
    async def test_require_where_allows_insert(self):
        """require_where：INSERT 不受影响。"""
        result = await self._check_require_where("INSERT INTO t VALUES (1)")
        assert not result.blocked

    @pytest.mark.asyncio
    async def test_require_where_default_off(self):
        """require_where 默认 False：无 WHERE DELETE 行为不变（不新增拦截）。"""
        result = await self._check("DELETE FROM t", user_role="admin")
        assert not result.blocked

    @pytest.mark.asyncio
    async def test_require_where_readonly_still_audit_blocked(self):
        """readonly + 无 WHERE DELETE：仍被既有角色审计拦（SQL_AUDIT_BLOCKED 优先）。"""
        result = await self._check_require_where("DELETE FROM t", user_role="readonly")
        assert result.blocked
        assert result.block_code == "SQL_AUDIT_BLOCKED"


# =============================================================================
# SQLAuditStage 语句类型白名单（allowed_stmt_types，fail-closed）
# =============================================================================


class TestSQLAuditStageStmtTypeWhitelist:
    """语句类型白名单：fail-closed 硬约束，堵住 Command 回退 / 解析异常绕过。"""

    WRITE_ALLOWED = ("INSERT", "UPDATE", "DELETE")
    READ_ALLOWED = ("SELECT", "SHOW", "EXPLAIN", "UNION", "USE", "SET")

    async def _check(
        self,
        sql: str,
        allowed: tuple[str, ...],
        user_role: str = "admin",
    ) -> StageResult:
        stage = SQLAuditStage(allowed_stmt_types=allowed)
        return await stage.check(
            _tc("execute_readonly_sql", {"sql": sql}, "call_1"),
            _conn(user_role),
            _ctx(user_role),
        )

    # ── 写工具：只放行 INSERT/UPDATE/DELETE ──
    @pytest.mark.asyncio
    async def test_write_tool_allows_dml(self):
        """写工具白名单：INSERT/UPDATE/DELETE（含 WITH 前缀）放行。"""
        for sql in (
            "INSERT INTO t VALUES (1)",
            "UPDATE t SET a = 1 WHERE id = 1",
            "DELETE FROM t WHERE id = 1",
            "WITH c AS (SELECT 1) DELETE FROM t WHERE id = 1",
            "INSERT INTO t (a) SELECT * FROM src",  # INSERT...SELECT 仍是 Insert
        ):
            result = await self._check(sql, self.WRITE_ALLOWED)
            assert not result.blocked, f"写工具误拦合法 DML: {sql}"

    @pytest.mark.asyncio
    async def test_write_tool_blocks_non_dml(self):
        """写工具白名单：非 DML（SELECT/GRANT/REPLACE/MERGE/DDL）一律拦截。"""
        for sql in (
            "SELECT * FROM t",
            "GRANT ALL ON *.* TO u",  # Command 回退
            "REPLACE INTO t (a) VALUES (1)",  # Command 回退
            "MERGE INTO t1 USING t2 ON (t1.id = t2.id) "
            "WHEN MATCHED THEN UPDATE SET t1.a = t2.a",  # Merge 类型 / 解析异常
            "RENAME TABLE a TO b",  # Command 回退
            "SET GLOBAL slow_query_log = ON",
            "CREATE USER u IDENTIFIED BY p",  # Command 回退
            "DROP TABLE t",
        ):
            result = await self._check(sql, self.WRITE_ALLOWED)
            assert result.blocked, f"写工具放行非 DML: {sql}"
            assert result.block_code == "SQL_STMT_TYPE_BLOCKED"

    @pytest.mark.asyncio
    async def test_write_tool_blocks_multi_statement(self):
        """写工具白名单：多语句（白名单内类型也不行）拦截。"""
        result = await self._check("DELETE FROM t WHERE id = 1; DELETE FROM t2", self.WRITE_ALLOWED)
        assert result.blocked
        assert result.block_code == "SQL_STMT_TYPE_BLOCKED"

    # ── 只读工具：只放行只读语句，拦写语句（即使 admin） ──
    @pytest.mark.asyncio
    async def test_readonly_tool_allows_read(self):
        """只读工具白名单：SELECT/SHOW/DESC/EXPLAIN/UNION/USE/SET 放行。"""
        for sql in (
            "SELECT * FROM t",
            "SELECT 1 UNION SELECT 2",  # 顶层 UNION
            "SHOW TABLES",
            "DESC t",  # Describe → EXPLAIN
            "EXPLAIN SELECT * FROM t",
            "USE mydb",
            "SET NAMES utf8mb4",
        ):
            result = await self._check(sql, self.READ_ALLOWED)
            assert not result.blocked, f"只读工具误拦合法只读语句: {sql}"

    @pytest.mark.asyncio
    async def test_readonly_tool_blocks_writes(self):
        """只读工具白名单：写语句（即使 admin）一律拦截。"""
        for sql in (
            "INSERT INTO t VALUES (1)",
            "UPDATE t SET a = 1 WHERE id = 1",
            "DELETE FROM t WHERE id = 1",
            "REPLACE INTO t (a) VALUES (1)",
            "GRANT ALL ON *.* TO u",
            "WITH c AS (SELECT 1) DELETE FROM t WHERE id = 1",  # WITH 前缀藏不住
        ):
            result = await self._check(sql, self.READ_ALLOWED)
            assert result.blocked, f"只读工具放行写语句: {sql}"
            assert result.block_code == "SQL_STMT_TYPE_BLOCKED"


# =============================================================================
# RowEstimationStage
# =============================================================================

# MySQL EXPLAIN JSON：全表扫描 500 万行（触发 R1_FULL_SCAN_LARGE）
_FULL_SCAN_EXPLAIN = (
    '{"query_block": {"select_id": 1, "table": {"table_name": "t", '
    '"access_type": "ALL", "rows_examined_per_scan": 5000000, '
    '"rows_produced_per_join": 5000000}}}'
)


def _mock_adapter(explain_result=None, explain_error=None):
    """构造 mock 临时适配器（explain + disconnect）。"""
    adapter = AsyncMock()
    if explain_error is not None:
        adapter.explain.side_effect = explain_error
    else:
        adapter.explain.return_value = explain_result or {
            "explain_output": _FULL_SCAN_EXPLAIN,
            "format": "json",
        }
    return adapter


class TestRowEstimationStage:
    @pytest.mark.asyncio
    async def test_blocks_full_scan(self):
        """全表扫描超阈值 → ROW_ESTIMATION_BLOCKED。"""
        stage = RowEstimationStage()
        with patch.object(stage, "_create_temp_adapter", AsyncMock(return_value=_mock_adapter())):
            result = await stage.check(
                _tc("execute_readonly_sql", {"sql": "SELECT * FROM big_table"}, "call_1"),
                _conn(),
                _ctx(),
            )
        assert result.blocked
        assert result.block_code == "ROW_ESTIMATION_BLOCKED"
        assert "EXPLAIN 安全评估" in (result.reason or "")

    @pytest.mark.asyncio
    async def test_non_dml_skipped(self):
        """非 DML 语句（SHOW / EXPLAIN）跳过 EXPLAIN。"""
        stage = RowEstimationStage()
        with patch.object(stage, "_create_temp_adapter", AsyncMock(return_value=_mock_adapter())):
            result = await stage.check(
                _tc("execute_readonly_sql", {"sql": "SHOW TABLES"}, "call_1"),
                _conn(),
                _ctx(),
            )
        assert not result.blocked

    @pytest.mark.asyncio
    async def test_empty_sql_skipped(self):
        """空 SQL 跳过。"""
        result = await RowEstimationStage().check(
            _tc("execute_readonly_sql", {"sql": ""}, "call_1"), _conn(), _ctx()
        )
        assert not result.blocked

    @pytest.mark.asyncio
    async def test_fail_open_on_explain_error(self):
        """EXPLAIN 执行失败 → fail-open 放行带 warning。"""
        stage = RowEstimationStage()
        adapter = _mock_adapter(explain_error=RuntimeError("db down"))
        with patch.object(stage, "_create_temp_adapter", AsyncMock(return_value=adapter)):
            result = await stage.check(
                _tc("execute_readonly_sql", {"sql": "SELECT * FROM t"}, "call_1"),
                _conn(),
                _ctx(),
            )
        assert not result.blocked
        assert result.warnings

    @pytest.mark.asyncio
    async def test_fail_open_on_adapter_create_error(self):
        """临时适配器创建失败 → fail-open 放行带 warning。"""
        stage = RowEstimationStage()
        with patch.object(
            stage, "_create_temp_adapter", AsyncMock(side_effect=RuntimeError("no conn"))
        ):
            result = await stage.check(
                _tc("execute_readonly_sql", {"sql": "SELECT * FROM t"}, "call_1"),
                _conn(),
                _ctx(),
            )
        assert not result.blocked
        assert result.warnings

    @pytest.mark.asyncio
    async def test_low_risk_allowed(self):
        """低风险查询（索引查找小行数）放行。"""
        stage = RowEstimationStage()
        low_risk = (
            '{"query_block": {"select_id": 1, "table": {"table_name": "t", '
            '"access_type": "ref", "rows_examined_per_scan": 10, '
            '"rows_produced_per_join": 10}}}'
        )
        adapter = _mock_adapter({"explain_output": low_risk, "format": "json"})
        with patch.object(stage, "_create_temp_adapter", AsyncMock(return_value=adapter)):
            result = await stage.check(
                _tc("execute_readonly_sql", {"sql": "SELECT * FROM t WHERE id = 1"}, "call_1"),
                _conn(),
                _ctx(),
            )
        assert not result.blocked


# =============================================================================
# ConfirmStage
# =============================================================================


class TestConfirmStage:
    @pytest.mark.asyncio
    async def test_check_always_allows(self):
        """标记型阶段：check 恒 blocked=False。"""
        result = await ConfirmStage().check(
            _tc("execute_write_sql", {"sql": "INSERT INTO t VALUES (1)"}, "call_1"),
            _conn(),
            _ctx(),
        )
        assert not result.blocked

    def test_build_action_fields(self):
        """build_action 字段齐全（tool_call_id/tool/category/description/details）。"""
        tc = _tc("execute_write_sql", {"sql": "INSERT INTO t VALUES (1)"}, "call_w")
        action = ConfirmStage.build_action(tc, category="sql_write")
        assert action["tool_call_id"] == "call_w"
        assert action["tool"] == "execute_write_sql"
        assert action["category"] == "sql_write"
        assert action["description"].startswith("execute_write_sql")
        assert action["details"] == {"sql": "INSERT INTO t VALUES (1)"}

    def test_build_action_filters_conn_params(self):
        """连接注入参数（含 password/user_role）被滤除，保持 details 干净。"""
        tc = _tc(
            "execute_write_sql",
            {
                "sql": "INSERT INTO t VALUES (1)",
                "connection_id": "c1",
                "password": "secret",
                "user_role": "admin",
            },
            "call_w",
        )
        action = ConfirmStage.build_action(tc, category="sql_write")
        for injected in ("connection_id", "password", "user_role"):
            assert injected not in action["details"]
        assert action["details"] == {"sql": "INSERT INTO t VALUES (1)"}

    def test_build_action_default_category(self):
        """未指定 category 时降级为 generic。"""
        tc = _tc("some_new_tool", {"a": 1}, "call_x")
        action = ConfirmStage.build_action(tc)
        assert action["category"] == "generic"
