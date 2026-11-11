param(
    [string]$AsOf = "",
    [ValidateSet("scheduled", "manual", "controlled")]
    [string]$TriggerType = "scheduled",
    [switch]$CheckOnly
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

$LogDir = Join-Path $ProjectDir "data\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir "r2_scheduler.log"
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

# 生产路径：research-driven 研判生成（Period Gate -> Research Pack -> 文章 -> Word -> 人工终审）
$argsList = @("-m", "app.assessment.research_driven.scheduled", "--config", "config/election_assessment.yaml", "--trigger-type", $TriggerType)
if ($AsOf) { $argsList += @("--as-of", $AsOf) }
if ($CheckOnly) { $argsList += "--check-only" }

$oldEA = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $output = & $Python @argsList 2>&1
    $exitCode = $LASTEXITCODE
    $output | Out-String | Tee-Object -FilePath $LogFile -Append
}
catch {
    $exitCode = 1
    Add-Content -Path $LogFile -Value "$stamp ERROR=$($_.Exception.Message)"
}
$ErrorActionPreference = $oldEA
Add-Content -Path $LogFile -Value "$stamp TRIGGER=$TriggerType AS_OF=$AsOf EXIT=$exitCode"
exit $exitCode
