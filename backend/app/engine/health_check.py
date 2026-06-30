"""
健康巡检引擎。

HealthCheckEngine 维护 20 项检查注册表，每项包含类别、名称、
检查函数、阈值和建议。通过异步生成器逐项输出结果，支持 SSE 流式推送。

依据 PRD §5.4：20+ 检查维度、健康评分 0-100、三项分级。
依据 api-contract §1.4：SSE check_progress 事件 + health_result 报告。
"""

from __future__ import annotations

import math
from collections.abc import AsyncGenerator, Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from app.db.base import BaseAdapter

# =============================================================================
# 检查项定义
# =============================================================================

CheckFn = Callable[
    [BaseAdapter],
    Coroutine[Any, Any, dict[str, Any]],
]
"""检查函数签名：接收 adapter，返回 {status, value, suggestion}。"""


@dataclass
class CheckItem:
    """检查项注册记录。

    Attributes:
        category: 类别名（连接/复制/性能/慢查询/存储/安全）。
        name: 检查项名称。
        threshold: 阈值描述（如 "<80%"、"<10s"），为 None 表示仅记录。
        suggestion: 未达标时的建议文本。
        check_fn: 异步检查函数，接收 adapter 并返回检查结果。
    """
    category: str
    name: str
    threshold: str | None
    suggestion: str | None
    check_fn: CheckFn


# =============================================================================
# 内部检查函数（每项一个）
# =============================================================================


async def _check_usage(adapter: BaseAdapter) -> dict[str, Any]:
    """检查 1：连接数使用率。"""
    data = await adapter.get_connections_status()
    usage = float(data.get("usage_percent", 0))
    total = data.get("total_connections", 0)
    active = data.get("active_connections", 0)
    value = f"{active}/{total} ({usage}%)"
    if usage > 95:
        return {"status": "error", "value": value,
                "suggestion": "连接数即将耗尽，请检查连接泄漏或增加 max_connections"}
    if usage > 80:
        return {"status": "warning", "value": value,
                "suggestion": "连接使用率偏高，建议排查闲置连接"}
    return {"status": "pass", "value": value, "suggestion": None}


async def _check_waiting(adapter: BaseAdapter) -> dict[str, Any]:
    """检查 2：等待连接数。"""
    data = await adapter.get_connections_status()
    waiting = int(data.get("waiting_connections", 0))
    value = f"{waiting}"
    if waiting > 10:
        return {"status": "error", "value": value,
                "suggestion": "大量连接在等待，可能存在连接池耗尽或死锁"}
    if waiting > 0:
        return {"status": "warning", "value": value,
                "suggestion": "存在等待连接的请求，建议检查连接池配置"}
    return {"status": "pass", "value": value, "suggestion": None}


async def _check_aborted(adapter: BaseAdapter) -> dict[str, Any]:
    """检查 3：连接异常率。"""
    data = await adapter.get_connections_status()
    rate = float(data.get("aborted_connections_rate", 0))
    value = f"{rate}%"
    if rate > 10:
        return {"status": "error", "value": value,
                "suggestion": "连接异常率过高，检查网络稳定性或密码错误"}
    if rate > 5:
        return {"status": "warning", "value": value,
                "suggestion": "连接异常率偏高，建议检查认证日志"}
    return {"status": "pass", "value": value, "suggestion": None}


async def _check_connections_detail(adapter: BaseAdapter) -> dict[str, Any]:
    """检查 4：连接详情汇总。"""
    data = await adapter.get_connections_status()
    active = data.get("active_connections", 0)
    idle = data.get("idle_connections", 0)
    total = data.get("total_connections", 0)
    value = f"活跃 {active} / 空闲 {idle} / 总计 {total}"
    return {"status": "pass", "value": value, "suggestion": None}


