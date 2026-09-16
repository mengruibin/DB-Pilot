@echo off
chcp 65001 >nul
setlocal EnableExtensions DisableDelayedExpansion

title DB-Pilot 一键启动

REM ============================================================
REM  子进程角色分派：父进程通过环境变量 DBPILOT_ROLE 指定
REM  本文件可同时充当父进程（无 ROLE）与子进程（有 ROLE）
REM ============================================================
if "%DBPILOT_ROLE%"=="backend"  goto run_backend
if "%DBPILOT_ROLE%"=="frontend" goto run_frontend

echo.
echo  ==================================================
echo    DB-Pilot 开发环境一键启动
echo  ==================================================
echo    后端 Backend  http://localhost:8000   FastAPI reload
echo    前端 Frontend http://localhost:5173   Vite 开发服务器
echo  --------------------------------------------------
echo    直接关闭对应窗口即可停止对应服务
echo  ==================================================
echo.

REM ---- 在独立窗口启动后端 ----
set "DBPILOT_ROLE=backend"
start "DB-Pilot Backend :8000" cmd /k ""%~f0""

REM ---- 在独立窗口启动前端 ----
set "DBPILOT_ROLE=frontend"
start "DB-Pilot Frontend :5173" cmd /k ""%~f0""

REM ---- 稍候自动打开浏览器（没弹开就手动访问 http://localhost:5173）----
timeout /t 4 /nobreak >nul
start "" "http://localhost:5173"
exit /b

REM ============================================================
REM  后端：优先项目 .venv，缺失才回退 uv（本机 uv 有文件锁问题，用 --no-sync）
REM ============================================================
:run_backend
cd /d "%~dp0backend"
echo [Backend] 正在启动 FastAPI，监听 http://localhost:8000 ...
if exist ".venv\Scripts\python.exe" (
    echo [Backend] 使用项目 .venv 的 Python 运行 uvicorn
    ".venv\Scripts\python.exe" -m uvicorn app.main:app --reload --port 8000 --loop app.main:selector_loop_factory
) else (
    echo [Backend] 未找到 .venv，改用 uv run --no-sync 启动
    uv run --no-sync uvicorn app.main:app --reload --port 8000 --loop app.main:selector_loop_factory
)
echo [Backend] 后端已退出。窗口保持打开以便查看日志，直接关闭即可。
exit /b

REM ============================================================
REM  前端：Vite 开发服务器（/api 已由 vite.config.ts 代理到 :8000）
REM ============================================================
:run_frontend
cd /d "%~dp0frontend"
echo [Frontend] 正在启动 Vite，监听 http://localhost:5173 ...
call npm run dev
echo [Frontend] 前端已退出。窗口保持打开以便查看日志，直接关闭即可。
exit /b
