param(
    [string]$ProductionDir = "",
    [string]$BackupRoot = "",
    [switch]$DryRun,
    [switch]$SkipBackup
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
if (-not $ProductionDir) { $ProductionDir = $ProjectDir }
if (-not $BackupRoot) { $BackupRoot = Join-Path $ProductionDir "data\election_candidates\tainan_2026\phase_f1\candidate_deployment_backups" }
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupDir = Join-Path $BackupRoot $stamp

if (-not (Test-Path -LiteralPath $ProductionDir)) {
    throw "生产目录不存在：$ProductionDir"
}

function Backup-Path([string]$Path, [string]$Relative) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $dst = Join-Path $backupDir $Relative
    $parent = Split-Path -Parent $dst
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    Copy-Item -LiteralPath $Path -Destination $dst -Recurse -Force
    Write-Host "已备份：$Relative"
}

function Backup-SchedulerDefinition() {
    $task = Get-ScheduledTask -TaskName "Tainan Election Candidate Monitor" -ErrorAction SilentlyContinue
    if (-not $task) { return }
    $xml = Export-ScheduledTask -TaskName "Tainan Election Candidate Monitor"
    $dst = Join-Path $backupDir "scheduler_definition.xml"
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
    [System.IO.File]::WriteAllText($dst, $xml, (New-Object System.Text.UTF8Encoding($true)))
    Write-Host "已备份计划任务定义：scheduler_definition.xml"
}