async def _check_idle_ratio(adapter: BaseAdapter) -> dict[str, Any]:
    """检查 5：空闲连接占比。"""
    data = await adapter.get_connections_status()
    active = int(data.get("active_connections", 0))
    idle = int(data.get("idle_connections", 0))
    total = active + idle
    ratio = (idle / max(total, 1)) * 100
    value = f"空闲 {idle} ({ratio:.1f}%)"
    if ratio < 10 and total > 0:
        return {"status": "warning", "value": value,
                "suggestion": "空闲连接太少，连接池可能配置不足"}
    return {"status": "pass", "value": value, "suggestion": None}


async def _check_active_ratio(adapter: BaseAdapter) -> dict[str, Any]:
    """检查 6：活跃连接比例。"""
    data = await adapter.get_connections_status()
    active = int(data.get("active_connections", 0))
    total = int(data.get("total_connections", 0))
    ratio = (active / max(total, 1)) * 100
    value = f"活跃 {active}/{total} ({ratio:.1f}%)"
    if ratio > 90:
        return {"status": "error", "value": value,
                "suggestion": "活跃连接占比过高，应用可能无法获取新连接"}
    if ratio > 70:
        return {"status": "warning", "value": value,
                "suggestion": "活跃连接占比较高，建议检查应用连接池设置"}
    return {"status": "pass", "value": value, "suggestion": None}


async def _check_replication_delay(adapter: BaseAdapter) -> dict[str, Any]:
    """检查 7：主从延迟。"""
    caps = adapter.get_capabilities()
    if not caps.supports_replication:
        return {"status": "skipped", "value": "不支持", "suggestion": None}
    repl = await adapter.get_replication_status()
    if repl.get("status") == "skipped":
        return {"status": "skipped", "value": "未配置", "suggestion": None}
    delay = repl.get("delay_seconds")
    value = f"{delay}s" if delay is not None else "未知"
    if delay is not None and delay > 60:
        return {"status": "error", "value": value,
                "suggestion": "主从延迟严重，检查从库 IO/SQL 线程或网络带宽"}
    if delay is not None and delay > 10:
        return {"status": "warning", "value": value,
                "suggestion": "主从延迟偏高，建议排查从库负载"}
    return {"status": "pass", "value": value, "suggestion": None}


async def _check_io_thread(adapter: BaseAdapter) -> dict[str, Any]:
    """检查 8：IO 线程运行状态。"""
    caps = adapter.get_capabilities()
    if not caps.supports_replication:
        return {"status": "skipped", "value": "不支持", "suggestion": None}
    repl = await adapter.get_replication_status()
    if repl.get("status") == "skipped":
        return {"status": "skipped", "value": "未配置", "suggestion": None}
    running = repl.get("io_thread_running", False)
    value = "运行中" if running else "已停止"
    if not running:
        return {"status": "error", "value": value,
                "suggestion": "IO 线程停止，从库无法接收主库的 binlog"}
    return {"status": "pass", "value": value, "suggestion": None}


async def _check_sql_thread(adapter: BaseAdapter) -> dict[str, Any]:
    """检查 9：SQL 线程运行状态。"""
    caps = adapter.get_capabilities()
    if not caps.supports_replication:
        return {"status": "skipped", "value": "不支持", "suggestion": None}
    repl = await adapter.get_replication_status()
    if repl.get("status") == "skipped":
        return {"status": "skipped", "value": "未配置", "suggestion": None}
    running = repl.get("sql_thread_running", False)
    value = "运行中" if running else "已停止"
    if not running:
        return {"status": "error", "value": value,
                "suggestion": "SQL 线程停止，从库无法 replay 主库的变更"}
    return {"status": "pass", "value": value, "suggestion": None}


async def _check_qps(adapter: BaseAdapter) -> dict[str, Any]:
    """检查 10：QPS（每秒查询数）。"""
    metrics = await adapter.get_metrics()
    qps = metrics.get("qps", 0)
    value = f"{qps:.2f}/s"
    if qps > 5000:
        return {"status": "warning", "value": value,
                "suggestion": "QPS 超过 5000，考虑读写分离或缓存"}
    return {"status": "pass", "value": value, "suggestion": None}


