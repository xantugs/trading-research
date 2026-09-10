# MetaTrader 5 workflow

The active workflow is documented in [WRDS QQQ ticks in MetaTrader 5](MT5_WRDS.md).
It covers conversion, verification, custom-symbol import, Strategy Tester setup
and the ten-trade smoke check.

Compile the EA and importer directly with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\compile_mt5.ps1
```

The helper fails unless both programs compile with zero errors and zero warnings.
