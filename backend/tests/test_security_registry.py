"""
安全注册表单元测试（security-pipeline-north-star-plan / 任务 D2）。

覆盖：
  1. SECURITY_REGISTRY 显式收录全部 11 个 Agent 工具，profile 与预期阶段一致
  2. 未收录工具 → 空 profile（无安全阶段）
  3. 加载期不变式：双 CONFIRM 抛错、阶段乱序抛错
  4. STAGE_REGISTRY 注册预期阶段名
"""

from __future__ import annotations

import os

import pytest

# 在导入 app 模块前设置测试用环境变量
os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault("LLM_MODEL", "test-model")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///test.db")

from app.agent.security.models import SecurityProfile, StagePhase, StageRef
from app.agent.security.registry import (
    SECURITY_REGISTRY,
    STAGE_REGISTRY,
    get_security_profile,
)
from app.agent.tools.registry import TOOL_REGISTRY

# =============================================================================
# 注册表覆盖
# =============================================================================


class TestRegistryCoverage:
    def test_all_tools_explicitly_listed(self):
        """SECURITY_REGISTRY 显式收录全部 11 个工具（TOOL_REGISTRY 全覆盖）。"""
        for tool_name in TOOL_REGISTRY:
            assert tool_name in SECURITY_REGISTRY, f"工具 {tool_name} 未收录"

    def test_registry_matches_tool_registry_size(self):
        """SECURITY_REGISTRY 工具数与 TOOL_REGISTRY 一致（11 个）。"""
        assert set(SECURITY_REGISTRY) == set(TOOL_REGISTRY)


class TestProfiles:
    def test_execute_readonly_sql_profile(self):
        """execute_readonly_sql：sql_audit(PRE_CONFIRM) + row_estimation(PRE_EXECUTE)。"""
        profile = get_security_profile("execute_readonly_sql")
        assert [(s.name, s.phase) for s in profile.stages] == [
            ("sql_audit", StagePhase.PRE_CONFIRM),
            ("row_estimation", StagePhase.PRE_EXECUTE),
        ]
        # sql_audit 声明只读语句类型白名单
        audit_ref = profile.stages[0]
        assert audit_ref.params.get("allowed_stmt_types") == (
            "SELECT",
            "SHOW",
            "EXPLAIN",
            "UNION",
            "USE",
            "SET",
        )

    def test_execute_write_sql_profile_has_pre_confirm_audit(self):
        """execute_write_sql：★ 审计缺口已补——sql_audit 前置 + confirm。"""
        profile = get_security_profile("execute_write_sql")
        assert [(s.name, s.phase) for s in profile.stages] == [
            ("sql_audit", StagePhase.PRE_CONFIRM),
            ("confirm", StagePhase.CONFIRM),
        ]
        # sql_audit 声明写语句类型白名单（只放行 DML）
        audit_ref = profile.stages[0]
        assert audit_ref.params.get("allowed_stmt_types") == (
            "INSERT",
            "UPDATE",
            "DELETE",
        )
        # sql_audit 声明 require_where（无 WHERE 全表删改硬拦，write-where-guard-plan）
        assert audit_ref.params.get("require_where") is True
        # confirm 阶段携带 sql_write 分类
        confirm_ref = profile.confirm_ref()
        assert confirm_ref is not None
        assert confirm_ref.params.get("category") == "sql_write"

    def test_explain_query_profile(self):
        """explain_query：sql_audit(PRE_CONFIRM)。"""
        profile = get_security_profile("explain_query")
        assert [(s.name, s.phase) for s in profile.stages] == [
            ("sql_audit", StagePhase.PRE_CONFIRM),
        ]

    def test_kill_transaction_profile(self):
        """kill_transaction：confirm(CONFIRM, connection_kill)。"""
        profile = get_security_profile("kill_transaction")
        assert [(s.name, s.phase) for s in profile.stages] == [
            ("confirm", StagePhase.CONFIRM),
        ]
        assert profile.confirm_ref().params.get("category") == "connection_kill"

    def test_no_audit_tools_empty_profile(self):
        """非 SQL 工具（list_tables/describe_table/check_locks 等）→ 空 profile。"""
        for tool in (
            "list_tables",
            "describe_table",
            "get_slow_queries",
            "check_connections",
            "check_locks",
            "analyze_locks",
            "check_replication",
            "run_health_check",
        ):
            assert get_security_profile(tool).stages == ()

    def test_unknown_tool_empty_profile(self):
        """未收录工具 → 空 profile（无安全阶段，跳过安全检查）。"""
        assert get_security_profile("no_such_tool").stages == ()


# =============================================================================
# 加载期不变式
# =============================================================================


class TestProfileInvariants:
    def test_double_confirm_raises(self):
        """至多一个 CONFIRM 阶段——双 confirm 抛 ValueError。"""
        with pytest.raises(ValueError, match="CONFIRM 阶段"):
            SecurityProfile(
                stages=(
                    StageRef("confirm", StagePhase.CONFIRM),
                    StageRef("confirm", StagePhase.CONFIRM),
                )
            )

    def test_out_of_order_raises(self):
        """阶段必须按 PRE_CONFIRM → CONFIRM → PRE_EXECUTE 有序。"""
        with pytest.raises(ValueError, match="有序"):
            SecurityProfile(
                stages=(
                    StageRef("row_estimation", StagePhase.PRE_EXECUTE),
                    StageRef("sql_audit", StagePhase.PRE_CONFIRM),
                )
            )

    def test_confirm_after_pre_execute_raises(self):
        """CONFIRM 不晚于 PRE_EXECUTE——confirm 在 pre_execute 之后抛错。"""
        with pytest.raises(ValueError):
            SecurityProfile(
                stages=(
                    StageRef("row_estimation", StagePhase.PRE_EXECUTE),
                    StageRef("confirm", StagePhase.CONFIRM),
                )
            )

    def test_valid_profile_ok(self):
        """合法 profile（sql_audit → confirm → row_estimation）不抛错。"""
        profile = SecurityProfile(
            stages=(
                StageRef("sql_audit", StagePhase.PRE_CONFIRM),
                StageRef("confirm", StagePhase.CONFIRM),
                StageRef("row_estimation", StagePhase.PRE_EXECUTE),
            )
        )
        assert len(profile.stages) == 3


# =============================================================================
# 阶段注册表
# =============================================================================


class TestStageRegistry:
    def test_stage_registry_keys(self):
        """STAGE_REGISTRY 注册 sql_audit / row_estimation / confirm。"""
        assert set(STAGE_REGISTRY) == {"sql_audit", "row_estimation", "confirm"}

    def test_stage_phases_match(self):
        """阶段类的相位声明与注册表语义一致。"""
        from app.agent.security.stages import (
            ConfirmStage,
            RowEstimationStage,
            SQLAuditStage,
        )

        assert STAGE_REGISTRY["sql_audit"].phase is StagePhase.PRE_CONFIRM
        assert STAGE_REGISTRY["row_estimation"].phase is StagePhase.PRE_EXECUTE
        assert STAGE_REGISTRY["confirm"].phase is StagePhase.CONFIRM
        # 实例化后的相位与类声明一致
        assert SQLAuditStage().phase is StagePhase.PRE_CONFIRM
        assert RowEstimationStage().phase is StagePhase.PRE_EXECUTE
        assert ConfirmStage().phase is StagePhase.CONFIRM
