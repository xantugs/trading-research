//+------------------------------------------------------------------+
//|                                                 ORB_Backtest.mq5 |
//|            Opening Range Breakout - backtest / research EA (MT5) |
//+------------------------------------------------------------------+
//
//  WHAT IT DOES
//  ------------
//  1. Builds one opening range each day from the observed opening candle or a
//     prior-session percentile forecast, optionally adjusted by premarket RV.
//  2. Trades the FIRST confirmed breakout of the day before a cutoff hour:
//       - close-confirmation mode: a candle must CLOSE outside the range
//         (previous candle was not already beyond that boundary), then enters
//         at the next candle's open
//       - immediate mode: entry as soon as price crosses the range
//  3. Stop-loss uses a range multiple or the range midpoint. Take-profit =
//     initial risk x TP ratio.
//  4. Position size = risk % of equity / stop distance.
//  5. Optional: end-of-day flat exit, range-vs-ATR filter, higher-timeframe
//     MA trend filter, move-to-breakeven, trailing stop.
//  6. Writes every closed trade to a CSV in the terminal's Common\Files
//     folder (shared by the tester and the live terminal) so results can be
//     analysed in Python / Excel and reconciled with the Python twin.
//  7. Volume self-check: the real session-open candle carries a tick-volume
//     spike.  If the "range candle" doesn't, the EA warns - the usual cause
//     is a broker whose server clock does not follow US daylight saving, so
//     the New York open drifts by an hour for part of the year.
//
//  SETUP
//  -----
//  - Save to  <MT5 data folder>\MQL5\Experts\ORB_Backtest.mq5  and press
//    F7 (Compile) in MetaEditor.
//  - ALL TIMES ARE SYMBOL HISTORY TIME.  The WRDS QQQ custom symbol keeps New
//    York wall time, so its defaults are range = 09:30 and cutoff = 12:00.
//  - Strategy Tester: use "Every tick based on real ticks" where available,
//    otherwise "1 minute OHLC". Stops and targets need an intrabar path even
//    when entries use close confirmation.
//  - The trade log lands in  <Common data folder>\Files\<InpLogFile>; the
//    full path is printed to the journal at start.  Logging is switched off
//    automatically during optimisation.
//
//+------------------------------------------------------------------+
#property copyright   "echo"
#property version     "1.50"
#property description "Opening Range Breakout EA - research / backtesting tool."
#property description "Captures one candle's high/low each day and trades the first"
#property description "confirmed breakout of the day before a cutoff hour."
#property description "Defaults use New York wall time for the WRDS custom symbol."

#include <Trade/Trade.mqh>

#define ORB_BOX_PREFIX "ORB_box_"
#define ORB_VERSION    "1.50"

//+------------------------------------------------------------------+
//| Enums (the trailing comments are what MetaTrader shows in the    |
//| input drop-downs)                                                |
//+------------------------------------------------------------------+
enum ENUM_ORB_HOUR
  {
   HOUR_00 = 0,    // 00:00
   HOUR_01 = 1,    // 01:00
   HOUR_02 = 2,    // 02:00
   HOUR_03 = 3,    // 03:00
   HOUR_04 = 4,    // 04:00
   HOUR_05 = 5,    // 05:00
   HOUR_06 = 6,    // 06:00
   HOUR_07 = 7,    // 07:00
   HOUR_08 = 8,    // 08:00
   HOUR_09 = 9,    // 09:00
   HOUR_10 = 10,   // 10:00
   HOUR_11 = 11,   // 11:00
   HOUR_12 = 12,   // 12:00
   HOUR_13 = 13,   // 13:00
   HOUR_14 = 14,   // 14:00
   HOUR_15 = 15,   // 15:00
   HOUR_16 = 16,   // 16:00
   HOUR_17 = 17,   // 17:00
   HOUR_18 = 18,   // 18:00
   HOUR_19 = 19,   // 19:00
   HOUR_20 = 20,   // 20:00
   HOUR_21 = 21,   // 21:00
   HOUR_22 = 22,   // 22:00
   HOUR_23 = 23    // 23:00
  };

enum ENUM_ORB_DIRECTION
  {
   DIR_BOTH       =  0,   // Long and short
   DIR_LONG_ONLY  =  1,   // Long only
   DIR_SHORT_ONLY = -1    // Short only
  };

enum ENUM_ORB_ENTRY_MODE
  {
   ENTRY_CLOSE_CONFIRM = 0,   // Wait for a candle to close outside the range
   ENTRY_IMMEDIATE     = 1    // Enter as soon as price crosses the range
  };

enum ENUM_ORB_RANGE_MODE
  {
   RANGE_OBSERVED              = 0,   // Observed opening candle
   RANGE_HISTORICAL_PERCENTILE = 1,   // Prior-session opening-range percentile
   RANGE_HISTORICAL_PREMARKET  = 2    // Historical percentile adjusted by premarket volatility
  };

enum ENUM_ORB_STOP_MODE
  {
   STOP_RANGE_MULTIPLE = 0,   // Distance measured from breakout boundary
   STOP_MIDPOINT       = 1    // Middle of opening range
  };

//+------------------------------------------------------------------+
//| Inputs                                                           |
//+------------------------------------------------------------------+
input group "Timing (symbol history time)"
input ENUM_TIMEFRAMES      InpTimeframe      = PERIOD_M15;          // Opening-range / signal timeframe
input ENUM_ORB_HOUR        InpOrbHour        = HOUR_09;             // Opening-range candle: hour
input int                  InpOrbMinute      = 30;                  // Opening-range candle: minute
input ENUM_ORB_HOUR        InpCutoffHour     = HOUR_12;             // No new entries from this hour

input group "Strategy"
input ENUM_ORB_ENTRY_MODE  InpEntryMode      = ENTRY_CLOSE_CONFIRM; // Entry mode
input ENUM_ORB_DIRECTION   InpDirection      = DIR_BOTH;            // Trade direction
input ENUM_ORB_RANGE_MODE  InpRangeMode      = RANGE_OBSERVED;      // Range construction
input int                  InpRangeSessions  = 20;                  // Historical sessions
input double               InpRangePercentile= 50.0;                // Historical percentile (0-100]
input double               InpRangeMultiplier= 1.0;                 // Forecast width multiplier
input int                  InpPremarketMinutes=120;                 // Volatility window before range
input double               InpPremarketSensitivity=0.5;             // Volatility adjustment power
input int                  InpConfirmationBars=1;                    // Consecutive closes outside range
input bool                 InpConfirmationColor=false;               // Require green longs / red shorts
input double               InpTpRatio        = 1.5;                 // Take-profit = initial risk x this
input double               InpSlRatio        = 1.0;                 // Stop-loss = opening range x this
input ENUM_ORB_STOP_MODE   InpStopMode       = STOP_RANGE_MULTIPLE; // Initial stop construction
input double               InpRiskPercent    = 2.0;                 // Risk per trade (% of equity)

input group "End of day"
input bool                 InpCloseEod       = false;               // Close all positions at end of session
input ENUM_ORB_HOUR        InpCloseHour      = HOUR_15;             // Close from this hour ...
input int                  InpCloseMinute    = 45;                  // ... and this minute

input group "Opening-range filters"
input int                  InpAtrPeriod      = 14;                  // Daily ATR period (for range / ATR)
input double               InpMinRangeAtr    = 0.0;                 // Skip day if range < ATR x this (0 = off)
input double               InpMaxRangeAtr    = 0.0;                 // Skip day if range > ATR x this (0 = off)
input int                  InpRangeLookbackMinutes=120;              // Prior window for range proportion
input double               InpMaxRangeLookbackRatio=0.0;             // Max range / prior span (0 = off)
input int                  InpVolumeLookback = 8;                   // Candles before the range candle for the volume check
input double               InpVolumeSpikeMin = 0.0;                 // Quote-count diagnostic (0 = off for cleaned WRDS ticks)
input bool                 InpSkipNoSpike    = false;               // Skip the day when the volume check fails

