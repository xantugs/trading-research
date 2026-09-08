param(
    [string]$MetaEditor = "$env:ProgramFiles\MetaTrader 5\metaeditor64.exe",
    [string]$Mql5Root = ""
)
$ErrorActionPreference = "Stop"
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$eaSource = Join-Path $projectRoot "mt5/ORB_Backtest.mq5"
$compileLog = Join-Path $projectRoot "mt5/compile.log"
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
if (Test-Path -LiteralPath $compileLog) { Remove-Item -LiteralPath $compileLog }
Start-Process -FilePath $MetaEditor -ArgumentList @('/compile:"' + $eaSource + '"', '/inc:"' + $Mql5Root + '"', '/log:"' + $compileLog + '"') -WindowStyle Hidden -Wait
if (-not (Test-Path -LiteralPath $compileLog)) { throw "MetaEditor did not produce a compile log." }
$result = Get-Content -LiteralPath $compileLog
$result | Select-Object -Last 4
if (-not ($result -match 'Result: 0 errors, 0 warnings')) { throw "Compilation failed or produced warnings. Read mt5/compile.log." }
Write-Output "Compiled EA: $(Join-Path $projectRoot 'mt5/ORB_Backtest.ex5')"
