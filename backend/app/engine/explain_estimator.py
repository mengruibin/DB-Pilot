"""
EXPLAIN 多维度安全评估引擎。

核心职责：
  1. 指标提取 — 解析 MySQL / PostgreSQL / Oracle 的 EXPLAIN 输出为标准化 ExplainMetrics
  2. 规则引擎 — 基于硬编码阈值的决策矩阵，CRITICAL 阻断 / LOW 放行（WARNING 级别已移除）
  3. 结果截断 — 保护 LLM 上下文窗口，截断超量查询结果

依据 docs/explain-row-estimation-plan.md 方案设计。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# =============================================================================
# 数据类型
# =============================================================================


@dataclass
class ExplainMetrics:
    """从 EXPLAIN 输出中提取的标准化评估指标。

    Attributes:
        access_pattern: 访问方式（FULL_SCAN / FULL_INDEX_SCAN / INDEX_RANGE / INDEX_LOOKUP / CONST / UNKNOWN）。
        estimated_rows_examined: 预估扫描行数（数据库 IO 负载指标）。
        estimated_rows_output: 预估返回行数（网络传输 + LLM 上下文负载指标）。
        row_width_bytes: 单行字节宽度（0 表示未知）。
        query_cost: 优化器成本值（跨数据库不可直接比较，仅同类型内参考）。
        extra_operations: 额外操作列表，如 ["filesort", "temporary", "hash_join"]。
    """

    access_pattern: str
    estimated_rows_examined: int
    estimated_rows_output: int
    row_width_bytes: int = 0
    query_cost: float | None = None
    extra_operations: list[str] = field(default_factory=list)


@dataclass
class ExplainDecision:
    """规则引擎评估决策结果。

    Attributes:
        allowed: 是否允许执行。
        risk_level: 风险级别（LOW / CRITICAL；WARNING 级别已移除）。
        reasons: 阻断或警告原因列表。
        metrics: 评估所用的指标（供上游日志记录）。
    """

    allowed: bool
    risk_level: str
    reasons: list[str]
    metrics: ExplainMetrics | None = None


# =============================================================================
# MySQL EXPLAIN FORMAT=JSON 解析
# =============================================================================


def _find_all_values(obj: object, target_key: str) -> list:
    """递归遍历 JSON 对象，收集所有指定键的值。

    Args:
        obj: 要遍历的 JSON 对象（dict / list / 标量）。
        target_key: 目标键名。

    Returns:
        匹配到的所有值列表（int/float/str/bool 混合，调用方自行过滤类型）。
    """
    values: list = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == target_key:
                values.append(v)
            else:
                values.extend(_find_all_values(v, target_key))
    elif isinstance(obj, list):
        for item in obj:
            values.extend(_find_all_values(item, target_key))
    return values


def _parse_mysql_explain(json_str: str) -> dict | None:
    """解析 MySQL EXPLAIN FORMAT=JSON 输出，提取多维度指标。

    递归遍历 JSON 树，从各 table 节点提取 rows_examined_per_scan 等指标。
    所有解析异常均返回 None，由调用方降级处理。

    Args:
        json_str: EXPLAIN FORMAT=JSON 输出的原始 JSON 字符串。

    Returns:
        包含结构化指标的 dict，或 None 表示解析失败。
    """
    import json

    try:
        root = json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(root, dict):
        return None

    # 提取顶层 query_block（可能多层嵌套，如 ordering_operation 包裹）
    def _unwind_to_query_block(node: dict) -> dict | None:
        """解包 ordering_operation / grouping_operation 等包装层，找到 query_block。"""
        for key in ("query_block",):
            if key in node:
                inner = node[key]
                if isinstance(inner, dict):
                    return inner
        # 也可能是直接就是 query_block
        if any(k.startswith("query_block") or k == "select_id" for k in node):
            return node
        return None

    qb = _unwind_to_query_block(root)
    if qb is None:
        return None

    # ── 1. 提取所有 table 节点的 rows_examined_per_scan 和 access_type ──
    all_rows_examined = _find_all_values(qb, "rows_examined_per_scan")
    numeric_examined = [int(v) for v in all_rows_examined if isinstance(v, (int, float))]
    est_rows_examined = max(numeric_examined) if numeric_examined else 0

    # Fallback: 某些 MySQL 版本/场景使用 "rows" 而非 "rows_examined_per_scan"
    # （如 COUNT(*) 且 Using index 时，EXPLAIN 输出简化为 "rows" 字段）
    if est_rows_examined == 0:
        all_rows_fb = _find_all_values(qb, "rows")
        numeric_fb = [int(v) for v in all_rows_fb if isinstance(v, (int, float))]
        est_rows_examined = max(numeric_fb) if numeric_fb else 0

    # ── 2. 提取 rows_produced_per_join ──
    all_rows_produced = _find_all_values(qb, "rows_produced_per_join")
    numeric_produced = [int(v) for v in all_rows_produced if isinstance(v, (int, float))]
    est_rows_output = max(numeric_produced) if numeric_produced else est_rows_examined

    # ── 3. 提取访问类型（取最严重的，即 ALL > index > range 等）
    all_access_types = _find_all_values(qb, "access_type")
    # 按严重程度排序：ALL 最严重
    access_type = "UNKNOWN"
    for at in all_access_types:
        if isinstance(at, str) and at.upper() == "ALL":
            access_type = "ALL"
            break
        if isinstance(at, str) and access_type == "UNKNOWN":
            access_type = at.upper()

    # ── 4. 查询成本 ──
    query_cost: float | None = None
    try:
        cost_str = _find_all_values(qb, "query_cost")
        if cost_str:
            # 取第一个非空值（通常是顶层 cost_info.query_cost）
            for c in cost_str:
                if isinstance(c, (str, int, float)):
                    query_cost = float(c)
                    break
    except (ValueError, TypeError):
        pass

    # ── 5. 列宽估算 ──
    all_used_columns = _find_all_values(qb, "used_columns")
    max_col_count = 0
    for cols in all_used_columns:
        if isinstance(cols, list) and len(cols) > max_col_count:
            max_col_count = len(cols)
    row_width_bytes = max_col_count * 50 if max_col_count > 0 else 0

    # ── 6. 额外操作 ──
    extra_ops: list[str] = []
    # filesort
    all_filesort = _find_all_values(qb, "using_filesort")
    if any(v is True for v in all_filesort):
        extra_ops.append("filesort")
    # temporary table
    all_temporary = _find_all_values(qb, "using_temporary_table")
    if any(v is True for v in all_temporary):
        extra_ops.append("temporary")

    return {
        "access_type": access_type,
        "rows_examined": est_rows_examined,
        "rows_output": est_rows_output,
        "row_width_bytes": row_width_bytes,
        "query_cost": query_cost,
        "extra_operations": extra_ops,
    }


# =============================================================================
# PostgreSQL EXPLAIN (FORMAT JSON) 解析
# =============================================================================


def _parse_pg_cost(cost: float | str) -> float:
    """解析 PostgreSQL 成本值。

    PostgreSQL 成本格式为 "startup_cost..total_cost"（字符串），
    此函数提取第二个数值作为总成本。如果已经是 float 则直接返回。

    Args:
        cost: 成本值字符串（"startup..total"）或数值。

    Returns:
        总成本值（float）。
    """
    if isinstance(cost, (int, float)):
        return float(cost)
    try:
        parts = str(cost).split("..")
        return float(parts[-1].strip())
    except (ValueError, IndexError):
        return 0.0


def _walk_pg_plan(node: dict, metrics: dict) -> None:
    """递归遍历 PostgreSQL 计划树，提取指标。

    PostgreSQL 的 EXPLAIN JSON 格式为嵌套的 Plan 树结构，
    每个节点含 Node Type、Plan Rows 等字段，下级节点在 Plans 数组中。

    提取策略：
      - access_type: 最底层（最深）的非辅助节点决定访问方式
      - Plan Rows: 根节点的值作为总预估行数
      - Plan Width: 根节点的值作为行宽
      - 成本: 根节点的 Total Cost
      - 额外操作: 检测 Sort/Hash/Materialize 等节点类型

    Args:
        node: 当前 Plan 节点 dict。
        metrics: 累加指标的 dict（会在递归中被修改）。
    """
    if not isinstance(node, dict):
        return

    node_type = node.get("Node Type", "")

    # 根节点记录总行数、行宽、成本
    is_root = metrics.get("_depth", 0) == 0
    if is_root:
        plan_rows = node.get("Plan Rows")
        if isinstance(plan_rows, (int, float)):
            metrics["rows_examined"] = max(metrics["rows_examined"], int(plan_rows))
            metrics["rows_output"] = max(metrics["rows_output"], int(plan_rows))

        plan_width = node.get("Plan Width")
        if isinstance(plan_width, (int, float)) and plan_width > 0:
            metrics["row_width_bytes"] = max(metrics["row_width_bytes"], int(plan_width))

        startup_cost = node.get("Startup Cost", 0)
        total_cost = node.get("Total Cost", 0)
        cost_val = total_cost if total_cost else startup_cost
        if isinstance(cost_val, (int, float)):
            metrics["query_cost"] = max(metrics.get("query_cost", 0) or 0, _parse_pg_cost(cost_val))

    # 访问方式判断（取最严重的访问类型，FULL_SCAN > INDEX_SCAN > INDEX_LOOKUP）
    if node_type and node_type not in (
        "Sort",
        "Hash",
        "Materialize",
        "Aggregate",
        "Limit",
        "Result",
    ):
        # 简单优先级：Seq Scan → FULL_SCAN 最严重，覆盖其他所有
        seen = metrics.get("_worst_scan_type", "")
        node_upper = node_type.upper()
        if node_upper in ("SEQ SCAN", "SEQUENTIAL SCAN", "FULL SCAN") or not seen:
            metrics["_worst_scan_type"] = node_type

    # 额外操作
    if (
        node_type in ("Sort", "External Sort", "External Merge")
        and "filesort" not in metrics["extra_operations"]
    ):
        metrics["extra_operations"].append("filesort")
    if node_type == "Hash" and "hash_join" not in metrics["extra_operations"]:
        metrics["extra_operations"].append("hash_join")
    if node_type == "Materialize" and "materialize" not in metrics["extra_operations"]:
        metrics["extra_operations"].append("materialize")

    # 递归遍历子计划
    subplans = node.get("Plans", [])
    if subplans:
        metrics["_depth"] = metrics.get("_depth", 0) + 1
        for sub in subplans:
            _walk_pg_plan(sub, metrics)
        metrics["_depth"] -= 1


def _parse_postgresql_explain(json_str: str) -> dict | None:
    """解析 PostgreSQL EXPLAIN (FORMAT JSON) 输出，提取多维度指标。

    Args:
        json_str: EXPLAIN (FORMAT JSON, ANALYZE false) 输出的 JSON 字符串。

    Returns:
        包含结构化指标的 dict，或 None 表示解析失败。
    """
    import json

    try:
        parsed = json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        return None

    # PostgreSQL EXPLAIN JSON 是一个数组，第一个元素包含 Plan
    if isinstance(parsed, list) and len(parsed) > 0:
        plan_root = parsed[0]
    elif isinstance(parsed, dict):
        plan_root = parsed
    else:
        return None

    plan = plan_root.get("Plan") if isinstance(plan_root, dict) else None
    if not plan:
        return None

    metrics: dict = {
        "access_type": "UNKNOWN",
        "rows_examined": 0,
        "rows_output": 0,
        "row_width_bytes": 0,
        "query_cost": None,
        "extra_operations": [],
        "_depth": 0,
        "_worst_scan_type": None,
    }

    _walk_pg_plan(plan, metrics)

    # 用最严重的扫描类型作为访问方式
    if metrics["_worst_scan_type"]:
        metrics["access_type"] = metrics["_worst_scan_type"]

    # 清理内部字段
    metrics.pop("_depth", None)
    metrics.pop("_worst_scan_type", None)

    return {
        "access_type": metrics["access_type"],
        "rows_examined": metrics["rows_examined"],
        "rows_output": metrics["rows_output"],
        "row_width_bytes": metrics["row_width_bytes"],
        "query_cost": metrics["query_cost"],
        "extra_operations": metrics["extra_operations"],
    }


# =============================================================================
# Oracle DBMS_XPLAN 文本解析
# =============================================================================

# Oracle DBMS_XPLAN 输出的表头行（正则，可选的 Id* 表示有 predicate 的行）
_ORACLE_TABLE_HEADER = re.compile(
    r"^\s*\|\s*Id\s*\|",
    re.IGNORECASE,
)
# 数据行：| Id | Operation | Name | Rows | Bytes | Cost (%CPU)| Time |
_ORACLE_DATA_ROW = re.compile(
    r"^\s*\|\s*(?:\*?\s*(\d+))\s*\|(.+?)\|(.+?)\|(.+?)\|(.+?)\|(.+?)\|",
    re.IGNORECASE,
)


def _parse_oracle_number(s: str) -> int | None:
    """解析 Oracle 数量字符串（支持 K/M/G 后缀）。

    Args:
        s: 数字字符串，如 "5000"、"10K"、"1.5M"、"2G"。

    Returns:
        解析后的整数值，或 None 表示无法解析。
    """
    s = s.strip()
    if not s:
        return None
    try:
        s_upper = s.upper()
        multiplier = 1
        if s_upper.endswith("K"):
            multiplier = 1_000
            s_upper = s_upper[:-1]
        elif s_upper.endswith("M"):
            multiplier = 1_000_000
            s_upper = s_upper[:-1]
        elif s_upper.endswith("G"):
            multiplier = 1_000_000_000
            s_upper = s_upper[:-1]
        return int(float(s_upper) * multiplier)
    except (ValueError, OverflowError):
        return None


def _classify_oracle_operation(operation: str) -> str:
    """对 Oracle Operation 文本进行分类识别。

    Args:
        operation: Operation 列的文本，如 "TABLE ACCESS FULL"。

    Returns:
        归一化的操作类型："FULL_SCAN" / "INDEX_SCAN" / "INDEX_LOOKUP" / "CONST" / "UNKNOWN"。
    """
    op_upper = operation.strip().upper()
    if "TABLE ACCESS FULL" in op_upper:
        return "FULL_SCAN"
    if "INDEX FULL SCAN" in op_upper:
        return "FULL_INDEX_SCAN"
    if "INDEX RANGE SCAN" in op_upper:
        return "INDEX_RANGE"
    if "INDEX SKIP SCAN" in op_upper:
        return "INDEX_RANGE"
    if "INDEX UNIQUE SCAN" in op_upper:
        return "INDEX_LOOKUP"
    if "TABLE ACCESS BY INDEX ROWID" in op_upper:
        return "INDEX_LOOKUP"
    if "TABLE ACCESS BY LOCAL INDEX ROWID" in op_upper:
        return "INDEX_LOOKUP"
    if "TABLE ACCESS BY GLOBAL INDEX ROWID" in op_upper:
        return "INDEX_LOOKUP"
    if "TABLE ACCESS CLUSTER" in op_upper:
        return "INDEX_LOOKUP"
    if "TABLE ACCESS HASH" in op_upper:
        return "INDEX_LOOKUP"
    return "UNKNOWN"


def _detect_oracle_extra_ops(operation: str) -> list[str]:
    """检测 Oracle Operation 中的额外操作。

    Args:
        operation: Operation 列文本。

    Returns:
        检测到的额外操作列表。
    """
    ops: list[str] = []
    op_upper = operation.strip().upper()
    if (" SORT " in op_upper or op_upper.startswith("SORT ")) and "filesort" not in ops:
        ops.append("filesort")
    if (" HASH " in op_upper or op_upper.startswith("HASH ")) and "hash_join" not in ops:
        ops.append("hash_join")
    if (" TEMP " in op_upper or "TEMPORARY" in op_upper) and "temporary" not in ops:
        ops.append("temporary")
    if "TABLE ACCESS FULL" in op_upper and "full_scan" not in ops:
        ops.append("full_scan")
    return ops


def _parse_oracle_explain(text: str) -> dict | None:
    """解析 Oracle DBMS_XPLAN.DISPLAY 输出，提取多维度指标。

    Oracle EXPLAIN 格式为文本表格，需要通过正则提取各行数据。
    支持 TYPICAL 格式的输出，Rows/Bytes 列可含 K/M/G 后缀。

    Args:
        text: DBMS_XPLAN.DISPLAY 输出的文本字符串。

    Returns:
        包含结构化指标的 dict，或 None 表示解析失败。
    """
    if not text or not isinstance(text, str):
        return None

    lines = text.split("\n")
    in_table = False
    header_found = False
    data_rows: list[dict] = []

    for line in lines:
        stripped = line.strip()

        # 检测表头行（转换行虚线之间找到 | Id | 表头）
        if not header_found and _ORACLE_TABLE_HEADER.search(stripped):
            header_found = True
            in_table = True
            continue

        if not in_table:
            continue

        # 虚线结束表
        if stripped.startswith("---") and len(stripped) > 5:
            if header_found and data_rows:
                # 表结束标记，继续后面可能还有更多的虚线但已经是结束了
                break
            continue

        match = _ORACLE_DATA_ROW.match(stripped)
        if not match:
            continue

        row_id = int(match.group(1))
        operation = match.group(2).strip()
        name = match.group(3).strip()
        rows_str = match.group(4).strip()
        bytes_str = match.group(5).strip()
        cost_str = match.group(6).strip()

        data_rows.append(
            {
                "id": row_id,
                "operation": operation,
                "name": name,
                "rows_str": rows_str,
                "bytes_str": bytes_str,
                "cost_str": cost_str,
            }
        )

    if not data_rows:
        return None

    # ── 提取指标 ──
    all_rows_examined: list[int] = []
    all_rows_bytes: list[int] = []
    all_costs: list[float] = []
    extra_ops: list[str] = []
    access_type = "UNKNOWN"
    first_row = data_rows[0]

    for row in data_rows:
        # Rows
        rows_val = _parse_oracle_number(row["rows_str"])
        if rows_val is not None:
            all_rows_examined.append(rows_val)

        # Bytes
        bytes_val = _parse_oracle_number(row["bytes_str"])
        if bytes_val is not None:
            all_rows_bytes.append(bytes_val)

        # Cost（取 "1234 (56)" 格式中的第一个数字）
        if row["cost_str"]:
            try:
                cost_match = re.match(r"(\d+(?:\.\d+)?)", row["cost_str"].strip())
                if cost_match:
                    all_costs.append(float(cost_match.group(1)))
            except (ValueError, IndexError):
                pass

        # 访问方式（从最外层 Operation 判断——Id=0 是 SELECT STATEMENT 固定返回 UNKNOWN，
        # 取第一行非 UNKNOWN 的 Operation 作为整体访问方式）
        if access_type == "UNKNOWN":
            op_type = _classify_oracle_operation(row["operation"])
            if op_type != "UNKNOWN":
                access_type = op_type

        # 额外操作
        extra_ops.extend(_detect_oracle_extra_ops(row["operation"]))

    # 去重
    extra_ops = list(dict.fromkeys(extra_ops))

    # Id=0 的 Rows 是输出行数，叶子节点的 Rows 是扫描行数
    est_rows_examined = max(all_rows_examined) if all_rows_examined else 0
    first_rows_val = _parse_oracle_number(first_row["rows_str"])
    est_rows_output = first_rows_val if first_rows_val is not None else est_rows_examined

    # 行宽 = Bytes / Rows
    row_width_bytes = 0
    if all_rows_examined and all_rows_bytes:
        total_rows = sum(all_rows_examined)
        total_bytes = max(all_rows_bytes)
        if total_rows > 0:
            row_width_bytes = total_bytes // total_rows

    # 成本
    query_cost: float | None = max(all_costs) if all_costs else None

    return {
        "access_type": access_type,
        "rows_examined": est_rows_examined,
        "rows_output": est_rows_output,
        "row_width_bytes": row_width_bytes,
        "query_cost": query_cost,
        "extra_operations": extra_ops,
    }


# =============================================================================
# 跨数据库访问方式分类器
# =============================================================================


# MySQL access_type → access_pattern 映射
_MYSQL_ACCESS_MAP: dict[str, str] = {
    "ALL": "FULL_SCAN",
    "INDEX": "FULL_INDEX_SCAN",        # 全索引扫描（遍历整个索引树，介于 ALL 和 RANGE 之间）
    "INDEX_MERGE": "INDEX_RANGE",      # 多索引合并（虽需优化但性能远好于全表扫）
    "RANGE": "INDEX_RANGE",
    "INDEX_SUBQUERY": "INDEX_RANGE",
    "REF": "INDEX_LOOKUP",
    "EQ_REF": "INDEX_LOOKUP",
    "FULLTEXT": "INDEX_LOOKUP",
    "REF_OR_NULL": "INDEX_LOOKUP",
    "UNIQUE_SUBQUERY": "INDEX_LOOKUP",
    "CONST": "CONST",
    "SYSTEM": "CONST",
}

# PostgreSQL Node Type → access_pattern 映射
_PG_ACCESS_MAP: dict[str, str] = {
    "SEQ SCAN": "FULL_SCAN",
    "SEQUENTIAL SCAN": "FULL_SCAN",
    "INDEX FULL SCAN": "FULL_INDEX_SCAN",
    "INDEX RANGE SCAN": "INDEX_RANGE",
    "BITMAP HEAP SCAN": "INDEX_RANGE",
    "BITMAP INDEX SCAN": "INDEX_RANGE",
    "INDEX SCAN": "INDEX_LOOKUP",
    "INDEX ONLY SCAN": "INDEX_LOOKUP",
}


def _classify_access_pattern(raw_type: str, db_type: str) -> str:
    """将各数据库原生的访问方式字符串统一转换为 5 种标准类型。

    支持的数据库：mysql / postgresql / oracle。
    未知输入返回 UNKNOWN，不抛异常。

    Args:
        raw_type: 各数据库原生的访问方式字符串。
        db_type: 数据库类型（mysql / postgresql / oracle）。

    Returns:
        统一后的访问模式（危险程度从高到低）：
          FULL_SCAN       — 全表扫描（最危险，逐行读整个表）
          FULL_INDEX_SCAN — 全索引扫描（遍历整个索引树）
          INDEX_RANGE     — 索引范围扫描（只读索引树中匹配部分）
          INDEX_LOOKUP    — 索引精确定位（单个/少量探针）
          CONST           — 常量折叠（主键=常量，最多1行）
          UNKNOWN         — 无法识别（保守处理）
    """
    if not raw_type:
        return "UNKNOWN"

    cleaned = raw_type.strip().upper()

    # 如果已经是统一类型（如 Oracle 解析器已分类），直接返回
    if cleaned in ("FULL_SCAN", "FULL_INDEX_SCAN", "INDEX_RANGE", "INDEX_LOOKUP", "CONST"):
        return cleaned

    if db_type == "mysql":
        return _MYSQL_ACCESS_MAP.get(cleaned, "UNKNOWN")

    if db_type in ("postgresql", "postgres"):
        return _PG_ACCESS_MAP.get(cleaned, "UNKNOWN")

    # Oracle 使用专门的 _classify_oracle_operation 函数
    if db_type == "oracle":
        return _classify_oracle_operation(raw_type)

    return "UNKNOWN"


# =============================================================================
# 指标提取调度（extract_metrics）
# =============================================================================


def extract_metrics(
    explain_output: str,
    db_type: str,
    _explain_format: str = "json",
) -> ExplainMetrics | None:
    """从 EXPLAIN 输出中提取标准化多维度评估指标。

    根据 db_type 调度到对应的数据库解析器，然后将 parse 出的 dict
    统一映射为 ExplainMetrics 结构体。

    解析失败（格式错误、不支持的类型等）返回 None，由调用方降级处理。

    Args:
        explain_output: EXPLAIN 输出的原始字符串（JSON 或文本）。
        db_type: 数据库类型（mysql / postgresql / oracle）。
        explain_format: EXPLAIN 输出格式（"json" / "text"）。

    Returns:
        ExplainMetrics 结构体，或 None 表示解析失败。
    """
    if not explain_output:
        return None

    parsed: dict | None = None

    if db_type == "mysql":
        parsed = _parse_mysql_explain(explain_output)
    elif db_type in ("postgresql", "postgres"):
        parsed = _parse_postgresql_explain(explain_output)
    elif db_type == "oracle":
        parsed = _parse_oracle_explain(explain_output)
    else:
        return None

    if parsed is None:
        return None

    # 统一访问方式
    access_pattern = _classify_access_pattern(parsed.get("access_type", ""), db_type)

    return ExplainMetrics(
        access_pattern=access_pattern,
        estimated_rows_examined=parsed.get("rows_examined", 0),
        estimated_rows_output=parsed.get("rows_output", 0),
        row_width_bytes=parsed.get("row_width_bytes", 0),
        query_cost=parsed.get("query_cost"),
        extra_operations=parsed.get("extra_operations", []),
    )


# =============================================================================
# 规则引擎 — 评估决策
# =============================================================================

# --- 阈值常量（硬编码，修改需 Code Review）---
# 全表扫描：最危险，逐行读整个表，阈值最保守
_FULL_SCAN_BLOCK_ROWS = 50_000

# 全索引扫描：遍历整个索引树，比全表扫轻但仍是全量IO，阈值适中
_FULL_INDEX_SCAN_BLOCK_ROWS = 100_000

# 索引范围扫描：只读索引树中匹配部分，顺序IO，阈值放宽
_INDEX_RANGE_BLOCK_ROWS = 500_000

# 索引精确查找：逐行B+Tree探针（随机IO），单行成本高于范围扫描，阈值与范围扫描对齐
_INDEX_LOOKUP_BLOCK_ROWS = 500_000

# 成本
_COST_BLOCK_RATIO = 1000.0

# 临时表威胁
_TEMPTABLE_BLOCK_ROWS = 200_000

# 无法识别的访问方式保守阈值
_UNKNOWN_BLOCK_ROWS = 100_000


def _get_limit_for_rule(rule_id: str) -> int:
    """获取规则对应的阈值常量。"""
    _limit_map: dict[str, int] = {
        "R1_FULL_SCAN_LARGE": _FULL_SCAN_BLOCK_ROWS,
        "R2_FULL_SCAN_COSTLY": 0,
        "R3_FULL_INDEX_SCAN": _FULL_INDEX_SCAN_BLOCK_ROWS,
        "R4_INDEX_RANGE_HUGE": _INDEX_RANGE_BLOCK_ROWS,
        "R5_LOOKUP_HUGE": _INDEX_LOOKUP_BLOCK_ROWS,
        "R6_UNKNOWN_CONSERVATIVE": _UNKNOWN_BLOCK_ROWS,
        "R7_TEMPTABLE_LARGE": _TEMPTABLE_BLOCK_ROWS,
    }
    return _limit_map.get(rule_id, 0)


_EVALUATION_RULES: list[dict] = [
    # ═══════ CRITICAL 级别（阻断执行），按危险程度 R1→R7 ═══════
    # R1: FULL_SCAN（全表扫描）+ 大行数
    {
        "id": "R1_FULL_SCAN_LARGE",
        "severity": "CRITICAL",
        "condition": lambda m: (
            m.access_pattern == "FULL_SCAN"
            and m.estimated_rows_examined > _FULL_SCAN_BLOCK_ROWS
        ),
        "message": (
            "全表扫描预估读取 {rows:,} 行，超过安全上限 {limit:,} 行。"
            "建议：为过滤条件列添加索引，或缩小 WHERE 条件范围。"
        ),
    },
    # R2: FULL_SCAN（全表扫描）+ 高成本
    {
        "id": "R2_FULL_SCAN_COSTLY",
        "severity": "CRITICAL",
        "condition": lambda m: (
            m.access_pattern == "FULL_SCAN"
            and m.query_cost is not None
            and m.query_cost > _COST_BLOCK_RATIO * 10
        ),
        "message": (
            "全表扫描成本 {cost:.1f} 过高。建议：添加索引避免全表扫描，或使用更精确的 WHERE 条件。"
        ),
    },
    # R3: FULL_INDEX_SCAN（全索引扫描，如 COUNT(*) 走覆盖索引）
    {
        "id": "R3_FULL_INDEX_SCAN",
        "severity": "CRITICAL",
        "condition": lambda m: (
            m.access_pattern == "FULL_INDEX_SCAN"
            and m.estimated_rows_examined > _FULL_INDEX_SCAN_BLOCK_ROWS
        ),
        "message": (
            "全索引扫描预估读取 {rows:,} 行，超过安全上限 {limit:,} 行。"
            "索引虽快但全扫索引仍会产生大量 IO。建议：添加 WHERE 条件缩小范围。"
        ),
    },
    # R4: INDEX_RANGE（索引范围扫描）+ 大行数
    {
        "id": "R4_INDEX_RANGE_HUGE",
        "severity": "CRITICAL",
        "condition": lambda m: (
            m.access_pattern == "INDEX_RANGE"
            and m.estimated_rows_examined > _INDEX_RANGE_BLOCK_ROWS
        ),
        "message": (
            "索引范围扫描预估读取 {rows:,} 行，超过安全上限 {limit:,} 行。"
            "建议：缩小 WHERE 条件范围，或使用 LIMIT 分页。"
        ),
    },
    # R5: INDEX_LOOKUP + CONST（精确查找）+ 极大数据量
    {
        "id": "R5_LOOKUP_HUGE",
        "severity": "CRITICAL",
        "condition": lambda m: (
            m.access_pattern in ("INDEX_LOOKUP", "CONST")
            and m.estimated_rows_examined > _INDEX_LOOKUP_BLOCK_ROWS
        ),
        "message": (
            "索引精确查找预估读取 {rows:,} 行，超过安全上限 {limit:,} 行。"
            "逐行 B+Tree 探针成本极高。建议：分批查询或改用范围扫描。"
        ),
    },
    # R6: UNKNOWN（无法识别）+ 保守阈值
    {
        "id": "R6_UNKNOWN_CONSERVATIVE",
        "severity": "CRITICAL",
        "condition": lambda m: (
            m.access_pattern == "UNKNOWN"
            and m.estimated_rows_examined > _UNKNOWN_BLOCK_ROWS
        ),
        "message": (
            "无法识别的访问方式预估读取 {rows:,} 行，超过保守安全上限 {limit:,} 行。"
            "建议：检查 SQL 是否有可用索引，或联系 DBA 确认。"
        ),
    },
    # R7: 临时表 + 大数据量（横切维度，不限访问方式）
    {
        "id": "R7_TEMPTABLE_LARGE",
        "severity": "CRITICAL",
        "condition": lambda m: (
            "temporary" in m.extra_operations
            and m.estimated_rows_examined > _TEMPTABLE_BLOCK_ROWS
        ),
        "message": (
            "查询需要使用临时表且数据量达 {rows:,} 行，"
            "可能导致磁盘 I/O 溢出。建议：优化 GROUP BY/DISTINCT，添加合适索引。"
        ),
    },
]


def evaluate(metrics: ExplainMetrics) -> ExplainDecision:
    """对提取的指标执行规则评估。

    规则按顺序匹配，首个 CRITICAL 规则命中则阻断。
    所有 CRITICAL 均未命中则放行（LOW 风险）。

    Args:
        metrics: 标准化评估指标。

    Returns:
        ExplainDecision — CRITICAL 时 allowed=False，否则 allowed=True。
    """
    for rule in _EVALUATION_RULES:
        if not rule["condition"](metrics):
            continue

        message = rule["message"].format(
            rows=metrics.estimated_rows_examined,
            limit=_get_limit_for_rule(rule["id"]),
            cost=metrics.query_cost or 0,
        )

        if rule["severity"] == "CRITICAL":
            return ExplainDecision(
                allowed=False,
                risk_level="CRITICAL",
                reasons=[message],
                metrics=metrics,
            )

    return ExplainDecision(
        allowed=True,
        risk_level="LOW",
        reasons=[],
        metrics=metrics,
    )


# =============================================================================
# LLM 上下文窗口保护 — 结果截断
# =============================================================================

_MAX_LLM_RESULT_ROWS = 100
_MAX_LLM_RESULT_CHARS = 40_000


def truncate_result_for_llm(
    result: dict,
    max_chars: int | None = None,
) -> dict:
    """截断工具结果，防止 LLM 上下文窗口溢出（兼容有/无 rows 键的结果）。

    在 tool_node.py 的 _run_one_tool 中，工具执行成功后、构造 ToolMessage 前调用。
    截断时补充元信息告知 LLM 数据已被截断。

    - rows 列表结果：沿用原逻辑（行数上限 + 序列化体积上限，阈值取 max_chars）
    - 无 rows 键结果（explain_output / items / categories 等）：迭代削减最大的
      list 字段（半数递减）；仍超限则对最大的字符串字段切片。
    - 正常小结果原样返回（不附加任何标记）。

    Args:
        result: 工具执行结果 dict（含 columns / rows / total_rows 等字段）。
        max_chars: 序列化体积上限。None 时使用默认 _MAX_LLM_RESULT_CHARS（40K）；
            调用方（tool_node）对 explain 类结果传入更小阈值（settings.EXPLAIN_MAX_CHARS）。

    Returns:
        截断后的 result dict（如果不需要截断则原样返回）。
    """
    if max_chars is None:
        max_chars = _MAX_LLM_RESULT_CHARS
    if not isinstance(result, dict):
        return result

    rows = result.get("rows", [])
    if isinstance(rows, list) and rows:
        return _truncate_rows_result(result, rows, max_chars)
    return _truncate_generic_result(result, max_chars)


def _truncate_rows_result(result: dict, rows: list, max_chars: int) -> dict:
    """截断 rows 结果（原 truncate_result_for_llm 逻辑，体积阈值参数化）。"""
    import json

    total_rows = result.get("total_rows", len(rows))

    # 行数在允许范围内 → 检查字符数
    if total_rows <= _MAX_LLM_RESULT_ROWS:
        body = json.dumps(result, ensure_ascii=False, default=str)
        if len(body) <= max_chars:
            return result
        # 字符数超额，降级到截断逻辑

    # 截断行数
    truncated: dict = dict(result)
    truncated["rows"] = rows[:_MAX_LLM_RESULT_ROWS]
    truncated["_truncated"] = True
    truncated["_original_total_rows"] = total_rows
    truncated["_truncated_to"] = _MAX_LLM_RESULT_ROWS

    # 如果序列化后仍过大，逐次减半直到收束
    serialized = json.dumps(truncated, ensure_ascii=False, default=str)
    while len(serialized) > max_chars and len(truncated["rows"]) > 10:
        truncated["rows"] = truncated["rows"][: max(10, len(truncated["rows"]) // 2)]
        serialized = json.dumps(truncated, ensure_ascii=False, default=str)

    return truncated


def _truncate_generic_result(result: dict, max_chars: int) -> dict:
    """截断无 rows 键的结果：迭代削减最大 list / 字符串字段，直到体积达标。

    体积未超限的小结果（如 rows 为空的查询结果）原样返回，不附加任何标记。
    仅在序列化体积确实超过 max_chars 时才进入削减循环。
    """
    import json

    if len(json.dumps(result, ensure_ascii=False, default=str)) <= max_chars:
        return result

    truncated: dict = dict(result)
    while True:
        serialized = json.dumps(truncated, ensure_ascii=False, default=str)
        if len(serialized) <= max_chars:
            truncated["_truncated"] = True
            truncated["_truncated_to_chars"] = max_chars
            return truncated
        # 1) 缩减最大的 list 字段（items / categories / held_locks 等）
        best_key, best_len = None, 0
        for k_, v_ in truncated.items():
            if isinstance(v_, list) and len(v_) > best_len:
                best_key, best_len = k_, len(v_)
        if best_key is not None and best_len > 1:
            truncated[best_key] = truncated[best_key][: max(1, best_len // 2)]
            continue
        # 2) 缩减最大的字符串字段（如 explain_output）
        # 注意：必须按半递减而非切片到 max_chars，否则序列化含其他字段仍可能略超限，
        # 切片后长度不变会导致死循环（踩坑记录见 context-compression-plan §7）。
        best_key, best_str = None, ""
        for k_, v_ in truncated.items():
            if isinstance(v_, str) and len(v_) > len(best_str):
                best_key, best_str = k_, v_
        if best_key is not None and len(best_str) > 100:
            truncated[best_key] = best_str[: max(100, len(best_str) // 2)]
            continue
        # 3) 兜底：所有字段已最小仍超限，直接返回当前结果
        truncated["_truncated"] = True
        truncated["_truncated_to_chars"] = max_chars
        return truncated
