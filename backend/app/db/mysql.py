"""
MySQL 数据库适配器实现。

实现 BaseAdapter 全部 15 个抽象方法，使用 aiomysql 异步驱动。
依据 AGENTS.md §数据库驱动：MUST 使用 aiomysql。
依据 AGENTS.md §数据库操作原则：
  - readonly=True, autocommit=True
  - 参数化查询（%s 占位符），禁止拼接 SQL
  - 连接失败不回显密码
执行超时保护（PRD §8.1 Layer 4，与 PostgreSQL 统一 30s）：
  - 服务器端：执行前 best-effort 设置会话超时变量
    （MySQL≥5.7.8 MAX_EXECUTION_TIME / MariaDB max_statement_time），
    让数据库主动终止超时语句；版本不支持时静默降级。
  - 客户端兜底：asyncio.wait_for 包裹执行+拉取，防止服务器端机制缺失时无限等待。
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import structlog

from app.db.base import (
    STATEMENT_EXEC_TIMEOUT_MS,
    STATEMENT_EXEC_TIMEOUT_SEC,
    STATEMENT_MAX_RESULT_ROWS,
    AdapterCapabilities,
    BaseAdapter,
    apply_read_limit,
)
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
    连接时自动检测 MySQL/MariaDB 版本，供后续方法按版本适配查询方式。
    执行超时保护：服务器端会话超时变量（MySQL≥5.7.8 MAX_EXECUTION_TIME /
    MariaDB max_statement_time）+ 客户端 asyncio.wait_for 兜底，统一 30s。
    """

    def __init__(self, config: ConnectionCreateRequest) -> None:
        self._config = config
        self._pool: Any = None  # aiomysql.Pool
        self._connected: bool = False
        # 版本检测结果（connect() 后填充）
        self._db_vendor: str = "unknown"  # "mysql" 或 "mariadb"
        self._version_int: int = 0  # 如 80000（MySQL 8.0）、50700（MySQL 5.7）
        # 服务器端执行超时变量 SQL（connect() 版本检测后确定，execute() 内 best-effort 设置）
        self._timeout_sql: str | None = None

    def is_mariadb(self) -> bool:
        """判断当前连接是否为 MariaDB。

        Returns:
            True 表示 MariaDB，False 表示 MySQL。
        """
        return self._db_vendor == "mariadb"

    def get_db_version(self) -> tuple[int, int, int]:
        """获取数据库版本三元组。

        Returns:
            (major, minor, patch) 如 (8, 0, 36)。
        """
        major = self._version_int // 10000
        minor = (self._version_int % 10000) // 100
        patch = self._version_int % 100
        return (major, minor, patch)

    # ================== 连接管理 ==================

    async def connect(
        self,
        config: ConnectionCreateRequest,
        user_role: str = "readonly",
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
            raise ImportError("无法加载 aiomysql 驱动。请安装：pip install aiomysql")

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
                connect_timeout=10,  # TCP 建连超时（秒），避免连不上的主机长时间阻塞
            )

            # SAFETY: admin 角色可执行写操作（仍受上层 SQL 审计管控）
            # 非 admin 角色设置会话只读事务，作为双重防线
            is_readonly_session = user_role != "admin"
            async with self._pool.acquire() as conn, conn.cursor() as cur:
                if is_readonly_session:
                    await cur.execute("SET SESSION TRANSACTION READ ONLY")
                # 版本检测：查询 MySQL/MariaDB 版本号 + 版本注释
                ver_sql = "SELECT VERSION() AS version_str, @@version_comment AS server_comment"
                await cur.execute(ver_sql)
                row = await cur.fetchone()
                if row:
                    version_str = str(row[0]) if row[0] else ""
                    server_comment = str(row[1]) if len(row) > 1 and row[1] else ""
                    self._db_vendor = "mariadb" if "mariadb" in server_comment.lower() else "mysql"
                    # 解析版本号：如 "8.0.36" → 80036, "10.6.18-MariaDB-log" → 100618
                    import re as _re

                    ver_match = _re.search(r"(\d+)\.(\d+)\.(\d+)", version_str)
                    if ver_match:
                        major = int(ver_match.group(1))
                        minor = int(ver_match.group(2))
                        patch = int(ver_match.group(3))
                        self._version_int = major * 10000 + minor * 100 + patch
                    logger.info(
                        "数据库版本检测",
                        vendor=self._db_vendor,
                        version_str=version_str,
                        version_int=self._version_int,
                    )

            # 服务器端执行超时保护变量（版本感知，execute() 内按会话 best-effort 设置）：
            #   - MySQL ≥5.7.8 : MAX_EXECUTION_TIME（毫秒，仅 SELECT，超时即中断）
            #   - MariaDB      : max_statement_time（秒，对全部语句生效）
            #   - 其余旧版本   : 不支持会话超时变量 → 依赖客户端 asyncio.wait_for 兜底
            self._timeout_sql = None
            if self.is_mariadb():
                self._timeout_sql = f"SET SESSION max_statement_time = {STATEMENT_EXEC_TIMEOUT_SEC}"
            elif self._version_int >= 50708:
                self._timeout_sql = f"SET SESSION MAX_EXECUTION_TIME = {STATEMENT_EXEC_TIMEOUT_MS}"

            self._connected = True
            logger.info(
                "MySQL 连接成功",
                host=config.host,
                port=config.port,
                database=config.database,
                user_role=user_role,
                readonly_session=is_readonly_session,
            )
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
            logger.warning(
                "MySQL 连接失败",
                host=config.host,
                port=config.port,
                database=config.database,
                user=config.user,
                error_type=err_type,
                error=err_str,
            )
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

        超时保护（与 PostgreSQL 统一 30s，见 base.STATEMENT_EXEC_TIMEOUT_*）：
          - 服务器端：执行前 best-effort 设置会话超时变量，让数据库主动终止超时语句；
            版本不支持（旧 MySQL）时静默降级到客户端兜底。
          - 客户端兜底：asyncio.wait_for 包住执行+拉取，触发时连接状态已不可信，
            关闭连接让池重建（aiomysql putconn 会丢弃已关闭连接）。
        """
        if not self._connected or self._pool is None:
            raise ConnectionError("MySQL 未连接，请先调用 connect()")

        import time

        start = time.monotonic()

        conn = None
        try:
            conn = await self._pool.acquire()
            try:
                # 参数化查询：params dict 转为位置参数列表（禁止拼接 SQL）
                param_values = list(params.values()) if params else []
                # 结果集行数上限（LIMIT 下推）：只读 SELECT 自动追加 LIMIT，
                # 让 DB 只返回有限行数，保护进程内存（truncate_result_for_llm
                # 只护 LLM 上下文窗口，不护堆内存）。非 SELECT / 解析失败时
                # fail-safe 原样放行。
                sql_eff, rewritten = apply_read_limit(sql, dialect="mysql")
                if rewritten:
                    logger.debug(
                        "SQL LIMIT 下推（进程内存防护）",
                        sql_before=sql[:200],
                        sql_after=sql_eff[:200],
                    )
                async with conn.cursor() as cur:
                    # 服务器端执行超时保护（best-effort：版本不支持会话超时变量时静默降级）
                    if self._timeout_sql:
                        # 旧版本不支持 → 静默降级，依赖客户端 asyncio.wait_for 兜底
                        with contextlib.suppress(Exception):
                            await cur.execute(self._timeout_sql)

                    # 客户端兜底：asyncio.wait_for 包住执行+拉取，防止服务器端机制缺失时无限等待。
                    # 兜底超时（35s）略高于服务器端（30s），让 DB 先主动终止语句、连接保持可复用。
                    affected, rows = await asyncio.wait_for(
                        self._run_query(cur, sql_eff, param_values),
                        timeout=STATEMENT_EXEC_TIMEOUT_SEC + 5,
                    )
                    columns = [desc[0] for desc in cur.description] if cur.description else []
            except TimeoutError:
                # 客户端兜底触发：语句可能仍在服务器执行，连接状态已不可信。
                # 关闭连接（池 putconn 会丢弃已关闭连接并新建），避免脏连接被复用。
                conn.close()
                logger.warning("MySQL 查询超时（客户端兜底）", sql=sql[:200])
                raise TimeoutError("MySQL 查询超时（>30s）") from None

            elapsed = int((time.monotonic() - start) * 1000)
            logger.debug(
                "MySQL 查询完成",
                sql=sql[:200],
                execution_time_ms=elapsed,
                rows_returned=len(rows),
                affected_rows=affected,
            )
            result: dict[str, Any] = {
                "columns": columns,
                "rows": [list(row) for row in rows],
                "total_rows": len(rows),
                "affected_rows": affected,  # 写操作时为影响行数，读操作时 = total_rows
                "execution_time_ms": elapsed,
                "is_readonly": True,
                "audit_status": "passed",
            }
            # 触顶检测：LIMIT 下推返回 max+1 行说明结果被 DB 端截断，
            # 附加元数据透传给 LLM（truncate_result_for_llm 保留其它键）
            if len(rows) > STATEMENT_MAX_RESULT_ROWS:
                result["truncated_by_db_limit"] = True
                result["db_row_limit"] = STATEMENT_MAX_RESULT_ROWS
            return result

        except TimeoutError:
            raise  # 已在上层转换为带消息的 TimeoutError，直接透传
        except Exception as exc:
            # 服务器端 max_execution_time / max_statement_time 主动终止
            # （MySQL 3024 ER_QUERY_TIMEOUT / MariaDB 1969 ER_STATEMENT_TIMEOUT）
            # 同样归为超时，让 Agent 提示 LLM 改写查询而非当作普通 SQL 错误
            if getattr(exc, "args", None) and exc.args and exc.args[0] in (3024, 1969):
                logger.warning("MySQL 查询超时（服务器端终止）", sql=sql[:200])
                raise TimeoutError("MySQL 查询超时（>30s）") from None
            logger.error("MySQL 查询异常", sql=sql[:200], error=str(exc)[:200])
            raise ValueError(f"SQL 执行错误：{exc}") from exc
        finally:
            if conn is not None:
                self._pool.release(conn)

    async def execute_transaction(self, statements: list[str]) -> dict[str, Any]:
        """在单个数据库事务中执行多条写 SQL（原子性）。

        通过连接池获取一条连接并临时关闭 autocommit，逐条执行全部写语句：
          - 全部成功 → COMMIT，返回每条语句的影响行数（per_statement）
          - 任一失败（服务器拒绝、连接健康）→ ROLLBACK 后复用连接
          - 任一超时（客户端兜底触发 / 服务器端 3024/1969 终止）→ 关闭连接，
            InnoDB 断连自动回滚未提交事务，事务原子性由数据库保证
        事务结束必须还原 autocommit=True 再释放连接——池不重置会话状态，
        残留 manual-commit 会污染池内下一位 acquire 者（超时分支已 close，
        还原被 suppress，release 丢弃脏连接）。

        Args:
            statements: 待执行的写 SQL 语句列表（按顺序执行，仅限 DML）。

        Returns:
            {"columns": [], "rows": [], "total_rows": 0, "affected_rows": int,
             "execution_time_ms": int, "is_readonly": False, "audit_status": "passed",
             "per_statement": [{"sql": str, "affected_rows": int}, ...]}

        Raises:
            ValueError: 任一语句语法错误或执行失败（事务已回滚）。
            TimeoutError: 任一语句超时（>30s，事务已回滚）。
        """
        if not self._connected or self._pool is None:
            raise ConnectionError("MySQL 未连接，请先调用 connect()")
        if not statements:
            raise ValueError("空语句列表，无法执行事务")

        import time

        start = time.monotonic()
        conn = None
        try:
            conn = await self._pool.acquire()
            # 进入事务：关闭 autocommit（aiomysql 的 autocommit 是 async 方法）
            await conn.autocommit(False)
            per_statement: list[dict[str, Any]] = []
            total_affected = 0
            async with conn.cursor() as cur:
                # 服务器端执行超时保护（best-effort，同 execute()）
                if self._timeout_sql:
                    with contextlib.suppress(Exception):
                        await cur.execute(self._timeout_sql)
                for stmt in statements:
                    # 客户端兜底：逐条包 asyncio.wait_for，防止服务器端机制缺失时无限等待。
                    # cur.execute 返回影响行数（DML）或行数（SELECT，审计已排除）。
                    affected = await asyncio.wait_for(
                        cur.execute(stmt),
                        timeout=STATEMENT_EXEC_TIMEOUT_SEC + 5,
                    )
                    affected = int(affected or 0)
                    total_affected += affected
                    per_statement.append({"sql": stmt, "affected_rows": affected})
            await conn.commit()
            elapsed = int((time.monotonic() - start) * 1000)
            logger.info(
                "MySQL 事务提交成功",
                statements=len(statements),
                affected_rows=total_affected,
                execution_time_ms=elapsed,
            )
            return {
                "columns": [],
                "rows": [],
                "total_rows": 0,
                "affected_rows": total_affected,
                "execution_time_ms": elapsed,
                "is_readonly": False,
                "audit_status": "passed",
                "per_statement": per_statement,
            }
        except TimeoutError:
            # 客户端兜底触发：语句可能仍在服务器执行，连接状态不可信。
            # 关闭连接让池丢弃重建；InnoDB 断连自动回滚未提交事务 → 原子性保持。
            if conn is not None:
                conn.close()
            logger.warning(
                "MySQL 事务语句超时，连接已关闭（事务回滚）",
                statements=len(statements),
            )
            raise TimeoutError("MySQL 事务执行超时（>30s），事务已回滚") from None
        except Exception as exc:
            # 非超时异常：语句被服务器拒绝，连接仍健康 → 主动 rollback 后复用
            if conn is not None:
                with contextlib.suppress(Exception):
                    await conn.rollback()
            # 服务器端主动终止（MySQL 3024 ER_QUERY_TIMEOUT / MariaDB 1969
            # ER_STATEMENT_TIMEOUT）→ 归为超时（同 execute() 的归类逻辑）
            if getattr(exc, "args", None) and exc.args and exc.args[0] in (3024, 1969):
                raise TimeoutError("MySQL 事务执行超时（>30s），事务已回滚") from None
            logger.error("MySQL 事务执行异常（已回滚）", error=str(exc)[:200])
            raise ValueError(f"SQL 执行错误：{exc}") from exc
        finally:
            if conn is not None:
                # ★ 还原 autocommit 再释放：池不重置会话状态，残留 manual-commit 污染下一位
                with contextlib.suppress(Exception):
                    await conn.autocommit(True)
                self._pool.release(conn)

    @staticmethod
    async def _run_query(
        cur: Any,
        sql: str,
        param_values: list[Any],
    ) -> tuple[int, list[Any]]:
        """执行参数化查询并拉取全部结果（供 asyncio.wait_for 客户端兜底包裹）。

        cur.execute() 返回值：SELECT 返回行数，INSERT/UPDATE/DELETE 返回影响行数。
        """
        affected = await cur.execute(sql, param_values)
        rows = await cur.fetchall() if cur.description else []
        return affected, rows

    async def stream_query(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        batch_size: int = 2000,
    ) -> Any:
        """流式执行只读 SQL，按批产出 (columns, rows_batch)，内存有界。

        边查边出（fetchmany 分批），供导出端点流式生成 CSV 使用。
        游标持有期间占用一个连接池连接，导出完成即释放。
        """
        if not self._connected or self._pool is None:
            raise ConnectionError("MySQL 未连接，请先调用 connect()")

        # 参数化查询：params dict 转为位置参数列表（与 execute 一致，禁止拼接）
        param_values = list(params.values()) if params else []
        async with self._pool.acquire() as conn, conn.cursor() as cur:
            # 服务器端执行超时保护（best-effort，同 execute()）：
            # 流式场景不做 asyncio.wait_for 客户端兜底（生成器按批产出，
            # 取消语义复杂），服务器端会话超时变量已足以终止超时语句
            if self._timeout_sql:
                # 版本不支持 → 无超时保护（流式导出场景低频，可接受）
                with contextlib.suppress(Exception):
                    await cur.execute(self._timeout_sql)
            await cur.execute(sql, param_values)
            # 无结果集（如 SET 语句）→ 无产出
            if not cur.description:
                return
            columns = [desc[0] for desc in cur.description]
            yielded_any = False
            while True:
                batch = await cur.fetchmany(batch_size)
                if not batch:
                    # 空结果集也产出一次（带列名），让导出端写出表头
                    if not yielded_any:
                        yield columns, []
                    break
                yield columns, [list(row) for row in batch]
                yielded_any = True

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
        include_explain: bool = False,
    ) -> dict[str, Any]:
        """从 mysql.slow_log 读取慢查询日志，支持降级方案。

        增强功能：
          - time_range 参数生效（修复原硬编码 1h 问题）
          - 检测慢查询日志是否开启，未开启时给出开启指引
          - 日志不可用时自动降级到 performance_schema.events_statements_summary_by_digest
          - 可选：对慢查询自动执行 EXPLAIN（include_explain=True）

        若 slow_log 表不存在或未启用，返回 warning 而非崩溃。
        """

        def _to_sec(val) -> float:
            return float(val.total_seconds()) if hasattr(val, "total_seconds") else float(val)

        # Step 1: 检测慢查询日志是否开启
        try:
            log_status = await self.execute("SHOW VARIABLES LIKE 'slow_query_log'")
            log_on = bool(log_status["rows"] and log_status["rows"][0][1] == "ON")
        except Exception:
            log_on = True  # 不确定时假设已开启，避免误报

        # Step 2: 构建 time_range 对应的 INTERVAL 表达式
        time_map = {"1h": "1 HOUR", "6h": "6 HOUR", "24h": "1 DAY", "7d": "7 DAY"}
        interval = time_map.get(time_range, "1 HOUR")

        # 日志已开启，从 mysql.slow_log 查询
        if log_on:
            try:
                sql = f"""
                    SELECT start_time, user_host, query_time, lock_time,
                           rows_examined, rows_sent, sql_text
                    FROM mysql.slow_log
                    WHERE start_time >= NOW() - INTERVAL {interval}
                    ORDER BY query_time DESC
                    LIMIT %s
                """
                result = await self.execute(sql, {"limit": limit})
                items = []
                for row in result["rows"]:
                    entry = {
                        "sql_text": row[6],
                        "query_time_sec": _to_sec(row[2]),
                        "lock_time_sec": _to_sec(row[3]),
                        "rows_examined": row[4],
                        "rows_sent": row[5],
                        "executed_at": (
                            row[0].isoformat() if hasattr(row[0], "isoformat") else str(row[0])
                        ),
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
                if "doesn't exist" in err_msg or "access denied" in err_msg:
                    logger.warning("慢查询日志不可访问，尝试降级", error=str(exc)[:100])
                    # 日志不可访问，走降级
                else:
                    logger.error("慢查询查询异常", error=str(exc)[:200])
                    raise

        # Step 3: 慢查询日志未开启或不可用 → 降级方案
        warning = (
            "慢查询日志未开启。"
            "开启命令: SET GLOBAL slow_query_log = ON; SET GLOBAL long_query_time = 1;"
        )
        if not log_on:
            logger.warning("慢查询日志未开启")

        # 尝试 performance_schema 降级
        try:
            ps_sql = """
                SELECT
                    DIGEST_TEXT AS sql_text,
                    AVG_TIMER_WAIT / 1e12 AS avg_query_time_sec,
                    SUM_LOCK_TIME / 1e12 AS sum_lock_time_sec,
                    SUM_ROWS_EXAMINED AS rows_examined,
                    SUM_ROWS_SENT AS rows_sent,
                    COUNT_STAR AS exec_count,
                    LAST_SEEN AS last_seen
                FROM performance_schema.events_statements_summary_by_digest
                WHERE DIGEST_TEXT IS NOT NULL
                ORDER BY AVG_TIMER_WAIT DESC
                LIMIT %s
            """
            ps_result = await self.execute(ps_sql, {"limit": limit})
            items = []
            for row in ps_result["rows"]:
                entry = {
                    "sql_text": row[0],
                    "query_time_sec": float(row[1]) if row[1] else 0,
                    "lock_time_sec": float(row[2]) if row[2] else 0,
                    "rows_examined": row[3] or 0,
                    "rows_sent": row[4] or 0,
                    "exec_count": row[5] or 0,
                    "executed_at": (
                        row[6].isoformat() if hasattr(row[6], "isoformat") else str(row[6])
                    )
                    if row[6]
                    else "",
                }
                # 可选执行 EXPLAIN
                if include_explain and entry.get("sql_text"):
                    try:
                        explain_result = await self.explain(entry["sql_text"])
                        entry["explain_result"] = explain_result.get("explain_output", "")
                    except Exception:
                        pass
                items.append(entry)

            if not log_on:
                warning = (
                    "慢查询日志未开启，以下结果来自 performance_schema（聚合数据，非原始日志）"
                )
            else:
                warning = (
                    "mysql.slow_log 表不可访问，以下结果来自 performance_schema"
                    "（聚合数据，非原始日志）"
                )
            return {
                "items": items,
                "total": len(items),
                "slow_log_enabled": log_on,
                "fallback_used": True,
                "warning": warning,
            }
        except Exception as ps_exc:
            # 降级也不可用
            logger.error("performance_schema 降级也失败", error=str(ps_exc)[:100])
            return {
                "items": [],
                "total": 0,
                "slow_log_enabled": log_on,
                "fallback_used": False,
                "warning": f"{warning} 替代方案 performance_schema 也不可访问。",
            }

    async def explain(self, sql: str) -> dict[str, Any]:
        """执行 EXPLAIN FORMAT=JSON 并返回原始执行计划。

        SAFETY: 第二道防线 — 上游 SQLAuditCheck 已做完整 AST 审计（@tool extras 声明），
        此处处理 EXPLAIN 语句特有的安全问题：
          - 检测多语句注入（; 分隔符）
          - 转义单引号防注入破坏
        注意：MySQL EXPLAIN 不支持参数化占位符（bind parameters），
        因此安全拼接 + 预检是正确做法。
        """
        # 安全预检：拒绝多语句（第二道防线）
        stripped = sql.strip().rstrip(";")
        if ";" in stripped:
            raise ValueError("多语句 SQL 无法执行 EXPLAIN（检测到未转义的分号）")
        # 转义单引号（SQL 标准双单引号转义），防止 EXPLAIN 格式被注入破坏
        safe_sql = sql.replace("'", "''")
        result = await self.execute(f"EXPLAIN FORMAT=JSON {safe_sql}")
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

    async def get_lock_info(self) -> dict[str, Any]:
        """分析 InnoDB 锁等待信息，返回完整的锁拓扑。

        版本感知路由：
          - MySQL 8.0+: performance_schema.data_locks + data_lock_waits
          - MySQL 5.7/MariaDB: SHOW ENGINE INNODB STATUS 解析（回退方案）

        Returns:
            {held_locks: [...], waiting_locks: [...], total_held, total_waiting, summary}。
            失败返回 {"error": ..., "detail": ...}。
        """
        try:
            # 版本感知路由：MySQL 8.0+ 用 performance_schema 获取结构化锁信息
            if self._version_int >= 80000 and not self.is_mariadb():
                return await self._get_lock_info_p_schema()
            # MySQL 5.7 / MariaDB 回退到 SHOW ENGINE INNODB STATUS 解析
            return await self._get_lock_info_innodb_status()
        except Exception as exc:
            return {"error": "get_lock_info 执行失败", "detail": f"{type(exc).__name__}: {exc}"}

    async def _get_lock_info_p_schema(self) -> dict[str, Any]:
        """通过 performance_schema.data_locks 获取锁信息（MySQL 8.0+）。"""
        # 查询所有锁（含持有和等待），关联等待链和线程信息
        sql = """
            SELECT
                dl.ENGINE_TRANSACTION_ID,
                dl.OBJECT_SCHEMA,
                dl.OBJECT_NAME,
                dl.INDEX_NAME,
                dl.LOCK_TYPE,
                dl.LOCK_MODE,
                dl.LOCK_STATUS,
                dl.LOCK_DATA,
                dlw.REQUESTING_ENGINE_TRANSACTION_ID,
                dlw.BLOCKING_ENGINE_TRANSACTION_ID,
                pl.ID AS thread_id,
                pl.TIME AS elapsed_seconds,
                pl.INFO AS query_text
            FROM performance_schema.data_locks dl
            LEFT JOIN performance_schema.data_lock_waits dlw
                ON dl.ENGINE_TRANSACTION_ID = dlw.REQUESTING_ENGINE_TRANSACTION_ID
            LEFT JOIN information_schema.PROCESSLIST pl
                ON dl.THREAD_ID = pl.ID
            WHERE dl.OBJECT_SCHEMA NOT IN ('mysql', 'sys',
                  'performance_schema', 'information_schema')
            ORDER BY dl.ENGINE_TRANSACTION_ID
        """
        try:
            result = await self.execute(sql)
        except Exception:
            # performance_schema 不可用时回退到 INNODB STATUS
            return await self._get_lock_info_innodb_status()

        held_locks: list[dict] = []
        waiting_locks: list[dict] = []

        for row in result["rows"]:
            trx_id = str(row[0]) if row[0] else ""
            schema = str(row[1]) if row[1] else ""
            table = str(row[2]) if row[2] else ""
            index_name = str(row[3]) if row[3] else ""
            lock_type = str(row[4]) if row[4] else ""
            lock_mode_raw = str(row[5]) if row[5] else ""
            lock_status = str(row[6]) if row[6] else ""
            lock_data = str(row[7]) if row[7] else ""
            blocking_trx_id = str(row[9]) if len(row) > 9 and row[9] else ""
            thread_id = str(row[10]) if len(row) > 10 and row[10] else ""
            elapsed = int(row[11]) if len(row) > 11 and row[11] else 0
            query = str(row[12]) if len(row) > 12 and row[12] else ""

            if not trx_id and not thread_id:
                continue

            # 标准化锁模式描述
            normalized_mode = self._normalize_lock_mode(lock_mode_raw, lock_type)

            # 表名拼接
            table_name = f"{schema}.{table}" if schema and table else table or schema or ""

            lock_entry = {
                "transaction_id": trx_id,
                "thread_id": thread_id,
                "table_name": table_name,
                "index_name": index_name if index_name != "PRIMARY" else "主键索引",
                "lock_mode": normalized_mode,
                "lock_type": lock_type,
                "lock_data": lock_data if lock_data else "",
                "elapsed_seconds": elapsed,
                "query": query,
            }

            if lock_status == "WAITING":
                lock_entry["waiting_seconds"] = elapsed
                lock_entry["blocking_transaction_id"] = blocking_trx_id
                waiting_locks.append(lock_entry)
            else:
                # GRANTED 或未知状态归为持有锁
                held_locks.append(lock_entry)

        # 去重：相同事务+表的锁只保留一条
        seen_held: set = set()
        dedup_held: list[dict] = []
        for lock in held_locks:
            key = (lock["transaction_id"], lock["table_name"])
            if key not in seen_held:
                seen_held.add(key)
                dedup_held.append(lock)
        seen_waiting: set = set()
        dedup_waiting: list[dict] = []
        for lock in waiting_locks:
            key = (lock["transaction_id"], lock["table_name"])
            if key not in seen_waiting:
                seen_waiting.add(key)
                dedup_waiting.append(lock)
        held_locks = dedup_held
        waiting_locks = dedup_waiting

        total_held = len(held_locks)
        total_waiting = len(waiting_locks)

        locked_tables = sorted(
            set(lock["table_name"] for lock in held_locks + waiting_locks if lock["table_name"])
        )

        return {
            "held_locks": held_locks,
            "waiting_locks": waiting_locks,
            "total_held": total_held,
            "total_waiting": total_waiting,
            "summary": self._build_lock_summary(
                total_held,
                total_waiting,
                locked_tables,
            ),
        }

    @staticmethod
    def _normalize_lock_mode(mode_raw: str, lock_type: str) -> str:
        """将 performance_schema 原始锁模式映射为可读描述。"""
        # TABLE 类型锁
        if lock_type == "TABLE":
            if "IX" in mode_raw:
                return "意向排他锁(IX)"
            if "IS" in mode_raw:
                return "意向共享锁(IS)"
            if "S" in mode_raw:
                return "共享锁(S)"
            if "X" in mode_raw:
                return "排他锁(X)"
            return mode_raw
        # RECORD 类型锁
        if (
            "GAP" in mode_raw
            and "REC_NOT_GAP" not in mode_raw
            and "INSERT_INTENTION" not in mode_raw
        ):
            return "Gap Lock"
        if "REC_NOT_GAP" in mode_raw:
            return "Record Lock"
        if "GAP" in mode_raw and "REC_NOT_GAP" not in mode_raw:
            return "Gap Lock"
        if "INSERT_INTENTION" in mode_raw:
            return "Insert Intention Lock"
        if "LOCK_DATA" in mode_raw or "AUTO_INC" in mode_raw:
            return "AUTO-INC Lock"
        # 无 GAP/REC_NOT_GAP 标记的复合型
        if "S" in mode_raw and "X" not in mode_raw and lock_type == "RECORD":
            return "Record Lock(S)"
        if "X" in mode_raw and lock_type == "RECORD":
            return "Record Lock(X)"
        return mode_raw

    async def _get_lock_info_innodb_status(self) -> dict[str, Any]:
        """通过 SHOW ENGINE INNODB STATUS 解析锁信息（MySQL 5.7 / MariaDB 回退方案）。"""
        import re

        result = await self.execute("SHOW ENGINE INNODB STATUS")
        innodb_status = result["rows"][0][2] if result["rows"] else ""

        locks = []

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

            if "waiting for" in detail.lower() or "lock" in detail.lower():
                query_match = re.search(r"MySQL thread id (\d+)", detail)
                thread_id = query_match.group(1) if query_match else ""

                # 提取阻塞的查询
                sql_match = re.search(r"\((.*?)\)\n(.*?)(?:\n|$)", detail)
                query_text = sql_match.group(2).strip() if sql_match else ""

                # 解析 RECORD LOCKS 段获取表名和锁模式
                table_name = ""
                lock_mode_desc = ""
                rec_lock_match = re.search(
                    r"TABLE LOCK table `(\w+)`\.`(\w+)`.*?\n.*?RECORD LOCKS.*?space id",
                    detail,
                    re.DOTALL,
                )
                if rec_lock_match:
                    table_name = f"{rec_lock_match.group(1)}.{rec_lock_match.group(2)}"
                # 从 detail 中提取锁模式
                lock_mode_match = re.search(
                    r"lock mode (S|X|IX|IS|S GAP|X GAP|AUTO-INC)",
                    detail,
                )
                if lock_mode_match:
                    lock_mode_desc = lock_mode_match.group(1)

                locks.append(
                    {
                        "transaction_id": tx_id_clean,
                        "thread_id": thread_id,
                        "table_name": table_name,
                        "lock_mode": lock_mode_desc,
                        "lock_type": "RECORD",
                        "query": query_text,
                        "elapsed_seconds": sec_val,
                        "blocking_transaction_id": "",
                    }
                )

        # INNODB STATUS 无法精确区分 held/waiting，全部归为 waiting_locks
        # 对于 INNODB STATUS 解析出的信息做去重
        seen = set()
        deduped_locks = []
        for lock in locks:
            key = (lock["transaction_id"], lock["table_name"], lock["query"])
            if key not in seen:
                seen.add(key)
                deduped_locks.append(lock)

        total_waiting = len(deduped_locks)
        locked_tables = sorted(
            set(lock["table_name"] for lock in deduped_locks if lock["table_name"])
        )

        return {
            "held_locks": [],
            "waiting_locks": deduped_locks,
            "total_held": 0,
            "total_waiting": total_waiting,
            "summary": self._build_lock_summary(0, total_waiting, locked_tables),
        }

    @staticmethod
    def _build_lock_summary(
        total_held: int,
        total_waiting: int,
        locked_tables: list[str],
    ) -> str:
        """构建锁信息摘要字符串。"""
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
        if not parts:
            return "无锁等待"
        return " | ".join(parts) + "。"

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
        - supports_replication: True（SHOW SLAVE STATUS / SHOW REPLICA STATUS）
        - supports_table_spaces: False（MySQL 表空间信息需 FILE 权限）
        - supports_json_type: True（MySQL 5.7+ JSON 类型）
        - supports_lock_analysis: MySQL 8.0+ 有 performance_schema，5.7 可通过 INFORMATION_SCHEMA
        - supports_kill_transaction: True（KILL CONNECTION / KILL QUERY）
        """
        # 锁分析能力：MySQL 8.0+ 用 performance_schema.data_locks，
        # 5.7 用 INFORMATION_SCHEMA.INNODB_LOCKS，MariaDB 不支持结构化查询（回退 INNODB STATUS）
        # 锁分析能力：
        #   MySQL 8.0+: performance_schema.data_locks（结构化，含表名/锁模式/行键值）
        #   MySQL 5.7:  INFORMATION_SCHEMA.INNODB_LOCKS（有限字段，无锁模式分类）
        #   MariaDB:     INFORMATION_SCHEMA 和 P_S 均不支持，回退到 INNODB STATUS
        can_lock_analysis = self._version_int >= 50700 and not self.is_mariadb()
        return AdapterCapabilities(
            supports_explain=True,
            supports_slow_query_log=True,
            supports_replication=True,
            supports_table_spaces=False,
            supports_json_type=True,
            supports_lock_analysis=can_lock_analysis,
            supports_kill_transaction=True,
        )
