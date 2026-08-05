"""
EXPLAIN 预估安全评估引擎单元测试。

覆盖三大模块：
  1. extract_metrics — MySQL / PostgreSQL / Oracle 解析器
  2. evaluate — 规则引擎决策
  3. truncate_result_for_llm — LLM 上下文窗口截断
"""

from __future__ import annotations

from app.engine.explain_estimator import (
    ExplainMetrics,
    _classify_access_pattern,
    evaluate,
    extract_metrics,
    truncate_result_for_llm,
)

# =============================================================================
# TestExtractMetrics — EXPLAIN 解析器测试
# =============================================================================


class TestExtractMetricsMySQL:
    """MySQL EXPLAIN FORMAT=JSON 解析测试。"""

    def test_simple_select(self) -> None:
        """基线：单表 SELECT。"""
        json_str = """{
            "query_block": {
                "select_id": 1,
                "cost_info": {"query_cost": "100.50"},
                "table": {
                    "table_name": "t1",
                    "access_type": "ALL",
                    "rows_examined_per_scan": 1000000,
                    "rows_produced_per_join": 10000,
                    "filtered": "1.00",
                    "cost_info": {"query_cost": "100.50"},
                    "used_columns": ["id", "name", "status"]
                }
            }
        }"""
        result = extract_metrics(json_str, "mysql")
        assert result is not None
        assert result.access_pattern == "FULL_SCAN"
        assert result.estimated_rows_examined == 1_000_000
        assert result.estimated_rows_output == 10_000
        assert result.row_width_bytes == 150  # 3 columns × 50
        assert result.query_cost == 100.5
        assert result.extra_operations == []

    def test_index_range_scan(self) -> None:
        """索引 Range 扫描（没有 ALL 时取 range）。"""
        json_str = """{
            "query_block": {
                "table": {
                    "access_type": "range",
                    "key": "idx_order_date",
                    "rows_examined_per_scan": 500
                }
            }
        }"""
        result = extract_metrics(json_str, "mysql")
        assert result is not None
        assert result.access_pattern == "INDEX_RANGE"
        assert result.estimated_rows_examined == 500

    def test_index_lookup(self) -> None:
        """索引等值查找（ref）。"""
        json_str = """{
            "query_block": {
                "table": {
                    "access_type": "ref",
                    "key": "idx_user_id",
                    "rows_examined_per_scan": 10
                }
            }
        }"""
        result = extract_metrics(json_str, "mysql")
        assert result is not None
        assert result.access_pattern == "INDEX_LOOKUP"
        assert result.estimated_rows_examined == 10

    def test_join_multi_table(self) -> None:
        """多表 JOIN 取最大 rows_examined_per_scan。"""
        json_str = """{
            "query_block": {
                "cost_info": {"query_cost": "5000.00"},
                "nested_loop": [
                    {
                        "table": {
                            "table_name": "orders",
                            "access_type": "ALL",
                            "rows_examined_per_scan": 500000
                        }
                    },
                    {
                        "table": {
                            "table_name": "order_items",
                            "access_type": "ref",
                            "rows_examined_per_scan": 2000000
                        }
                    }
                ]
            }
        }"""
        result = extract_metrics(json_str, "mysql")
        assert result is not None
        assert result.estimated_rows_examined == 2_000_000  # 取最大值
        assert result.access_pattern == "FULL_SCAN"
        assert result.query_cost == 5000.0

    def test_filesort_and_temporary(self) -> None:
        """filesort + temporary table。"""
        json_str = """{
            "query_block": {
                "ordering_operation": {
                    "using_filesort": true,
                    "table": {
                        "table_name": "t",
                        "access_type": "ALL",
                        "rows_examined_per_scan": 10000,
                        "using_temporary_table": true
                    }
                }
            }
        }"""
        result = extract_metrics(json_str, "mysql")
        assert result is not None
        assert "filesort" in result.extra_operations
        assert "temporary" in result.extra_operations

    def test_const_access(self) -> None:
        """常量访问（主键等值）。"""
        json_str = """{
            "query_block": {
                "table": {
                    "access_type": "const",
                    "rows_examined_per_scan": 1
                }
            }
        }"""
        result = extract_metrics(json_str, "mysql")
        assert result is not None
        assert result.access_pattern == "CONST"
        assert result.estimated_rows_examined == 1

    def test_count_star_with_rows_fallback(self) -> None:
        """COUNT(*) + covering index 使用 'rows' 而非 'rows_examined_per_scan'。"""
        json_str = """{
            "query_block": {
                "select_id": 1,
                "table": {
                    "table_name": "p_list",
                    "access_type": "index",
                    "key": "type",
                    "rows": 6363723,
                    "filtered": 100,
                    "using_index": true
                }
            }
        }"""
        result = extract_metrics(json_str, "mysql")
        assert result is not None
        assert result.access_pattern == "FULL_INDEX_SCAN"
        assert result.estimated_rows_examined == 6_363_723
        assert result.estimated_rows_output == 6_363_723  # 无 rows_produced_per_join 时 fallback

    def test_malformed_json(self) -> None:
        """格式错误的 JSON 返回 None。"""
        assert extract_metrics("not json", "mysql") is None
        assert extract_metrics("", "mysql") is None
        assert extract_metrics("{}", "mysql") is None


