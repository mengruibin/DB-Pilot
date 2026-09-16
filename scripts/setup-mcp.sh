#!/usr/bin/env bash
# setup-mcp.sh - DB-Pilot MCP local setup (macOS / Linux)
# Usage: bash scripts/setup-mcp.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"

echo '== DB-Pilot MCP setup =='

if ! command -v uv >/dev/null 2>&1; then
  echo '[ERROR] uv not found. Install: https://docs.astral.sh/uv/' >&2
  exit 1
fi
echo "[OK] uv: $(uv --version)"

PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
MAJOR="${PY_VER%%.*}"
MINOR="${PY_VER#*.}"
if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 12 ]; }; then
  echo "[ERROR] python >= 3.12 required, found $PY_VER" >&2
  exit 1
fi
echo "[OK] python: $PY_VER"

(cd "$BACKEND_DIR" && uv sync --extra mcp)
echo '[OK] dependencies synced'

if [ ! -f "$BACKEND_DIR/.env" ]; then
  cp "$BACKEND_DIR/.env.example" "$BACKEND_DIR/.env"
  echo '[OK] created backend/.env from .env.example'
else
  echo '[OK] backend/.env already exists'
fi

TOOLS="$(cd "$BACKEND_DIR" && uv run python -c 'from app.mcp_server.schema import TOOL_META; print(len(TOOL_META))')"
echo "[OK] mcp package loads; tools: $TOOLS"

echo
echo 'Next steps:'
echo '  1) Edit backend/.env, add your DSN:'
echo '     DBPILOT_DSN=mysql://user:pass@127.0.0.1:3306/shop   (single)'
echo '     DBPILOT_CONNECTIONS={...}                          (multi)'
echo '     DBPILOT_HEADLESS=1'
echo '  2) Register in Claude Code/Codex (see docs/mcp-usage.md)'
echo '  3) Verify: ask "dbpilot_list_connections to list databases"'
