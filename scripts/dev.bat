@echo off
REM 开发模式一键启动：FastAPI 后端 + Vite 前端（两个窗口）
REM 生产模式：cd frontend && npm run build 后仅运行 backend/run.py（同源托管）

start "AkaiRye Backend" cmd /k "cd /d %~dp0..\backend && python run.py"
start "AkaiRye Frontend" cmd /k "cd /d %~dp0..\frontend && npm run dev"
echo Backend:  http://127.0.0.1:8686/api/system/status
echo Frontend: http://localhost:5173/
