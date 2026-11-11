@echo off
cd /d "%~dp0"
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scheduled_task_status.ps1"
pause
