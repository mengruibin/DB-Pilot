"""
MySQL 用户权限检测引擎。

通过连接 MySQL 执行 SHOW GRANTS FOR CURRENT_USER()，解析 GRANT 语句，
自动判定数据库用户的实际可写权限，映射为应用层 user_role：
  - 仅有 SELECT 权限 → "readonly"
  - 有 INSERT/UPDATE/DELETE 或 ALL PRIVILEGES → "admin"

安全降级原则：任何检测失败（连接异常、解析异常、权限不足）→ 返回 "readonly"，
宁可误拦，不可漏放。

依据 PRD §8.3 权限模型：
  DB-Pilot 不自创权限体系，权限源头来自目标数据库本身的 GRANT。
"""

from __future__ import annotations

import contextlib
import re
import time as _time

import structlog

logger = structlog.get_logger(__name__)

# 角色缓存（connection_id → (role, expire_at)）
# 使用惰性过期：读取时检查 expire_at，过期视为未命中
# 供 chat.py / troubleshoot.py 共享使用
_role_cache: dict[str, tuple[str, float]] = {}
# TTL：5 分钟。DBA 改 GRANT 后最多 5 分钟生效
_role_cache_ttl: int = 300


def get_cached_role(connection_id: str, trace_id: str = "") -> str | None:
    """从内存缓存读取用户角色（惰性过期）。

    缓存条目过期时视为未命中，返回 None。
    调用方会重新检测并覆盖写入缓存。

    Args:
        connection_id: 连接 ID。
        trace_id: 请求追踪 ID（仅用于日志）。

    Returns:
        "admin"、"readonly"，或 None（未命中或已过期）。
    """
    entry = _role_cache.get(connection_id)
    if entry is None:
        logger.debug("角色缓存未命中",
                     connection_id=connection_id, trace_id=trace_id)
        return None
    role, expire_at = entry
    if _time.monotonic() > expire_at:
        # 过期——惰性删除，视为未命中
        del _role_cache[connection_id]
        logger.info("角色缓存已过期，下次请求将重新检测",
                    connection_id=connection_id, role=role,
                    trace_id=trace_id)
        return None
    logger.debug("角色缓存命中",
                 connection_id=connection_id, role=role, trace_id=trace_id)
    return role


def set_cached_role(connection_id: str, role: str, trace_id: str = "") -> None:
    """写入用户角色到内存缓存，附带过期时间。

    Args:
        connection_id: 连接 ID。
        role: "admin" 或 "readonly"。
        trace_id: 请求追踪 ID（仅用于日志）。
    """
    _role_cache[connection_id] = (role, _time.monotonic() + _role_cache_ttl)
    logger.info("角色缓存已更新",
                connection_id=connection_id, role=role,
                expire_seconds=_role_cache_ttl, trace_id=trace_id)


# 可写权限关键字（出现任意一个即视为 admin）
_WRITE_KEYWORDS = frozenset({
    "INSERT",
    "UPDATE",
    "DELETE",
    "ALL PRIVILEGES",
    "ALL",
})

# SHOW GRANTS 每行 GRANT 语句的正则（MySQL 8.0+ / MariaDB 格式）
# 示例匹配行:
#   GRANT SELECT ON `mydb`.* TO `user`@`host`
#   GRANT SELECT, INSERT, UPDATE ON *.* TO `user`@`host`
#   GRANT ALL PRIVILEGES ON *.* TO `user`@`host`
_GRANT_LINE_RE = re.compile(
    r"^GRANT\s+(.+?)\s+ON\s+(.+?)\s+TO\s+",
    re.IGNORECASE,
)

# 逗号分隔的权限列表拆分
_PRIVILEGE_SPLIT_RE = re.compile(r"\s*,\s*")


