$TaskName = "Taiwan News Monitor"
$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $Task) {
    Write-Host "任务不存在：$TaskName，无需卸载。" -ForegroundColor Yellow
    exit 0
}
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "任务已卸载：$TaskName" -ForegroundColor Green
Write-Host "项目文件、.env、news.db、Word报告和日志均未删除。"
