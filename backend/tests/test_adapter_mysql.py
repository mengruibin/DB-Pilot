"""
MySQL 适配器层集成测试。

依据 AGENTS.md §验证与测试要求：
  - 每个适配器类型至少有一个集成测试（连接测试库 → 执行 → 断言返回结构）

覆盖操作：
  - connect / test_connection / disconnect
  - get_tables（元数据）
  - get_columns（列信息）
  - execute（查询）
  - explain（执行计划）

测试数据库通过环境变量 TEST_MYSQL_URL 配置。
未配置时自动 SKIP（非 FAIL）。
"""

from __future__ import annotations

import pytest

from app.db.factory import AdapterFactory

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
async def mysql_adapter(mysql_test_config):
    """创建并连接 MySQL 适配器，测试后自动断开。"""
    if mysql_test_config is None:
        pytest.skip("TEST_MYSQL_URL not configured")

    adapter = AdapterFactory.create("mysql", mysql_test_config)
    await adapter.connect(mysql_test_config)
    yield adapter
    await adapter.disconnect()


# =============================================================================
# 连接测试
# =============================================================================


@pytest.mark.integration
class TestMySQLAdapter:
    """MySQL 适配器集成测试套件。"""

    @pytest.mark.dependency(name="connect")
    async def test_mysql_connect(self, mysql_adapter) -> None:
        """AC-2：连接成功。"""
        assert mysql_adapter is not None
        # test_connection 发送 SELECT 1 心跳
        result = await mysql_adapter.test_connection()
        assert result is True

    @pytest.mark.dependency(name="get_tables", depends=["connect"])
    async def test_mysql_get_tables(self, mysql_adapter) -> None:
        """AC-3：返回非空表列表，每项含必需字段。"""
        # 获取当前数据库的表列表
        config = mysql_adapter._config  # type: ignore[attr-defined]
        tables = await mysql_adapter.get_tables(config.database)
        assert isinstance(tables, list)
        # 至少应存在表
        assert len(tables) > 0
        # 每项应包含必需字段
        required_keys = {"database", "table_name", "comment", "row_count_estimate"}
        for t in tables:
            assert required_keys.issubset(t.keys()), f"缺少字段：{required_keys - t.keys()}"

    @pytest.mark.dependency(name="get_columns", depends=["connect"])
    async def test_mysql_get_columns(self, mysql_adapter) -> None:
        """AC-4：指定表返回列列表，每项含 name/type/is_primary。"""
        config = mysql_adapter._config  # type: ignore[attr-defined]
        tables = await mysql_adapter.get_tables(config.database)
        assert len(tables) > 0

        # 取第一张表获取列信息
        first_table = tables[0]["table_name"]
        columns = await mysql_adapter.get_columns(config.database, first_table)

        assert isinstance(columns, list)
        assert len(columns) > 0

        # 每项应含必需字段
        for col in columns:
            assert "name" in col
            assert "type" in col
            assert "nullable" in col
            assert "is_primary" in col
            assert "comment" in col

    @pytest.mark.dependency(name="execute_select", depends=["connect"])
    async def test_mysql_execute_select(self, mysql_adapter) -> None:
        """AC-5：SELECT 1 返回正确结构。"""
        result = await mysql_adapter.execute("SELECT 1 AS test_col")
        assert "columns" in result
        assert "rows" in result
        assert result["columns"] == ["test_col"]
        assert len(result["rows"]) == 1
        assert result["rows"][0][0] == 1
        assert result["execution_time_ms"] >= 0
        assert result["is_readonly"] is True
        assert result["audit_status"] == "passed"

    @pytest.mark.dependency(name="explain", depends=["connect"])
    async def test_mysql_explain(self, mysql_adapter) -> None:
        """AC-6：EXPLAIN SELECT 1 返回非空字符串。"""
        explain_result = await mysql_adapter.explain("SELECT 1")
        assert "explain_output" in explain_result
        assert len(explain_result["explain_output"]) > 0
        assert "format" in explain_result

    @pytest.mark.dependency(name="stream_query", depends=["connect"])
    async def test_mysql_stream_query(self, mysql_adapter) -> None:
        """AC-7：stream_query 分批产出 (columns, rows_batch)。"""
        config = mysql_adapter._config  # type: ignore[attr-defined]
        tables = await mysql_adapter.get_tables(config.database)
        assert len(tables) > 0
        first_table = tables[0]["table_name"]
        sql = f"SELECT * FROM `{first_table}` LIMIT 1000"

        seen_columns = None
        row_count = 0
        batch_count = 0
        async for columns, batch in mysql_adapter.stream_query(sql, batch_size=10):
            if seen_columns is None:
                seen_columns = columns
            assert isinstance(columns, list)
            assert isinstance(batch, list)
            row_count += len(batch)
            batch_count += 1

        assert seen_columns is not None
        assert row_count >= 1
        if row_count > 10:
            assert batch_count > 1, "超过 batch_size 时应分批产出"

    @pytest.mark.dependency(name="stream_query_empty", depends=["connect"])
    async def test_mysql_stream_query_empty(self, mysql_adapter) -> None:
        """AC-8：空结果集也应产出一次（带列名），供导出端写表头。"""
        result = await mysql_adapter.execute("SELECT 1 AS a WHERE 1=0")
        assert result["columns"] == ["a"]
        # 空结果集：stream_query 应产出一次 (columns, [])
        batches = []
        async for columns, batch in mysql_adapter.stream_query(
            "SELECT 1 AS a WHERE 1=0", batch_size=10
        ):
            batches.append((columns, batch))
        assert len(batches) == 1
        assert batches[0][0] == ["a"]
        assert batches[0][1] == []
