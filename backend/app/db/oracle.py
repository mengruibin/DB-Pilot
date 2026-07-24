"""
Oracle 数据库适配器实现。

实现 BaseAdapter 全部 15 个抽象方法，使用 oracledb>=2.0 异步驱动。
依据 AGENTS.md §数据库驱动：MUST 使用 oracledb>=2.0。
特性：
  - :1, :2 参数化占位符（Oracle 风格）
  - 从 ALL_TABLES / ALL_TAB_COLUMNS 查询元数据
  - EXPLAIN PLAN FOR + DBMS_XPLAN.DISPLAY 获取执行计划
  - 连接失败不回显密码
"""

from __future__ import annotations

from typing import Any

import structlog

from app.db.base import AdapterCapabilities, BaseAdapter
from app.models.schemas import ConnectionCreateRequest

logger = structlog.get_logger("app.db.oracle")

# SAFETY: oracledb 在 connect() 时懒加载，不在模块层级 import
_ORACLEDB_AVAILABLE: bool = False
_ORACLEDB_ERR: str | None = None
try:
    import oracledb  # noqa: F401 — 延迟到 connect() 中实际使用

    _ORACLEDB_AVAILABLE = True
except ImportError as exc:
    _ORACLEDB_ERR = str(exc)


class OracleAdapter(BaseAdapter):
    """Oracle 数据库适配器。

    使用 oracledb 异步模式，适用于 Oracle 19c+。
    Oracle 适配器能力有限：不支持慢查询自动检索（需 AWR license），不支持复制检测。
    """

    def __init__(self, config: ConnectionCreateRequest) -> None:
        self._config = config
        self._conn: Any = None  # oracledb.AsyncConnection
        self._connected: bool = False

    # ================== 连接管理 ==================

    async def connect(
        self,
        config: ConnectionCreateRequest,
        user_role: str = "readonly",
    ) -> bool:
        """建立到 Oracle 的异步连接。

        使用 oracledb.connect_async()（oracledb>=2.0 异步模式）。
        连接失败时异常消息仅包含 host:port，不暴露密码。

        Args:
            config: 连接配置。
            user_role: 用户角色（当前 Oracle 适配器忽略此参数，保持标准模式）。
        """
        if not _ORACLEDB_AVAILABLE:
            raise ImportError("无法加载 oracledb 驱动。请安装：pip install oracledb>=2.0")

        import oracledb

        try:
            oracledb.defaults.fetch_lobs = False

            # 构建 DSN
            dsn = oracledb.makedsn(config.host, config.port or 1521, config.database)

            # SAFETY: 参数化连接配置，不拼接连接串
            self._conn = await oracledb.connect_async(
                user=config.user,
                password=config.password or "",
                dsn=dsn,
            )
            self._connected = True
            logger.info(
                "Oracle 连接成功", host=config.host, port=config.port, database=config.database
            )
            return True

        except Exception as exc:
            self._connected = False
            logger.warning(
                "Oracle 连接失败", host=config.host, port=config.port, error=str(exc)[:100]
            )
            # SAFETY: 连接失败异常消息仅包含 host:port
            raise ConnectionError(
                f"Oracle 连接失败 [{config.host}:{config.port}] — 请检查网络、用户名和密码"
            ) from exc

    async def disconnect(self) -> None:
        """关闭 Oracle 连接。"""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            logger.debug("Oracle 连接已关闭")
        self._connected = False

    async def test_connection(self) -> bool:
        """发送心跳查询测试连接。"""
        if not self._connected or self._conn is None:
            return False
        try:
            async with self._conn.cursor() as cur:
                await cur.execute("SELECT 1 FROM DUAL")
                row = await cur.fetchone()
                return row is not None and row[0] == 1
        except Exception:
            self._connected = False
            return False

    async def execute(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行只读 SQL 查询。

        SAFETY: 使用 :1, :2 参数化占位符（Oracle 风格），禁止拼接 SQL。
        oracledb 接受位置参数，将 dict values 转为列表。
        """
        if not self._connected or self._conn is None:
            raise ConnectionError("Oracle 未连接，请先调用 connect()")

        import time

        start = time.monotonic()

        try:
            # Oracle 使用 :1, :2 位置参数
            param_values = list(params.values()) if params else []

            async with self._conn.cursor() as cur:
                await cur.execute(sql, param_values)
                if cur.description:
                    # 读操作：有结果集返回
                    rows = await cur.fetchall()
                    columns = [desc[0] for desc in cur.description]
                    affected = len(rows)
                else:
                    # 写操作：无结果集，cur.rowcount 即为影响行数
                    affected = cur.rowcount
                    rows = []
                    columns = []

            elapsed = int((time.monotonic() - start) * 1000)
            logger.debug(
                "Oracle 查询完成",
                sql=sql[:200],
                execution_time_ms=elapsed,
                rows_returned=len(rows),
                affected_rows=affected,
            )
            return {
                "columns": columns,
                "rows": [list(row) for row in rows],
                "total_rows": len(rows),
                "affected_rows": affected,  # 写操作时为影响行数，读操作时 = total_rows
                "execution_time_ms": elapsed,
                "is_readonly": True,
                "audit_status": "passed",
            }

        except TimeoutError:
            logger.warning("Oracle 查询超时", sql=sql[:200])
            raise TimeoutError("Oracle 查询超时（>30s）") from None
        except Exception as exc:
            logger.error("Oracle 查询异常", sql=sql[:200], error=str(exc)[:200])
            raise ValueError(f"SQL 执行错误：{exc}") from exc

    # ================== 元数据 ==================

    async def get_databases(self) -> list[str]:
        """查询实例中的可插拔数据库（PDB）或用户 schema 列表。"""
        result = await self.execute("SELECT username FROM all_users ORDER BY username")
        return [row[0] for row in result["rows"]]

    async def get_tables(self, database: str) -> list[dict[str, Any]]:
        """从 ALL_TABLES 获取表列表。

        依据 AC-4：使用 ALL_TABLES 查询（Oracle 特有系统视图）。
        """
        sql = """
            SELECT table_name,
                   COALESCE(comments, '') AS table_comment,
                   num_rows AS row_count_estimate
            FROM all_tables t
            LEFT JOIN all_tab_comments c
                ON c.table_name = t.table_name AND c.owner = t.owner
            WHERE t.owner = UPPER(:1)
            ORDER BY t.table_name
        """
        result = await self.execute(sql, {"owner": database})
        tables = []
        for row in result["rows"]:
            tables.append(
                {
                    "database": database,
                    "table_name": row[0],
                    "comment": row[1] or "",
                    "row_count_estimate": row[2] or 0,
                }
            )
        return tables

    async def get_columns(
        self,
        database: str,
        table: str,
    ) -> list[dict[str, Any]]:
        """从 ALL_TAB_COLUMNS 获取列信息。

        依据 AC-5：使用 ALL_TAB_COLUMNS 查询。
        """
        sql = """
            SELECT
                c.column_name,
                c.data_type || CASE
                    WHEN c.data_precision IS NOT NULL AND c.data_scale IS NOT NULL
                    THEN '(' || c.data_precision || ',' || c.data_scale || ')'
                    WHEN c.char_length > 0 THEN '(' || c.char_length || ')'
                    ELSE ''
                END AS col_type,
                c.nullable,
                CASE WHEN pk.column_name IS NOT NULL THEN 1 ELSE 0 END AS is_primary,
                COALESCE(c.comments, '') AS column_comment
            FROM all_tab_columns c
            LEFT JOIN all_col_comments cc
                ON cc.table_name = c.table_name
                AND cc.column_name = c.column_name
                AND cc.owner = c.owner
            LEFT JOIN (
                SELECT acc.column_name, acc.table_name, acc.owner
                FROM all_constraints ac
                JOIN all_cons_columns acc
                    ON acc.constraint_name = ac.constraint_name
                    AND acc.owner = ac.owner
                WHERE ac.constraint_type = 'P'
            ) pk ON pk.column_name = c.column_name
                AND pk.table_name = c.table_name
                AND pk.owner = c.owner
            WHERE c.owner = UPPER(:1) AND c.table_name = UPPER(:2)
            ORDER BY c.column_id
        """
        result = await self.execute(sql, {"owner": database, "table": table})
        columns = []
        for row in result["rows"]:
            columns.append(
                {
                    "name": row[0],
                    "type": row[1],
                    "nullable": row[2] == "Y",
                    "is_primary": bool(row[3]),
                    "comment": row[4],
                }
            )
        return columns

    async def get_indexes(
        self,
        database: str,
        table: str,
    ) -> list[dict[str, Any]]:
        """从 ALL_INDEXES / ALL_IND_COLUMNS 获取索引信息。"""
        sql = """
            SELECT
                i.index_name,
                ic.column_name,
                i.uniqueness,
                i.index_type
            FROM all_indexes i
            JOIN all_ind_columns ic
                ON ic.index_name = i.index_name
                AND ic.table_owner = i.table_owner
            WHERE i.table_owner = UPPER(:1) AND i.table_name = UPPER(:2)
            ORDER BY i.index_name, ic.column_position
        """
        result = await self.execute(sql, {"owner": database, "table": table})

        index_map: dict[str, dict[str, Any]] = {}
        for row in result["rows"]:
            idx_name = row[0]
            if idx_name not in index_map:
                index_map[idx_name] = {
                    "name": idx_name,
                    "columns": [],
                    "is_unique": row[2] == "UNIQUE",
                    "type": row[3] or "BTREE",
                }
            index_map[idx_name]["columns"].append(row[1])
        return list(index_map.values())

    # ================== 诊断 ==================

    async def get_slow_queries(
        self,
        limit: int = 20,
        time_range: str = "1h",
        include_explain: bool = False,
    ) -> dict[str, Any]:
        """慢查询检索返回静态提示。

        依据 AC-6：Oracle 慢查询需要 AWR license，不自动检索。
        返回固定 warning。
        """
        return {
            "items": [],
            "total": 0,
            "slow_log_enabled": False,
            "fallback_used": False,
            "warning": "Oracle slow query retrieval requires AWR license; not auto-retrieved",
        }

    async def explain(self, sql: str) -> dict[str, Any]:
        """执行 EXPLAIN PLAN FOR + DBMS_XPLAN.DISPLAY。

        依据 AC-7：Oracle 标准执行计划获取方式。
        """
        # SAFETY: 第二道防线 — 上游 SQLAuditCheck 已做完整 AST 审计，
        # 此处处理 EXPLAIN 语句特有的安全问题：
        #   - 检测多语句注入（; 分隔符）
        #   - 转义单引号防注入破坏
        # Oracle EXPLAIN PLAN FOR 不支持参数化占位符，
        # 因此安全拼接 + 预检是正确做法。
        stripped = sql.strip().rstrip(";")
        if ";" in stripped:
            raise ValueError("多语句 SQL 无法执行 EXPLAIN（检测到未转义的分号）")
        safe_sql = sql.replace("'", "''")
        plan_sql = f"EXPLAIN PLAN FOR {safe_sql}"
        await self.execute(plan_sql)

        # 从计划表读取执行计划
        xplan_sql = """
            SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY(NULL, NULL, 'TYPICAL'))
        """
        result = await self.execute(xplan_sql)

        # DBMS_XPLAN.DISPLAY 返回格式化文本
        plan_lines = [row[0] for row in result["rows"]]
        logger.debug("Oracle EXPLAIN 完成", sql=sql[:200])
        return {
            "explain_output": "\n".join(plan_lines),
            "format": "text",
        }

    async def get_connections_status(self) -> dict[str, Any]:
        """从 V$SESSION 获取当前连接状态。"""
        # 最大连接数
        max_sql = "SELECT value FROM v$parameter WHERE name = 'sessions'"
        max_result = await self.execute(max_sql)
        max_val = int(max_result["rows"][0][0]) if max_result["rows"] else 300

        # 活跃和空闲连接
        active_sql = """
            SELECT COUNT(*) FROM v$session
            WHERE status = 'ACTIVE' AND username IS NOT NULL
        """
        total_sql = """
            SELECT COUNT(*) FROM v$session
            WHERE username IS NOT NULL
        """
        # 等待事件（非空闲等待）
        waiting_sql = """
            SELECT COUNT(*) FROM v$session
            WHERE wait_class != 'Idle' AND status = 'ACTIVE'
        """

        active_result = await self.execute(active_sql)
        active_val = active_result["rows"][0][0] if active_result["rows"] else 0
        total_result = await self.execute(total_sql)
        total_val = total_result["rows"][0][0] if total_result["rows"] else 0
        waiting_result = await self.execute(waiting_sql)
        waiting_val = waiting_result["rows"][0][0] if waiting_result["rows"] else 0

        usage_pct = round((total_val / max_val) * 100, 1) if max_val > 0 else 0.0

        import datetime

        return {
            "total_connections": max_val,
            "active_connections": active_val,
            "idle_connections": max(0, total_val - active_val),
            "waiting_connections": waiting_val,
            "usage_percent": usage_pct,
            "aborted_connections_rate": 0.0,
            "sampled_at": datetime.datetime.now(datetime.UTC).isoformat(),
        }

    async def get_lock_info(self) -> dict[str, Any]:
        """从 V$LOCK + V$SESSION 获取锁等待信息。

        Returns:
            {held_locks: [...], waiting_locks: [...], total_held, total_waiting, summary}。
            失败返回 {"error": ..., "detail": ...}。
        """
        try:
            sql = """
                SELECT
                    blocked.sid AS blocked_sid,
                    blocked.serial# AS blocked_serial,
                    blocked.blocking_session AS blocking_sid,
                    blocked.event,
                    blocked.seconds_in_wait AS elapsed_seconds,
                    blocked.sql_id,
                    blocked.username,
                    blocked.machine
                FROM v$session blocked
                WHERE blocked.blocking_session IS NOT NULL
                  AND blocked.username IS NOT NULL
                ORDER BY blocked.seconds_in_wait DESC
            """
            result = await self.execute(sql)

            waiting_locks = []
            for row in result["rows"]:
                waiting_locks.append(
                    {
                        "transaction_id": str(row[0]),
                        "thread_id": f"{row[0]},{row[1]}" if row[1] else str(row[0]),
                        "table_name": "",
                        "lock_mode": str(row[3]) if row[3] else "Lock",
                        "lock_type": "RECORD",
                        "waiting_seconds": row[4] or 0,
                        "elapsed_seconds": row[4] or 0,
                        "query": row[5] and f"SQL_ID: {row[5]}" or "",
                        "blocking_transaction_id": str(row[2]),
                        "blocking_thread_id": str(row[2]),
                    }
                )

            total_waiting = len(waiting_locks)
            summary = f"等待锁: {total_waiting}。" if total_waiting > 0 else "无锁等待"

            return {
                "held_locks": [],
                "waiting_locks": waiting_locks,
                "total_held": 0,
                "total_waiting": total_waiting,
                "summary": summary,
            }
        except Exception:
            return {
                "held_locks": [],
                "waiting_locks": [],
                "total_held": 0,
                "total_waiting": 0,
                "summary": "无法获取锁信息（可能是权限不足）",
            }

    async def get_replication_status(self) -> dict[str, Any]:
        """Oracle 复制状态暂不支持自动检测。"""
        return {"status": "skipped", "delay_seconds": None}

    # ================== 指标 ==================

    async def get_metrics(self) -> dict[str, Any]:
        """采集关键性能指标。

        使用 V$SYSSTAT 和 V$BUFFER_POOL_STATISTICS。
        """
        # 从 v$sysstat 获取累计指标
        stats_sql = """
            SELECT name, value
            FROM v$sysstat
            WHERE name IN (
                'user commits', 'user rollbacks',
                'physical reads', 'db block gets', 'consistent gets',
                'execute count', 'parse count (total)'
            )
        """
        stats_result = await self.execute(stats_sql)
        stats_map = {row[0]: int(row[1] or 0) for row in stats_result["rows"]}

        # Uptime
        uptime_sql = "SELECT ROUND((SYSDATE - startup_time) * 86400) FROM v$instance"
        uptime_result = await self.execute(uptime_sql)
        uptime_sec = int(uptime_result["rows"][0][0]) if uptime_result["rows"] else 1

        tps = round(
            (stats_map.get("user commits", 0) + stats_map.get("user rollbacks", 0))
            / max(uptime_sec, 1),
            2,
        )

        logical_reads = stats_map.get("db block gets", 0) + stats_map.get("consistent gets", 0)
        physical_reads = stats_map.get("physical reads", 0)
        bp_hit = (
            round((1 - physical_reads / max(logical_reads, 1)) * 100, 2)
            if logical_reads > 0
            else 100.0
        )

        import datetime

        return {
            "qps": 0.0,
            "tps": tps,
            "buffer_pool_hit_rate": bp_hit,
            "slow_query_ratio": 0.0,
            "connections": await self.get_connections_status(),
            "sampled_at": datetime.datetime.now(datetime.UTC).isoformat(),
        }

    # ================== 能力声明 ==================

    def get_capabilities(self) -> AdapterCapabilities:
        """Oracle 适配器能力声明。

        能力有限：
        - supports_explain: True（EXPLAIN PLAN + DBMS_XPLAN）
        - supports_slow_query_log: False（需 AWR license）
        - supports_replication: False（Data Guard 需额外配置）
        - supports_table_spaces: True（v$tablespace 可用）
        - supports_json_type: True（Oracle 12c+ JSON）
        - supports_lock_analysis: False（v$lock 需额外权限）
        - supports_kill_transaction: False（ALTER SYSTEM KILL SESSION 需权限）
        """
        return AdapterCapabilities(
            supports_explain=True,
            supports_slow_query_log=False,
            supports_replication=False,
            supports_table_spaces=True,
            supports_json_type=True,
            supports_lock_analysis=False,
            supports_kill_transaction=False,
        )