def _parse_grants(grants: list[str], target_database: str) -> str:
    """解析 SHOW GRANTS 输出，判定用户角色。

    逐行检查 GRANT 语句：
      1. 按正则提取该行授予的权限列表和 ON 的范围
      2. 若目标数据库没有显式 GRANT，且存在 *.* 级别的写权限 → admin
      3. 若目标数据库级别有写权限 → admin
      4. 否则 → readonly

    Args:
        grants: SHOW GRANTS 返回的行列表。
        target_database: 目标数据库名，用于判断 `db`.* 级别的权限。

    Returns:
        "admin" 或 "readonly"。
    """
    # 判断一个 ON 范围是否匹配目标数据库
    # 匹配模式: `target_db`.*, `target_db`.`table`, *.*
    def _matches_database(on_clause: str) -> bool:
        on_clean = on_clause.strip().replace("`", "").strip()
        # *.* 匹配所有数据库；或 db.* 精确匹配目标数据库
        return (
            on_clean == "*.*"
            or on_clean.startswith(f"{target_database}.")
        )

    has_global_write = False      # *.* 级别有写权限
    has_database_write = False    # 目标数据库级别有写权限

    for line in grants:
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("--"):
            continue

        match = _GRANT_LINE_RE.match(line)
        if not match:
            # USAGE 权限（仅连接，无任何实际权限）
            # REVOKE 语句可以忽略
            continue

        privs_raw = match.group(1).strip()
        on_clause = match.group(2).strip()

        # 检查 USAGE 权限——仅允许连接，跳过
        if privs_raw.upper() == "USAGE":
            continue

        # 拆分权限列表
        # 注意: GRANT 中的权限名可能带括号，如 CREATE (col1), SELECT (col1)
        # 我们只关心关键字级别的权限名，括号内的列过滤忽略
        privs = _PRIVILEGE_SPLIT_RE.split(privs_raw)

        # 检查是否包含写权限关键字
        has_write = False
        for priv in privs:
            priv_upper = priv.upper().strip()
            # 去除可能的后括号，如 "SELECT (col1)" → "SELECT"
            priv_upper = re.sub(r"\s*\(.*\)\s*$", "", priv_upper).strip()
            if priv_upper in _WRITE_KEYWORDS:
                has_write = True
                break

        if not has_write:
            continue

        # 有写权限，判断范围
        if _matches_database(on_clause):
            if on_clause.strip().strip("`") == "*.*":
                has_global_write = True
            else:
                has_database_write = True

    # 判定：目标数据库级别有写权限 → admin
    # 或者无数据库级别权限但有全局写权限 → admin（用户可写任意数据库）
    if has_database_write or has_global_write:
        return "admin"

    return "readonly"


async def detect_mysql_role(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    ssl_enabled: bool = False,
    ssl_ca_cert: str | None = None,
    trace_id: str = "",
) -> str:
    """连接 MySQL，执行 SHOW GRANTS，判定用户角色。

    创建一个临时连接（非连接池），执行 SHOW GRANTS FOR CURRENT_USER()，
    解析结果后立即断开。

    Args:
        host: 数据库主机地址。
        port: 数据库端口号。
        user: 连接用户名。
        password: 连接密码。
        database: 目标数据库名（用于精确判断数据库级权限）。
        ssl_enabled: 是否启用 SSL。
        ssl_ca_cert: SSL CA 证书（可选）。
        trace_id: 请求追踪 ID（关联到 SSE 请求链路）。

    Returns:
        "admin" 或 "readonly"。任何异常安全降级为 "readonly"。
    """
    try:
        import aiomysql
    except ImportError:
        logger.warning("aiomysql 不可用，权限检测降级为 readonly",
                       trace_id=trace_id)
        return "readonly"

    conn = None
    try:
        # SAFETY: 创建临时连接，使用参数化连接配置
        conn = await aiomysql.connect(
            host=host,
            port=port,
            user=user,
            password=password or "",
            db=database,
            ssl=ssl_enabled or None,
            autocommit=True,
            connect_timeout=5,  # 5 秒超时，避免长时间阻塞
        )

        async with conn.cursor() as cur:
            await cur.execute("SHOW GRANTS FOR CURRENT_USER()")
            rows = await cur.fetchall()

        # rows 是 tuple 列表，每个元素是 (grant_stmt,)
        grants = [row[0] for row in rows if row and row[0]]

        logger.info(
            "MySQL 权限检测完成",
            host=host,
            port=port,
            user=user,
            database=database,
            grant_count=len(grants),
            trace_id=trace_id,
        )

        role = _parse_grants(grants, database)
        logger.info(
            "用户角色判定结果",
            host=host,
            user=user,
            database=database,
            role=role,
            trace_id=trace_id,
        )
        return role

    except Exception as exc:
        # SAFETY: 任何异常安全降级为 readonly
        logger.warning(
            "MySQL 权限检测异常，降级为 readonly",
            host=host,
            port=port,
            user=user,
            database=database,
            error=str(exc)[:200],
            trace_id=trace_id,
        )
        return "readonly"

    finally:
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()
