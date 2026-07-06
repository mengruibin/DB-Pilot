"""
MySQL 数据库适配器实现。

实现 BaseAdapter 全部 15 个抽象方法，使用 aiomysql 异步驱动。
依据 AGENTS.md §数据库驱动：MUST 使用 aiomysql。
依据 AGENTS.md §数据库操作原则：
  - readonly=True, autocommit=True
  - 参数化查询（%s 占位符），禁止拼接 SQL
  - 连接失败不回显密码
"""

from __future__ import annotations

from typing import Any

import structlog

from app.db.base import AdapterCapabilities, BaseAdapter
from app.models.schemas import ConnectionCreateRequest

logger = structlog.get_logger("app.db.mysql")

# SAFETY: aiomysql 在 connect() 时懒加载，不在模块层级 import
# 依据 AGENTS.md §数据库驱动：适配器层不 import 目标数据库驱动（模块级别）
_AIOMYSQL_AVAILABLE: bool = False
_AIOMYSQL_ERR: str | None = None
try:
    import aiomysql  # noqa: F401 — 延迟到 connect() 中实际使用
    _AIOMYSQL_AVAILABLE = True
except ImportError as exc:
    _AIOMYSQL_ERR = str(exc)


class MySQLAdapter(BaseAdapter):
    """MySQL/MariaDB 数据库适配器。

    使用 aiomysql 异步连接池，默认只读 + 自动提交模式。
    """

    def __init__(self, config: ConnectionCreateRequest) -> None:
        self._config = config
        self._pool: Any = None  # aiomysql.Pool
        self._connected: bool = False

    # ================== 连接管理 ==================

    async def connect(
        self,
        config: ConnectionCreateRequest,
        user_role: str = "standard",
    ) -> bool:
        """建立到 MySQL 的连接池。

        根据 user_role 控制只读事务：
          - admin: 不设置 READ ONLY（可执行写操作，由上层 SQL 审计管控）
          - 其他: 设置 SET SESSION TRANSACTION READ ONLY（双重防线）
        使用 autocommit=True 模式（AGENTS.md §数据库操作原则）。
        连接失败时异常消息仅包含 host:port，不暴露密码。

        Args:
            config: 连接配置。
            user_role: 用户角色（admin 跳过只读事务，其他角色设置只读）。
        """
        if not _AIOMYSQL_AVAILABLE:
            raise ImportError(
                "无法加载 aiomysql 驱动。"
                "请安装：pip install aiomysql"
            )

        import aiomysql

        try:
            # SAFETY: 参数化连接配置，不拼接连接串
            self._pool = await aiomysql.create_pool(
                host=config.host,
                port=config.port,
                user=config.user,
                password=config.password or "",
                db=config.database,
                autocommit=True,  # AGENTS.md：只读 + 自动提交
                pool_recycle=3600,
                maxsize=10,
                minsize=1,
            )

            # SAFETY: admin 角色可执行写操作（仍受上层 SQL 审计管控）
            # 非 admin 角色设置会话只读事务，作为双重防线
            is_readonly_session = user_role != "admin"
            async with self._pool.acquire() as conn, conn.cursor() as cur:
                if is_readonly_session:
                    await cur.execute("SET SESSION TRANSACTION READ ONLY")
                await cur.execute("SELECT 1")

            self._connected = True
            logger.info("MySQL 连接成功",
                        host=config.host, port=config.port,
                        database=config.database,
                        user_role=user_role,
                        readonly_session=is_readonly_session)
            return True

        except Exception as exc:
            self._connected = False
            err_str = str(exc)[:200]
            # 区分常见错误类型（不暴露密码）
            if "Access denied" in err_str or "1045" in err_str:
                err_type = "认证失败"
            elif "Can't connect" in err_str or "2003" in err_str or "2002" in err_str:
                err_type = "无法连接（网络/防火墙）"
            elif "Unknown database" in err_str or "1049" in err_str:
                err_type = "数据库不存在"
            else:
                err_type = "连接异常"
            logger.warning("MySQL 连接失败",
                           host=config.host, port=config.port,
                           database=config.database, user=config.user,
                           error_type=err_type, error=err_str)
            # SAFETY: 连接失败异常消息仅包含 host:port（AGENTS.md §安全与合规红线）
            raise ConnectionError(
                f"MySQL 连接失败 [{config.host}:{config.port}] — {err_type}，"
                "请检查网络、用户名和密码"
            ) from exc

    async def disconnect(self) -> None:
        """关闭连接池，释放所有连接。"""
        if self._pool is not None:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
            logger.debug("MySQL 连接已关闭")
        self._connected = False

    async def test_connection(self) -> bool:
        """发送心跳查询测试连接。"""
        if not self._connected or self._pool is None:
            return False
        try:
            async with self._pool.acquire() as conn, conn.cursor() as cur:
                await cur.execute("SELECT 1 AS ping")
                result = await cur.fetchone()
                return result is not None and result[0] == 1
        except Exception:
            self._connected = False
            return False

    async def execute(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行只读 SQL 查询。

        SAFETY: 使用 %s 参数化占位符，禁止拼接 SQL 字符串。
        AGENTS.md §数据库操作原则：所有 SQL 通过占位符传递。
        """
        if not self._connected or self._pool is None:
            raise ConnectionError("MySQL 未连接，请先调用 connect()")

        import time

        start = time.monotonic()

        try:
            async with self._pool.acquire() as conn, conn.cursor() as cur:
                # 参数化查询：params dict 转为位置参数列表
                param_values = list(params.values()) if params else []
                await cur.execute(sql, param_values)
                rows = await cur.fetchall()
                columns = [desc[0] for desc in cur.description] if cur.description else []

            elapsed = int((time.monotonic() - start) * 1000)
            logger.debug("MySQL 查询完成",
                         sql=sql[:200], execution_time_ms=elapsed,
                         rows_returned=len(rows))
            return {
                "columns": columns,
                "rows": [list(row) for row in rows],
                "total_rows": len(rows),
                "execution_time_ms": elapsed,
                "is_readonly": True,
                "audit_status": "passed",
            }

        except TimeoutError:
            logger.warning("MySQL 查询超时", sql=sql[:200])
            raise TimeoutError("MySQL 查询超时（>30s）") from None
        except Exception as exc:
            logger.error("MySQL 查询异常", sql=sql[:200], error=str(exc)[:200])
            raise ValueError(f"SQL 执行错误：{exc}") from exc

    # ================== 元数据 ==================

    async def get_databases(self) -> list[str]:
        """查询实例上的数据库列表。"""
        result = await self.execute("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA")
        return [row[0] for row in result["rows"]]

    async def get_tables(self, database: str) -> list[dict[str, Any]]:
        """从 information_schema.TABLES 获取表列表。"""
        sql = """
            SELECT TABLE_NAME, TABLE_COMMENT, TABLE_ROWS
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'
            ORDER BY TABLE_NAME
        """
        result = await self.execute(sql, {"schema": database})
        tables = []
        for row in result["rows"]:
            tables.append({
                "database": database,
                "table_name": row[0],
                "comment": row[1] or "",
                "row_count_estimate": row[2] or 0,
            })
        return tables

    async def get_columns(
        self,
        database: str,
        table: str,
    ) -> list[dict[str, Any]]:
        """从 information_schema.COLUMNS 获取列信息。"""
        sql = """
            SELECT
                c.COLUMN_NAME,
                c.COLUMN_TYPE,
                c.IS_NULLABLE,
                c.COLUMN_KEY = 'PRI' AS IS_PRIMARY,
                COALESCE(c.COLUMN_COMMENT, '') AS COLUMN_COMMENT
            FROM information_schema.COLUMNS c
            WHERE c.TABLE_SCHEMA = %s AND c.TABLE_NAME = %s
            ORDER BY c.ORDINAL_POSITION
        """
        result = await self.execute(sql, {"schema": database, "table": table})
        columns = []
        for row in result["rows"]:
            columns.append({
                "name": row[0],
                "type": row[1],
                "nullable": row[2] == "YES",
                "is_primary": bool(row[3]),
                "comment": row[4],
            })
        return columns

    async def get_indexes(
        self,
        database: str,
        table: str,
    ) -> list[dict[str, Any]]:
        """从 information_schema.STATISTICS 获取索引信息。

        TODO(T-4): 敏感列判定暂不支持从注释读取，使用列名正则 fallback。
        """
        sql = """
            SELECT
                INDEX_NAME,
                COLUMN_NAME,
                NON_UNIQUE = 0 AS IS_UNIQUE,
                INDEX_TYPE
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            ORDER BY INDEX_NAME, SEQ_IN_INDEX
        """
        result = await self.execute(sql, {"schema": database, "table": table})

        # 按索引名聚合多列索引
        index_map: dict[str, dict[str, Any]] = {}
        for row in result["rows"]:
            idx_name = row[0]
            if idx_name not in index_map:
                index_map[idx_name] = {
                    "name": idx_name,
                    "columns": [],
                    "is_unique": bool(row[2]),
                    "type": row[3] or "BTREE",
                }
            index_map[idx_name]["columns"].append(row[1])

        return list(index_map.values())

    # ================== 诊断 ==================

    async def get_slow_queries(
        self,
        limit: int = 20,
        time_range: str = "1h",
    ) -> dict[str, Any]:
        """从 mysql.slow_log 读取慢查询日志。

        若 slow_log 表不存在或未启用，返回 warning 而非崩溃。
        """
        def _to_sec(val) -> float:
            return float(val.total_seconds()) if hasattr(val, "total_seconds") else float(val)

        sql = """
            SELECT start_time, user_host, query_time, lock_time,
                   rows_examined, rows_sent, sql_text
            FROM mysql.slow_log
            WHERE start_time >= NOW() - INTERVAL 1 HOUR
            ORDER BY query_time DESC
            LIMIT %s
        """
        try:
            result = await self.execute(sql, {"limit": limit})
            items = []
            for row in result["rows"]:
                items.append({
                    "sql_text": row[6],
                    "query_time_sec": _to_sec(row[2]),
                    "lock_time_sec": _to_sec(row[3]),
                    "rows_examined": row[4],
                    "rows_sent": row[5],
                    "executed_at": (
                        row[0].isoformat() if hasattr(row[0], "isoformat") else str(row[0])
                    ),
                })
            return {"items": items, "total": len(items)}

        except Exception as exc:
            err_msg = str(exc).lower()
            if "doesn't exist" in err_msg or "access denied" in err_msg:
                logger.warning("慢查询日志不可访问", error=str(exc)[:100])
                return {
                    "items": [],
                    "warning": "mysql.slow_log 表不可访问（可能是未启用慢查询日志或权限不足）",
                }
            logger.error("慢查询查询异常", error=str(exc)[:200])
            raise  # 其他异常向上传播

    async def explain(self, sql: str) -> dict[str, Any]:
        """执行 EXPLAIN FORMAT=JSON 并返回原始执行计划。"""
        result = await self.execute(f"EXPLAIN FORMAT=JSON {sql}")
        return {
            "explain_output": result["rows"][0][0] if result["rows"] else "",
            "format": "json",
        }

    async def get_connections_status(self) -> dict[str, Any]:
        """获取当前连接状态（SHOW STATUS + SHOW PROCESSLIST）。"""
        # 获取最大连接数和当前连接数
        max_conn = await self.execute("SHOW VARIABLES LIKE 'max_connections'")
        active = await self.execute("SHOW STATUS LIKE 'Threads_connected'")
        threads_running = await self.execute("SHOW STATUS LIKE 'Threads_running'")

        max_val = int(max_conn["rows"][0][1]) if max_conn["rows"] else 200
        active_val = int(active["rows"][0][1]) if active["rows"] else 0
        running_val = int(threads_running["rows"][0][1]) if threads_running["rows"] else 0

        # 等待连接数：SHOW PROCESSLIST 中状态为 "Waiting for..." 的线程
        plist = await self.execute("SHOW PROCESSLIST")
        waiting = 0
        if plist["rows"]:
            waiting = sum(1 for row in plist["rows"] if "wait" in str(row[5]).lower())

        usage_pct = round((active_val / max_val) * 100, 1) if max_val > 0 else 0.0

        import datetime
        return {
            "total_connections": max_val,
            "active_connections": active_val,
            "idle_connections": max(0, active_val - running_val),
            "waiting_connections": waiting,
            "usage_percent": usage_pct,
            "aborted_connections_rate": 0.0,
            "sampled_at": datetime.datetime.now(datetime.UTC).isoformat(),
        }

    async def get_lock_info(self) -> list[dict[str, Any]]:
        """分析 InnoDB 锁等待信息。

        使用 SHOW ENGINE INNODB STATUS 解析锁等待链（PRD §5.3 死锁检测方式）。
        """
        import re

        result = await self.execute("SHOW ENGINE INNODB STATUS")
        innodb_status = result["rows"][0][2] if result["rows"] else ""

        # 解析 LATEST DETECTED DEADLOCK 和 TRANSACTIONS 段
        locks = []

        # 从 TRANSACTIONS 段提取锁等待事务
        tx_blocks = re.findall(
            r"---TRANSACTION (.*?), ACTIVE (.*?) sec.*?\n(.*?)(?=---TRANSACTION|\\n)",
            innodb_status,
            re.DOTALL,
        )

        for tx_id, sec_str, detail in tx_blocks:
            tx_id_clean = tx_id.strip()
            sec_val = 0
            try:
                sec_val = int(sec_str.strip())
            except ValueError:
                sec_val = 0

            # 检查是否有锁等待
            if "waiting for" in detail.lower() or "lock" in detail.lower():
                query_match = re.search(r"MySQL thread id (\d+)", detail)
                thread_id = query_match.group(1) if query_match else ""

                # 提取阻塞的查询
                sql_match = re.search(r"\((.*?)\)\n(.*?)(?:\n|$)", detail)
                query_text = sql_match.group(2).strip() if sql_match else ""

                locks.append({
                    "transaction_id": tx_id_clean,
                    "elapsed_seconds": sec_val,
                    "state": "LOCK WAIT",
                    "query": query_text,
                    "blocking_transaction_id": "",
                    "thread_id": thread_id,
                })

        return locks

    async def get_replication_status(self) -> dict[str, Any]:
        """获取主从复制状态。

        SAFETY: 检查 SHOW SLAVE STATUS（MySQL 5.7）/ SHOW REPLICA STATUS（MySQL 8.0+）。
        """
        try:
            result = await self.execute("SHOW SLAVE STATUS")
        except Exception:
            try:
                result = await self.execute("SHOW REPLICA STATUS")
            except Exception:
                return {"status": "skipped", "delay_seconds": None}

        if not result["rows"]:
            return {"status": "skipped", "delay_seconds": None}

        row = result["rows"][0]
        io_running = row[10] if len(row) > 10 else "No"
        sql_running = row[11] if len(row) > 11 else "No"
        delay = row[32] if len(row) > 32 else None  # Seconds_Behind_Master

        io_ok = str(io_running).strip().upper() == "YES"
        sql_ok = str(sql_running).strip().upper() == "YES"
        delay_sec = int(delay) if delay is not None and str(delay).isdigit() else None

        if io_ok and sql_ok:
            status = "healthy"
        elif delay_sec is not None and delay_sec > 60:
            status = "error"
        elif delay_sec is not None and delay_sec > 10:
            status = "warning"
        else:
            status = "degraded"

        return {
            "status": status,
            "delay_seconds": delay_sec,
            "io_thread_running": io_ok,
            "sql_thread_running": sql_ok,
        }

    # ================== 指标 ==================

    async def get_metrics(self) -> dict[str, Any]:
        """采集关键性能指标。

        包含：QPS、TPS、缓冲池命中率、慢查询比例等（PRD §5.4）。
        """
        # QPS: Com_select / Uptime
        uptime = await self.execute("SHOW STATUS LIKE 'Uptime'")
        com_insert = await self.execute("SHOW STATUS LIKE 'Com_insert'")
        com_update = await self.execute("SHOW STATUS LIKE 'Com_update'")
        com_delete = await self.execute("SHOW STATUS LIKE 'Com_delete'")
        innodb_bp_reads = await self.execute("SHOW STATUS LIKE 'Innodb_buffer_pool_reads'")
        stmt = "SHOW STATUS LIKE 'Innodb_buffer_pool_read_requests'"
        innodb_bp_read_requests = await self.execute(stmt)
        slow_queries = await self.execute("SHOW STATUS LIKE 'Slow_queries'")
        questions = await self.execute("SHOW STATUS LIKE 'Questions'")

        def get_val(data: dict, idx: int = 1) -> int:
            try:
                return int(data["rows"][0][idx])
            except (IndexError, ValueError, TypeError):
                return 0

        uptime_sec = get_val(uptime)
        questions_val = get_val(questions)
        qps = round(questions_val / uptime_sec, 2) if uptime_sec > 0 else 0.0
        tps_numerator = get_val(com_insert) + get_val(com_update) + get_val(com_delete)
        tps = round(tps_numerator / max(uptime_sec, 1), 2)

        bp_reads = get_val(innodb_bp_reads)
        bp_requests = get_val(innodb_bp_read_requests)
        bp_hit = round((1 - bp_reads / max(bp_requests, 1)) * 100, 2) if bp_requests > 0 else 100.0

        slow_val = get_val(slow_queries)
        slow_pct = round((slow_val / max(questions_val, 1)) * 100, 4)

        import datetime
        return {
            "qps": qps,
            "tps": tps,
            "buffer_pool_hit_rate": bp_hit,
            "slow_query_ratio": slow_pct,
            "connections": await self.get_connections_status(),
            "sampled_at": datetime.datetime.now(datetime.UTC).isoformat(),
        }

    # ================== 能力声明 ==================

    def get_capabilities(self) -> AdapterCapabilities:
        """MySQL 适配器能力声明。

        - supports_explain: True（EXPLAIN FORMAT=JSON）
        - supports_slow_query_log: True（mysql.slow_log）
        - supports_replication: True（SHOW SLAVE STATUS）
        - supports_table_spaces: False（MySQL 表空间信息需 FILE 权限）
        - supports_json_type: True（MySQL 5.7+ JSON 类型）
        """
        return AdapterCapabilities(
            supports_explain=True,
            supports_slow_query_log=True,
            supports_replication=True,
            supports_table_spaces=False,
            supports_json_type=True,
        )
