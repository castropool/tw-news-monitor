@echo off
setlocal

cd /d "%~dp0"

chcp 65001 >nul

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo [%date% %time%] ERROR: project Python not found: "%PYTHON_EXE%"
    exit /b 9009
)

"%PYTHON_EXE%" -m app.cmd_gateway %*
set "EXIT_CODE=%ERRORLEVEL%"

endlocal & exit /b %EXIT_CODE%
