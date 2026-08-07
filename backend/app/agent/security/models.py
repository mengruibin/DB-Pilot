"""
安全流水线核心抽象（security-pipeline-north-star-plan §2.3）。

定义安全阶段的公共数据类型与抽象基类：
  - StagePhase: 阶段相位（决定 interrupt 前后时序）
  - StageRef:   注册表内的阶段引用（名称 + 相位 + 参数）
  - SecurityProfile: 工具的完整安全策略（有序阶段序列，加载期校验不变式）
  - StageResult: 阶段检查结果（blocked / block_code / reason / context / warnings）
  - SecurityContext: 一次工具编排的共享上下文（跨阶段证据 + 被拦截集合）
  - SecurityStage(ABC): 阶段抽象基类
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class StagePhase(StrEnum):
    """安全阶段相位——决定阶段在 interrupt 前后的执行时序。

    - PRE_CONFIRM: 纯审计/无 DB 副作用，位于 interrupt() 之前（重跑两遍）。
                   sqlglot 语法审计属此类；EXPLAIN 有 DB 副作用，不允许放这里。
    - CONFIRM:     批量 interrupt 确认（orchestrator 特殊编排），标记型阶段。
    - PRE_EXECUTE: 确认之后才执行，允许 DB 副作用（EXPLAIN），只跑一遍。
    """

    PRE_CONFIRM = "PRE_CONFIRM"
    CONFIRM = "CONFIRM"
    PRE_EXECUTE = "PRE_EXECUTE"


# 相位顺序表（用于 SecurityProfile 加载期校验：阶段必须按此顺序排列）
_PHASE_ORDER: dict[StagePhase, int] = {
    StagePhase.PRE_CONFIRM: 0,
    StagePhase.CONFIRM: 1,
    StagePhase.PRE_EXECUTE: 2,
}


@dataclass(frozen=True)
class StageRef:
    """注册表内的安全阶段引用。

    Attributes:
        name: STAGE_REGISTRY 键（阶段名）。
        phase: 阶段相位。
        params: 阶段参数（例：ConfirmStage 的 category）。
    """

    name: str
    phase: StagePhase
    params: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SecurityProfile:
    """工具的完整安全策略——有序阶段序列（单一事实源，SECURITY_REGISTRY）。

    加载期校验不变式（违反抛 ValueError，在 SECURITY_REGISTRY 构建时即生效）：
      1. 至多一个 CONFIRM 阶段（interrupt 是批量决策，不允许两个确认点）
      2. 阶段按相位有序：PRE_CONFIRM → CONFIRM → PRE_EXECUTE
         （该约束同时保证 CONFIRM 不晚于 PRE_EXECUTE）

    Attributes:
        stages: 按执行顺序排列的阶段引用元组。
    """

    stages: tuple[StageRef, ...] = ()

    def __post_init__(self) -> None:
        """加载期校验：确认阶段数与相位顺序。"""
        confirm_count = sum(1 for s in self.stages if s.phase is StagePhase.CONFIRM)
        if confirm_count > 1:
            raise ValueError(
                f"SecurityProfile 至多允许一个 CONFIRM 阶段，实际 {confirm_count} 个"
            )
        prev_order = -1
        for s in self.stages:
            order = _PHASE_ORDER[s.phase]
            if order < prev_order:
                raise ValueError(
                    f"SecurityProfile 阶段必须按 PRE_CONFIRM → CONFIRM → PRE_EXECUTE 有序，"
                    f"发现 {s.phase} 出现在 {prev_order} 之后"
                )
            prev_order = order

    def confirm_ref(self) -> StageRef | None:
        """返回 CONFIRM 阶段引用（至多一个），无确认阶段时返回 None。

        供 orchestrator 判断某工具是否属于"需用户确认"的写操作。
        """
        for s in self.stages:
            if s.phase is StagePhase.CONFIRM:
                return s
        return None


@dataclass
class StageResult:
    """安全阶段检查结果。

    Attributes:
        blocked: 是否被拦截（True 时工具不进入后续阶段/执行）。
        block_code: 结构化拦截代码（"SQL_AUDIT_BLOCKED" / "ROW_ESTIMATION_BLOCKED"）。
            None 表示非拦截。用于 consecutive_blocks 识别，替代旧字符串特征耦合。
        reason: 面向 LLM/用户的拦截原因说明。
        context: 证据上下文（audit_result / explain_metrics 等，供日志与测试断言）。
        warnings: 非阻断警告（fail-open 放行时携带）。
    """

    blocked: bool
    block_code: str | None = None
    reason: str | None = None
    context: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class SecurityContext:
    """一次工具编排的共享上下文，跨三个阶段传递。

    Attributes:
        run_id: 本次 Agent 运行 ID。
        session_id: 当前会话 ID。
        conn_config: 目标数据库连接配置（含 db_type / user_role）。
        user_role: 当前用户角色（readonly / admin）。
        consecutive_blocks: 本节点入口的连续 RE 拦截计数（防改写死循环）。
        evidence: tool_call_id → 跨阶段共享证据（预留，供阶段间传参）。
        blocked_calls: 被审计拦截/确认拒绝的 tool_call_id 集合（防御去重）。
    """

    run_id: str
    session_id: str | None
    conn_config: dict[str, Any]
    user_role: str
    consecutive_blocks: int
    evidence: dict = field(default_factory=dict)
    blocked_calls: set = field(default_factory=set)


class SecurityStage(ABC):
    """安全阶段抽象基类。

    每个子类实现 check() 方法，对单个工具调用做一维安全检查。
    profile 中的阶段按顺序串行执行，任一 blocked=True 即拦截该工具。

    Attributes:
        name: 阶段名（= STAGE_REGISTRY 键）。
        phase: 阶段相位（决定 interrupt 前后时序）。
    """

    name: str
    phase: StagePhase

    @abstractmethod
    async def check(
        self,
        tool_call: Mapping[str, Any],
        conn_config: dict[str, Any],
        ctx: SecurityContext,
    ) -> StageResult:
        """检查单个工具调用是否安全。

        Args:
            tool_call: LangChain ToolCall 字典（含 id / name / args）。
            conn_config: 连接配置字典。
            ctx: 安全上下文（含 user_role / blocked_calls / evidence）。

        Returns:
            StageResult — blocked=True 表示该工具被本阶段拦截。
        """
        ...
