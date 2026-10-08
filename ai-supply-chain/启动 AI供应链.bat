@echo off
chcp 65001 >nul
title AI供应链 · 业务助理 —— 启动中
cd /d "%~dp0"

echo ============================================================
echo   AI供应链 · 业务助理  （本地演示）
echo ============================================================
echo.
echo   后端  http://127.0.0.1:8000
echo   前端  http://127.0.0.1:5173
echo.
echo   关闭本窗口即停止服务。
echo ============================================================
echo.

if not exist "backend\.venv\Scripts\python.exe" if not exist ".venv\Scripts\python.exe" (
  echo [错误] 找不到 Python 虚拟环境。
  echo        请确认本目录下有 .venv 或 backend\.venv
  pause
  exit /b 1
)

set PY=.venv\Scripts\python.exe
if not exist "%PY%" set PY=backend\.venv\Scripts\python.exe

echo [1/3] 启动后端 ...
set PYTHONPATH=%cd%\backend
start "AI供应链-后端" /min cmd /c ""%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

echo [2/3] 等待后端就绪 ...
set /a tries=0
:waitloop
set /a tries+=1
powershell -NoProfile -Command "try{Invoke-RestMethod http://127.0.0.1:8000/api/health -TimeoutSec 2 > $null; exit 0}catch{exit 1}" >nul 2>&1
if %errorlevel%==0 goto ready
if %tries% GEQ 40 (
  echo [警告] 后端 40 秒内没起来，仍继续启动前端。
  goto front
)
timeout /t 1 /nobreak >nul
goto waitloop

:ready
echo         后端已就绪。

:front
echo [3/3] 启动前端 ...
start "AI供应链-前端" /min cmd /c "cd /d "%cd%\frontend" && npm run dev"

echo.
echo 等待前端就绪 ...
set /a tries2=0
:wait2
set /a tries2+=1
powershell -NoProfile -Command "try{(Invoke-WebRequest http://127.0.0.1:5173/ -TimeoutSec 2 -UseBasicParsing) > $null; exit 0}catch{exit 1}" >nul 2>&1
if %errorlevel%==0 goto open
if %tries2% GEQ 60 (
  echo [警告] 前端 60 秒内没起来，请手动打开 http://127.0.0.1:5173/
  pause
  exit /b 1
)
timeout /t 1 /nobreak >nul
goto wait2

:open
echo         前端已就绪。
start "" http://127.0.0.1:5173/
echo.
echo 已在浏览器打开。登录账号见项目交接文档。
echo.
pause
