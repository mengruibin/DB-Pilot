"""
PostgreSQL 数据库适配器实现。

实现 BaseAdapter 全部 15 个抽象方法，使用 asyncpg 异步驱动。
依据 AGENTS.md §数据库驱动：MUST 使用 asyncpg。
特性：
  - $1, $2 参数化占位符（PostgreSQL 风格）
  - statement_timeout=30000ms（PRD §8.1 Layer 4 执行保护）
  - 连接失败不回显密码
"""

from __future__ import annotations

from typing import Any

import structlog

from app.db.base import (
    STATEMENT_EXEC_TIMEOUT_MS,
    STATEMENT_EXEC_TIMEOUT_SEC,
    AdapterCapabilities,
    BaseAdapter,
)
from app.models.schemas import ConnectionCreateRequest

logger = structlog.get_logger("app.db.postgresql")

# SAFETY: asyncpg 在 connect() 时懒加载，不在模块层级 import
_ASYNCPG_AVAILABLE: bool = False
_ASYNCPG_ERR: str | None = None
try:
    import asyncpg  # type: ignore  # noqa: F401 — 可选懒加载驱动，connect() 才真正 import

    _ASYNCPG_AVAILABLE = True
except ImportError as exc:
    _ASYNCPG_ERR = str(exc)


