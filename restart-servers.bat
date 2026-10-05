@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "RFQ_ROOT=%~dp0"

rem Optional override: set RFQ_BACKEND_PYTHON=C:\path\to\python.exe
if defined RFQ_BACKEND_PYTHON (
    set "RFQ_PYTHON=%RFQ_BACKEND_PYTHON%"
) else (
    set "RFQ_PYTHON=python"
    if exist "%RFQ_ROOT%venv\Scripts\python.exe" set "RFQ_PYTHON=%RFQ_ROOT%venv\Scripts\python.exe"
    if exist "%RFQ_ROOT%.venv\Scripts\python.exe" set "RFQ_PYTHON=%RFQ_ROOT%.venv\Scripts\python.exe"
    if exist "%RFQ_ROOT%backend\venv\Scripts\python.exe" set "RFQ_PYTHON=%RFQ_ROOT%backend\venv\Scripts\python.exe"
    if exist "%RFQ_ROOT%backend\.venv\Scripts\python.exe" set "RFQ_PYTHON=%RFQ_ROOT%backend\.venv\Scripts\python.exe"
)

if /I "%~1"=="--backend" goto backend
if /I "%~1"=="--frontend" goto frontend

if not exist "%RFQ_ROOT%backend\app\main.py" goto wrong_folder
if not exist "%RFQ_ROOT%frontend\package.json" goto wrong_folder
where npm.cmd >nul 2>&1
if errorlevel 1 (
    echo ERROR: npm is missing. Install Node.js and reopen this window.
    goto failed
)
if not exist "%RFQ_ROOT%frontend\node_modules\vite\bin\vite.js" (
    echo ERROR: Frontend dependencies are missing. Run npm install in frontend first.
    goto failed
)
"%RFQ_PYTHON%" -c "import uvicorn" >nul 2>&1
if errorlevel 1 (
    echo ERROR: Backend Python or uvicorn is missing.
    echo Activate your backend environment or set RFQ_BACKEND_PYTHON to its python.exe.
    goto failed
)

echo Stopping processes listening on backend port 8000 and frontend port 5173...
rem Kill only owners of local listening sockets, including their reload child processes.
powershell.exe -NoProfile -Command "$ErrorActionPreference='Stop'; try { $owners=@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in @(8000,5173) } | Select-Object -ExpandProperty OwningProcess -Unique); foreach ($owner in $owners) { if (Get-Process -Id $owner -ErrorAction SilentlyContinue) { & taskkill.exe /PID $owner /T /F; if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $owner -ErrorAction SilentlyContinue)) { throw ('Cannot stop PID '+$owner+'. Run this batch file as administrator if needed.') } } }; $remaining=@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in @(8000,5173) }); if ($remaining.Count -gt 0) { throw 'A server port is still occupied.' } } catch { Write-Host $_.Exception.Message; exit 1 }"
if errorlevel 1 goto failed

start "RFQ Backend - 8000" cmd.exe /k ""%~f0" --backend"
start "RFQ Frontend - 5173" cmd.exe /k ""%~f0" --frontend"
echo.
echo Both server windows have been opened. Check them for startup errors.
echo Frontend: http://localhost:5173
echo Backend:  http://localhost:8000/docs
exit /b 0

:backend
cd /d "%RFQ_ROOT%backend"
"%RFQ_PYTHON%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
exit /b %errorlevel%

:frontend
cd /d "%RFQ_ROOT%frontend"
call npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort
exit /b %errorlevel%

:wrong_folder
echo ERROR: Keep restart-servers.bat in the repository root beside backend and frontend.
:failed
echo Restart failed. Existing servers are only stopped after dependency checks pass.
pause
exit /b 1
