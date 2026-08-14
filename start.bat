@echo off
rem Stem Studio 一键启动
cd /d "%~dp0"
start "Stem Studio Server" .venv\Scripts\python.exe -m uvicorn app:app --app-dir server --host 127.0.0.1 --port 8765
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:8765
