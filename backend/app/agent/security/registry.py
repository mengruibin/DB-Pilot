"""
安全注册表——安全策略单一事实源（security-pipeline-north-star-plan §2.4）。

STAGE_REGISTRY：阶段名 → 阶段类（orchestrator 按 StageRef.name 实例化）。
SECURITY_REGISTRY：11 个 Agent 工具 → SecurityProfile（显式全列，含空 profile）。

约定（CLAUDE.md Key Conventions）：
  - 安全行为一律经本模块声明，禁止在工具 extras 或图节点里散落安全逻辑。
  - 新增危险操作工具只需在此登记对应阶段即可接入安全流水线。
  - 未收录工具返回空 profile（无安全阶段），与现状"未声明需求的工具跳过安全检查"一致。
"""

from __future__ import annotations

from app.agent.security.models import (
    SecurityProfile,
    SecurityStage,
    StagePhase,
    StageRef,
)
from app.agent.security.stages import (
    ConfirmStage,
    RowEstimationStage,
    SQLAuditStage,
)

# =============================================================================
# 阶段注册表：阶段名 → 阶段类
# =============================================================================

STAGE_REGISTRY: dict[str, type[SecurityStage]] = {
    "sql_audit": SQLAuditStage,  # sqlglot 纯审计（PRE_CONFIRM）
    "row_estimation": RowEstimationStage,  # EXPLAIN 多维度评估（PRE_EXECUTE）
    "confirm": ConfirmStage,  # 用户确认标记（CONFIRM）
}

# =============================================================================
# 安全注册表：12 个工具 → SecurityProfile（安全策略单一事实源）
# =============================================================================

SECURITY_REGISTRY: dict[str, SecurityProfile] = {
    # ── 查询类工具 ──
    # list_tables / describe_table 只读元数据，无安全阶段
    "list_tables": SecurityProfile(),
    "describe_table": SecurityProfile(),
    # execute_readonly_sql：前置 sql 审计（PRE_CONFIRM 纯审计）
    # + 语句类型白名单（只放行只读语句，堵住 GRANT/REPLACE/MERGE 等 Command 回退绕过）
    # + 确认后 EXPLAIN 安全评估（PRE_EXECUTE，只跑一遍）
    "execute_readonly_sql": SecurityProfile(
        stages=(
            StageRef(
                "sql_audit",
                StagePhase.PRE_CONFIRM,
                params={"allowed_stmt_types": ("SELECT", "SHOW", "EXPLAIN", "UNION", "USE", "SET")},
            ),
            StageRef("row_estimation", StagePhase.PRE_EXECUTE),
        ),
    ),
    # execute_write_sql：★ 补审计缺口——写操作确认前先过 SQLAuditCheck，
    # 红线 DDL / 非 admin 写操作在确认流之前即被拦（不再弹确认卡片）
    # + 语句类型白名单（只放行 INSERT/UPDATE/DELETE，同堵 Command 回退绕过）
    "execute_write_sql": SecurityProfile(
        stages=(
            StageRef(
                "sql_audit",
                StagePhase.PRE_CONFIRM,
                params={"allowed_stmt_types": ("INSERT", "UPDATE", "DELETE")},
            ),
            StageRef("confirm", StagePhase.CONFIRM, params={"category": "sql_write"}),
        ),
    ),
    # ── 诊断类工具 ──
    "get_slow_queries": SecurityProfile(),
    # explain_query：前置 sql 审计（EXPLAIN 自身的 sql 也需审计）
    "explain_query": SecurityProfile(
        stages=(StageRef("sql_audit", StagePhase.PRE_CONFIRM),),
    ),
    # ── 故障排查类工具 ──
    "check_connections": SecurityProfile(),
    "check_locks": SecurityProfile(),
    "analyze_locks": SecurityProfile(),
    # kill_transaction：终止连接是危险操作，走 connection_kill 确认流
    "kill_transaction": SecurityProfile(
        stages=(StageRef("confirm", StagePhase.CONFIRM, params={"category": "connection_kill"}),),
    ),
    "check_replication": SecurityProfile(),
    # ── 健康巡检类工具 ──
    "run_health_check": SecurityProfile(),
}


def get_security_profile(tool_name: str) -> SecurityProfile:
    """获取工具的安全配置文件。

    Args:
        tool_name: 工具名（TOOL_REGISTRY 键）。

    Returns:
        SecurityProfile — 未收录的工具返回空 profile（无安全阶段，跳过安全检查）。
    """
    return SECURITY_REGISTRY.get(tool_name, SecurityProfile())
