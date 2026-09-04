"""
write_impact 引擎单测（write-impact-estimate-plan Task 1 验收）。

用三段固化的执行计划样本（PG JSON / MySQL JSON / Oracle TYPICAL text）验证
extract_write_impact() 的提取逻辑 + 边界 + 失败降级。样本为贴近真实 DB 输出格式
的固化结构，字段名以真实 DB 对拍为准（实现注释已标注）。
"""

from __future__ import annotations

from app.engine.write_impact import (
    WRITE_IMPACT_WARN_ROWS,
    classify_write,
    extract_write_impact,
)

# =============================================================================
# 固化 EXPLAIN 样本
# =============================================================================

# PostgreSQL: EXPLAIN (FORMAT JSON) UPDATE users SET status='active' WHERE status='inactive'
PG_UPDATE_JSON = """[
  {
    "Plan": {
      "Node Type": "Update",
      "Relation Name": "users",
      "Schema": "public",
      "Alias": "users",
      "Startup Cost": 0.00,
      "Total Cost": 1250.43,
      "Plans": [
        {
          "Node Type": "Seq Scan",
          "Parent Relationship": "Outer",
          "Relation Name": "users",
          "Alias": "users",
          "Startup Cost": 0.00,
          "Total Cost": 1250.43,
          "Plan Rows": 12430,
          "Plan Width": 57,
          "Filter": "(status = 'inactive'::text)"
        }
      ]
    }
  }
]"""

# PostgreSQL: DELETE 子树顶层带 Plan Rows 的变体（ModifyTable 自带 Plan Rows）
PG_DELETE_JSON = """[
  {
    "Plan": {
      "Node Type": "Delete",
      "Relation Name": "audit_log",
      "Schema": "public",
      "Startup Cost": 0.00,
      "Total Cost": 800.00,
      "Plan Rows": 500,
      "Plans": [
        {
          "Node Type": "Index Scan",
          "Parent Relationship": "Outer",
          "Relation Name": "audit_log",
          "Index Name": "idx_audit_created",
          "Startup Cost": 0.00,
          "Total Cost": 800.00,
          "Plan Rows": 500,
          "Plan Width": 24,
          "Index Cond": "(created_at < '2024-01-01'::date)"
        }
      ]
    }
  }
]"""

# MySQL: EXPLAIN FORMAT=JSON UPDATE（8.0.19+），range 访问 + filtered=33.33
MYSQL_UPDATE_JSON = """{
  "query_block": {
    "select_id": 1,
    "table": {
      "table_name": "users",
      "access_type": "range",
      "possible_keys": ["idx_status"],
      "key": "idx_status",
      "used_key_parts": ["status"],
      "key_length": "1",
      "rows_examined_per_scan": 37290,
      "rows_produced_per_join": 12430,
      "filtered": 33.33,
      "index_condition": "(`users`.`status` = 'inactive')"
    }
  }
}"""

# Oracle: EXPLAIN PLAN FOR UPDATE + DBMS_XPLAN TYPICAL 文本
ORACLE_UPDATE_TEXT = """Plan hash value: 3617692013

------------------------------------------------------------------------------
| Id  | Operation            | Name   | Rows  | Bytes | Cost (%CPU)| Time     |
------------------------------------------------------------------------------
|   0 | UPDATE STATEMENT     |        |  1243 | 66033 |   150  (2) | 00:00:02 |
|   1 |  TABLE ACCESS FULL   | USERS  |  1243 | 66033 |   150  (2) | 00:00:02 |
------------------------------------------------------------------------------

Note
-----
   - dynamic statistics used: dynamic sampling (level=2)
"""


# =============================================================================
# classify_write
# =============================================================================


class TestClassifyWrite:
    def test_update_target(self) -> None:
        info = classify_write("UPDATE users SET status = 1 WHERE id = 5", "mysql")
        assert info is not None
        assert info["stmt_type"] == "UPDATE"
        assert info["target_table"] == "users"
        assert info["has_select_source"] is False

    def test_delete_target(self) -> None:
        info = classify_write("DELETE FROM orders WHERE created_at < '2020-01-01'", "postgresql")
        assert info is not None
        assert info["stmt_type"] == "DELETE"
        assert info["target_table"] == "orders"

    def test_insert_select_estimable(self) -> None:
        info = classify_write(
            "INSERT INTO archive SELECT * FROM orders WHERE created_at < '2020-01-01'",
            "mysql",
        )
        assert info is not None
        assert info["stmt_type"] == "INSERT"
        assert info["target_table"] == "archive"
        assert info["has_select_source"] is True

    def test_insert_literal_not_estimable(self) -> None:
        info = classify_write("INSERT INTO users (id, name) VALUES (1, 'a'), (2, 'b')", "mysql")
        assert info is not None
        assert info["has_select_source"] is False

    def test_select_not_write_returns_none(self) -> None:
        assert classify_write("SELECT * FROM users", "mysql") is None

    def test_multi_statement_returns_none(self) -> None:
        assert classify_write("UPDATE a SET x=1; UPDATE b SET y=2", "mysql") is None

    def test_parse_error_returns_none(self) -> None:
        assert classify_write("UPDATE (((", "mysql") is None


