param(
    [string]$TaskName = "Tainan Election Candidate Monitor"
)

$ErrorActionPreference = "Stop"
schtasks.exe /run /tn $TaskName
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Start-Sleep -Seconds 3
schtasks.exe /query /tn $TaskName /v /fo LIST
