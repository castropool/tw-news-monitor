$ErrorActionPreference = "Stop"
$TaskName = "Taiwan News Monitor"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BatPath = Join-Path $ProjectDir "run_monitor.bat"
if (-not (Test-Path $BatPath)) { throw "Not found: $BatPath" }
$EnvFile = Join-Path $ProjectDir ".env"
$ConfigOk = $true
if (-not (Test-Path $EnvFile)) {
    Write-Host "[WARN] .env not found" -ForegroundColor Yellow
} else {
    $content = Get-Content $EnvFile -Raw -Encoding UTF8
    if ($content -match "FEISHU_APP_ID=") { Write-Host "FEISHU_APP_ID: OK" -ForegroundColor Green }
    else { Write-Host "FEISHU_APP_ID: MISSING" -ForegroundColor Red; $ConfigOk = $false }
    if ($content -match "FEISHU_APP_SECRET=") { Write-Host "FEISHU_APP_SECRET: OK" -ForegroundColor Green }
    else { Write-Host "FEISHU_APP_SECRET: MISSING" -ForegroundColor Red; $ConfigOk = $false }
    if ($content -match "FEISHU_CHAT_ID=") { Write-Host "FEISHU_CHAT_ID: OK" -ForegroundColor Green }
    else { Write-Host "FEISHU_CHAT_ID: MISSING" -ForegroundColor Red; $ConfigOk = $false }
}
if (-not $ConfigOk) { throw "Feishu config incomplete" }
$Action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"`"$BatPath`"`"" -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 30) 
$Settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -StartWhenAvailable -RunOnlyIfNetworkAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$UserId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description "Monitor" -Force
Write-Host ""
Write-Host "Task installed" -ForegroundColor Green
Write-Host "  Name: $TaskName" -ForegroundColor Green
Write-Host "  Bat:  $BatPath" -ForegroundColor Green
Write-Host "  Next: ~1 minute" -ForegroundColor Green
Write-Host "  Repeat: 30 min" -ForegroundColor Green
