$TaskName = "Taiwan News Monitor"
$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $Task) {
    Write-Host "任务不存在：$TaskName，请先运行 install_task.bat" -ForegroundColor Yellow
    exit 1
}
Start-ScheduledTask -TaskName $TaskName
Write-Host "任务已启动：$TaskName" -ForegroundColor Green
$Info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host "当前状态：$($Task.State)"
Write-Host ""
Write-Host "请检查："
Write-Host "  1. data\monitor.log 查看运行结果"
Write-Host "  2. 手机飞书群确认是否收到新闻简报和Word附件"
