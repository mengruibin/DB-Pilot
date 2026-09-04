"""
安全阶段实现（security-pipeline-north-star-plan §2.3 / 任务 A2）。

迁移自 app/agent/safety.py：
  - SQLAuditCheck     → SQLAuditStage（PRE_CONFIRM，纯审计无 DB 副作用）
  - RowEstimationCheck → RowEstimationStage（PRE_EXECUTE，确认后允许 DB 副作用）
  - ConnectionLimitCheck → ConnectionLimitStage（可选，默认不注册；重写为无实例状态）
新增：
  - ConfirmStage（CONFIRM，标记型 + build_action 迁移自 graph.py:_build_confirmable_action）

阶段划分依据（interrupt 重放语义）：
  - PRE_CONFIRM 阶段在 interrupt() 之前，重跑两遍 → 必须纯函数（sqlglot 审计 OK，EXPLAIN 不行）
  - PRE_EXECUTE 阶段只在 resume 遍/单遍可达 → 允许 EXPLAIN 等 DB 副作用
"""

from __future__ import annotations

import asyncio
import json as _json
from collections.abc import Mapping
from typing import Any

import structlog

from app.agent.security.models import (
    SecurityContext,
    SecurityStage,
    StagePhase,
    StageResult,
)

logger = structlog.get_logger(__name__)

# =============================================================================
# 连接注入参数集合（ConfirmStage.build_action 过滤，保持 details 干净；
# orchestrator 执行注入时同样使用此集合）
# =============================================================================

CONN_INJECTED_PARAMS = frozenset(
    {
        "connection_id",
        "db_type",
        "host",
        "port",
        "database",
        "user",
        "password",
        "ssl_enabled",
        "ssl_ca_cert",
        "user_role",
    }
)


# =============================================================================
# 临时适配器共享 helper（write-impact-estimate-plan Task 2）
# RowEstimationStage 与 ImpactEstimateStage 共用：从 conn_config 建已连接临时
# 适配器做 EXPLAIN。模块级单一实现，避免阶段间复制（测试可 patch 本函数注入 mock）。
# =============================================================================


async def _create_temp_adapter(conn_config: dict) -> object:
    """从连接配置创建临时数据库适配器（已连接）。

    Args:
        conn_config: 连接配置字典（含 host/port/user/password/db_type 等）。

    Returns:
        数据库适配器实例（已连接）。
    """
    from app.db.factory import AdapterFactory
    from app.models.schemas import ConnectionCreateRequest

    conn_id = conn_config.get("connection_id", "")

    config = ConnectionCreateRequest(
        name=f"est_{conn_id}",
        db_type=conn_config["db_type"],  # type: ignore[arg-type]
        host=conn_config["host"],
        port=conn_config["port"],
        database=conn_config["database"],
        user=conn_config["user"],
        password=conn_config.get("password", ""),
        ssl_enabled=conn_config.get("ssl_enabled", False),
        ssl_ca_cert=conn_config.get("ssl_ca_cert"),
    )

    adapter = AdapterFactory.create(conn_config["db_type"], config)
    await adapter.connect(config, user_role=conn_config.get("user_role", "readonly"))
    return adapter


# =============================================================================
# SQLAuditStage — SQL 审计（含只读角色权限拦截）
# =============================================================================