input group "Higher-timeframe trend filter"
input int                  InpFilterMaPeriod = 0;                   // MA period (0 = filter off)
input ENUM_TIMEFRAMES      InpFilterTF       = PERIOD_H1;           // MA timeframe
input ENUM_MA_METHOD       InpFilterMaMethod = MODE_EMA;            // MA method
input int                  InpFilterSlopeBars=0;                     // Completed HTF bars for slope
input double               InpFilterMinSlopePct=0.0;                 // Required signed slope percent

input group "Exit management (R = initial risk distance)"
input double               InpBreakEvenAtR   = 0.0;                 // Move SL to breakeven at +R (0 = off)
input double               InpBreakEvenLockR = 0.0;                 // Breakeven locks in this many R
input double               InpTrailStartR    = 0.0;                 // Start trailing at +R (0 = off)
input double               InpTrailDistR     = 1.0;                 // Trail distance behind price (R)
input double               InpTrailStepR     = 0.1;                 // Minimum SL improvement per modify (R)

input group "Trade management"
input string               InpComment        = "ORB bot";           // Trade comment
input ulong                InpMagic          = 20260512;            // Magic number
input int                  InpDeviation      = 10;                  // Max slippage (points)
input double               InpMaxSpreadPoints= 0.0;                 // Skip wider spread (points, 0 = off)
input int                  InpMinStopPoints  = 0;                   // Minimum initial stop (points, 0 = off)
input bool                 InpAllowLiveTrading=false;               // Explicitly allow non-tester entries

input group "Logging and display"
input string               InpLogFile        = "ORB_trades.csv";    // Trade log in Common\Files (empty = off)
input bool                 InpShowPanel      = true;                // Show status panel on the chart
input bool                 InpDrawBox        = true;                // Draw the opening range on the chart

//+------------------------------------------------------------------+
//| One open trade, kept until its closing deal is logged            |
//+------------------------------------------------------------------+
struct TradeRecord
  {
   ulong    order;        // ticket of the opening order
   ulong    position;     // position id (from the opening deal)
   int      direction;    // +1 long, -1 short
   datetime signalTime;
   datetime orbTime;
   double   orbHigh;
   double   orbLow;
   double   rangeAtr;     // opening range / daily ATR (0 = unavailable)
   double   volRatio;     // range-candle volume / average of the candles before it (0 = unavailable)
   double   requestedEntry;// quote used to build the order
   double   entry;        // actual fill price
   datetime entryTime;
   double   sl;           // initial stop
   double   tp;           // initial target
   double   lots;
   double   commission;   // opening commission
   bool     filled;       // opening deal seen
  };

//+------------------------------------------------------------------+
//| Globals                                                          |
//+------------------------------------------------------------------+
CTrade      trade;
int         g_maHandle     = INVALID_HANDLE;
int         g_atrHandle    = INVALID_HANDLE;
int         g_logHandle    = INVALID_HANDLE;
datetime    g_lastBarTime  = 0;       // open time of the bar most recently processed
datetime    g_currentDay   = 0;       // 00:00 (server time) of the day we are in
bool        g_orbSet       = false;   // do we have today's opening range yet?
double      g_orbHigh      = 0.0;
double      g_orbLow       = 0.0;
datetime    g_orbTime      = 0;
double      g_orbRangeAtr  = 0.0;
double      g_orbVolRatio  = 0.0;
bool        g_tradeTaken   = false;   // has today's one breakout attempt been used?
bool        g_eodDone      = false;   // end-of-day close finished for today
string      g_dayStatus    = "";      // shown on the panel while no range is set
int         g_closedCount  = 0;       // rows written to the log this run
TradeRecord g_open[];                 // trades opened by this EA, not yet logged

//+------------------------------------------------------------------+
//| Expert initialization                                            |
//+------------------------------------------------------------------+
int OnInit()
  {
//--- validate inputs
   if(InpOrbMinute < 0 || InpOrbMinute > 59)
      return(Fail("ORB minute must be between 0 and 59"));

   const int tfMinutes = PeriodSeconds(InpTimeframe) / 60;
   if(tfMinutes <= 0 || ((int)InpOrbHour * 60 + InpOrbMinute) % tfMinutes != 0)
      return(Fail("ORB minute must sit on a " + EnumToString(InpTimeframe) + " candle boundary"));

   if((int)InpOrbHour >= (int)InpCutoffHour)
      return(Fail("ORB hour must be earlier than the cutoff hour"));

   if(InpCloseEod)
     {
      if(InpCloseMinute < 0 || InpCloseMinute > 59)
         return(Fail("Close minute must be between 0 and 59"));
      if((int)InpCloseHour < (int)InpCutoffHour)
         return(Fail("End-of-day close hour must not be earlier than the cutoff hour"));
     }

   if(InpTpRatio <= 0.0 || InpSlRatio <= 0.0)
      return(Fail("TP and SL ratios must be positive"));

   if(InpRangeSessions < 1 || InpRangePercentile <= 0.0 || InpRangePercentile > 100.0 ||
      InpRangeMultiplier <= 0.0 || InpPremarketMinutes < 1 || InpPremarketSensitivity < 0.0)
      return(Fail("Forecast-range inputs are invalid"));

   if(InpConfirmationBars < 1)
      return(Fail("Confirmation bars must be positive"));

   if(InpRiskPercent <= 0.0 || InpRiskPercent > 100.0)
      return(Fail("Risk percent must be between 0 and 100"));

   if(InpMinRangeAtr < 0.0 || InpMaxRangeAtr < 0.0 || InpVolumeSpikeMin < 0.0 || InpVolumeLookback < 0 ||
      InpRangeLookbackMinutes < 1 || InpMaxRangeLookbackRatio < 0.0 || InpFilterSlopeBars < 0 ||
      InpFilterMinSlopePct < 0.0 || InpMaxSpreadPoints < 0.0 || InpMinStopPoints < 0)
      return(Fail("Filter values cannot be negative"));

   if(InpFilterMinSlopePct > 0.0 && (InpFilterMaPeriod < 1 || InpFilterSlopeBars < 1))
      return(Fail("MA slope filter needs a positive MA period and slope lookback"));

   if(InpBreakEvenAtR < 0.0 || InpBreakEvenLockR < 0.0 || InpTrailStartR < 0.0 || InpTrailStepR < 0.0)
      return(Fail("Exit-management R values cannot be negative"));

   if(InpTrailStartR > 0.0 && InpTrailDistR <= 0.0)
      return(Fail("Trail distance must be positive when trailing is on"));

//--- trade object
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints((ulong)InpDeviation);
   trade.SetTypeFillingBySymbol(_Symbol);

//--- optional trend filter
   if(InpFilterMaPeriod > 0)
     {
      g_maHandle = iMA(_Symbol, InpFilterTF, InpFilterMaPeriod, 0, InpFilterMaMethod, PRICE_CLOSE);
      if(g_maHandle == INVALID_HANDLE)
        {
         Print("ORB EA: failed to create the trend-filter MA indicator");
         return(INIT_FAILED);
        }
     }

//--- daily ATR, used for the range filter and logged with every trade
   if(InpAtrPeriod > 0)
     {
      g_atrHandle = iATR(_Symbol, PERIOD_D1, InpAtrPeriod);
      if(g_atrHandle == INVALID_HANDLE)
         Print("ORB EA: could not create the daily ATR - range/ATR filter and column disabled");
     }
   if(g_atrHandle == INVALID_HANDLE && (InpMinRangeAtr > 0.0 || InpMaxRangeAtr > 0.0))
      return(Fail("Range/ATR filter needs a daily ATR period greater than 0"));

//--- trade log
   OpenLog();

//--- start from the current bar so a stale signal is never traded on attach
   g_lastBarTime = iTime(_Symbol, InpTimeframe, 0);
   g_currentDay = DayStart(TimeCurrent());
   ArrayResize(g_open, 0);
   RestoreDailyState();
   RecoverTodayRange();

//--- summary
   Print("ORB EA v", ORB_VERSION, " started on ", _Symbol, " ", EnumToString(InpTimeframe));
   PrintFormat("  Opening-range candle %02d:%02d, entries until %02d:00 (server time), entry mode: %s",
               (int)InpOrbHour, InpOrbMinute, (int)InpCutoffHour, EntryModeText());
   PrintFormat("  Range: %s, TP %.2f x risk, stop: %s, risk %.2f%% per trade, direction: %s",
               RangeModeText(), InpTpRatio, StopModeText(), InpRiskPercent, DirectionText());
   if(InpCloseEod)
      PrintFormat("  End-of-day close at %02d:%02d", (int)InpCloseHour, InpCloseMinute);
   if(InpMinRangeAtr > 0.0 || InpMaxRangeAtr > 0.0)
      PrintFormat("  Range filter: %.2f - %.2f x daily ATR(%d) (0 = no limit)", InpMinRangeAtr, InpMaxRangeAtr, InpAtrPeriod);
   if(g_maHandle != INVALID_HANDLE)
      PrintFormat("  Trend filter: %d-period %s on %s",
                  InpFilterMaPeriod, EnumToString(InpFilterMaMethod), EnumToString(InpFilterTF));
   if(InpBreakEvenAtR > 0.0)
      PrintFormat("  Breakeven at +%.2fR, locking %.2fR", InpBreakEvenAtR, InpBreakEvenLockR);
   if(InpTrailStartR > 0.0)
      PrintFormat("  Trailing from +%.2fR, %.2fR behind price, %.2fR minimum step",
                  InpTrailStartR, InpTrailDistR, InpTrailStepR);

   UpdatePanel();
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Expert deinitialization                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   if(g_maHandle != INVALID_HANDLE)
     {
      IndicatorRelease(g_maHandle);
      g_maHandle = INVALID_HANDLE;
     }
   if(g_atrHandle != INVALID_HANDLE)
     {
      IndicatorRelease(g_atrHandle);
      g_atrHandle = INVALID_HANDLE;
     }
   if(g_logHandle != INVALID_HANDLE)
     {
      FileClose(g_logHandle);
      g_logHandle = INVALID_HANDLE;
     }
//--- keep boxes and panel on the tester chart so the run can be inspected
   if(!MQLInfoInteger(MQL_TESTER))
     {
      ObjectsDeleteAll(0, ORB_BOX_PREFIX);
      Comment("");
     }
  }

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
  {
//--- end-of-day flat exit (cheap no-op when off or already done)
   CloseAtEndOfDay();

//--- breakeven / trailing run on every tick (cheap no-op when both are off)
   ManageOpenPositions();

//--- bar-based logic: daily reset, opening-range capture, close-confirmed entries
   if(IsNewBar())
      OnNewBar();

//--- tick-based entries
   if(InpEntryMode == ENTRY_IMMEDIATE && g_orbSet)
      CheckImmediateBreakout();
  }

