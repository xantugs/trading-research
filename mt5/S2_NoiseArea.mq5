//+------------------------------------------------------------------+
//|                                                 S2_NoiseArea.mq5 |
//|      Noise-area intraday momentum (Zarattini, Aziz & Barbon 2024) |
//+------------------------------------------------------------------+
//
//  v1.10 - every order carries a visible stop-loss InpStopPct from the entry (The5ers requires
//          one): 1.25% of price, beyond 99.5% of the 2010-2017 adverse moves, hit by 15 of
//          1,171 trades in 2018-2024 (+5.63 against +5.87 bps a day without it).
//  v1.00 - the S2 strategy of docs/INTRADAY_MOMENTUM_PLAN.md, as the Python engine
//  (orb_research/intraday_momentum.py) trades it:
//   - session 09:30-16:00 New York = 16:30-23:00 on FTMO's server (New York + 7 h);
//   - at every checkpoint (17:00, 17:30 ... 22:30 server) the decision price is the close
//     of the minute bar that ends at the checkpoint;
//   - noise area: sigma(t) = average |price(t) / open - 1| over the previous 14 sessions
//     (at least 10 with the price); upper = max(open, previous close) x (1 + sigma),
//     lower = min(open, previous close) x (1 - sigma);
//   - a long below or at the upper band, or a short above or at the lower band, is closed;
//     then, if flat, go long above the upper band and short below the lower band;
//   - everything is closed at the session close (from 22:59 server, the last minute);
//   - size = leverage x equity in notional.
//  Orders go at market on the first tick after the checkpoint, so fills sit about one
//  tick after the backtest's checkpoint price; the backtest showed the edge survives
//  entries one minute late.
//
//  Account protection (FTMO):
//   - daily stop: once equity is InpDailyStopPct below the balance at the start of the
//     server day, close every position and stop trading until the next day;
//   - hard stop: once equity is InpHardStopPct below the starting balance - or below the
//     highest day-start balance when InpTrailingHardStop is on (1-Step accounts) - close
//     every position and halt until the global variable S2_<login>_<symbol>_halted is
//     deleted (F3);
//   - news filter (FTMO funded Standard accounts): no order within 2 minutes of a
//     high-impact US calendar event; the checkpoint is skipped and the next one decides.
//
//+------------------------------------------------------------------+
#property copyright   "orb-backtest"
#property version     "1.10"
#property description "Noise-area intraday momentum (S2). All times are BROKER SERVER time."

#include <Trade/Trade.mqh>

#define S2_VERSION "1.10"
#define MAX_CHECKPOINTS 64

input group "Session (broker server time; FTMO = New York + 7 h)"
input int    InpOpenHour        = 16;     // Session open hour (16:30 = 09:30 New York)
input int    InpOpenMinute      = 30;     // Session open minute
input int    InpCloseHour       = 23;     // Session close hour (23:00 = 16:00 New York)
input int    InpCloseMinute     = 0;      // Session close minute
input int    InpStepMinutes     = 30;     // Minutes between checkpoints
input int    InpLookback        = 14;     // Noise-area lookback (sessions)
input int    InpMinLookback     = 10;     // Sessions with the price required for sigma

input group "Sizing and stop"
input double InpLeverage        = 1.0;    // Position notional / account equity
input double InpStopPct         = 1.25;   // Stop-loss on every order, % of price from the entry (0 = none)

input group "Account protection"
input double InpStartBalance    = 0.0;    // Starting balance (0 = balance when first attached, remembered)
input double InpDailyStopPct    = 2.5;    // Close all and stop for the day at this % below the day's start (0 = off)
input double InpHardStopPct     = 8.5;    // Close all and halt at this % below the start (0 = off)
input bool   InpTrailingHardStop = false; // Measure the hard stop from the highest day-start balance (1-Step)
input bool   InpNewsFilter      = false;  // Skip orders within 2 min of high-impact US news (funded Standard)

input group "Trade management"
input ulong  InpMagic           = 20261008;
input int    InpDeviation       = 20;     // Max slippage (points)
input string InpComment         = "S2 noise area";
input string InpLogFile         = "auto"; // Common\Files log ("auto" = S2_<login>_<symbol>.csv, empty = off)