class SQLAuditStage(SecurityStage):
    """SQL 审计阶段（PRE_CONFIRM，纯审计无 DB 副作用）。

    对 SQL 类工具传入的 SQL 执行 sqlglot 审计，同时负责非 admin 角色的
    权限拦截（通过 sql_auditor._ADMIN_ONLY_STATEMENTS）。
    拦截时返回结构化 block_code="SQL_AUDIT_BLOCKED"。

    依据 AGENTS.md §安全与合规红线：
      - 绝对禁止: DROP/ALTER/TRUNCATE/CREATE/GRANT/REVOKE
      - DELETE/UPDATE/INSERT/MERGE 仅 admin 角色可通过
      - 多语句直接拦截

    放在 PRE_CONFIRM 相位：确认卡片弹出之前即拦截红线 DDL / 非 admin 写操作，
    使 DDL 不再进入确认流（对"绝对禁止"的严格化收敛）。
    """

    name = "sql_audit"
    phase = StagePhase.PRE_CONFIRM

    def __init__(
        self,
        allowed_stmt_types: tuple[str, ...] | None = None,
        require_where: bool = False,
    ) -> None:
        """初始化 SQL 审计阶段。

        Args:
            allowed_stmt_types: 语句类型白名单（如 ("INSERT", "UPDATE", "DELETE")）。
                经 SECURITY_REGISTRY 的 StageRef.params 注入。配置后强制校验 SQL
                顶层语句类型必须落在名单内（fail-closed）：解析失败 / 多语句 /
                未知类型一律拦截，堵住 sqlglot Command 回退（GRANT/REPLACE 等）与
                解析异常（MERGE/LOAD DATA 等）导致的审计绕过。
                未配置时保持原审计行为（fail-open 语义不变）。
            require_where: 为 True 时，UPDATE/DELETE 缺少 WHERE（或 WHERE 为不含
                列引用的纯常量恒真表达式）→ 拦截（block_code="WRITE_NO_WHERE_BLOCKED"）。
                经 SECURITY_REGISTRY 的 StageRef.params 注入；默认 False 保持原行为。
        """
        self._allowed_stmt_types = frozenset(allowed_stmt_types) if allowed_stmt_types else None
        self._require_where = require_where

    async def check(
        self,
        tool_call: Mapping[str, Any],
        conn_config: dict[str, Any],
        ctx: SecurityContext,
    ) -> StageResult:
        """对 SQL 类工具调用执行审计检查（含语句类型白名单硬约束）。"""
        sql = (tool_call.get("args") or {}).get("sql", "")
        if not sql:
            return StageResult(blocked=False)

        db_type = conn_config.get("db_type", "mysql")
        user_role = conn_config.get("user_role", "readonly")
        return self._audit_single_sql(sql, db_type, user_role)

    def _audit_single_sql(self, sql: str, db_type: str, user_role: str) -> StageResult:
        """对单条 SQL 执行三步审计（语句类型白名单 → sqlglot 审计 → require_where）。

        单语句审计（check）与事务审计（TransactionAuditStage 逐条复用）的单一事实源。
        blocked=False 表示通过；fail-open 语义（审计模块 ImportError / 审计异常 →
        放行带 warning）保持。
        """
        # ── Step 1: 语句类型白名单（工具契约硬约束，fail-closed）──
        # 白名单配置后，SQL 顶层语句类型必须落在名单内；解析失败 / 多语句 /
        # 未知类型一律拦截（不允许"无法确认即放行"），
        # 堵住 sqlglot Command 回退（GRANT/REPLACE 等）与解析异常（MERGE 等）绕过。
        if self._allowed_stmt_types is not None:
            from app.engine.sql_auditor import get_statement_types

            stmt_types, parse_error = get_statement_types(sql, db_type)
            allowed_str = " / ".join(sorted(self._allowed_stmt_types))
            if parse_error:
                return StageResult(
                    blocked=True,
                    block_code="SQL_STMT_TYPE_BLOCKED",
                    reason=f"SQL 无法解析，无法确认语句类型（仅允许 {allowed_str}），已拦截",
                )
            if len(stmt_types) != 1 or stmt_types[0] not in self._allowed_stmt_types:
                actual = stmt_types[0] if stmt_types else "（空）"
                return StageResult(
                    blocked=True,
                    block_code="SQL_STMT_TYPE_BLOCKED",
                    reason=(f"该工具只允许 {allowed_str} 类型语句，实际为 {actual}，已拦截"),
                )

        # ── Step 2: 现有 sqlglot 审计（fail-open 语义保留）──
        try:
            from app.engine.sql_auditor import audit

            result = audit(sql, db_type, user_role=user_role)

            if not result.passed:
                violations_desc = [f"{v.type}: {v.message}" for v in result.violations]
                return StageResult(
                    blocked=True,
                    block_code="SQL_AUDIT_BLOCKED",
                    reason=f"SQL 审计未通过: {'; '.join(violations_desc)}",
                    context={"audit_result": result},
                )
        except ImportError:
            # 审计模块不可用——放行（有风险，但避免阻断正常使用），保留 fail-open 语义
            return StageResult(
                blocked=False,
                warnings=["SQL 审计模块不可用——此查询未经安全审计"],
            )
        except Exception as exc:
            # 审计异常——放行但记录警告
            return StageResult(
                blocked=False,
                warnings=[f"SQL 审计异常（已放行）: {str(exc)[:100]}"],
            )

        # ── Step 3: UPDATE/DELETE 范围检查（require_where 硬约束）──
        # 无 WHERE（或 WHERE 为纯常量恒真表达式）的 UPDATE/DELETE 等价全表删改，
        # 属高危操作，确认卡之前即拦截（与红线 DDL 同策略，fail-closed）。
        # 置于既有审计（Step 2）之后：readonly 的权限不足先报 SQL_AUDIT_BLOCKED，
        # 只有审计通过的 admin 写操作才进入本检查（WRITE_NO_WHERE_BLOCKED）。
        if self._require_where:
            from app.engine.sql_auditor import check_write_scope

            scope = check_write_scope(sql, db_type)
            if scope.is_full_table:
                target = scope.target_table or "（未知表）"
                return StageResult(
                    blocked=True,
                    block_code="WRITE_NO_WHERE_BLOCKED",
                    reason=(
                        f"UPDATE/DELETE 缺少有效的 WHERE 条件，"
                        f"将影响表 {target} 的全部行，已拦截。\n"
                        "请补充具体的 WHERE 条件（如主键/唯一键/时间范围）限定操作范围后重试。\n"
                        "全表更新/删除属高危操作，无法经本工具执行；如确需全表操作，"
                        "请先在数据库客户端中确认数据量后手动执行。"
                    ),
                    context={"write_scope": scope},
                )

        return StageResult(blocked=False)


