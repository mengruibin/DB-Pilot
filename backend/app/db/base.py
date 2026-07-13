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

from app.models.schemas import ConnectionCreateRequest

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
    ) -> dict[str, Any]:
        """获取慢查询列表。

        Args:
            limit: 最大返回条数（默认 20，上限 100）。
            time_range: 时间范围（1h/6h/24h/7d）。

        Returns:
            {"items": [...], "total": int}。
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
    async def get_lock_info(self) -> list[dict[str, Any]]:
        """获取当前锁等待信息。

        Returns:
            [{transaction_id, user, host, elapsed_seconds, state, query,
              blocking_transaction_id, ...}, ...]。
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