CTrade   trade;
datetime g_day          = 0;      // server-day start of the session being traded
bool     g_ready        = false;  // today's open, previous close and bands are known
double   g_open         = 0.0;
double   g_prevClose    = 0.0;
double   g_upper[MAX_CHECKPOINTS];
double   g_lower[MAX_CHECKPOINTS];
int      g_checkpoints  = 0;
int      g_nextCheck    = 0;      // index of the next checkpoint to process
bool     g_flatDone     = false;
double   g_startBalance = 0.0;
double   g_dayBalance   = 0.0;
double   g_peakDayBalance = 0.0;
bool     g_dailyStopped = false;
bool     g_halted       = false;
string   g_haltKey      = "";
string   g_peakKey      = "";
int      g_log          = INVALID_HANDLE;
string   g_status       = "";

//+------------------------------------------------------------------+
int OnInit()
  {
   if(InpOpenMinute < 0 || InpOpenMinute > 59 || InpCloseMinute < 0 || InpCloseMinute > 59)
      return(Fail("Minutes must be between 0 and 59"));
   const int openM = InpOpenHour * 60 + InpOpenMinute, closeM = InpCloseHour * 60 + InpCloseMinute;
   if(closeM <= openM + 2 * InpStepMinutes || InpStepMinutes <= 0)
      return(Fail("The session must hold at least one checkpoint"));
   g_checkpoints = (closeM - openM - InpStepMinutes) / InpStepMinutes;   // open+step ... close-step
   if(g_checkpoints > MAX_CHECKPOINTS)
      return(Fail("Too many checkpoints"));
   if(InpLookback < 1 || InpMinLookback < 1 || InpMinLookback > InpLookback)
      return(Fail("Lookback settings are invalid"));
   if(InpLeverage <= 0.0 || InpLeverage > 50.0)
      return(Fail("Leverage must be between 0 and 50"));
   if(InpStopPct < 0.0 || InpStopPct >= 20.0)
      return(Fail("Stop-loss % must be between 0 and 20"));
   if(InpDailyStopPct < 0.0 || InpDailyStopPct >= 100.0 || InpHardStopPct < 0.0 || InpHardStopPct >= 100.0)
      return(Fail("Protection percentages must be between 0 and 100"));
   if(SymbolInfoString(_Symbol, SYMBOL_CURRENCY_PROFIT) != AccountInfoString(ACCOUNT_CURRENCY))
      Print("S2: warning - symbol profit currency differs from the account; leverage sizing is approximate");

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints((ulong)InpDeviation);
   trade.SetTypeFillingBySymbol(_Symbol);

   const long login = AccountInfoInteger(ACCOUNT_LOGIN);
   const string startKey = StringFormat("S2_%I64d_start_balance", login);
   if(InpStartBalance > 0.0)
      g_startBalance = InpStartBalance;
   else if(GlobalVariableCheck(startKey))
      g_startBalance = GlobalVariableGet(startKey);
   else
      g_startBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   GlobalVariableSet(startKey, g_startBalance);
   g_haltKey = StringFormat("S2_%I64d_%s_halted", login, _Symbol);
   g_peakKey = StringFormat("S2_%I64d_peak_day_balance", login);
   g_halted  = GlobalVariableCheck(g_haltKey);
   g_peakDayBalance = GlobalVariableCheck(g_peakKey) ? GlobalVariableGet(g_peakKey) : g_startBalance;

   OpenLog();
   PrintFormat("S2 v%s on %s: session %02d:%02d-%02d:%02d server, %d checkpoints every %d min, lookback %d (min %d), leverage %.2fx, stop %.2f%%",
               S2_VERSION, _Symbol, InpOpenHour, InpOpenMinute, InpCloseHour, InpCloseMinute, g_checkpoints,
               InpStepMinutes, InpLookback, InpMinLookback, InpLeverage, InpStopPct);
   PrintFormat("  Protection: start %.2f, daily stop -%.1f%%, hard stop -%.1f%% (%s), news filter %s",
               g_startBalance, InpDailyStopPct, InpHardStopPct, InpTrailingHardStop ? "trailing" : "static",
               InpNewsFilter ? "on" : "off");
   if(g_halted)
      Print("  HALTED by the hard stop: delete the global variable ", g_haltKey, " to trade again");
   UpdatePanel();
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   if(g_log != INVALID_HANDLE)
      FileClose(g_log);
   if(!MQLInfoInteger(MQL_TESTER))
      Comment("");
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   const datetime now = TimeCurrent();
   const datetime day = DayStart(now);
   if(day != g_day)
      NewDay(day);

   Protect();
   if(g_halted)
     {
      CloseAll("hard stop");
      return;
     }
   if(g_dailyStopped)
     {
      CloseAll("daily stop");
      return;
     }

   const datetime sessionOpen  = g_day + (InpOpenHour * 60 + InpOpenMinute) * 60;
   const datetime sessionClose = g_day + (InpCloseHour * 60 + InpCloseMinute) * 60;

   if(!g_ready && now >= sessionOpen + 60)
      PrepareSession(sessionOpen);

   // flat from the last minute of the session (the backtest exits at the 15:59 bar's close)
   if(now >= sessionClose - 60)
     {
      if(!g_flatDone)
        {
         CloseAll("session close");
         g_flatDone = (CountPositions() == 0);
        }
      return;
     }

   if(!g_ready)
      return;
   while(g_nextCheck < g_checkpoints)
     {
      const datetime at = sessionOpen + (g_nextCheck + 1) * InpStepMinutes * 60;
      if(now < at)
         break;
      if(now < at + 5 * 60)          // a checkpoint is only traded within 5 minutes of its time
         Checkpoint(g_nextCheck, at);
      g_nextCheck++;
     }
  }

