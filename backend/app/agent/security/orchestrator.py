"""
安全流水线编排器（security-pipeline-north-star-plan §2.5 / 任务 B1）。

三阶段编排 + 工具执行原语（自 tool_node._run_one_tool 迁入）：
  - run_pre_confirm:            Phase 1 PRE_CONFIRM（并行逐工具，纯审计无 DB 副作用）
  - build_confirm_payload / apply_confirm_decision: Phase 2 CONFIRM（批量 interrupt）
  - run_phase3:                 Phase 3 PRE_EXECUTE + 执行（并行逐工具）
  - run_security_pipeline:      顶层编排入口，返回图节点更新 dict

关键正确性保证（LangGraph 1.2.8 interrupt 语义）：
  - 不重复执行 / EXPLAIN 恰一次：Phase 3 只在 resume 遍（有中断时）或单遍（无中断时）
    可达——首遍在 interrupt() 抛 GraphInterrupt 提前终止，永不进入 Phase 3。
  - 不替换 AIMessage：每条原始 tool_call 都产出一条 ToolMessage（审拦/拒绝/RE拦截/执行
    各一条），满足 LLM API 工具调用-结果配对约束；tools→agent 固定边，全拦/全拒自然回
    agent 由 LLM 回应。
  - SSE 顺序：`[PRE_CONFIRM 拦截] → [CONFIRM 拒绝] → [PRE_EXECUTE 拦截/执行]`。
  - resume 遍截断 sse_events：只返回本遍新事件，否则历史 tool_call 事件被重发、
    前端重复卡片（chat.py emitted_count 两条路径都对 → 零改动）。
  - consecutive_blocks：读入口值，RE 拦截只认结构化 block_code=="ROW_ESTIMATION_BLOCKED"；
    先算 new 后建 advisory（同一轮内第 k 个被拦工具用递增序号，修复并行旧值问题）；
    >=4 强制终止（is_complete=True + error 事件）。
"""

from __future__ import annotations

import asyncio
import json as _json
import time
import types
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, get_args, get_origin

import structlog
from langchain_core.messages import ToolMessage
from langgraph.types import interrupt

from app.agent.security.models import (
    SecurityContext,
    SecurityProfile,
    StagePhase,
    StageResult,
)
from app.agent.security.registry import STAGE_REGISTRY, get_security_profile
from app.agent.security.stages import ConfirmStage
from app.config import settings

logger = structlog.get_logger(__name__)

# 每轮 ReAct 迭代中最大并行工具数（从配置读取，默认 5）
_MAX_CONCURRENT_TOOLS = max(1, settings.AGENT_MAX_CONCURRENT_TOOLS)

# 连续 RE 拦截强制终止阈值（防改写死循环）
_MAX_CONSECUTIVE_BLOCKS = 4

# 工具连接注入参数集合（执行前合并到 tool_args）
_INJECTED_KEYS = (
    "connection_id",
    "db_type",
    "host",
    "port",
    "database",
    "user",
    "password",
    "ssl_enabled",
    "ssl_ca_cert",
)


@dataclass
class ToolOutcome:
    """单个工具调用在流水线中的最终结果（供装配阶段构造消息/SSE）。

    Attributes:
        tc: 原始 ToolCall 字典。
        kind: 结果类型（blocked / denied / re_blocked / executed / not_found）。
        block_phase: 拦截发生的相位（"PRE_CONFIRM" / "PRE_EXECUTE"），仅 blocked 类。
        reason: 拦截原因（blocked / re_blocked）。
        sql: 被拦 SQL（re_blocked 的 advisory 需要）。
        description: 拒绝操作的描述（denied）。
        content: 已构造的 ToolMessage content（executed）。
        sse: 该工具产生的 SSE 事件（executed）。
    """

    tc: Mapping[str, Any]
    kind: str
    block_phase: str | None = None
    reason: str | None = None
    sql: str | None = None
    description: str | None = None
    content: str | None = None
    sse: list[dict] = field(default_factory=list)


# =============================================================================
# 工具参数类型修正（自 tool_node._coerce_tool_args 迁入）
# =============================================================================


