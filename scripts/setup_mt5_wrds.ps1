param(
    [Parameter(Mandatory = $true)][string]$From,
    [Parameter(Mandatory = $true)][string]$To,
    [string]$InputRoot = "C:\data\wrds_ticks",
    [string]$OutputDir = "$env:APPDATA\MetaQuotes\Terminal\Common\Files",
    [string]$Mql5Root = "",
    [switch]$Overwrite,
    [switch]$SkipDependencies,
    [switch]$SkipCompile
)

$ErrorActionPreference = "Stop"
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $InputRoot -PathType Container)) {
    throw "WRDS tick root not found: $InputRoot"
}
try {
    $start = [DateTime]::ParseExact($From, "yyyy-MM-dd", $null)
    $end = [DateTime]::ParseExact($To, "yyyy-MM-dd", $null)
} catch {
    throw "-From and -To must use YYYY-MM-DD. -To is exclusive."
}
if ($end -le $start) { throw "-To must be later than -From." }

if (-not (Test-Path -LiteralPath $python)) {
    & python -m venv (Join-Path $projectRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Could not create .venv with Python." }
}
if (-not $SkipDependencies) {
    & $python -m pip install -e "$projectRoot[wrds]"
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
}

if (-not $SkipCompile) {
    $compileArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        (Join-Path $PSScriptRoot "compile_mt5.ps1"))
    if ($Mql5Root) { $compileArgs += @("-Mql5Root", $Mql5Root) }
    & powershell.exe @compileArgs
    if ($LASTEXITCODE -ne 0) { throw "MQL5 compilation failed." }
}

if (-not $Mql5Root) {
    $terminalRoot = Join-Path $env:APPDATA "MetaQuotes\Terminal"
    $candidates = @(Get-ChildItem -LiteralPath $terminalRoot -Directory | ForEach-Object {
        $candidate = Join-Path $_.FullName "MQL5"
        if (Test-Path -LiteralPath (Join-Path $candidate "Include\Trade\Trade.mqh")) { $candidate }
    })
    if ($candidates.Count -ne 1) {
        throw "Expected one MT5 data folder, found $($candidates.Count). Pass -Mql5Root explicitly."
    }
    $Mql5Root = $candidates[0]
}

$expertDir = Join-Path $Mql5Root "Experts"
$scriptDir = Join-Path $Mql5Root "Scripts"
New-Item -ItemType Directory -Force -Path $expertDir, $scriptDir, $OutputDir | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot "mt5\ORB_Backtest.ex5") -Destination $expertDir -Force
Copy-Item -LiteralPath (Join-Path $projectRoot "mt5\Import_WRDS_Ticks.ex5") -Destination $scriptDir -Force

$convertArgs = @(
    (Join-Path $projectRoot "scripts\wrds_to_mt5_ticks.py"),
    $InputRoot, "--from", $From, "--to", $To, "--output-dir", $OutputDir,
    "--format", "bin"
)
if (($end - $start).TotalDays -gt 31) { $convertArgs += "--allow-large-range" }
if ($Overwrite) { $convertArgs += "--overwrite" }
& $python @convertArgs
if ($LASTEXITCODE -ne 0) { throw "WRDS-to-MT5 conversion failed." }

$verifiedFiles = 0
for ($day = $start; $day -lt $end; $day = $day.AddDays(1)) {
    $file = Join-Path $OutputDir ("QQQ_{0}_ticks.bin" -f $day.ToString("yyyy-MM-dd"))
    if (-not (Test-Path -LiteralPath $file)) { continue }
    & $python (Join-Path $projectRoot "scripts\verify_mt5_ticks.py") $file
    if ($LASTEXITCODE -ne 0) { throw "MT5 tick verification failed for $file." }
    $verifiedFiles++
}
if ($verifiedFiles -eq 0) { throw "Conversion produced no QQQ daily files to verify." }

Write-Output ""
Write-Output "READY"
Write-Output "  EA:       $expertDir\ORB_Backtest.ex5"
Write-Output "  Importer: $scriptDir\Import_WRDS_Ticks.ex5"
Write-Output "  Ticks:    $OutputDir"
Write-Output "In MT5, refresh Navigator, run Import_WRDS_Ticks, and set InpFileName to an exact file or QQQ_*_ticks.bin."
