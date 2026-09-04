"""
安全流水线编排器单元测试（security-pipeline-north-star-plan / 任务 D2）。

覆盖：
  1. 单遍只读执行一次 + ToolMessage 配对
  2. readonly 发 INSERT 到 write 工具 → 确认前审计拦截（不弹确认卡片）
  3. DDL 发到 write 工具 → 确认前拦截（不弹确认卡片）
  4. admin 合法写 → interrupt 首遍抛 GraphInterrupt，resume 后执行恰一次
  5. 拒绝确认 → 取消消息 + 不执行
  6. 混合「写工具 + 只读工具(row_estimation)」→ 写确认、只读 EXPLAIN 恰一次
  7. resume 遍截断 sse_events（不含历史 tool_call 事件）
  8. consecutive_blocks 递增 / 重置 / >=4 强制终止
  9. 工具未注册 → "未注册" 消息
  10. interrupt payload 结构逐字段保留
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

# 在导入 app 模块前设置测试用环境变量
os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault("LLM_MODEL", "test-model")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///test.db")

from langgraph.errors import GraphInterrupt

from app.agent.security.models import SecurityContext
from app.agent.security.orchestrator import run_security_pipeline
from app.agent.security.registry import get_security_profile


@pytest.fixture(autouse=True)
def _no_real_db_adapter():
    """单元测试不连真实 DB：临时适配器创建直接抛错（EXPLAIN 类阶段按 fail-open 降级）。

    写工具 profile 现含 impact_estimate（PRE_CONFIRM 会尝试建临时适配器做 EXPLAIN），
    统一在此短路，保证断言确定且零网络依赖（只读 RowEstimationStage 同样受益）。
    """
    with patch(
        "app.agent.security.stages._create_temp_adapter",
        AsyncMock(side_effect=RuntimeError("unit test: no real DB")),
    ):
        yield


# =============================================================================
# 辅助函数
# =============================================================================


def _tc(name: str, args: dict, cid: str) -> dict:
    """构造 LangChain ToolCall 字典。"""
    return {"id": cid, "name": name, "args": args}


_CONN = {
    "connection_id": "c1",
    "db_type": "mysql",
    "host": "h",
    "port": 3306,
    "database": "d",
    "user": "u",
    "password": "p",
    "user_role": "admin",
}


def _ctx(consecutive_blocks: int = 0, user_role: str = "admin") -> SecurityContext:
    """构造安全上下文（默认 admin，写操作可过审计）。"""
    conn = {**_CONN, "user_role": user_role}
    return SecurityContext(
        run_id="r",
        session_id="s1",
        conn_config=conn,
        user_role=user_role,
        consecutive_blocks=consecutive_blocks,
    )


def _profiles(tool_calls: list[dict]) -> dict:
    """从工具调用列表构建 profile 映射。"""
    return {t["name"]: get_security_profile(t["name"]) for t in tool_calls}


def _make_tool_registry(registry: dict):
    """mock TOOL_REGISTRY，返回 patch context manager。"""
    return patch("app.agent.tools.registry.TOOL_REGISTRY", registry)


class _MockTool:
    """包装异步函数为 LangChain 工具兼容对象。"""

    def __init__(self, fn):
        self._fn = fn

    async def ainvoke(self, args: dict) -> dict:
        return await self._fn(**args)


def _interrupt_raises(payload):
    """模拟 interrupt 首遍：抛 GraphInterrupt 终止节点。"""
    raise GraphInterrupt()


def _interrupt_returns(decision: dict):
    """模拟 interrupt resume 遍：返回用户决策值。"""

    def _fn(payload):
        return decision

    return _fn


async def _fake_readonly_sql(counter: dict, **kwargs) -> dict:
    counter["readonly"] += 1
    return {
        "columns": ["id"],
        "rows": [[1]],
        "total_rows": 1,
        "execution_time_ms": 1,
        "audit_status": "passed",
        "is_readonly": True,
        "summary": "返回 1 行",
    }


async def _fake_write_sql(counter: dict, **kwargs) -> dict:
    counter["write"] += 1
    return {
        "summary": "写入成功",
        "affected_rows": 1,
        "total_rows": 1,
        "execution_time_ms": 1,
        "audit_status": "passed",
        "is_readonly": False,
    }


async def _fake_transaction_write(counter: dict, **kwargs) -> dict:
    counter["tx"] += 1
    statements = kwargs.get("statements") or []
    return {
        "summary": "事务提交成功",
        "affected_rows": len(statements),
        "total_rows": 0,
        "execution_time_ms": 1,
        "audit_status": "passed",
        "is_readonly": False,
        "per_statement": [{"sql": stmt, "affected_rows": 1} for stmt in statements],
    }


# =============================================================================
# 1. 单遍只读执行 + 消息配对
# =============================================================================


class TestSinglePassReadonly:
    @pytest.mark.asyncio
    async def test_readonly_executes_once_no_interrupt(self):
        """单遍只读：无 interrupt，执行一次，ToolMessage 与 tool_call 配对。"""
        counter = {"readonly": 0, "write": 0}
        registry = {
            "execute_readonly_sql": _MockTool(lambda **kw: _fake_readonly_sql(counter, **kw)),
        }
        tcs = [_tc("execute_readonly_sql", {"sql": "SELECT * FROM t"}, "call_ro")]
        with _make_tool_registry(registry):
            res = await run_security_pipeline(tcs, _profiles(tcs), _CONN, _ctx(), "r", 1, "s1", [])

        assert counter["readonly"] == 1
        assert len(res["messages"]) == 1
        assert res["messages"][0].tool_call_id == "call_ro"
        assert res["messages"][0].name == "execute_readonly_sql"
        # 单遍：sse_events = 历史 + 新事件
        assert res["consecutive_blocks"] == 0


# =============================================================================
# 2/3. readonly 写 / DDL → 确认前审计拦截
# =============================================================================


class TestPreConfirmBlocks:
    @pytest.mark.asyncio
    async def test_readonly_insert_blocked_before_confirm(self):
        """readonly 发 INSERT 到 execute_write_sql：确认前被 SQL_AUDIT_BLOCKED 拦。"""
        counter = {"readonly": 0, "write": 0}
        registry = {
            "execute_write_sql": _MockTool(lambda **kw: _fake_write_sql(counter, **kw)),
        }
        tcs = [_tc("execute_write_sql", {"sql": "INSERT INTO t VALUES (1)"}, "call_w")]
        # conn_config 必须与 ctx 的角色一致（SQLAuditStage 读 conn_config.user_role）
        ctx = _ctx(user_role="readonly")
        with _make_tool_registry(registry):
            res = await run_security_pipeline(
                tcs, _profiles(tcs), ctx.conn_config, ctx, "r", 1, "s1", []
            )

        # 确认前拦截：无 pending（未走到 interrupt），消息为审计拦截
        assert "SQL 审计未通过" in res["messages"][0].content
        assert res["messages"][0].tool_call_id == "call_w"
        assert counter["write"] == 0  # 未执行
        # 无 confirm_required SSE 事件（不弹确认卡片）
        assert "confirm_required" not in [e["type"] for e in res["sse_events"]]

    @pytest.mark.asyncio
    async def test_ddl_blocked_before_confirm(self):
        """任何人发 DROP TABLE 到 write 工具：确认前被拦，不弹确认卡片。

        现由语句类型白名单在审计之前拦截（DROP 不在 INSERT/UPDATE/DELETE 名单内），
        仍满足"确认前拦截、不弹确认卡片"语义。
        """
        counter = {"write": 0}
        registry = {
            "execute_write_sql": _MockTool(lambda **kw: _fake_write_sql(counter, **kw)),
        }
        tcs = [_tc("execute_write_sql", {"sql": "DROP TABLE t"}, "call_ddl")]
        with _make_tool_registry(registry):
            res = await run_security_pipeline(tcs, _profiles(tcs), _CONN, _ctx(), "r", 1, "s1", [])

        assert "只允许 DELETE / INSERT / UPDATE" in res["messages"][0].content
        assert counter["write"] == 0
        assert "confirm_required" not in [e["type"] for e in res["sse_events"]]


# =============================================================================
# 4/5. 写确认流：interrupt 首遍 raise → resume 执行/拒绝
# =============================================================================


class TestWriteConfirmFlow:
    @pytest.mark.asyncio
    async def test_interrupt_first_pass_then_resume_execute_once(self):
        """首遍抛 GraphInterrupt；resume 后执行恰一次。"""
        counter = {"write": 0}
        registry = {
            "execute_write_sql": _MockTool(lambda **kw: _fake_write_sql(counter, **kw)),
        }
        tcs = [_tc("execute_write_sql", {"sql": "INSERT INTO t VALUES (1)"}, "call_w")]
        profiles = _profiles(tcs)

        # ── 首遍：interrupt 抛 GraphInterrupt，节点被终止 ──
        with _make_tool_registry(registry), pytest.raises(GraphInterrupt):
            await run_security_pipeline(
                tcs,
                profiles,
                _CONN,
                _ctx(),
                "r",
                1,
                "s1",
                [],
                interrupt_fn=_interrupt_raises,
            )
        assert counter["write"] == 0  # 首遍绝不执行

        # ── resume 遍：interrupt 返回批准决策 → 执行恰一次 ──
        decision = {"approved_tool_call_ids": ["call_w"], "denied_tool_call_ids": []}
        with _make_tool_registry(registry):
            res = await run_security_pipeline(
                tcs,
                profiles,
                _CONN,
                _ctx(),
                "r",
                1,
                "s1",
                [],
                interrupt_fn=_interrupt_returns(decision),
            )
        assert counter["write"] == 1
        assert "写入成功" in res["messages"][0].content

    @pytest.mark.asyncio
    async def test_denied_write_not_executed(self):
        """拒绝确认 → 取消消息 + 不执行。"""
        counter = {"write": 0}
        registry = {
            "execute_write_sql": _MockTool(lambda **kw: _fake_write_sql(counter, **kw)),
        }
        tcs = [_tc("execute_write_sql", {"sql": "INSERT INTO t VALUES (1)"}, "call_w")]
        decision = {"approved_tool_call_ids": [], "denied_tool_call_ids": ["call_w"]}
        with _make_tool_registry(registry):
            res = await run_security_pipeline(
                tcs,
                _profiles(tcs),
                _CONN,
                _ctx(),
                "r",
                1,
                "s1",
                [],
                interrupt_fn=_interrupt_returns(decision),
            )

        assert "操作已被用户取消" in res["messages"][0].content
        assert counter["write"] == 0
        assert res["sse_events"][0]["summary"] == "用户取消了操作"

    @pytest.mark.asyncio
    async def test_confirm_payload_structure(self):
        """interrupt payload 逐字段保留（type/writes/safe_tool_count/session_id）。"""
        counter = {"write": 0, "readonly": 0}
        registry = {
            "execute_write_sql": _MockTool(lambda **kw: _fake_write_sql(counter, **kw)),
            "execute_readonly_sql": _MockTool(lambda **kw: _fake_readonly_sql(counter, **kw)),
        }
        tcs = [
            _tc("execute_write_sql", {"sql": "INSERT INTO t VALUES (1)"}, "call_w"),
            _tc("execute_readonly_sql", {"sql": "SELECT * FROM t"}, "call_ro"),
        ]
        captured: dict = {}

        def _capture_interrupt(payload):
            captured["payload"] = payload
            raise GraphInterrupt()

        with _make_tool_registry(registry), pytest.raises(GraphInterrupt):
            await run_security_pipeline(
                tcs,
                _profiles(tcs),
                _CONN,
                _ctx(),
                "r",
                1,
                "s1",
                [],
                interrupt_fn=_capture_interrupt,
            )

        payload = captured["payload"]
        assert payload["type"] == "confirm_required"
        assert payload["safe_tool_count"] == 1  # 只读工具
        assert payload["session_id"] == "s1"
        writes = payload["writes"]
        assert len(writes) == 1
        w = writes[0]
        assert w["tool_call_id"] == "call_w"
        assert w["tool"] == "execute_write_sql"
        assert w["category"] == "sql_write"
        assert w["description"].startswith("execute_write_sql")
        # 连接注入参数被过滤（details 干净）
        for injected in ("password", "connection_id", "user_role"):
            assert injected not in w["details"]


# =============================================================================
# 6/7. 混合写 + 只读：EXPLAIN 恰一次；resume 截断 sse_events
# =============================================================================


class TestMixedRoundAndTruncation:
    @pytest.mark.asyncio
    async def test_mixed_write_plus_readonly_explain_once(self):
        """写工具确认 + 只读 EXPLAIN（row_estimation）在 resume 遍恰执行一次。"""
        counter = {"readonly": 0, "write": 0}
        registry = {
            "execute_write_sql": _MockTool(lambda **kw: _fake_write_sql(counter, **kw)),
            "execute_readonly_sql": _MockTool(lambda **kw: _fake_readonly_sql(counter, **kw)),
        }
        re_counter = {"calls": 0}

        async def _mock_re_check(self, tool_call, conn_config, ctx):
            re_counter["calls"] += 1
            return type("R", (), {"blocked": False, "warnings": []})()

        tcs = [
            _tc("execute_write_sql", {"sql": "INSERT INTO t VALUES (1)"}, "call_w"),
            _tc("execute_readonly_sql", {"sql": "SELECT * FROM t"}, "call_ro"),
        ]
        profiles = _profiles(tcs)

        # 首遍：interrupt 抛异常 → Phase 3（含 EXPLAIN）不可达
        with (
            _make_tool_registry(registry),
            patch(
                "app.agent.security.stages.RowEstimationStage.check",
                _mock_re_check,
            ),
            pytest.raises(GraphInterrupt),
        ):
            await run_security_pipeline(
                tcs,
                profiles,
                _CONN,
                _ctx(),
                "r",
                1,
                "s1",
                [],
                interrupt_fn=_interrupt_raises,
            )
        assert re_counter["calls"] == 0  # 首遍 EXPLAIN = 0

        # resume 遍：批准写 → 只读 EXPLAIN 恰一次 + 两个工具各执行一次
        decision = {"approved_tool_call_ids": ["call_w"], "denied_tool_call_ids": []}
        with (
            _make_tool_registry(registry),
            patch(
                "app.agent.security.stages.RowEstimationStage.check",
                _mock_re_check,
            ),
        ):
            res = await run_security_pipeline(
                tcs,
                profiles,
                _CONN,
                _ctx(),
                "r",
                1,
                "s1",
                [],
                interrupt_fn=_interrupt_returns(decision),
            )
        assert re_counter["calls"] == 1  # EXPLAIN 恰一次
        assert counter["write"] == 1
        assert counter["readonly"] == 1
        assert len(res["messages"]) == 2  # 两条 ToolMessage 配对

    @pytest.mark.asyncio
    async def test_resume_truncates_sse_events(self):
        """resume 遍返回的 sse_events 不含历史 tool_call 事件（截断）。"""
        counter = {"write": 0}
        registry = {
            "execute_write_sql": _MockTool(lambda **kw: _fake_write_sql(counter, **kw)),
        }
        tcs = [_tc("execute_write_sql", {"sql": "INSERT INTO t VALUES (1)"}, "call_w")]
        # 模拟 checkpoint 中持久化的历史 tool_call 事件（agent_node 产生）
        initial_sse = [
            {
                "type": "tool_call",
                "tool": "execute_write_sql",
                "args": {"sql": "INSERT INTO t VALUES (1)"},
                "tool_call_id": "call_w",
                "agent_run_id": "r",
                "iteration": 1,
            }
        ]
        decision = {"approved_tool_call_ids": ["call_w"], "denied_tool_call_ids": []}
        with _make_tool_registry(registry):
            res = await run_security_pipeline(
                tcs,
                _profiles(tcs),
                _CONN,
                _ctx(),
                "r",
                1,
                "s1",
                initial_sse,
                interrupt_fn=_interrupt_returns(decision),
            )

        # resume 遍：只返回新事件，历史 tool_call 不重发
        assert all(e["type"] != "tool_call" for e in res["sse_events"])
        assert [e["type"] for e in res["sse_events"]] == ["tool_result"]

    @pytest.mark.asyncio
    async def test_single_pass_keeps_historical_sse(self):
        """单遍（无 interrupt）：返回 历史 + 新事件（chat.py emitted_count 去重）。"""
        counter = {"readonly": 0}
        registry = {
            "execute_readonly_sql": _MockTool(lambda **kw: _fake_readonly_sql(counter, **kw)),
        }
        tcs = [_tc("execute_readonly_sql", {"sql": "SELECT * FROM t"}, "call_ro")]
        initial_sse = [{"type": "tool_call", "tool_call_id": "call_ro"}]
        with _make_tool_registry(registry):
            res = await run_security_pipeline(
                tcs, _profiles(tcs), _CONN, _ctx(), "r", 1, "s1", initial_sse
            )
        assert res["sse_events"][0]["type"] == "tool_call"  # 历史保留
        assert res["sse_events"][-1]["type"] == "tool_result"  # 新事件追加


# =============================================================================
# 8. consecutive_blocks 递增 / 重置 / >=4 强制终止
# =============================================================================


class TestConsecutiveBlocks:
    @pytest.mark.asyncio
    async def test_row_estimation_block_increments(self):
        """RE 拦截 → consecutive_blocks 递增。"""
        counter = {"readonly": 0}
        registry = {
            "execute_readonly_sql": _MockTool(lambda **kw: _fake_readonly_sql(counter, **kw)),
        }
        tcs = [_tc("execute_readonly_sql", {"sql": "SELECT * FROM big"}, "call_ro")]

        async def _mock_re_block(self, tool_call, conn_config, ctx):
            from app.agent.security.models import StageResult

            return StageResult(
                blocked=True,
                block_code="ROW_ESTIMATION_BLOCKED",
                reason="[EXPLAIN 安全评估] 查询被阻断\n原因: 全表扫描预估读取 5000000 行",
            )

        with (
            _make_tool_registry(registry),
            patch(
                "app.agent.security.stages.RowEstimationStage.check",
                _mock_re_block,
            ),
        ):
            res = await run_security_pipeline(
                tcs,
                _profiles(tcs),
                _CONN,
                _ctx(consecutive_blocks=1),
                "r",
                1,
                "s1",
                [],
            )
        assert res["consecutive_blocks"] == 2
        assert counter["readonly"] == 0  # 被拦未执行
        # RE 拦截消息（入口=1 → k=2 → 普通拦截消息，非 advisory）
        assert "操作被安全策略拦截" in res["messages"][0].content

    @pytest.mark.asyncio
    async def test_row_estimation_block_parallel_numbering(self):
        """同轮多个 RE 拦截 → advisory 序号递增（修复并行旧值）。"""
        counter = {"readonly": 0}
        registry = {
            "execute_readonly_sql": _MockTool(lambda **kw: _fake_readonly_sql(counter, **kw)),
        }
        tcs = [
            _tc("execute_readonly_sql", {"sql": "SELECT * FROM a"}, "call_a"),
            _tc("execute_readonly_sql", {"sql": "SELECT * FROM b"}, "call_b"),
            _tc("execute_readonly_sql", {"sql": "SELECT * FROM c"}, "call_c"),
        ]

        async def _mock_re_block(self, tool_call, conn_config, ctx):
            from app.agent.security.models import StageResult

            return StageResult(
                blocked=True,
                block_code="ROW_ESTIMATION_BLOCKED",
                reason="[EXPLAIN 安全评估] 查询被阻断",
            )

        with (
            _make_tool_registry(registry),
            patch(
                "app.agent.security.stages.RowEstimationStage.check",
                _mock_re_block,
            ),
        ):
            res = await run_security_pipeline(
                tcs,
                _profiles(tcs),
                _CONN,
                _ctx(consecutive_blocks=0),
                "r",
                1,
                "s1",
                [],
            )
        # 入口 0 → k=1,2,3：第三个得到 advisory（第 3 次）
        msgs = res["messages"]
        assert "[连续拦截提醒 - 第 1 次]" not in msgs[0].content
        assert "[连续拦截提醒 - 第 2 次]" not in msgs[1].content
        assert "[连续拦截提醒 - 第 3 次]" in msgs[2].content
        assert res["consecutive_blocks"] == 1  # 本轮算 1 次连续拦截

    @pytest.mark.asyncio
    async def test_no_block_resets_counter(self):
        """本轮无 RE 拦截 → consecutive_blocks 重置为 0。"""
        counter = {"readonly": 0}
        registry = {
            "execute_readonly_sql": _MockTool(lambda **kw: _fake_readonly_sql(counter, **kw)),
        }
        tcs = [_tc("execute_readonly_sql", {"sql": "SELECT * FROM t"}, "call_ro")]
        with _make_tool_registry(registry):
            res = await run_security_pipeline(
                tcs,
                _profiles(tcs),
                _CONN,
                _ctx(consecutive_blocks=3),
                "r",
                1,
                "s1",
                [],
            )
        assert res["consecutive_blocks"] == 0

    @pytest.mark.asyncio
    async def test_force_terminate_at_4(self):
        """连续拦截 >=4 → 强制终止（is_complete=True + error 事件）。"""
        counter = {"readonly": 0}
        registry = {
            "execute_readonly_sql": _MockTool(lambda **kw: _fake_readonly_sql(counter, **kw)),
        }
        tcs = [_tc("execute_readonly_sql", {"sql": "SELECT * FROM big"}, "call_ro")]

        async def _mock_re_block(self, tool_call, conn_config, ctx):
            from app.agent.security.models import StageResult

            return StageResult(
                blocked=True,
                block_code="ROW_ESTIMATION_BLOCKED",
                reason="[EXPLAIN 安全评估] 查询被阻断",
            )

        with (
            _make_tool_registry(registry),
            patch(
                "app.agent.security.stages.RowEstimationStage.check",
                _mock_re_block,
            ),
        ):
            res = await run_security_pipeline(
                tcs,
                _profiles(tcs),
                _CONN,
                _ctx(consecutive_blocks=3),
                "r",
                1,
                "s1",
                [],
            )
        assert res["is_complete"] is True
        assert res["consecutive_blocks"] == 4
        assert res["sse_events"][-1]["error_code"] == "CONSECUTIVE_BLOCKS_EXCEEDED"


# =============================================================================
# 9. 工具未注册
# =============================================================================


class TestToolNotFound:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_not_found(self):
        """未知工具 → "未注册" ToolMessage。"""
        tcs = [_tc("no_such_tool", {}, "call_x")]
        with _make_tool_registry({}):
            res = await run_security_pipeline(tcs, _profiles(tcs), _CONN, _ctx(), "r", 1, "s1", [])
        assert "未注册" in res["messages"][0].content
        assert len(res["messages"]) == 1  # 配对


# =============================================================================
# 10. 混合 readonly 写拦截 + 合法写确认（同轮 V 型场景）
# =============================================================================


class TestMixedBlockedAndPending:
    @pytest.mark.asyncio
    async def test_blocked_ddl_and_pending_write_same_round(self):
        """同轮 DDL 被拦 + 合法 INSERT：DDL 不弹卡、INSERT 弹卡。"""
        counter = {"write": 0}
        registry = {
            "execute_write_sql": _MockTool(lambda **kw: _fake_write_sql(counter, **kw)),
        }
        tcs = [
            _tc("execute_write_sql", {"sql": "DROP TABLE t"}, "call_ddl"),
            _tc("execute_write_sql", {"sql": "INSERT INTO t VALUES (1)"}, "call_ins"),
        ]
        captured: dict = {}

        def _capture_interrupt(payload):
            captured["payload"] = payload
            raise GraphInterrupt()

        with _make_tool_registry(registry), pytest.raises(GraphInterrupt):
            await run_security_pipeline(
                tcs,
                _profiles(tcs),
                _CONN,
                _ctx(),
                "r",
                1,
                "s1",
                [],
                interrupt_fn=_capture_interrupt,
            )

        # payload 只含通过 PRE_CONFIRM 的 INSERT（DDL 不弹卡）
        writes = captured["payload"]["writes"]
        assert [w["tool_call_id"] for w in writes] == ["call_ins"]
        assert captured["payload"]["safe_tool_count"] == 1  # DDL 被拦，不计数为 write


# =============================================================================
# 事务写工具（execute_write_transaction）确认流
# =============================================================================


class TestTransactionWriteFlow:
    @pytest.mark.asyncio
    async def test_readonly_transaction_blocked_before_confirm(self):
        """readonly 发事务写：确认前被逐条审计拦，不弹确认卡片、不执行。"""
        counter = {"tx": 0}
        registry = {
            "execute_write_transaction": _MockTool(
                lambda **kw: _fake_transaction_write(counter, **kw)
            ),
        }
        tcs = [
            _tc(
                "execute_write_transaction",
                {"statements": ["INSERT INTO t VALUES (1)"]},
                "call_tx",
            )
        ]
        # conn_config 必须与 ctx 的角色一致（TransactionAuditStage 读 conn_config.user_role）
        ctx = _ctx(user_role="readonly")
        with _make_tool_registry(registry):
            res = await run_security_pipeline(
                tcs, _profiles(tcs), ctx.conn_config, ctx, "r", 1, "s1", []
            )

        assert "SQL 审计未通过" in res["messages"][0].content
        assert counter["tx"] == 0  # 未执行
        assert "confirm_required" not in [e["type"] for e in res["sse_events"]]

    @pytest.mark.asyncio
    async def test_admin_transaction_interrupt_then_resume_executes_once(self):
        """admin 合法事务：首遍 interrupt 抛 GraphInterrupt，resume 批准后执行恰一次。"""
        counter = {"tx": 0}
        registry = {
            "execute_write_transaction": _MockTool(
                lambda **kw: _fake_transaction_write(counter, **kw)
            ),
        }
        tcs = [
            _tc(
                "execute_write_transaction",
                {"statements": ["INSERT INTO orders (id) VALUES (1)"]},
                "call_tx",
            )
        ]
        profiles = _profiles(tcs)

        # ── 首遍：interrupt 抛 GraphInterrupt，节点被终止，绝不执行 ──
        with _make_tool_registry(registry), pytest.raises(GraphInterrupt):
            await run_security_pipeline(
                tcs,
                profiles,
                _CONN,
                _ctx(),
                "r",
                1,
                "s1",
                [],
                interrupt_fn=_interrupt_raises,
            )
        assert counter["tx"] == 0

        # ── resume 遍：批准 → 执行恰一次 ──
        decision = {"approved_tool_call_ids": ["call_tx"], "denied_tool_call_ids": []}
        with _make_tool_registry(registry):
            res = await run_security_pipeline(
                tcs,
                profiles,
                _CONN,
                _ctx(),
                "r",
                1,
                "s1",
                [],
                interrupt_fn=_interrupt_returns(decision),
            )
        assert counter["tx"] == 1
        assert "事务提交成功" in res["messages"][0].content

    @pytest.mark.asyncio
    async def test_transaction_confirm_payload_sql(self):
        """确认 payload：事务工具 category=sql_write 且 details.sql 为 join 后全部语句。"""
        counter = {"tx": 0}
        registry = {
            "execute_write_transaction": _MockTool(
                lambda **kw: _fake_transaction_write(counter, **kw)
            ),
        }
        statements = [
            "INSERT INTO orders (id, qty) VALUES (1, 10)",
            "UPDATE inventory SET stock = stock - 10 WHERE sku = 'A'",
        ]
        tcs = [_tc("execute_write_transaction", {"statements": statements}, "call_tx")]
        captured: dict = {}

        def _capture_interrupt(payload):
            captured["payload"] = payload
            raise GraphInterrupt()

        with _make_tool_registry(registry), pytest.raises(GraphInterrupt):
            await run_security_pipeline(
                tcs,
                _profiles(tcs),
                _CONN,
                _ctx(),
                "r",
                1,
                "s1",
                [],
                interrupt_fn=_capture_interrupt,
            )

        writes = captured["payload"]["writes"]
        assert len(writes) == 1
        w = writes[0]
        assert w["tool_call_id"] == "call_tx"
        assert w["tool"] == "execute_write_transaction"
        assert w["category"] == "sql_write"
        assert w["details"]["sql"] == (
            "INSERT INTO orders (id, qty) VALUES (1, 10);\n"
            "UPDATE inventory SET stock = stock - 10 WHERE sku = 'A'"
        )
        # 连接注入参数被过滤（details 干净）
        for injected in ("password", "connection_id", "user_role"):
            assert injected not in w["details"]


# =============================================================================
# 确认 payload 写影响预估注入（write-impact-estimate-plan Task 5）
# =============================================================================


# MySQL EXPLAIN FORMAT=JSON 固化样本：UPDATE 命中 5000 行
_MYSQL_IMPACT_EXPLAIN = (
    '{"query_block": {"select_id": 1, "table": {"table_name": "t", '
    '"access_type": "range", "rows_examined_per_scan": 5000, "filtered": 100.0}}}'
)


class TestConfirmPayloadImpact:
    """ImpactEstimateStage 写入 evidence → build_confirm_payload 注入 writes[].impact。

    本类显式覆盖 _no_real_db_adapter autouse 夹具：为临时适配器注入 mock EXPLAIN
    （外层夹具先抛错、此处内层 patch 覆盖，模拟预估成功的真实路径）。
    """

    @pytest.mark.asyncio
    async def test_payload_includes_impact_when_estimable(self):
        """UPDATE 可 EXPLAIN 预估 → 确认 payload writes 含 impact（预估行数）。"""
        counter = {"write": 0}
        registry = {
            "execute_write_sql": _MockTool(lambda **kw: _fake_write_sql(counter, **kw)),
        }
        tcs = [_tc("execute_write_sql", {"sql": "UPDATE t SET a=1 WHERE id>0"}, "call_w")]
        adapter = AsyncMock()
        adapter.explain.return_value = {
            "explain_output": _MYSQL_IMPACT_EXPLAIN,
            "format": "json",
        }
        captured: dict = {}

        def _capture_interrupt(payload):
            captured["payload"] = payload
            raise GraphInterrupt()

        with (
            _make_tool_registry(registry),
            patch(
                "app.agent.security.stages._create_temp_adapter",
                AsyncMock(return_value=adapter),
            ),
            pytest.raises(GraphInterrupt),
        ):
            await run_security_pipeline(
                tcs,
                _profiles(tcs),
                _CONN,
                _ctx(),
                "r",
                1,
                "s1",
                [],
                interrupt_fn=_capture_interrupt,
            )

        writes = captured["payload"]["writes"]
        assert len(writes) == 1
        impact = writes[0].get("impact")
        assert impact is not None
        assert impact["available"] is True
        assert impact["estimated_rows"] == 5000
        assert impact["method"] == "explain"
        # impact 是顶层字段，不进 details（details 仅 SQL，前端渲染不被污染）
        assert "impact" not in writes[0]["details"]

    @pytest.mark.asyncio
    async def test_payload_omits_impact_when_explain_fails(self):
        """EXPLAIN 失败 → writes 不含 impact（fail-open，确认流程照常）。"""
        counter = {"write": 0}
        registry = {
            "execute_write_sql": _MockTool(lambda **kw: _fake_write_sql(counter, **kw)),
        }
        tcs = [_tc("execute_write_sql", {"sql": "UPDATE t SET a=1 WHERE id>0"}, "call_w")]
        adapter = AsyncMock()
        adapter.explain.side_effect = RuntimeError("db down")
        captured: dict = {}

        def _capture_interrupt(payload):
            captured["payload"] = payload
            raise GraphInterrupt()

        with (
            _make_tool_registry(registry),
            patch(
                "app.agent.security.stages._create_temp_adapter",
                AsyncMock(return_value=adapter),
            ),
            pytest.raises(GraphInterrupt),
        ):
            await run_security_pipeline(
                tcs,
                _profiles(tcs),
                _CONN,
                _ctx(),
                "r",
                1,
                "s1",
                [],
                interrupt_fn=_capture_interrupt,
            )

        w = captured["payload"]["writes"][0]
        assert "impact" not in w  # 无预估不注入，writes 结构与未接入预估时一致
