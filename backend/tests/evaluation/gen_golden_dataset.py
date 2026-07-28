"""
Text-to-SQL 测评集验证与 Golden Dataset 构建脚本。

功能：
  1. 读取 LLM 生成的原始测评用例 JSON
  2. 连接 MySQL 数据库，逐条执行 SQL 验证语法合法性
  3. 自动捕获查询结果作为 expected_result / row_count
  4. 危险写操作（DELETE/UPDATE/TRUNCATE/DROP/ALTER…）跳过真实执行
  5. 输出 golden_dataset.json，失败用例单独保存供 LLM 修正反馈

用法：
    cd docs
    python gen_golden_dataset.py [输入文件] [输出文件]

    默认输入：text_to_sql_evaluation.json
    默认输出：golden_dataset.json

环境变量（也可在 backend/.env 中配置）：
    DB_HOST      - 数据库主机（默认 127.0.0.1）
    DB_PORT      - 数据库端口（默认 3306）
    DB_USER      - 用户名（默认 root）
    DB_PASSWORD  - 密码
    DB_NAME      - 数据库名（默认 test）
"""

from __future__ import annotations

import asyncio
import datetime
import decimal
import json
import os
import sys
from pathlib import Path
from typing import Any

# =============================================================================
# 加载 .env 配置（复用项目 backend/.env，避免额外配置）
# =============================================================================
try:
    from dotenv import load_dotenv

    _ENV_PATH = Path(__file__).resolve().parent.parent / "backend" / ".env"
    if _ENV_PATH.exists():
        load_dotenv(_ENV_PATH)
except ImportError:
    pass  # python-dotenv 未安装时忽略，靠系统环境变量

# =============================================================================
# 数据库连接配置
# =============================================================================
DB_CONFIG: dict[str, Any] = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", "123456"),
    "db": os.getenv("DB_NAME", "shopping"),
}

# =============================================================================
# 危险 SQL 前缀 —— 匹配到即跳过真实执行
# =============================================================================
_DANGEROUS_PREFIXES: tuple[str, ...] = (
    "DELETE", "UPDATE", "INSERT", "TRUNCATE", "DROP",
    "ALTER", "CREATE", "GRANT", "REVOKE", "RENAME",
    "REPLACE", "LOAD",
)


def _is_dangerous(sql: str) -> bool:
    """判断 SQL 是否为危险写操作，不应在真实数据库执行。"""
    stripped = sql.strip().upper()
    return any(
        stripped.startswith(kw + " ") or stripped.startswith(kw + "\t") or stripped == kw
        for kw in _DANGEROUS_PREFIXES
    )


def _serialize_value(val: Any) -> Any:
    """将数据库返回值转为 JSON 可序列化类型。"""
    if val is None:
        return None
    if isinstance(val, datetime.datetime):
        return val.isoformat()
    if isinstance(val, datetime.date):
        return val.isoformat()
    if isinstance(val, datetime.timedelta):
        return str(val)
    if isinstance(val, decimal.Decimal):
        return float(val)
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    # set / memoryview 等不可序列化类型 → 转字符串兜底
    if isinstance(val, (set, frozenset, memoryview, bytearray)):
        return str(val)
    return val


# =============================================================================
# 核心验证逻辑
# =============================================================================


async def validate_cases(
    raw_cases: list[dict],
    pool: Any,
) -> tuple[list[dict], list[dict]]:
    """逐条执行 SQL 并捕获预期结果。

    Args:
        raw_cases: LLM 生成的原始测评用例列表。
        pool: aiomysql 异步连接池。

    Returns:
        (validated, failed): 验证通过的用例（含 expected_result）和失败用例列表。
    """
    validated: list[dict] = []
    failed: list[dict] = []

    async with pool.acquire() as conn:
        for case in raw_cases:
            case_id = case.get("id", "?")
            question = case.get("question", "")[:60]
            sql = case.get("sql", "").strip().rstrip(";")

            if not sql:
                failed.append({"case_id": case_id, "question": question, "error": "SQL 为空"})
                print(f"  ❌ [{case_id}] SQL 为空")
                continue

            # ---------------------------------------------------------------
            # 危险写操作：跳过真实执行，仅保留用例（不执行、不回滚风险）
            # ---------------------------------------------------------------
            if _is_dangerous(sql):
                case["expected_result"] = None
                case["row_count"] = None
                case["_validation"] = "skipped_dangerous"
                validated.append(case)
                print(f"  ⏭️  [{case_id}] 跳过危险操作: {sql[:70]}...")
                continue

            # ---------------------------------------------------------------
            # 正常执行 & 捕获结果
            # ---------------------------------------------------------------
            try:
                async with conn.cursor() as cur:
                    await cur.execute(sql)

                    if cur.description:
                        rows = await cur.fetchall()
                        columns = [desc[0] for desc in cur.description]
                        result = [
                            {col: _serialize_value(val) for col, val in zip(columns, row)}
                            for row in rows
                        ]
                    else:
                        result = []
                        columns = []

                case["expected_result"] = result
                case["row_count"] = len(result)
                case["_validation"] = "executed"
                validated.append(case)
                print(f"  ✅ [{case_id}] 通过 ({len(result)} 行): {question}")

            except Exception as exc:
                failed.append({
                    "case_id": case_id,
                    "question": question,
                    "sql": sql,
                    "error": str(exc),
                })
                print(f"  ❌ [{case_id}] 失败: {str(exc)[:100]}")

    return validated, failed


