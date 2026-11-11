@echo off
setlocal

cd /d "%~dp0"
if not exist "data" mkdir "data"

chcp 65001 >nul

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo [%date% %time%] ERROR: project Python not found: "%PYTHON_EXE%" >> "data\monitor.log"
    exit /b 9009
)

rem Ensure the local Command Code summarizer gateway is up (best-effort).
"%PYTHON_EXE%" scripts\ensure_cmd_gateway.py

"%PYTHON_EXE%" -m app.main %*
set "EXIT_CODE=%ERRORLEVEL%"

endlocal & exit /b %EXIT_CODE%