//+------------------------------------------------------------------+
void NewDay(const datetime day)
  {
   g_day = day;
   g_ready = false;
   g_nextCheck = 0;
   g_flatDone = false;
   g_dailyStopped = false;
   g_dayBalance = DayStartBalance(day);
   if(g_dayBalance > g_peakDayBalance)
     {
      g_peakDayBalance = g_dayBalance;
      GlobalVariableSet(g_peakKey, g_peakDayBalance);
     }
   g_status = "waiting for the session open";
   UpdatePanel();
  }

//--- today's open, the previous close and the noise bands
void PrepareSession(const datetime sessionOpen)
  {
   double open;
   if(!BarOpen(sessionOpen, open))
     {
      g_status = "no opening bar today - not trading";
      g_ready = false;
      g_nextCheck = g_checkpoints;   // nothing to do today
      UpdatePanel();
      return;
     }
   double prevClose;
   datetime prevSession;
   if(!PreviousSession(sessionOpen, prevSession) || !PriceAt(prevSession + (InpCloseHour * 60 + InpCloseMinute) * 60 - (InpOpenHour * 60 + InpOpenMinute) * 60, prevClose))
     {
      g_status = "no previous close - not trading";
      g_nextCheck = g_checkpoints;
      UpdatePanel();
      return;
     }
   double sigma[MAX_CHECKPOINTS];
   if(!Sigma(sessionOpen, sigma))
     {
      g_status = "not enough history for the noise area - not trading";
      g_nextCheck = g_checkpoints;
      UpdatePanel();
      return;
     }
   g_open = open;
   g_prevClose = prevClose;
   for(int k = 0; k < g_checkpoints; k++)
     {
      g_upper[k] = MathMax(open, prevClose) * (1.0 + sigma[k]);
      g_lower[k] = MathMin(open, prevClose) * (1.0 - sigma[k]);
     }
   g_ready = true;
   g_status = StringFormat("open %s, previous close %s", DoubleToString(open, _Digits), DoubleToString(prevClose, _Digits));
   // a late attach skips the checkpoints already past
   while(g_nextCheck < g_checkpoints && TimeCurrent() >= sessionOpen + (g_nextCheck + 1) * InpStepMinutes * 60 + 5 * 60)
      g_nextCheck++;
   UpdatePanel();
  }