# =============================================================================
# 主入口
# =============================================================================


async def main() -> None:
    """主入口：连接数据库 → 验证用例 → 输出 golden dataset。"""
    # -------- 命令行参数 --------
    script_dir = Path(__file__).resolve().parent
    input_name = sys.argv[1] if len(sys.argv) > 1 else "text_to_sql_evaluation.json"
    output_name = sys.argv[2] if len(sys.argv) > 2 else "golden_dataset.json"

    input_path = script_dir / input_name
    output_path = script_dir / output_name

    # -------- 读取输入 --------
    if not input_path.exists():
        print(f"❌ 输入文件不存在: {input_path}")
        print(f"   用法: python {Path(__file__).name} [输入JSON] [输出JSON]")
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        raw_cases = json.load(f)

    if not isinstance(raw_cases, list):
        print("❌ 输入文件格式错误：顶层应为 JSON 数组")
        sys.exit(1)

    print(f"📂 读取用例: {len(raw_cases)} 条")
    print(
        f"🔗 连接数据库: mysql://{DB_CONFIG['user']}@{DB_CONFIG['host']}"
        f":{DB_CONFIG['port']}/{DB_CONFIG['db']}"
    )

    # -------- 连接数据库 --------
    import aiomysql

    pool = None
    try:
        pool = await aiomysql.create_pool(
            host=DB_CONFIG["host"],
            port=DB_CONFIG["port"],
            user=DB_CONFIG["user"],
            password=DB_CONFIG["password"],
            db=DB_CONFIG["db"],
            autocommit=True,
            maxsize=5,
            minsize=1,
            charset="utf8mb4",
        )

        # 预检：目标表是否存在
        async with pool.acquire() as conn, conn.cursor() as cur:
            await cur.execute("SHOW TABLES LIKE 'orders'")
            if not await cur.fetchone():
                print("⚠️  警告：数据库中未找到 'orders' 表，大部分查询将失败。")
                print("   请先执行 docs/shopping.sql 或手动建表并导入数据后再运行本脚本。")

        # -------- 逐条验证 --------
        validated, failed = await validate_cases(raw_cases, pool)

    except Exception as exc:
        print(f"❌ 数据库连接失败: {exc}")
        print("   请检查环境变量: DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME")
        print("   也可在 backend/.env 中配置目标库连接信息。")
        sys.exit(1)
    finally:
        if pool is not None:
            pool.close()
            await pool.wait_closed()

    # -------- 汇总 --------
    danger_count = sum(1 for c in validated if c.get("_validation") == "skipped_dangerous")
    executed_count = sum(1 for c in validated if c.get("_validation") == "executed")

    print(f"\n{'=' * 55}")
    print(f"✅ 执行验证通过: {executed_count} 条")
    print(f"⏭️  跳过危险操作: {danger_count} 条")
    print(f"❌ 验证失败:     {len(failed)} 条")

    # -------- 输出 --------
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(validated, f, ensure_ascii=False, indent=2)
    print(f"\n📝 Golden Dataset 已写入: {output_path}")

    if failed:
        failed_path = script_dir / "failed_cases.json"
        with open(failed_path, "w", encoding="utf-8") as f:
            json.dump(failed, f, ensure_ascii=False, indent=2)
        print(f"⚠️  失败用例已保存: {failed_path}")
        print(f'   → 可提示 LLM："以下 SQL 执行报错，请修正失败用例"{failed_path}"')


if __name__ == "__main__":
    asyncio.run(main())
