param(
    [switch]$DryRun,
    [switch]$Force,
    [string]$RunTime = "09:00",
    [ValidateSet("development", "dry_run", "production")]
    [string]$Mode = "production"
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$BatPath = Join-Path $ProjectDir "scripts\run_tainan_assessment.bat"
if (-not (Test-Path $BatPath)) { throw "Not found: $BatPath" }

$timeParts = $RunTime -split ":"
if ($timeParts.Count -ne 2) { throw "RunTime 必须为 HH:mm 格式" }
$hour = [int]$timeParts[0]
$minute = [int]$timeParts[1]
if ($hour -lt 0 -or $hour -gt 23 -or $minute -lt 0 -or $minute -gt 59) {
    throw "RunTime 超出合法范围: $RunTime"
}

$tasks = @(
    @{ Name = "Taiwan Election Assessment - Day 9";  Day = 9  },
    @{ Name = "Taiwan Election Assessment - Day 22"; Day = 22 }
)
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"`"$BatPath`"`" -Mode $Mode" -WorkingDirectory $ProjectDir
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$xmlMonths = "<January/><February/><March/><April/><May/><June/><July/><August/><September/><October/><November/><December/>"
$xmlUser = [System.Security.SecurityElement]::Escape($userId)
$xmlProjectDir = [System.Security.SecurityElement]::Escape($ProjectDir)
$xmlArguments = [System.Security.SecurityElement]::Escape('/d /c call "' + $BatPath + '" -Mode ' + $Mode)

foreach ($t in $tasks) {
    Write-Host "任务名称：$($t.Name)"
    Write-Host "触发日：每月 $($t.Day) 日"
    Write-Host "运行时间：$RunTime (Asia/Taipei 系统时区)"
    Write-Host "工作目录：$ProjectDir"
    Write-Host "执行命令：cmd.exe /c `"`"$BatPath`"`" -Mode $Mode"
    Write-Host "运行账户：$userId"
    Write-Host "是否需要登录：Interactive（当前会话账户）"
    Write-Host "失败重试策略：MultipleInstances=IgnoreNew, StartWhenAvailable, 限时2小时"
    Write-Host ""
    if (-not $DryRun) {
        # New-ScheduledTaskTrigger 没有 Monthly 参数；使用任务计划程序原生 XML 月触发器。
        $startBoundary = (Get-Date -Hour $hour -Minute $minute -Second 0).ToString("yyyy-MM-ddTHH:mm:ss")
        $description = [System.Security.SecurityElement]::Escape("台南选情半月研判 - 每月 $($t.Day) 日生成")
        $taskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>$description</Description></RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>$startBoundary</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByMonth>
        <DaysOfMonth><Day>$($t.Day)</Day></DaysOfMonth>
        <Months>$xmlMonths</Months>
      </ScheduleByMonth>
    </CalendarTrigger>
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
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>cmd.exe</Command>
      <Arguments>$xmlArguments</Arguments>
      <WorkingDirectory>$xmlProjectDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
        $params = @{
            TaskName = $t.Name
            Xml = $taskXml
        }
        if ($Force) { $params.Force = $true }
        Register-ScheduledTask @params
        Write-Host "已注册：$($t.Name)" -ForegroundColor Green
    }
}

if ($DryRun) {
    Write-Host "DryRun：未注册任何计划任务" -ForegroundColor Yellow
}