//+------------------------------------------------------------------+
//| Deals: fill in opening details, log closing deals                |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
  {
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD)
      return;
   if(!HistoryDealSelect(trans.deal))
      return;
   if(HistoryDealGetString(trans.deal, DEAL_SYMBOL) != _Symbol)
      return;
   if(HistoryDealGetInteger(trans.deal, DEAL_MAGIC) != (long)InpMagic)
      return;

   if(HistoryDealGetInteger(trans.deal, DEAL_ENTRY) == DEAL_ENTRY_IN)
      OnEntryDeal(trans.deal);
   else
      OnExitDeal(trans.deal);     // OUT, INOUT and OUT_BY all close (part of) a position
  }

//+------------------------------------------------------------------+
//| Runs once at the open of every new bar on the signal timeframe   |
//+------------------------------------------------------------------+
void OnNewBar()
  {
   ResetOnNewDay(g_lastBarTime);

//--- the most recent closed signal bar
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyRates(_Symbol, InpTimeframe, 1, 1, rates) != 1)
      return;   // history not ready yet
   const MqlRates last = rates[0];

//--- Forecast ranges are known at the opening time. Observed ranges are known
//    only after the selected candle closes.
   if(!g_orbSet)
     {
      if(InpRangeMode != RANGE_OBSERVED)
         TryForecastOrb(g_lastBarTime);
      if(!g_orbSet && InpRangeMode == RANGE_OBSERVED)
         TryCaptureOrb(last);
      UpdatePanel();
      return;
     }

   UpdatePanel();

   if(InpEntryMode != ENTRY_CLOSE_CONFIRM)
      return;
   if(!IsBeforeCutoff(g_lastBarTime))
      return;

   CheckCloseBreakout();
  }

//+------------------------------------------------------------------+
//| Bar-close confirmation, including optional sequence and colour    |
//+------------------------------------------------------------------+
void CheckCloseBreakout()
  {
   if(g_tradeTaken)
      return;
   const int needed = InpConfirmationBars + 1;
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyRates(_Symbol, InpTimeframe, 1, needed, rates) != needed)
      return;

   bool longOk = DirectionAllowed(1) && rates[InpConfirmationBars].close <= g_orbHigh;
   bool shortOk = DirectionAllowed(-1) && rates[InpConfirmationBars].close >= g_orbLow;
   for(int i = 0; i < InpConfirmationBars; i++)
     {
      longOk = longOk && rates[i].close > g_orbHigh;
      shortOk = shortOk && rates[i].close < g_orbLow;
      if(InpConfirmationColor)
        {
         longOk = longOk && rates[i].close > rates[i].open;
         shortOk = shortOk && rates[i].close < rates[i].open;
        }
     }
   if(longOk)
      HandleSignal(1, rates[0].close);
   else if(shortOk)
      HandleSignal(-1, rates[0].close);
  }

//+------------------------------------------------------------------+
//| Tick-based entry: trade the moment price leaves the range        |
//+------------------------------------------------------------------+
void CheckImmediateBreakout()
  {
   if(g_tradeTaken)
      return;
   if(!IsBeforeCutoff(TimeCurrent()))
      return;

   const double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   const double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   if(!g_tradeTaken && DirectionAllowed(1) && ask > g_orbHigh)
      HandleSignal(1, ask);
   else if(!g_tradeTaken && DirectionAllowed(-1) && bid < g_orbLow)
      HandleSignal(-1, bid);
  }

//+------------------------------------------------------------------+
//| A breakout signal fired: consume the day's attempt, filter, trade |
//+------------------------------------------------------------------+
void HandleSignal(const int direction, const double refPrice)
  {
//--- The first confirmed breakout consumes the whole day's attempt. A signal
//    vetoed by the trend filter does not let a later, weaker breakout through.
   g_tradeTaken = true;
   SaveDailyState();

   if(!MQLInfoInteger(MQL_TESTER) && !InpAllowLiveTrading)
     {
      Print("Signal recorded, but live entries are disabled. Set InpAllowLiveTrading=true to trade outside the tester.");
      UpdatePanel();
      return;
     }

   if(!TrendAllows(direction, refPrice))
     {
      Print(direction > 0 ? "Long" : "Short", " breakout at ",
            TimeToString(TimeCurrent(), TIME_DATE|TIME_MINUTES), " skipped by the trend filter");
      UpdatePanel();
      return;
     }

   OpenBreakout(direction);
   UpdatePanel();
  }

