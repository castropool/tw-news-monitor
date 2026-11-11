$TaskName = "Taiwan News Monitor"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogPath = Join-Path $ProjectDir "data\monitor.log"

$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if (-not $Task) {
    Write-Host "任务不存在：$TaskName" -ForegroundColor Yellow
    exit 1
}

Write-Host "任务名称：$TaskName"
Write-Host "状态：$($Task.State)"

$Info = Get-ScheduledTaskInfo -TaskName $TaskName
if ($Info.LastRunTime) {
    Write-Host "上次运行：$($Info.LastRunTime)"
} else { Write-Host "上次运行：(尚未运行)" }
if ($Info.LastTaskResult -ne $null) {
    Write-Host "上次结果：$($Info.LastTaskResult)"
}
if ($Info.NextRunTime) {
    Write-Host "下次运行：$($Info.NextRunTime)"
}

$Action = $Task.Actions | Select-Object -First 1
if ($Action) {
    Write-Host "执行命令：$($Action.Execute) $($Action.Arguments)"
}

Write-Host ""
Write-Host "=== 最新日志 ==="
if (Test-Path $LogPath) {
    Get-Content $LogPath -Tail 10 -Encoding UTF8
} else {
    Write-Host "(日志文件不存在)"
}