# =============================================================================
# extract_write_impact —— 各库样本
# =============================================================================


class TestExtractWriteImpact:
    def test_pg_update_takes_child_plan_rows(self) -> None:
        result = extract_write_impact(
            "UPDATE users SET status = 'active' WHERE status = 'inactive'",
            "postgresql",
            PG_UPDATE_JSON,
        )
        assert result is not None
        assert result["stmt_type"] == "UPDATE"
        assert result["target_table"] == "users"
        assert result["estimated_rows"] == 12430
        assert result["method"] == "explain"
        assert result["high_impact"] is True

    def test_pg_delete_uses_modifytable_own_rows(self) -> None:
        result = extract_write_impact(
            "DELETE FROM audit_log WHERE created_at < '2024-01-01'",
            "postgresql",
            PG_DELETE_JSON,
        )
        assert result is not None
        assert result["estimated_rows"] == 500
        assert result["high_impact"] is False

    def test_mysql_update_applies_filtered(self) -> None:
        result = extract_write_impact(
            "UPDATE users SET status = 1 WHERE status = 0",
            "mysql",
            MYSQL_UPDATE_JSON,
        )
        assert result is not None
        # 37290 × 33.33% ≈ 12430（用整数舍入断言为 12428~12431 区间更稳，直接按 round）
        assert 12_420 <= result["estimated_rows"] <= 12_440
        assert result["target_table"] == "users"
        assert result["high_impact"] is True

    def test_oracle_update_takes_statement_rows(self) -> None:
        result = extract_write_impact(
            "UPDATE users SET status = 1 WHERE status = 0", "oracle", ORACLE_UPDATE_TEXT
        )
        assert result is not None
        assert result["estimated_rows"] == 1243
        assert result["high_impact"] is False


class TestExtractWriteImpactDegradation:
    def test_insert_literal_returns_none(self) -> None:
        # 字面量 INSERT 无需 EXPLAIN 也无法预估 → None
        assert (
            extract_write_impact(
                "INSERT INTO users (id) VALUES (1)",
                "mysql",
                '{"query_block": {"select_id": 1}}',
            )
            is None
        )

    def test_empty_explain_returns_none(self) -> None:
        assert extract_write_impact("UPDATE users SET a=1 WHERE b=2", "mysql", "") is None

    def test_malformed_json_returns_none(self) -> None:
        assert extract_write_impact("UPDATE users SET a=1 WHERE b=2", "mysql", "{ not json") is None

    def test_pg_missing_dml_node_returns_none(self) -> None:
        select_plan = """[{"Plan": {"Node Type": "Seq Scan", "Plan Rows": 100}}]"""
        assert (
            extract_write_impact("UPDATE users SET a=1 WHERE b=2", "postgresql", select_plan)
            is None
        )

    def test_unsupported_db_type_returns_none(self) -> None:
        assert extract_write_impact("UPDATE users SET a=1 WHERE b=2", "sqlserver", "x") is None

    def test_non_write_sql_returns_none(self) -> None:
        assert extract_write_impact("SELECT 1", "mysql", "{}") is None


class TestHighImpactThreshold:
    def test_boundary_below(self) -> None:
        json_out = (
            '{"query_block": {"table": {"table_name": "users", "rows_examined_per_scan": '
            + str(WRITE_IMPACT_WARN_ROWS - 1)
            + ', "filtered": 100}}}'
        )
        result = extract_write_impact("UPDATE users SET a=1 WHERE b=2", "mysql", json_out)
        assert result is not None
        assert result["estimated_rows"] == WRITE_IMPACT_WARN_ROWS - 1
        assert result["high_impact"] is False

    def test_boundary_at(self) -> None:
        json_out = (
            '{"query_block": {"table": {"table_name": "users", "rows_examined_per_scan": '
            + str(WRITE_IMPACT_WARN_ROWS)
            + ', "filtered": 100}}}'
        )
        result = extract_write_impact("UPDATE users SET a=1 WHERE b=2", "mysql", json_out)
        assert result is not None
        assert result["high_impact"] is True
