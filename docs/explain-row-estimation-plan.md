# EXPLAIN 多维度安全评估 — 工程落地方案（V3）

## Context

DB-Pilot 目前对 SQL 执行无任何行数/资源限制，风险：
1. **数据库层面**：大表全扫消耗 IO/CPU/内存
2. **应用层面**：全量结果加载到内存，可能 OOM
3. **LLM 层面**：百万行 JSON 塞进 ToolMessage，上下文窗口溢出导致调用失败

之前 `PerformanceCheck` 仅做正则静态分析（检测 `SELECT *`、缺少 `LIMIT`），无法感知真实数据量。已在后续迭代中移除，由 `RowEstimationCheck` 独立负责安全评估。

本方案设计一套**多维指标提取 + 规则引擎评估 + LLM 上下文保护**的三阶段防护体系。

---

## 一、架构总览

```
execute_sql 被 LLM 调用
 │
 ├─ Phase 1: [已移除] PerformanceCheck（静态文本分析，已被移除）
 │   └─ 静态文本分析 → 非阻断警告（SELECT * / 缺 LIMIT / WHERE 函数）
 │
 ├─ Phase 2: RowEstimationCheck（新增，一次 EXPLAIN 往返）
 │   ├─ Step 1: 判断是否需要 EXPLAIN（仅 DML 语句）
 │   ├─ Step 2: adapter.explain(sql) → 获取原始 EXPLAIN 输出
 │   ├─ Step 3: extract_metrics() → ExplainMetrics 结构体
 │   │   ├─ access_pattern: 访问方式分类
 │   │   ├─ estimated_rows_examined: 预估扫描行数
 │   │   ├─ estimated_rows_output: 预估返回行数
 │   │   ├─ query_cost: 优化器成本
 │   │   └─ extra_operations: 额外操作（filesort/temp table）
 │   ├─ Step 4: evaluate(metrics) → ExplainDecision
 │   │   └─ 规则引擎逐条匹配，CRITICAL → 阻断，其余 → LOW 放行
 │   └─ EXPLAIN 失败 → 降级放行（不阻断正常业务）
 │
 ├─ Phase 3: 实际执行（tool_fn.ainvoke）
 │
 └─ Phase 4: LLM 上下文保护（tool_node.py 结果后处理）
     └─ truncate_result() → 截断行数 + 序列化字符数 + 附加截断提示
```

---

## 二、ExplainMetrics：统一评估指标

无论哪种数据库，EXPLAIN 输出统一解析为同一结构体：

```python
@dataclass
class ExplainMetrics:
    """从 EXPLAIN 输出中提取的标准化评估指标"""

    # D1: 访问方式
    access_pattern: str
    # "FULL_SCAN" | "INDEX_SCAN" | "INDEX_LOOKUP" | "CONST" | "UNKNOWN"

    # D2: 数据规模
    estimated_rows_examined: int     # 预估扫描行数（DB IO 负载）
    estimated_rows_output: int       # 预估返回行数（网络 + LLM 负载）
    row_width_bytes: int             # 单行字节宽度（0=未知）

    # D3: 成本
    query_cost: float | None         # 优化器成本（跨 DB 不可直接比较，仅同 DB 内参考）

    # D4: 额外操作
    extra_operations: list[str]      # ["filesort", "temporary", "hash_join", "disk_spill"]
```

### 各数据库字段映射

| ExplainMetrics 字段 | MySQL (FORMAT=JSON) | PostgreSQL (FORMAT JSON) | Oracle (DBMS_XPLAN) |
|---|---|---|---|
| `access_pattern` | `table.access_type` → 分类 | `Plan["Node Type"]` → 分类 | `Operation` 列 → 分类 |
| `estimated_rows_examined` | `table.rows_examined_per_scan` (取所有 table 节点最大值) | `Plan["Plan Rows"]` (根节点) | `Rows` 列第一行数值 |
| `estimated_rows_output` | `table.rows_produced_per_join` (取最大值，默认等于 examined) | `Plan["Plan Rows"]` | 同上（Oracle 不区分） |
| `row_width_bytes` | 从 `used_columns` 数量 × 50 估算 | `Plan["Plan Width"]` | `Bytes ÷ Rows` 估算 |
| `query_cost` | `cost_info.query_cost` | `Plan["Total Cost"]`(第二个数字) | `Cost` 列第一行 |
| `extra_operations` | `using_filesort`, `using_temporary_table` | Plan 树含 `Sort`/`Hash`/`Materialize` 节点 | `Operation` 列含 `SORT`/`HASH`/`TEMP` |

### 访问方式分类规则（cross-DB normalization）

```
MySQL access_type → access_pattern:
  ALL, INDEX_MERGE(非索引)           → FULL_SCAN
  index                              → INDEX_SCAN
  range                              → INDEX_SCAN
  ref, eq_ref, fulltext              → INDEX_LOOKUP
  const, system                      → CONST

PG Node Type → access_pattern:
  Seq Scan                           → FULL_SCAN
  Index Full Scan                    → INDEX_SCAN
  Index Range Scan, Bitmap Heap Scan → INDEX_SCAN
  Index Scan (with Index Cond)       → INDEX_LOOKUP
  Index Only Scan                    → INDEX_LOOKUP

Oracle Operation → access_pattern:
  TABLE ACCESS FULL                  → FULL_SCAN
  INDEX FULL SCAN                    → INDEX_SCAN
  INDEX RANGE SCAN, INDEX SKIP SCAN  → INDEX_SCAN
  INDEX UNIQUE SCAN                  → INDEX_LOOKUP
  TABLE ACCESS BY INDEX ROWID        → INDEX_LOOKUP
```

---

## 三、规则引擎：评估决策

### 设计原则
- **硬编码**于 `explain_estimator.py` 模块级常量，修改需 Code Review
- **声明式**：每条规则是一个 dict/object，含条件、严重级别、消息模板
- **优先级排序**：CRITICAL 优先匹配，第一个命中即返回（短路求值）
- **每次拒绝都附完整上下文**：LLM 看到被拒原因后可自主改写 SQL

### 规则定义

