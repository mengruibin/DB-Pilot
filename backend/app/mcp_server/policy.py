"""MCP 无头写策略（EXTENSION: 以策略决策替代 Web 的 interrupt 确认）。

默认只读；DBPILOT_ALLOW_WRITE=1 才放行危险工具（写 SQL / 终止连接）。
红线 DDL 等审计拦截不受本策略影响——仍由 SQLAuditStage 硬拦（registry 单一事实源）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.agent.security.registry import SECURITY_REGISTRY

# 需确认的危险工具：registry 中含 CONFIRM 相位的工具（动态推导，不硬编码）
WRITE_GATED_TOOLS: frozenset[str] = frozenset(
    name
    for name, profile in SECURITY_REGISTRY.items()
    if profile.confirm_ref() is not None
)

# 拒绝消息中的操作描述（与 Web 确认卡 category 对齐）
_GATE_DESC: dict[str, str] = {
    "execute_write_sql": "执行写 SQL（INSERT/UPDATE/DELETE）",
    "execute_write_transaction": "执行事务型写 SQL",
    "kill_transaction": "终止数据库连接/事务",
}

_ALLOW_TRUE = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Policy:
    """写策略快照。"""

    allow_write: bool

    @property
    def effective_user_role(self) -> str:
        """工具执行注入的角色：允许写时按 admin（对齐 Web 确认后提权），否则 readonly。"""
        return "admin" if self.allow_write else "readonly"

    def gate_message(self, tool_name: str) -> str:
        """危险工具被拒时的说明文本（供 MCP 工具返回）。"""
        desc = _GATE_DESC.get(tool_name, tool_name)
        return (
            f"操作被安全策略拦截: {desc} 需要写权限。\n"
            "当前为只读模式。如需放行，请在启动 MCP 服务时设置环境变量 "
            "DBPILOT_ALLOW_WRITE=1，并确认目标数据库用户具备相应权限。"
        )


def load_policy(env: Mapping[str, str] | None = None) -> Policy:
    """从环境变量加载策略（纯函数）。

    Args:
        env: 环境变量映射；None 时读 os.environ。

    Returns:
        Policy — allow_write 来自 DBPILOT_ALLOW_WRITE。
    """
    import os

    env = env if env is not None else os.environ
    raw = (env.get("DBPILOT_ALLOW_WRITE") or "").strip().lower()
    return Policy(allow_write=raw in _ALLOW_TRUE)
