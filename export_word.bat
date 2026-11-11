@echo off
cd /d "%~dp0"
.venv\Scripts\python.exe -m app.main --export-word 30
echo.
pause
exit /b %ERRORLEVEL%