```python
# =============================================================================
# 评估规则表（硬编码，优先级从高到低）
# 修改需 Code Review。格式：
#   (condition_fn, severity, message_template)
# condition_fn 签名为 (ExplainMetrics) -> bool
# severity: CRITICAL → 阻断 | 其他 → 放行
# =============================================================================

# --- 规则用阈值常量 ---
# 全表扫描：绝对风险最高，阈值最保守
_FULL_SCAN_BLOCK_ROWS = 5_000       # 全扫超此值直接阻断
_FULL_SCAN_WARN_ROWS = 500          # 全扫超此值发出警告

# 索引扫描：有索引加持，阈值放宽
_INDEX_SCAN_BLOCK_ROWS = 100_000    # 索引扫描超此值阻断
_INDEX_SCAN_WARN_ROWS = 10_000      # 索引扫描超此值警告

# 索引查找：精准访问，几乎不设限
_INDEX_LOOKUP_BLOCK_ROWS = 500_000  # 极大量索引查找也需阻断

# LLM 上下文窗口保护
_RESULT_BLOCK_ROWS = 10_000          # 返回超此值直接阻断（必定撑爆上下文）
_RESULT_WARN_ROWS = 500              # 返回超此值警告
_RESULT_BLOCK_BYTES = 500_000        # 返回超此字节数阻断（≈500KB）

# 成本（跨DB不可比，仅同DB内相对参考）
_COST_BLOCK_RATIO = 100.0            # 相对于 "正常查询" 的成本倍数（暂用绝对值）

# 额外操作威胁
_FILESORT_WARN_ROWS = 5_000          # filesort + 超此行数 → 警告
_TEMPTABLE_BLOCK_ROWS = 50_000       # temp table + 超此行数 → 阻断
```

### 规则表（优先级从高到低）

```python
_EVALUATION_RULES: list[dict] = [
    # ═══════ CRITICAL 级别（阻断执行）═══════

    # R1: 全表扫描 + 大行数 → 阻断（最常见的数据库性能杀手）
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

    # R2: 全表扫描 + 高成本 → 阻断（rows 不高但计算量大的场景，如聚合）
    {
        "id": "R2_FULL_SCAN_COSTLY",
        "severity": "CRITICAL",
        "condition": lambda m: (
            m.access_pattern == "FULL_SCAN"
            and m.query_cost is not None
            and m.query_cost > _COST_BLOCK_RATIO * 10  # 成本 > 1000
        ),
        "message": (
            "全表扫描成本 {cost:.1f} 过高。"
            "建议：添加索引避免全表扫描，或使用更精确的 WHERE 条件。"
        ),
    },

    # R3: 任何访问方式 + 超大扫描行数 → 阻断
    {
        "id": "R3_HUGE_SCAN",
        "severity": "CRITICAL",
        "condition": lambda m: (
            m.estimated_rows_examined > _INDEX_SCAN_BLOCK_ROWS
        ),
        "message": (
            "预估扫描 {rows:,} 行，超过最大允许值 {limit:,} 行。"
            "建议：通过 WHERE 条件缩小范围，或使用 LIMIT 分页。"
        ),
    },

    # R4: 结果集过大 → 阻断（保护 LLM 上下文窗口 + 应用内存）
    {
        "id": "R4_HUGE_RESULT_ROWS",
        "severity": "CRITICAL",
        "condition": lambda m: (
            m.estimated_rows_output > _RESULT_BLOCK_ROWS
        ),
        "message": (
            "预估返回 {rows:,} 行，超过 LLM 上下文窗口安全上限 {limit:,} 行。"
            "建议：添加 LIMIT 限制返回行数，或使用聚合查询减少数据量。"
        ),
    },

    # R5: 结果字节数过大 → 阻断（保护 LLM 上下文窗口 + 网络带宽）
    {
        "id": "R5_HUGE_RESULT_BYTES",
        "severity": "CRITICAL",
        "condition": lambda m: (
            m.row_width_bytes > 0
            and m.estimated_rows_output * m.row_width_bytes > _RESULT_BLOCK_BYTES
        ),
        "message": (
            "预估返回数据量 {bytes:,} 字节，超过安全上限 {limit:,} 字节。"
            "建议：减少选择的列数，或添加 LIMIT 限制。"
        ),
    },

    # R6: 临时表 + 大数据量 → 阻断（可能导致磁盘溢出）
    {
        "id": "R6_TEMPTABLE_LARGE",
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

    # ═══════ WARNING 级别（仅警告，不阻断）═══════

    # W1: 全表扫描 + 中等行数 → 警告
    {
        "id": "W1_FULL_SCAN_MODERATE",
        "severity": "WARNING",
        "condition": lambda m: (
            m.access_pattern == "FULL_SCAN"
            and m.estimated_rows_examined > _FULL_SCAN_WARN_ROWS
        ),
        "message": (
            "全表扫描 {rows:,} 行，建议检查是否有可用索引。"
        ),
    },

    # W2: 索引扫描 + 较多行数 → 警告
    {
        "id": "W2_INDEX_SCAN_MODERATE",
        "severity": "WARNING",
        "condition": lambda m: (
            m.access_pattern == "INDEX_SCAN"
            and m.estimated_rows_examined > _INDEX_SCAN_WARN_ROWS
        ),
        "message": (
            "索引扫描 {rows:,} 行，数据量较大，建议进一步缩小范围或使用 LIMIT。"
        ),
    },

    # W3: 结果行数偏多 → 警告
    {
        "id": "W3_RESULT_ROWS_MODERATE",
        "severity": "WARNING",
        "condition": lambda m: (
            m.estimated_rows_output > _RESULT_WARN_ROWS
        ),
        "message": (
            "预估返回 {rows:,} 行，可能占用较多 LLM 上下文，建议添加 LIMIT。"
        ),
    },

    # W4: filesort + 一定数据量 → 警告
    {
        "id": "W4_FILESORT_LARGE",
        "severity": "WARNING",
        "condition": lambda m: (
            "filesort" in m.extra_operations
            and m.estimated_rows_examined > _FILESORT_WARN_ROWS
        ),
        "message": (
            "查询需要文件排序且扫描 {rows:,} 行，考虑为 ORDER BY 列添加索引。"
        ),
    },

    # W5: 索引查找 + 极大行数 → 警告（极少触发）
    {
        "id": "W5_LOOKUP_HUGE",
        "severity": "WARNING",
        "condition": lambda m: (
            m.access_pattern == "INDEX_LOOKUP"
            and m.estimated_rows_examined > _INDEX_LOOKUP_BLOCK_ROWS
        ),
        "message": (
            "索引查找预估返回 {rows:,} 行，结果集极大，建议分页获取。"
        ),
    },
]
```