# =============================================================================
# TransactionAuditStage — 事务写工具逐条审计（PRE_CONFIRM）
# =============================================================================


class TransactionAuditStage(SQLAuditStage):
    """事务写工具（execute_write_transaction）审计阶段（PRE_CONFIRM，纯审计）。

    对 statements 列表逐条复用 SQLAuditStage._audit_single_sql——与
    execute_write_sql 同一套类型白名单 + sqlglot 审计 + require_where 规则，
    不引入独立审计引擎逻辑（安全行为单一事实源）。任一语句被拦 → 整事务拦截，
    reason 带上语句索引与预览。空列表 / 非列表 / 空白语句 fail-closed。
    """

    name = "transaction_sql_audit"
    phase = StagePhase.PRE_CONFIRM

    async def check(
        self,
        tool_call: Mapping[str, Any],
        conn_config: dict[str, Any],
        ctx: SecurityContext,
    ) -> StageResult:
        """逐条审计 statements（PRE_CONFIRM 纯审计，无 DB 副作用）。"""
        statements = (tool_call.get("args") or {}).get("statements")
        db_type = conn_config.get("db_type", "mysql")
        user_role = conn_config.get("user_role", "readonly")

        # 参数形态归一：PRE_CONFIRM 阶段看到的是 LLM 原始参数——部分 LLM（如
        # DeepSeek）会把 list 参数传成 JSON 字符串（orchestrator._coerce_tool_args
        # 只在 Phase3 执行前修正），此处同样尝试解析，避免合法调用被误拦。
        if isinstance(statements, str):
            try:
                parsed = _json.loads(statements)
            except (_json.JSONDecodeError, TypeError):
                parsed = None
            if isinstance(parsed, list):
                statements = parsed

        # 参数形态 fail-closed：非列表 / 空列表 → 拦（不允许"确认空事务"或传错类型）
        if not isinstance(statements, list) or not statements:
            return StageResult(
                blocked=True,
                block_code="TRANSACTION_INVALID_ARGUMENTS",
                reason=(
                    "execute_write_transaction 的 statements 参数必须是非空字符串列表，"
                    "实际收到空值或非列表，已拦截"
                ),
            )

        all_warnings: list[str] = []
        for idx, raw in enumerate(statements):
            sql = str(raw).strip()
            if not sql:
                return StageResult(
                    blocked=True,
                    block_code="TRANSACTION_INVALID_ARGUMENTS",
                    reason=f"第 {idx + 1} 条语句为空或纯空白，已拦截（事务不允许空语句）",
                )
            result = self._audit_single_sql(sql, db_type, user_role)
            if result.blocked:
                preview = sql[:120] + ("..." if len(sql) > 120 else "")
                # 保留原始 block_code（SQL_STMT_TYPE_BLOCKED / SQL_AUDIT_BLOCKED /
                # WRITE_NO_WHERE_BLOCKED），reason 补语句索引 + 预览
                return StageResult(
                    blocked=True,
                    block_code=result.block_code or "SQL_AUDIT_BLOCKED",
                    reason=f"第 {idx + 1} 条语句被拦截：{result.reason}\n语句预览：{preview}",
                    context=result.context,
                )
            all_warnings.extend(result.warnings)
        return StageResult(blocked=False, warnings=all_warnings)


