param(
    [switch]$DryRun,
    [switch]$Force,
    [switch]$Disable,
    [string]$RunTime = "09:00",
    [ValidateSet("NonInteractive", "Interactive")]
    [string]$LogonMode = "NonInteractive",
    [ValidateSet("tainan", "new_taipei")]
    [string]$Election = "tainan",
    [string]$ExportXmlOnly
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$BatPath = Join-Path $ProjectDir "scripts\run_assessment_scheduled.bat"
$taskName = "Tainan Election Assessment"
$description = "台南选情研判 - 每月9日/22日 09:00 生成并进入人工终审（只生成，不自动发送）"
if ($Election -eq "new_taipei") {
    $BatPath = Join-Path $ProjectDir "scripts\run_assessment_scheduled_new_taipei.bat"
    $taskName = "New Taipei Election Assessment"
    $description = "新北选情研判 - 每月9日/22日 09:00 生成并进入人工终审（只生成，不自动发送）"
}
if (-not (Test-Path $BatPath)) { throw "Not found: $BatPath" }

$timeParts = $RunTime -split ":"
$hour = [int]$timeParts[0]
$minute = [int]$timeParts[1]
if ($hour -lt 0 -or $hour -gt 23 -or $minute -lt 0 -or $minute -gt 59) {
    throw "RunTime 必须为合法 HH:mm"
}

$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$startBoundary = (Get-Date -Hour $hour -Minute $minute -Second 0).ToString("yyyy-MM-ddTHH:mm:ss")
$enabled = if ($Disable) { "false" } else { "true" }

# 登录模式：
# - 默认 NonInteractive（LogonType=S4U，Passwordless，不存密码，用户未交互登录也可运行）
# - 显式 -LogonMode Interactive 时退回 InteractiveToken（仅交互登录可运行），作为 fallback
if ($LogonMode -eq "Interactive") {
    $logonType = "InteractiveToken"
} else {
    $logonType = "S4U"
}

$xmlUser = [System.Security.SecurityElement]::Escape($userId)
$xmlProjectDir = [System.Security.SecurityElement]::Escape($ProjectDir)
$xmlArguments = [System.Security.SecurityElement]::Escape('/d /c call "' + $BatPath + '"')
$description = [System.Security.SecurityElement]::Escape($description)

$taskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>$description</Description></RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>$startBoundary</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByMonth>
        <DaysOfMonth><Day>9</Day></DaysOfMonth>
        <Months><January/><February/><March/><April/><May/><June/><July/><August/><September/><October/><November/><December/></Months>
      </ScheduleByMonth>
    </CalendarTrigger>
    <CalendarTrigger>
      <StartBoundary>$startBoundary</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByMonth>
        <DaysOfMonth><Day>22</Day></DaysOfMonth>
        <Months><January/><February/><March/><April/><May/><June/><July/><August/><September/><October/><November/><December/></Months>
      </ScheduleByMonth>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$xmlUser</UserId>
      <LogonType>$logonType</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <Enabled>$enabled</Enabled>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <IdleSettings><StopOnIdleEnd>true</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
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

if ($ExportXmlOnly) {
    $utf16 = New-Object System.Text.UnicodeEncoding($false, $true)
    [System.IO.File]::WriteAllText($ExportXmlOnly, $taskXml, $utf16)
    Write-Host "已导出任务 XML：$ExportXmlOnly（未注册）"
    exit 0
}

Write-Host "任务名称：$taskName"
Write-Host "触发：每月 9 日、22 日 $RunTime（Asia/Taipei，UTC+8）"
Write-Host "工作目录：$ProjectDir"
Write-Host "执行命令：cmd.exe /c call `"$BatPath`""
Write-Host "运行账户：$userId"
Write-Host "登录模式：$LogonMode（LogonType=$logonType，无密码存储）"
Write-Host "启用：$(-not $Disable)"
if ($DryRun) {
    Write-Host "DryRun：未注册任务" -ForegroundColor Yellow
    exit 0
}

$params = @{ TaskName = $taskName; Xml = $taskXml }
if ($Force) { $params.Force = $true }
try {
    $registered = Register-ScheduledTask @params
} catch {
    if ($LogonMode -eq "NonInteractive") {
        Write-Host "S4U（Passwordless）注册被拒绝（通常需要管理员权限）。" -ForegroundColor Red
        Write-Host "处理方式：以管理员身份运行本脚本；或显式使用 -LogonMode Interactive 退回交互模式（仅交互登录时运行）。" -ForegroundColor Yellow
    }
    throw
}

# 安装后验证：action 必须指向当前主目录下的 bat，工作目录必须为当前主目录
$action = $registered.Actions | Select-Object -First 1
$expectedArgs = '/d /c call "' + $BatPath + '"'
if (-not $action -or $action.Arguments.Trim() -ne $expectedArgs -or $action.WorkingDirectory.Trim() -ne $ProjectDir) {
    throw "注册后验证失败：action 未指向当前主目录（Arguments=$($action.Arguments), WorkingDirectory=$($action.WorkingDirectory)）"
}
Write-Host "已注册并验证：$taskName（action 指向 $BatPath，工作目录 $ProjectDir）" -ForegroundColor Green