//--- the decision at one checkpoint
void Checkpoint(const int k, const datetime at)
  {
   double price;
   if(!PriceAt(at, price))
      return;
   if(InpNewsFilter && NewsNear(at))
     {
      Print("S2: checkpoint ", TimeToString(at, TIME_MINUTES), " skipped - high-impact news within 2 minutes");
      return;
     }
   int position = PositionDirection();
   if((position > 0 && price <= g_upper[k]) || (position < 0 && price >= g_lower[k]))
     {
      CloseAll(position > 0 ? "long back inside the band" : "short back inside the band");
      position = PositionDirection();
     }
   if(position == 0 && (price > g_upper[k] || price < g_lower[k]))
     {
      const int direction = price > g_upper[k] ? 1 : -1;
      const double lots = Lots();
      if(lots <= 0.0)
         return;
      double sl = 0.0;
      if(InpStopPct > 0.0)
        {
         const double reference = direction > 0 ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);
         sl = NormalizeDouble(direction > 0 ? reference * (1.0 - InpStopPct / 100.0)
                                            : reference * (1.0 + InpStopPct / 100.0), _Digits);
        }
      const bool sent = direction > 0 ? trade.Buy(lots, _Symbol, 0.0, sl, 0.0, InpComment)
                                      : trade.Sell(lots, _Symbol, 0.0, sl, 0.0, InpComment);
      if(!sent || (trade.ResultRetcode() != TRADE_RETCODE_DONE && trade.ResultRetcode() != TRADE_RETCODE_PLACED))
         PrintFormat("S2: order failed %u %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
      else
         PrintFormat("S2: %s %.2f lots at checkpoint %s: price %s vs bands %s / %s",
                     direction > 0 ? "long" : "short", lots, TimeToString(at, TIME_MINUTES),
                     DoubleToString(price, _Digits), DoubleToString(g_lower[k], _Digits), DoubleToString(g_upper[k], _Digits));
     }
   UpdatePanel();
  }

//--- sigma per checkpoint from the previous sessions that have the price
bool Sigma(const datetime sessionOpen, double &sigma[])
  {
   double sum[MAX_CHECKPOINTS];
   int    count[MAX_CHECKPOINTS];
   ArrayInitialize(sum, 0.0);
   ArrayInitialize(count, 0);
   int sessions = 0;
   datetime cursor = sessionOpen;
   for(int back = 0; back < 60 && sessions < InpLookback; back++)
     {
      datetime previous;
      if(!PreviousSession(cursor, previous))
         break;
      cursor = previous;
      double open;
      if(!BarOpen(previous, open))
         continue;
      sessions++;
      for(int k = 0; k < g_checkpoints; k++)
        {
         double price;
         if(PriceAt(previous + (k + 1) * InpStepMinutes * 60, price))
           {
            sum[k] += MathAbs(price / open - 1.0);
            count[k]++;
           }
        }
     }
   for(int k = 0; k < g_checkpoints; k++)
     {
      if(count[k] < InpMinLookback)
         return(false);
      sigma[k] = sum[k] / count[k];
     }
   return(true);
  }

//--- the session open of the latest earlier day that has an opening bar
bool PreviousSession(const datetime sessionOpen, datetime &previous)
  {
   for(int d = 1; d <= 10; d++)
     {
      const datetime candidate = sessionOpen - d * 86400;
      double open;
      if(BarOpen(candidate, open))
        {
         previous = candidate;
         return(true);
        }
     }
   return(false);
  }

//--- open of the M1 bar that starts exactly at t
bool BarOpen(const datetime t, double &open)
  {
   const int shift = iBarShift(_Symbol, PERIOD_M1, t, true);
   if(shift < 0)
      return(false);
   open = iOpen(_Symbol, PERIOD_M1, shift);
   return(open > 0.0);
  }

//--- price at time t: close of the minute bar ending at t, or the last close up to 5 minutes older
bool PriceAt(const datetime t, double &price)
  {
   const int shift = iBarShift(_Symbol, PERIOD_M1, t - 60, false);
   if(shift < 0)
      return(false);
   const datetime barTime = iTime(_Symbol, PERIOD_M1, shift);
   if(barTime > t - 60 || barTime < t - 60 - 5 * 60)
      return(false);
   price = iClose(_Symbol, PERIOD_M1, shift);
   return(price > 0.0);
  }