class TestExtractMetricsPostgreSQL:
    """PostgreSQL EXPLAIN (FORMAT JSON) 解析测试。"""

    def test_seq_scan(self) -> None:
        """顺序扫描。"""
        json_str = """[{
            "Plan": {
                "Node Type": "Seq Scan",
                "Relation Name": "orders",
                "Alias": "orders",
                "Startup Cost": 0.00,
                "Total Cost": 4587.50,
                "Plan Rows": 500000,
                "Plan Width": 64
            }
        }]"""
        result = extract_metrics(json_str, "postgresql")
        assert result is not None
        assert result.access_pattern == "FULL_SCAN"
        assert result.estimated_rows_examined == 500_000
        assert result.row_width_bytes == 64
        assert result.query_cost == 4587.5

    def test_index_scan(self) -> None:
        """索引扫描。"""
        json_str = """[{
            "Plan": {
                "Node Type": "Index Scan",
                "Relation Name": "users",
                "Index Name": "users_email_idx",
                "Startup Cost": 0.44,
                "Total Cost": 8.28,
                "Plan Rows": 10,
                "Plan Width": 32
            }
        }]"""
        result = extract_metrics(json_str, "postgresql")
        assert result is not None
        assert result.access_pattern == "INDEX_LOOKUP"
        assert result.estimated_rows_examined == 10

    def test_sort_node(self) -> None:
        """含 Sort 节点。"""
        json_str = """[{
            "Plan": {
                "Node Type": "Sort",
                "Startup Cost": 813.32,
                "Total Cost": 837.48,
                "Plan Rows": 9664,
                "Plan Width": 32,
                "Sort Key": ["name"],
                "Plans": [{
                    "Node Type": "Seq Scan",
                    "Relation Name": "t",
                    "Startup Cost": 0.00,
                    "Total Cost": 173.64,
                    "Plan Rows": 9664,
                    "Plan Width": 32
                }]
            }
        }]"""
        result = extract_metrics(json_str, "postgresql")
        assert result is not None
        assert result.access_pattern == "FULL_SCAN"
        assert "filesort" in result.extra_operations
        assert result.estimated_rows_examined == 9664

    def test_hash_join(self) -> None:
        """Hash Join + 嵌套计划。"""
        json_str = """[{
            "Plan": {
                "Node Type": "Hash Join",
                "Startup Cost": 100.00,
                "Total Cost": 5000.00,
                "Plan Rows": 10000,
                "Plans": [
                    {"Node Type": "Seq Scan", "Relation Name": "a", "Plan Rows": 5000, "Plan Width": 32},
                    {"Node Type": "Hash", "Plans": [
                        {"Node Type": "Index Scan", "Relation Name": "b", "Plan Rows": 100, "Plan Width": 32}
                    ]}
                ]
            }
        }]"""  # noqa: E501
        result = extract_metrics(json_str, "postgresql")
        assert result is not None
        assert result.access_pattern == "FULL_SCAN"  # 最深的扫描是 Seq Scan
        assert "hash_join" in result.extra_operations
        assert result.estimated_rows_examined == 10_000

    def test_malformed(self) -> None:
        """格式错误返回 None。"""
        assert extract_metrics("[]", "postgresql") is None
        assert extract_metrics("", "postgresql") is None


