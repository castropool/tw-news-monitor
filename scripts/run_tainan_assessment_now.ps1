param(
    [ValidateSet("development", "dry_run", "production")]
    [string]$Mode = "development",
    [string]$AsOf = "",
    [string]$PeriodStart = "",
    [string]$PeriodEnd = "",
    [switch]$AllowDraftWithGap
)

$ErrorActionPreference = "Stop"
$runner = Join-Path $PSScriptRoot "run_tainan_assessment.ps1"
$innerArgs = @("-Mode", $Mode)
if ($AsOf) { $innerArgs += @("-AsOf", $AsOf) }
if ($PeriodStart) { $innerArgs += @("-PeriodStart", $PeriodStart) }
if ($PeriodEnd) { $innerArgs += @("-PeriodEnd", $PeriodEnd) }
if ($AllowDraftWithGap) { $innerArgs += "-AllowDraftWithGap" }

& $runner @innerArgs
exit $LASTEXITCODE
