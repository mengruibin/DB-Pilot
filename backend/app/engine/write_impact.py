"""
写操作影响范围预估引擎（write-impact-estimate-plan Task 1）。

把「原始写 SQL + 原始 EXPLAIN 输出」转换为「预估受影响行数」——纯静态/纯函数，
供确认卡展示（execute_write_sql / execute_write_transaction 的 ImpactEstimateStage）。

设计要点：
  - 只出计划不执行：各引擎对 UPDATE/DELETE/INSERT..SELECT 的 EXPLAIN 均不执行语句
    （PG EXPLAIN 默认不 ANALYZE、MySQL EXPLAIN 只描述计划、Oracle EXPLAIN PLAN FOR
    只生成计划）→ 本模块读取的永远是"预估值"，非精确值。
  - 多库提取语义：
      * PostgreSQL（FORMAT JSON）: ModifyTable 节点（Update/Delete/Insert）子树顶层
        "Plan Rows" ≈ 会被修改的行数。
      * MySQL（FORMAT JSON，8.0.19+ 才支持对 DML EXPLAIN）: 目标表访问节点
        rows_examined_per_scan × filtered% ≈ 命中行数（MySQL 用 filtered 表示过滤后保留比例）。
      * Oracle（DBMS_XPLAN TYPICAL 文本）: Id=0（UPDATE/DELETE/INSERT 语句节点）的
        Rows 列 ≈ 语句级基数估计。
  - 字面量 INSERT（多值 VALUES、无 SELECT 源）无法 EXPLAIN 预估 → 返回 None 不展示。
  - 任一环节解析失败/字段缺失 → 返回 None（fail-open，不阻断审批）。
  - 字段名以真实 DB 抓取的执行计划对拍为准；固化样本单测覆盖解析分支。

阈值（硬编码管理，修改需 Code Review，对齐 explain_estimator.py 约定）：
  WRITE_IMPACT_WARN_ROWS —— high_impact 警示阈值。
"""

from __future__ import annotations

import json
import re

import sqlglot
from sqlglot import exp

# =============================================================================
# 常量
# =============================================================================

# 预估受影响行数达到该阈值 → 确认卡 high_impact 警示（仅信息展示，不拦截）
WRITE_IMPACT_WARN_ROWS = 10_000

# 可 EXPLAIN 预估的写语句类型——INSERT 仅 INSERT..SELECT 可估，字面量 INSERT 走 classify_write 判定
# db_type → sqlglot 方言（与 sql_auditor._DIALECT_MAP 一致）
_DIALECT_MAP: dict[str, str] = {
    "mysql": "mysql",
    "postgresql": "postgres",
    "oracle": "oracle",
}


# =============================================================================
# 写语句静态分类（sqlglot，不依赖 EXPLAIN）
# =============================================================================


def classify_write(sql: str, db_type: str = "mysql") -> dict | None:
    """解析写 SQL 顶层语句，返回可预估信息（单语句、类型合法才返回）。

    Args:
        sql: 原始写 SQL（单条，未做语句类型白名单之外的多语句判断——多语句由
            上游 SQLAuditStage 拦截，本函数对多语句防御性返回 None）。
        db_type: 数据库类型（mysql / postgresql / oracle）。

    Returns:
        {"stmt_type": "UPDATE"|"DELETE"|"INSERT",
         "target_table": 目标表名（可能为 ""）,
         "has_select_source": 仅 INSERT 有意义——是否为 INSERT..SELECT（True 才可 EXPLAIN 预估）}
        解析失败 / 多语句 / 非写语句类型返回 None。
    """
    dialect = _DIALECT_MAP.get(db_type, "mysql")
    try:
        parsed = sqlglot.parse(sql, dialect=dialect)
    except Exception:
        return None

    # 过滤 Semicolon 占位，仅接受单条语句
    statements = [s for s in parsed if s is not None and not isinstance(s, exp.Semicolon)]
    if len(statements) != 1:
        return None
    stmt = statements[0]

    if isinstance(stmt, exp.Update):
        return {
            "stmt_type": "UPDATE",
            "target_table": _target_of_update(stmt),
            "has_select_source": False,
        }
    if isinstance(stmt, exp.Delete):
        return {
            "stmt_type": "DELETE",
            "target_table": _target_of_delete(stmt),
            "has_select_source": False,
        }
    if isinstance(stmt, exp.Insert):
        return {
            "stmt_type": "INSERT",
            "target_table": _target_of_insert(stmt),
            "has_select_source": _has_select_source(stmt),
        }
    return None


