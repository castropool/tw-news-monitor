param(
    [ValidateSet("development", "dry_run", "production")]
    [string]$Mode = "production",
    [string]$AsOf = "",
    [string]$PeriodStart = "",
    [string]$PeriodEnd = "",
    [switch]$AllowDraftWithGap
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectDir

# 加载 .env（仅注入进程环境，不输出任何值）
$EnvFile = Join-Path $ProjectDir ".env"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $idx = $line.IndexOf("=")
            $name = $line.Substring(0, $idx).Trim()
            $value = $line.Substring($idx + 1).Trim()
            if ($name -and -not [System.Environment]::GetEnvironmentVariable($name, "Process")) {
                [System.Environment]::SetEnvironmentVariable($name, $value, "Process")
            }
        }
    }
}

if (-not $AsOf -and -not $PeriodStart -and -not $PeriodEnd) {
    $AsOf = Get-Date -Format "yyyy-MM-dd"
}

$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

$pipelineArgs = @("--config", "config/election_assessment.yaml", "--mode", $Mode)
if ($AsOf) { $pipelineArgs += @("--as-of", $AsOf) }
if ($PeriodStart) { $pipelineArgs += @("--period-start", $PeriodStart) }
if ($PeriodEnd) { $pipelineArgs += @("--period-end", $PeriodEnd) }
if ($AllowDraftWithGap) { $pipelineArgs += "--allow-draft-with-gap" }

$LogDir = Join-Path $ProjectDir "data\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir "pipeline_scheduler.log"
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

try {
    & $Python -m app.assessment.run_assessment_pipeline @pipelineArgs 2>&1 | Tee-Object -FilePath $LogFile -Append
    $exitCode = $LASTEXITCODE
}
catch {
    $exitCode = 1
    Add-Content -Path $LogFile -Value "$stamp ERROR=$($_.Exception.Message)"
}
Add-Content -Path $LogFile -Value "$stamp MODE=$Mode AS_OF=$AsOf EXIT=$exitCode"
exit $exitCode
