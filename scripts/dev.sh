#!/usr/bin/env bash
# 开发模式一键启动（Git Bash / 通用 shell）
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
(cd "$ROOT/backend" && python run.py) &
(cd "$ROOT/frontend" && npm run dev) &
echo "Backend:  http://127.0.0.1:8686/api/system/status"
echo "Frontend: http://localhost:5173/"
wait
