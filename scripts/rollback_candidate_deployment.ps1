param(
    [switch]$DisableTaskOnly,
    [string]$TaskName = "Tainan Election Candidate Monitor",
    [string]$ProductionDir = "",
    [string]$BackupRoot = ""
)

$ErrorActionPreference = "Stop"
if (-not $ProductionDir) { $ProductionDir = Split-Path -Parent $PSScriptRoot }
if (-not $BackupRoot) { $BackupRoot = Join-Path $ProductionDir "data\election_candidates\tainan_2026\phase_f1\candidate_deployment_backups" }

if ($DisableTaskOnly) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Disable-ScheduledTask -TaskName $TaskName | Out-Null
        Write-Host "已禁用计划任务：$TaskName（不影响 Taiwan News Monitor）"
    } else {
        Write-Host "计划任务不存在：$TaskName"
    }
    exit 0
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupDir = Join-Path $BackupRoot $stamp
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

$paths = @(
    (Join-Path $ProductionDir "data\election_candidates\tainan_2026\candidate_fact_pipeline.db"),
    (Join-Path $ProductionDir "config\election_candidate_pipeline.yaml"),
    (Join-Path $ProductionDir "run_candidate_monitor.bat"),
    (Join-Path $ProductionDir "app\election_candidates")
)
foreach ($p in $paths) {
    if (Test-Path -LiteralPath $p) {
        $dst = Join-Path $backupDir ([IO.Path]::GetFileName($p))
        Copy-Item -LiteralPath $p -Destination $dst -Recurse -Force
        Write-Host "已备份：$p -> $dst"
    }
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    $xml = Export-ScheduledTask -TaskName $TaskName
    $dst = Join-Path $backupDir "scheduler_definition.xml"
    [System.IO.File]::WriteAllText($dst, $xml, (New-Object System.Text.UTF8Encoding($true)))
    Write-Host "已备份计划任务定义：$dst"
}

if ($task) {
    Disable-ScheduledTask -TaskName $TaskName | Out-Null
    Write-Host "已禁用计划任务：$TaskName"
} else {
    Write-Host "计划任务不存在，无需禁用：$TaskName"
}
Write-Host "回滚备份目录：$backupDir"