def _target_of_update(stmt: exp.Update) -> str:
    """提取 UPDATE 目标表名（异常时返回空串，不影响预估）。"""
    try:
        return stmt.this.sql() if stmt.this else ""
    except Exception:
        return ""


def _target_of_delete(stmt: exp.Delete) -> str:
    """提取 DELETE 目标表名。"""
    try:
        return stmt.this.sql() if stmt.this else ""
    except Exception:
        return ""


def _target_of_insert(stmt: exp.Insert) -> str:
    """提取 INSERT 目标表名（列清单会把 this 包成 Schema，需解包）。"""
    try:
        target = stmt.this
        if isinstance(target, exp.Schema):
            target = target.this
        return target.sql() if target else ""
    except Exception:
        return ""


def _has_select_source(stmt: exp.Insert) -> bool:
    """INSERT 的源是否为 SELECT/UNION/WITH（INSERT..SELECT 才可由 EXPLAIN 预估）。"""
    expr = stmt.expression
    return isinstance(expr, (exp.Select, exp.Union, exp.With))


# =============================================================================
# 各数据库提取器：EXPLAIN 输出 → 预估受影响行数（int | None）
# =============================================================================


def _mysql_affected_rows(json_str: str, target_table: str) -> int | None:
    """MySQL EXPLAIN FORMAT=JSON → 预估受影响行数。

    对每个 table 访问节点取 rows_examined_per_scan × filtered%，优先目标表节点；
    无目标表命中时取所有节点的最大值（保守上界）。
    filtered 为过滤后保留比例（百分数，如 100.00 / 33.33）。

    Returns:
        预估受影响行数；解析失败 / 无行信息返回 None。
    """
    try:
        root = json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        return None

    candidates: list[tuple[str, int]] = []  # (table_name, est)
    for node in _iter_table_nodes(root):
        rows = node.get("rows_examined_per_scan")
        if not isinstance(rows, (int, float)):
            continue
        # filtered 可能缺失 / 为 None / 字符串（MySQL 部分场景），显式逐型归一，
        # 避免对 None 调用 float()（静态类型报错 + 运行时歧义）。
        filtered_raw = node.get("filtered")
        if isinstance(filtered_raw, (int, float)):
            filtered_pct = float(filtered_raw)
        elif isinstance(filtered_raw, str):
            try:  # MySQL 部分场景 filtered 以字符串形式存在（如 "33.33"）
                filtered_pct = float(filtered_raw)
            except ValueError:
                filtered_pct = 100.0
        else:
            filtered_pct = 100.0  # 缺失/非数值 → 按无过滤（100%）保守处理
        est = int(round(float(rows) * filtered_pct / 100.0))
        candidates.append((str(node.get("table_name", "")), est))

    if not candidates:
        return None

    target_lower = _last_identifier(target_table).lower()
    for table_name, est in candidates:
        if target_lower and _last_identifier(table_name).lower() == target_lower:
            return est
    # 无目标表命中 → 保守取最大预估
    return max(est for _, est in candidates)


def _iter_table_nodes(obj) -> list[dict]:
    """递归收集可能携带 table 访问信息的 dict 节点（含 table 嵌套结构）。"""
    found: list[dict] = []
    if isinstance(obj, dict):
        # MySQL 的 table 节点可能直接是 {"table_name": ...}，也可能有 table 子键
        for k, v in obj.items():
            if k == "table" and isinstance(v, dict):
                found.append(v)
            elif k == "tables" and isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        found.append(item)
            else:
                found.extend(_iter_table_nodes(v))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_iter_table_nodes(item))
    return found