//--- leverage x equity in notional, rounded down to the volume step
double Lots()
  {
   const double price = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   const double contract = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);
   if(price <= 0.0 || contract <= 0.0)
      return(0.0);
   double lots = InpLeverage * AccountInfoDouble(ACCOUNT_EQUITY) / (price * contract);
   const double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   const double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   const double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(step > 0.0)
      lots = MathFloor(lots / step) * step;
   if(lots < minLot)
     {
      PrintFormat("S2: %.2fx needs %.3f lots, below the minimum %.2f - no trade", InpLeverage, lots, minLot);
      return(0.0);
     }
   return(NormalizeDouble(MathMin(lots, maxLot), 2));
  }

//+------------------------------------------------------------------+
//| Account protection                                               |
//+------------------------------------------------------------------+
void Protect()
  {
   if(g_halted)
      return;
   const double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   const double base = InpTrailingHardStop ? MathMax(g_startBalance, g_peakDayBalance) : g_startBalance;
   if(InpHardStopPct > 0.0 && equity <= base - g_startBalance * InpHardStopPct / 100.0)
     {
      g_halted = true;
      GlobalVariableSet(g_haltKey, (double)TimeCurrent());
      Notify(StringFormat("S2 HARD STOP on %s: equity %.2f reached the limit %.2f - closing all and halting",
                          _Symbol, equity, base - g_startBalance * InpHardStopPct / 100.0));
      CloseAll("hard stop");
      return;
     }
   if(InpDailyStopPct > 0.0 && !g_dailyStopped && equity <= g_dayBalance * (1.0 - InpDailyStopPct / 100.0))
     {
      g_dailyStopped = true;
      Notify(StringFormat("S2 daily stop on %s: equity %.2f is %.1f%% below today's start %.2f - flat until tomorrow",
                          _Symbol, equity, 100.0 * (1.0 - equity / g_dayBalance), g_dayBalance));
      CloseAll("daily stop");
     }
  }

double DayStartBalance(const datetime day)
  {
   double today = 0.0;
   if(HistorySelect(day, TimeCurrent() + 60))
      for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
        {
         const ulong deal = HistoryDealGetTicket(i);
         const long type = HistoryDealGetInteger(deal, DEAL_TYPE);
         if(type != DEAL_TYPE_BUY && type != DEAL_TYPE_SELL)
            continue;
         today += HistoryDealGetDouble(deal, DEAL_PROFIT) + HistoryDealGetDouble(deal, DEAL_COMMISSION)
                  + HistoryDealGetDouble(deal, DEAL_SWAP) + HistoryDealGetDouble(deal, DEAL_FEE);
        }
   return(AccountInfoDouble(ACCOUNT_BALANCE) - today);
  }

//--- high-impact US calendar events within 2 minutes of t (server time)
bool NewsNear(const datetime t)
  {
   MqlCalendarValue values[];
   if(CalendarValueHistory(values, t - 120, t + 120, "US", NULL) <= 0)
      return(false);
   for(int i = 0; i < ArraySize(values); i++)
     {
      MqlCalendarEvent event;
      if(CalendarEventById(values[i].event_id, event) && event.importance == CALENDAR_IMPORTANCE_HIGH)
         return(true);
     }
   return(false);
  }

//+------------------------------------------------------------------+
//| Positions, logging, display                                      |
//+------------------------------------------------------------------+
int PositionDirection()
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != (long)InpMagic)
         continue;
      return(PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? 1 : -1);
     }
   return(0);
  }

int CountPositions()
  {
   int n = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = PositionGetTicket(i);
      if(ticket != 0 && PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == (long)InpMagic)
         n++;
     }
   return(n);
  }

void CloseAll(const string why)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != (long)InpMagic)
         continue;
      if(!trade.PositionClose(ticket))
         PrintFormat("S2: %s close of #%I64u failed: %u %s", why, ticket, trade.ResultRetcode(), trade.ResultRetcodeDescription());
     }
  }