//+------------------------------------------------------------------+
//| Work out entry / stop / target from the range and send the order |
//+------------------------------------------------------------------+
void OpenBreakout(const int direction)
  {
   const double range = g_orbHigh - g_orbLow;
   const double spreadPoints = (double)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(InpMaxSpreadPoints > 0.0 && spreadPoints > InpMaxSpreadPoints)
     {
      PrintFormat("Spread %.1f points exceeds the %.1f-point limit - trade skipped", spreadPoints, InpMaxSpreadPoints);
      return;
     }
   const double entry = (direction > 0) ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                                        : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double sl = 0.0;
   double tp = 0.0;

   if(direction > 0)
     {
      sl = InpStopMode == STOP_MIDPOINT ? (g_orbHigh + g_orbLow) / 2.0
                                        : g_orbHigh - range * InpSlRatio;
      tp = entry + (entry - sl) * InpTpRatio;
     }
   else
     {
      sl = InpStopMode == STOP_MIDPOINT ? (g_orbHigh + g_orbLow) / 2.0
                                        : g_orbLow + range * InpSlRatio;
      tp = entry - (sl - entry) * InpTpRatio;
     }

//--- a gap against us at the open can leave the entry on the wrong side of the stop
   if((direction > 0 && sl >= entry) || (direction < 0 && sl <= entry))
     {
      Print("Entry ", DoubleToString(entry, _Digits), " is already beyond the stop ",
            DoubleToString(sl, _Digits), " - trade skipped");
      return;
     }

   ExecuteTrade(direction, entry, sl, tp);
  }

//+------------------------------------------------------------------+
//| Send a market order with the risk-based lot size                 |
//+------------------------------------------------------------------+
bool ExecuteTrade(const int direction, double entry, double sl, double tp)
  {
   const double requestedEntry = entry;
   if(InpMinStopPoints > 0 && MathAbs(entry - sl) < InpMinStopPoints * _Point)
     {
      PrintFormat("Initial stop %.1f points is below the configured %d-point minimum - trade skipped",
                  MathAbs(entry - sl) / _Point, InpMinStopPoints);
      return(false);
     }
   const double lots = CalcLots(entry, sl);
   if(lots <= 0.0)
     {
      Print("Lot calculation failed - trade skipped");
      return(false);
     }

   entry = NormalizeDouble(entry, _Digits);
   sl    = NormalizeDouble(sl, _Digits);
   tp    = NormalizeDouble(tp, _Digits);

   const double minDist = StopsDistance();
   if(MathAbs(entry - sl) < minDist || MathAbs(tp - entry) < minDist)
     {
      Print("Stop or target is closer than the broker's minimum distance (",
            DoubleToString(minDist, _Digits), ") - trade skipped");
      return(false);
     }

   double margin = 0.0;
   const ENUM_ORDER_TYPE orderType = direction > 0 ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   if(!OrderCalcMargin(orderType, _Symbol, lots, entry, margin))
     {
      Print("Margin calculation failed (error ", GetLastError(), ") - trade skipped");
      return(false);
     }
   if(margin > AccountInfoDouble(ACCOUNT_MARGIN_FREE))
     {
      PrintFormat("Required margin %.2f exceeds free margin %.2f - trade skipped",
                  margin, AccountInfoDouble(ACCOUNT_MARGIN_FREE));
      return(false);
     }

   const bool sent = (direction > 0) ? trade.Buy(lots, _Symbol, entry, sl, tp, InpComment)
                                     : trade.Sell(lots, _Symbol, entry, sl, tp, InpComment);
   const uint rc = trade.ResultRetcode();
   if(!sent || !RetcodeOk(rc))
     {
      PrintFormat("Order failed: %u %s", rc, trade.ResultRetcodeDescription());
      return(false);
     }

   PrintFormat("%s %s lots @ %s  SL %s  TP %s  (risk %s, target %.2fR)",
               direction > 0 ? "BUY" : "SELL",
               DoubleToString(lots, 2),
               DoubleToString(entry, _Digits),
               DoubleToString(sl, _Digits),
               DoubleToString(tp, _Digits),
               DoubleToString(MathAbs(entry - sl), _Digits),
               InpTpRatio);

//--- remember the trade for the log
   TradeRecord rec;
   ZeroMemory(rec);
   rec.order      = trade.ResultOrder();
   rec.position   = 0;
   rec.direction  = direction;
   rec.signalTime = TimeCurrent();
   rec.orbTime    = g_orbTime;
   rec.orbHigh    = g_orbHigh;
   rec.orbLow     = g_orbLow;
   rec.rangeAtr   = g_orbRangeAtr;
   rec.volRatio   = g_orbVolRatio;
   rec.requestedEntry = requestedEntry;
   rec.entry      = entry;
   rec.entryTime  = TimeCurrent();
   rec.sl         = sl;
   rec.tp         = tp;
   rec.lots       = lots;
   rec.commission = 0.0;
   rec.filled     = false;

//--- market executions usually report the deal straight away
   const ulong deal = trade.ResultDeal();
   if(deal > 0 && HistoryDealSelect(deal))
     {
      rec.position   = (ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID);
      rec.entry      = HistoryDealGetDouble(deal, DEAL_PRICE);
      rec.entryTime  = (datetime)HistoryDealGetInteger(deal, DEAL_TIME);
      rec.lots       = HistoryDealGetDouble(deal, DEAL_VOLUME);
      rec.commission = HistoryDealGetDouble(deal, DEAL_COMMISSION);
      rec.filled     = true;
     }

   PrintFormat("Requested entry %s, actual fill %s, slippage %.1f points",
               DoubleToString(rec.requestedEntry, _Digits), DoubleToString(rec.entry, _Digits),
               (rec.entry - rec.requestedEntry) * direction / _Point);

   const int n = ArraySize(g_open);
   ArrayResize(g_open, n + 1);
   g_open[n] = rec;
   return(true);
  }

//+------------------------------------------------------------------+
//| Opening deal arrived through the transaction queue               |
//+------------------------------------------------------------------+
void OnEntryDeal(const ulong deal)
  {
   const ulong order = (ulong)HistoryDealGetInteger(deal, DEAL_ORDER);
   for(int i = 0; i < ArraySize(g_open); i++)
     {
      if(g_open[i].filled || g_open[i].order != order)
         continue;
      g_open[i].position   = (ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID);
      g_open[i].entry      = HistoryDealGetDouble(deal, DEAL_PRICE);
      g_open[i].entryTime  = (datetime)HistoryDealGetInteger(deal, DEAL_TIME);
      g_open[i].lots       = HistoryDealGetDouble(deal, DEAL_VOLUME);
      g_open[i].commission = HistoryDealGetDouble(deal, DEAL_COMMISSION);
      g_open[i].filled     = true;
      return;
     }
  }