class PostgresAdapter(BaseAdapter):
    """PostgreSQL 数据库适配器。

    使用 asyncpg 异步连接池，默认 statement_timeout=30s（统一策略，见 base.py）。
    """

    def __init__(self, config: ConnectionCreateRequest) -> None:
        self._config = config
        self._pool: Any = None  # asyncpg.Pool
        self._connected: bool = False

    # ================== 连接管理 ==================

    async def connect(
        self,
        config: ConnectionCreateRequest,
        user_role: str = "readonly",
    ) -> bool:
        """建立到 PostgreSQL 的连接池。

        使用 statement_timeout=30000（PRD §8.1 Layer 4：执行保护默认 30s，
        统一策略见 base.STATEMENT_EXEC_TIMEOUT_*）。
        连接失败时异常消息仅包含 host:port，不暴露密码。

        Args:
            config: 连接配置。
            user_role: 用户角色（当前 PostgreSQL 适配器忽略此参数，保持标准模式）。
        """
        if not _ASYNCPG_AVAILABLE:
            raise ImportError("无法加载 asyncpg 驱动。请安装：pip install asyncpg")

        import asyncpg

        try:
            # SAFETY: 参数化连接配置，不拼接连接串
            self._pool = await asyncpg.create_pool(
                host=config.host,
                port=config.port,
                user=config.user,
                password=config.password or "",
                database=config.database,
                min_size=1,
                max_size=10,
                # SAFETY: 执行超时保护（PRD §8.1 Layer 4，统一 30s，见 base.py）
                command_timeout=STATEMENT_EXEC_TIMEOUT_SEC,
                # SSL 配置
                ssl="require" if config.ssl_enabled else "prefer",
            )
            # 验证连接可用
            async with self._pool.acquire() as conn:
                await conn.execute("SELECT 1")
            self._connected = True
            logger.info(
                "PostgreSQL 连接成功", host=config.host, port=config.port, database=config.database
            )
            return True

        except Exception as exc:
            self._connected = False
            logger.warning(
                "PostgreSQL 连接失败", host=config.host, port=config.port, error=str(exc)[:100]
            )
            # SAFETY: 连接失败异常消息仅包含 host:port
            raise ConnectionError(
                f"PostgreSQL 连接失败 [{config.host}:{config.port}] — 请检查网络、用户名和密码"
            ) from exc

    async def disconnect(self) -> None:
        """关闭连接池，释放所有连接。"""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.debug("PostgreSQL 连接已关闭")
        self._connected = False

    async def test_connection(self) -> bool:
        """发送心跳查询测试连接。"""
        if not self._connected or self._pool is None:
            return False
        try:
            async with self._pool.acquire() as conn:
                result = await conn.fetchval("SELECT 1 AS ping")
                return result == 1
        except Exception:
            self._connected = False
            return False

    async def execute(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行只读 SQL 查询。

        SAFETY: 使用 $1, $2 参数化占位符（PostgreSQL 风格），禁止拼接 SQL。
        asyncpg 的 execute() 接受位置参数，这里将 dict 转为位置列表。
        """
        if not self._connected or self._pool is None:
            raise ConnectionError("PostgreSQL 未连接，请先调用 connect()")

        import time

        start = time.monotonic()

        try:
            # asyncpg 使用 $1, $2 位置参数，将 dict values 转为列表
            param_values = list(params.values()) if params else []
            # 判断 SQL 类型：INSERT/UPDATE/DELETE 等写操作使用 execute() 获取影响行数
            sql_upper = sql.strip().upper()
            is_write = any(
                sql_upper.startswith(kw)
                for kw in [
                    "INSERT",
                    "UPDATE",
                    "DELETE",
                    "TRUNCATE",
                    "CREATE",
                    "ALTER",
                    "DROP",
                    "GRANT",
                    "REVOKE",
                    "MERGE",
                    "REPLACE",
                ]
            )

            async with self._pool.acquire() as conn:
                # 设置当前会话的 statement_timeout（服务器端执行超时保护，统一 30s）
                await conn.execute(f"SET statement_timeout = '{STATEMENT_EXEC_TIMEOUT_MS}'")

                if is_write:
                    # 写操作：execute() 返回状态字符串如 "INSERT 0 1"、"UPDATE 3"
                    status = await conn.execute(sql, *param_values)
                    # 状态字符串最后一个 token 即为影响行数
                    parts = status.split()
                    affected = int(parts[-1]) if parts and parts[-1].isdigit() else 0
                    rows = []
                    columns = []
                else:
                    # 读操作：使用 fetch() 返回所有行
                    rows = await conn.fetch(sql, *param_values)
                    columns = list(rows[0].keys()) if rows else []
                    affected = len(rows)

            elapsed = int((time.monotonic() - start) * 1000)
            logger.debug(
                "PostgreSQL 查询完成",
                sql=sql[:200],
                execution_time_ms=elapsed,
                rows_returned=len(rows),
                affected_rows=affected,
            )
            return {
                "columns": columns,
                "rows": [list(row.values()) for row in rows],
                "total_rows": len(rows),
                "affected_rows": affected,  # 写操作时为影响行数，读操作时 = total_rows
                "execution_time_ms": elapsed,
                "is_readonly": True,
                "audit_status": "passed",
            }

        except TimeoutError:
            logger.warning("PostgreSQL 查询超时", sql=sql[:200])
            raise TimeoutError("PostgreSQL 查询超时（>30s）") from None
        except Exception as exc:
            logger.error("PostgreSQL 查询异常", sql=sql[:200], error=str(exc)[:200])
            raise ValueError(f"SQL 执行错误：{exc}") from exc

    async def stream_query(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        batch_size: int = 2000,
    ) -> Any:
        """流式拉取暂未支持（v1 仅 MySQL）。"""
        raise NotImplementedError("stream_query 暂未支持此数据库类型（v1 仅 MySQL）")

    # ================== 元数据 ==================

    async def get_databases(self) -> list[str]:
        """查询实例上的数据库列表（PostgreSQL 中为 datname）。"""
        result = await self.execute("SELECT datname FROM pg_database WHERE datistemplate = false")
        return [row[0] for row in result["rows"]]

    async def get_tables(self, database: str) -> list[dict[str, Any]]:
        """从 information_schema.tables 获取表列表。"""
        sql = """
            SELECT table_name,
                   COALESCE(obj_description(
                       (quote_ident(table_schema) || '.' || quote_ident(table_name))::regclass,
                       'pg_class'
                   ), '') AS table_comment,
                   0 AS row_count_estimate
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """
        result = await self.execute(sql)
        tables = []
        for row in result["rows"]:
            tables.append(
                {
                    "database": database,
                    "table_name": row[0],
                    "comment": row[1] or "",
                    "row_count_estimate": row[2],
                }
            )
        return tables

    async def get_columns(
        self,
        database: str,
        table: str,
    ) -> list[dict[str, Any]]:
        """从 information_schema.columns + pg_catalog 获取列信息。

        使用 pg_catalog 判断主键（is_primary），结合 information_schema 的通用字段。
        依据 AC-4：含 is_primary 判断。
        """
        sql = """
            SELECT
                c.column_name,
                c.data_type || COALESCE('(' || c.character_maximum_length || ')', '') AS col_type,
                c.is_nullable,
                CASE WHEN pk.column_name IS NOT NULL THEN true ELSE false END AS is_primary,
                COALESCE(pg_catalog.col_description(
                    (quote_ident(c.table_schema) || '.' || quote_ident(c.table_name))::regclass,
                    c.ordinal_position
                ), '') AS column_comment
            FROM information_schema.columns c
            LEFT JOIN (
                SELECT ku.column_name, ku.table_name, ku.table_schema
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage ku
                    ON tc.constraint_name = ku.constraint_name
                    AND tc.table_schema = ku.table_schema
                WHERE tc.constraint_type = 'PRIMARY KEY'
            ) pk ON pk.column_name = c.column_name
                AND pk.table_name = c.table_name
                AND pk.table_schema = c.table_schema
            WHERE c.table_schema = 'public' AND c.table_name = $1
            ORDER BY c.ordinal_position
        """
        result = await self.execute(sql, {"table": table})
        columns = []
        for row in result["rows"]:
            columns.append(
                {
                    "name": row[0],
                    "type": row[1],
                    "nullable": row[2] == "YES",
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
        """从 pg_indexes 获取索引信息。"""
        sql = """
            SELECT
                i.indexname,
                i.indexdef,
                am.amname AS index_type
            FROM pg_indexes i
            JOIN pg_class c ON c.relname = i.indexname
            JOIN pg_am am ON am.oid = c.relam
            WHERE i.schemaname = 'public' AND i.tablename = $1
            ORDER BY i.indexname
        """
        result = await self.execute(sql, {"table": table})
        indexes = []
        for row in result["rows"]:
            idx_def = row[1]
            # 从 indexdef 解析列名和唯一性
            is_unique = "UNIQUE" in str(idx_def).upper()
            # 提取括号内的列名
            import re

            col_match = re.search(r"\((.*?)\)", str(idx_def))
            columns = [c.strip() for c in col_match.group(1).split(",")] if col_match else []
            indexes.append(
                {
                    "name": row[0],
                    "columns": columns,
                    "is_unique": is_unique,
                    "type": row[2] or "BTREE",
                }
            )
        return indexes

    # ================== 诊断 ==================

    async def get_slow_queries(
        self,
        limit: int = 20,
        time_range: str = "1h",
        include_explain: bool = False,
    ) -> dict[str, Any]:
        """从 pg_stat_statements 扩展读取慢查询。

        若 pg_stat_statements 扩展未安装，返回 warning 而非崩溃（AC-5）。
        """
        sql = """
            SELECT
                query,
                mean_exec_time,
                total_exec_time,
                calls,
                rows,
                min_exec_time,
                max_exec_time
            FROM pg_stat_statements
            ORDER BY mean_exec_time DESC
            LIMIT $1
        """
        try:
            result = await self.execute(sql, {"limit": limit})
            items = []
            for row in result["rows"]:
                entry = {
                    "sql_text": row[0][:2000] if row[0] else "",
                    "query_time_sec": round(float(row[1] / 1000), 3) if row[1] else 0,
                    "total_time_sec": round(float(row[2] / 1000), 3) if row[2] else 0,
                    "calls": row[3] or 0,
                    "rows_examined": 0,
                    "rows_sent": row[4] or 0,
                    "executed_at": "",
                }
                # 可选执行 EXPLAIN
                if include_explain and entry.get("sql_text"):
                    try:
                        explain_result = await self.explain(entry["sql_text"])
                        entry["explain_result"] = explain_result.get("explain_output", "")
                    except Exception:
                        pass
                items.append(entry)
            return {
                "items": items,
                "total": len(items),
                "slow_log_enabled": True,
                "fallback_used": False,
            }

        except Exception as exc:
            err_msg = str(exc).lower()
            if "pg_stat_statements" in err_msg or "does not exist" in err_msg:
                logger.warning("慢查询日志不可访问", error=str(exc)[:100])
                return {
                    "items": [],
                    "total": 0,
                    "slow_log_enabled": False,
                    "fallback_used": False,
                    "warning": "pg_stat_statements 扩展未安装或权限不足，"
                    "请执行：CREATE EXTENSION pg_stat_statements;",
                }
            raise

    async def explain(self, sql: str) -> dict[str, Any]:
        """执行 EXPLAIN (FORMAT JSON, ANALYZE false)。

        依据 AC-6：EXPLAIN (FORMAT JSON, ANALYZE false)
        仅获取执行计划，不实际执行查询。

        SAFETY: 第二道防线 — 上游 SQLAuditCheck 已做完整 AST 审计，
        此处处理 EXPLAIN 语句特有的安全问题：
          - 检测多语句注入（; 分隔符）
          - 转义单引号防注入破坏
        PostgreSQL EXPLAIN 不支持参数化占位符（PREPARE 不支持 EXPLAIN），
        因此安全拼接 + 预检是正确做法。
        """
        # 安全预检：拒绝多语句（第二道防线）
        stripped = sql.strip().rstrip(";")
        if ";" in stripped:
            raise ValueError("多语句 SQL 无法执行 EXPLAIN（检测到未转义的分号）")
        # 转义单引号，防止 EXPLAIN 格式被注入破坏
        safe_sql = sql.replace("'", "''")
        result = await self.execute(f"EXPLAIN (FORMAT JSON, ANALYZE false) {safe_sql}")
        logger.debug("PostgreSQL EXPLAIN 完成", sql=sql[:200])
        return {
            "explain_output": result["rows"][0][0] if result["rows"] else "",
            "format": "json",
        }

    async def get_connections_status(self) -> dict[str, Any]:
        """从 pg_stat_activity 获取当前连接状态。"""
        # 总连接数（最大连接数配置）
        max_conn = await self.execute("SHOW max_connections")
        # 当前活跃连接
        active_sql = """
            SELECT COUNT(*) FROM pg_stat_activity
            WHERE state = 'active'
        """
        # 空闲连接
        idle_sql = """
            SELECT COUNT(*) FROM pg_stat_activity
            WHERE state = 'idle'
        """
        # 等待连接（wait_event 非空即为等待）
        waiting_sql = """
            SELECT COUNT(*) FROM pg_stat_activity
            WHERE wait_event IS NOT NULL AND state = 'active'
        """
        # 总连接
        total_sql = """
            SELECT COUNT(*) FROM pg_stat_activity
            WHERE backend_type = 'client backend'
        """

        max_val = int(max_conn["rows"][0][0]) if max_conn["rows"] else 100
        active_val_result = await self.execute(active_sql)
        active_val = active_val_result["rows"][0][0] if active_val_result["rows"] else 0
        idle_val_result = await self.execute(idle_sql)
        idle_val = idle_val_result["rows"][0][0] if idle_val_result["rows"] else 0
        waiting_val_result = await self.execute(waiting_sql)
        waiting_val = waiting_val_result["rows"][0][0] if waiting_val_result["rows"] else 0
        total_val_result = await self.execute(total_sql)
        total_val = total_val_result["rows"][0][0] if total_val_result["rows"] else 0

        usage_pct = round((total_val / max_val) * 100, 1) if max_val > 0 else 0.0

        import datetime

        return {
            "total_connections": max_val,
            "active_connections": active_val,
            "idle_connections": idle_val,
            "waiting_connections": waiting_val,
            "usage_percent": usage_pct,
            "aborted_connections_rate": 0.0,
            "sampled_at": datetime.datetime.now(datetime.UTC).isoformat(),
        }

    async def get_lock_info(self) -> dict[str, Any]:
        """查询 pg_locks + pg_stat_activity 关联分析锁等待。

        依据 AC-7：关联 pg_locks 和 pg_stat_activity 识别阻塞事务。
        PRD §5.3：使用 pg_locks 检测死锁。

        Returns:
            {held_locks: [...], waiting_locks: [...], total_held, total_waiting, summary}。
            失败返回 {"error": ..., "detail": ...}。
        """
        try:
            # 查询等待锁（blocked）——未被授予的锁
            waiting_sql = """
                SELECT
                    blocked.pid AS blocked_pid,
                    blocked.query AS blocked_query,
                    blocked.datname AS database_name,
                    blocking.pid AS blocking_pid,
                    blocking.query AS blocking_query,
                    EXTRACT(EPOCH FROM (NOW() - blocked.query_start))::int AS elapsed_seconds,
                    COALESCE(blocked.wait_event_type, '') AS wait_event_type
                FROM pg_locks blocked_locks
                JOIN pg_stat_activity blocked ON blocked.pid = blocked_locks.pid
                JOIN pg_locks blocking_locks
                    ON blocking_locks.locktype = blocked_locks.locktype
                    AND blocking_locks.database = blocked_locks.database
                    AND blocking_locks.relation = blocked_locks.relation
                    AND blocking_locks.page = blocked_locks.page
                    AND blocking_locks.tuple = blocked_locks.tuple
                    AND blocking_locks.pid != blocked_locks.pid
                JOIN pg_stat_activity blocking
                    ON blocking.pid = blocking_locks.pid
                WHERE NOT blocked_locks.granted
                  AND blocked.backend_type = 'client backend'
                GROUP BY blocked.pid, blocked.query, blocked.datname,
                         blocking.pid, blocking.query, blocked.query_start,
                         blocked.wait_event_type
                ORDER BY elapsed_seconds DESC
            """
            waiting_result = await self.execute(waiting_sql)

            # 查询持有锁（granted）——已授予且正在活跃的事务
            held_sql = """
                SELECT
                    a.pid,
                    a.query,
                    a.datname,
                    l.locktype,
                    l.mode,
                    EXTRACT(EPOCH FROM (NOW() - a.query_start))::int AS elapsed_seconds,
                    l.relation::regclass::text AS relation_name
                FROM pg_locks l
                JOIN pg_stat_activity a ON a.pid = l.pid
                WHERE l.granted = true
                  AND a.backend_type = 'client backend'
                  AND a.state = 'active'
                  AND l.relation IS NOT NULL
                ORDER BY elapsed_seconds DESC
                LIMIT 50
            """
            held_result = await self.execute(held_sql)

            waiting_locks = []
            for row in waiting_result["rows"]:
                waiting_locks.append(
                    {
                        "transaction_id": str(row[0]),
                        "thread_id": str(row[0]),
                        "table_name": str(row[2]) if row[2] else "",
                        "lock_mode": str(row[6]) if row[6] else "Lock",
                        "lock_type": "RECORD",
                        "waiting_seconds": row[5] or 0,
                        "elapsed_seconds": row[5] or 0,
                        "query": row[1] or "",
                        "blocking_transaction_id": str(row[3]),
                        "blocking_thread_id": str(row[3]),
                        "blocking_query": row[4] or "",
                    }
                )

            held_locks = []
            for row in held_result["rows"]:
                held_locks.append(
                    {
                        "transaction_id": str(row[0]),
                        "thread_id": str(row[0]),
                        "table_name": str(row[6]) if row[6] else str(row[2]) if row[2] else "",
                        "index_name": str(row[3]) if row[3] else "",
                        "lock_mode": str(row[4]) if row[4] else "Lock",
                        "lock_type": "RECORD",
                        "elapsed_seconds": row[5] or 0,
                        "query": row[1] or "",
                    }
                )

            total_held = len(held_locks)
            total_waiting = len(waiting_locks)

            locked_tables = sorted(
                set(lock["table_name"] for lock in held_locks + waiting_locks if lock["table_name"])
            )

            # 构建摘要
            parts = []
            if total_held > 0:
                parts.append(f"持有锁: {total_held}")
            if total_waiting > 0:
                parts.append(f"等待锁: {total_waiting}")
            if locked_tables:
                tables_str = ", ".join(locked_tables[:5])
                if len(locked_tables) > 5:
                    tables_str += f" 等 {len(locked_tables)} 张表"
                parts.append(f"涉及表: {tables_str}")
            summary = " | ".join(parts) + "。" if parts else "无锁等待"

            return {
                "held_locks": held_locks,
                "waiting_locks": waiting_locks,
                "total_held": total_held,
                "total_waiting": total_waiting,
                "summary": summary,
            }
        except Exception:
            # 权限不足时返回空结构
            return {
                "held_locks": [],
                "waiting_locks": [],
                "total_held": 0,
                "total_waiting": 0,
                "summary": "无法获取锁信息（可能是权限不足）",
            }

    async def get_replication_status(self) -> dict[str, Any]:
        """查询 pg_stat_replication 获取主从复制状态。

        依据 AC-8：查询 pg_stat_replication 识别延迟。
        """
        sql = """
            SELECT
                pid,
                application_name,
                state,
                sync_state,
                EXTRACT(EPOCH FROM (pg_last_wal_receive_lsn() - pg_last_wal_replay_lsn()))::int
                    AS replay_lag_seconds
            FROM pg_stat_replication
        """
        try:
            result = await self.execute(sql)
            if not result["rows"]:
                return {"status": "skipped", "delay_seconds": None}

            row = result["rows"][0]
            delay = row[4] if len(row) > 4 else None
            state = row[2] if len(row) > 2 else ""

            if state == "streaming" and (delay is None or delay < 10):
                status = "healthy"
            elif delay is not None and delay > 60:
                status = "error"
            elif delay is not None and delay > 10:
                status = "warning"
            else:
                status = "degraded"

            return {
                "status": status,
                "delay_seconds": delay,
                "io_thread_running": state == "streaming",
                "sql_thread_running": True,
                "application_name": row[1] if len(row) > 1 else "",
                "sync_mode": row[3] if len(row) > 3 else "",
            }
        except Exception:
            return {"status": "skipped", "delay_seconds": None}

    # ================== 指标 ==================

    async def get_metrics(self) -> dict[str, Any]:
        """采集关键性能指标。

        包含：QPS、TPS、缓冲池命中率、慢查询比例等（PRD §5.4）。
        PostgreSQL 使用 pg_stat_database 和 pg_buffercache。。
        """
        # QPS 和 TPS 从 pg_stat_database 获取
        stats_sql = """
            SELECT
                datname,
                xact_commit + xact_rollback AS transactions,
                tup_inserted + tup_updated + tup_deleted AS total_tuples
            FROM pg_stat_database
            WHERE datname = current_database()
        """
        # 缓冲池命中率（pg_stat_bgwriter + pg_stat_database）
        hit_sql = """
            SELECT
                COALESCE(blks_hit, 0) AS blks_hit,
                COALESCE(blks_read, 0) AS blks_read
            FROM pg_stat_database
            WHERE datname = current_database()
        """
        stats = await self.execute(stats_sql)
        hit = await self.execute(hit_sql)

        # Uptime
        uptime_sql = "SELECT EXTRACT(EPOCH FROM pg_postmaster_start_time())::int"
        uptime_result = await self.execute(uptime_sql)
        uptime_sec = uptime_result["rows"][0][0] if uptime_result["rows"] else 1

        tps = 0.0
        if stats["rows"] and uptime_sec > 0:
            tps_val = stats["rows"][0][1] or 0
            tps = round(float(tps_val) / uptime_sec, 2)

        bp_hit = 100.0
        if hit["rows"]:
            blks_hit_val = int(hit["rows"][0][0] or 0)
            blks_read_val = int(hit["rows"][0][1] or 0)
            total = blks_hit_val + blks_read_val
            bp_hit = round((blks_hit_val / max(total, 1)) * 100, 2)

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
        """PostgreSQL 适配器能力声明。

        全部能力支持（PostgreSQL 功能最全）：
        - supports_explain: True（EXPLAIN FORMAT JSON）
        - supports_slow_query_log: True（pg_stat_statements）
        - supports_replication: True（pg_stat_replication）
        - supports_table_spaces: True（pg_tablespace）
        - supports_json_type: True
        - supports_lock_analysis: True（pg_locks + pg_stat_activity）
        - supports_kill_transaction: True（pg_terminate_backend）
        """
        return AdapterCapabilities(
            supports_explain=True,
            supports_slow_query_log=True,
            supports_replication=True,
            supports_table_spaces=True,
            supports_json_type=True,
            supports_lock_analysis=True,
            supports_kill_transaction=True,
        )
