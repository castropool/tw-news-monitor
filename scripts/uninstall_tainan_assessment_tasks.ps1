$ErrorActionPreference = "Stop"
$taskNames = @(
    "Taiwan Election Assessment - Day 9",
    "Taiwan Election Assessment - Day 22"
)

foreach ($name in $taskNames) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "任务不存在：$name（跳过）" -ForegroundColor Yellow
        continue
    }
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Write-Host "任务已卸载：$name" -ForegroundColor Green
}
Write-Host "其他计划任务不受影响。"