# =============================================================================
# RowEstimationStage — EXPLAIN 多维度安全评估
# =============================================================================

# 需要执行 EXPLAIN 检查的 SQL 语句前缀
_DML_PREFIXES = ("SELECT", "INSERT", "UPDATE", "DELETE", "WITH", "MERGE", "REPLACE")


def _is_dml_statement(sql: str) -> bool:
    """判断 SQL 语句是否需要进行 EXPLAIN 检查。

    仅对可能产生大量数据的 DML 语句进行检查，DDL/元数据操作跳过。
    使用简单前缀匹配，轻量快速（不依赖 sqlglot）。
    """
    sql_upper = sql.strip().upper()
    return any(sql_upper.startswith(prefix) for prefix in _DML_PREFIXES)


class RowEstimationStage(SecurityStage):
    """EXPLAIN 多维度安全评估阶段（PRE_EXECUTE，确认后只跑一遍）。

    在 SQL 执行前通过 EXPLAIN 提取多维度指标（访问方式、扫描行数、返回行数、
    查询成本、额外操作），用硬编码规则引擎评估，超阈值即阻断。
    不依赖 .env 配置，所有阈值在 explain_estimator.py 中 Code Review 管理。

    拦截时返回结构化 block_code="ROW_ESTIMATION_BLOCKED"——
    供 orchestrator 用 block_code 识别（替代旧 `"[EXPLAIN 安全评估]" in reason`
    字符串特征耦合），驱动 consecutive_blocks 防改写死循环计数。

    放 PRE_EXECUTE 相位：EXPLAIN 需建临时 DB 连接（有副作用），且确认后才
    允许执行；interrupt 首遍不会到达本阶段，保证 EXPLAIN 恰执行一次。
    EXPLAIN 失败时降级放行，不阻断正常业务（fail-open 语义保留）。
    """

    name = "row_estimation"
    phase = StagePhase.PRE_EXECUTE

    async def check(
        self,
        tool_call: Mapping[str, Any],
        conn_config: dict[str, Any],
        ctx: SecurityContext,
    ) -> StageResult:
        """执行 EXPLAIN 多维度安全评估。

        执行流程：
          1. 空 SQL / 非 DML → 跳过
          2. 创建临时适配器连接 → adapter.explain(sql)
          3. 提取标准化 ExplainMetrics → 规则引擎评估
          4. CRITICAL → 阻断（返回详细评估详情）
             LOW → 放行
        """
        sql = (tool_call.get("args") or {}).get("sql", "")
        if not sql:
            logger.debug("RowEstimationStage 跳过：空 SQL", tool_call_id=tool_call.get("id"))
            return StageResult(blocked=False)

        # 仅检查 DML 语句（DDL 已被 SQLAuditCheck 拦截）
        if not _is_dml_statement(sql):
            logger.debug(
                "RowEstimationStage 跳过：非 DML 语句",
                tool_call_id=tool_call.get("id"),
                sql_preview=sql[:80],
            )
            return StageResult(blocked=False)

        db_type = conn_config.get("db_type", "mysql")
        logger.info(
            "RowEstimationStage 开始评估",
            tool_call_id=tool_call.get("id"),
            sql_preview=sql[:200],
            db_type=db_type,
            database=conn_config.get("database", ""),
        )

        adapter: Any = None
        explain_raw: str = ""

        # ── Step 1: 创建临时适配器并执行 EXPLAIN ──
        try:
            adapter = await _create_temp_adapter(conn_config)
        except Exception as exc:
            logger.error(
                "RowEstimationStage 适配器创建失败",
                error=str(exc)[:300],
                db_type=db_type,
                exc_info=True,
            )
            return StageResult(
                blocked=False,
                warnings=[f"EXPLAIN 适配器创建失败（{exc}），跳过安全评估，查询已放行"],
            )

        # ── Step 2: 执行 EXPLAIN ──
        try:
            explain_result = await adapter.explain(sql)  # type: ignore[union-attr]
            explain_raw = explain_result.get("explain_output", "")
        except Exception as exc:
            logger.error(
                "RowEstimationStage EXPLAIN 执行失败",
                error=str(exc)[:300],
                tool_call_id=tool_call.get("id"),
                exc_info=True,
            )
            return StageResult(
                blocked=False,
                warnings=[f"EXPLAIN 执行失败（{exc}），跳过安全评估，查询已放行"],
            )
        finally:
            # 确保适配器断开
            if adapter is not None:
                try:  # noqa: SIM105
                    await adapter.disconnect()
                except Exception:
                    pass

        # ── Step 3: 提取标准化指标 ──
        from app.engine.explain_estimator import evaluate, extract_metrics

        try:
            metrics = extract_metrics(
                explain_output=explain_raw,
                db_type=db_type,
            )
        except Exception as exc:
            logger.error(
                "RowEstimationStage 指标提取异常",
                error=str(exc)[:200],
                tool_call_id=tool_call.get("id"),
                exc_info=True,
            )
            return StageResult(
                blocked=False,
                warnings=[f"EXPLAIN 指标提取异常（{exc}），跳过安全评估，查询已放行"],
            )

        if metrics is None:
            logger.warning(
                "RowEstimationStage 指标提取失败：解析器返回 None",
                tool_call_id=tool_call.get("id"),
                explain_raw_preview=explain_raw[:300],
                db_type=db_type,
            )
            return StageResult(
                blocked=False,
                warnings=["EXPLAIN 指标提取失败（解析器返回空），跳过安全评估，查询已放行"],
            )

        logger.info(
            "RowEstimationStage 指标提取成功",
            access_pattern=metrics.access_pattern,
            estimated_rows_examined=metrics.estimated_rows_examined,
            estimated_rows_output=metrics.estimated_rows_output,
            row_width_bytes=metrics.row_width_bytes,
            query_cost=metrics.query_cost,
            extra_operations=metrics.extra_operations,
        )

        # ── Step 4: 规则引擎评估 ──
        decision = evaluate(metrics)

        logger.info(
            "RowEstimationStage 规则引擎决策",
            allowed=decision.allowed,
            risk_level=decision.risk_level,
            reasons=decision.reasons,
        )

        if not decision.allowed:
            reason = (
                f"[EXPLAIN 安全评估] 查询被阻断\n"
                f"原因: {decision.reasons[0]}\n\n"
                f"评估详情:\n"
                f"  访问方式: {metrics.access_pattern}\n"
                f"  预估扫描行数: {metrics.estimated_rows_examined:,}\n"
                f"  预估返回行数: {metrics.estimated_rows_output:,}\n"
                f"  查询成本: {metrics.query_cost}\n"
                f"  额外操作: {', '.join(metrics.extra_operations) or '无'}\n\n"
                f"请改写 SQL 后重试。"
            )
            logger.warning(
                "RowEstimationStage 阻断查询",
                tool_call_id=tool_call.get("id"),
                reason=decision.reasons[0],
                sql_preview=sql[:200],
                access_pattern=metrics.access_pattern,
                estimated_rows=metrics.estimated_rows_examined,
            )
            return StageResult(
                blocked=True,
                block_code="ROW_ESTIMATION_BLOCKED",
                reason=reason,
                context={"metrics": metrics, "decision": decision},
            )

        # LOW 风险 — 放行
        logger.debug(
            "RowEstimationStage 评估通过（LOW 风险）",
            tool_call_id=tool_call.get("id"),
            sql_preview=sql[:100],
        )
        return StageResult(blocked=False)


