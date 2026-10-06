# XAUUSD ORB research, version 2

This project starts with a deliberately plain Python baseline. Its purpose
is to test whether the opening-range rule survives unseen dates and trading
costs; it does not target a specified monthly return.

## Status

The baseline code and synthetic verification are ready. A real FTMO-Demo
development-period baseline was run on 17 September 2026: 1,749 trades,
−0.120 mean modeled net R per trade, −62.22% cumulative return, and all seven
calendar years negative. Twelve representative trades were checked directly
against cached minute bars, including stop, target, time, gap, ambiguous
same-minute exits, and both 2024 US DST changes. The audit is saved under
`outputs/runs/<timestamp>/trade_audit.json`. These are provisional bar-based
results, not tick-exact fills. A second check read the native tick order for
eight selected exits. The four DST samples agreed with M1. Of four ambiguous
M1 exits with tick history, three touched target first and one touched stop
first. The tick sample is saved as `sample_tick_exit_comparison.json`. Revaluing
those three positions at their original size would recover about $86 before
the changed commission; even a deliberately favorable correction of all 13
ambiguous exits would recover at most about $472 at original sizes, versus
$3,111 modeled net loss. These figures are diagnostics, not a tick-replayed
equity curve. On 17 September 2026, the connected terminal
reported `FTMO Global Markets Ltd / FTMO-Demo` with `XAUUSD`: 100 oz per lot,
0.01 minimum/step, 0.01 price point and USD account currency. Native M1 bars
were downloaded in 105 UTC-month files from 2 January 2018 through 16 September
2026 (3,053,966 bars; 69.2 MB of Parquet). All files passed read-back validation;
24 weekdays without M1 bars are flagged as market closures to review.
FTMO real ticks were archived from 4 October 2021 through 16 September 2026:
200,890,543 bid/ask records in 1,278 nonempty UTC-day files (1.68 GB), all
read back successfully. The tick verifier found one M1 trading day with no
broker ticks, 1 December 2021, which remains an explicit gap. The exact server
is required by the configuration; a different login is rejected.

The 2025-and-later holdout is sealed by the configuration. No data from that
period is loaded into the baseline. The development interval starts in January
2018 and ends before 2025; native tick history starts in October 2021. The
2025-and-later data is stored for final testing but has not been used to choose
strategy settings. The first-day probe is a sample, not a retention guarantee.

On 18 September 2026, the fixed annual walk-forward buffer-selection run
completed on 2,454,505 cached development M1 bars. The training windows
selected 0.55 ATR for 2021, 2022 and 2023, then 0.35 ATR for 2024. The
concatenated following-year result was 371 trades, +0.0248 mean modeled net
R, profit factor 1.149, +3.82% total return over four years and 3.00% maximum
closed-equity drawdown. All four test years were positive, but 2021 was
essentially flat (+0.02%). Of the 371 exits, 342 occurred at the session
close, 26 at the stop and three at the 2R target. These results are not
fully independent because the buffer candidates were chosen after earlier
inspection of the development period. The 2025-onward final holdout remains
sealed.

## Broker clock

The M1 cache stores MT5 bar times as epoch seconds labelled UTC. Those
seconds carry the broker server's clock. On FTMO-Demo XAUUSD, the tick-volume
spike of every scheduled 08:30 New York release sits at 15:30 in the stored
clock: the first-Friday (NFP) peak lands there in 42 of 71 months from 2018 to
2024, and CPI or NFP on 2024-02-13 (both zones on winter time), 2024-06-12
(both on summer time), 2024-03-12, 2024-11-01, 2023-03-14 and 2023-11-03 (US and
EU daylight saving out of step) all print at 15:30–15:35. Real 08:30 New York
is 13:30 UTC in winter and 12:30 UTC in summer, so the server runs at New York
+ 7 h in every season. The weekly open at stored Monday 01:05 (Sunday 18:05
New York) and the Friday close at stored 23:54 (16:54 New York) agree.

