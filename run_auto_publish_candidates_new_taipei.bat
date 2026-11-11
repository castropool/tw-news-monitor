@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist "data\election_candidates\new_taipei_2026\logs" mkdir "data\election_candidates\new_taipei_2026\logs"
"%~dp0.venv\Scripts\python.exe" -m app.election_candidates.auto_review_orchestrator --config config/election_candidate_pipeline.new_taipei.yaml >> data\election_candidates\new_taipei_2026\logs\auto_publish_candidates.log 2>&1
endlocal & exit /b %ERRORLEVEL%