class TestExtractMetricsOracle:
    """Oracle DBMS_XPLAN 文本解析测试。"""

    ORACLE_OUTPUT = """-----------------------------------------------------------------------------------------
| Id  | Operation              | Name       | Rows  | Bytes | Cost (%CPU)| Time     |
-----------------------------------------------------------------------------------------
|   0 | SELECT STATEMENT       |            |     1 |    57 |     6  (34)| 00:00:01 |
|*  1 |  TABLE ACCESS FULL     | EMP        |     1 |    37 |     3  (34)| 00:00:01 |
|   2 |  TABLE ACCESS FULL     | DEPT       |     4 |    80 |     3  (34)| 00:00:01 |
-----------------------------------------------------------------------------------------"""  # noqa: E501

    def test_full_scan(self) -> None:
        """全表扫描。"""
        result = extract_metrics(self.ORACLE_OUTPUT, "oracle")
        assert result is not None
        assert result.access_pattern == "FULL_SCAN"
        assert result.estimated_rows_examined == 4  # max of {1, 1, 4}
        assert result.estimated_rows_output == 1  # Id=0 的 rows
        assert result.row_width_bytes >= 10  # Bytes/Rows 取整
        assert result.query_cost == 6.0

    def test_k_m_suffix(self) -> None:
        """K/M 后缀解析。"""
        output = """-----------------------------------------------------------------------------------------
| Id  | Operation              | Name  | Rows  | Bytes | Cost (%CPU)| Time     |
-----------------------------------------------------------------------------------------
|   0 | SELECT STATEMENT       |       |  100K |  10M  |  5000  (1)| 00:00:20 |
|   1 |  TABLE ACCESS FULL     | BIG   |   50K |   5M  |  5000  (1)| 00:00:20 |
-----------------------------------------------------------------------------------------"""  # noqa: E501
        result = extract_metrics(output, "oracle")
        assert result is not None
        assert result.estimated_rows_examined == 100_000  # max of {100K, 50K}
        assert result.estimated_rows_output == 100_000

    def test_index_range_scan(self) -> None:
        """索引范围扫描。"""
        output = """-----------------------------------------------------------------------------------------
| Id  | Operation                  | Name        | Rows  | Bytes | Cost (%CPU)| Time     |
-----------------------------------------------------------------------------------------
|   0 | SELECT STATEMENT           |             |  100  |  900  |    52   (8)| 00:00:01 |
|   1 |  TABLE ACCESS BY INDEX ROWID| ORDERS     |  100  |  900  |    52   (8)| 00:00:01 |
|*  2 |   INDEX RANGE SCAN         | IDX_CREATED |  100  |       |     2   (0)| 00:00:01 |
-----------------------------------------------------------------------------------------"""  # noqa: E501
        result = extract_metrics(output, "oracle")
        assert result is not None
        assert result.access_pattern == "INDEX_LOOKUP"  # TABLE ACCESS BY INDEX ROWID
        assert result.estimated_rows_examined == 100

    def test_no_data_rows(self) -> None:
        """无数据行返回 None。"""
        output = """no data found"""
        assert extract_metrics(output, "oracle") is None
        assert extract_metrics("", "oracle") is None


# =============================================================================
# TestClassifyAccessPattern — 跨数据库访问方式分类
# =============================================================================