def _pg_affected_rows(json_str: str) -> int | None:
    """PostgreSQL EXPLAIN (FORMAT JSON) → 预估受影响行数。

    定位 ModifyTable 节点（Insert/Update/Delete/Merge）：取节点自带 "Plan Rows"
    （部分版本不存在），否则取子树顶层（首个子计划）的 "Plan Rows"——
    该子计划的输出行 ≈ 会被修改/插入的行数。

    Returns:
        预估受影响行数；无 ModifyTable 节点 / 解析失败返回 None。
    """
    try:
        parsed = json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        return None

    if isinstance(parsed, list) and len(parsed) > 0:
        plan_root = parsed[0]
    elif isinstance(parsed, dict):
        plan_root = parsed
    else:
        return None

    plan = plan_root.get("Plan") if isinstance(plan_root, dict) else None
    if not isinstance(plan, dict):
        return None

    dml_plan = _find_pg_dml_node(plan)
    if dml_plan is None:
        return None

    rows = dml_plan.get("Plan Rows")
    if isinstance(rows, (int, float)):
        return int(rows)

    # ModifyTable 自带 Plan Rows 缺失 → 取第一个子计划输出行数
    children = dml_plan.get("Plans") or []
    if children:
        child_rows = children[0].get("Plan Rows")
        if isinstance(child_rows, (int, float)):
            return int(child_rows)
    return None


def _find_pg_dml_node(plan: dict) -> dict | None:
    """递归查找顶层 ModifyTable（Insert/Update/Delete/Merge）计划节点。"""
    node_type = plan.get("Node Type", "")
    if node_type in ("Insert", "Update", "Delete", "Merge"):
        return plan
    for sub in plan.get("Plans") or []:
        found = _find_pg_dml_node(sub) if isinstance(sub, dict) else None
        if found is not None:
            return found
    return None


# Oracle DBMS_XPLAN 表格数据行：| Id | Operation | Name | Rows | Bytes | Cost ... | Time |
_ORACLE_DATA_ROW = re.compile(
    r"^\s*\|\s*(?:\*?\s*(\d+))\s*\|(.+?)\|(.+?)\|(.+?)\|(.+?)\|",
    re.IGNORECASE,
)


def _oracle_affected_rows(text: str) -> int | None:
    """Oracle DBMS_XPLAN.DISPLAY（TYPICAL 文本）→ 预估受影响行数。

    Id=0 的语句节点（UPDATE/DELETE/INSERT STATEMENT）的 Rows 列 = 语句级基数估计
    （≈ 会被修改/插入的行数）。

    Returns:
        预估受影响行数；解析失败返回 None。
    """
    if not text or not isinstance(text, str):
        return None

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        match = _ORACLE_DATA_ROW.match(stripped)
        if not match:
            continue
        # 取第一行有效数据行（即 Id=0 语句节点）的 Rows
        rows_val = _parse_number_suffix(match.group(4))
        return rows_val  # 可能为 None，直接作为无法预估
    return None


def _parse_number_suffix(s: str) -> int | None:
    """解析 Oracle 数量字符串（支持 K/M/G 后缀）。"""
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


def _last_identifier(name: str) -> str:
    """取表名的最后一个标识符段（去掉库名/模式前缀，如 "db.users" → "users"）。"""
    return name.split(".")[-1].strip().strip('`"[]')


# =============================================================================
# 顶层入口
# =============================================================================


def extract_write_impact(
    sql: str,
    db_type: str,
    explain_output: str,
) -> dict | None:
    """从写 SQL + EXPLAIN 输出提取预估影响范围。

    Args:
        sql: 原始写 SQL（单条 UPDATE/DELETE/INSERT..SELECT）。
        db_type: 数据库类型（mysql / postgresql / oracle）。
        explain_output: 适配器 explain() 返回的 explain_output 原文。

    Returns:
        {"stmt_type", "target_table", "estimated_rows", "method", "high_impact"}；
        无法预估（字面量 INSERT / 解析失败 / 引擎不支持 / 异常）返回 None。
    """
    info = classify_write(sql, db_type)
    if info is None:
        return None
    # 字面量 INSERT（无 SELECT 源）无法由 EXPLAIN 预估 → 不展示
    if info["stmt_type"] == "INSERT" and not info["has_select_source"]:
        return None

    if not explain_output:
        return None

    try:
        if db_type == "mysql":
            rows = _mysql_affected_rows(explain_output, info["target_table"])
        elif db_type in ("postgresql", "postgres"):
            rows = _pg_affected_rows(explain_output)
        elif db_type == "oracle":
            rows = _oracle_affected_rows(explain_output)
        else:
            return None
    except Exception:
        # 提取异常 → fail-open（不阻断审批），返回 None
        return None

    if rows is None or rows < 0:
        return None

    return {
        "stmt_type": info["stmt_type"],
        "target_table": info["target_table"],
        "estimated_rows": int(rows),
        "method": "explain",
        "high_impact": int(rows) >= WRITE_IMPACT_WARN_ROWS,
    }
