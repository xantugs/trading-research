param(
    [string]$InputRoot = "C:\data\wrds_ticks",
    [string]$OutputDir = "$env:APPDATA\MetaQuotes\Terminal\Common\Files",
    [switch]$DeleteSourceAfterVerify,
    [switch]$Overwrite
)

$ErrorActionPreference = "Stop"
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$inputRootFull = [IO.Path]::GetFullPath($InputRoot).TrimEnd('\')
$outputFull = [IO.Path]::GetFullPath($OutputDir).TrimEnd('\')
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Missing $python. Run scripts\setup_mt5_wrds.ps1 for one day first."
}
if (-not (Test-Path -LiteralPath $inputRootFull -PathType Container)) {
    throw "WRDS tick root not found: $inputRootFull"
}
New-Item -ItemType Directory -Force -Path $outputFull | Out-Null

$months = @(Get-ChildItem -LiteralPath $inputRootFull -Directory | Sort-Object Name | ForEach-Object {
    Get-ChildItem -LiteralPath $_.FullName -Directory | Where-Object Name -Match '^\d{4}-\d{2}$'
} | Sort-Object Name)
if (-not $months.Count) { throw "No YYYY-MM source directories found under $inputRootFull." }

$totalSourceDays = 0
$totalOutputBytes = 0L
$started = Get-Date
for ($position = 0; $position -lt $months.Count; $position++) {
    $month = $months[$position]
    if (-not $month.FullName.StartsWith($inputRootFull + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe source path: $($month.FullName)"
    }
    $start = [DateTime]::ParseExact($month.Name + "-01", "yyyy-MM-dd", $null)
    $end = $start.AddMonths(1)
    $quoteFiles = @(Get-ChildItem -LiteralPath $month.FullName -Filter 'QQQ_*_quotes.parquet' -File)
    if (-not $quoteFiles.Count) { throw "No QQQ quote files in $($month.FullName)." }
    $expectedNames = @($quoteFiles | ForEach-Object {
        $_.Name.Replace('_quotes.parquet', '_ticks.bin')
    } | Sort-Object)
    $freeGiB = [math]::Round((Get-PSDrive -Name C).Free / 1GB, 2)
    $elapsed = [math]::Round(((Get-Date) - $started).TotalMinutes, 1)
    Write-Output ""
    Write-Output ("[{0}/{1}] {2} | {3} source days | {4} GiB free | {5}m elapsed" -f `
        ($position + 1), $months.Count, $month.Name, $quoteFiles.Count, $freeGiB, $elapsed)

    $convertArgs = @(
        (Join-Path $projectRoot "scripts\wrds_to_mt5_ticks.py"),
        $inputRootFull,
        "--from", $start.ToString("yyyy-MM-dd"),
        "--to", $end.ToString("yyyy-MM-dd"),
        "--output-dir", $outputFull,
        "--format", "bin",
        "--allow-large-range",
        "--workers", "2"
    )
    if ($Overwrite) { $convertArgs += "--overwrite" }
    & $python @convertArgs
    if ($LASTEXITCODE -ne 0) { throw "Conversion failed for $($month.Name)." }

    & $python (Join-Path $projectRoot "scripts\verify_mt5_ticks.py") $outputFull `
        --pattern ("QQQ_{0}-*_ticks.bin" -f $month.Name)
    if ($LASTEXITCODE -ne 0) { throw "Verification failed for $($month.Name)." }

    $actualFiles = @(Get-ChildItem -LiteralPath $outputFull `
        -Filter ("QQQ_{0}-*_ticks.bin" -f $month.Name) -File | Sort-Object Name)
    $actualNames = @($actualFiles.Name)
    if ($actualFiles.Count -ne $expectedNames.Count -or
        (Compare-Object -ReferenceObject $expectedNames -DifferenceObject $actualNames)) {
        throw "Source/output day mismatch for $($month.Name); source has $($expectedNames.Count), output has $($actualFiles.Count)."
    }
    foreach ($file in $actualFiles) {
        $manifest = [IO.Path]::ChangeExtension($file.FullName, ".manifest.json")
        if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) {
            throw "Missing verified manifest: $manifest"
        }
    }

    $totalSourceDays += $quoteFiles.Count
    $monthBytes = ($actualFiles | Measure-Object Length -Sum).Sum
    $totalOutputBytes += $monthBytes
    Write-Output ("Verified {0}: {1} days, {2:N2} GiB compact binary" -f `
        $month.Name, $actualFiles.Count, ($monthBytes / 1GB))

    if ($DeleteSourceAfterVerify) {
        Remove-Item -LiteralPath $month.FullName -Recurse -Force
        Write-Output "Deleted verified raw month: $($month.FullName)"
    }
}

if ($DeleteSourceAfterVerify) {
    Get-ChildItem -LiteralPath $inputRootFull -Directory | Sort-Object Name -Descending | ForEach-Object {
        if (-not (Get-ChildItem -LiteralPath $_.FullName -Force)) {
            Remove-Item -LiteralPath $_.FullName -Force
        }
    }
}

$allOutputs = @(Get-ChildItem -LiteralPath $outputFull -Filter 'QQQ_*_ticks.bin' -File)
$allOutputBytes = ($allOutputs | Measure-Object Length -Sum).Sum
$summary = [ordered]@{
    format_version = 1
    symbol = "QQQ"
    clock = "America/New_York wall time"
    window = "00:00:00.000 <= time < next-day 00:00:00.000"
    output_days = $allOutputs.Count
    source_days_processed_this_run = $totalSourceDays
    output_format = "ORB compact binary v1 (12 bytes per tick plus 32-byte daily header)"
    output_binary_bytes = $allOutputBytes
    output_directory = $outputFull
    source_deleted_after_verification = [bool]$DeleteSourceAfterVerify
    completed_utc = [DateTime]::UtcNow.ToString("o")
}
$summaryPath = Join-Path $outputFull "QQQ_WRDS_MT5_inventory.json"
$summary | ConvertTo-Json | Set-Content -LiteralPath $summaryPath -Encoding UTF8
Write-Output ""
Write-Output ("COMPLETE: {0} total days, {1:N2} GiB compact binary, inventory {2}" -f `
    $allOutputs.Count, ($allOutputBytes / 1GB), $summaryPath)
