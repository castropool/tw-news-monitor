$ErrorActionPreference = "Stop"
$taskName = "Tainan Election Assessment"
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "已卸载：$taskName"
} else {
    Write-Host "任务不存在：$taskName"
}
