"""
数据库适配器抽象基类与能力声明。

定义 BaseAdapter(ABC) 作为所有数据库适配器的通用接口，
以及 AdapterCapabilities 数据类声明各适配器支持的能力。

依据 PRD §6.2 BaseAdapter 设计、AGENTS.md §目录与命名规范（`db/base.py`）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp

from app.models.schemas import ConnectionCreateRequest

# =============================================================================
# SQL 执行超时保护（统一策略，PRD §8.1 Layer 4：执行保护默认 30s）
# 三个数据库适配器共用同一时长，保证行为一致：
#   - 服务器端超时（首选）：让数据库主动终止超时语句，真正回收服务器资源
#       * PostgreSQL : SET statement_timeout（毫秒）
#       * MySQL ≥5.7.8 : SET SESSION MAX_EXECUTION_TIME（毫秒，仅 SELECT）
#       * MariaDB     : SET SESSION max_statement_time（秒，全部语句）
#       * Oracle      : AsyncConnection.call_timeout（毫秒，中断在途语句，连接仍可用）
#   - 客户端兜底（服务器端机制缺失/不生效时）：asyncpg command_timeout /
#     asyncio.wait_for，防止应用无限等待
# 时长依据（30s）：本应用正常业务查询（元数据/诊断/健康巡检/EXPLAIN）耗时均远
# 低于 30s；30s 主要用于拦截 LLM 生成的失控查询（缺 WHERE 全表扫描、笛卡尔积、
# 错误执行计划等）。超时后由 Agent 提示 LLM 改写查询，而非长时间占用数据库
# CPU/IO/锁资源。与并行工具执行（默认 5 并发）配合，最坏情况为 5 条语句各占用
# 30s，资源占用有界。
# =============================================================================
STATEMENT_EXEC_TIMEOUT_SEC = 30
"""SQL 语句执行超时（秒），服务器端优先由数据库主动终止。"""

STATEMENT_EXEC_TIMEOUT_MS = 30_000
"""SQL 语句执行超时（毫秒），供按毫秒计时的机制使用（statement_timeout /
MAX_EXECUTION_TIME / call_timeout）。"""

# =============================================================================
# 结果集行数上限（LIMIT 下推）— 进程内存防护
# 三层防护中的"DB 端限行"：truncate_result_for_llm 只保护 LLM 上下文窗口，
# 不保护 Python 进程内存——适配器 fetchall 会把 DB 返回的全部行物化进堆。
# 失控查询（缺 WHERE 全表扫描）× 并发请求 → 单进程内存可能被打爆。
# 对只读 SELECT 自动追加 LIMIT max+1（Oracle 12c+ 用 FETCH FIRST），
# DB 只传输有限行数，单次查询内存峰值有界（≤ max+1 行 × 行宽）。
# =============================================================================
STATEMENT_MAX_RESULT_ROWS = 1000
"""SQL 结果集行数上限（DB 端 LIMIT 下推，进程内存防护）。

