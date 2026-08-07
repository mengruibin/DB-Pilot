"""
Agent 图结构 + message_type 推断单元测试。

覆盖：
  1. 图结构：入口为 agent，不含 classify/general 节点
  2. _infer_message_type：从工具调用推断消息类型
  3. Intent 枚举向后兼容
"""

from __future__ import annotations

import os
from typing import Any

import pytest

# 在导入 app 模块前设置测试用环境变量（config.py 启动时校验必填字段）
# 若已在环境中配置则保留原值
os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault("LLM_MODEL", "test-model")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///test.db")

from app.agent.graph import build_agent_graph, _extract_token_usage  # noqa: E402, I001
# from app.agent.state import Intent  # noqa: E402

# =============================================================================
# 图结构测试
# =============================================================================


class TestGraphStructure:
    """验证重构后的图结构（2026-07：移除了意图分类）。"""

    def test_build_compiles(self):
        """build_agent_graph() 应能成功编译图。"""
        graph = build_agent_graph()
        assert graph is not None

    def test_entry_point_is_agent(self):
        """图中包含 agent_node，但不含 classify_node（入口为 agent）。"""
        graph = build_agent_graph()
        assert "agent" in graph.nodes
        assert "classify" not in graph.nodes

    def test_no_classify_or_general_nodes(self):
        """图中不应存在 classify_node 和 general_node。"""
        graph = build_agent_graph()
        assert "classify" not in graph.nodes
        assert "general" not in graph.nodes

    def test_required_nodes_exist(self):
        """图中应存在 agent/tools 节点，且不含 confirm（已并入安全流水线）。"""
        graph = build_agent_graph()
        assert "agent" in graph.nodes
        assert "tools" in graph.nodes

    def test_confirm_node_removed(self):
        """图中不应存在 confirm 节点（security-pipeline-north-star-plan 收敛）。"""
        graph = build_agent_graph()
        assert "confirm" not in graph.nodes

    def test_format_response_removed(self):
        """图中不应存在 format_response 节点（2026-07 已删除）。"""
        graph = build_agent_graph()
        assert "format_response" not in graph.nodes

    def test_only_expected_nodes(self):
        """图中仅应有 agent/tools（以及 LangGraph 内置 __start__）。"""
        graph = build_agent_graph()
        actual_nodes = set(graph.nodes.keys())
        # LangGraph 自动添加 __start__ 内部节点
        assert actual_nodes >= {"agent", "tools"}
        assert actual_nodes - {"__start__", "agent", "tools"} == set()


# =============================================================================
# _infer_message_type 测试
# =============================================================================


def _make_state(tools_called: list[str]) -> dict[str, Any]:
    """构造一个带有工具调用 trace 的 state 字典。"""
    trace_entries = [
        {
            "iteration": i + 1,
            "tool_calls": [{"id": f"call_{i}", "name": name, "arguments": {}}],
        }
        for i, name in enumerate(tools_called)
    ]
    return {"trace_iterations": trace_entries}


class TestInferMessageType:
    """_infer_message_type() 应正确从工具调用推断消息类型。"""

    # 从 chat.py 导入 _infer_message_type
    @pytest.fixture(autouse=True)
    def _import(self):
        from app.api.chat import _infer_message_type
        self._func = _infer_message_type

    def test_general_no_tools(self):
        """未调用任何工具 → general。"""
        state = _make_state([])
        assert self._func(state) == "general"

    def test_general_no_trace(self):
        """trace_iterations 为空列表 → general。"""
        state: dict[str, Any] = {"trace_iterations": []}
        assert self._func(state) == "general"

    def test_general_missing_trace(self):
        """trace_iterations 不存在 → general。"""
        state: dict[str, Any] = {}
        assert self._func(state) == "general"

    def test_query(self):
        """调用 execute_readonly_sql → query。"""
        state = _make_state(["execute_readonly_sql"])
        assert self._func(state) == "query"

    def test_query_list_tables(self):
        """调用 list_tables → query。"""
        state = _make_state(["list_tables"])
        assert self._func(state) == "query"

    def test_query_describe_table(self):
        """调用 describe_table → query。"""
        state = _make_state(["describe_table"])
        assert self._func(state) == "query"

    def test_diagnosis_explain(self):
        """调用 explain_query → diagnosis。"""
        state = _make_state(["explain_query"])
        assert self._func(state) == "diagnosis"

    def test_diagnosis_slow_queries(self):
        """调用 get_slow_queries → diagnosis。"""
        state = _make_state(["get_slow_queries"])
        assert self._func(state) == "diagnosis"

    def test_troubleshoot_locks(self):
        """调用 check_locks → troubleshoot。"""
        state = _make_state(["check_locks"])
        assert self._func(state) == "troubleshoot"

    def test_troubleshoot_connections(self):
        """调用 check_connections → troubleshoot。"""
        state = _make_state(["check_connections"])
        assert self._func(state) == "troubleshoot"

    def test_troubleshoot_replication(self):
        """调用 check_replication → troubleshoot。"""
        state = _make_state(["check_replication"])
        assert self._func(state) == "troubleshoot"

    def test_health_check(self):
        """调用 run_health_check → health_check。"""
        state = _make_state(["run_health_check"])
        assert self._func(state) == "health_check"

    def test_priority_health_check(self):
        """多工具调用时，health_check 优先级最高。"""
        state = _make_state(["execute_readonly_sql", "run_health_check"])
        assert self._func(state) == "health_check"

    def test_priority_troubleshoot(self):
        """多工具调用时，troubleshoot 优先级高于 diagnosis 和 query。"""
        state = _make_state(["execute_readonly_sql", "explain_query", "check_locks"])
        assert self._func(state) == "troubleshoot"

    def test_priority_diagnosis(self):
        """多工具调用时，diagnosis 优先级高于 query。"""
        state = _make_state(["execute_readonly_sql", "explain_query"])
        assert self._func(state) == "diagnosis"

    def test_unknown_tool_fallback(self):
        """只有未知工具时 → general。"""
        state = _make_state(["unknown_tool"])
        assert self._func(state) == "general"


