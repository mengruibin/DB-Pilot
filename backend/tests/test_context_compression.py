"""
Agent 上下文压缩单元测试（context-compression-plan）。

覆盖：
  1. _partition_turns — 轮次切分（远古历史 / 近K轮窗口 / 当前轮）
  2. _build_history_digest — 抽取式摘要生成（问题 | 工具 | 结论）
  3. _estimate_input_tokens — 懒触发 token 估算
  4. _effective_window_k — 闲置衰减窗口决策
  5. _ensure_digest — 摘要缓存与增量失效
  6. _build_llm_messages / _build_system_prompt — 摘要走 user 通道，system 恒静态
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph.message import add_messages

from app.agent.graph import (
    _build_history_digest,
    _build_llm_messages,
    _build_system_prompt,
    _count_input_chars,
    _count_msg_chars,
    _effective_window_k,
    _ensure_digest,
    _estimate_input_tokens,
    _partition_turns,
)
from app.config import settings

# =============================================================================
# 测试工具：构造消息 + 模拟 LangGraph add_messages reducer
# =============================================================================


def _mk_human(text: str) -> HumanMessage:
    return HumanMessage(content=text)


def _mk_tool_call(names: list[str]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"id": f"c{i}", "name": n, "args": {}} for i, n in enumerate(names)],
    )


def _mk_tool_calls(calls: list[tuple[str, dict]]) -> AIMessage:
    """构造带参数的多次工具调用（用于摘要参数渲染测试）。

    Args:
        calls: (工具名, 参数 dict) 列表，保持调用顺序。
    """
    return AIMessage(
        content="",
        tool_calls=[
            {"id": f"c{i}", "name": name, "args": args} for i, (name, args) in enumerate(calls)
        ],
    )


def _mk_answer(text: str) -> AIMessage:
    return AIMessage(content=text)


def _mk_tool(name: str, content: str = "结果") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id="c0", name=name)


def _thru_reducer(msgs: list) -> list:
    """模拟 LangGraph add_messages reducer：为无 id 消息分配稳定 id。

    真实图流程中消息经 add_messages 合并，都会获得稳定 id；
    直接构造的消息 id 为 None，因此测试必须先过 reducer 才能测缓存失效。
    """
    return add_messages([], msgs)


def _make_turn1() -> list:
    return _thru_reducer(
        [
            _mk_human("问题1"),
            _mk_tool_call(["list_tables"]),
            _mk_tool("list_tables"),
            _mk_answer("答1"),
        ]
    )


def _make_turn2() -> list:
    return _thru_reducer(
        [
            _mk_human("问题2"),
            _mk_tool_call(["get_slow_queries"]),
            _mk_tool("get_slow_queries"),
            _mk_answer("答2"),
        ]
    )


def _make_turn3() -> list:
    return _thru_reducer([_mk_human("问题3")])


# =============================================================================
# TestPartitionTurns — 轮次切分
# =============================================================================


class TestPartitionTurns:
    """_partition_turns 轮次切分测试。"""

    def test_first_turn_only_human(self) -> None:
        """首轮（仅 HumanMessage）→ 全部归当前轮。"""
        m = [_mk_human("问题1")]
        assert _partition_turns(m, 2) == ([], [], m)

    def test_three_turns_k1(self) -> None:
        """三轮会话 K=1：历史=turn1，窗口=turn2，当前轮=turn3。"""
        turn1, turn2, turn3 = _make_turn1(), _make_turn2(), _make_turn3()
        msgs = _thru_reducer(turn1 + turn2 + turn3)
        hist, win, cur = _partition_turns(msgs, 1)
        assert hist == turn1, "K=1 历史应为 turn1"
        assert win == turn2, "K=1 窗口应为 turn2"
        assert cur == turn3, "当前轮应为 turn3"

    def test_k0_all_old_into_history(self) -> None:
        """K=0：不保留任何已完成轮次，全部进历史。"""
        turn1, turn2, turn3 = _make_turn1(), _make_turn2(), _make_turn3()
        msgs = _thru_reducer(turn1 + turn2 + turn3)
        hist, win, cur = _partition_turns(msgs, 0)
        assert hist == turn1 + turn2 and win == [] and cur == turn3

    def test_k2_keeps_two_recent(self) -> None:
        """K=2：窗口覆盖最近两个已完成轮次。"""
        turn1, turn2, turn3 = _make_turn1(), _make_turn2(), _make_turn3()
        msgs = _thru_reducer(turn1 + turn2 + turn3)
        hist, win, cur = _partition_turns(msgs, 2)
        assert hist == [] and win == turn1 + turn2 and cur == turn3

    def test_no_human_message(self) -> None:
        """无 HumanMessage（异常态）→ 全部归当前轮。"""
        m = [_mk_answer("只回答")]
        assert _partition_turns(m, 2) == ([], [], m)

    def test_no_overlap_no_gap(self) -> None:
        """多 K 值下三段拼接应等于全集（无重叠、无遗漏）。"""
        turn1, turn2, turn3 = _make_turn1(), _make_turn2(), _make_turn3()
        msgs = _thru_reducer(turn1 + turn2 + turn3)
        for k in (0, 1, 2):
            hist, win, cur = _partition_turns(msgs, k)
            assert hist + win + cur == msgs, f"K={k} 三段拼接应等于全集"


# =============================================================================
# TestBuildHistoryDigest — 抽取式摘要生成
# =============================================================================


class TestBuildHistoryDigest:
    """_build_history_digest 摘要生成测试。"""

    def test_format(self) -> None:
        """单轮摘要格式：`1. 用户：… | 工具：… | 结论：…`。"""
        turn = _thru_reducer(
            [
                _mk_human("问题1"),
                _mk_tool_call(["list_tables"]),
                _mk_tool("list_tables"),
                _mk_answer("答1"),
            ]
        )
        digest = _build_history_digest(turn)
        assert digest == "1. 用户：问题1 | 工具：list_tables | 结论：答1"

    def test_tool_calls_in_order_not_dedup(self) -> None:
        """同一轮内工具调用按顺序列出每次，不折叠重复（带标签事件日志范式）。"""
        turn = _thru_reducer(
            [
                _mk_human("问题1"),
                _mk_tool_call(["a", "b", "a"]),
                _mk_tool("a"),
                _mk_tool("b"),
                _mk_answer("答1"),
            ]
        )
        digest = _build_history_digest(turn)
        assert "工具：a, b, a" in digest, "顺序保留、重复不折叠"

    def test_unfinished_turn_shows_no_conclusion(self) -> None:
        """未完成轮（以工具调用/结果收尾，无最终回答）→ 结论：（未完成，被中断）。"""
        turn = _thru_reducer(
            [_mk_human("问题3"), _mk_tool_call(["explain_query"])]
        )
        digest = _build_history_digest(turn)
        assert "结论：（未完成，被中断）" in digest

    def test_tool_result_ending_turn_marks_interrupted(self) -> None:
        """轮以 ToolMessage 收尾（回答未持久化）→ 结论：（未完成，被中断）。"""
        turn = _thru_reducer(
            [
                _mk_human("问题3"),
                _mk_tool_call(["execute_readonly_sql"]),
                _mk_tool("execute_readonly_sql"),
            ]
        )
        digest = _build_history_digest(turn)
        assert "结论：（未完成，被中断）" in digest

    def test_plain_question_no_answer_marks_none(self) -> None:
        """仅用户问题无任何响应 → 结论：（无）。"""
        turn = _thru_reducer([_mk_human("问题3")])
        digest = _build_history_digest(turn)
        assert "结论：（无）" in digest

    def test_empty_history(self) -> None:
        """空历史返回空串。"""
        assert _build_history_digest([]) == ""

    def test_max_turns_keeps_recent_only(self) -> None:
        """max_turns 只保留最近 N 轮，最旧轮次丢弃。"""
        many = _thru_reducer(
            [m for i in range(5) for m in [_mk_human(f"问题{i}"), _mk_answer(f"答{i}")]]
        )
        digest = _build_history_digest(many, max_turns=2)
        assert digest.count("用户：") == 2
        assert "问题3" in digest and "问题4" in digest
        assert "问题0" not in digest and "问题1" not in digest

    def test_truncation_limits(self) -> None:
        """问题 / 结论 / 工具参数字符上限生效（带省略号）。"""
        digest = _build_history_digest(
            _thru_reducer([_mk_human("超长问题" * 100), _mk_answer("超长结论" * 100)]),
            max_q_chars=20,
            max_conclusion_chars=20,
        )
        assert "…" in digest
        # 工具参数截断：超长 sql 按 max_arg_chars 截断
        digest2 = _build_history_digest(
            _thru_reducer(
                [
                    _mk_human("问题"),
                    _mk_tool_calls([("execute_readonly_sql", {"sql": "S" * 100})]),
                    _mk_tool("execute_readonly_sql"),
                    _mk_answer("答"),
                ]
            ),
            max_arg_chars=20,
        )
        assert 'sql="' + "S" * 20 + "…" in digest2

    def test_conclusion_takes_last_final_answer(self) -> None:
        """结论取最后一个无 tool_calls 的 AIMessage。"""
        turn = _thru_reducer(
            [
                _mk_human("问题1"),
                _mk_tool_call(["x"]),
                _mk_tool("x"),
                _mk_answer("中间"),
                _mk_answer("最终"),
            ]
        )
        digest = _build_history_digest(turn)
        assert "结论：最终" in digest

    def test_tool_call_args_rendered(self) -> None:
        """工具调用带参数渲染为 `name(k="v")`，sql 原样展示（≤上限不截）。"""
        turn = _thru_reducer(
            [
                _mk_human("问题1"),
                _mk_tool_calls(
                    [("execute_readonly_sql", {"sql": "SELECT * FROM orders WHERE id = 1"})]
                ),
                _mk_tool("execute_readonly_sql"),
                _mk_answer("答1"),
            ]
        )
        digest = _build_history_digest(turn)
        assert 'execute_readonly_sql(sql="SELECT * FROM orders WHERE id = 1")' in digest

    def test_zero_arg_tool_renders_bare_name(self) -> None:
        """零参数工具渲染为裸名（无括号）。"""
        turn = _thru_reducer(
            [
                _mk_human("问题1"),
                _mk_tool_call(["check_connections"]),
                _mk_tool("check_connections"),
                _mk_answer("答1"),
            ]
        )
        digest = _build_history_digest(turn)
        assert "工具：check_connections" in digest

    def test_list_arg_rendered(self) -> None:
        """list 参数渲染为 `k=[...]`（如 describe_table 的 table_names）。"""
        turn = _thru_reducer(
            [
                _mk_human("问题1"),
                _mk_tool_calls(
                    [("describe_table", {"table_names": ["orders", "order_items"]})]
                ),
                _mk_tool("describe_table"),
                _mk_answer("答1"),
            ]
        )
        digest = _build_history_digest(turn)
        assert "describe_table(table_names=['orders', 'order_items'])" in digest


# =============================================================================
# TestCountInputChars — 请求级字符计数（token-estimation-plan T1）
# =============================================================================


class TestCountInputChars:
    """_count_msg_chars / _count_input_chars 修正漏计测试。"""

    def test_str_content(self) -> None:
        m = _mk_human("你好")
        assert _count_msg_chars(m) == len("你好")

    def test_tool_calls_args_json(self) -> None:
        """tool_calls 参数按 JSON 序列化长度计入（旧启发式漏计的字段）。"""
        import json

        m = AIMessage(
            content="",
            tool_calls=[
                {"id": "c0", "name": "execute_readonly_sql", "args": {"sql": "SELECT 1"}}
            ],
        )
        assert _count_msg_chars(m) == len(json.dumps({"sql": "SELECT 1"}, ensure_ascii=False))

    def test_additional_kwargs_reasoning(self) -> None:
        """additional_kwargs（DeepSeek reasoning_content）计入。"""
        m = AIMessage(content="答案", additional_kwargs={"reasoning_content": "推理" * 100})
        assert _count_msg_chars(m) == len("答案") + len("推理" * 100)

    def test_multimodal_blocks(self) -> None:
        """多模态 content 只取各块 text 字段。"""
        m = AIMessage(content=[{"type": "text", "text": "块A"}, {"text": "块B"}])
        assert _count_msg_chars(m) == len("块A") + len("块B")

    def test_empty_content(self) -> None:
        """空 content 计 0。"""
        m = AIMessage(content="", tool_calls=[])
        assert _count_msg_chars(m) == 0

    def test_count_input_chars_includes_system(self) -> None:
        msgs = [_mk_human("问题"), _mk_tool("x", "结果")]
        total = _count_input_chars("SYS", msgs)
        assert total == len("SYS") + _count_msg_chars(msgs[0]) + _count_msg_chars(msgs[1])


# =============================================================================
# TestEstimateInputTokens — 真实锚定 + 增量估算（token-estimation-plan T2）
# =============================================================================


class TestEstimateInputTokens:
    """_estimate_input_tokens 锚定 / 冷启动双路径测试。"""

    def test_empty_returns_at_least_one(self) -> None:
        assert _estimate_input_tokens("", []) >= 1

    def test_cold_start_monotonic_with_content(self) -> None:
        """冷启动（无锚点）：随内容单调增长。"""
        small = _estimate_input_tokens("sys", [_mk_human("hi")])
        big = _estimate_input_tokens("sys" + "x" * 10_000, [_mk_human("hi" * 10_000)])
        assert big > small

    def test_anchored_no_growth_uses_anchor(self) -> None:
        """无增量（counted 与 last_counted 相同）→ est = last_actual + last_completion。"""
        msgs = [_mk_human("q" * 100)]
        counted = _count_input_chars("SYS", msgs)
        est = _estimate_input_tokens(
            "SYS", msgs, last_actual=10_000, last_counted=counted, last_completion=0
        )
        assert est == 10_000

    def test_anchored_adds_completion(self) -> None:
        """上次真实 output（上次回答回发历史）计入 est。"""
        msgs = [_mk_human("q" * 100)]
        counted = _count_input_chars("SYS", msgs)
        est0 = _estimate_input_tokens(
            "SYS", msgs, last_actual=10_000, last_counted=counted, last_completion=0
        )
        est1 = _estimate_input_tokens(
            "SYS", msgs, last_actual=10_000, last_counted=counted, last_completion=500
        )
        assert est1 - est0 == 500

    def test_anchored_delta_is_new_content_only(self) -> None:
        """completion_chars 从增量剔除：增量 = 工具结果 + 用户输入（不含上次回答）。"""
        prev = _mk_human("q" * 100)
        answer = AIMessage(content="answer" * 50)  # 上次回答（新增，已用真实 completion 记账）
        new_user = _mk_human("新问题")
        last_counted = _count_input_chars("SYS", [prev])
        msgs = [prev, answer, new_user]
        est = _estimate_input_tokens(
            "SYS", msgs, last_actual=10_000, last_counted=last_counted, last_completion=100
        )
        completion_chars = _count_msg_chars(answer)
        delta = max(0, _count_input_chars("SYS", msgs) - last_counted - completion_chars)
        rate = max(
            1 / settings.AGENT_ESTIMATE_CHARS_PER_TOKEN,
            (10_000 - 3_000) / last_counted,
        )
        assert est == 10_000 + 100 + int(delta * rate)
        assert delta == _count_msg_chars(new_user), "增量应只含用户输入，上次回答已被剔除"

    def test_anchor_invalid_falls_back_to_cold_start(self) -> None:
        """锚点无效（last_actual=0 / last_counted 缺失）→ 回退冷启动启发式。"""
        msgs = [_mk_human("hi")]
        cold = _estimate_input_tokens("SYS", msgs)
        assert _estimate_input_tokens(
            "SYS", msgs, last_actual=0, last_counted=100, last_completion=0
        ) == cold
        assert _estimate_input_tokens(
            "SYS", msgs, last_actual=10_000, last_counted=None, last_completion=0
        ) == cold


# =============================================================================
# TestEffectiveWindowK — 闲置衰减窗口决策
# =============================================================================


class TestEffectiveWindowK:
    """_effective_window_k 闲置衰减决策测试。"""

    def test_new_turn_fresh_keeps_config_k(self) -> None:
        """新轮且未过闲置 → K=配置值。"""
        now = 1_000_000.0
        state = {"last_turn_at": now - 30}  # 30 秒前
        k, new_ts, update = _effective_window_k(state, True, now=now)
        assert k == settings.AGENT_KEEP_RECENT_TURNS
        assert new_ts == now and update is True

    def test_new_turn_idle_sets_k_zero(self) -> None:
        """新轮且闲置超过阈值 → K=0（闲置衰减）。"""
        now = 1_000_000.0
        # 用"超过配置阈值 1 分钟"而非固定 7200s：对 AGENT_IDLE_DECAY_MINUTES
        # 的取值鲁棒（.env 可设为 120，7200s 恰好等于阈值导致 7200 > 7200 不成立）
        idle_seconds = settings.AGENT_IDLE_DECAY_MINUTES * 60 + 60
        state = {"last_turn_at": now - idle_seconds}
        k, _, update = _effective_window_k(state, True, now=now)
        assert k == 0 and update is True

    def test_not_new_turn_reuses_persisted_k(self) -> None:
        """非新轮（ReAct 迭代中）→ 沿用已持久化的 context_window_k，不更新元数据。"""
        state = {"context_window_k": 0}
        k, ts, update = _effective_window_k(state, False, now=1_000_000.0)
        assert k == 0 and ts is None and update is False


# =============================================================================
# TestEnsureDigest — 摘要缓存与增量失效
# =============================================================================


class TestEnsureDigest:
    """_ensure_digest 缓存失效逻辑测试。"""

    def test_compute_then_reuse_cache(self) -> None:
        """首次生成摘要；id 未变时复用缓存、不返回更新。"""
        turn1, turn2, turn3 = _make_turn1(), _make_turn2(), _make_turn3()
        msgs = _thru_reducer(turn1 + turn2 + turn3)
        hist, _, _ = _partition_turns(msgs, 1)  # K=1 → hist=turn1（非空）
        d1, upd1 = _ensure_digest({"messages": msgs}, hist)
        assert d1 is not None and "问题1" in d1
        assert upd1["digest_upto_msg_id"] == hist[-1].id
        state2 = {"history_digest": d1, "digest_upto_msg_id": hist[-1].id}
        d2, upd2 = _ensure_digest(state2, hist)
        assert d2 == d1 and upd2 == {}

    def test_invalidate_on_history_growth(self) -> None:
        """新轮到来历史增长 → id 变化 → 重算摘要。"""
        turn1, turn2, turn3 = _make_turn1(), _make_turn2(), _make_turn3()
        msgs = _thru_reducer(turn1 + turn2 + turn3)
        hist, _, _ = _partition_turns(msgs, 1)  # hist=turn1
        d1, upd1 = _ensure_digest({"messages": msgs}, hist)
        state2 = {"history_digest": d1, "digest_upto_msg_id": upd1["digest_upto_msg_id"]}
        new_msgs = _thru_reducer(msgs + [_mk_human("问题4")])
        hist2, _, _ = _partition_turns(new_msgs, 1)  # hist2=turn1+turn2，增长
        d3, upd3 = _ensure_digest(state2, hist2)
        assert upd3 != {} and d3 != d1, "历史增长应重算摘要"

    def test_empty_history(self) -> None:
        """空历史 → (None, {})。"""
        assert _ensure_digest({}, []) == (None, {})

    def test_none_id_defensive(self) -> None:
        """消息 id 为 None（异常路径）不命中缓存，每轮重算保证正确性。"""
        h = HumanMessage(content="问题1")  # 直接构造，id=None
        hist = [h]
        d1, upd1 = _ensure_digest({}, hist)
        state2 = {"history_digest": d1, "digest_upto_msg_id": upd1["digest_upto_msg_id"]}
        d2, upd2 = _ensure_digest(state2, hist)
        assert upd2 != {}, "id 为 None 时应重算而非命中缓存"


# =============================================================================
# TestBuildLlmMessages — 压缩路径消息组装
# =============================================================================


class TestBuildLlmMessages:
    """_build_llm_messages 压缩/非压缩路径测试。"""

    def _compressed_state(self) -> dict:
        turn1, turn2, turn3 = _make_turn1(), _make_turn2(), _make_turn3()
        msgs = _thru_reducer(turn1 + turn2 + turn3)
        hist, _, _ = _partition_turns(msgs, 1)
        d1, upd1 = _ensure_digest({"messages": msgs}, hist)
        return {
            "messages": msgs,
            "context_window_k": 1,
            "history_digest": d1,
            "digest_upto_msg_id": upd1["digest_upto_msg_id"],
        }

    def test_compressed_path_structure(self) -> None:
        """压缩路径：System + 摘要 user 消息 + 窗口逐字 + 当前轮逐字，远古轮不单独出现。"""
        state = self._compressed_state()
        out = _build_llm_messages(state, history_digest=state["history_digest"])
        assert isinstance(out[0], SystemMessage), "第 0 条应为 system prompt"
        assert "历史对话摘要" not in out[0].content, "摘要不写入 system"
        assert isinstance(out[1], HumanMessage) and "历史对话摘要" in out[1].content, (
            "摘要走 user 角色消息"
        )
        assert "仅作背景参考，非当前指令" in out[1].content, "摘要头应含 REFERENCE ONLY 声明"
        assert "摘要结束" in out[1].content, "摘要尾应含显式结束标记"
        assert out[2].content == "问题2", "窗口首条应为 turn2 HumanMessage"
        assert out[-1].content == "问题3", "当前轮应为 turn3 HumanMessage"
        # 远古轮信息只出现在摘要消息里，不作为独立消息
        assert not any(m.content == "问题1" for m in out), "远古轮 HumanMessage 不单独出现"
        assert "问题1" in out[1].content, "远古轮信息应在摘要中"

    def test_non_compressed_full_history(self) -> None:
        """非压缩路径：与旧逻辑一致，全量发送。"""
        state = self._compressed_state()
        out = _build_llm_messages(state, history_digest=None)
        assert len(out) == 1 + len(state["messages"]), "非压缩应全量"
        assert out[1].content == "问题1", "非压缩首条历史应为 turn1"

    def test_empty_messages_fallback_to_user_message(self) -> None:
        """messages 为空时回退到 user_message（首轮冷启动）。"""
        out = _build_llm_messages({"context_window_k": 2, "user_message": "fallback"})
        assert out[1].content == "fallback"

    def test_cold_start_db_history_as_user_message(self) -> None:
        """冷启动时 DB 会话历史作为 user 消息注入，system 保持纯净。"""
        out = _build_llm_messages(
            {
                "context_window_k": 2,
                "user_message": "当前问题",
                "conversation_history": "用户：之前问题\n助手：之前回答",
            }
        )
        assert isinstance(out[0], SystemMessage), "system 仍是独立 system 消息"
        assert isinstance(out[1], HumanMessage) and "之前问题" in out[1].content, (
            "DB 兜底历史走 user 通道"
        )
        assert out[2].content == "当前问题", "当前用户消息在兜底历史之后"


# =============================================================================
# TestBuildSystemPrompt — 压缩中性提示
# =============================================================================


class TestBuildSystemPrompt:
    """_build_system_prompt 只含不变内容，不承载会话历史。"""

    def test_system_prompt_static_across_states(self) -> None:
        """压缩与否、历史多少，system prompt 逐字节一致（历史走 user 通道）。"""
        sp_multi = _build_system_prompt(
            {"messages": [_mk_human("问题1"), _mk_human("问题2")]}
        )
        sp_cold = _build_system_prompt({"messages": [_mk_human("问题1")]})
        assert sp_multi == sp_cold, "system prompt 不应因历史状态变化"

    def test_system_prompt_contains_only_invariant_content(self) -> None:
        """system 只承载角色/规则/约束，不含对话历史内容。"""
        sp = _build_system_prompt({"messages": [_mk_human("问题1")]})
        assert "DB-Pilot" in sp and "## 工作原则" in sp
        assert "问题1" not in sp, "历史内容不写入 system"
        assert "历史对话摘要" not in sp, "摘要不写入 system"
        assert "对话历史" not in sp, "system 不再有对话历史段"
