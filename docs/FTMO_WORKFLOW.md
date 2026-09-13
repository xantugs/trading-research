# FTMO XAUUSD ORB backtest workflow

This guide is the shortest reproducible path from a clean Windows computer to
an Opening Range Breakout smoke test using FTMO's native XAUUSD history in
MetaTrader 5. It is written for someone who does not need to edit the code.

## What this uses

- The broker symbol is FTMO's native `XAUUSD`.
- MetaTrader downloads the broker's M1 bars and bid/ask ticks into its own local
  cache.
- Strategy Tester runs the EA with **Every tick based on real ticks**.
- No WRDS, QQQ, custom symbol, tick conversion or market-data upload is needed.

The amount of available history belongs to the FTMO server currently selected.
On 14 September 2026, `FTMO-Demo` returned XAUUSD M1 bars and real ticks from
4 October 2021 onward. Always run the probe again after changing account or
server.

## 1. Install the project

Install Python 3.11, 3.12 or 3.13 and MetaTrader 5 first. Then open PowerShell:

```powershell
git clone https://github.com/xantugs/orb-backtest.git
cd orb-backtest

py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev,mt5]"
```

If the repository already exists, update it instead:

```powershell
cd orb-backtest
git pull
```

## 2. Connect MetaTrader to FTMO

1. Open MetaTrader 5.
2. Log into the FTMO account intended for testing.
3. Confirm that the connection indicator is active.
4. In Market Watch, show the broker's gold symbol. It is normally `XAUUSD`.

Do not send account credentials to anyone. The probe talks to the already
logged-in terminal.

## 3. Check the available real ticks

Keep MT5 open and run:

```powershell
python run_all.py ftmo-probe --symbol XAUUSD --start-year 2020
```

Check that the output names FTMO as the connected company/server, confirms M1
bars, and reports real ticks. MT5 may download history during the first probe;
that data stays in MetaTrader's local cache.

If the probe connects to another installed terminal, pass the FTMO executable:

```powershell
python run_all.py ftmo-probe --symbol XAUUSD `
  --terminal-path "C:\Path\To\FTMO MetaTrader 5\terminal64.exe"
```

## 4. Compile and install the EA

Compile the repository source:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\compile_mt5.ps1
```

The command must finish with `0 errors, 0 warnings`.

In MT5, select **File > Open Data Folder**. Copy
`mt5\ORB_Backtest.ex5` from the repository into `MQL5\Experts` in that data
folder. Return to MT5, right-click **Expert Advisors** in Navigator, and select
**Refresh**. `ORB_Backtest` should appear.

The EA blocks entries outside Strategy Tester while `InpAllowLiveTrading` is
`false`. Leave that input disabled during research.

## 5. Run the one-month smoke test

Open Strategy Tester with **Ctrl+R** and use:

| Tester setting | Value |
| --- | --- |
| Expert | `ORB_Backtest` |
| Symbol | Native `XAUUSD` |
| Timeframe | `M15` |
| Model | **Every tick based on real ticks** |
| Date | `2025-01-01` through `2025-02-01` |
| Deposit and leverage | Match the intended FTMO account |
| Visual mode | On |

Use this simple first-pass input set:

| EA input | Value |
| --- | --- |
| Opening-range hour | `16` |
| Opening-range minute | `30` |
| Entry cutoff hour | `19` |
| Range construction | Observed opening candle |
| Entry mode | Close confirmation |
| Confirmation bars | `1` |
| Confirmation colour | Off |
| Direction | Long and short |
| Take profit | `1.5 R` |
| Range, ATR and trend filters | Off |
| Breakeven and trailing | Off |
| End-of-day close | Off |
| Allow live trading | `false` |

On normal FTMO clock weeks, server time 16:30 corresponds to the 09:30 New
York cash open and 19:00 corresponds to noon in New York.

## 6. Inspect ten trades

For each selected trade, confirm all of the following:

1. The 16:30-16:45 M15 candle sets the opening-range high and low.
2. The first accepted breakout candle closes outside that range.
3. The trade direction matches that first confirmed breakout.
4. The EA takes no second signal that day.
5. Entry, initial stop and target match the selected inputs.
6. Bid/ask spread explains any difference between the visible candle and fill.
7. The Strategy Tester trade and `ORB_trades.csv` record agree.

The trade log is stored under MetaTrader's shared `Common\Files` folder when
logging is enabled.

## 7. Before the full-history test

FTMO and New York do not change daylight-saving time on the same dates. During
the short transition windows, the New York 09:30 open can occur at 15:30 FTMO
server time instead of 16:30. The current EA can diagnose a likely clock
mismatch but does not automatically move the range hour.

Treat the January smoke test as execution validation. Add and verify automatic
New York session conversion before interpreting a continuous multi-year result.

After that correction, keep a final period out of parameter selection:

- Development: 4 October 2021 through 31 December 2024
- Holdout: 1 January 2025 onward

Run the plain baseline first. Change one rule or parameter group at a time,
save each MT5 report, and compare annual results and drawdowns rather than only
the combined return.

## Troubleshooting

- **Wrong company/server:** close the other MT5 terminal or use
  `--terminal-path`.
- **Symbol unavailable:** copy the exact symbol name from FTMO Market Watch; an
  account may use a suffix.
- **No old ticks:** the broker server controls retention. Reduce the test period
  to the first confirmed date printed by the probe.
- **EA missing:** copy the compiled EX5 into the active terminal's
  `MQL5\Experts` folder and refresh Navigator.
- **Tester shifts the starting date:** the selected server does not have all
  requested history; use the synchronized period shown in the tester Journal.

This is research software. Passing the smoke test establishes that the rules
execute as specified; it does not establish a profitable strategy.
