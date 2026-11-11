@echo off
cd /d "%~dp0"
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall_scheduled_task.ps1"
pause