//+------------------------------------------------------------------+
//| Closing deal: write the trade to the log                         |
//+------------------------------------------------------------------+
void OnExitDeal(const ulong deal)
  {
   const ulong position = (ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID);
   int idx = -1;
   for(int i = 0; i < ArraySize(g_open); i++)
      if(g_open[i].filled && g_open[i].position == position)
        {
         idx = i;
         break;
        }
   if(idx < 0)
     {
      PrintFormat("Closing deal #%I64u for position #%I64u has no open record - not logged", deal, position);
      return;
     }

   const double   exitPrice  = HistoryDealGetDouble(deal, DEAL_PRICE);
   const datetime exitTime   = (datetime)HistoryDealGetInteger(deal, DEAL_TIME);
   const double   profit     = HistoryDealGetDouble(deal, DEAL_PROFIT);
   const double   commission = HistoryDealGetDouble(deal, DEAL_COMMISSION) + g_open[idx].commission;
   const double   swap       = HistoryDealGetDouble(deal, DEAL_SWAP);
   const long     reason     = HistoryDealGetInteger(deal, DEAL_REASON);

   string reasonText = "OTHER";
   if(reason == DEAL_REASON_SL)
      reasonText = "SL";
   else if(reason == DEAL_REASON_TP)
      reasonText = "TP";
   else if(reason == DEAL_REASON_EXPERT)
      reasonText = "EOD";        // the EA only ever closes for end of day
   else if(reason == DEAL_REASON_SO)
      reasonText = "STOPOUT";

   const double riskDist  = MathAbs(g_open[idx].entry - g_open[idx].sl);
   const double rMultiple = (riskDist > 0.0) ? (exitPrice - g_open[idx].entry) * g_open[idx].direction / riskDist : 0.0;

   PrintFormat("%s closed by %s @ %s  %.2fR  net %.2f",
               g_open[idx].direction > 0 ? "Long" : "Short", reasonText,
               DoubleToString(exitPrice, _Digits), rMultiple, profit + commission + swap);

   if(g_logHandle != INVALID_HANDLE)
     {
      const string line = StringFormat("%s,%s,%s,%s,%s,%s,%.3f,%.2f,%s,%s,%s,%s,%.2f,%s,%s,%.2f,%s,%s,%s,%.3f,%.2f,%.2f,%.2f,%.2f\n",
                                       TimeToString(g_open[idx].orbTime, TIME_DATE),
                                       g_open[idx].direction > 0 ? "long" : "short",
                                       TimeToString(g_open[idx].orbTime, TIME_DATE|TIME_MINUTES),
                                       DoubleToString(g_open[idx].orbHigh, _Digits),
                                       DoubleToString(g_open[idx].orbLow, _Digits),
                                       DoubleToString(g_open[idx].orbHigh - g_open[idx].orbLow, _Digits),
                                       g_open[idx].rangeAtr,
                                        g_open[idx].volRatio,
                                        TimeToString(g_open[idx].signalTime, TIME_DATE|TIME_MINUTES|TIME_SECONDS),
                                        TimeToString(g_open[idx].entryTime, TIME_DATE|TIME_MINUTES|TIME_SECONDS),
                                        DoubleToString(g_open[idx].requestedEntry, _Digits),
                                        DoubleToString(g_open[idx].entry, _Digits),
                                        (g_open[idx].entry - g_open[idx].requestedEntry) * g_open[idx].direction / _Point,
                                        DoubleToString(g_open[idx].sl, _Digits),
                                       DoubleToString(g_open[idx].tp, _Digits),
                                       g_open[idx].lots,
                                       TimeToString(exitTime, TIME_DATE|TIME_MINUTES|TIME_SECONDS),
                                       DoubleToString(exitPrice, _Digits),
                                       reasonText,
                                       rMultiple,
                                       profit,
                                       commission,
                                       swap,
                                       AccountInfoDouble(ACCOUNT_BALANCE));
      FileWriteString(g_logHandle, line);
      FileFlush(g_logHandle);
     }
   g_closedCount++;

//--- drop the record unless part of the position is still open
   if(!PositionSelectByTicket(position))
      RemoveRecord(idx);

   UpdatePanel();
  }

//+------------------------------------------------------------------+
//| Close every position of this EA once the session close is reached |
//+------------------------------------------------------------------+
void CloseAtEndOfDay()
  {
   if(!InpCloseEod || g_eodDone)
      return;
   if(!TimeReached(TimeCurrent(), (int)InpCloseHour, InpCloseMinute))
      return;

   bool remaining = false;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      if(PositionGetInteger(POSITION_MAGIC) != (long)InpMagic)
         continue;

      if(!trade.PositionClose(ticket))
        {
         PrintFormat("End-of-day close of #%I64u failed: %u %s", ticket, trade.ResultRetcode(), trade.ResultRetcodeDescription());
         remaining = true;   // try again on the next tick
        }
     }
   if(!remaining)
      g_eodDone = true;
  }

//+------------------------------------------------------------------+
//| Lot size so that (entry - stop) loses InpRiskPercent of equity   |
//+------------------------------------------------------------------+
double CalcLots(const double entry, const double sl)
  {
   const double riskMoney = AccountInfoDouble(ACCOUNT_EQUITY) * InpRiskPercent / 100.0;
   const double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);   // account currency per tick per lot
   const double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(riskMoney <= 0.0 || tickValue <= 0.0 || tickSize <= 0.0)
      return(0.0);

   const double stopTicks = MathAbs(entry - sl) / tickSize;
   if(stopTicks <= 0.0)
      return(0.0);

   double lots = riskMoney / (stopTicks * tickValue);

//--- fit the broker's volume rules
   const double step   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   const double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   const double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

   if(step > 0.0)
      lots = MathFloor(lots / step) * step;   // round DOWN so risk never exceeds the target

   if(lots < minLot)
     {
      PrintFormat("Calculated %s lots is below the minimum %s - using the minimum (risk exceeds %.2f%%)",
                  DoubleToString(lots, 2), DoubleToString(minLot, 2), InpRiskPercent);
      lots = minLot;
     }
   if(lots > maxLot)
      lots = maxLot;

   return(NormalizeDouble(lots, VolumeDigits(step)));
  }

//+------------------------------------------------------------------+
//| Breakeven and trailing stop for this EA's open positions         |
//+------------------------------------------------------------------+
void ManageOpenPositions()
  {
   if(InpBreakEvenAtR <= 0.0 && InpTrailStartR <= 0.0)
      return;

   const double bid     = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   const double ask     = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   const double minDist = StopsDistance();

   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = PositionGetTicket(i);   // also selects the position
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      if(PositionGetInteger(POSITION_MAGIC) != (long)InpMagic)
         continue;

      const bool   isLong    = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
      const double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      const double sl        = PositionGetDouble(POSITION_SL);
      const double tp        = PositionGetDouble(POSITION_TP);
      const double risk      = InitialRisk(openPrice, tp);
      if(risk <= 0.0)
         continue;

      const double profitR = (isLong ? bid - openPrice : openPrice - ask) / risk;
      double newSl = sl;

      //--- breakeven: applies as soon as the trigger is reached
      if(InpBreakEvenAtR > 0.0 && profitR >= InpBreakEvenAtR)
        {
         const double beSl = isLong ? openPrice + InpBreakEvenLockR * risk
                                    : openPrice - InpBreakEvenLockR * risk;
         if(Improves(isLong, beSl, newSl))
            newSl = beSl;
        }

      //--- trailing: only modify when the stop would move by at least the step
      if(InpTrailStartR > 0.0 && profitR >= InpTrailStartR)
        {
         const double trailSl = isLong ? bid - InpTrailDistR * risk
                                       : ask + InpTrailDistR * risk;
         if(Improves(isLong, trailSl, newSl) && MathAbs(trailSl - sl) >= InpTrailStepR * risk)
            newSl = trailSl;
        }

      if(!Improves(isLong, newSl, sl))
         continue;

      //--- respect the broker's minimum stop distance from the current price
      if(isLong && newSl > bid - minDist)
         newSl = bid - minDist;
      if(!isLong && newSl < ask + minDist)
         newSl = ask + minDist;
      if(!Improves(isLong, newSl, sl))
         continue;

      newSl = NormalizeDouble(newSl, _Digits);
      if(trade.PositionModify(ticket, newSl, tp))
         PrintFormat("#%I64u stop moved to %s (+%.2fR)", ticket, DoubleToString(newSl, _Digits), profitR);
      else
         PrintFormat("#%I64u modify failed: %u %s", ticket, trade.ResultRetcode(), trade.ResultRetcodeDescription());
     }
  }

//+------------------------------------------------------------------+
//| Opening-range capture                                            |
//+------------------------------------------------------------------+

//--- record the range if this closed bar is the opening-range candle
void TryCaptureOrb(const MqlRates &bar)
  {
   MqlDateTime dt;
   TimeToStruct(bar.time, dt);
   if(dt.hour != (int)InpOrbHour || dt.min != InpOrbMinute)
      return;

   SetOrb(bar.time, bar.high, bar.low, bar.tick_volume, true, "Observed opening range");
  }

