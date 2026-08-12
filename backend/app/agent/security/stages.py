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

    def __init__(self, allowed_stmt_types: tuple[str, ...] | None = None) -> None:
        """初始化 SQL 审计阶段。

        Args:
            allowed_stmt_types: 语句类型白名单（如 ("INSERT", "UPDATE", "DELETE")）。
                经 SECURITY_REGISTRY 的 StageRef.params 注入。配置后强制校验 SQL
                顶层语句类型必须落在名单内（fail-closed）：解析失败 / 多语句 /
                未知类型一律拦截，堵住 sqlglot Command 回退（GRANT/REPLACE 等）与
                解析异常（MERGE/LOAD DATA 等）导致的审计绕过。
                未配置时保持原审计行为（fail-open 语义不变）。
        """
        self._allowed_stmt_types = frozenset(allowed_stmt_types) if allowed_stmt_types else None

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

        return StageResult(blocked=False)


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
            adapter = await self._create_temp_adapter(conn_config)
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

    async def _create_temp_adapter(self, conn_config: dict) -> object:
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
