param(
    [string]$TaskName = "Tainan Election Candidate Monitor",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "任务不存在：$TaskName"
    exit 0
}
if (-not $Force) {
    Write-Host "将删除计划任务：$TaskName"
    Write-Host "使用 -Force 确认删除。"
    exit 1
}
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "已删除：$TaskName"