# =============================================================================
# ImpactEstimateStage — 写操作影响范围预估（write-impact-estimate-plan）
# =============================================================================


class ImpactEstimateStage(SecurityStage):
    """写操作预估影响范围阶段（PRE_CONFIRM，只读 EXPLAIN，永不拦截）。

    对写工具（execute_write_sql / execute_write_transaction）的 UPDATE/DELETE/
    INSERT..SELECT 执行 EXPLAIN（各引擎只出计划不执行语句），用
    write_impact.extract_write_impact 换算「预估受影响行数」，写入
    ctx.evidence[tool_call_id]["impact"]，供 orchestrator 在 interrupt 前组装
    确认 payload 时读入 writes[].impact → 前端确认卡展示。

    与 RowEstimationStage 的关键差异：
      - RowEstimationStage 是安全闸门（PRE_EXECUTE，超阈值阻断）；
        ImpactEstimateStage 是信息增强（PRE_CONFIRM，恒 blocked=False，不拦截）。
      - 放 PRE_CONFIRM 位置（在 sql_audit 之后、confirm 之前）是为让估算在
        interrupt 之前产出；红线 DDL / WRITE_NO_WHERE 在 sql_audit 即被拦截，
        该工具不会进入本阶段（短路，不花 EXPLAIN）。

    重放语义（重要）：PRE_CONFIRM 在 interrupt 重放时跑两遍，本阶段会重复一次
    只读 EXPLAIN——首遍产出卡片内容，resume 遍重算一次（仅开销、不影响正确性，
    卡片在首遍已发给用户）。写审批低频，先接受重复；后续可加
    (run_id, tool_call_id) 短 TTL memo 命中跳过。
    """

    name = "impact_estimate"
    phase = StagePhase.PRE_CONFIRM

    # 写估算超时（秒）：EXPLAIN 大表计划可能偏慢，超时即放弃本工具的影响展示
    # （fail-open，不阻塞确认卡出现与用户确认）。
    _ESTIMATE_TIMEOUT_SEC = 3.0

    async def check(
        self,
        tool_call: Mapping[str, Any],
        conn_config: dict[str, Any],
        ctx: SecurityContext,
    ) -> StageResult:
        """执行写影响预估（只读 EXPLAIN），写入 ctx.evidence，恒不拦截。"""
        args = tool_call.get("args") or {}
        tool_name = tool_call.get("name", "")

        # 事务写工具：statements 逐条预估；单语句写工具：sql 单条预估
        statements: list[str]
        if tool_name == "execute_write_transaction":
            raw_statements = args.get("statements")
            if isinstance(raw_statements, str):
                try:
                    raw_statements = _json.loads(raw_statements)
                except (_json.JSONDecodeError, TypeError):
                    raw_statements = None
            if not isinstance(raw_statements, list) or not raw_statements:
                return StageResult(blocked=False)
            statements = [str(s) for s in raw_statements if str(s).strip()]
        else:
            sql = args.get("sql", "")
            if not sql:
                return StageResult(blocked=False)
            statements = [str(sql)]

        db_type = conn_config.get("db_type", "mysql")

        impact = await self._estimate_statements(
            statements, db_type, conn_config, tool_call_id=tool_call.get("id", "")
        )
        if impact is not None:
            ctx.evidence[tool_call.get("id", "")] = ctx.evidence.get(tool_call.get("id", ""), {})
            ctx.evidence[tool_call.get("id", "")]["impact"] = impact

        # 信息增强阶段：永不拦截
        return StageResult(blocked=False)

    async def _estimate_statements(
        self,
        statements: list[str],
        db_type: str,
        conn_config: dict[str, Any],
        tool_call_id: str,
    ) -> dict | None:
        """对一批写语句逐条 EXPLAIN 预估，汇聚成 impact 结构。

        单语句返回：
            {available, stmt_type, target_table, estimated_rows,
             method, high_impact, note}
        多语句（事务）返回：
            {available, stmt_count, per_statement: [...], high_impact, note}
        全部无法预估 / 无 DB 依赖可估语句 → 返回 None（卡片不展示 impact）。
        """
        single = len(statements) == 1

        if single:
            sql = statements[0]
            impact = await self._estimate_one(sql, db_type, conn_config, tool_call_id)
            if impact is None:
                return None
            return {
                "available": True,
                "stmt_type": impact["stmt_type"],
                "target_table": impact["target_table"],
                "estimated_rows": impact["estimated_rows"],
                "method": "explain",
                "high_impact": impact["high_impact"],
                "note": "EXPLAIN 执行计划预估，非精确值，实际受影响行数可能不同",
            }

        per_statement: list[dict] = []
        any_high = False
        any_estimable = False
        for idx, sql in enumerate(statements):
            impact = await self._estimate_one(sql, db_type, conn_config, tool_call_id)
            if impact is None:
                per_statement.append({"idx": idx + 1, "stmt_type": None, "estimated_rows": None})
                continue
            any_estimable = True
            any_high = any_high or bool(impact["high_impact"])
            per_statement.append(
                {
                    "idx": idx + 1,
                    "stmt_type": impact["stmt_type"],
                    "estimated_rows": impact["estimated_rows"],
                }
            )
        if not any_estimable:
            return None
        return {
            "available": True,
            "stmt_count": len(statements),
            "per_statement": per_statement,
            "high_impact": any_high,
            "method": "explain",
            "note": "EXPLAIN 执行计划预估，非精确值，实际受影响行数可能不同",
        }

    async def _estimate_one(
        self,
        sql: str,
        db_type: str,
        conn_config: dict[str, Any],
        tool_call_id: str,
    ) -> dict | None:
        """对单条写语句执行只读 EXPLAIN 并预估影响（带超时，失败返回 None）。"""
        from app.engine.write_impact import extract_write_impact

        adapter: Any = None
        try:
            async with asyncio.timeout(self._ESTIMATE_TIMEOUT_SEC):
                # 先静态判定是否可估（字面量 INSERT / 非写语句 → 无需连库）
                # 直接交由 extract_write_impact 的 classify_write 兜底：
                # 但为省一次 EXPLAIN，先试 EXPLAIN 本身不区分——字面量 INSERT 对
                # MySQL EXPLAIN 可能报错，走 fail-open 即可。为稳妥先 classify。
                from app.engine.write_impact import classify_write

                info = classify_write(sql, db_type)
                if info is None:
                    return None
                if info["stmt_type"] == "INSERT" and not info["has_select_source"]:
                    # 字面量 INSERT：可静态数出行数（无需连库），仅当列数有限时展示
                    return None

                adapter = await _create_temp_adapter(conn_config)
                explain_result = await adapter.explain(sql)
                explain_raw = (
                    explain_result.get("explain_output", "")
                    if isinstance(explain_result, dict)
                    else str(explain_result)
                )
                return extract_write_impact(sql, db_type, explain_raw)
        except TimeoutError:
            logger.debug(
                "ImpactEstimateStage 估算超时（放弃该语句的影响展示）",
                tool_call_id=tool_call_id,
                sql_preview=str(sql)[:120],
                db_type=db_type,
            )
            return None
        except Exception as exc:
            logger.debug(
                "ImpactEstimateStage 估算失败（fail-open，不影响审批）",
                tool_call_id=tool_call_id,
                error=str(exc)[:200],
                db_type=db_type,
            )
            return None
        finally:
            if adapter is not None:
                try:  # noqa: SIM105
                    await adapter.disconnect()
                except Exception:
                    pass