class TestClassifyAccessPattern:
    """访问方式统一分类测试。"""

    def test_mysql_mapping(self) -> None:
        """MySQL 全量 access_type 映射。"""
        inputs = {
            "ALL": "FULL_SCAN",
            "INDEX": "FULL_INDEX_SCAN",
            "INDEX_MERGE": "INDEX_RANGE",
            "RANGE": "INDEX_RANGE",
            "REF": "INDEX_LOOKUP",
            "EQ_REF": "INDEX_LOOKUP",
            "CONST": "CONST",
            "SYSTEM": "CONST",
        }
        for raw, expected in inputs.items():
            assert _classify_access_pattern(raw, "mysql") == expected

    def test_pg_mapping(self) -> None:
        """PostgreSQL 全量映射。"""
        inputs = {
            "SEQ SCAN": "FULL_SCAN",
            "INDEX FULL SCAN": "FULL_INDEX_SCAN",
            "INDEX RANGE SCAN": "INDEX_RANGE",
            "INDEX SCAN": "INDEX_LOOKUP",
            "INDEX ONLY SCAN": "INDEX_LOOKUP",
        }
        for raw, expected in inputs.items():
            assert _classify_access_pattern(raw, "postgresql") == expected

    def test_oracle_mapping(self) -> None:
        """Oracle 全量映射。"""
        inputs = {
            "TABLE ACCESS FULL": "FULL_SCAN",
            "INDEX FULL SCAN": "FULL_INDEX_SCAN",
            "INDEX RANGE SCAN": "INDEX_RANGE",
            "INDEX UNIQUE SCAN": "INDEX_LOOKUP",
            "TABLE ACCESS BY INDEX ROWID": "INDEX_LOOKUP",
            "SORT ORDER BY": "UNKNOWN",
        }
        for raw, expected in inputs.items():
            assert _classify_access_pattern(raw, "oracle") == expected

    def test_unknown_input(self) -> None:
        """未知输入返回 UNKNOWN。"""
        assert _classify_access_pattern("", "mysql") == "UNKNOWN"
        assert _classify_access_pattern("BOGUS", "mysql") == "UNKNOWN"
        assert _classify_access_pattern(None, "mysql") == "UNKNOWN"  # type: ignore[arg-type]

    def test_unsupported_db(self) -> None:
        """不支持的 DB 类型返回 UNKNOWN。"""
        assert _classify_access_pattern("ALL", "sqlite") == "UNKNOWN"


# =============================================================================
# TestEvaluate — 规则引擎决策测试
# =============================================================================