def _is_list_type(annotation: Any) -> bool:
    """判断类型注解中是否包含 list 类型（处理 Union 如 list[str] | None）。"""
    origin = get_origin(annotation)
    if origin is list:
        return True
    # list[str] | None → UnionType, args=(list[str], NoneType)
    if origin is type(None) or origin is types.UnionType:  # noqa: E721
        return any(_is_list_type(a) for a in get_args(annotation))
    return False


def _coerce_tool_args(tool_fn: Any, tool_args: dict[str, Any]) -> None:
    """修正 LLM 工具调用参数的类型不匹配（就地修改）。

    某些 LLM（如 DeepSeek 部分版本）在 function calling 中可能把数组参数
    传成 JSON 字符串（如 '"[\"a\",\"b\"]"') 而非原生 list，
    导致 Pydantic 校验失败。此函数检查工具 schema 中标为 list 的字段，
    若实际传入的是字符串则尝试解析。
    """
    schema = getattr(tool_fn, "args_schema", None)
    if schema is None:
        return
    for field_name, field_obj in schema.model_fields.items():
        if field_name not in tool_args:
            continue
        val = tool_args[field_name]
        if not isinstance(val, str):
            continue
        if not _is_list_type(field_obj.annotation):
            continue
        # 字段标为 list 但值是字符串 → 尝试 JSON 解析
        try:
            tool_args[field_name] = _json.loads(val)
        except (_json.JSONDecodeError, TypeError):
            logger.debug(
                "tool arg 类型修正失败",
                field=field_name,
                value_preview=str(val)[:80],
            )


# =============================================================================
# 连续拦截强提示消息（自 tool_node._build_row_estimation_block_advisory 迁入）
# =============================================================================


def _build_row_estimation_block_advisory(
    block_count: int,
    safety_reason: str,
    sql_text: str,
) -> str:
    """构造连续被 RowEstimationStage 拦截时的强提示消息。

    当同一查询连续 >= 3 次被 EXPLAIN 安全评估拦截时，
    告知 LLM 这不是 SQL 写法问题而是数据量问题，应立即停止改写并告知用户。
    """
    return (
        f"[连续拦截提醒 - 第 {block_count} 次]\n\n"
        f"你的 SQL 查询已连续 {block_count} 次因数据量过大被 EXPLAIN 安全评估拦截。"
        "这不是 SQL 语法或写法问题，而是查询本身需要处理的数据量超过了系统安全阈值。"
        "继续改写 SQL 无法解决此问题。\n\n"
        "请**立即停止调用 execute_readonly_sql**，改为直接向用户说明情况：\n"
        "1. 告知用户该查询预估需扫描大量数据，具体评估详情如下：\n"
        f"{safety_reason}\n"
        "2. 将你最后一次的 SQL 语句提供给用户，方便用户在数据库客户端中手动执行\n"
        "3. 建议用户缩小查询范围（如添加更精确的 WHERE 条件、使用 LIMIT、"
        "或改用聚合统计替代明细查询）\n"
        "4. 建议用户在数据库客户端中先手动执行 EXPLAIN 确认执行计划\n\n"
        "最终被拦截的 SQL 语句：\n"
        f"```sql\n{sql_text}\n```"
    )


# =============================================================================
# Phase 1: PRE_CONFIRM（并行逐工具，纯审计无 DB 副作用）
# =============================================================================


async def _run_tool_pre_confirm(
    tc: Mapping[str, Any],
    profile: SecurityProfile,
    conn_config: dict[str, Any],
    ctx: SecurityContext,
) -> StageResult | None:
    """串行跑单个工具的 PRE_CONFIRM 阶段，返回第一个拦截结果（无拦截返回 None）。"""
    for ref in profile.stages:
        if ref.phase is not StagePhase.PRE_CONFIRM:
            continue
        # StageRef.params 注入阶段构造参数（如 SQLAuditStage 的 allowed_stmt_types）
        stage = STAGE_REGISTRY[ref.name](**ref.params)
        result = await stage.check(tc, conn_config, ctx)
        if result.blocked:
            return result
    return None