# =============================================================================
# ConfirmStage — 用户确认（标记型，orchestrator 批量 interrupt）
# =============================================================================


class ConfirmStage(SecurityStage):
    """用户确认阶段（CONFIRM，标记型）。

    check() 恒 blocked=False —— 本阶段不执行拦截，真正的确认由 orchestrator
    在 Phase 2 对含 CONFIRM 阶段的工具批量调用 interrupt() 完成。

    build_action(tool_call, category) 从工具调用构建"需确认操作"记录
    （迁移自 graph.py:_build_confirmable_action），产出：
        {tool_call_id, tool, category, description, details}
    连接注入参数（CONN_INJECTED_PARAMS）被过滤，保持 description/details 干净。
    """

    name = "confirm"
    phase = StagePhase.CONFIRM

    def __init__(self, category: str = "generic") -> None:
        """初始化确认阶段。

        Args:
            category: 确认卡片分类（sql_write / connection_kill / generic）。
                经 STAGE_REGISTRY[ref.name](**ref.params) 注入；check() 不消费，
                实际分类在 build_action 调用处（orchestrator 读取 ref.params）使用，
                构造函数仅接收以保证 orchestrator 统一传参。
        """
        self.category = category

    async def check(
        self,
        tool_call: Mapping[str, Any],
        conn_config: dict[str, Any],
        ctx: SecurityContext,
    ) -> StageResult:
        """标记型阶段：不拦截，返回放行。"""
        return StageResult(blocked=False)

    @staticmethod
    def build_action(tool_call: Mapping[str, Any], category: str = "generic") -> dict[str, Any]:
        """从工具调用构建"需确认操作"记录（过滤连接注入参数）。

        Args:
            tool_call: LangChain ToolCall 字典（含 id / name / args）。
            category: 确认卡片分类（sql_write / connection_kill / generic）。

        Returns:
            {tool_call_id, tool, category, description, details}。
        """
        tool_name = tool_call["name"]
        raw_args = dict(tool_call.get("args") or {})

        # 过滤连接注入参数（保持 description/details 干净）
        key_args: dict[str, Any] = {
            k: v for k, v in raw_args.items() if k not in CONN_INJECTED_PARAMS
        }

        # 自动生成人类可读描述
        parts: list[str] = []
        for k, v in key_args.items():
            v_str = str(v)
            if len(v_str) > 80:
                v_str = v_str[:77] + "..."
            parts.append(f"{k}={v_str}")
        arg_desc = ", ".join(parts)
        description = tool_name + (f": {arg_desc}" if arg_desc else "")

        # 事务写工具（execute_write_transaction）：statements 列表 → 注入
        # details.sql（分号换行连接），前端 sql_write 确认卡片据此在单个代码块
        # 展示全部 SQL（WriteConfirmation.vue 渲染 details?.sql ?? description）。
        # 仅对含 statements 列表的调用生效，不影响其它工具（table_names 等）。
        if isinstance(key_args.get("statements"), list):
            key_args["sql"] = ";\n".join(str(s) for s in key_args["statements"])

        return {
            "tool_call_id": tool_call["id"],
            "tool": tool_name,
            "category": category,
            "description": description,
            "details": key_args,
        }