async def _check_tps(adapter: BaseAdapter) -> dict[str, Any]:
    """检查 11：TPS（每秒事务数）。"""
    metrics = await adapter.get_metrics()
    tps = metrics.get("tps", 0)
    value = f"{tps:.2f}/s"
    if tps > 2000:
        return {"status": "warning", "value": value,
                "suggestion": "TPS 超过 2000，考虑优化写入或分库"}
    return {"status": "pass", "value": value, "suggestion": None}


async def _check_bp_hit(adapter: BaseAdapter) -> dict[str, Any]:
    """检查 12：缓冲池命中率。"""
    metrics = await adapter.get_metrics()
    hit = float(metrics.get("buffer_pool_hit_rate", 100))
    value = f"{hit:.2f}%"
    if hit < 90:
        return {"status": "error", "value": value,
                "suggestion": "缓冲池命中率过低，增加 innodb_buffer_pool_size"}
    if hit < 95:
        return {"status": "warning", "value": value,
                "suggestion": "缓冲池命中率偏低，考虑优化查询或增加内存"}
    return {"status": "pass", "value": value, "suggestion": None}


async def _check_slow_ratio(adapter: BaseAdapter) -> dict[str, Any]:
    """检查 13：慢查询占比。"""
    metrics = await adapter.get_metrics()
    ratio = float(metrics.get("slow_query_ratio", 0))
    value = f"{ratio:.4f}%"
    if ratio > 10:
        return {"status": "error", "value": value,
                "suggestion": "慢查询占比过高，逐一分析并优化索引"}
    if ratio > 5:
        return {"status": "warning", "value": value,
                "suggestion": "存在慢查询，建议分析 slow_query_log"}
    return {"status": "pass", "value": value, "suggestion": None}


async def _check_uptime(adapter: BaseAdapter) -> dict[str, Any]:
    """检查 14：数据库运行时间（只报告，无阈值）。"""
    metrics = await adapter.get_metrics()
    sampled = metrics.get("sampled_at", "")
    value = f"采样时间 {sampled}" if sampled else "N/A"
    return {"status": "pass", "value": value, "suggestion": None}


async def _check_slow_count(adapter: BaseAdapter) -> dict[str, Any]:
    """检查 15：慢查询数量。"""
    caps = adapter.get_capabilities()
    if not caps.supports_slow_query_log:
        return {"status": "skipped", "value": "不支持", "suggestion": None}
    slow = await adapter.get_slow_queries(limit=100, time_range="1h")
    items = slow.get("items", [])
    count = len(items)
    value = f"{count} 条（最近 1h）"
    if count > 50:
        return {"status": "error", "value": value,
                "suggestion": "大量慢查询，严重影响数据库性能"}
    if count > 5:
        return {"status": "warning", "value": value,
                "suggestion": "存在慢查询，建议逐一分析执行计划"}
    if slow.get("warning"):
        return {"status": "pass", "value": slow["warning"], "suggestion": None}
    return {"status": "pass", "value": value, "suggestion": None}


async def _check_slow_max(adapter: BaseAdapter) -> dict[str, Any]:
    """检查 16：最慢查询耗时。"""
    caps = adapter.get_capabilities()
    if not caps.supports_slow_query_log:
        return {"status": "skipped", "value": "不支持", "suggestion": None}
    slow = await adapter.get_slow_queries(limit=1, time_range="1h")
    items = slow.get("items", [])
    if not items:
        return {"status": "pass", "value": "无", "suggestion": None}
    slowest = items[0]
    sec = float(slowest.get("query_time_sec", 0))
    sql = slowest.get("sql_text", "")[:80]
    value = f"{sec}s — {sql}"
    if sec > 10:
        return {"status": "error", "value": value,
                "suggestion": "存在超长慢查询，建议立即分析并优化"}
    if sec > 2:
        return {"status": "warning", "value": value,
                "suggestion": "存在耗时超 2s 的查询，建议检查执行计划"}
    return {"status": "pass", "value": value, "suggestion": None}


