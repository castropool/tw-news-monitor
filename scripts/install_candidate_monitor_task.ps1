param(
    [switch]$DryRun,
    [switch]$Force,
    [string]$ProductionDir = "",
    [string]$TaskName = "Tainan Election Candidate Monitor",
    [ValidateSet("tainan", "new_taipei")]
    [string]$Election = "tainan",
    [string]$StartMinute = ""
)

$ErrorActionPreference = "Stop"
if (-not $ProductionDir) { $ProductionDir = Split-Path -Parent $PSScriptRoot }
$runner = Join-Path $ProductionDir "run_candidate_monitor.bat"
$logDir = Join-Path $ProductionDir "data\election_candidates\tainan_2026\logs"
$description = "台南选情候选事实监控 - 每30分钟增量生成候选（独立于新闻采集）"
if ($Election -eq "new_taipei") {
    if ($TaskName -eq "Tainan Election Candidate Monitor") {
        $TaskName = "New Taipei Election Candidate Monitor"
    }
    $runner = Join-Path $ProductionDir "run_candidate_monitor_new_taipei.bat"
    $logDir = Join-Path $ProductionDir "data\election_candidates\new_taipei_2026\logs"
    $description = "新北选情候选事实监控 - 每30分钟增量生成候选（独立于新闻采集）"
}

function Get-TaskStartMinute([string]$Task) {
    $out = & schtasks.exe /query /tn $Task /v /fo LIST 2>$null
    foreach ($line in $out) {
        if ($line -match "Next Run Time:\s+(\d{4}/\d{1,2}/\d{1,2}\s+(\d{1,2}):(\d{2}):(\d{2}))") {
            return [int]$Matches[3]
        }
        if ($line -match "Start Time:\s+(\d{1,2}):(\d{2}):(\d{2})") {
            return [int]$Matches[2]
        }
    }
    return $null
}

$monitorMinute = Get-TaskStartMinute "Taiwan News Monitor"
$pipelineMinute = Get-TaskStartMinute "Taiwan News Event Pipeline"
Write-Host "Taiwan News Monitor 启动分钟：$monitorMinute"
Write-Host "Taiwan News Event Pipeline 启动分钟：$pipelineMinute"

if (-not $StartMinute) {
    if ($null -eq $monitorMinute) {
        $StartMinute = "05"
    } else {
        $StartMinute = (($monitorMinute + 5) % 30).ToString("00")
        $taken = @($monitorMinute, $pipelineMinute) | Where-Object { $null -ne $_ }
        $collides = $false
        foreach ($m in $taken) {
            $delta = [Math]::Abs(([int]$StartMinute - ($m % 30) + 30) % 30)
            if ($delta -le 3 -or $delta -ge 27) { $collides = $true }
        }
        if ($collides) {
            $StartMinute = (([int]$StartMinute + 10) % 30).ToString("00")
        }
    }
}
Write-Host "候选监控启动分钟（错峰）：$StartMinute"

if (-not (Test-Path -LiteralPath $runner)) {
    throw "Not found: $runner"
}
if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
}

$now = Get-Date
if ($StartMinute.Length -eq 2) {
    $start = Get-Date -Hour 0 -Minute ([int]$StartMinute) -Second 0
} else {
    $start = Get-Date -Hour ([int]$StartMinute.Substring(0,2)) -Minute ([int]$StartMinute.Substring(2,2)) -Second 0
}
while ($start -le $now) { $start = $start.AddMinutes(30) }
$startBoundary = $start.ToString("yyyy-MM-ddTHH:mm:ss")

$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$xmlUser = [System.Security.SecurityElement]::Escape($userId)
$xmlRunner = [System.Security.SecurityElement]::Escape($runner)
$xmlDir = [System.Security.SecurityElement]::Escape($ProductionDir)
$description = [System.Security.SecurityElement]::Escape($description)
$xmlArgs = [System.Security.SecurityElement]::Escape('/d /c call "' + $runner + '"')

$taskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>$description</Description></RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>$startBoundary</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT30M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$xmlUser</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <Enabled>true</Enabled>
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>cmd.exe</Command>
      <Arguments>$xmlArgs</Arguments>
      <WorkingDirectory>$xmlDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

Write-Host "任务：$TaskName"
Write-Host "运行：cmd.exe /d /c call `"$runner`""
Write-Host "工作目录：$ProductionDir"
Write-Host "触发：每 30 分钟（起始 $startBoundary）"
Write-Host "账户：$userId (Interactive)"

if (-not $DryRun) {
    $params = @{ TaskName = $TaskName; Xml = $taskXml }
    if ($Force) { $params.Force = $true }
    Register-ScheduledTask @params
    Write-Host "已注册：$TaskName" -ForegroundColor Green
}
