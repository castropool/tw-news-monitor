param(
    [string]$TaskName = "Tainan Election Fact Auto Publisher",
    [string]$ProductionDir = ""
)

$ErrorActionPreference = "Stop"
if (-not $ProductionDir) { $ProductionDir = Split-Path -Parent $PSScriptRoot }

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "任务不存在：$TaskName" -ForegroundColor Yellow
    exit 1
}

Write-Host "任务名称：$TaskName"
Write-Host "状态：$($task.State)"
$info = Get-ScheduledTaskInfo -TaskName $TaskName
if ($info.LastRunTime) { Write-Host "上次运行：$($info.LastRunTime)" } else { Write-Host "上次运行：(尚未运行)" }
if ($null -ne $info.LastTaskResult) { Write-Host "上次退出码：$($info.LastTaskResult)" }
if ($info.NextRunTime) { Write-Host "下次运行：$($info.NextRunTime)" } else { Write-Host "下次运行：(无)" }

$action = $task.Actions | Select-Object -First 1
if ($action) {
    Write-Host "执行命令：$($action.Execute) $($action.Arguments)"
    Write-Host "工作目录：$($action.WorkingDirectory)"
}

Write-Host ""
Write-Host "--- auto_publish_candidates.log tail ---"
$log = Join-Path $ProductionDir "data\election_candidates\tainan_2026\logs\auto_publish_candidates.log"
if (Test-Path -LiteralPath $log) {
    Get-Content -LiteralPath $log -Tail 10
} else {
    Write-Host "日志文件不存在：$log"
}
