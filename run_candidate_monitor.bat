@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
"%~dp0.venv\Scripts\python.exe" -m app.election_candidates.build_candidate_queue --since-last-success >> data\election_candidates\tainan_2026\logs\candidate_monitor.log 2>&1
endlocal & exit /b %ERRORLEVEL%
