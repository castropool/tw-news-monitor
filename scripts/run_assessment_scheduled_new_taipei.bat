@echo off
setlocal
cd /d "%~dp0.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_assessment_scheduled_new_taipei.ps1" %*
exit /b %ERRORLEVEL%
