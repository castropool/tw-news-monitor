$ErrorActionPreference = "Continue"
$taskName = "Tainan Election Assessment"
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "STATUS=NOT_INSTALLED"
    exit 2
}
$info = Get-ScheduledTaskInfo -TaskName $taskName
Write-Host "STATUS=$($task.State)"
Write-Host "LAST_RUN=$($info.LastRunTime)"
Write-Host "LAST_RESULT=$($info.LastTaskResult)"
Write-Host "NEXT_RUN=$($info.NextRunTime)"