### 规则评估函数

```python
def evaluate(metrics: ExplainMetrics) -> ExplainDecision:
    """对提取的指标执行规则评估。

    规则按优先级顺序匹配，首个命中即返回。
    CRITICAL 规则命中即阻断，其余情况返回 LOW 放行。

    Returns:
        ExplainDecision(allowed, risk_level, reasons, metrics)
    """
    warnings = []
    for rule in _EVALUATION_RULES:
        if not rule["condition"](metrics):
            continue

        message = rule["message"].format(
            rows=metrics.estimated_rows_examined,
            limit=_get_limit_for_rule(rule["id"]),
            cost=metrics.query_cost or 0,
            bytes=metrics.row_width_bytes * metrics.estimated_rows_output,
        )

        if rule["severity"] == "CRITICAL":
            return ExplainDecision(
                allowed=False,
                risk_level="CRITICAL",
                reasons=[message],
                metrics=metrics,
            )
        else:
            warnings.append(message)

    if warnings:
        return ExplainDecision(
            allowed=True,
            risk_level="WARNING",
            reasons=warnings,
            metrics=metrics,
        )

    return ExplainDecision(
        allowed=True,
        risk_level="LOW",
        reasons=[],
        metrics=metrics,
    )
```

---

## 四、LLM 上下文窗口保护（Phase 4，tool_node.py 后处理）

**为什么需要在 Phase 4？** EXPLAIN 预估可能不准确：统计信息过时、JOIN 膨胀等。实际执行后才知道真实结果大小。Phase 4 是**兜底保护**，不管 Phase 2 是否放行，这里都要截断。

```python
# =============================================================================
# LLM 上下文窗口保护常量（硬编码）
# =============================================================================

_MAX_LLM_RESULT_ROWS = 200          # 返回给 LLM 的最大行数
_MAX_LLM_RESULT_CHARS = 80_000      # 返回给 LLM 的最大字符数
# Claude 上下文窗口 ~200K tokens ≈ 150K chars
# 80K chars ≈ 全窗口的 50%，留一半给推理和对话历史
```

### 截断实现

```python
def truncate_result_for_llm(result: dict) -> dict:
    """截断查询结果，防止 LLM 上下文窗口溢出。

    在 tool_node.py 的 _run_one_tool 中，工具执行成功后、构造 ToolMessage 前调用。
    截断时补充元信息告知 LLM 数据已被截断，包括原始行数/截断后行数。
    """
    rows = result.get("rows", [])
    if not isinstance(rows, list) or not rows:
        return result

    total_rows = result.get("total_rows", len(rows))
    columns = result.get("columns", [])

    # 不需要截断
    if total_rows <= _MAX_LLM_RESULT_ROWS:
        # 仍检查序列化字符数
        import json
        body_only = json.dumps(result, ensure_ascii=False, default=str)
        if len(body_only) <= _MAX_LLM_RESULT_CHARS:
            return result
        # 字符数超额，降级到截断逻辑

    # 截断行数
    truncated = dict(result)
    truncated["rows"] = rows[:_MAX_LLM_RESULT_ROWS]
    truncated["_truncated"] = True
    truncated["_original_total_rows"] = total_rows
    truncated["_truncated_to"] = _MAX_LLM_RESULT_ROWS

    # 如果序列化后仍过大，逐次减半直到收束
    import json
    serialized = json.dumps(truncated, ensure_ascii=False, default=str)
    while len(serialized) > _MAX_LLM_RESULT_CHARS and len(truncated["rows"]) > 10:
        truncated["rows"] = truncated["rows"][:max(10, len(truncated["rows"]) // 2)]
        serialized = json.dumps(truncated, ensure_ascii=False, default=str)

    return truncated
```

---

## 五、文件修改清单

### 5.1 新增：`backend/app/engine/explain_estimator.py`

**职责**：EXPLAIN 解析 + 评估规则引擎 + 结果截断（约 400 行）

```
explain_estimator.py
├── ExplainMetrics dataclass      # 统一评估指标
├── ExplainDecision dataclass     # 评估决策结果
│
├── # 指标提取 (per-DB parsers)
├── extract_metrics(explain_output, db_type, format) -> ExplainMetrics | None
├── _parse_mysql_explain(json_str) -> dict
├── _parse_postgresql_explain(json_str) -> dict
├── _parse_oracle_explain(text_str) -> dict
├── _classify_access_pattern(raw_type, db_type) -> str
├── _find_max_rows_mysql(json_obj) -> int
├── _extract_extra_ops_mysql(json_obj) -> list[str]
├── _extract_extra_ops_pg(json_obj) -> list[str]
├── _extract_extra_ops_oracle(text) -> list[str]
│
├── # 规则引擎
├── _EVALUATION_RULES: list[dict]  # 规则表
├── _THRESHOLDS (constants)        # 硬编码阈值
├── evaluate(ExplainMetrics) -> ExplainDecision
│
├── # LLM 上下文保护
├── _MAX_LLM_RESULT_ROWS: int
├── _MAX_LLM_RESULT_CHARS: int
└── truncate_result_for_llm(result: dict) -> dict
```

### 5.2 修改：`backend/app/agent/safety.py`

新增 `RowEstimationCheck`：