function Deploy-Item([string]$Source, [string]$Relative, [switch]$Overwrite) {
    if (-not (Test-Path -LiteralPath $Source)) {
        throw "源文件缺失：$Source"
    }
    $dest = Join-Path $ProductionDir $Relative
    if ((Test-Path -LiteralPath $dest) -and -not $Overwrite) {
        throw "目标已存在且不允许覆盖：$Relative"
    }
    if ((Test-Path -LiteralPath $dest) -and $Overwrite) {
        $resolved = [System.IO.Path]::GetFullPath($dest)
        if (-not $resolved.StartsWith([System.IO.Path]::GetFullPath($ProductionDir))) {
            throw "拒绝删除越界路径：$resolved"
        }
        Remove-Item -LiteralPath $dest -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
    if ($DryRun) {
        Write-Host "将部署：$Relative"
    } else {
        Copy-Item -LiteralPath $Source -Destination $dest -Recurse -Force
        Write-Host "已部署：$Relative"
    }
}

if (-not $SkipBackup) {
    New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
    Backup-Path (Join-Path $ProductionDir "data\election_candidates\tainan_2026\candidate_fact_pipeline.db") "candidate_fact_pipeline.db"
    Backup-Path (Join-Path $ProductionDir "config\election_candidate_pipeline.yaml") "config\election_candidate_pipeline.yaml"
    Backup-Path (Join-Path $ProductionDir "run_candidate_monitor.bat") "run_candidate_monitor.bat"
    Backup-Path (Join-Path $ProductionDir "app\election_candidates") "app\election_candidates"
    Backup-Path (Join-Path $ProductionDir "app\election_context\repository.py") "app\election_context\repository.py"
    Backup-Path (Join-Path $ProductionDir "app\election_context\bootstrap_v2.py") "app\election_context\bootstrap_v2.py"
    Backup-Path (Join-Path $ProductionDir "app\election_context\formal_state_hash.py") "app\election_context\formal_state_hash.py"
    Backup-Path (Join-Path $ProductionDir "app\election_context\coverage_builder.py") "app\election_context\coverage_builder.py"
    Backup-Path (Join-Path $ProductionDir "app\election_context\coverage_rules.py") "app\election_context\coverage_rules.py"
    Backup-Path (Join-Path $ProductionDir "app\election_context\coverage_validator.py") "app\election_context\coverage_validator.py"
    Backup-Path (Join-Path $ProductionDir "app\election_context\downstream_refresh.py") "app\election_context\downstream_refresh.py"
    Backup-Path (Join-Path $ProductionDir "app\election_context\snapshot_candidate_builder.py") "app\election_context\snapshot_candidate_builder.py"
    Backup-Path (Join-Path $ProductionDir "app\election_context\snapshot_pipeline.py") "app\election_context\snapshot_pipeline.py"
    Backup-Path (Join-Path $ProductionDir "app\election_context\snapshot_validator.py") "app\election_context\snapshot_validator.py"
    Backup-Path (Join-Path $ProductionDir "app\election_context\run_post_publication_pipeline.py") "app\election_context\run_post_publication_pipeline.py"
    Backup-Path (Join-Path $ProductionDir "app\assessment") "app\assessment"
    Backup-Path (Join-Path $ProductionDir "scripts\install_candidate_monitor_task.ps1") "scripts\install_candidate_monitor_task.ps1"
    Backup-Path (Join-Path $ProductionDir "scripts\status_candidate_monitor_task.ps1") "scripts\status_candidate_monitor_task.ps1"
    Backup-Path (Join-Path $ProductionDir "scripts\run_candidate_monitor_now.ps1") "scripts\run_candidate_monitor_now.ps1"
    Backup-Path (Join-Path $ProductionDir "scripts\uninstall_candidate_monitor_task.ps1") "scripts\uninstall_candidate_monitor_task.ps1"
    Backup-Path (Join-Path $ProductionDir "scripts\rollback_candidate_deployment.ps1") "scripts\rollback_candidate_deployment.ps1"
    Backup-SchedulerDefinition
    Write-Host "备份目录：$backupDir"
} else {
    Write-Host "跳过备份（-SkipBackup）"
}

# 新增/覆盖（repository.py 为纯新增方法的覆盖，其余均为新增文件）
Deploy-Item (Join-Path $ProjectDir "app\election_candidates") "app\election_candidates" -Overwrite
Deploy-Item (Join-Path $ProjectDir "app\assessment") "app\assessment" -Overwrite
foreach ($name in @(
    "repository.py",
    "bootstrap_v2.py",
    "formal_state_hash.py",
    "coverage_builder.py",
    "coverage_rules.py",
    "coverage_validator.py",
    "downstream_refresh.py",
    "snapshot_candidate_builder.py",
    "snapshot_pipeline.py",
    "snapshot_validator.py",
    "run_post_publication_pipeline.py"
)) {
    $src = Join-Path $ProjectDir ("app\election_context\" + $name)
    $dest = Join-Path $ProductionDir ("app\election_context\" + $name)
    if (Test-Path -LiteralPath $dest) {
        Deploy-Item $src ("app\election_context\" + $name) -Overwrite
    } else {
        Deploy-Item $src ("app\election_context\" + $name)
    }
}
Deploy-Item (Join-Path $ProjectDir "config\election_candidate_pipeline.yaml") "config\election_candidate_pipeline.yaml" -Overwrite
Deploy-Item (Join-Path $ProjectDir "run_candidate_monitor.bat") "run_candidate_monitor.bat" -Overwrite
foreach ($name in @(
    "install_candidate_monitor_task.ps1",
    "status_candidate_monitor_task.ps1",
    "run_candidate_monitor_now.ps1",
    "uninstall_candidate_monitor_task.ps1",
    "rollback_candidate_deployment.ps1"
)) {
    Deploy-Item (Join-Path $ProjectDir ("scripts\" + $name)) ("scripts\" + $name) -Overwrite
}
Deploy-Item (Join-Path $ProjectDir "docs\FACT_MAINTENANCE_OPERATOR_GUIDE.md") "docs\FACT_MAINTENANCE_OPERATOR_GUIDE.md" -Overwrite

if (-not $DryRun) {
    New-Item -ItemType Directory -Force -Path (Join-Path $ProductionDir "data\election_candidates\tainan_2026\logs") | Out-Null
    Write-Host "已创建候选数据目录与日志目录"
}
Write-Host "部署完成"
