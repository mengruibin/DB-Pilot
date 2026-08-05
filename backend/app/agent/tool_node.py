"""
SafeToolNode（任务 5：安全护栏 + 连接注入 + 结果脱敏）。

LangGraph 图节点函数，替代旧的自研 tools_node（graph.py 中 ~170 行手动 for 循环）。
采用 LangChain 标准消息模式：
  - 从 state["messages"] 最后一条 AIMessage 读取 tool_calls
  - 返回 ToolMessage 列表（LangGraph 自动追加到 messages）

功能：
  1. 连接配置注入 — 将 conn_config（host/port/user/password 等）合并到工具参数
  2. 安全护栏 — SQL 审计、只读检查、连接限额
  3. 工具执行 — 通过 TOOL_REGISTRY 查找并调用 tool_fn.ainvoke()
  4. 结果脱敏 — 检测并掩码敏感列
  5. SSE 事件生成 — tool_result / sql 事件写入 sse_events
"""

from __future__ import annotations

import asyncio
import json as _json
import time
import types
from collections.abc import Mapping
from typing import Any, get_origin, get_args

import structlog
from langchain_core.messages import AIMessage, ToolMessage

from app.agent.safety import (
    RowEstimationCheck,
    SQLAuditCheck,
    run_safety_checks,
)
from app.agent.state import AgentState
from app.config import settings

logger = structlog.get_logger(__name__)

def _build_row_estimation_block_advisory(
    block_count: int,
    safety_reason: str,
    sql_text: str,
) -> str:
    """构造连续被 RowEstimationCheck 拦截时的强提示消息。

    当同一查询连续 >= 3 次被 EXPLAIN 安全评估拦截时，
    告知 LLM 这不是 SQL 写法问题而是数据量问题，应立即停止改写并告知用户。

    Args:
        block_count: 当前连续拦截次数（>= 3）。
        safety_reason: RowEstimationCheck 返回的拦截原因。
        sql_text: 被拦截的 SQL 语句。

    Returns:
        给 LLM 的 ToolMessage content 字符串。
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


# 每轮 ReAct 迭代中最大并行工具数（从配置读取，默认 5）
_MAX_CONCURRENT_TOOLS = max(1, settings.AGENT_MAX_CONCURRENT_TOOLS)


def _resolve_checks(tool_fn: Any) -> list:
    """根据工具元数据解析需要执行的安全检查列表。

    工具通过 @tool(extras={...}) 声明安全需求，
    此函数将其映射为具体的 SafetyCheck 实例列表。
    未声明需求的工具返回空列表（跳过安全检查）。

    Args:
        tool_fn: BaseTool 实例（from TOOL_REGISTRY）。

    Returns:
        需要执行的 SafetyCheck 列表（可能为空）。
    """
    extras = getattr(tool_fn, "extras", None) or {}
    checks: list = []
    if extras.get("needs_sql_audit"):
        checks.append(SQLAuditCheck())
    if extras.get("needs_row_estimation"):
        checks.append(RowEstimationCheck())
        logger.debug(
            "安全检查链已注册 RowEstimationCheck",
            tool_name=tool_fn.name,
        )
    return checks


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

    Args:
        tool_fn: BaseTool 实例，用于读取 args_schema。
        tool_args: 即将传给 ainvoke 的参数 dict（就地修改）。
    """
    schema = getattr(tool_fn, "args_schema", None)
    if schema is None:
        return
    for field_name, field in schema.model_fields.items():
        if field_name not in tool_args:
            continue
        val = tool_args[field_name]
        if not isinstance(val, str):
            continue
        if not _is_list_type(field.annotation):
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