```python
class RowEstimationCheck(SafetyCheck):
    """EXPLAIN 多维度安全评估护栏（Phase 2）。

    通过 EXPLAIN 提取多维度指标，用硬编码规则引擎评估，超阈值即阻断。
    不依赖 .env 配置，所有阈值在 explain_estimator.py 中 Code Review 管理。
    """

    async def check(self, tool_name, tool_args, conn_config) -> SafetyResult:
        sql = tool_args.get("sql", "")
        if not sql:
            return SafetyResult(blocked=False)

        # 仅检查 DML 语句（DDL 已被 SQLAuditCheck 拦截）
        if not _is_dml_statement(sql):
            return SafetyResult(blocked=False)

        try:
            # 1. 创建临时连接执行 EXPLAIN
            adapter = await self._create_temp_adapter(conn_config)
            explain_result = await adapter.explain(sql)

            # 2. 提取标准化指标
            from app.engine.explain_estimator import extract_metrics, evaluate
            metrics = extract_metrics(
                explain_output=explain_result["explain_output"],
                db_type=conn_config["db_type"],
                explain_format=explain_result.get("format", "json"),
            )

            if metrics is None:
                return SafetyResult(
                    blocked=False,
                    warnings=["EXPLAIN 指标提取失败，跳过安全评估，查询已放行"],
                )

            # 3. 规则引擎评估
            decision = evaluate(metrics)

            if not decision.allowed:
                return SafetyResult(
                    blocked=True,
                    reason=(
                        f"[EXPLAIN 安全评估] 查询被阻断\n"
                        f"原因: {decision.reasons[0]}\n\n"
                        f"评估详情:\n"
                        f"  访问方式: {metrics.access_pattern}\n"
                        f"  预估扫描行数: {metrics.estimated_rows_examined:,}\n"
                        f"  预估返回行数: {metrics.estimated_rows_output:,}\n"
                        f"  查询成本: {metrics.query_cost}\n"
                        f"  额外操作: {', '.join(metrics.extra_operations) or '无'}\n\n"
                        f"请改写 SQL 后重试。"
                    ),
                )

            if decision.risk_level == "WARNING":
                return SafetyResult(
                    blocked=False,
                    warnings=[f"[EXPLAIN 评估] {r}" for r in decision.reasons],
                )

            return SafetyResult(blocked=False)

        except Exception as exc:
            # EXPLAIN 自身失败 → 降级放行
            return SafetyResult(
                blocked=False,
                warnings=[f"EXPLAIN 执行失败（{exc}），跳过安全评估，查询已放行"],
            )
        finally:
            try:
                await adapter.disconnect()
            except Exception:
                pass
```

### 5.3 修改：`backend/app/agent/tool_node.py`

两处修改：

**a) `_resolve_checks()` 增加映射（第 38-57 行）：**

```python
from app.agent.safety import SQLAuditCheck, RowEstimationCheck

def _resolve_checks(tool_fn: Any) -> list:
    extras = getattr(tool_fn, "extras", None) or {}
    checks: list = []
    if extras.get("needs_sql_audit"):
        checks.append(SQLAuditCheck())

    if extras.get("needs_row_estimation"):        # 新增
        checks.append(RowEstimationCheck())       # 新增
    return checks
```

**b) `_run_one_tool()` 工具执行成功后，调用结果截断（Phase 4）：**

在第 178 行 `result = await tool_fn.ainvoke(tool_args)` 之后，第 217 行 `tool_content = _json.dumps(...)` 之前：

```python
        # ── 5.5. LLM 上下文窗口保护：截断大结果集 ──
        if isinstance(result, dict) and "rows" in result:
            from app.engine.explain_estimator import truncate_result_for_llm
            result = truncate_result_for_llm(result)
```

### 5.4 修改：`backend/app/agent/tools/query.py`

仅一行变更——`execute_sql` 的 `@tool` 装饰器增加 `needs_row_estimation`:

```python
@tool(extras={
    "needs_sql_audit": True,
    "needs_row_estimation": True,
    "needs_write_confirmation": True,
    "needs_row_estimation": True,  # 新增
})
```

### 5.5 不修改

- `backend/app/config.py` — **不在 .env 中增加配置**，所有阈值硬编码于 `explain_estimator.py`
- `backend/.env.example` — **不增加新配置项**
- `backend/app/agent/safety.py` 中 `PerformanceCheck` — **已移除**，该功能无阻断效果，由 `RowEstimationCheck` 完全接管

---

## 七、与现有 diagnosis.py BOTTLENECK_PATTERNS 的关系

`diagnosis.py` 中的 `BOTTLENECK_PATTERNS` 用于 **LLM 诊断分析**（`analyze_explain()` → 分析瓶颈 → 优化建议），属于查询后的诊断优化流程。本方案的规则引擎用于 **执行前安全拦截**（阻断危险查询），属于事前防护。两者面向不同阶段，互不替代。

---

## 八、行业参考

本方案的设计参考了以下行业实践：

| 参考来源 | 核心思路 | 本方案采纳 |
|---|---|---|
| OpenSpecimen Query Risk Assessment | 多阈值 EXPLAIN JSON 解析（rows_examined_per_scan, rows_produced_per_join, query_cost, filesort/temp rows） | ✅ 多指标规则引擎 |
| pg_plan_filter (PostgreSQL 扩展) | 基于 `total_cost` 的执行前阻断 | ✅ 成本阈值规则（R2） |
| Metabase / DBeaver | 注入 LIMIT / 设置 maxRows 上限 | ✅ Phase 4 结果截断 |
| qail-pg (Rust) | EXPLAIN 预检查（cost + rows + depth） | ✅ 多维评估架构 |
| MCP 安全服务器（SafeDB, dbridge-mcp） | 分层防御：只读 + 行数上限 + EXPLAIN + 超时 | ✅ 双层防护（EXPLAIN 阻断 + 结果截断） |

### 阈值参考值（行业校准）

| 指标 | 保守 | 适中 | 激进 |
|---|---|---|---|
| 全表扫描行数上限 | 1,000 | 5,000 | 50,000 |
| 索引扫描行数上限 | 10,000 | 100,000 | 1,000,000 |
| 结果行数上限 | 100 | 500 | 2,000 |
| 查询成本上限 (MySQL) | 100 | 1,000 | 10,000 |
| 查询成本上限 (PG) | 1,000 | 10,000 | 100,000 |

本方案默认采用**保守**档位，后续根据生产环境实际误报率调整。

---

## 九、验证方案

### 9.1 单元测试：`backend/tests/test_explain_estimator.py`