void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request, const MqlTradeResult &result)
  {
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD || g_log == INVALID_HANDLE || !HistoryDealSelect(trans.deal))
      return;
   if(HistoryDealGetString(trans.deal, DEAL_SYMBOL) != _Symbol || HistoryDealGetInteger(trans.deal, DEAL_MAGIC) != (long)InpMagic)
      return;
   const long entry = HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
   FileWrite(g_log, TimeToString((datetime)HistoryDealGetInteger(trans.deal, DEAL_TIME), TIME_DATE | TIME_SECONDS),
             entry == DEAL_ENTRY_IN ? "in" : "out",
             HistoryDealGetInteger(trans.deal, DEAL_TYPE) == DEAL_TYPE_BUY ? "buy" : "sell",
             DoubleToString(HistoryDealGetDouble(trans.deal, DEAL_VOLUME), 2),
             DoubleToString(HistoryDealGetDouble(trans.deal, DEAL_PRICE), _Digits),
             DoubleToString(HistoryDealGetDouble(trans.deal, DEAL_PROFIT), 2),
             DoubleToString(HistoryDealGetDouble(trans.deal, DEAL_COMMISSION), 2),
             DoubleToString(HistoryDealGetDouble(trans.deal, DEAL_SWAP), 2),
             (string)HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID),
             DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2), DoubleToString(InpLeverage, 2));
   FileFlush(g_log);
  }

void OpenLog()
  {
   if(StringLen(InpLogFile) == 0 || MQLInfoInteger(MQL_OPTIMIZATION))
      return;
   string name = InpLogFile;
   if(name == "auto")
      name = StringFormat("S2_%I64d_%s.csv", AccountInfoInteger(ACCOUNT_LOGIN), _Symbol);
   g_log = FileOpen(name, FILE_READ | FILE_WRITE | FILE_CSV | FILE_COMMON | FILE_ANSI, ',');
   if(g_log == INVALID_HANDLE)
     {
      Print("S2: cannot open the log ", name);
      return;
     }
   if(MQLInfoInteger(MQL_TESTER))
      FileSeek(g_log, 0, SEEK_SET);
   if(FileSize(g_log) == 0 || MQLInfoInteger(MQL_TESTER))
      FileWrite(g_log, "time", "entry", "type", "lots", "price", "profit", "commission", "swap", "position", "balance", "leverage");
   FileSeek(g_log, 0, SEEK_END);
   Print("S2: trade log ", TerminalInfoString(TERMINAL_COMMONDATA_PATH), "\\Files\\", name);
  }

void UpdatePanel()
  {
   if(MQLInfoInteger(MQL_OPTIMIZATION))
      return;
   string s = StringFormat("S2 noise area v%s | %s | %.2fx leverage\n", S2_VERSION, _Symbol, InpLeverage);
   s += "Today: " + g_status + "\n";
   if(g_ready && g_nextCheck < g_checkpoints)
      s += StringFormat("Next checkpoint %s: long above %s, short below %s\n",
                        TimeToString(g_day + (InpOpenHour * 60 + InpOpenMinute + (g_nextCheck + 1) * InpStepMinutes) * 60, TIME_MINUTES),
                        DoubleToString(g_upper[g_nextCheck], _Digits), DoubleToString(g_lower[g_nextCheck], _Digits));
   s += StringFormat("Position: %s\n", PositionDirection() > 0 ? "long" : PositionDirection() < 0 ? "short" : "flat");
   if(g_halted)
      s += "HALTED by the hard stop - delete global variable " + g_haltKey + " to trade again";
   else
      s += StringFormat("Protection: daily stop %s | hard stop at %.2f", g_dailyStopped ? "REACHED" :
                        StringFormat("at %.2f", g_dayBalance * (1.0 - InpDailyStopPct / 100.0)),
                        (InpTrailingHardStop ? MathMax(g_startBalance, g_peakDayBalance) : g_startBalance) - g_startBalance * InpHardStopPct / 100.0);
   Comment(s);
  }

void Notify(const string message)
  {
   Print(message);
   if(!MQLInfoInteger(MQL_TESTER))
      Alert(message);
   UpdatePanel();
  }

datetime DayStart(const datetime t)
  {
   MqlDateTime parts;
   TimeToStruct(t, parts);
   parts.hour = 0;
   parts.min = 0;
   parts.sec = 0;
   return(StructToTime(parts));
  }

int Fail(const string message)
  {
   Print("S2: ", message);
   return(INIT_PARAMETERS_INCORRECT);
  }
//+------------------------------------------------------------------+
