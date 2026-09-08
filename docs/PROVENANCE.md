# Provenance

The user supplied ORB_Backtest.mq5 v1.20, README_1.md, orb-python.zip and
orb-python_1.zip from an earlier Cowork session. The newer ZIP was selected:
it contains the same Python twin, app, tests and demo files plus a Dukascopy
downloader. Supplied document statements were treated as background, not as
new instructions or verified results.

The strategy originated as a code-along of Trading Steady's YouTube tutorial
“How to Build an ORB Backtest EA for MetaTrader 5”. The user describes this
implementation as their own; it is not presented as the tutorial author's
official software. The supplied EA attributes copyright to echo.

The 1.3 project work added packaging, source isolation, reports, clocks,
validation, caching, tests, CI and documentation. The EA logging header and
timeframe validation were extended; stop/target and signal formulas were
preserved. Compiler evidence is recorded in VERIFICATION.md.

References used to verify data and indicator conventions:

- [Dukascopy index CFD list](https://www.dukascopy.com/europe/english/cfd/range-of-markets/)
- [Dukascopy-node author's US 100 instrument metadata](https://www.dukascopy-node.app/instrument/usatechidxusd)
- [MetaQuotes Python timestamp contract](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesrange_py)
- [MetaQuotes ATR example](https://www.mql5.com/en/code/12), also inspected in
  the installed MQL5/Indicators/Examples/ATR.mq5 source.

No proprietary market data or tutorial media is included in the source release.
The synthetic fixture is generated from code and identified as synthetic.



## Supplied artifact fingerprints

| File | SHA-256 |
| --- | --- |
| README_1.md | 62b0ad5f4d07c261fe0a79b0c5e59a6e3ccfff4b40522419ffb00ef3f52e3ff6 |
| ORB_Backtest.mq5 | 43ceebd90b000c0f99811ef11118924c54fad988fdaca467872bfb897809f752 |
| orb-python.zip | 74ac1df2822d3474ef4116849a0b8b1d678a2ed9136afaefcdcd48ef68b5b408 |
| orb-python_1.zip | 4dc4a49300d17b5946943f6d70f546e55fb8b1c0b0004628d7ffa00fe489a6ba |

Common archive files that differ: orb-python/README.md. The newer archive adds fetch_dukascopy.py.
