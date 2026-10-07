# US100 candidate: forward test plan (7 October 2026)

Written and committed **before the first live trade**. Every historical year has now been
used for development, so this forward test on new data is the only clean test left. The
rules below decide whether it continues, stops or counts as support. They do not change
while it runs.

## What runs

- **Strategy:** the frozen candidate of [US100_CANDIDATE_PLAN.md](US100_CANDIDATE_PLAN.md),
  unchanged: FTMO `US100.cash`, range 16:30–16:45 broker time (09:30 New York), first
  15-minute close outside it per direction before 19:00 broker (12:00 New York), stop at
  the far side, target 1.5R, held until stop or target.
- **EA:** [`mt5/ORB_Framework.mq5`](../mt5/ORB_Framework.mq5) v1.30. Its defaults are these
  rules, and it compiles in MetaEditor with 0 errors and 0 warnings. Leave every input at
  its default except `InpRiskPercent`.
- **Two FTMO free-trial accounts**, run side by side: **A at 0.5%** risk per trade (the
  plan's rule) and **B at 1.5%**. Running both at once needs two MT5 installs, for example
  a second portable copy. With `InpLogFile = auto`, each account writes its own
  `ORB_<login>_US100.cash.csv` in the MT5 Common\Files folder.
- **Uptime:** a VPS or an always-on PC. The EA must be attached to a US100.cash chart
  before 16:30 broker time each trading day.

## Monthly check

Run once a month, and after any restart:

```powershell
python -m scripts.forward_reconcile --log "<Common>\Files\ORB_<login>_US100.cash.csv"
```

The script reads the EA's log and downloads that period's FTMO M1 bars (read-only). It
runs the Python engine on the same days and compares the two trade by trade. Output goes
to `outputs/forward/<stamp>/`.

## Pre-declared rules

R is net P/L (profit + commission + swap) ÷ the money at risk at entry. These are the
backtest figures the rules use (1,390 trades, 2021-10 to 2026-10):
- mean +0.081R per trade, with a standard deviation of 1.24R;
- worst drawdown 12.2% at 0.5% risk and 33.3% at 1.5%;
- longest losing streak 12 trades.

### 1. Fidelity first

Performance is not judged until the EA is shown to trade the backtested rules. Each
monthly check must show all three:
- at least 90% of the engine's signal days have a live trade with the same date and
  direction, and the other way round;
- the median entry-price difference is at most 1.5 points, about one spread;
- at least 90% of matched trades have the same exit reason.

If a check fails, the cause is found and fixed: downtime, rejected orders or clock
problems. The affected trades are logged as excluded, not dropped silently.

### 2. Checkpoint at 60 live trades (about 3 months)

Stop if the live mean R is below **−0.24**. That is the backtest mean minus two
standard errors over 60 trades (1.24 ÷ √60 ≈ 0.16), so falling below it would be an
unlikely result if the backtest were right.

### 3. Final read at 150 live trades (about 7 months)

The two accounts trade the same signals, so their R sequences are the same; the read uses
account A.

| Live mean R over 150 trades | Verdict |
|---|---|
| > 0 | **Supported**: worth a paid challenge at the account-A risk |
| −0.12 to 0 | **Inconclusive**: the edge, if any, is too small to see in 150 trades |
| < −0.12 (backtest mean − 2 SE) | **Not supported**: the candidate is retired |

### 4. Hard stops at any time

Either of these ends that account's test; the other account carries on:
- **Account A** draws down more than **12.2%**, or **account B** more than **33.3%**,
  measured from closed balance. That is worse than anything in five years of backtest.
- The account breaches FTMO's 5% daily or 10% total loss limit. For account B this is
  recorded as information, since its pass chance was only about 50%.

### 5. What does not change

- No rule, input or symbol changes during the test.
- Restarts are allowed, because the v1.30 log appends.
- Missed days, from downtime or rejected orders, are recorded in the monthly check, not
  re-traded.
- A losing streak shorter than 12 trades is normal and is not a reason to stop.