```
TestExtractMetrics:
  test_mysql_simple_select → ExplainMetrics(FULL_SCAN, 1000000, ..., cost=xxx)
  test_mysql_index_scan → ExplainMetrics(INDEX_SCAN, 100, ...)
  test_mysql_join_multi_table → 取最大 rows_examined_per_scan
  test_mysql_filesort_temp → extra_operations=["filesort", "temporary"]
  test_postgresql_seq_scan → ExplainMetrics(FULL_SCAN, ...)
  test_postgresql_index_scan → ExplainMetrics(INDEX_LOOKUP, ...)
  test_postgresql_width → row_width_bytes 来自 Plan Width
  test_oracle_full_scan → ExplainMetrics(FULL_SCAN, ...)
  test_oracle_k_m_suffix → 10K→10000, 1.5M→1500000
  test_malformed_json → None
  test_empty_output → None

TestEvaluate:
  test_r1_full_scan_block → 全扫+6K行 → CRITICAL
  test_r1_full_scan_allow → 全扫+3K行 → ALLOW（低于阈值）
  test_r3_huge_scan_block → 索引扫描+200K行 → CRITICAL
  test_r4_huge_result_block → 返回 50K 行 → CRITICAL
  test_full_scan_below_threshold_low → 全扫+1K行 → LOW
  test_subcritical_risk_low → 多条规则触发均未达阈值 → LOW
  test_critical_overrides_low → CRITICAL 先命中 → 阻断
  test_all_pass → INDEX_LOOKUP + 10行 → LOW

TestTruncateResult:
  test_small_result_unchanged → 50行 → 不截断
  test_over_row_limit → 500行 → 截断到200行
  test_over_char_limit → 200行但序列化超80KB → 进一步截断
  test_truncation_metadata → 截断后包含 _truncated/_original_total_rows 字段
  test_empty_result → 不报错
  test_non_dict_result → 直接返回
```

### 9.2 集成测试

- Mock 适配器 → 验证 RowEstimationCheck 在安全链中正确阻断/放行
- 验证 EXPLAIN 失败时降级放行
- 验证 tool_node.py 中结果截断被正确调用

### 9.3 端到端

```bash
cd backend && uv run uvicorn app.main:app --reload --port 8000
# 连接测试 DB 后：
# 1. "查询所有订单"（大表全表扫描）→ 被规则 R1 阻断，LLM 收到原因后自动改写
# 2. "查询最近 10 笔订单" → 索引查找，LOW 风险，正常执行
# 3. 手动执行 SELECT * FROM huge_table → R1/R4 阻断
```

---

## 十、变更总结

| 文件 | 操作 | 改动点 |
|---|---|---|
| `backend/app/engine/explain_estimator.py` | **新增** | ~400 行：指标提取 + 规则引擎 + 结果截断 |
| `backend/app/agent/safety.py` | 修改 | +80 行：新增 `RowEstimationCheck` 类 |
| `backend/app/agent/tool_node.py` | 修改 | +5 行：`_resolve_checks` 映射 + 结果截断调用 |
| `backend/app/agent/tools/query.py` | 修改 | +1 行：`needs_row_estimation: True` |
| `backend/tests/test_explain_estimator.py` | **新增** | ~300 行：完整单元测试 |
| `backend/app/config.py` | **不修改** | — |
| `backend/.env.example` | **不修改** | — |
| `backend/app/agent/safety.py` PerformanceCheck | **已移除** | 无阻断效果，由 RowEstimationCheck 完全接管 |

---

# 任务清单

## 方案简述

在 SQL 执行前通过 EXPLAIN 获取多维度指标（访问方式、扫描行数、返回行数、查询成本、额外操作），用硬编码规则引擎逐条匹配评估，CRITICAL 规则命中即阻断执行并反馈 LLM 改写（WARNING 级别已移除，仅保留 CRITICAL / LOW 二分类）。同时在查询执行后对返回 LLM 的结果做行数+字符数双重截断，防止上下文窗口溢出。

## 最终目标

1. **大表全扫拦截**：全表扫描预估 >5000 行 → 阻断，告知 LLM 添加 WHERE 条件或索引
2. **LLM 上下文保护**：预估返回 >10000 行 → 阻断；实际返回 >200 行 → 截断
3. **昂贵操作检测**：filesort + 大数据量 → 警告；临时表 + 大数据量 → 阻断
4. **降级兜底**：EXPLAIN 执行/解析失败 → 警告但放行，不阻断正常业务
5. **不依赖 .env**：所有阈值硬编码于 `explain_estimator.py`，修改走 Code Review
6. **简化护栏链**：SQLAuditCheck 在前审计，RowEstimationCheck 在后 EXPLAIN 评估，已移除无阻断效果的 PerformanceCheck
7. **跨数据库统一**：MySQL / PostgreSQL / Oracle 输出归一化为 `ExplainMetrics` 同一结构

---

## 任务 1：数据模型定义

**描述**：在 `backend/app/engine/explain_estimator.py` 中创建 `ExplainMetrics` 和 `ExplainDecision` 两个 dataclass。

**具体执行**：
- 创建文件 `backend/app/engine/explain_estimator.py`
- 定义 `ExplainMetrics`：`access_pattern: str`, `estimated_rows_examined: int`, `estimated_rows_output: int`, `row_width_bytes: int`, `query_cost: float | None`, `extra_operations: list[str]`
- 定义 `ExplainDecision`：`allowed: bool`, `risk_level: str ("LOW"/"CRITICAL")`, `reasons: list[str]`, `metrics: ExplainMetrics`
- 添加模块级 docstring 说明职责

**验收标准**：
- [ ] 文件创建于 `backend/app/engine/explain_estimator.py`
- [ ] `ExplainMetrics` 含全部 6 个字段，类型标注正确
- [ ] `ExplainDecision` 含全部 4 个字段，类型标注正确
- [ ] 模块级 docstring 说明三块职责（指标提取、规则引擎、结果截断）
- [ ] `ruff check` 无报错

**状态**：⬜ 未开始

---

## 任务 2：MySQL EXPLAIN JSON 解析器

**描述**：实现 MySQL `EXPLAIN FORMAT=JSON` 输出的指标提取。

