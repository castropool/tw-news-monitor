@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist "data\election_candidates\new_taipei_2026\logs" mkdir "data\election_candidates\new_taipei_2026\logs"
"%~dp0.venv\Scripts\python.exe" -m app.election_candidates.build_candidate_queue --config config/election_candidate_pipeline.new_taipei.yaml --since-last-success >> data\election_candidates\new_taipei_2026\logs\candidate_monitor.log 2>&1
endlocal & exit /b %ERRORLEVEL%