async def run_pre_confirm(
    tool_calls: Sequence[Mapping[str, Any]],
    profiles: dict[str, SecurityProfile],
    conn_config: dict[str, Any],
    ctx: SecurityContext,
    semaphore: asyncio.Semaphore,
) -> dict[str, StageResult]:
    """Phase 1：并行跑每个工具的 PRE_CONFIRM 阶段。

    返回 {tool_call_id: blocked_result}（仅被拦截的），同时将被拦截的 id
    写入 ctx.blocked_calls（防御后续阶段重复处理）。

    PRE_CONFIRM 阶段必须纯（无 DB 副作用）——interrupt 重放会跑两遍。
    阶段异常按 fail-open 放行（与现状 run_safety_checks 语义一致），不阻断流程。
    """

    async def _one(tc: Mapping[str, Any]) -> tuple[str, StageResult | None]:
        async with semaphore:
            profile = profiles.get(tc["name"]) or get_security_profile(tc["name"])
            return tc["id"], await _run_tool_pre_confirm(tc, profile, conn_config, ctx)

    results = await asyncio.gather(
        *[_one(tc) for tc in tool_calls],
        return_exceptions=True,
    )

    blocked: dict[str, StageResult] = {}
    for i, r in enumerate(results):
        tc = tool_calls[i]
        if isinstance(r, BaseException):
            # PRE_CONFIRM 阶段异常 → fail-open（不拦截），记录警告
            logger.error(
                "PRE_CONFIRM 阶段异常（已放行）",
                run_id=ctx.run_id,
                tool=tc["name"],
                error=str(r)[:300],
            )
            continue
        # r = (tool_call_id, StageResult | None)——gather 保留 _one 的元组返回值
        _, result = r
        if result is not None and result.blocked:
            blocked[tc["id"]] = result
            ctx.blocked_calls.add(tc["id"])
    return blocked


# =============================================================================
# Phase 2: CONFIRM（批量 interrupt + 决策应用）
# =============================================================================


def build_confirm_payload(
    tool_calls: Sequence[Mapping[str, Any]],
    pending_ids: set[str],
    profiles: dict[str, SecurityProfile],
    session_id: str | None,
    ctx: SecurityContext | None = None,
) -> dict:
    """构建 interrupt payload（与 confirm_node 现有结构逐字段一致）。

    writes 只含通过 PRE_CONFIRM 的 CONFIRM 阶段工具（DDL 被 SQL_AUDIT_BLOCKED
    拦下后不在 pending_ids → 不再弹确认卡片）。

    ImpactEstimateStage（PRE_CONFIRM）把写影响预估写入 ctx.evidence[tool_call_id]
    ["impact"]；本函数读取并作为 writes 项**顶层字段 impact** 注入（不进 details，
    不污染 connection_kill/generic 渲染）。无预估（EXPLAIN 失败 / 字面量 INSERT /
    非预估工具）时不注入该键，writes 结构与未接入预估时逐字节一致（前端零破坏）。

    Args:
        ctx: 安全上下文（含 impact_estimate 写入的跨阶段 evidence）。缺省为 None
            时兼容未传 ctx 的调用（不注入 impact）。

    Returns:
        {type, writes, safe_tool_count, session_id}。
    """
    writes: list[dict] = []
    for tc in tool_calls:
        if tc["id"] not in pending_ids:
            continue
        profile = profiles[tc["name"]]
        confirm_ref = profile.confirm_ref()
        category = (
            (confirm_ref.params or {}).get("category", "generic")
            if confirm_ref is not None
            else "generic"
        )
        action = ConfirmStage.build_action(tc, category)
        # 写影响预估注入（evidence 由同批 PRE_CONFIRM 的 ImpactEstimateStage 写入）
        impact = None
        if ctx is not None:
            tc_evidence = ctx.evidence.get(tc["id"], {}) or {}
            impact = tc_evidence.get("impact")
        if impact is not None:
            action["impact"] = impact
        writes.append(action)
    safe_count = len(tool_calls) - len(pending_ids)
    return {
        "type": "confirm_required",
        "writes": writes,
        "safe_tool_count": safe_count,
        "session_id": session_id or "",
    }


