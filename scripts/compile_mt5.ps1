param(
    [string]$MetaEditor = "$env:ProgramFiles\MetaTrader 5\metaeditor64.exe",
    [string]$Mql5Root = ""
)
$ErrorActionPreference = "Stop"
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$sources = @(
    @{ Source = Join-Path $projectRoot "mt5/ORB_Backtest.mq5"; Log = Join-Path $projectRoot "mt5/compile.log" },
    @{ Source = Join-Path $projectRoot "mt5/Import_WRDS_Ticks.mq5"; Log = Join-Path $projectRoot "mt5/import_compile.log" }
)
if (-not (Test-Path -LiteralPath $MetaEditor)) { throw "MetaEditor missing; pass -MetaEditor with its executable path." }
if (-not $Mql5Root) {
    $terminalRoot = Join-Path $env:APPDATA "MetaQuotes/Terminal"
    $candidates = @(Get-ChildItem -LiteralPath $terminalRoot -Directory | ForEach-Object {
        $candidate = Join-Path $_.FullName "MQL5"
        if (Test-Path -LiteralPath (Join-Path $candidate "Include/Trade/Trade.mqh")) { $candidate }
    })
    if (-not $candidates.Count) { throw "MQL5 standard library not found. Open MT5 once or pass -Mql5Root." }
    $Mql5Root = $candidates[0]
}
if (-not (Test-Path -LiteralPath (Join-Path $Mql5Root "Include/Trade/Trade.mqh"))) { throw "Invalid Mql5Root" }
foreach ($item in $sources) {
    if (-not (Test-Path -LiteralPath $item.Source)) { throw "MQL5 source missing: $($item.Source)" }
    if (Test-Path -LiteralPath $item.Log) { Remove-Item -LiteralPath $item.Log }
    Start-Process -FilePath $MetaEditor -ArgumentList @('/compile:"' + $item.Source + '"', '/inc:"' + $Mql5Root + '"', '/log:"' + $item.Log + '"') -WindowStyle Hidden -Wait
    if (-not (Test-Path -LiteralPath $item.Log)) { throw "MetaEditor did not produce $($item.Log)." }
    $result = Get-Content -LiteralPath $item.Log
    $result | Select-Object -Last 4
    if (-not ($result -match 'Result: 0 errors, 0 warnings')) {
        throw "Compilation failed or produced warnings. Read $($item.Log)."
    }
    Write-Output "Compiled: $([IO.Path]::ChangeExtension($item.Source, '.ex5'))"
}