**具体执行**：
- 实现 `_parse_mysql_explain(json_str: str) -> dict`：
  - `json.loads` 解析 JSON
  - 递归遍历所有节点，收集所有 `rows_examined_per_scan`，取最大值 → `est_rows_examined`
  - 收集所有 `rows_produced_per_join`，取最大值（不存在则 fallback 到 examined） → `est_rows_output`
  - 提取 `cost_info.query_cost`（取最外层 `query_block.cost_info.query_cost`） → `query_cost`
  - 提取 `used_columns` 数量 × 50 → `row_width_bytes`
  - 收集 `using_filesort`、`using_temporary_table` 标志 → `extra_operations`
  - 返回 dict 供 `extract_metrics()` 组装
- 实现辅助函数 `_find_all_values(obj, key) -> list` 用于递归 JSON 遍历
- 所有解析异常捕获返回 `None`

**验收标准**：
- [ ] 单表 `SELECT` 正确提取 `rows_examined_per_scan`
- [ ] 多表 JOIN 取所有表的最大 `rows_examined_per_scan`
- [ ] 子查询/materialized 子查询递归提取
- [ ] `filesort`/`temporary` 正确收集到 `extra_operations`
- [ ] `query_cost` 从顶层正确提取
- [ ] JSON 格式错误返回 `None`（不抛异常）
- [ ] 空字符串返回 `None`

**状态**：⬜ 未开始

---

## 任务 3：PostgreSQL EXPLAIN JSON 解析器

**描述**：实现 PostgreSQL `EXPLAIN (FORMAT JSON, ANALYZE false)` 输出的指标提取。

**具体执行**：
- 实现 `_parse_postgresql_explain(json_str: str) -> dict`：
  - PostgreSQL 输出是 JSON 数组 `[{Plan: {...}}]`，取 `[0]["Plan"]`
  - 取根节点 `Plan Rows` → `est_rows_examined` / `est_rows_output`
  - 取 `Plan Width` → `row_width_bytes`
  - 取 `Total Cost`（第二个数字，从 `0.00..1234.56` 格式解析） → `query_cost`
  - 递归遍历 `Plans` 数组，检测 `Sort`/`Hash`/`Materialize` 节点 → `extra_operations`
  - 返回 dict 供 `extract_metrics()` 组装
- 实现 `_parse_pg_cost(cost_str) -> float` 辅助函数解析 `startup..total` 格式
- 所有异常返回 `None`

**验收标准**：
- [ ] `Seq Scan` 单表查询正确提取
- [ ] 嵌套 `Plans` 递归检测 Sort/Hash/Materialize 节点
- [ ] `Total Cost` 从 `"1234.56..5678.90"` 正确解析为 `5678.90`
- [ ] `Plan Width` 为 int 时正确处理
- [ ] JSON 格式错误/空数组返回 `None`

**状态**：⬜ 未开始

---

## 任务 4：Oracle DBMS_XPLAN 文本解析器

**描述**：实现 Oracle `DBMS_XPLAN.DISPLAY('TYPICAL')` 文本输出的指标提取。

**具体执行**：
- 实现 `_parse_oracle_explain(text: str) -> dict`：
  - 正则匹配表头 `| Id | Operation | Name | Rows | Bytes | Cost (%CPU)| Time |`
  - 匹配数据行 `| 0 | SELECT STATEMENT | | 100K| 120M| 25000 (1)|...`
  - 提取第一行 `Rows` 值（支持 K/M/G 后缀转换） → `est_rows_examined`
  - 提取第一行 `Bytes` 值（支持 K/M/G 后缀） → `bytes`
  - 提取第一行 `Cost` 值（取 `xxx (yy)` 的第一个数字） → `query_cost`
  - 遍历所有数据行的 `Operation` 列，检测 `SORT`/`HASH`/`TEMP`/`TABLE ACCESS FULL` → `extra_operations`
  - 计算 `row_width_bytes = bytes / rows`（若两者都有值）
- 实现 `_parse_oracle_number(s: str) -> int` 处理 K/M/G 后缀
- 所有异常返回 `None`

**验收标准**：
- [ ] `10K` 解析为 `10000`，`1.5M` 解析为 `1500000`
- [ ] 正常格式 `5000` 解析为 `5000`
- [ ] 第一行数据作为根节点提取
- [ ] `TABLE ACCESS FULL` 出现在 Operation 列被正确检测
- [ ] 无数字格式返回 `None`
- [ ] 空文本返回 `None`

**状态**：⬜ 未开始

---

## 任务 5：访问方式分类器 + 额外操作提取

**描述**：实现 `_classify_access_pattern()` 将各数据库原生的访问方式字符串统一转换为 5 种标准类型，以及各 DB 的 `_extract_extra_ops_*` 函数。

**具体执行**：
- 实现 `_classify_access_pattern(raw_type: str, db_type: str) -> str`：
  - MySQL: `ALL` → `FULL_SCAN`, `index` → `INDEX_SCAN`, `range` → `INDEX_SCAN`, `ref/eq_ref/fulltext` → `INDEX_LOOKUP`, `const/system` → `CONST`
  - PG: `Seq Scan` → `FULL_SCAN`, `Index Full Scan`/`Index Range Scan`/`Bitmap Heap Scan` → `INDEX_SCAN`, `Index Scan`/`Index Only Scan` → `INDEX_LOOKUP`
  - Oracle: `TABLE ACCESS FULL` → `FULL_SCAN`, `INDEX FULL SCAN`/`INDEX RANGE SCAN`/`INDEX SKIP SCAN` → `INDEX_SCAN`, `INDEX UNIQUE SCAN`/`TABLE ACCESS BY INDEX ROWID` → `INDEX_LOOKUP`
  - 未知 → `UNKNOWN`
- 实现 `extract_metrics(explain_output: Any, db_type: str, explain_format: str) -> ExplainMetrics | None`：
  - 调度到对应 DB 解析器
  - 调用 `_classify_access_pattern` 统一访问方式
  - 组装为 `ExplainMetrics` 返回

