# Verification record — 2026-09-10

## Release 1.7.0 checks

Local environment: Windows, Python 3.13.5, NumPy 2.3.5 and pandas 2.3.3.

- Full app-free suite: **187 pytest tests passed** in 20.90 seconds.
- Ruff: all checks passed.
- Package: source distribution and `py3-none-any` wheel built successfully.
- MetaEditor: EA v1.50 and `Import_WRDS_Ticks` each compiled with **zero errors
  and zero warnings**.
- Setup wrapper: installed both binaries into the detected terminal data folder,
  converted a real WRDS day into MT5 `Common\Files`, and ran the verifier.

## Real-data conversion smoke

Source: `QQQ_2018-01-02_quotes.parquet` under the local 2018 WRDS archive.

- 600,190 source rows;
- 599,409 valid non-crossed NBBO rows;
- 1 invalid row and 780 crossed rows removed;
- 576,543 consecutive unchanged bid/ask states removed;
- 22,866 MT5 price ticks written, a 26.2× row reduction;
- 6,023 adjacent output pairs share a millisecond and remain in source order;
- output schema, timestamps, prices, count, byte size and SHA-256 passed the
  independent streaming verifier;
- first tick `2018-01-02 04:00:00.061`, last tick near 20:00 New York.

The source window was the entire New York calendar day. The 04:00–20:00 observed
bounds reflect actual QQQ quote availability that day, not a converter filter.

## Pending terminal evidence

- Run `Import_WRDS_Ticks` inside MT5 and confirm the sample appears under
  `QQQ.WRDS` with the correct date and tick count.
- Run one month in Strategy Tester with Every tick based on real ticks.
- Complete the ten-trade visual audit.
- Add and validate QQQ commissions before interpreting net performance.
- Run the configured GitHub Actions matrix after pushing.

Compilation and CSV verification demonstrate code and file readiness. They do
not prove the terminal accepted `CustomTicksReplace` or that strategy results
match the intended rules; those require the manual MT5 checks above.