阈值依据：truncate_result_for_llm 上限 100 行 / 40K 字符，1000 行留出
字符数减半削减的空间；LIMIT max+1 的 +1 用于探测触顶（返回 max+1 行
说明实际结果更多）。与超时常量一致 hardcode，不依赖 .env 配置。
"""


def apply_read_limit(
    sql: str,
    dialect: str,
    max_rows: int = STATEMENT_MAX_RESULT_ROWS,
) -> tuple[str, bool]:
    """只读 SELECT 结果集 LIMIT 下推：让 DB 只返回有限行数（进程内存防护）。

    对顶层 SELECT / WITH(...)SELECT / UNION 追加 LIMIT max_rows+1；
    顶层已有 ≤ max_rows 的数值 LIMIT 时原样返回（已足够有界）；
    非 SELECT / 解析失败 / 占位符 LIMIT 等无法安全改写时一律原样返回
    （fail-safe，不阻塞业务，体积级截断仍由 truncate_result_for_llm 兜底）。

    Args:
        sql: 原始 SQL（只读查询）。
        dialect: sqlglot 方言（"mysql" / "postgres" / "oracle"）。
        max_rows: 行数上限，DB 实际返回 ≤ max_rows+1 行。

    Returns:
        (要执行的 SQL, 是否改写)。改写失败返回 (原 SQL, False)。
    """
    if not sql or not sql.strip():
        return sql, False
    try:
        # 只解析单条语句（多语句 sqlglot 抛错 → fail-safe 原样放行；
        # 上游 sql_audit 已拦截多语句，这里是纵深防御）
        parsed = sqlglot.parse_one(sql.strip().rstrip(";"), read=dialect)
    except Exception:
        return sql, False

    # 定位顶层 SELECT：Select 本体 / With(...) 包裹的 SELECT / UNION
    if isinstance(parsed, exp.Select):
        select = parsed
    elif isinstance(parsed, exp.With):
        inner = parsed.expression
        select = inner if isinstance(inner, (exp.Select, exp.Union)) else None
    elif isinstance(parsed, exp.Union):
        select = parsed
    else:
        # 非只读查询（INSERT/UPDATE/SHOW/SET/EXPLAIN 等）→ 不动
        return sql, False
    if select is None:
        return sql, False

    # 顶层已有 LIMIT：数值 ≤ max → 已足够有界，原样返回；
    # 数值 > max → 收敛到 max+1；占位符/表达式 → 无法安全判断，原样返回
    existing = select.args.get("limit")
    if existing is not None:
        count = existing.expression
        if isinstance(count, exp.Literal) and count.is_number:
            limit_value = int(count.name)
            if limit_value <= max_rows:
                return sql, False
            # 收敛超大 LIMIT 到 max+1（LLM 上下文本就容纳不下更大结果）
            return select.limit(max_rows + 1).sql(dialect=dialect), True
        return sql, False

    # 无 LIMIT → 追加 max+1。注意 sqlglot .limit() 默认 copy=True 返回新
    # 表达式，必须直接链式调用 .sql()（原地调用不会生效）
    return select.limit(max_rows + 1).sql(dialect=dialect), True


# =============================================================================
# AdapterCapabilities：适配器能力声明
# 各适配器返回不同的能力组合（如 MySQL 支持复制但 Oracle 不支持）。
# 默认全部 False，由具体适配器按需覆盖。
# =============================================================================


@dataclass
class AdapterCapabilities:
    """适配器能力声明。

    每个布尔字段表示适配器是否支持对应功能。
    默认全部 False，由具体适配器实现类覆盖。
    """

    supports_explain: bool = False
    """是否支持 EXPLAIN 执行计划分析"""
    supports_slow_query_log: bool = False
    """是否支持慢查询日志读取"""
    supports_replication: bool = False
    """是否支持主从复制状态检测"""
    supports_table_spaces: bool = False
    """是否支持表空间信息查询"""
    supports_json_type: bool = False
    """是否支持 JSON 数据类型"""
    supports_lock_analysis: bool = False
    """是否支持细粒度锁信息查询（表名、锁模式、行键值）"""
    supports_kill_transaction: bool = False
    """是否支持主动终止连接（KILL CONNECTION / pg_terminate_backend）"""


# =============================================================================
# BaseAdapter：所有数据库适配器的抽象基类
# 每个方法对应一个 PRD §6.2 定义的能力。
# 全部为 async def（AGENTS.md §技术栈约束：所有 DB I/O 必须原生异步）。
# =============================================================================


class BaseAdapter(ABC):
    """数据库适配器抽象基类。

    所有目标数据库（MySQL、PostgreSQL、Oracle）的适配器必须继承此类
    并实现所有 @abstractmethod 方法。参数签名与 PRD §6.2 一致。

    适配器层不 import 目标数据库驱动——使用懒加载（AGENTS.md §数据库驱动）。
    """

    # ================== 连接管理 ==================

    @abstractmethod
    async def connect(
        self,
        config: ConnectionCreateRequest,
        user_role: str = "readonly",
    ) -> bool:
        """建立到目标数据库的连接。

        Args:
            config: 包含密码在内的完整连接配置。
            user_role: 用户角色（readonly / admin），
                用于控制是否设置只读事务。仅 MySQL 适配器按角色动态处理，
                PostgreSQL/Oracle 默认安全兜底为 readonly。

        Returns:
            True 表示连接成功。

        Raises:
            ConnectionError: 连接失败，异常消息仅包含 host:port（不暴露密码）。
        """

    @abstractmethod
    async def disconnect(self) -> None:
        """断开与目标数据库的连接，释放连接池资源。"""

    @abstractmethod
    async def test_connection(self) -> bool:
        """测试当前连接是否可用（发送心跳查询）。

        Returns:
            True 表示连接正常。
        """

    @abstractmethod
    async def execute(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行只读 SQL 查询。

        Args:
            sql: 要执行的 SQL 语句。
            params: 参数化查询的参数字典（防止 SQL 注入）。

        Returns:
            {"columns": [...], "rows": [...], "execution_time_ms": int,
             "is_readonly": True, "audit_status": "passed"}

        Raises:
            ValueError: SQL 语法错误或参数不匹配。
            TimeoutError: 查询超时（>max_execution_ms）。
        """

    @abstractmethod
    async def stream_query(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        batch_size: int = 2000,
    ) -> Any:
        """流式执行只读 SQL，按批产出 (columns, rows_batch)。

        v1 仅 MySQL 实现；PostgreSQL/Oracle 抛 NotImplementedError（导出端点按
        db_type 前置拦截）。调用方负责 connect/disconnect。

        Args:
            sql: 要执行的只读 SQL 语句。
            params: 参数化查询的参数字典（防止 SQL 注入）。
            batch_size: 每批拉取行数。

        Yields:
            (columns, rows_batch)：columns 为列名列表，rows_batch 为当前批的行列表。
            无结果集时无产出；空结果集产出一次 (columns, []) 以便写表头。
        """

    # ================== 元数据 ==================

    @abstractmethod
    async def get_databases(self) -> list[str]:
        """获取目标实例上的数据库/模式列表。

        Returns:
            数据库名列表。
        """

    @abstractmethod
    async def get_tables(self, database: str) -> list[dict[str, Any]]:
        """获取指定数据库中的表列表。

        Args:
            database: 数据库名。

        Returns:
            [{database, table_name, comment, row_count_estimate}, ...]。
        """

    @abstractmethod
    async def get_columns(
        self,
        database: str,
        table: str,
    ) -> list[dict[str, Any]]:
        """获取指定表的列信息。

        Args:
            database: 数据库名。
            table: 表名。

        Returns:
            [{name, type, nullable, is_primary, comment}, ...]。
        """

    @abstractmethod
    async def get_indexes(
        self,
        database: str,
        table: str,
    ) -> list[dict[str, Any]]:
        """获取指定表的索引信息。

        Args:
            database: 数据库名。
            table: 表名。

        Returns:
            [{name, columns, is_unique, type}, ...]。
        """

    # ================== 诊断 ==================

    @abstractmethod
    async def get_slow_queries(
        self,
        limit: int = 20,
        time_range: str = "1h",
        include_explain: bool = False,
    ) -> dict[str, Any]:
        """获取慢查询列表。

        Args:
            limit: 最大返回条数（默认 20，上限 100）。
            time_range: 时间范围（1h/6h/24h/7d）。
            include_explain: 是否自动对慢查询执行 EXPLAIN（最多前 5 条）。

        Returns:
            {"items": [...], "total": int, "slow_log_enabled": bool,
             "fallback_used": bool, "warning": str | None}。
            若慢查询日志未启用，返回 {"items": [], "warning": "..."}（不崩溃）。
        """

    @abstractmethod
    async def explain(self, sql: str) -> dict[str, Any]:
        """获取 SQL 执行计划。

        Args:
            sql: 要分析的 SQL 语句。

        Returns:
            包含执行计划输出的字典（不同数据库格式不同）。
        """

    @abstractmethod
    async def get_connections_status(self) -> dict[str, Any]:
        """获取目标数据库当前连接状态。

        Returns:
            {total_connections, active_connections, idle_connections,
             waiting_connections, usage_percent, aborted_connections_rate,
             sampled_at}。
        """

    @abstractmethod
    async def get_lock_info(self) -> dict[str, Any]:
        """获取当前锁等待信息。

        Returns:
            {
                "held_locks": [{"transaction_id", "thread_id", "table_name",
                                "index_name", "lock_mode", "lock_type",
                                "elapsed_seconds", "query"}, ...],
                "waiting_locks": [{"transaction_id", "thread_id", "table_name",
                                   "index_name", "lock_mode", "lock_type",
                                   "waiting_seconds", "blocking_transaction_id",
                                   "blocking_thread_id", "query"}, ...],
                "total_held": int, "total_waiting": int, "summary": str
            }
            失败返回 {"error": str, "detail": str}。
        """

    @abstractmethod
    async def get_replication_status(self) -> dict[str, Any]:
        """获取主从复制状态。

        Returns:
            {status, delay_seconds, io_thread_running, sql_thread_running, ...}。
            若不支持复制，返回 {"status": "skipped"}。
        """

    # ================== 指标 ==================

    @abstractmethod
    async def get_metrics(self) -> dict[str, Any]:
        """获取关键性能指标。

        Returns:
            包含连接数、QPS/TPS、缓冲池命中率、慢查询比例等指标的字典。
        """

    # ================== 能力声明 ==================

    @abstractmethod
    def get_capabilities(self) -> AdapterCapabilities:
        """返回此适配器实例的能力声明。

        Returns:
            AdapterCapabilities 实例，各布尔字段指示支持的功能。
        """