def apply_confirm_decision(
    tool_calls: Sequence[Mapping[str, Any]],
    pending_ids: set[str],
    decision: dict[str, Any],
    ctx: SecurityContext,
) -> tuple[set[str], list[Mapping[str, Any]]]:
    """处理用户确认决策，返回 (approved_ids, denied_calls)。

    规则（与 confirm_node 语义一致，叠加安全兜底）：
      - approved → 继续执行（survivor）
      - safe（非 pending）→ 继续执行
      - denied → 剔除 + 生成取消 ToolMessage
      - 未处理的 pending（既未批准也未拒绝）→ 视作拒绝（安全兜底：不确定时不执行）
    """
    approved_ids = set(decision.get("approved_tool_call_ids", []))
    denied_ids = set(decision.get("denied_tool_call_ids", []))

    # 安全兜底：pending 中既未批准也未拒绝的写操作 → 视作拒绝
    for tc in tool_calls:
        if tc["id"] in pending_ids and tc["id"] not in approved_ids:
            ctx.blocked_calls.add(tc["id"])
            denied_ids.add(tc["id"])

    denied_calls = [tc for tc in tool_calls if tc["id"] in denied_ids]
    return approved_ids, denied_calls


# =============================================================================
# Phase 3: PRE_EXECUTE + 执行（并行逐工具，只跑一遍）
# =============================================================================


async def _execute_tool(
    tc: Mapping[str, Any],
    conn_config: dict[str, Any],
    ctx: SecurityContext,
    run_id: str,
    iteration: int,
    semaphore: asyncio.Semaphore,
    warnings: list[str] | None = None,
) -> dict:
    """单工具执行原语（连接注入 → 查找 → 类型修正 → 执行 → 截断 → 消息/SSE）。

    Returns:
        {"content": ToolMessage content, "sse": [SSE 事件], "not_found": bool}
    """
    warnings = list(warnings or [])
    async with semaphore:
        from app.agent.tools.registry import TOOL_REGISTRY  # noqa: I001

        tool_name = tc["name"]
        tool_args: dict[str, Any] = dict(tc.get("args") or {})
        local_sse: list[dict] = []
        tool_start = time.monotonic()

        # ── 1. 连接配置注入 ──
        for key in _INJECTED_KEYS:
            if key in conn_config and key not in tool_args:
                tool_args[key] = conn_config[key]
        if "user_role" not in tool_args:
            tool_args["user_role"] = conn_config.get("user_role", "readonly")

        # ── 2. 查找工具 ──
        tool_fn = TOOL_REGISTRY.get(tool_name)
        if tool_fn is None:
            logger.error(
                "工具未注册",
                run_id=run_id,
                tool=tool_name,
                available=list(TOOL_REGISTRY.keys()),
            )
            return {
                "content": f"工具 '{tool_name}' 未注册，请联系管理员",
                "sse": [
                    {
                        "type": "tool_result",
                        "tool": tool_name,
                        "summary": f"工具 '{tool_name}' 未注册",
                        "tool_call_id": tc["id"],
                        "agent_run_id": run_id,
                        "iteration": iteration,
                        "safety_checks_passed": False,
                    }
                ],
                "not_found": True,
            }

        # ── 3. 类型修正 + 执行 ──
        _coerce_tool_args(tool_fn, tool_args)
        try:
            result = await tool_fn.ainvoke(tool_args)
            # ── 3.5. LLM 上下文窗口保护：截断大结果集 ──
            if isinstance(result, dict):
                from app.engine.explain_estimator import truncate_result_for_llm

                cap = (
                    settings.EXPLAIN_MAX_CHARS
                    if ("explain_output" in result or "explain_result" in result)
                    else 40_000
                )
                result = truncate_result_for_llm(result, max_chars=cap)
        except Exception as exc:
            logger.error(
                "工具执行异常",
                run_id=run_id,
                tool=tool_name,
                error=str(exc)[:300],
            )
            return {
                "content": f"工具执行失败: {str(exc)[:200]}",
                "sse": [
                    {
                        "type": "tool_result",
                        "tool": tool_name,
                        "summary": f"执行失败: {str(exc)[:100]}",
                        "tool_call_id": tc["id"],
                        "agent_run_id": run_id,
                        "iteration": iteration,
                        "safety_checks_passed": True,
                    }
                ],
            }

        elapsed = int((time.monotonic() - tool_start) * 1000)

        logger.info(
            "工具执行完成",
            run_id=run_id,
            tool=tool_name,
            duration_ms=elapsed,
            success=True,
            result_summary=str(result)[:200],
        )

        # ── 4. 构造 ToolMessage（含安全阶段非阻断警告） ──
        tool_content = _json.dumps(result, ensure_ascii=False, default=str)
        if warnings:
            tool_content = "[性能提示] " + " | ".join(warnings) + "\n\n" + tool_content

        # ── 5. 生成 SSE 事件 ──
        if isinstance(result, dict):
            sql_text = result.get("sql") or result.get("sql_executed", "")
            if sql_text:
                local_sse.append(
                    {
                        "type": "sql",
                        "content": sql_text,
                        "audit_status": result.get("audit_status", "passed"),
                        "is_readonly": result.get("is_readonly", True),
                        "agent_run_id": run_id,
                        "iteration": iteration,
                    }
                )
            # 只读查询结果：附加导出元信息（供前端渲染「导出完整结果」按钮）
            export_meta = {}
            if result.get("is_readonly") is True and isinstance(result.get("columns"), list):
                export_meta = {
                    "export_sql": tool_args.get("sql", ""),
                    # 截断后 total_rows 保留原始总数，_original_total_rows 兼容兜底
                    "total_rows": result.get("_original_total_rows", result.get("total_rows", 0)),
                }
            local_sse.append(
                {
                    "type": "tool_result",
                    "tool": tool_name,
                    "summary": (result.get("summary", "") or f"{tool_name} 执行完成"),
                    "duration_ms": result.get("execution_time_ms", elapsed),
                    "tool_call_id": tc["id"],
                    "agent_run_id": run_id,
                    "iteration": iteration,
                    "safety_checks_passed": True,
                    **export_meta,
                }
            )
        else:
            local_sse.append(
                {
                    "type": "tool_result",
                    "tool": tool_name,
                    "summary": str(result)[:200],
                    "duration_ms": elapsed,
                    "tool_call_id": tc["id"],
                    "agent_run_id": run_id,
                    "iteration": iteration,
                    "safety_checks_passed": True,
                }
            )

        return {"content": tool_content, "sse": local_sse}


