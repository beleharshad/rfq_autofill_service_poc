@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "RFQ_ROOT=%~dp0"
rem Repair only this process and its children; do not change the user's saved PATH.
set "RFQ_SYSTEM=%SystemRoot%\System32"
set "RFQ_POWERSHELL=%RFQ_SYSTEM%\WindowsPowerShell\v1.0\powershell.exe"
set "RFQ_CMD=%RFQ_SYSTEM%\cmd.exe"
set "RFQ_TASKKILL=%RFQ_SYSTEM%\taskkill.exe"
set "PATH=%RFQ_SYSTEM%;%SystemRoot%;%RFQ_SYSTEM%\Wbem;%RFQ_SYSTEM%\WindowsPowerShell\v1.0;%PATH%"

rem Optional override: set RFQ_BACKEND_PYTHON=C:\path\to\python.exe
set "RFQ_PYTHON=python"
if exist "%RFQ_ROOT%venv\Scripts\python.exe" set "RFQ_PYTHON=%RFQ_ROOT%venv\Scripts\python.exe"
if exist "%RFQ_ROOT%.venv\Scripts\python.exe" set "RFQ_PYTHON=%RFQ_ROOT%.venv\Scripts\python.exe"
if exist "%RFQ_ROOT%backend\venv\Scripts\python.exe" set "RFQ_PYTHON=%RFQ_ROOT%backend\venv\Scripts\python.exe"
if exist "%RFQ_ROOT%backend\.venv\Scripts\python.exe" set "RFQ_PYTHON=%RFQ_ROOT%backend\.venv\Scripts\python.exe"
if defined CONDA_PREFIX if exist "%CONDA_PREFIX%\python.exe" set "RFQ_PYTHON=%CONDA_PREFIX%\python.exe"
if defined RFQ_BACKEND_PYTHON set "RFQ_PYTHON=%RFQ_BACKEND_PYTHON%"

rem Optional override: set RFQ_NPM=C:\Program Files\nodejs\npm.cmd
if not defined RFQ_NPM (
    for %%I in (npm.cmd) do set "RFQ_NPM=%%~$PATH:I"
)
if not defined RFQ_NPM if exist "%ProgramFiles%\nodejs\npm.cmd" set "RFQ_NPM=%ProgramFiles%\nodejs\npm.cmd"
if not defined RFQ_NPM if defined ProgramFiles(x86) if exist "%ProgramFiles(x86)%\nodejs\npm.cmd" set "RFQ_NPM=%ProgramFiles(x86)%\nodejs\npm.cmd"
if not defined RFQ_NPM if exist "%LocalAppData%\Volta\bin\npm.cmd" set "RFQ_NPM=%LocalAppData%\Volta\bin\npm.cmd"
if not defined RFQ_NPM if exist "%AppData%\nvm\npm.cmd" set "RFQ_NPM=%AppData%\nvm\npm.cmd"

rem npm's child processes also need the selected Node installation on PATH.
if defined RFQ_NPM for %%I in ("%RFQ_NPM%") do set "PATH=%%~dpI;%PATH%"

if /I "%~1"=="--backend" goto backend
if /I "%~1"=="--frontend" goto frontend

if not exist "%RFQ_POWERSHELL%" (
    echo ERROR: Windows PowerShell is missing at "%RFQ_POWERSHELL%".
    goto failed
)
if not exist "%RFQ_CMD%" goto missing_system_tools
if not exist "%RFQ_TASKKILL%" goto missing_system_tools
"%RFQ_POWERSHELL%" -NoProfile -NonInteractive -Command "Get-Command Get-NetTCPConnection -ErrorAction Stop | Out-Null" >nul 2>&1
if errorlevel 1 (
    echo ERROR: Windows PowerShell could not load Get-NetTCPConnection.
    goto failed
)
if not exist "%RFQ_ROOT%backend\app\main.py" goto wrong_folder
if not exist "%RFQ_ROOT%frontend\package.json" goto wrong_folder
if not defined RFQ_NPM (
    echo ERROR: npm was not found.
    echo Install Node.js LTS from https://nodejs.org, then close and reopen PowerShell.
    echo If Node.js is already installed, set RFQ_NPM to the full path of npm.cmd and run this file again.
    goto failed
)
if not exist "%RFQ_ROOT%frontend\node_modules\vite\bin\vite.js" (
    echo ERROR: Frontend dependencies are missing. Run npm install in frontend first.
    goto failed
)
call "%RFQ_NPM%" --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: npm could not start. Check RFQ_NPM and your Node.js installation.
    goto failed
)
node --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Node.js could not start. Add its installation directory to PATH.
    goto failed
)
pushd "%RFQ_ROOT%backend"
"%RFQ_PYTHON%" -c "import uvicorn; import app.main"
set "RFQ_IMPORT_STATUS=%errorlevel%"
popd
if not "%RFQ_IMPORT_STATUS%"=="0" goto backend_dependency_failed
if /I "%~1"=="--check" (
    echo Preflight passed. No servers were stopped or started.
    exit /b 0
)
goto restart

:backend_dependency_failed
echo ERROR: Backend imports failed. See the Python error above.
echo Activate the API environment or set RFQ_BACKEND_PYTHON to its python.exe.
goto failed

:restart
echo Stopping processes listening on backend port 8000 and frontend port 5173...
rem Kill only owners of local listening sockets, including their reload child processes.
"%RFQ_POWERSHELL%" -NoProfile -NonInteractive -Command "$ErrorActionPreference='Stop'; try { $owners=@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in @(8000,5173) } | Select-Object -ExpandProperty OwningProcess -Unique); foreach ($owner in $owners) { if (Get-Process -Id $owner -ErrorAction SilentlyContinue) { & $env:RFQ_TASKKILL /PID $owner /T /F; if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $owner -ErrorAction SilentlyContinue)) { throw ('Cannot stop PID '+$owner+'. Run this batch file as administrator if needed.') } } }; $remaining=@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in @(8000,5173) }); if ($remaining.Count -gt 0) { throw 'A server port is still occupied.' } } catch { Write-Host $_.Exception.Message; exit 1 }"
if errorlevel 1 goto failed

start "RFQ Backend - 8000" "%RFQ_CMD%" /k ""%~f0" --backend"
start "RFQ Frontend - 5173" "%RFQ_CMD%" /k ""%~f0" --frontend"
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
call "%RFQ_NPM%" run dev -- --host 127.0.0.1 --port 5173 --strictPort
exit /b %errorlevel%

:missing_system_tools
echo ERROR: Windows cmd.exe or taskkill.exe is missing from "%RFQ_SYSTEM%".
goto failed

:wrong_folder
echo ERROR: Keep restart-servers.bat in the repository root beside backend and frontend.
:failed
echo Restart failed. Check the error above; server startup is not confirmed.
pause
exit /b 1
