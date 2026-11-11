param(
    [string]$BundleDir = "dist\tainan-assessment-deployment"
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
if ([System.IO.Path]::IsPathRooted($BundleDir)) {
    $BundleRoot = $BundleDir
} else {
    $BundleRoot = Join-Path $ProjectDir $BundleDir
}
$errors = @()
$warnings = @()

$required = @(
    "MANIFEST.json",
    "SHA256SUMS",
    "VERSION",
    "README_DEPLOYMENT.md",
    "requirements.txt",
    "scripts\install_tainan_assessment_tasks.ps1",
    "scripts\uninstall_tainan_assessment_tasks.ps1",
    "scripts\status_tainan_assessment_tasks.ps1",
    "scripts\run_tainan_assessment_now.ps1",
    "scripts\run_tainan_assessment.ps1",
    "scripts\run_tainan_assessment.bat",
    "scripts\build_tainan_assessment_deployment_bundle.ps1",
    "scripts\validate_tainan_assessment_deployment.ps1",
    "config\election_assessment.yaml",
    "config\election_candidate_pipeline.yaml",
    "config\coverage_acceptance_rules.yaml",
    "config\sources.yaml",
    "config\llm_pricing.yaml",
    "config\election_assessment_deployment.example.yaml",
    "config\feishu_delivery.example.yaml",
    "app\assessment\run_assessment_pipeline.py",
    "app\election_context\formal_state_validator.py",
    "app\election_context\coverage_builder.py",
    "app\election_context\snapshot_pipeline.py",
    "app\election_candidates\publication_pipeline.py",
    "app\election_candidates\publication_recovery.py",
    "data\election_seed\tainan_2026\actors.yaml",
    "data\election_seed\tainan_2026\events.jsonl",
    "data\election_seed\tainan_2026\sources.jsonl",
    "data\election_seed\tainan_2026\polls.jsonl",
    "data\election_seed\tainan_2026\poll_questions.jsonl",
    "data\election_seed\tainan_2026\poll_results.jsonl",
    "data\election_seed\tainan_2026\seed_manifest.json",
    "data\election_seed\tainan_2026\schema_versions.json",
    "data\election_context.db",
    "run_monitor.bat",
    "install_scheduled_task.ps1"
)
foreach ($rel in $required) {
    if (-not (Test-Path (Join-Path $BundleRoot $rel))) { $errors += "missing_required:$rel" }
}

if (Test-Path (Join-Path $BundleRoot ".env")) { $errors += "secret_file:.env" }
if (Test-Path (Join-Path $BundleRoot ".env.example")) { $warnings += "file:.env.example" }
if (Test-Path (Join-Path $BundleRoot ".git")) { $errors += "forbidden:.git" }

# SHA256 校验
$sumsPath = Join-Path $BundleRoot "SHA256SUMS"
$validCount = 0
if (Test-Path $sumsPath) {
    $sumCount = 0
    foreach ($line in Get-Content $sumsPath -Encoding UTF8) {
        if (-not $line.Trim()) { continue }
        $parts = $line -split "\s+\*", 2
        if ($parts.Count -ne 2) { $errors += "sha256_format"; continue }
        $expected = $parts[0].ToLowerInvariant()
        $rel = $parts[1]
        $file = Join-Path $BundleRoot $rel
        $sumCount++
        if (-not (Test-Path $file)) { $errors += "sha256_missing_file:$rel"; continue }
        $actual = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -eq $expected) { $validCount++ }
        else { $errors += "sha256_mismatch:$rel" }
    }
    if ($validCount -ne $sumCount) { $errors += "sha256_incomplete" }
} else {
    $errors += "sha256_missing"
}

# MANIFEST 独立校验：文件集合、文件数和每个受保护文件 hash 必须同时一致。
$manifestPath = Join-Path $BundleRoot "MANIFEST.json"
if (Test-Path $manifestPath) {
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $manifestNames = @($manifest.files.PSObject.Properties.Name | Sort-Object)
        if ([int]$manifest.file_count -ne $manifestNames.Count) { $errors += "manifest_file_count" }
        $actualNames = @(Get-ChildItem -Path $BundleRoot -Recurse -File |
            ForEach-Object { $_.FullName.Substring($BundleRoot.Length).TrimStart("\", "/").Replace("\", "/") } |
            Where-Object { $_ -notin @("MANIFEST.json", "SHA256SUMS", "validation.json") } |
            Sort-Object)
        if (Compare-Object -ReferenceObject $manifestNames -DifferenceObject $actualNames) {
            $errors += "manifest_file_set"
        }
        foreach ($name in $manifestNames) {
            $path = Join-Path $BundleRoot $name
            if (-not (Test-Path -LiteralPath $path)) { continue }
            $expected = [string]$manifest.files.$name
            $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($actual -ne $expected.ToLowerInvariant()) { $errors += "manifest_hash:$name" }
        }
    } catch {
        $errors += "manifest_invalid"
    }
} else {
    $errors += "manifest_missing"
}

# 敏感信息扫描
$bs = [char]92
$pattern = @(
    'sk-[A-Za-z0-9_\-]{16,}',
    'https://open\.feishu\.cn/open-apis/bot/v2/hook/',
    'Authorization\s*[:=]\s*(Bearer\s+)?[A-Za-z0-9._\-]{16,}',
    '(?im)^\s*DEEPSEEK_API_KEY\s*=\s*\S+',
    '(?im)^\s*FEISHU_APP_SECRET\s*=\s*\S+',
    '(?im)^\s*FEISHU_WEBHOOK(_URL)?\s*=\s*\S+',
    '(?im)^\s*TELEGRAM_BOT_TOKEN\s*=\s*\S+'
)
$devPathSingle = 'D:' + $bs + 'WXWorkLocal' + $bs + 'TW News-Monitor111'
$devPathDouble = 'D:' + $bs + $bs + 'WXWorkLocal' + $bs + $bs + 'TW News-Monitor111'
$userPathSingle = 'C:' + $bs + 'Users' + $bs + 'User' + $bs
$userPathDouble = 'C:' + $bs + $bs + 'Users' + $bs + $bs + 'User' + $bs + $bs
$scanned = 0
foreach ($file in Get-ChildItem -Path $BundleRoot -Recurse -File) {
    try {
        $content = [System.IO.File]::ReadAllText($file.FullName)
    } catch { continue }
    $scanned++
    foreach ($p in $pattern) {
        if ($content -match $p) {
            $rel = $file.FullName.Substring($BundleRoot.Length).TrimStart("\", "/")
            $errors += "secret_scan:$rel"
            break
        }
    }
    if ($content.Contains($devPathSingle) -or $content.Contains($devPathDouble) -or
        $content.Contains($userPathSingle) -or $content.Contains($userPathDouble)) {
        $rel = $file.FullName.Substring($BundleRoot.Length).TrimStart("\", "/")
        $errors += "secret_scan:$rel"
    }
}

$result = [ordered]@{
    bundle_valid = ($errors.Count -eq 0)
    errors = $errors
    warnings = $warnings
    scanned_file_count = $scanned
    sha256_validated_count = $validCount
}
$json = $result | ConvertTo-Json -Depth 4
[System.IO.File]::WriteAllText((Join-Path $BundleRoot "validation.json"), $json, (New-Object System.Text.UTF8Encoding($false)))
Write-Output $json

if ($errors.Count -gt 0) {
    foreach ($e in $errors) { Write-Host "ERROR: $e" -ForegroundColor Red }
    exit 1
}
Write-Host "部署包验证通过" -ForegroundColor Green
exit 0