async def _run_phase3_tool(
    tc: Mapping[str, Any],
    profile: SecurityProfile,
    conn_config: dict[str, Any],
    ctx: SecurityContext,
    run_id: str,
    iteration: int,
    semaphore: asyncio.Semaphore,
) -> ToolOutcome:
    """Phase 3 单工具：跑 PRE_EXECUTE 阶段 → 拦截则返回 block outcome；通过则执行。"""
    warnings: list[str] = []
    sql = (tc.get("args") or {}).get("sql", "")

    for ref in profile.stages:
        if ref.phase is not StagePhase.PRE_EXECUTE:
            continue
        # StageRef.params 注入阶段构造参数（如 SQLAuditStage 的 allowed_stmt_types）
        stage = STAGE_REGISTRY[ref.name](**ref.params)
        result = await stage.check(tc, conn_config, ctx)
        if result.blocked:
            is_re = result.block_code == "ROW_ESTIMATION_BLOCKED"
            return ToolOutcome(
                tc=tc,
                kind="re_blocked" if is_re else "blocked",
                block_phase="PRE_EXECUTE",
                reason=result.reason,
                sql=sql,
            )
        warnings.extend(result.warnings)

    # 通过 PRE_EXECUTE → 执行
    exec_result = await _execute_tool(
        tc, conn_config, ctx, run_id, iteration, semaphore, warnings=warnings
    )
    if exec_result.get("not_found"):
        return ToolOutcome(tc=tc, kind="not_found")
    return ToolOutcome(
        tc=tc, kind="executed", content=exec_result["content"], sse=exec_result["sse"]
    )


