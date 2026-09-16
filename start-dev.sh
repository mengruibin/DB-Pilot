#!/usr/bin/env bash
# DB-Pilot 一键启动脚本（Git Bash / Linux / macOS）
# 用法：bash start-dev.sh   或   chmod +x start-dev.sh && ./start-dev.sh
# 说明：Ctrl+C 一次会同时停止前后端；前端已由 vite.config.ts 把 /api 代理到 :8000
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT_BACK=8000
PORT_FRONT=5173

echo "=================================================="
echo "  DB-Pilot 开发环境一键启动"
echo "=================================================="
echo "  后端 Backend  http://localhost:${PORT_BACK}   FastAPI reload"
echo "  前端 Frontend http://localhost:${PORT_FRONT}   Vite 开发服务器"
echo "--------------------------------------------------"
echo "  Ctrl+C 一次同时停止前后端"
echo "=================================================="
echo ""

PID_BACK=""
PID_FRONT=""
cleanup() {
    echo ""
    echo "[stop] 正在停止 DB-Pilot 前后端 ..."
    [ -n "$PID_BACK"  ] && kill "$PID_BACK"  2>/dev/null
    [ -n "$PID_FRONT" ] && kill "$PID_FRONT" 2>/dev/null
}
trap cleanup EXIT INT TERM

# ---------- 1) 后端：优先项目 .venv，缺失才回退 uv（本机 uv 有文件锁问题，用 --no-sync） ----------
(
    cd "$ROOT_DIR/backend" || exit 1
    if [ -x ".venv/Scripts/python.exe" ]; then        # Windows .venv
        exec ".venv/Scripts/python.exe" -m uvicorn app.main:app --reload --port "$PORT_BACK" --loop app.main:selector_loop_factory
    elif [ -x ".venv/bin/python" ]; then              # Linux/macOS .venv
        exec ".venv/bin/python" -m uvicorn app.main:app --reload --port "$PORT_BACK" --loop app.main:selector_loop_factory
    else
        exec uv run --no-sync uvicorn app.main:app --reload --port "$PORT_BACK" --loop app.main:selector_loop_factory
    fi
) &
PID_BACK=$!

# ---------- 2) 前端：Vite 开发服务器 ----------
(
    cd "$ROOT_DIR/frontend" || exit 1
    exec npm run dev
) &
PID_FRONT=$!

# ---------- 等后端端口就绪后给出访问提示（最长等 30s，等不到也直接放行） ----------
for _ in $(seq 1 30); do
    if (exec 3<>"/dev/tcp/127.0.0.1/$PORT_BACK") 2>/dev/null; then
        exec 3>&- 3<&-
        echo ""
        echo ">>> 前后端均已启动，请在浏览器打开 http://localhost:${PORT_FRONT}"
        break
    fi
    sleep 1
done

wait
