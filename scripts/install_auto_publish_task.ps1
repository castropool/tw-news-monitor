param(
    [switch]$DryRun,
    [switch]$Force,
    [switch]$Disable,
    [ValidateSet("NonInteractive", "Interactive")]
    [string]$LogonMode = "NonInteractive",
    [ValidateSet("tainan", "new_taipei")]
    [string]$Election = "tainan",
    [string]$ExportXmlOnly,
    [string]$StartMinute = ""
)

# Tainan Election Fact Auto Publisher - 计划任务安装脚本
# 默认 S4U NonInteractive（Passwordless，无密码存储，未交互登录也可运行）；
# 显式 -LogonMode Interactive 退回 InteractiveToken 作为 fallback。
# 错峰：candidate monitor 每 30 分钟运行（当前 :19/:49），本任务在其后 10 分钟
# （:29/:59）。单实例 IgnoreNew，超时 30 分钟，StartWhenAvailable。
# S4U 注册被拒时绝不回退交互并冒充成功：保留配置启用，打印管理员命令，exit 1。
# -Election new_taipei 注册新北并行任务（任务名/runner bat 均按选举派生）。

$ErrorActionPreference = "Stop"
$TaskName = "Tainan Election Fact Auto Publisher"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$BatPath = Join-Path $ProjectDir "run_auto_publish_candidates.bat"
$description = "台南选情低风险事实自动发布 - 每30分钟（monitor 后10分钟错峰）运行已验收的 auto_publish_candidates 保守策略"
if ($Election -eq "new_taipei") {
    $TaskName = "New Taipei Election Fact Auto Publisher"
    $BatPath = Join-Path $ProjectDir "run_auto_publish_candidates_new_taipei.bat"
    $description = "新北选情低风险事实自动发布 - 每30分钟（monitor 后10分钟错峰）运行已验收的 auto_review_orchestrator 保守策略"
}
if (-not (Test-Path -LiteralPath $BatPath)) { throw "Not found: $BatPath" }

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

# 错峰：monitor :19/:49 -> auto publisher :29/:59（monitor 分钟 +10 mod 30）
$monitorMinute = Get-TaskStartMinute "Tainan Election Candidate Monitor"
Write-Host "Tainan Election Candidate Monitor 启动分钟：$monitorMinute"
if (-not $StartMinute) {
    if ($null -eq $monitorMinute) {
        $StartMinute = "29"  # 探测不到 monitor 时使用约定默认值（:29/:59）
    } else {
        $StartMinute = (($monitorMinute + 10) % 30).ToString("00")
    }
}
if ($StartMinute.Length -ne 2 -or [int]$StartMinute -lt 0 -or [int]$StartMinute -gt 59) {
    throw "StartMinute 必须为两位分钟数（MM），当前值：$StartMinute"
}
Write-Host "自动发布任务启动分钟（错峰 +10min）：$StartMinute"

$now = Get-Date
$start = Get-Date -Hour 0 -Minute ([int]$StartMinute) -Second 0
while ($start -le $now) { $start = $start.AddMinutes(30) }
$startBoundary = $start.ToString("yyyy-MM-ddTHH:mm:ss")

# 登录模式：默认 NonInteractive（S4U）；显式 Interactive 时才退回交互
if ($LogonMode -eq "Interactive") {
    $logonType = "InteractiveToken"
} else {
    $logonType = "S4U"
}

$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$xmlUser = [System.Security.SecurityElement]::Escape($userId)
$xmlProjectDir = [System.Security.SecurityElement]::Escape($ProjectDir)
$xmlBatPath = [System.Security.SecurityElement]::Escape($BatPath)
$xmlArguments = [System.Security.SecurityElement]::Escape('/d /c call "' + $BatPath + '"')
$description = [System.Security.SecurityElement]::Escape($description)
$enabled = if ($Disable) { "false" } else { "true" }

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
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
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

Write-Host "任务名称：$TaskName"
Write-Host "触发：每 30 分钟（起始 $startBoundary，即 :$StartMinute 与 :$(([int]$StartMinute + 30) % 60)）"
Write-Host "工作目录：$ProjectDir"
Write-Host "执行命令：cmd.exe /d /c call `"$BatPath`""
Write-Host "运行账户：$userId"
Write-Host "登录模式：$LogonMode（LogonType=$logonType，无密码存储）"
Write-Host "启用：$(-not $Disable)"
if ($DryRun) {
    Write-Host "DryRun：未注册任务" -ForegroundColor Yellow
    exit 0
}

# 同名任务已存在时的 XML 备份（-Force 才允许覆盖注册）
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    if (-not $Force) {
        throw "任务已存在：$TaskName（如需覆盖请加 -Force，注册前会先备份现有任务 XML）"
    }
    $ts = Get-Date -Format "yyyyMMdd_HHmmss"
    $bkDir = Join-Path $ProjectDir ("data\backups\auto_publish_enablement\" + $ts)
    New-Item -ItemType Directory -Force -Path $bkDir | Out-Null
    $existingXml = Join-Path $bkDir "existing_task_auto_publisher.xml"
    Export-ScheduledTask -TaskName $TaskName | Out-File -FilePath $existingXml -Encoding Unicode
    Write-Host "已备份现有同名任务 XML：$existingXml"
}

$params = @{ TaskName = $TaskName; Xml = $taskXml }
if ($Force) { $params.Force = $true }
try {
    $registered = Register-ScheduledTask @params
} catch {
    if ($LogonMode -eq "NonInteractive") {
        Write-Host "" -ForegroundColor Red
        Write-Host "S4U（Passwordless）注册被拒绝（通常需要管理员权限）。" -ForegroundColor Red
        Write-Host "配置已保持启用（auto_publish.enabled=true / auto_activate_snapshots=true），" -ForegroundColor Yellow
        Write-Host "但计划任务未安装。不会回退到交互模式冒充成功。" -ForegroundColor Yellow
        Write-Host "请以管理员身份运行（任选其一）：" -ForegroundColor Yellow
        Write-Host "  1) powershell -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -ForegroundColor Cyan
        Write-Host "  2) schtasks /create /tn `"$TaskName`" /xml `"<导出XML路径>`" /f   （先加 -ExportXmlOnly 导出）" -ForegroundColor Cyan
    }
    throw
}

# 安装后验证：action 必须指向当前主目录下的 bat，工作目录必须为当前主目录
$action = $registered.Actions | Select-Object -First 1
$expectedArgs = '/d /c call "' + $BatPath + '"'
if (-not $action -or $action.Arguments.Trim() -ne $expectedArgs -or $action.WorkingDirectory.Trim() -ne $ProjectDir) {
    throw "注册后验证失败：action 未指向当前主目录（Arguments=$($action.Arguments), WorkingDirectory=$($action.WorkingDirectory)）"
}
Write-Host "已注册并验证：$TaskName（action 指向 $BatPath，工作目录 $ProjectDir）" -ForegroundColor Green