async def run_phase3(
    tool_calls: Sequence[Mapping[str, Any]],
    profiles: dict[str, SecurityProfile],
    conn_config: dict[str, Any],
    ctx: SecurityContext,
    run_id: str,
    iteration: int,
    semaphore: asyncio.Semaphore,
) -> list[ToolOutcome]:
    """Phase 3：并行跑每个幸存工具的 PRE_EXECUTE + 执行。

    返回按输入顺序排列的 ToolOutcome 列表（re_blocked / blocked / executed / not_found）。
    """

    async def _one(tc: Mapping[str, Any]) -> ToolOutcome:
        profile = profiles.get(tc["name"]) or get_security_profile(tc["name"])
        return await _run_phase3_tool(tc, profile, conn_config, ctx, run_id, iteration, semaphore)

    results = await asyncio.gather(
        *[_one(tc) for tc in tool_calls],
        return_exceptions=True,
    )
    outcomes: list[ToolOutcome] = []
    for i, r in enumerate(results):
        if isinstance(r, BaseException):
            # Phase 3 阶段异常 → 工具级错误消息（不阻断其他工具）
            tc = tool_calls[i]
            logger.error(
                "Phase 3 工具异常",
                run_id=run_id,
                tool=tc["name"],
                error=str(r)[:300],
            )
            outcomes.append(
                ToolOutcome(
                    tc=tc,
                    kind="executed",
                    content=f"工具执行异常: {str(r)[:200]}",
                    sse=[
                        {
                            "type": "tool_result",
                            "tool": tc["name"],
                            "summary": f"执行异常: {str(r)[:100]}",
                            "tool_call_id": tc["id"],
                            "agent_run_id": run_id,
                            "iteration": iteration,
                            "safety_checks_passed": True,
                        }
                    ],
                )
            )
        else:
            outcomes.append(r)
    return outcomes


# =============================================================================
# 装配：ToolMessage（原始顺序）+ SSE 事件（阶段分组）
# =============================================================================


def _assemble_messages_and_sse(
    outcomes: list[ToolOutcome],
    run_id: str,
    iteration: int,
    ctx: SecurityContext,
) -> tuple[list[ToolMessage], list[dict]]:
    """装配 ToolMessage 列表（原始 tool_calls 顺序）与 SSE 事件（阶段分组）。

    SSE 事件顺序：`[PRE_CONFIRM 拦截] → [CONFIRM 拒绝] → [PRE_EXECUTE 拦截/执行]`。
    RE 拦截的 advisory 序号 k 从 ctx.consecutive_blocks 起逐工具递增（修复并行旧值）。
    """
    messages: list[ToolMessage] = []
    pre_confirm_events: list[dict] = []
    denied_events: list[dict] = []
    phase3_events: list[dict] = []

    # RE 拦截递增序号（先算 new 后建 advisory——new 在调用方计算，这里只负责递增 k）
    k = ctx.consecutive_blocks

    for o in outcomes:
        tc = o.tc
        tool_name = tc["name"]

        if o.kind in ("blocked", "re_blocked"):
            # 构造 ToolMessage content（RE 拦截 >= 3 次 → 强提示 advisory）
            if o.kind == "re_blocked":
                k += 1
                content = (
                    _build_row_estimation_block_advisory(k, o.reason or "", o.sql or "")
                    if k >= 3
                    else f"操作被安全策略拦截: {o.reason}"
                )
            else:
                content = f"操作被安全策略拦截: {o.reason}"
            messages.append(ToolMessage(content=content, tool_call_id=tc["id"], name=tool_name))
            ev = {
                "type": "tool_result",
                "tool": tool_name,
                "summary": f"拦截: {o.reason}",
                "tool_call_id": tc["id"],
                "agent_run_id": run_id,
                "iteration": iteration,
                "safety_checks_passed": False,
            }
            # 拦截相位决定事件分组：PRE_CONFIRM 拦截先发，PRE_EXECUTE 拦截后发
            if o.block_phase == "PRE_EXECUTE":
                phase3_events.append(ev)
            else:
                pre_confirm_events.append(ev)

        elif o.kind == "denied":
            messages.append(
                ToolMessage(
                    content=f"操作已被用户取消: {o.description or tool_name}",
                    tool_call_id=tc["id"],
                    name=tool_name,
                )
            )
            denied_events.append(
                {
                    "type": "tool_result",
                    "tool": tool_name,
                    "summary": "用户取消了操作",
                    "tool_call_id": tc["id"],
                    "agent_run_id": run_id,
                    "iteration": iteration,
                    "safety_checks_passed": False,
                }
            )

        elif o.kind == "executed":
            messages.append(
                ToolMessage(content=o.content or "", tool_call_id=tc["id"], name=tool_name)
            )
            phase3_events.extend(o.sse)

        elif o.kind == "not_found":
            messages.append(
                ToolMessage(
                    content=f"工具 '{tool_name}' 未注册，请联系管理员",
                    tool_call_id=tc["id"],
                    name=tool_name,
                )
            )
            phase3_events.append(
                {
                    "type": "tool_result",
                    "tool": tool_name,
                    "summary": f"工具 '{tool_name}' 未注册",
                    "tool_call_id": tc["id"],
                    "agent_run_id": run_id,
                    "iteration": iteration,
                    "safety_checks_passed": False,
                }
            )

    sse_events = pre_confirm_events + denied_events + phase3_events
    return messages, sse_events


