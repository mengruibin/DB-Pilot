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
    ImpactEstimateStage,
    RowEstimationStage,
    SQLAuditStage,
    TransactionAuditStage,
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
# TransactionAuditStage（事务写工具 statements 逐条审计）
# =============================================================================


class TestTransactionAuditStage:
    """execute_write_transaction 的 statements 逐条审计。

    复用 execute_write_sql 同一套规则（类型白名单 + sqlglot 审计 + require_where），
    任一语句被拦 → 整事务拦截；空列表 / 非列表 / 空白语句 fail-closed。
    """

    async def _check(self, statements, user_role: str = "admin") -> StageResult:
        stage = TransactionAuditStage(
            allowed_stmt_types=("INSERT", "UPDATE", "DELETE"),
            require_where=True,
        )
        return await stage.check(
            _tc("execute_write_transaction", {"statements": statements}, "call_tx"),
            _conn(user_role),
            _ctx(user_role),
        )

    @pytest.mark.asyncio
    async def test_empty_list_blocked(self):
        """空列表 → TRANSACTION_INVALID_ARGUMENTS（fail-closed）。"""
        result = await self._check([])
        assert result.blocked
        assert result.block_code == "TRANSACTION_INVALID_ARGUMENTS"

    @pytest.mark.asyncio
    async def test_non_list_blocked(self):
        """非列表参数（字符串 / None / 数字）→ 拦。"""
        for bad in ("INSERT INTO t VALUES (1)", None, 123):
            result = await self._check(bad)
            assert result.blocked, f"非列表参数未拦截: {bad!r}"
            assert result.block_code == "TRANSACTION_INVALID_ARGUMENTS"

    @pytest.mark.asyncio
    async def test_json_string_list_coerced(self):
        """LLM 把 list 传成 JSON 字符串 → 解析后正常逐条审计（不误拦）。"""
        statements = '["INSERT INTO t (id) VALUES (1)", "UPDATE t SET a=1 WHERE id=1"]'
        result = await self._check(statements)
        assert not result.blocked

    @pytest.mark.asyncio
    async def test_blank_statement_blocked(self):
        """含空白元素 → 拦，reason 带语句索引。"""
        result = await self._check(["INSERT INTO t (id) VALUES (1)", "   "])
        assert result.blocked
        assert result.block_code == "TRANSACTION_INVALID_ARGUMENTS"
        assert "第 2 条" in (result.reason or "")

    @pytest.mark.asyncio
    async def test_mixed_select_blocked(self):
        """混入 SELECT（纯写契约）→ 整事务拦 SQL_STMT_TYPE_BLOCKED。"""
        result = await self._check(["INSERT INTO t (id) VALUES (1)", "SELECT * FROM t"])
        assert result.blocked
        assert result.block_code == "SQL_STMT_TYPE_BLOCKED"
        assert "第 2 条" in (result.reason or "")

    @pytest.mark.asyncio
    async def test_mixed_ddl_blocked(self):
        """混入红线 DDL（DROP）→ 类型白名单拦 SQL_STMT_TYPE_BLOCKED。"""
        result = await self._check(["INSERT INTO t (id) VALUES (1)", "DROP TABLE t"])
        assert result.blocked
        assert result.block_code == "SQL_STMT_TYPE_BLOCKED"
        assert "第 2 条" in (result.reason or "")

    @pytest.mark.asyncio
    async def test_multi_statement_in_one_element_blocked(self):
        """单元素内塞多语句（; 分隔）→ 类型白名单拦（双保险）。"""
        result = await self._check(["INSERT INTO t (id) VALUES (1); DELETE FROM t"])
        assert result.blocked
        assert result.block_code == "SQL_STMT_TYPE_BLOCKED"

    @pytest.mark.asyncio
    async def test_no_where_update_blocked(self):
        """事务内 UPDATE 无 WHERE → WRITE_NO_WHERE_BLOCKED。"""
        result = await self._check(["INSERT INTO t (id) VALUES (1)", "UPDATE t SET a = 1"])
        assert result.blocked
        assert result.block_code == "WRITE_NO_WHERE_BLOCKED"
        assert "第 2 条" in (result.reason or "")

    @pytest.mark.asyncio
    async def test_all_valid_dml_allowed(self):
        """全部 admin 合法 DML（带 WHERE）→ 放行。"""
        result = await self._check(
            [
                "INSERT INTO orders (id, qty) VALUES (1, 10)",
                "UPDATE inventory SET stock = stock - 10 WHERE sku = 'A'",
                "DELETE FROM tmp WHERE created_at < NOW()",
            ]
        )
        assert not result.blocked

    @pytest.mark.asyncio
    async def test_readonly_user_blocked(self):
        """readonly 发事务写 → 逐条审计拦 SQL_AUDIT_BLOCKED。"""
        result = await self._check(["INSERT INTO t (id) VALUES (1)"], user_role="readonly")
        assert result.blocked
        assert result.block_code == "SQL_AUDIT_BLOCKED"

    @pytest.mark.asyncio
    async def test_fail_open_on_audit_exception(self):
        """逐条审计异常 → fail-open 放行带 warning（与单语句语义一致）。"""
        stage = TransactionAuditStage(
            allowed_stmt_types=("INSERT", "UPDATE", "DELETE"),
            require_where=True,
        )
        with patch("app.engine.sql_auditor.audit", side_effect=RuntimeError("boom")):
            result = await stage.check(
                _tc(
                    "execute_write_transaction",
                    {"statements": ["INSERT INTO t VALUES (1)"]},
                    "call_tx",
                ),
                _conn(),
                _ctx(),
            )
        assert not result.blocked
        assert result.warnings


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
        with patch(
            "app.agent.security.stages._create_temp_adapter",
            AsyncMock(return_value=_mock_adapter()),
        ):
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
        with patch(
            "app.agent.security.stages._create_temp_adapter",
            AsyncMock(return_value=_mock_adapter()),
        ):
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
        with patch(
            "app.agent.security.stages._create_temp_adapter",
            AsyncMock(return_value=adapter),
        ):
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
        with patch(
            "app.agent.security.stages._create_temp_adapter",
            AsyncMock(side_effect=RuntimeError("no conn")),
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
        with patch(
            "app.agent.security.stages._create_temp_adapter",
            AsyncMock(return_value=adapter),
        ):
            result = await stage.check(
                _tc("execute_readonly_sql", {"sql": "SELECT * FROM t WHERE id = 1"}, "call_1"),
                _conn(),
                _ctx(),
            )
        assert not result.blocked


# =============================================================================
# ImpactEstimateStage（write-impact-estimate-plan Task 3）
# =============================================================================

# MySQL EXPLAIN JSON：UPDATE 命中 5000 行（< WRITE_IMPACT_WARN_ROWS，high_impact=False）
_IMPACT_EXPLAIN = (
    '{"query_block": {"select_id": 1, "table": {"table_name": "t", '
    '"access_type": "range", "rows_examined_per_scan": 5000, '
    '"filtered": 100.0}}}'
)


def _impact_adapter(explain_output=None, explain_error=None):
    """构造 mock 适配器（explain 返回固化样本 / 抛错）。"""
    adapter = AsyncMock()
    if explain_error is not None:
        adapter.explain.side_effect = explain_error
    else:
        adapter.explain.return_value = {
            "explain_output": explain_output or _IMPACT_EXPLAIN,
            "format": "json",
        }
    return adapter


class TestImpactEstimateStage:
    """ImpactEstimateStage：只读 EXPLAIN → ctx.evidence[tool_call_id]["impact"]。

    恒不拦截（信息增强阶段）；EXPLAIN 失败 / 无法预估 → 不写 evidence，不影响审批。
    """

    @pytest.mark.asyncio
    async def test_single_update_writes_evidence_and_never_blocks(self):
        """单语句 UPDATE：EXPLAIN 成功后 evidence 写入预估行数，阶段恒放行。"""
        stage = ImpactEstimateStage()
        ctx = _ctx(user_role="admin")
        adapter = _impact_adapter()
        with patch(
            "app.agent.security.stages._create_temp_adapter",
            AsyncMock(return_value=adapter),
        ):
            result = await stage.check(
                _tc("execute_write_sql", {"sql": "UPDATE t SET a=1 WHERE id>0"}, "call_w"),
                _conn(user_role="admin"),
                ctx,
            )
        assert not result.blocked  # 信息增强：永不拦截
        impact = ctx.evidence["call_w"]["impact"]
        assert impact["available"] is True
        assert impact["stmt_type"] == "UPDATE"
        assert impact["target_table"] == "t"
        assert impact["estimated_rows"] == 5000
        assert impact["high_impact"] is False
        assert impact["method"] == "explain"

    @pytest.mark.asyncio
    async def test_explain_failure_writes_no_evidence(self):
        """EXPLAIN 失败 → fail-open：不写 evidence、恒放行（不影响审批）。"""
        stage = ImpactEstimateStage()
        ctx = _ctx(user_role="admin")
        adapter = _impact_adapter(explain_error=RuntimeError("db down"))
        with patch(
            "app.agent.security.stages._create_temp_adapter",
            AsyncMock(return_value=adapter),
        ):
            result = await stage.check(
                _tc("execute_write_sql", {"sql": "UPDATE t SET a=1 WHERE id>0"}, "call_w"),
                _conn(user_role="admin"),
                ctx,
            )
        assert not result.blocked
        assert "call_w" not in ctx.evidence

    @pytest.mark.asyncio
    async def test_literal_insert_skipped_no_explain(self):
        """字面量 INSERT（VALUES）→ 无法预估：跳过且不尝试 EXPLAIN。"""
        stage = ImpactEstimateStage()
        ctx = _ctx(user_role="admin")
        adapter = _impact_adapter()
        with patch(
            "app.agent.security.stages._create_temp_adapter",
            AsyncMock(return_value=adapter),
        ) as mock_create:
            result = await stage.check(
                _tc("execute_write_sql", {"sql": "INSERT INTO t (id) VALUES (1)"}, "call_w"),
                _conn(user_role="admin"),
                ctx,
            )
        assert not result.blocked
        assert "call_w" not in ctx.evidence
        # 静态判定即可跳过，未创建临时适配器
        mock_create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_transaction_per_statement_estimate(self):
        """事务写工具：逐条预估——字面量 INSERT 无预估、UPDATE 有预估。"""
        stage = ImpactEstimateStage()
        ctx = _ctx(user_role="admin")
        statements = [
            "INSERT INTO orders (id, qty) VALUES (1, 10)",
            "UPDATE t SET a=1 WHERE id>0",
        ]
        adapter = _impact_adapter()
        with patch(
            "app.agent.security.stages._create_temp_adapter",
            AsyncMock(return_value=adapter),
        ):
            result = await stage.check(
                _tc("execute_write_transaction", {"statements": statements}, "call_tx"),
                _conn(user_role="admin"),
                ctx,
            )
        assert not result.blocked
        impact = ctx.evidence["call_tx"]["impact"]
        assert impact["available"] is True
        assert impact["stmt_count"] == 2
        per = impact["per_statement"]
        assert len(per) == 2
        # 第 1 条：字面量 INSERT 无法预估
        assert per[0]["idx"] == 1
        assert per[0]["estimated_rows"] is None
        # 第 2 条：UPDATE 命中 5000 行
        assert per[1]["idx"] == 2
        assert per[1]["stmt_type"] == "UPDATE"
        assert per[1]["estimated_rows"] == 5000
        assert impact["high_impact"] is False


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

    def test_build_action_injects_sql_for_transaction(self):
        """事务写工具：statements 列表 → 注入 details.sql（前端一个代码块展示全部 SQL）。"""
        tc = _tc(
            "execute_write_transaction",
            {
                "statements": [
                    "INSERT INTO orders (id) VALUES (1)",
                    "UPDATE inventory SET stock=1 WHERE id=1",
                ]
            },
            "call_tx",
        )
        action = ConfirmStage.build_action(tc, category="sql_write")
        assert action["tool"] == "execute_write_transaction"
        assert action["category"] == "sql_write"
        assert action["details"]["sql"] == (
            "INSERT INTO orders (id) VALUES (1);\nUPDATE inventory SET stock=1 WHERE id=1"
        )
        assert action["details"]["statements"] == [
            "INSERT INTO orders (id) VALUES (1)",
            "UPDATE inventory SET stock=1 WHERE id=1",
        ]

    def test_build_action_no_injection_for_other_tools(self):
        """非 statements 参数的工具（execute_write_sql / kill_transaction）不注入 sql。"""
        # execute_write_sql：本身有 sql 字段，不受注入逻辑影响
        tc_w = _tc("execute_write_sql", {"sql": "INSERT INTO t VALUES (1)"}, "call_w")
        action_w = ConfirmStage.build_action(tc_w, category="sql_write")
        assert action_w["details"] == {"sql": "INSERT INTO t VALUES (1)"}
        # kill_transaction：thread_id 参数，details 不含 sql
        tc_k = _tc("kill_transaction", {"thread_id": 12345}, "call_k")
        action_k = ConfirmStage.build_action(tc_k, category="connection_kill")
        assert "sql" not in action_k["details"]
        assert action_k["details"] == {"thread_id": 12345}
