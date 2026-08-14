@echo off
rem Stem Studio 一键启动（监听局域网, 同事用下方地址访问）
cd /d "%~dp0"
echo 局域网访问地址（发给同事）:
powershell -NoProfile -Command "Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object {$_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*'} | ForEach-Object { '   http://' + $_.IPAddress + ':8765' }"
echo.
start "Stem Studio Server" .venv\Scripts\python.exe -m uvicorn app:app --app-dir server --host 0.0.0.0 --port 8765
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:8765