//--- build a forecast range at today's opening time from earlier sessions
void TryForecastOrb(const datetime currentBar)
  {
   MqlDateTime dt;
   TimeToStruct(currentBar, dt);
   if(dt.hour != (int)InpOrbHour || dt.min != InpOrbMinute)
      return;

   double widths[];
   datetime sessions[];
   if(!HistoricalRanges(currentBar, widths, sessions))
     {
      g_dayStatus = "skipped: insufficient session history";
      Print(g_dayStatus);
      return;
     }
   const double base = Percentile(widths, InpRangePercentile);
   double adjustment = 1.0;
   if(InpRangeMode == RANGE_HISTORICAL_PREMARKET)
     {
      const double todaySigma = RealizedSigma(currentBar);
      double sigmas[];
      ArrayResize(sigmas, ArraySize(sessions));
      for(int i = 0; i < ArraySize(sessions); i++)
         sigmas[i] = RealizedSigma(sessions[i]);
      const double reference = Percentile(sigmas, 50.0);
      if(todaySigma <= 0.0 || reference <= 0.0)
        {
         g_dayStatus = "skipped: insufficient premarket history";
         Print(g_dayStatus);
         return;
        }
      adjustment = MathPow(todaySigma / reference, InpPremarketSensitivity);
     }
   const int anchorShift = iBarShift(_Symbol, InpTimeframe, currentBar, true);
   const double anchor = anchorShift >= 0 ? iOpen(_Symbol, InpTimeframe, anchorShift) : 0.0;
   if(anchor <= 0.0)
     {
      g_dayStatus = "skipped: opening price unavailable";
      return;
     }
   const double width = anchor * base * InpRangeMultiplier * adjustment;
   const string label = InpRangeMode == RANGE_HISTORICAL_PREMARKET
                        ? StringFormat("Historical P%.1f x %.2f x premarket %.2f", InpRangePercentile, InpRangeMultiplier, adjustment)
                        : StringFormat("Historical P%.1f x %.2f", InpRangePercentile, InpRangeMultiplier);
   SetOrb(currentBar, anchor + width / 2.0, anchor - width / 2.0, 0, false, label);
  }

//--- collect the last N matching observed opening candles, normalized by open
bool HistoricalRanges(const datetime before, double &widths[], datetime &sessions[])
  {
   const int calendarDays = MathMax(60, InpRangeSessions * 4);
   MqlRates history[];
   const int copied = CopyRates(_Symbol, InpTimeframe, before - calendarDays * 86400, before - 1, history);
   if(copied <= 0)
      return(false);
   double allWidths[];
   datetime allSessions[];
   for(int i = 0; i < copied; i++)
     {
      MqlDateTime dt;
      TimeToStruct(history[i].time, dt);
      if(dt.hour != (int)InpOrbHour || dt.min != InpOrbMinute || history[i].open <= 0.0)
         continue;
      const int n = ArraySize(allWidths);
      ArrayResize(allWidths, n + 1);
      ArrayResize(allSessions, n + 1);
      allWidths[n] = (history[i].high - history[i].low) / history[i].open;
      allSessions[n] = history[i].time;
     }
   const int available = ArraySize(allWidths);
   if(available < InpRangeSessions)
      return(false);
   ArrayResize(widths, InpRangeSessions);
   ArrayResize(sessions, InpRangeSessions);
   for(int i = 0; i < InpRangeSessions; i++)
     {
      widths[i] = allWidths[available - InpRangeSessions + i];
      sessions[i] = allSessions[available - InpRangeSessions + i];
     }
   return(true);
  }

//--- linearly interpolated percentile, matching numpy's default convention
double Percentile(const double &values[], const double percentile)
  {
   const int n = ArraySize(values);
   if(n <= 0)
      return(0.0);
   double sorted[];
   ArrayCopy(sorted, values);
   ArraySort(sorted);
   for(int i = 0; i < n; i++)
      if(!MathIsValidNumber(sorted[i]) || sorted[i] <= 0.0)
         return(0.0);
   const double position = (n - 1) * percentile / 100.0;
   const int lower = (int)MathFloor(position);
   const int upper = (int)MathCeil(position);
   const double fraction = position - lower;
   return(sorted[lower] + (sorted[upper] - sorted[lower]) * fraction);
  }

//--- realized log-return volatility in M1 bars ending immediately before end
double RealizedSigma(const datetime end)
  {
   MqlRates minute[];
   const datetime start = end - InpPremarketMinutes * 60;
   const int copied = CopyRates(_Symbol, PERIOD_M1, start, end - 1, minute);
   if(copied < InpPremarketMinutes || minute[0].open <= 0.0)
      return(0.0);
   double previous = minute[0].open;
   double sum = 0.0;
   for(int i = 0; i < copied; i++)
     {
      if(minute[i].close <= 0.0 || previous <= 0.0)
         return(0.0);
      const double value = MathLog(minute[i].close / previous);
      sum += value * value;
      previous = minute[i].close;
     }
   return(MathSqrt(sum));
  }

//--- apply common filters and accept an observed or forecast opening range
bool SetOrb(const datetime time, const double high, const double low, const long volume,
            const bool checkVolume, const string label)
  {
   const string when = TimeToString(time, TIME_DATE|TIME_MINUTES);
   const double range = high - low;
   if(range <= 0.0)
     {
      Print("Opening range at ", when, " has zero width - no trades today");
      g_dayStatus = "skipped: zero range";
      return(false);
     }

   if(!RangeLookbackAllows(time, range))
      return(false);

   g_orbRangeAtr = RangeToAtr(range);
   g_orbVolRatio = checkVolume ? VolumeRatio(volume, time) : 0.0;

   if(checkVolume && InpVolumeSpikeMin > 0.0 && g_orbVolRatio > 0.0 && g_orbVolRatio < InpVolumeSpikeMin)
     {
      PrintFormat("WARNING %s: range-candle volume is only %.2fx the previous %d candles", when, g_orbVolRatio, InpVolumeLookback);
      if(InpSkipNoSpike)
        {
         g_dayStatus = StringFormat("skipped: no volume spike (x%.1f)", g_orbVolRatio);
         return(false);
        }
     }

   if(g_orbRangeAtr > 0.0)
     {
      if(InpMinRangeAtr > 0.0 && g_orbRangeAtr < InpMinRangeAtr)
        {
         g_dayStatus = StringFormat("skipped: range %.2f ATR is too small", g_orbRangeAtr);
         return(false);
        }
      if(InpMaxRangeAtr > 0.0 && g_orbRangeAtr > InpMaxRangeAtr)
        {
         g_dayStatus = StringFormat("skipped: range %.2f ATR is too large", g_orbRangeAtr);
         return(false);
        }
     }

   g_orbSet = true;
   g_orbHigh = high;
   g_orbLow = low;
   g_orbTime = time;
   g_dayStatus = "";
   PrintFormat("ORB set %s  %s  high %s  low %s  range %s", when, label,
               DoubleToString(high, _Digits), DoubleToString(low, _Digits), DoubleToString(range, _Digits));
   DrawOrbBox(time, high, low);
   return(true);
  }

//--- reject an opening range that is too large relative to the completed prior window
bool RangeLookbackAllows(const datetime end, const double range)
  {
   if(InpMaxRangeLookbackRatio <= 0.0)
      return(true);
   MqlRates minute[];
   const datetime start = end - InpRangeLookbackMinutes * 60;
   const int copied = CopyRates(_Symbol, PERIOD_M1, start, end - 1, minute);
   if(copied < InpRangeLookbackMinutes)
     {
      g_dayStatus = "skipped: incomplete prior range window";
      return(false);
     }
   double high = minute[0].high;
   double low = minute[0].low;
   for(int i = 1; i < copied; i++)
     {
      high = MathMax(high, minute[i].high);
      low = MathMin(low, minute[i].low);
     }
   const double span = high - low;
   if(span <= 0.0 || range / span > InpMaxRangeLookbackRatio)
     {
      g_dayStatus = "skipped: range too large vs prior window";
      return(false);
     }
   return(true);
  }

//--- opening range divided by yesterday's daily ATR (0 when unavailable)
double RangeToAtr(const double range)
  {
   if(g_atrHandle == INVALID_HANDLE)
      return(0.0);
   double atr[];
   if(CopyBuffer(g_atrHandle, 0, 1, 1, atr) != 1 || atr[0] <= 0.0)
      return(0.0);
   return(range / atr[0]);
  }

