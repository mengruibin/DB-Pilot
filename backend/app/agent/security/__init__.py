"""
Agent 安全流水线（security-pipeline-north-star-plan）。

单一声明式安全流水线——11 个工具在 SECURITY_REGISTRY 声明 SecurityProfile（有序阶段序列），
一个 secure_tools_node 按三阶段编排，图简化为 agent → tools → agent。

子模块：
  - models.py:        核心抽象（StagePhase / StageRef / SecurityProfile / StageResult /
                      SecurityContext / SecurityStage(ABC)）
  - stages.py:        三个阶段的实现（SQLAuditStage / RowEstimationStage / ConfirmStage）
  - registry.py:      STAGE_REGISTRY + SECURITY_REGISTRY（安全策略单一事实源）
  - orchestrator.py:  三阶段编排 helper + 工具执行原语

安全约定（CLAUDE.md Key Conventions）：
  - 安全行为一律经 security/registry.py 的 SECURITY_REGISTRY 声明，
    禁止在工具 extras 或图节点里散落安全逻辑。
  - PRE_CONFIRM 阶段必须无 DB 副作用（interrupt 重放会跑两遍）。
  - 两条硬性不变量（LangGraph interrupt 语义）：
      1. 首遍 interrupt() 前的代码跑两遍（首遍 + resume 遍），其返回值整体丢弃；
      2. resume 遍必须截断 sse_events（只返回本遍新事件），否则历史 tool_call 被重发。
"""

from __future__ import annotations

# 包导出：安全流水线的公共 API（显式 re-export，供 `from app.agent.security import ...` 使用）
from app.agent.security.models import (  # noqa: I001
    SecurityContext as SecurityContext,
)
from app.agent.security.models import (
    SecurityProfile as SecurityProfile,
)
from app.agent.security.models import (
    SecurityStage as SecurityStage,
)
from app.agent.security.models import (
    StagePhase as StagePhase,
)
from app.agent.security.models import (
    StageRef as StageRef,
)
from app.agent.security.models import (
    StageResult as StageResult,
)
from app.agent.security.registry import (  # noqa: I001
    SECURITY_REGISTRY as SECURITY_REGISTRY,
)
from app.agent.security.registry import (
    STAGE_REGISTRY as STAGE_REGISTRY,
)
from app.agent.security.registry import (
    get_security_profile as get_security_profile,
)

# 新增模块说明（AGENTS.md §目录规范）：
# <!-- EXTENSION: 安全流水线为推倒重来的北极星架构核心，独立成包以承载
#      models/registry/stages/orchestrator 四层职责，避免全部塞进 safety.py -->