Consequence: converting a New York + 7 h clock with New York DST rules, as the
loader does under the default `broker_clock: "stored_as_utc"`, places the
"08:20 New York" session at 06:20 New York in winter and 05:20 in summer
(11:20 and 09:20 UTC) and the "16:00" exit at 14:00 and 13:00 New York. The
profitable development-period configuration is therefore a late London-morning
breakout held to about the COMEX pit close, and its window moves one hour
between seasons. Every backtest remains internally consistent because the same
stored window is used each day, and the buffer, walk-forward and entry-rule
findings all describe that window. `broker_clock: "new_york_plus_7"` decodes the
cache correctly (subtract seven hours, localise as America/New_York). It is not
the default because switching it changes what every existing configuration
means; re-anchoring the session to real New York time and re-running the
sweep is additional development-period selection and should be treated as
such. `python run_all.py verify-clock --config configs/strategy_b_0820.json`
enforces release-time, weekly-boundary and DST checks on the 2018–2024 cache
before any corrected-clock research.

## Minimum-lot sampling correction and fixed Strategy B extension

The original $5,000 / 0.5%-risk walk-forward silently skipped a breakout when
its opposite-range stop required less than the broker's 0.01 minimum lot.
That omission depends on range width and price level, so it can change both
the training buffer choice and the unseen-year sample. It is a sizing defect
in this research comparison, not a trading signal. The original Strategy A
and B reports are preserved. A separate sensitivity uses $100,000 simulated
equity at the same 0.5% risk, 0.01 minimum/step and cost model, then reports
the fraction of valid signals that a $5,000 account could not size. This
$100,000 figure is a sampling convention, not a live account recommendation.
MT5 defines the broker-specific minimum and increment as
[`SYMBOL_VOLUME_MIN` and `SYMBOL_VOLUME_STEP`](https://www.mql5.com/en/docs/constants/environment_state/marketinfoconstants).

The corrected-clock Strategy B test repeats its frozen 60-minute B1 baseline
and one last B2 filter-transfer audit under research sizing. The B2 timing
and width rules are retired for Strategy B and are absent from the following
prespecified extension. The extension evaluates only 15/30/45/60-minute
opening ranges crossed with the existing 0.25/0.35/0.45/0.55 ATR buffers.
Each of four annual folds selects the highest training mean net R among cells
with at least 100 training trades; exact ties choose shorter range, then
smaller buffer. Only that cell runs in the following unseen year.

Before reading sweep outcomes, support was defined to require all of:
positive combined unseen mean R with a session-block-bootstrap
P(mean R ≤ 0) below 0.10; at least three of four unseen years positive;
and the top three eligible training cells fitting an adjacent 2×2 grid
block in every fold. Otherwise the intended 08:20 New York ORB is reported
unsupported by the 2018–2024 development data. No follow-on rescue search.

Decoding the broker clock also moves the UTC boundary used by the unchanged
daily ATR routine. Strategy A's pseudo-UTC day and Strategy B's real UTC day
therefore do not have identical ATR inputs. Strategy A's actual opening window
starts at 09:20 UTC in summer and 11:20 UTC in winter. Any live version needs
an explicit real-clock anchor; choosing one would be a new test, not a relabel.

## First run

Use Windows, Python 3.11–3.13 and a MetaTrader 5 terminal logged into the
intended FTMO-Demo account. From the repository root:

```powershell
python run_all.py backtest --config configs/xauusd_orb_research.json
```

Edit the JSON parameters before running. The command installs required Python
packages, checks the connected broker, prepares and verifies M1 and native tick
data through the development end date, runs the strategy, generates charts,
audits sample bars and tick exits, and saves a timestamped report bundle under
`outputs/runs/`. A completed first end-to-end run on 17 September 2026 used
the existing archive and needed no manual step after choosing the config.
`--cached-only` verifies and reuses the archive without MT5 connectivity;
`--skip-ticks` omits tick preparation and comparison.

The probe prints the company, server, exact symbol and approximate first
real-tick date. Compare it with the account you intend to trade. Edit
`expected_server`, `start`, `development_end`, and `holdout_start` in the JSON
config only after you have confirmed the available history. If multiple MT5
installations are open, pass `--terminal-path` to the intended
`terminal64.exe`. Do not share your login credentials.

The downloader gets M1 bars by month from the exact configured MT5 server,
stores Parquet files under `C:\data\ftmo_xauusd_m1` (outside OneDrive), and
saves source metadata. With `--with-ticks`, it also archives complete UTC
calendar days of native bid/ask quotes under `C:\data\ftmo_xauusd_ticks`.
Repeated timestamps are preserved in their original order. It resumes
completed periods and records any day where M1 exists but the broker serves no
ticks. The verifier reopens every file. The runner stops on
missing months, an obviously incomplete month, wrong broker, wrong contract
size, or a non-Bid chart mode. It never transmits orders.

The archived MT5 rate values are server wall times labelled UTC in the raw
cache. Strategy A deliberately retains that legacy interpretation. Strategy B
decodes them using the verified FTMO New York + 7-hour clock and then uses
`zoneinfo` for the real New York session; the raw files are not rewritten.
[MetaTrader's Python documentation describes UTC rate timestamps](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesrange_py),
but observed release and weekly-boundary timing on this FTMO-Demo cache
contradicts that interpretation for this terminal. The execution model uses
[Bid-based OTC bars and Ask-side execution](https://www.metatrader5.com/en/terminal/help/trading_advanced/price_data).

## Baseline rule

The provided configuration tests one session only: New York 09:30, 30-minute
observed range, M5 close-confirmed first breakout, entry at the next M1 open,
opposite-range stop, 2R target, 0.5% equity risk and a 16:00 New York time
exit. No filter, breakeven, trailing, martingale or same-day re-entry is active.
To test London 08:00 or New York 08:20 later, make separate configuration files
and runs; do not combine sessions in the baseline.

The engine reads OHLC from Bid bars. It approximates Ask as Bid plus each M1
bar's recorded spread, or an explicit fallback if the spread is zero. It applies
configured slippage to entry and exit, respects broker lot steps, and resolves
an M1 bar touching both stop and target as a stop. MAE/MFE omit the unknown
intraminute path after exit. These are conservative bar-data assumptions, not
tick-exact execution. Real-tick replay against the same server is required
before claiming high-fidelity execution.

**Cost calibration is still required.** FTMO says to check the instrument's
[specification in the trading platform](https://ftmo.com/en/faq/what-are-the-account-specifications/).
The development config models 0.0005% of notional per side for metals CFD,
inferred from [FTMO's 2025 published change](https://ftmo.com/en/blog/trading-updates/trading-update-25-sep-2025/)
to 0.0007% per side effective 29 September 2025. This does not prove the same
rate applied throughout 2018–2024 or that our account was charged identically.
The modeled commission paid across baseline trades was about $155.53. The
configured spread fallback and slippage are also
assumptions to be checked against account history. April, May, June, and
September 2021 have more than 5% zero-spread M1 bars (5.6%, 25.9%, 18.8%, and
55.9% respectively). There are no native broker ticks for those months to
recover the historical bid/ask path, so cost-sensitive inferences from them
remain weak.

## Outputs

One command saves under a new `outputs/runs/<timestamp>/` folder:

- `trades.csv`, `monthly_returns.csv`, `yearly_returns.csv`
- `metrics.json`, `diagnostics.json`, `data_provenance.json`, `config_used.json`
- `summary_report.md`
- equity, drawdown, monthly/yearly return, rolling-expectancy, rolling-Sharpe,
  and MAE/MFE HTML plots

The `--post-baseline-analysis` flag additionally runs seeded 10,000-path
reshuffle and bootstrap Monte Carlo methods and an **illustrative** prop-rule
replay, 95% trade/bootstrap confidence intervals, plus 1×/1.5×/2× cost
sensitivity. Run it only after inspecting the
baseline trades. Prop rules are read from `configs/prop_research_example.json`;
pass `--prop-rules PATH` for a reviewed rule set. The prop replay uses
closed trades, so it cannot prove compliance with intraday equity limits or
FTMO's actual terms. The example $5,000 / 10% / 5% / 5% / 10% rule set comes
from the research brief, not from a verified program contract.

## Fixed walk-forward buffer selection

Exploratory development work found that the unbuffered opening-range rule was
poor and that ATR-confirmed breakouts had a broad, more promising region. To
test whether a buffer selected using past years transfers to the next year,
run `python run_all.py walk-forward` using the local 2018–2024 M1 archive.
This command is separate from the ordinary configurable backtest and leaves
the browser dashboard unchanged.

The frozen structure is an 08:20 New York start, 60-minute observed range,
M5 close confirmation, first breakout only, opposite-range stop, 2R target,
16:00 time exit and 0.5% equity risk. The four allowed buffers are 0.25,
0.35, 0.45 and 0.55 times the already implemented prior daily ATR. For each
of four folds, all candidates run on three prior training years. The highest
training mean net R wins, with the lower buffer breaking an exact tie. That
one choice runs on the following year. The test years are 2021–2024, and
only their trades form the combined walk-forward curve. The runner refuses
2025-onward bars and records the exact choices and code hashes in
`outputs/walk_forward/`.

This procedure checks chronological transfer but does not erase earlier
selection bias: the candidate set itself was chosen after looking at
2018–2024. These test years are unseen to each *fold's* selection step, not
fully independent of the project's research history. The 2025-onward final
holdout remains sealed. Results are M1-bar simulations with assumed costs,
not tick-exact fills or evidence of prop-firm rule compliance.

## Prespecified entry-rule and exit arms

A descriptive analysis of the frozen development event table (`outputs/ml/`)
left two relationships whose session-block bootstrap intervals excluded zero:
first breakouts confirmed later after the opening range did worse, and
opening ranges that were narrow relative to ATR did worse. Late trades were
not bad trades so much as trades with no time — their MFE and MAE both
collapse and nearly all exit at the session close — which raised the question
of whether the forced exit, not the entry, was the cause. Both hypotheses were
formed on 2018–2024, so a walk-forward over 2021–2024 can only show whether a
percentile rule transfers, not whether the relationships generalise.

`python run_all.py filter-arms` runs fifteen arms through the same four folds
with the same buffer candidates. Inside each training window only, an arm
takes the buffer (the baseline's choice for "fixed" arms; re-selected after
the rules for "joint" arms), sets the timing cutoff at the 60th percentile of
minutes from range end to the confirming M5 close among completed training
trades, and sets the width floor at the 20th percentile of range width / ATR14.
Later exits (17:00, 18:30 in the project's session clock) keep the base entry
window of 400 minutes so that only the exit changes. Each arm is compared with
the arm that differs from it by one decision, using the expectancy difference
and a 4,000-draw session bootstrap. The baseline arm must reproduce
`walk-forward` exactly or no report is written.

Run of 18 September 2026 (`outputs/filter_arms/`), fixed buffers, 16:00 exit:

| Arm | Trades | Mean R | PF | Return | Max DD | Δ mean R vs baseline | 95% CI | P(Δ ≤ 0) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| Baseline | 371 | +0.0248 | 1.149 | +3.82% | 3.00% | — | — | — |
| Early only | 216 | +0.0591 | 1.306 | +5.39% | 1.79% | +0.034 | [−0.009, +0.077] | 0.064 |
| OR-width only | 277 | +0.0453 | 1.268 | +4.92% | 2.12% | +0.021 | [−0.012, +0.052] | 0.103 |
| Both | 158 | +0.0809 | 1.436 | +5.39% | 1.74% | +0.056 | [−0.012, +0.123] | 0.049 |

Fold means (baseline → early → width → both): 2021 +0.000 → −0.000 → −0.003 →
−0.001; 2022 +0.022 → +0.032 → +0.060 → +0.083; 2023 +0.063 → +0.105 → +0.137
→ +0.218; 2024 +0.015 → +0.080 → +0.023 → +0.077. The trades the early rule
removed averaged −0.023 R and the width rule's −0.036 R: the rules mostly drop
near-zero trades, which is why expectancy and drawdown improve much more than
return. Later exits hurt the baseline (17:00: +0.013 R, P(Δ ≤ 0) = 0.90;
18:30: +0.018 R) and did not rescue late entries, so the timing weakness is
not an artefact of the forced exit. Joint selection chose 0.35 in 2022–2024
with both rules (234 trades, +0.086 R, +8.25%, 1.94% drawdown), but the
sessions it added at the lower buffer lost on average; its gain comes from
earlier entries on shared sessions. Cutoffs were 245/225/225/210 minutes and
floors 0.104/0.111/0.124/0.110 ATR by fold.

Reading: both rules transfer in the intended direction and the combination
is the best development-period configuration so far, at about forty trades a
year. No interval excludes zero convincingly, the fold gains lean on 2023 for
the width rule, and the years tested are the years the rules were chosen on.
This is not a validated edge. Independent evidence requires the same frozen
rules on another instrument or session, or the sealed 2025-onward holdout after
the full specification — including the broker-clock decision above — is frozen.

## Strategy B prespecified 4x4 sweep and closing verdict

The 60-minute opening range entered the project through Strategy A work on the
broker-clock window, so it carried no authority for the true New York session.
The single pre-registered extension was therefore run on 18 September 2026:
opening range in 15/30/45/60 minutes crossed with the frozen buffers
0.25/0.35/0.45/0.55, under corrected clock and $100,000 research sizing, with
no filters and nothing else changed. Each training window scores all sixteen
cells, cells with fewer than `min_trades_for_inference` training trades cannot
be selected, the highest training mean net R wins with ties resolved toward the
shorter range and then the lower buffer, and that one cell runs the following
unseen year. Only selected-path trades reach a test year; no all-cell
out-of-sample surface is produced. The support rule was written into the
report template before the run.

Selected path: 2021 tested 60m/0.25 (90 trades, -0.0141 R, -0.63%); 2022 tested
15m/0.25 (193, +0.0391, +3.63%); 2023 tested 30m/0.55 (81, +0.0840, +3.35%);
2024 tested 30m/0.55 (71, -0.0178, -0.62%). Combined 2021-2024: 435 trades,
mean R +0.0272, median R -0.0185, profit factor 1.127, return +5.76%, maximum
closed drawdown 3.75% over 301 calendar days, longest losing streak 8, two
positive years of four, session-block bootstrap 95% interval
[-0.0273, +0.0851] and P(mean R <= 0) = 0.181.

Three of four support conditions failed. The bootstrap probability is 0.181
against a required 0.10; two years were positive against a required three; and
the top three eligible cells did not fit an adjacent 2x2 block in any fold.
**The intended 08:20 New York XAUUSD ORB is unsupported by the 2018-2024
development data under the prespecified grid.** The grid is closed and no
rescue search follows.

The post-hoc description in `robustness.md` is the part worth carrying forward.
The positive headline mean is concentrated in five trades of 435, each a 2R
target hit, which supply +9.95 R of the +11.82 R total while the remaining 430
average +0.0043 R and the median trade loses. The training surface moves as an
era: profitable cells run 0/16, 6/16, 11/16 and 16/16 across the four training
windows, so the selection statistic mostly measures which years are in the
window. Relative cell rank persists somewhat between folds, but the level does
not, and the level is what pays. Training mean R and next-year mean R correlate
-0.12 across the four folds, and the best training window ever seen produced a
negative test year. Finally, at $5,000 and 0.5% risk, 49% of the selected 2024
signals were too small to size at the 0.01-lot minimum, so the true session is
also a poor structural fit for a small prop-challenge account.

What this does not say: Strategy A is not thereby validated. Both strategies
remain development-period results shaped by prior selection on the same cache,
and the honest next test for either is the same frozen specification on another
instrument, or the sealed 2025-onward holdout after a complete freeze.

## Strategy A replication on XAGUSD, and project close

Strategy A was never validated; it was only never tested. Its exam was the
frozen specification replayed on a second instrument, pre-registered before the
silver cache existed, with a declared reading rule and one run. It failed.

Combined unseen 2021-2024 on XAGUSD: 330 trades, mean net R -0.0187, profit
factor 0.899, return -3.02%, maximum closed drawdown 3.82%, one nominally
positive year of four, session-block bootstrap 95% interval [-0.0708, +0.0333]
with P(mean R <= 0) = 0.767. The pre-registered rule fails on mean R <= 0 and
separately on the positive-years condition.

Fifteen of the sixteen training cells are negative, against selected training
cells of +0.070, +0.049, +0.044 and +0.041 on gold. Selection did not go wrong
on silver; there was nothing to select. The buffer gradient does replicate
directionally, expectancy improving monotonically with the buffer inside every
fold, but it never crosses zero. Median stop distance is 1.945% of price
against 0.22-0.58% on gold, so 313 of 330 exits are the session close and one
trade in 330 reached its target: the specification degenerates into a
directional hold. That is the frozen rule meeting a different range geometry,
and an ATR-normalised variant invented after seeing this would be exactly the
rescue the pre-registration forbids.

Consequence, as written down in advance: Strategy A does not go to the 2025+
holdout and the ORB project closes on this development data. The holdout stays
sealed and unspent, which is the point of having kept it.

Both strategies are now closed. Strategy B failed its prespecified 4x4 sweep;
Strategy A failed its replication. Neither was ever a validated edge, and
neither is one now. What survives is the machinery: verified broker data with
provenance, a clock verifier that caught a real timestamp fault, walk-forward
selection that cannot see its test year, a leakage-audited feature table, and
pre-registered decision rules that were honoured when they went against us.

## Research sequence

1. Confirm target account, server, symbol, contract, spread and data coverage.
2. Run the unmodified baseline on the development period only.
3. Audit at least ten signals and fills, including DST weeks and losing
   trades. Twelve bar-level checks passed and eight selected exits were
   compared with archived ticks; broader tick replay and MT5 Strategy Tester
   comparison are next.
4. Calibrate commission and slippage; run normal, 1.5× and 2× cost scenarios.
5. Treat the 09:30 unbuffered baseline as a failed benchmark. The exploratory
   08:20 buffer sweep is already complete; run the fixed annual walk-forward
   selection above without extending the buffer grid.
5b. The entry-rule and exit arms above are complete. Do not retune their
   percentiles or add rules against 2021–2024. The corrected-clock 08:20
   session is now a separate Strategy B; its frozen transfer test is in
   `outputs/strategy_b/report.md`. The legacy window remains Strategy A.
5d. Strategy A is closed on this data. Its XAGUSD replication failed the
   pre-registered rule, so it does not reach the holdout. Do not re-run it with
   an ATR stop, another instrument chosen after the fact, a different session,
   or a reweighted cost model in the hope of a different answer. Any future ORB
   work starts from new data and a new pre-registration.
5c. Strategy B is closed on this data. The frozen filters did not transfer, and
   the single prespecified 4x4 opening-range sweep failed three of its four
   pre-declared support conditions. Do not extend the grid, add sessions,
   revisit the percentiles, or search for a variant that rescues it. Any future
   Strategy B work must start from different data, not from a new search over
   2018–2024.
6. Freeze the strategy before the final holdout. If it is changed afterward,
   that period becomes development data.

Data acquisition alone does not establish realistic fills, commission, or an
out-of-sample edge.