//--- range-candle tick volume relative to the average of the candles before it
//    (the range candle is at shift 1 when this runs, so its predecessors start at 2)
double VolumeRatio(const long orbVolume, const datetime orbTime)
  {
   if(InpVolumeLookback <= 0)
      return(0.0);
   const int orbShift = iBarShift(_Symbol, InpTimeframe, orbTime, true);
   if(orbShift < 0)
      return(0.0);
   long vol[];
   if(CopyTickVolume(_Symbol, InpTimeframe, orbShift + 1, InpVolumeLookback, vol) != InpVolumeLookback)
      return(0.0);
   double sum = 0.0;
   for(int i = 0; i < InpVolumeLookback; i++)
      sum += (double)vol[i];
   if(sum <= 0.0)
      return(0.0);
   return((double)orbVolume / (sum / InpVolumeLookback));
  }

//+------------------------------------------------------------------+
//| Helpers                                                          |
//+------------------------------------------------------------------+

//--- persist the one-attempt-per-day lock across live terminal restarts
string DailyStateKey()
  {
   return(StringFormat("ORB.%I64d.%I64u.%s.trade_day",
                       AccountInfoInteger(ACCOUNT_LOGIN), InpMagic, _Symbol));
  }

void SaveDailyState()
  {
   if(MQLInfoInteger(MQL_TESTER))
      return;
   GlobalVariableSet(DailyStateKey(), g_tradeTaken ? (double)g_currentDay : 0.0);
  }

void RestoreDailyState()
  {
   if(MQLInfoInteger(MQL_TESTER) || !GlobalVariableCheck(DailyStateKey()))
      return;
   g_tradeTaken = ((datetime)GlobalVariableGet(DailyStateKey()) == g_currentDay);
   if(g_tradeTaken)
      Print("Restored today's consumed breakout lock after restart");
  }

//--- reconstruct today's range when the EA is attached after the opening time
void RecoverTodayRange()
  {
   const datetime now = TimeCurrent();
   const datetime target = g_currentDay + (int)InpOrbHour * 3600 + InpOrbMinute * 60;
   if(now < target)
      return;
   if(InpRangeMode != RANGE_OBSERVED)
     {
      TryForecastOrb(target);
      return;
     }
   const int seconds = PeriodSeconds(InpTimeframe);
   if(seconds <= 0 || now < target + seconds)
      return;
   MqlRates rangeBar[];
   if(CopyRates(_Symbol, InpTimeframe, target, target + seconds - 1, rangeBar) == 1)
      SetOrb(rangeBar[0].time, rangeBar[0].high, rangeBar[0].low,
             rangeBar[0].tick_volume, true, "Recovered observed opening range");
  }

//--- true once per new bar on the signal timeframe
bool IsNewBar()
  {
   const datetime t = iTime(_Symbol, InpTimeframe, 0);
   if(t == 0 || t == g_lastBarTime)
      return(false);
   g_lastBarTime = t;
   return(true);
  }

//--- clear yesterday's range and breakout flags when the server-time date changes
void ResetOnNewDay(const datetime barTime)
  {
   const datetime day = DayStart(barTime);
   if(day == g_currentDay)
      return;

   const bool firstCall = (g_currentDay == 0);
   g_currentDay  = day;
   g_orbSet      = false;
   g_orbHigh     = 0.0;
   g_orbLow      = 0.0;
   g_orbTime     = 0;
   g_orbRangeAtr = 0.0;
   g_orbVolRatio = 0.0;
   g_tradeTaken  = false;
   g_eodDone     = false;
   g_dayStatus   = "";
   SaveDailyState();

   if(!firstCall)
      Print("New day ", TimeToString(day, TIME_DATE), " - opening range reset");
  }

//--- shaded rectangle from the range candle to the cutoff hour (the trading window)
void DrawOrbBox(const datetime t, const double high, const double low)
  {
   if(!InpDrawBox)
      return;

   const string   name = ORB_BOX_PREFIX + TimeToString(t, TIME_DATE);
   const datetime end  = DayStart(t) + (int)InpCutoffHour * 3600;

   if(ObjectFind(0, name) >= 0)
      ObjectDelete(0, name);
   if(!ObjectCreate(0, name, OBJ_RECTANGLE, 0, t, high, end, low))
      return;

   ObjectSetInteger(0, name, OBJPROP_COLOR, clrYellow);
   ObjectSetInteger(0, name, OBJPROP_FILL, true);
   ObjectSetInteger(0, name, OBJPROP_BACK, true);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
  }

//--- status panel in the top-left corner of the chart
void UpdatePanel()
  {
   if(!InpShowPanel || MQLInfoInteger(MQL_OPTIMIZATION))
      return;

   string s = StringFormat("ORB EA v%s  |  %s %s\n", ORB_VERSION, _Symbol, EnumToString(InpTimeframe));
   s += StringFormat("Range candle %02d:%02d  |  entries until %02d:00  |  end-of-day close %s\n",
                     (int)InpOrbHour, InpOrbMinute, (int)InpCutoffHour,
                     InpCloseEod ? StringFormat("%02d:%02d", (int)InpCloseHour, InpCloseMinute) : "off");
   s += StringFormat("Range: %s  |  Entry: %s (%d close%s)  |  %s\n",
                     RangeModeText(), EntryModeText(), InpConfirmationBars,
                     InpConfirmationColor ? ", colour required" : "", DirectionText());
   s += StringFormat("TP %.2fR  |  stop %s  |  risk %.1f%%  |  spread now %.1f points\n",
                     InpTpRatio, StopModeText(), InpRiskPercent,
                     (double)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD));
   if(g_orbSet)
      s += StringFormat("Today: range %s - %s (%s, %.2f x ATR, volume x%.1f)  |  trade: %s\n",
                        DoubleToString(g_orbLow, _Digits), DoubleToString(g_orbHigh, _Digits),
                        DoubleToString(g_orbHigh - g_orbLow, _Digits), g_orbRangeAtr, g_orbVolRatio,
                        g_tradeTaken ? "taken" : "waiting");
   else
      s += "Today: " + (g_dayStatus == "" ? "waiting for the range candle" : g_dayStatus) + "\n";
   s += StringFormat("Open positions: %d  |  trades logged this run: %d", CountOurPositions(), g_closedCount);

   Comment(s);
  }