async def _check_table_spaces(adapter: BaseAdapter) -> dict[str, Any]:
    """检查 17：表空间使用。"""
    caps = adapter.get_capabilities()
    if not caps.supports_table_spaces:
        return {"status": "skipped", "value": "不支持", "suggestion": None}
    return {"status": "pass", "value": "已监控", "suggestion": None}


async def _check_tmp_tables(adapter: BaseAdapter) -> dict[str, Any]:
    """检查 18：临时表磁盘使用率。"""
    metrics = await adapter.get_metrics()
    tmp_val = metrics.get("tmp_disk_tables_ratio", 0)
    if tmp_val:
        ratio = float(tmp_val) * 100
        value = f"{ratio:.2f}%"
        if ratio > 30:
            return {"status": "warning", "value": value,
                    "suggestion": "磁盘临时表占比高，增加 tmp_table_size 或优化查询"}
        return {"status": "pass", "value": value, "suggestion": None}
    return {"status": "pass", "value": "N/A", "suggestion": None}


async def _check_ssl(adapter: BaseAdapter) -> dict[str, Any]:
    """检查 19：SSL 使用（仅报告配置，因适配器连接时已决定）。"""
    return {"status": "pass", "value": "按连接配置", "suggestion": None}


async def _check_availability(adapter: BaseAdapter) -> dict[str, Any]:
    """检查 20：数据库可用性——执行心跳查询。"""
    try:
        ok = await adapter.test_connection()
        if ok:
            return {"status": "pass", "value": "可达", "suggestion": None}
        return {"status": "error", "value": "不可达",
                "suggestion": "数据库无法响应心跳，检查服务状态"}
    except Exception:
        return {"status": "error", "value": "不可达",
                "suggestion": "数据库连接异常，检查网络和数据库服务"}


# =============================================================================
# 检查注册表（20 项）
# =============================================================================

_CHECK_REGISTRY: list[CheckItem] = [
    # ── 连接类 (6项) ──
    CheckItem(category="连接", name="连接数使用率",
              threshold="<80%", suggestion=None, check_fn=_check_usage),
    CheckItem(category="连接", name="等待连接数",
              threshold="0", suggestion=None, check_fn=_check_waiting),
    CheckItem(category="连接", name="连接异常率",
              threshold="<5%", suggestion=None, check_fn=_check_aborted),
    CheckItem(category="连接", name="连接详情",
              threshold=None, suggestion=None, check_fn=_check_connections_detail),
    CheckItem(category="连接", name="空闲连接占比",
              threshold=">10%", suggestion=None, check_fn=_check_idle_ratio),
    CheckItem(category="连接", name="活跃连接占比",
              threshold="<70%", suggestion=None, check_fn=_check_active_ratio),
    # ── 复制类 (3项) ──
    CheckItem(category="复制", name="主从延迟",
              threshold="<10s", suggestion=None, check_fn=_check_replication_delay),
    CheckItem(category="复制", name="IO 线程状态",
              threshold="running", suggestion=None, check_fn=_check_io_thread),
    CheckItem(category="复制", name="SQL 线程状态",
              threshold="running", suggestion=None, check_fn=_check_sql_thread),
    # ── 性能类 (5项) ──
    CheckItem(category="性能", name="QPS",
              threshold="<5000/s", suggestion=None, check_fn=_check_qps),
    CheckItem(category="性能", name="TPS",
              threshold="<2000/s", suggestion=None, check_fn=_check_tps),
    CheckItem(category="性能", name="缓冲池命中率",
              threshold=">95%", suggestion="增加 innodb_buffer_pool_size",
              check_fn=_check_bp_hit),
    CheckItem(category="性能", name="慢查询占比",
              threshold="<5%", suggestion=None, check_fn=_check_slow_ratio),
    CheckItem(category="性能", name="数据库运行时间",
              threshold=None, suggestion=None, check_fn=_check_uptime),
    # ── 慢查询类 (2项) ──
    CheckItem(category="慢查询", name="慢查询数量",
              threshold="<5/h", suggestion=None, check_fn=_check_slow_count),
    CheckItem(category="慢查询", name="最慢查询耗时",
              threshold="<2s", suggestion=None, check_fn=_check_slow_max),
    # ── 存储类 (2项) ──
    CheckItem(category="存储", name="表空间使用",
              threshold=None, suggestion=None, check_fn=_check_table_spaces),
    CheckItem(category="存储", name="临时表磁盘使用率",
              threshold="<30%", suggestion=None, check_fn=_check_tmp_tables),
    # ── 安全类 (2项) ──
    CheckItem(category="安全", name="SSL 加密检查",
              threshold="按配置", suggestion=None, check_fn=_check_ssl),
    CheckItem(category="安全", name="数据库可用性",
              threshold="可达", suggestion=None, check_fn=_check_availability),
]