class TestEvaluate:
    """规则引擎评估决策测试 — 7 条 CRITICAL 规则。"""

    # ── R1: FULL_SCAN + 大行数 ──
    def test_r1_full_scan_block(self) -> None:
        """R1: FULL_SCAN + 60K 行 → CRITICAL。"""
        m = ExplainMetrics("FULL_SCAN", 60_000, 100)
        d = evaluate(m)
        assert not d.allowed
        assert d.risk_level == "CRITICAL"

    def test_r1_full_scan_below_threshold(self) -> None:
        """R1: FULL_SCAN + 30K 行（<50K）→ LOW。"""
        m = ExplainMetrics("FULL_SCAN", 30_000, 100)
        d = evaluate(m)
        assert d.allowed

    # ── R2: FULL_SCAN + 高成本 ──
    def test_r2_full_scan_costly(self) -> None:
        """R2: FULL_SCAN + cost 20000 → CRITICAL。"""
        m = ExplainMetrics("FULL_SCAN", 3_000, 100, query_cost=20_000.0)
        d = evaluate(m)
        assert not d.allowed
        assert d.risk_level == "CRITICAL"

    def test_r2_full_scan_low_cost(self) -> None:
        """R2: FULL_SCAN + cost 5000（<10000）→ 不触发 R2。"""
        m = ExplainMetrics("FULL_SCAN", 3_000, 100, query_cost=5_000.0)
        d = evaluate(m)
        assert d.allowed

    # ── R3: FULL_INDEX_SCAN ──
    def test_r3_full_index_scan_block(self) -> None:
        """R3: FULL_INDEX_SCAN + 150K 行 → CRITICAL。"""
        m = ExplainMetrics("FULL_INDEX_SCAN", 150_000, 150_000)
        d = evaluate(m)
        assert not d.allowed
        assert d.risk_level == "CRITICAL"

    def test_r3_full_index_scan_below(self) -> None:
        """R3: FULL_INDEX_SCAN + 50K 行（<100K）→ LOW。"""
        m = ExplainMetrics("FULL_INDEX_SCAN", 50_000, 50_000)
        d = evaluate(m)
        assert d.allowed

    # ── R4: INDEX_RANGE ──
    def test_r4_index_range_block(self) -> None:
        """R4: INDEX_RANGE + 600K 行 → CRITICAL。"""
        m = ExplainMetrics("INDEX_RANGE", 600_000, 100)
        d = evaluate(m)
        assert not d.allowed
        assert d.risk_level == "CRITICAL"

    def test_r4_index_range_below(self) -> None:
        """R4: INDEX_RANGE + 200K 行（<500K）→ LOW。"""
        m = ExplainMetrics("INDEX_RANGE", 200_000, 100)
        d = evaluate(m)
        assert d.allowed

    # ── R5: INDEX_LOOKUP + CONST ──
    def test_r5_lookup_huge_block(self) -> None:
        """R5: INDEX_LOOKUP + 600K 行 → CRITICAL。"""
        m = ExplainMetrics("INDEX_LOOKUP", 600_000, 100)
        d = evaluate(m)
        assert not d.allowed
        assert d.risk_level == "CRITICAL"

    def test_r5_const_huge_block(self) -> None:
        """R5: CONST + 600K 行（极端场景）→ CRITICAL。"""
        m = ExplainMetrics("CONST", 600_000, 100)
        d = evaluate(m)
        assert not d.allowed
        assert d.risk_level == "CRITICAL"

    def test_r5_lookup_below_threshold(self) -> None:
        """R5: INDEX_LOOKUP + 200K 行（<500K）→ LOW。"""
        m = ExplainMetrics("INDEX_LOOKUP", 200_000, 100)
        d = evaluate(m)
        assert d.allowed

    # ── R6: UNKNOWN 保守处理 ──
    def test_r6_unknown_block(self) -> None:
        """R6: UNKNOWN + 150K 行 → CRITICAL。"""
        m = ExplainMetrics("UNKNOWN", 150_000, 100)
        d = evaluate(m)
        assert not d.allowed
        assert d.risk_level == "CRITICAL"

    def test_r6_unknown_below(self) -> None:
        """R6: UNKNOWN + 50K 行（<100K）→ LOW。"""
        m = ExplainMetrics("UNKNOWN", 50_000, 100)
        d = evaluate(m)
        assert d.allowed

    # ── R7: 临时表（横切维度） ──
    def test_r7_temptable_block(self) -> None:
        """R7: temp table + 250K 行 → CRITICAL。"""
        m = ExplainMetrics("INDEX_RANGE", 250_000, 250_000, extra_operations=["temporary"])
        d = evaluate(m)
        assert not d.allowed
        assert d.risk_level == "CRITICAL"

    def test_r7_temptable_below_threshold(self) -> None:
        """R7: temp table + 100K 行（<200K）→ LOW。"""
        m = ExplainMetrics("INDEX_RANGE", 100_000, 100_000, extra_operations=["temporary"])
        d = evaluate(m)
        assert d.allowed

    def test_r7_temptable_fulled_scan_also_blocked(self) -> None:
        """R7: FULL_SCAN + temp + 250K → R1 先命中（短路）。"""
        m = ExplainMetrics("FULL_SCAN", 250_000, 250_000, extra_operations=["temporary"])
        d = evaluate(m)
        assert not d.allowed
        # R1 先匹配，只返回 1 条原因
        assert len(d.reasons) == 1

    # ── 边界：全 PASS ──
    def test_all_pass_low_risk(self) -> None:
        """INDEX_LOOKUP + 10 行 + 无额外操作 → LOW。"""
        m = ExplainMetrics("INDEX_LOOKUP", 10, 10)
        d = evaluate(m)
        assert d.allowed
        assert d.risk_level == "LOW"

    # ── filesort 不影响（无临时表不触发 R7） ──
    def test_filesort_no_temptable_low(self) -> None:
        """filesort + 60K 行（无 temporary）→ LOW。"""
        m = ExplainMetrics("INDEX_RANGE", 60_000, 100, extra_operations=["filesort"])
        d = evaluate(m)
        assert d.allowed


# =============================================================================
# TestTruncateResult — LLM 上下文窗口截断
# =============================================================================