# =============================================================================
# 顶层编排入口
# =============================================================================


async def run_security_pipeline(
    tool_calls: Sequence[Mapping[str, Any]],
    profiles: dict[str, SecurityProfile],
    conn_config: dict[str, Any],
    ctx: SecurityContext,
    run_id: str,
    iteration: int,
    session_id: str | None,
    initial_sse: list[dict],
    interrupt_fn: Any = interrupt,
) -> dict[str, Any]:
    """三阶段安全流水线完整编排，返回图节点更新 dict。

    Args:
        tool_calls: 本轮 AIMessage.tool_calls 列表。
        profiles: 工具名 → SecurityProfile 映射。
        conn_config: 目标数据库连接配置。
        ctx: 安全上下文（consecutive_blocks 为入口值）。
        run_id: 本次 Agent 运行 ID。
        iteration: 当前 ReAct 迭代轮次。
        session_id: 当前会话 ID（写入确认 payload）。
        initial_sse: state["sse_events"] 历史（单遍时透传；resume 遍丢弃）。
        interrupt_fn: LangGraph interrupt（可注入 mock，测试重放语义）。

    Returns:
        节点更新 dict：messages / sse_events / consecutive_blocks，
        强制终止时附 is_complete / final_answer / error 事件。
    """
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_TOOLS)

    # ── Phase 1: PRE_CONFIRM（并行逐工具，纯审计） ──
    pre_blocked = await run_pre_confirm(tool_calls, profiles, conn_config, ctx, semaphore)

    # ── Phase 2: CONFIRM（批量 interrupt，任何工具执行之前） ──
    pending_ids = {
        tc["id"]
        for tc in tool_calls
        if tc["id"] not in ctx.blocked_calls and (profiles[tc["name"]].confirm_ref() is not None)
    }
    resume_pass = False
    denied_calls: list[Mapping[str, Any]] = []
    if pending_ids:
        payload = build_confirm_payload(tool_calls, pending_ids, profiles, session_id, ctx)
        _log_writes_detected(payload, run_id)
        # interrupt() 首遍抛 GraphInterrupt 终止节点；resume 遍返回决策值。
        # 返回 dict 只包含新事件（截断）→ 由调用方据此判断 resume 遍。
        resume_value = interrupt_fn(payload)
        resume_pass = True
        approved_ids, denied_calls = apply_confirm_decision(
            tool_calls, pending_ids, resume_value, ctx
        )
        _log_decision_processed(approved_ids, denied_calls, payload, run_id)

    # 幸存者 = 未被 PRE_CONFIRM 拦截、未被拒绝的 tool_calls
    survivors = [tc for tc in tool_calls if tc["id"] not in ctx.blocked_calls]

    # ── Phase 3: PRE_EXECUTE + 执行（并行逐工具，只跑一遍） ──
    phase3_outcomes = await run_phase3(
        survivors, profiles, conn_config, ctx, run_id, iteration, semaphore
    )

    # ── 汇总 outcome（按原始 tool_calls 顺序） ──
    outcomes = _merge_outcomes(tool_calls, pre_blocked, denied_calls, phase3_outcomes)

    # ── consecutive_blocks（先算 new） ──
    re_blocked_in_order = [o for o in outcomes if o.kind == "re_blocked"]
    new_consecutive = ctx.consecutive_blocks + 1 if re_blocked_in_order else 0

    # ── 装配 messages + SSE（advisory 序号基于入口值递增） ──
    messages, new_events = _assemble_messages_and_sse(outcomes, run_id, iteration, ctx)

    # ── resume 遍截断 sse_events：只返回本遍新事件，否则历史 tool_call 被重发 ──
    sse_events = new_events if resume_pass else list(initial_sse) + new_events

    result: dict[str, Any] = {
        "messages": messages,
        "sse_events": sse_events,
        "consecutive_blocks": new_consecutive,
    }

    # ── 连续拦截 >= 4 次：强制终止，不再让 LLM 继续改写 ──
    if new_consecutive >= _MAX_CONSECUTIVE_BLOCKS:
        logger.warning(
            "连续 RowEstimationStage 拦截已达上限，强制终止 Agent",
            run_id=run_id,
            consecutive_blocks=new_consecutive,
        )
        result.update(
            {
                "final_answer": (
                    "抱歉，该查询已连续多次被安全策略拦截，"
                    "数据量过大无法安全执行。\n\n"
                    "请尝试以下方式缩小查询范围后重试：\n"
                    "- 添加更精确的 WHERE 条件过滤数据\n"
                    "- 使用 LIMIT 限制返回行数\n"
                    "- 改用 COUNT/GROUP BY 等聚合查询\n"
                    "- 在数据库客户端中直接执行以绕过安全阈值"
                ),
                "is_complete": True,
                "sse_events": sse_events
                + [
                    {
                        "type": "error",
                        "error_code": "CONSECUTIVE_BLOCKS_EXCEEDED",
                        "user_message": (
                            f"连续 {new_consecutive} 次被 EXPLAIN 安全评估拦截，Agent 已自动终止"
                        ),
                        "severity": "warning",
                    }
                ],
            }
        )
    return result