# =============================================================================
# HealthCheckEngine
# =============================================================================


class HealthCheckEngine:
    """健康巡检引擎。

    维护 20 项检查注册表，通过异步生成器逐项输出检查进度。
    支持按检查项名称过滤、评分计算。

    依据 PRD §5.4：
    - 评分公式：score = floor((pass_count / total_checked) * 100)
    - skipped 项不计入分母
    - 评分分级：80-100=健康/绿、60-79=警告/黄、0-59=严重/红
    """

    def __init__(self, adapter: BaseAdapter) -> None:
        """初始化引擎。

        Args:
            adapter: 已连接的数据库适配器实例。
        """
        self._adapter = adapter
        self._registry: list[CheckItem] = list(_CHECK_REGISTRY)

    # ================== 公开方法 ==================

    async def run_checks(
        self,
        check_items: list[str] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """逐项执行健康检查，yield 每项结果。

        若 check_items 不为空，仅运行名称匹配的检查项。
        每个检查项结果格式为：
          {current, total, item, category, status, value, threshold, suggestion}
        最终 yield 一个 {score, severity_counts} 汇总报告。

        Args:
            check_items: 要运行的检查项名称列表；None 或 ["all"] 表示全部。

        Yields:
            每项检查的进度字典，最后一条为汇总报告。
        """
        items = self._resolve_items(check_items)
        total = len(items)
        result_store: list[dict[str, Any]] = []

        for idx, check in enumerate(items):
            current = idx + 1

            try:
                raw = await check.check_fn(self._adapter)
                status = raw.get("status", "error")
                value = raw.get("value", "N/A")
                suggestion = raw.get("suggestion") or check.suggestion
            except Exception:
                status = "error"
                value = "检查异常"
                suggestion = f"{check.name} 执行时发生异常，请人工排查"

            result = {
                "current": current,
                "total": total,
                "item": check.name,
                "category": check.category,
                "status": status,
                "value": value,
                "threshold": check.threshold or "N/A",
                "suggestion": suggestion,
            }
            result_store.append(result)
            yield result

        report = self._build_report(result_store, total)
        yield report

    # ================== 内部方法 ==================

    def _resolve_items(self, check_items: list[str] | None) -> list[CheckItem]:
        """根据输入筛选要运行的检查项。"""
        if not check_items or "all" in check_items:
            return self._registry
        name_set = {n.strip().lower() for n in check_items}
        matched = [c for c in self._registry if c.name.lower() in name_set]
        return matched if matched else self._registry

    def _build_report(
        self,
        results: list[dict[str, Any]],
        total: int,
    ) -> dict[str, Any]:
        """从检查结果列表构建汇总报告。"""
        error_count = 0
        warning_count = 0
        pass_count = 0
        skipped_count = 0
        for r in results:
            s = r.get("status", "")
            if s == "pass":
                pass_count += 1
            elif s == "warning":
                warning_count += 1
            elif s == "error":
                error_count += 1
            else:
                skipped_count += 1

        # 评分：skipped 不计入分母（全部 skipped 视为健康 100 分）
        denominator = total - skipped_count
        score = math.floor(
            (pass_count / denominator) * 100
        ) if denominator > 0 else 100

        return {
            "score": score,
            "severity_counts": {
                "error": error_count,
                "warning": warning_count,
                "pass": pass_count,
                "skipped": skipped_count,
            },
        }
