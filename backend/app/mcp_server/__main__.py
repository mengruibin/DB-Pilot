"""python -m app.mcp_server 入口：拉起 DB-Pilot MCP（stdio）。

Windows：与 app/main.py 一致使用 SelectorEventLoop（psycopg 异步兼容）。
"""

from __future__ import annotations

import asyncio
import sys


def main() -> None:
    """MCP stdio 服务主入口。"""
    # 先加载 backend/.env（connections/policy 从 os.environ 读 env DSN；
    # 真实进程 env 优先，load_dotenv 默认不覆盖已存在的变量）。
    from dotenv import load_dotenv

    load_dotenv()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    from app.mcp_server.server import run_server

    run_server()


if __name__ == "__main__":
    main()