# =============================================================================
# ConnectionLimitStage — 连接限额（可选，默认不注册）
# =============================================================================


class ConnectionLimitStage(SecurityStage):
    """连接限额阶段（可选，默认不注册）。

    原 safety.py 中的 ConnectionLimitCheck 是死代码（定义了但从未被引用）。
    本方案将其重写为无实例状态的阶段：查询计数经 ctx.evidence 累计，
    阶段实例不维护自身可变状态（可在多次编排间安全复用）。

    默认不注册进 STAGE_REGISTRY（避免再次引入死代码）；如需启用，在
    STAGE_REGISTRY 中注册并给目标工具的 SecurityProfile 声明该阶段。
    """

    name = "connection_limit"
    phase = StagePhase.PRE_EXECUTE

    def __init__(self, max_queries: int = 50) -> None:
        """初始化连接限额。

        Args:
            max_queries: 单次会话最大查询次数。
        """
        self._max_queries = max_queries

    async def check(
        self,
        tool_call: Mapping[str, Any],
        conn_config: dict[str, Any],
        ctx: SecurityContext,
    ) -> StageResult:
        """检查是否超出查询次数限制（计数存 ctx.evidence，无实例状态）。"""
        counter_key = "connection_limit_query_count"
        count = int(ctx.evidence.get(counter_key, 0)) + 1
        ctx.evidence[counter_key] = count
        if count > self._max_queries:
            return StageResult(
                blocked=True,
                block_code="CONNECTION_LIMIT_EXCEEDED",
                reason=f"单次会话查询次数已达上限（{self._max_queries}次）",
            )
        return StageResult(blocked=False)
