# Compile, smoke test, reconcile

## Compilation

Run `python run_all.py --compile-mt5 --no-app` or
`powershell -File scripts/compile_mt5.ps1`. The helper launches MetaEditor
hidden, reads the compile log and fails unless it says zero errors/warnings.
The current source compiled successfully on the installed local MetaEditor.
This is a compiler result, not a trading validation result.

Alternatively copy `mt5/ORB_Backtest.mq5` to
`<MT5 data folder>/MQL5/Experts/`, open MetaEditor and press F7.

## One-month smoke test

1. Open Strategy Tester and select the EA, your exact symbol, M15 and one month.
2. Use Every tick based on real ticks where the broker supplies it; otherwise
   1 minute OHLC. Open-prices-only mode does not validate intrabar SL/TP.
3. Confirm the chart clock at the NY opening bell. Match ORB/cutoff settings.
4. Initially disable breakeven, trailing, volume skipping and trend filters.
   Match EOD on/off explicitly: Python's supplied baseline config is on.
5. Enable visual mode. Check that the range box is on the intended 15-minute
   candle and inspect the journal for warnings or rejected orders.
6. Save a unique `InpLogFile`. The EA writes to the terminal's shared
   `Common/Files` directory and prints the exact path. Logging is off during
   optimization. An existing file with the same name is overwritten.

## Ten-trade checklist for the friend

For each trade record the date/direction, opening-range high/low, breakout
candle close, next bar open/fill, SL, TP, lot size and exit reason. Check:

- Was the range candle really the session open on that date?
- Was entry before cutoff, on the first signal in that direction?
- Did stop and target match the formulas in MODEL.md?
- Was the volume/ATR/trend filter status understood?
- Does the journal explain any skipped trade or fill difference?
- Did EOD close as configured, and did any overnight gap change the exit?

Retain the tester settings, broker/symbol contract details, tester report, log,
and data files locally beside the Python result bundle.

## Dukascopy custom symbols

Use the generated `*_M1_mt5.csv` in MT5's Custom Symbol -> Bars -> Import.
Set contract/tick/lot rules deliberately; imported prices do not supply your
broker's execution rules. Imported volume is synthetic (one per minute);
turn off the volume filter. A constant spread and generated tester ticks do
not become real executable historical quotes. Raw CSV ticks are for archival
research and are not currently emitted in MT5's custom-tick import format.

## Reconciliation

Use the same M1 source to derive the signal bars for Python. Run
`reconcile.py --strict` with matching spread settings. New v1.30 log headers
carry ATR/volume settings and initial contract/balance information.
Older logs need explicit overrides for anything absent from the header.

Entry time is compared at minute granularity. Stops/targets/entry/exit use
price tolerance, R has its own tolerance, and exit reason must match. Exit
timestamps are shown but not a strict match because Python timestamps refer
to the path bar open. Money and lots are diagnostic columns, not assertions
that margin/currency/commission handling is identical.

Exit code 0 = command succeeded (and, with strict, checks matched).
Exit code 2 = strict execution mismatch. Malformed inputs are failures.
A fabricated-log test can verify the reconciliation tool; it cannot verify
the EA against actual broker executions.