class TestTruncateResult:
    """LLM 上下文保护截断测试。"""

    def test_small_result_unchanged(self) -> None:
        """50 行结果不截断。"""
        result = {
            "columns": ["id", "name"],
            "rows": [{"id": i, "name": f"user{i}"} for i in range(50)],
            "total_rows": 50,
        }
        truncated = truncate_result_for_llm(result)
        assert truncated is result  # 原样返回，不创建新 dict
        assert "_truncated" not in truncated

    def test_over_row_limit(self) -> None:
        """500 行截断到 _MAX_LLM_RESULT_ROWS（100）行。"""
        result = {
            "columns": ["id"],
            "rows": [{"id": i} for i in range(500)],
            "total_rows": 500,
        }
        truncated = truncate_result_for_llm(result)
        assert truncated["_truncated"] is True
        assert truncated["_original_total_rows"] == 500
        assert truncated["_truncated_to"] == 100
        assert len(truncated["rows"]) == 100

    def test_truncation_metadata(self) -> None:
        """截断后包含正确元信息。"""
        result = {
            "columns": ["id"],
            "rows": [{"id": i} for i in range(300)],
            "total_rows": 300,
        }
        truncated = truncate_result_for_llm(result)
        assert "_truncated" in truncated
        assert "_original_total_rows" in truncated
        assert "_truncated_to" in truncated

    def test_empty_rows(self) -> None:
        """空 rows 列表不截断。"""
        result = {"columns": ["id"], "rows": [], "total_rows": 0}
        truncated = truncate_result_for_llm(result)
        assert truncated is result

    def test_non_dict_result(self) -> None:
        """非 dict 输入直接返回。"""
        assert truncate_result_for_llm("string") == "string"  # type: ignore[arg-type]
        assert truncate_result_for_llm(None) is None  # type: ignore[arg-type]
        assert truncate_result_for_llm([]) == []  # type: ignore[arg-type]


class TestTruncateGenericResult:
    """无 rows 键结果的通用体积截断（context-compression-plan 第二层配套）。"""

    def test_small_non_rows_result_unchanged(self) -> None:
        """小体积非 rows 结果原样返回，无标记。"""
        result = {"status": "pass", "summary": "无锁等待", "total_waiting": 0}
        assert truncate_result_for_llm(result) is result

    def test_empty_rows_unchanged(self) -> None:
        """rows 为空的结果原样返回（不误触通用截断打标记）。"""
        result = {"columns": ["id"], "rows": [], "total_rows": 0}
        assert truncate_result_for_llm(result) is result

    def test_huge_explain_output_truncated(self) -> None:
        """巨型 explain_output 截到指定阈值并带 _truncated 标记。"""
        result = {"explain_output": "x" * 200_000, "format": "json", "summary": "s"}
        truncated = truncate_result_for_llm(result, max_chars=8_000)
        import json

        serialized = json.dumps(truncated, ensure_ascii=False)
        assert truncated["_truncated"] is True
        assert truncated["_truncated_to_chars"] == 8_000
        assert len(serialized) <= 8_000 + 500, f"截断后应接近阈值，实际 {len(serialized)}"

    def test_large_items_list_reduced(self) -> None:
        """超限 items 列表按半数递减（get_slow_queries include_explain 场景）。"""
        items = [
            {"sql_text": "SELECT " + "x" * 1_200, "query_time_sec": i} for i in range(50)
        ]
        result = {"items": items, "total": len(items)}
        truncated = truncate_result_for_llm(result, max_chars=40_000)
        assert truncated["_truncated"] is True
        assert len(truncated["items"]) < len(items), "items 应被缩减"
        assert truncated["items"][0]["sql_text"] == items[0]["sql_text"], "剩余条目内容完整"

    def test_rows_behavior_unchanged_with_custom_max_chars(self) -> None:
        """rows 结果沿用原逻辑，max_chars 参数化后仍截断。"""
        result = {
            "columns": ["id"],
            "rows": [{"id": i} for i in range(300)],
            "total_rows": 300,
        }
        truncated = truncate_result_for_llm(result, max_chars=40_000)
        assert truncated["_truncated"] is True
        assert len(truncated["rows"]) == 100
