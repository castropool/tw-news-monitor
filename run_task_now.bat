@echo off
cd /d "%~dp0"
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_scheduled_task_now.ps1"
pause