**验收标准**：
- [ ] MySQL 8 种 `access_type` 值全部正确映射
- [ ] PostgreSQL 6 种 `Node Type` 全部正确映射
- [ ] Oracle 6 种 `Operation` 全部正确映射
- [ ] 未知输入返回 `UNKNOWN`，不抛异常
- [ ] `extract_metrics()` 集成三个解析器，输出标准 `ExplainMetrics`
- [ ] 不支持的 `db_type` 返回 `None`

**状态**：⬜ 未开始

---

## 任务 6：规则引擎

**描述**：定义 6 条 CRITICAL 规则 + evaluate() 函数（WARNING 规则已移除）。

**具体执行**：
- 定义阈值常量：`_FULL_SCAN_BLOCK_ROWS=5000`, `_INDEX_SCAN_BLOCK_ROWS=100000`, `_RESULT_BLOCK_ROWS=10000`, `_RESULT_BLOCK_BYTES=500000`, `_COST_BLOCK_RATIO=100.0`, `_TEMPTABLE_BLOCK_ROWS=50000`（WARNING 阈值已移除）
- 定义 `_EVALUATION_RULES: list[dict]`，每条含 `id/severity/condition/message`
- 实现 `evaluate(metrics: ExplainMetrics) -> ExplainDecision`
- 实现 `_get_limit_for_rule(rule_id: str) -> int` 辅助函数
- CRITICAL 命中 → 短路返回 blocked=True
- ~~WARNING 命中 → 收集全部消息~~（WARNING 规则已移除）
- 无规则命中 → LOW 风险返回

**验收标准**：
- [ ] 6 条 CRITICAL 规则按优先级排列正确（R1→R6）
- [x] WARNING 规则已移除（仅保留 CRITICAL 阻断 / LOW 放行）
- [ ] `evaluate()` 返回的 `ExplainDecision` 含 `metrics` 字段供上游日志记录
- [ ] 所有阈值作为模块级 `_UPPER_CASE` 常量

**状态**：⬜ 未开始

---

## 任务 7：LLM 上下文保护 — 结果截断

**描述**：实现 `truncate_result_for_llm()` 函数，在查询结果返回 LLM 前截断超量数据。

**具体执行**：
- 定义常量 `_MAX_LLM_RESULT_ROWS = 200`, `_MAX_LLM_RESULT_CHARS = 80_000`
- 实现 `truncate_result_for_llm(result: dict) -> dict`：
  - 行数 ≤200 且序列化 ≤80K → 不截断直接返回
  - 行数 >200 → 截断 rows 到前 200 行
  - 附加元信息 `_truncated=True`, `_original_total_rows=<原行数>`, `_truncated_to=200`
  - 序列化仍 >80K 时循环减半直到收束（最少保留 10 行）
- 处理边界：空结果、非 dict 结果、rows 不是 list 的结果

**验收标准**：
- [ ] 50 行结果 → 不修改，直接返回
- [ ] 500 行结果 → 截断为 200 行，含截断元信息
- [ ] 200 行但序列化 >80K（列很多） → 循环减半至收束
- [ ] 空 `rows` → 原样返回
- [ ] 非 dict result → 原样返回
- [ ] 截断后 `_original_total_rows` 正确记录原始行数

**状态**：⬜ 未开始

---

## 任务 8：RowEstimationCheck 安全护栏

**描述**：在 `safety.py` 中新增 `RowEstimationCheck` 类，整合 EXPLAIN 执行、指标提取、规则评估。

**具体执行**：
- 新增 `RowEstimationCheck(SafetyCheck)` 类
- 实现 `_is_dml_statement(sql: str) -> bool` 辅助函数（判断是否为 SELECT/INSERT/UPDATE/DELETE/WITH）
- 实现 `_create_temp_adapter(conn_config: dict)` 辅助函数（用 `AdapterFactory` 创建临时连接）
- 实现 `check()` 方法：
  - 空 SQL → 跳过
  - 非 DML → 跳过
  - 创建临时适配器 → `adapter.explain(sql)`
  - 调用 `extract_metrics()` → `evaluate()`
  - CRITICAL → `SafetyResult(blocked=True, reason=详细阻断信息+评估详情)`
  - ~~WARNING → `SafetyResult(blocked=False, warnings=[...])`~~ **已移除**，仅保留 CRITICAL / LOW
  - LOW → `SafetyResult(blocked=False)`
  - 异常 → `SafetyResult(blocked=False, warnings=[降级放行])`
  - `finally` 中确保 `adapter.disconnect()`

**验收标准**：
- [ ] 继承 `SafetyCheck` 并正确实现 `check()` 方法
- [ ] DDL（CREATE/ALTER/DROP）被 `_is_dml_statement` 跳过
- [ ] INSERT VALUES（无 SELECT 子句）被正确识别为 DML 并执行 EXPLAIN
- [ ] EXPLAIN 失败（网络错误/权限不足）→ 降级放行，不阻断
- [ ] 指标提取失败（解析返回 None）→ 警告放行
- [ ] 阻断时 reason 包含：阻断原因 + 评估详情（访问方式/扫描行数/返回行数/成本/额外操作）
- [ ] 适配器在 finally 中正确 disconnect

**状态**：⬜ 未开始

---

## 任务 9：tool_node.py 集成

**描述**：在 `tool_node.py` 中注册 `RowEstimationCheck` 映射，并插入结果截断调用。

**具体执行**：
- 在 `_resolve_checks()` 中新增 `needs_row_estimation` → `RowEstimationCheck()` 映射
- 在 `_run_one_tool()` 中 `result = await tool_fn.ainvoke(tool_args)` 之后，`tool_content = _json.dumps(...)` 之前，插入结果截断逻辑
- 更新 `import` 语句：`from app.agent.safety import ..., RowEstimationCheck`

**验收标准**：
- [ ] `_resolve_checks` 中 `needs_row_estimation` extras 正确创建 `RowEstimationCheck` 实例
- [ ] 仅 `execute_sql`（声明了 extras）触发该检查，其他工具不触发
- [ ] 结果截断对所有工具生效（不止 execute_sql），`isinstance(result, dict) and "rows" in result` 条件正确
- [ ] 截断后的 result 正确参与后续的 `_json.dumps` 序列化
- [ ] `ruff check` 无报错