//--- open the CSV log in the shared Common\Files folder and write the header
void OpenLog()
  {
   if(StringLen(InpLogFile) == 0 || MQLInfoInteger(MQL_OPTIMIZATION))
      return;

   g_logHandle = FileOpen(InpLogFile, FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ);
   if(g_logHandle == INVALID_HANDLE)
     {
      Print("Could not open the trade log ", InpLogFile, " (error ", GetLastError(), ") - logging off");
      return;
     }

   const string settings = StringFormat("# ORB EA v%s | symbol=%s tf=%s orb=%02d:%02d cutoff=%02d entry=%s direction=%s tp=%.10g sl=%.10g risk=%.10g eod=%s range_atr=%.10g-%.10g ma=%d/%s/%s be=%.10g/%.10g trail=%.10g/%.10g/%.10g\n",
                                        ORB_VERSION, _Symbol, EnumToString(InpTimeframe),
                                        (int)InpOrbHour, InpOrbMinute, (int)InpCutoffHour,
                                        InpEntryMode == ENTRY_IMMEDIATE ? "immediate" : "close",
                                        InpDirection == DIR_BOTH ? "both" : (InpDirection == DIR_LONG_ONLY ? "long" : "short"),
                                        InpTpRatio, InpSlRatio, InpRiskPercent,
                                        InpCloseEod ? StringFormat("%02d:%02d", (int)InpCloseHour, InpCloseMinute) : "off",
                                        InpMinRangeAtr, InpMaxRangeAtr,
                                        InpFilterMaPeriod, EnumToString(InpFilterTF), EnumToString(InpFilterMaMethod),
                                        InpBreakEvenAtR, InpBreakEvenLockR,
                                        InpTrailStartR, InpTrailDistR, InpTrailStepR);
   const double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   const double pointValue = tickSize > 0.0 ? SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE) / tickSize : 0.0;
   string completeSettings = settings;
   StringReplace(completeSettings, "\n", "");
   completeSettings += StringFormat(" atr_period=%d vol_lookback=%d vol_spike_min=%.10g point=%.10g point_value=%.10g lot_step=%.10g min_lot=%.10g max_lot=%.10g initial_balance=%.10g\n",
                                    InpAtrPeriod, InpVolumeLookback, InpSkipNoSpike ? InpVolumeSpikeMin : 0.0,
                                    _Point, pointValue, SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP),
                                    SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN), SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX),
                                     AccountInfoDouble(ACCOUNT_BALANCE));
   StringReplace(completeSettings, "\n", "");
   completeSettings += StringFormat(" range_mode=%s range_sessions=%d range_percentile=%.10g range_multiplier=%.10g premarket_minutes=%d premarket_sensitivity=%.10g confirmation_bars=%d confirmation_color=%s stop_mode=%s range_lookback_minutes=%d max_range_lookback_ratio=%.10g ma_slope_bars=%d ma_min_slope_pct=%.10g max_spread_points=%.10g min_stop_points=%d\n",
                                    RangeModeKey(), InpRangeSessions, InpRangePercentile, InpRangeMultiplier,
                                    InpPremarketMinutes, InpPremarketSensitivity, InpConfirmationBars,
                                    InpConfirmationColor ? "true" : "false", StopModeKey(),
                                    InpRangeLookbackMinutes, InpMaxRangeLookbackRatio,
                                    InpFilterSlopeBars, InpFilterMinSlopePct,
                                    InpMaxSpreadPoints, InpMinStopPoints);
   FileWriteString(g_logHandle, completeSettings);
   FileWriteString(g_logHandle, "date,direction,orb_time,orb_high,orb_low,orb_range,range_atr,vol_ratio,signal_time,entry_time,requested_entry,entry,slippage_points,sl,tp,lots,exit_time,exit,reason,r_multiple,profit,commission,swap,balance\n");
   FileFlush(g_logHandle);

   Print("Logging trades to ", TerminalInfoString(TERMINAL_COMMONDATA_PATH), "\\Files\\", InpLogFile);
  }

//--- drop one record from the open-trade array
void RemoveRecord(const int idx)
  {
   const int n = ArraySize(g_open);
   for(int j = idx; j < n - 1; j++)
      g_open[j] = g_open[j + 1];
   ArrayResize(g_open, n - 1);
  }

//--- positions on this symbol carrying our magic number
int CountOurPositions()
  {
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == (long)InpMagic)
         count++;
     }
   return(count);
  }

//--- entries are allowed while the hour is strictly before the cutoff hour
bool IsBeforeCutoff(const datetime t)
  {
   MqlDateTime dt;
   TimeToStruct(t, dt);
   return(dt.hour < (int)InpCutoffHour);
  }

//--- has the clock reached hour:minute today?
bool TimeReached(const datetime t, const int hour, const int minute)
  {
   MqlDateTime dt;
   TimeToStruct(t, dt);
   return(dt.hour > hour || (dt.hour == hour && dt.min >= minute));
  }

//--- does the direction input permit this trade?
bool DirectionAllowed(const int direction)
  {
   if(InpDirection == DIR_BOTH)
      return(true);
   return(direction > 0 ? InpDirection == DIR_LONG_ONLY : InpDirection == DIR_SHORT_ONLY);
  }

//--- higher-timeframe filter: longs only above the MA, shorts only below it
bool TrendAllows(const int direction, const double price)
  {
   if(g_maHandle == INVALID_HANDLE)
      return(true);

   double ma[];
   if(CopyBuffer(g_maHandle, 0, 0, 1, ma) != 1)
     {
      Print("Trend filter: MA value not available - signal skipped");
      return(false);
     }
   const bool priceSide = direction > 0 ? price > ma[0] : price < ma[0];
   if(!priceSide || InpFilterMinSlopePct <= 0.0)
      return(priceSide);

//--- use only MA values that were complete before the breakout's HTF bar
   double latest[];
   double previous[];
   if(CopyBuffer(g_maHandle, 0, 2, 1, latest) != 1 ||
      CopyBuffer(g_maHandle, 0, InpFilterSlopeBars + 2, 1, previous) != 1 ||
      previous[0] == 0.0)
     {
      Print("Trend filter: completed MA slope is not available - signal skipped");
      return(false);
     }
   const double slopePct = (latest[0] - previous[0]) / MathAbs(previous[0]) * 100.0;
   return(direction > 0 ? slopePct >= InpFilterMinSlopePct
                        : slopePct <= -InpFilterMinSlopePct);
  }

//--- the initial risk distance of a position.  TP is never modified, so it still
//    encodes the original stop: |TP - open| = risk x TP ratio.
double InitialRisk(const double openPrice, const double tp)
  {
   if(tp <= 0.0 || InpTpRatio <= 0.0)
      return(0.0);
   return(MathAbs(tp - openPrice) / InpTpRatio);
  }

//--- would this stop be an improvement (never moves a stop backwards)?
bool Improves(const bool isLong, const double candidate, const double current)
  {
   if(current <= 0.0)
      return(true);
   return(isLong ? candidate > current + _Point * 0.5
                 : candidate < current - _Point * 0.5);
  }

//--- broker's minimum distance between price and a stop/target, in price units
double StopsDistance()
  {
   return((double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point);
  }

//--- server-side result codes that mean the request went through
bool RetcodeOk(const uint rc)
  {
   return(rc == TRADE_RETCODE_DONE || rc == TRADE_RETCODE_PLACED || rc == TRADE_RETCODE_DONE_PARTIAL);
  }

//--- 00:00 server time of the day containing t
datetime DayStart(const datetime t)
  {
   const long secs = (long)t;
   return((datetime)(secs - secs % 86400));
  }

//--- number of decimals in the volume step (0.01 -> 2, 0.1 -> 1, 1 -> 0)
int VolumeDigits(double step)
  {
   if(step <= 0.0)
      return(2);   // broker reported no step - fall back to the usual 0.01 lots
   int digits = 0;
   while(step > 0.0 && step < 0.999999 && digits < 8)
     {
      step   *= 10.0;
      digits++;
     }
   return(digits);
  }

//--- readable input values for the panel and journal
string EntryModeText()
  {
   return(InpEntryMode == ENTRY_IMMEDIATE ? "immediate break" : "close confirmation");
  }

string RangeModeKey()
  {
   if(InpRangeMode == RANGE_HISTORICAL_PERCENTILE)
      return("historical_percentile");
   if(InpRangeMode == RANGE_HISTORICAL_PREMARKET)
      return("historical_percentile_premarket");
   return("signal_bar");
  }

string RangeModeText()
  {
   if(InpRangeMode == RANGE_HISTORICAL_PERCENTILE)
      return(StringFormat("historical P%.1f x %.2f", InpRangePercentile, InpRangeMultiplier));
   if(InpRangeMode == RANGE_HISTORICAL_PREMARKET)
      return(StringFormat("historical P%.1f x %.2f + premarket", InpRangePercentile, InpRangeMultiplier));
   return("observed candle");
  }

string StopModeKey()
  {
   return(InpStopMode == STOP_MIDPOINT ? "midpoint" : "range_multiple");
  }

string StopModeText()
  {
   return(InpStopMode == STOP_MIDPOINT ? "range midpoint"
                                       : StringFormat("%.2f x range", InpSlRatio));
  }

string DirectionText()
  {
   if(InpDirection == DIR_LONG_ONLY)
      return("long only");
   if(InpDirection == DIR_SHORT_ONLY)
      return("short only");
   return("long + short");
  }

//--- print a message and reject the inputs
int Fail(const string msg)
  {
   Print("ORB EA: ", msg);
   return(INIT_PARAMETERS_INCORRECT);
  }
//+------------------------------------------------------------------+