def _merge_outcomes(
    tool_calls: Sequence[Mapping[str, Any]],
    pre_blocked: dict[str, Any],
    denied_calls: list[Mapping[str, Any]],
    phase3_outcomes: list[ToolOutcome],
) -> list[ToolOutcome]:
    """将三阶段结果合并为按原始 tool_calls 顺序排列的 outcome 列表。"""
    phase3_by_id = {o.tc["id"]: o for o in phase3_outcomes}
    denied_by_id = {tc["id"]: tc for tc in denied_calls}
    outcomes: list[ToolOutcome] = []
    for tc in tool_calls:
        tc_id = tc["id"]
        if tc_id in pre_blocked:
            result = pre_blocked[tc_id]
            outcomes.append(
                ToolOutcome(
                    tc=tc,
                    kind="blocked",
                    block_phase="PRE_CONFIRM",
                    reason=result.reason,
                )
            )
        elif tc_id in denied_by_id:
            # 描述从确认 payload 的 writes 中取（用 build_action 重建以保持一致）
            desc = ConfirmStage.build_action(tc, "generic")["description"]
            outcomes.append(ToolOutcome(tc=tc, kind="denied", description=desc))
        elif tc_id in phase3_by_id:
            outcomes.append(phase3_by_id[tc_id])
        else:
            # 防御：理论上不会到达（每个 tool_call 必属于三阶段之一）
            logger.warning("tool_call 无 outcome（防御分支）", run_id=None, tool_call_id=tc_id)
            outcomes.append(
                ToolOutcome(tc=tc, kind="executed", content=f"工具 '{tc['name']}' 未处理")
            )
    return outcomes


def _log_writes_detected(payload: dict, run_id: str) -> None:
    """安全审计日志：检测到危险操作，等待用户确认。"""
    writes = payload.get("writes", [])
    logger.warning(
        "【安全审计】检测到危险操作，等待用户确认",
        run_id=run_id,
        write_count=len(writes),
        safe_count=payload.get("safe_tool_count", 0),
    )
    for w in writes:
        logger.warning(
            "【安全审计】操作详情",
            run_id=run_id,
            tool_call_id=w["tool_call_id"],
            tool=w["tool"],
            category=w["category"],
            description=w["description"],
        )


def _log_decision_processed(
    approved_ids: set[str],
    denied_calls: list[Mapping[str, Any]],
    payload: dict,
    run_id: str,
) -> None:
    """安全审计日志：用户决策结果。"""
    logger.warning(
        "【安全审计】用户决策已处理",
        run_id=run_id,
        approved_count=len(approved_ids),
        denied_count=len(denied_calls),
    )
    for tc in denied_calls:
        logger.warning(
            "【安全审计】操作已拒绝",
            run_id=run_id,
            tool_call_id=tc["id"],
            tool=tc["name"],
        )
