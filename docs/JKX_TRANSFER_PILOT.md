# Pilot A: do stock-trained chart CNNs transfer to what FTMO trades? (pre-registered 9 October 2026)

**Question.** The JKX CNNs replicated in docs/JKX_REPLICATION_PLAN.md were trained only on
US stock charts from 1993–2000. Do the same frozen models predict returns of the non-stock
instruments a prop account trades: FX, metals, stock indices, energy, agriculture and
crypto?

**Prior evidence.**
- Hao (2025, *Journal of Futures Markets*) finds that image CNNs *trained on* commodity
  futures predict them, but that transfer from US to Chinese commodity markets fails.
- No study we found tests a stock-trained chart CNN on FX, index CFDs or crypto
  (literature check, 9 October 2026).

Our prior is therefore weak: a failure is the more likely outcome, and an informative one.

## Design (fixed before any data are examined)

- **Models.** The frozen five-seed ensembles from the replication, with no retraining,
  calibration or tuning of any kind:
  - **primary: I5/R5** (weekly);
  - secondary: I20/R20 (monthly);
  - secondary: I20/R5, once trained.
- **Instruments.**
  - **Test universe:** every FTMO-Demo symbol in the groups Forex, Exotics, Metals CFD, Cash
    CFD, Cash II CFD, Cash III CFD, Commodities, Agriculture, Crypto I CFD and Crypto II CFD.
  - **Control group:** the stock CFDs (Equities I and II), reported separately as an
    in-domain check.
  - **Excluded:** the custom symbol QQQ.WRDS.
- **Data.** FTMO-Demo daily (D1) bars from MT5:
  - open, high, low and close;
  - **tick volume** stands in for volume, since CFDs have no exchange volume.

  Each symbol uses whatever history it has.
- **Test window.** Formation dates from 2010-01-01 to 2025-12-31, with every forward return
  ending by 2025-12-31. **2026 bars are not used.** They are reserved as a forward holdout
  for anything this pilot leads to.
- **Images.** The same `orb_research/jkx_images.py` builder as the replication:
  - the close path comes from close-to-close returns;
  - a window qualifies when the symbol has a bar on every image day;
  - for crypto, 5 bars means 5 calendar days.
- **Formation and returns.** The last bar of each ISO week (R5) or month (R20). Returns are
  held from that close to the next formation close. They are gross: no spread or swap in
  the pilot.

## Statistics

**Primary.**
- Each week, compute the cross-sectional Spearman rank correlation (IC) between the I5/R5
  ensemble probability and the next-week return, over all test-universe instruments with an
  image that week. A week needs at least 10 instruments.
- Report the mean weekly IC and its Newey–West t-statistic (4 lags).
- **Transfer is supported if the mean IC is > 0 with t ≥ 2.0.**

**Secondary (reported, not gated).**
1. The IC by group: FX (Forex plus Exotics), metals, indices (cash CFDs plus DXY),
   energy/agriculture, crypto, and stock CFDs (the control).
2. **A time-series sign strategy.** Each instrument is long if p > 0.5, short otherwise.
   - Each position is scaled to 10% annualised volatility, using the 60-bar realised
     volatility known at formation.
   - Instruments are equally weighted.
   - Report the weekly Sharpe ratio and t.
3. **Baselines on the same sample**, IC and sign strategy:
   - 1-week reversal: −(the last 5 bars' return);
   - 12-month momentum: the return over bars t−251 to t−21.
4. The I20/R20 monthly IC (at least 10 instruments per month) and its sign strategy.

## What follows from the result

- **If the primary test passes,** a follow-up must confirm it on data not used here before
  anything is traded: the 2026 forward bars, and costs at FTMO spreads.
- **If it fails,** pilot A closes as a negative transfer result, consistent with Hao (2025).
  There is no rescue search: no retraining on these instruments, no subset selection, no
  other horizons or thresholds.

## Amendment (9 October 2026, before any pilot result was computed)

The MT5 download was stopped after 92 symbols because the terminal's minute-history cache was
filling the disk. These are the pilot's instruments; no result had been computed or looked at.

**Test universe: 92 of the ~106 pre-registered symbols.**
- **Complete groups:** Forex (28), Exotics (15), all cash indices including DXY and both oils
  (16), Metals (9), Agriculture (7), Commodities (2).
- **Crypto:** 15 of 30. Missing: 15 small Crypto II coins (AAV, ALG, BAR, GAL, GRT, ICP, IMX,
  LNK, MAN, NER, SAN, UNI, VEC, XLM, XTZ).

**Stock-CFD control group: not downloaded, so it is omitted from this run.** It may be added
later as a separate, labelled run.

Everything else (models, window, statistics, the pass rule) is unchanged.
