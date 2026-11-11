@echo off
cd /d "%~dp0"
.venv\Scripts\python.exe -m app.main --test-feishu-file
echo.
pause
exit /b %ERRORLEVEL%
