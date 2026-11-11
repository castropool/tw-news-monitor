param(
    [switch]$SkipMain
)

$ErrorActionPreference = "Stop"

function Get-TainanTaskStatusText {
    param(
        [object[]]$Tasks,
        [object[]]$Infos,
        [string[]]$Names
    )
    $lines = @()
    $missing = 0
    for ($i = 0; $i -lt $Names.Count; $i++) {
        $name = $Names[$i]
        $task = $Tasks[$i]
        $info = $Infos[$i]
        if (-not $task) {
            $lines += "任务不存在：$name"
            $missing++
            continue
        }
        $lines += "任务名称：$name"
        $lines += "状态：$($task.State)"
        if ($info.LastRunTime) {
        $lines += "上次运行：$($info.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss'))"
        } else {
            $lines += "上次运行：(尚未运行)"
        }
        $lines += "上次退出码：$($info.LastTaskResult)"
        if ($info.NextRunTime) {
        $lines += "下次运行：$($info.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss'))"
        } else {
            $lines += "下次运行：(未计划)"
        }
        $firstAction = $task.Actions | Select-Object -First 1
        if ($firstAction) {
            $lines += "执行命令：$($firstAction.Execute) $($firstAction.Arguments)"
            $lines += "工作目录：$($firstAction.WorkingDirectory)"
        }
        $lines += ""
    }
    return @{ Lines = $lines; Missing = $missing }
}

function Show-TainanTaskStatuses {
    param(
        [object[]]$Tasks,
        [object[]]$Infos,
        [string[]]$Names
    )
    $result = Get-TainanTaskStatusText -Tasks $Tasks -Infos $Infos -Names $Names
    foreach ($line in $result.Lines) {
        Write-Host $line
    }
    return $result.Missing
}

if ($SkipMain) {
    return
}

$taskNames = @(
    "Taiwan Election Assessment - Day 9",
    "Taiwan Election Assessment - Day 22"
)
$tasks = @()
$infos = @()
foreach ($name in $taskNames) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $task) {
        $tasks += $null
        $infos += $null
        continue
    }
    $tasks += $task
    $infos += (Get-ScheduledTaskInfo -TaskName $name)
}
$missing = Show-TainanTaskStatuses -Tasks $tasks -Infos $infos -Names $taskNames
if ($missing -gt 0) {
    exit 1
}
exit 0