**状态**：⬜ 未开始

---

## 任务 10：query.py 工具声明

**描述**：在 `execute_sql` 的 `@tool` 装饰器中增加 `needs_row_estimation: True`。

**具体执行**：
- 修改 `backend/app/agent/tools/query.py` 第 305 行
- 在 `extras` 字典中增加 `"needs_row_estimation": True`
- 确认不影响现有的 `needs_sql_audit`、`needs_row_estimation`、`needs_write_confirmation`

**验收标准**：
- [ ] `execute_sql` extras 含 4 个 flag（已移除 needs_performance_check）
- [ ] `explain_query`（diagnosis.py）不添加此 flag
- [ ] 其他工具不添加此 flag
- [ ] `ruff check` 无报错

**状态**：⬜ 未开始

---

## 任务 11：单元测试

**描述**：创建 `backend/tests/test_explain_estimator.py`，覆盖指标提取、规则引擎、结果截断三大模块。

**具体执行**：

**11a. TestExtractMetrics**（约 12 个用例）：
- 编写 MySQL 真实 EXPLAIN JSON 样例（字符串常量）
  - 单表全扫：`{"query_block": {"table": {"access_type": "ALL", "rows_examined_per_scan": 1000000}}}`
  - 索引 Range 扫描：`{"query_block": {"table": {"access_type": "range", "key": "idx_x", "rows_examined_per_scan": 100}}}`
  - 两表 JOIN：含 `nested_loop` 数组
  - filesort + temp：`using_filesort: true, using_temporary_table: true`
  - 子查询：嵌套 `query_block`
- 编写 PostgreSQL 真实 EXPLAIN JSON 样例
  - Seq Scan
  - Index Scan with Index Cond
  - 含 Sort 节点的 Plan
- 编写 Oracle 真实 DBMS_XPLAN 文本样例
  - TABLE ACCESS FULL
  - INDEX RANGE SCAN
  - Rows 列为 `100K` 格式
- 异常输入：空字符串、错误 JSON、无数字

**11b. TestEvaluate**（约 8 个用例）：
- R1: FULL_SCAN + rows=6000 → CRITICAL
- R1: FULL_SCAN + rows=3000 → 低于阈值 → 不命中 R1
- R3: INDEX_SCAN + rows=200000 → CRITICAL
- R4: rows_output=50000 → CRITICAL
- ~~W1: FULL_SCAN + rows=1000 → WARNING~~（已移除，现在为 LOW）
- 同时命中 W1+W3 → 收集 2 条警告
- 同时命中 R1+W1 → 只返回 CRITICAL（短路）
- INDEX_LOOKUP + rows=10 → LOW

**11c. TestTruncateResult**（约 6 个用例）：
- 50 行 → 原样返回（无 `_truncated` 字段）
- 500 行 → rows 截断为 200，含 `_truncated`/`_original_total_rows`
- 200 行但序列化 >80K → 减半截断
- 空 rows(`[]`) → 原样返回
- 非 dict 输入 → 原样返回
- 截断后 `_original_total_rows` 记录原始值

**验收标准**：
- [ ] `pytest backend/tests/test_explain_estimator.py -v` 全部通过
- [ ] 覆盖率 ≥ 90%（explain_estimator.py 模块）
- [ ] 测试数据使用内联字符串/常量，不依赖外部文件或真实数据库

**状态**：⬜ 未开始

---

## 任务 12：集成验证 + 回归测试

**描述**：验证新模块与现有安全链的集成，确保不破坏现有功能。

**具体执行**：
- 运行现有全量测试 `pytest backend/tests/ -v`，确认 0 失败
- 运行 `ruff check backend/app/` 确认 0 报错
- 启动后端 `uv run uvicorn app.main:app --reload --port 8000`
- 通过 API 调用验证：
  1. 连接测试 DB，发送"查询所有订单"（目标大表）→ 被规则 R1 阻断，LLM 收到阻断原因后改写 SQL
  2. 发送"查询最近 10 笔订单" → 索引查找，LOW 风险，正常执行
  3. 发送 `SELECT * FROM <大表>` → 被 R1 或 R4 阻断
- ~~验证 PerformanceCheck 仍正常输出警告~~（已移除）
- 验证 SQLAuditCheck 仍正常拦截 DDL（不因新增检查而失效）

**验收标准**：
- [ ] `pytest backend/tests/ -v` 全部通过，0 失败
- [ ] `ruff check backend/app/` 0 报错
- [ ] 大表全扫被阻断，阻断消息含 EXPLAIN 评估详情
- [ ] 正常查询不被阻断，延迟增加 <50ms
- [x] PerformanceCheck 已移除，SQLAuditCheck 功能不受影响

**状态**：⬜ 未开始

---

## 任务依赖关系

```
任务 1 (数据模型)
  ├─→ 任务 2 (MySQL 解析)
  ├─→ 任务 3 (PG 解析)
  ├─→ 任务 4 (Oracle 解析)
  └─→ 任务 6 (规则引擎)
         │
任务 5 (分类器) ←── 任务 2 + 3 + 4
  └─→ 任务 6 (规则引擎)
         │
任务 7 (结果截断) ──┘
         │
         └─→ 任务 8 (RowEstimationCheck)
                │
                ├─→ 任务 9 (tool_node.py 集成)
                ├─→ 任务 10 (query.py 声明)
                │
                └─→ 任务 11 (单元测试)
                       │
                       └─→ 任务 12 (集成验证)
```

## 建议执行顺序

| 批次 | 任务 | 预估工时 |
|---|---|---|
| 第 1 批 | 任务 1 → 任务 2/3/4/5（数据模型 + 三个解析器 + 分类器可并行） | 3h |
| 第 2 批 | 任务 6 → 任务 7（规则引擎 + 结果截断） | 1.5h |
| 第 3 批 | 任务 8 → 任务 9 → 任务 10（安全护栏 + 集成 + 声明） | 1.5h |
| 第 4 批 | 任务 11（单元测试，可与第 3 批并行准备测试数据） | 2h |
| 第 5 批 | 任务 12（集成验证 + 回归测试） | 1h |
| **合计** | | **~9h** |