# =============================================================================
# token 用量提取测试（2026-08：会话级 token 统计）
# =============================================================================


class TestExtractTokenUsage:
    """_extract_token_usage 多 provider 兼容性测试。"""

    def test_usage_metadata_new_langchain(self):
        """LangChain 新版 usage_metadata 标准化字段。"""
        from types import SimpleNamespace

        resp = SimpleNamespace(
            usage_metadata={"input_tokens": 10, "output_tokens": 5},
        )
        assert _extract_token_usage(resp) == {"input_tokens": 10, "output_tokens": 5}

    def test_token_usage_openai_style(self):
        """OpenAI 系 response_metadata.token_usage（prompt/completion_tokens 键名）。"""
        from types import SimpleNamespace

        resp = SimpleNamespace(
            usage_metadata=None,
            response_metadata={
                "token_usage": {"prompt_tokens": 100, "completion_tokens": 20}
            },
        )
        assert _extract_token_usage(resp) == {"input_tokens": 100, "output_tokens": 20}

    def test_usage_anthropic_style(self):
        """Anthropic 系 response_metadata.usage（input/output_tokens 键名）。"""
        from types import SimpleNamespace

        resp = SimpleNamespace(
            usage_metadata=None,
            response_metadata={"usage": {"input_tokens": 7, "output_tokens": 3}},
        )
        assert _extract_token_usage(resp) == {"input_tokens": 7, "output_tokens": 3}

    def test_missing_usage_returns_zeros(self):
        """无用量信息时返回全 0，不抛异常。"""
        from types import SimpleNamespace

        resp = SimpleNamespace(usage_metadata=None, response_metadata={})
        assert _extract_token_usage(resp) == {"input_tokens": 0, "output_tokens": 0}

    def test_usage_metadata_takes_priority(self):
        """usage_metadata 优先于 response_metadata。"""
        from types import SimpleNamespace

        resp = SimpleNamespace(
            usage_metadata={"input_tokens": 1, "output_tokens": 2},
            response_metadata={"token_usage": {"prompt_tokens": 9, "completion_tokens": 9}},
        )
        assert _extract_token_usage(resp) == {"input_tokens": 1, "output_tokens": 2}


# # =============================================================================
# # Intent 枚举向后兼容测试
# # =============================================================================


# class TestIntentBackwardCompat:
#     """Intent 枚举保留用于数据库历史记录兼容。"""

#     def test_enum_values(self):
#         """所有旧枚举值仍可访问。"""
#         assert Intent.QUERY == "QUERY"
#         assert Intent.DIAGNOSIS == "DIAGNOSIS"
#         assert Intent.TROUBLESHOOT == "TROUBLESHOOT"
#         assert Intent.HEALTH_CHECK == "HEALTH_CHECK"
#         assert Intent.GENERAL == "GENERAL"

#     def test_enum_is_str_enum(self):
#         """Intent 是 StrEnum，可序列化为字符串。"""
#         assert isinstance(Intent.QUERY.value, str)
#         assert Intent.QUERY.value.lower() == "query"
#         assert Intent.GENERAL.value.lower() == "general"
