param(
    [string]$TaskName = "Tainan Election Candidate Monitor",
    [string]$ProductionDir = ""
)

$ErrorActionPreference = "Stop"
if (-not $ProductionDir) { $ProductionDir = Split-Path -Parent $PSScriptRoot }
schtasks.exe /query /tn $TaskName /v /fo LIST
Write-Host ""
$log = Join-Path $ProductionDir "data\election_candidates\tainan_2026\logs\candidate_monitor.log"
if (Test-Path -LiteralPath $log) {
    Write-Host "--- candidate_monitor.log tail ---"
    Get-Content -LiteralPath $log -Tail 10
} else {
    Write-Host "candidate_monitor.log 不存在：$log"
}
