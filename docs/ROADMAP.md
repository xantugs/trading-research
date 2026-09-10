# Roadmap

## Ready now

- WRDS QQQ complete-NBBO Parquet is converted directly to MT5 quote ticks.
- The full 00:00–24:00 New York source day is eligible with no session filter.
- Invalid/crossed states and metadata-only repeated prices are removed; every
  bid/ask change remains in source order.
- Daily MT5 CSVs and manifests are independently verified.
- The custom-symbol importer supports exact files and wildcard batches, imports
  one day at a time, and replaces days safely.
- EA v1.50 and the importer compile with zero errors and zero warnings.
- The setup wrapper installs dependencies and binaries, converts, and verifies.

## Next evidence

1. Run `Import_WRDS_Ticks` inside MT5 for the prepared 2018-01-02 sample.
2. Confirm the `QQQ.WRDS` tick history, M1/M15 bars and New York timestamps.
3. Import one month and run the EA with Every tick based on real ticks.
4. Hand-check ten trades with optional filters and management disabled.
5. Decide how to model QQQ commissions and validate that cost mechanism.
6. Import 2018–2022 in monthly batches, deleting staged CSVs after each verified
   MT5 import to control disk use.
7. Define development, validation and untouched holdout periods before tuning
   range, confirmation, stop or filter parameters.

## Later work

- Automate terminal-side import verification if MT5 exposes a dependable local
  command or script interface for querying custom tick counts.
- Add an optional consolidated-trade overlay only if a rule needs LAST or volume;
  do not mix it into NBBO bid/ask ordering without an explicit contract.
- Add tested commission, latency, slippage and market-impact assumptions.
- Compare MT5 output with an independent event-order audit for selected days.
- Add multi-candle opening ranges and partial targets after the base setup has an
  untouched out-of-sample result.

The Python engine, Streamlit workspace, M1 data and Dukascopy code are retained
for reference. They are outside the active production path.