async def _run_one_tool(
    tc: Mapping[str, Any],
    conn_config: dict[str, Any],
    run_id: str,
    iteration: int,
    semaphore: asyncio.Semaphore,
    consecutive_blocks: int = 0,
) -> dict:
    """执行单个工具调用的完整生命周期（连接注入 → 安全护栏 → 执行 → 脱敏 → 消息构造）。

    供 safe_tools_node 内部并行调度使用。

    Args:
        tc: LangChain ToolCall 字典，含 id/name/args。
        conn_config: 数据库连接配置字典。
        run_id: 当前 Agent 运行 ID。
        iteration: 当前 ReAct 迭代轮次。
        semaphore: 并发控制信号量。

    Returns:
        {"tool_message": ToolMessage, "sse_events": list[dict]}
    """
    async with semaphore:
        from app.agent.tools.registry import TOOL_REGISTRY  # noqa: I001

        tool_name = tc["name"]
        tool_args: dict[str, Any] = dict(tc["args"])
        local_sse: list[dict] = []
        tool_start = time.monotonic()

        # ── 1. 连接配置注入 ──
        for key in (
            "connection_id",
            "db_type",
            "host",
            "port",
            "database",
            "user",
            "password",
            "ssl_enabled",
            "ssl_ca_cert",
        ):
            if key in conn_config and key not in tool_args:
                tool_args[key] = conn_config[key]
        if "user_role" not in tool_args:
            tool_args["user_role"] = conn_config.get("user_role", "readonly")

        # ── 2. 查找工具（提前到安全检查之前，用于解析安全元数据） ──
        tool_fn = TOOL_REGISTRY.get(tool_name)
        if tool_fn is None:
            logger.error(
                "工具未注册",
                run_id=run_id,
                tool=tool_name,
                available=list(TOOL_REGISTRY.keys()),
            )
            return {
                "tool_message": ToolMessage(
                    content=f"工具 '{tool_name}' 未注册，请联系管理员",
                    tool_call_id=tc["id"],
                    name=tool_name,
                ),
                "sse_events": [
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
                "row_estimation_blocked": False,
            }

        # ── 3. 根据元数据解析安全检查列表 ──
        applicable_checks = _resolve_checks(tool_fn)

        # ── 4. 安全护栏检查 ──
        safety_result = await run_safety_checks(
            tool_name,
            tool_args,
            conn_config,
            checks=applicable_checks,
        )
        if safety_result.blocked:
            # 检测是否为 RowEstimationCheck 拦截（通过拦截原因特征字符串判断）
            is_re_blocked = (
                safety_result.reason is not None
                and "[EXPLAIN 安全评估]" in safety_result.reason
            )
            logger.warning(
                "工具被安全护栏拦截",
                run_id=run_id,
                tool=tool_name,
                reason=safety_result.reason,
                consecutive_blocks=consecutive_blocks,
                is_row_estimation_block=is_re_blocked,
            )

            # 确定返回给 LLM 的拦截消息内容
            if is_re_blocked and consecutive_blocks >= 2:
                # 第 3 次及以上连续拦截 → 返回强提示消息
                # is_re_blocked 为 True 已确保 reason 不为 None，assert 消除类型检查器警告
                assert safety_result.reason is not None
                sql_text = tool_args.get("sql", "")
                msg_content = _build_row_estimation_block_advisory(
                    block_count=consecutive_blocks + 1,
                    safety_reason=safety_result.reason,
                    sql_text=sql_text,
                )
            else:
                msg_content = f"操作被安全策略拦截: {safety_result.reason}"

            return {
                "tool_message": ToolMessage(
                    content=msg_content,
                    tool_call_id=tc["id"],
                    name=tool_name,
                ),
                "sse_events": [
                    {
                        "type": "tool_result",
                        "tool": tool_name,
                        "summary": f"拦截: {safety_result.reason}",
                        "tool_call_id": tc["id"],
                        "agent_run_id": run_id,
                        "iteration": iteration,
                        "safety_checks_passed": False,
                    }
                ],
                "row_estimation_blocked": is_re_blocked,
            }

        safety_warnings = list(safety_result.warnings)
        if safety_warnings:
            logger.info(
                "安全护栏性能提示已记录（非阻断）",
                run_id=run_id,
                tool=tool_name,
                warning_count=len(safety_warnings),
            )

        # ── 5. 执行工具 ──
        # 某些 LLM(如 DeepSeek)可能把数组参数串化成 JSON 字符串，
        # 在 ainvoke 的 Pydantic 校验前做类型修正
        _coerce_tool_args(tool_fn, tool_args)
        try:
            result = await tool_fn.ainvoke(tool_args)
            # ── 5.5. LLM 上下文窗口保护：截断大结果集 ──
            # 兼容无 rows 键的结果（explain_output / items / categories 等）：
            # explain 类用更小阈值（JSON 计划高度重复，LLM 只需看节点形状），
            # 其余用默认 40K 上限（context-compression-plan 第二层配套）
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
                "tool_message": ToolMessage(
                    content=f"工具执行失败: {str(exc)[:200]}",
                    tool_call_id=tc["id"],
                    name=tool_name,
                ),
                "sse_events": [
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
                "row_estimation_blocked": False,
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

        # ── 6. 构造 ToolMessage（含安全护栏的非阻断警告） ──
        tool_content = _json.dumps(result, ensure_ascii=False, default=str)
        if safety_warnings:
            tool_content = "[性能提示] " + " | ".join(safety_warnings) + "\n\n" + tool_content

        # ── 7. 生成 SSE 事件 ──
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
            if (
                isinstance(result, dict)
                and result.get("is_readonly") is True
                and isinstance(result.get("columns"), list)
            ):
                export_meta = {
                    "export_sql": tool_args.get("sql", ""),
                    # 截断后 total_rows 保留原始总数，_original_total_rows 兼容兜底
                    "total_rows": result.get(
                        "_original_total_rows", result.get("total_rows", 0)
                    ),
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

        return {
            "tool_message": ToolMessage(
                content=tool_content,
                tool_call_id=tc["id"],
                name=tool_name,
            ),
            "sse_events": local_sse,
            "row_estimation_blocked": False,
        }


async def safe_tools_node(state: AgentState) -> dict[str, Any]:
    """标准 LangGraph 节点：安全执行 AIMessage 中的工具调用（并行调度）。

    从 state["messages"] 中提取最后一条 AIMessage.tool_calls，
    并发执行安全检查、连接配置注入、工具调用、结果脱敏。
    单个工具异常不会影响其他工具的执行。

    Args:
        state: 当前 AgentState，需含 messages, conn_config。

    Returns:
        包含 messages（ToolMessage 列表）和 sse_events 的更新 dict。
    """
    run_id = state.get("run_id", "")
    conn_config: dict[str, Any] = state.get("conn_config") or {}
    iteration = len(state.get("trace_iterations", []))
    consecutive_blocks = state.get("consecutive_blocks", 0)
    sse_events = list(state.get("sse_events", []))

    # ── 从消息列表中提取 tool_calls ──
    all_messages = state.get("messages", [])
    last_msg = all_messages[-1] if all_messages else None

    if not isinstance(last_msg, AIMessage) or not last_msg.tool_calls:
        logger.warning(
            "safe_tools_node 被调用但无 AIMessage.tool_calls",
            run_id=run_id,
            last_msg_type=type(last_msg).__name__ if last_msg else None,
        )
        return {"sse_events": sse_events}

    tool_calls = last_msg.tool_calls

    logger.info(
        "图节点执行: tools（SafeToolNode 并行模式）",
        run_id=run_id,
        iteration=iteration,
        tools_count=len(tool_calls),
        tools=[tc["name"] for tc in tool_calls],
    )

    # 并发执行所有工具
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_TOOLS)
    results = await asyncio.gather(
        *[_run_one_tool(tc, conn_config, run_id, iteration, semaphore, consecutive_blocks) for tc in tool_calls],
        return_exceptions=True,
    )

    # 按原始 tool_calls 顺序组装结果（gather 保持输入顺序）
    tool_messages: list[ToolMessage] = []
    for i, r in enumerate(results):
        if isinstance(r, BaseException):
            # 极少情况：gather 自身的异常（超时等），非工具抛出的异常
            tc = tool_calls[i]
            logger.error(
                "工具并行执行异常",
                run_id=run_id,
                tool=tc["name"],
                error=str(r)[:300],
            )
            tool_messages.append(
                ToolMessage(
                    content=f"工具执行异常: {str(r)[:200]}",
                    tool_call_id=tc["id"],
                    name=tc["name"],
                )
            )
            sse_events.append(
                {
                    "type": "tool_result",
                    "tool": tc["name"],
                    "summary": f"执行异常: {str(r)[:100]}",
                    "tool_call_id": tc["id"],
                    "agent_run_id": run_id,
                    "iteration": iteration,
                    "safety_checks_passed": True,
                }
            )
        else:
            tool_messages.append(r["tool_message"])
            sse_events.extend(r["sse_events"])

    # ── 统计本轮 RowEstimationCheck 拦截数，更新 consecutive_blocks ──
    round_blocked_count = sum(
        1 for r in results
        if not isinstance(r, BaseException) and r.get("row_estimation_blocked")
    )
    if round_blocked_count > 0:
        new_consecutive_blocks = consecutive_blocks + 1
    else:
        new_consecutive_blocks = 0  # 本轮无 RE 拦截，重置计数器

    # ── 连续拦截 >= 5 次：强制终止，不再让 LLM 继续改写 ──
    if new_consecutive_blocks >= 5:
        logger.warning(
            "连续 RowEstimationCheck 拦截已达上限，强制终止 Agent",
            run_id=run_id,
            consecutive_blocks=new_consecutive_blocks,
        )
        return {
            "messages": tool_messages,
            "sse_events": sse_events,
            "consecutive_blocks": new_consecutive_blocks,
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
            "sse_events": sse_events + [{
                "type": "error",
                "error_code": "CONSECUTIVE_BLOCKS_EXCEEDED",
                "user_message": (
                    f"连续 {new_consecutive_blocks} 次被 EXPLAIN 安全评估拦截，"
                    "Agent 已自动终止"
                ),
                "severity": "warning",
            }],
        }

    return {
        "messages": tool_messages,
        "sse_events": sse_events,
        "consecutive_blocks": new_consecutive_blocks,
    }
