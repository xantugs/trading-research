//+------------------------------------------------------------------+
//|                                            Import_WRDS_Ticks.mq5 |
//| Import one verified WRDS-to-MT5 daily tick CSV into a custom QQQ |
//+------------------------------------------------------------------+
#property script_show_inputs
#property version "1.00"

input string InpFileName       = "QQQ_2018-01-02_ticks.csv"; // Exact name or wildcard, e.g. QQQ_*_ticks.csv
input string InpCustomSymbol   = "QQQ.WRDS";
input string InpOriginSymbol   = "";          // Optional broker stock symbol to copy properties from
input bool   InpUseCommonFiles = true;        // Read from Terminal Common\Files
input int    InpDigits         = 2;

bool SetDoubleProperty(const ENUM_SYMBOL_INFO_DOUBLE property, const double value)
  {
   if(CustomSymbolSetDouble(InpCustomSymbol, property, value))
      return(true);
   PrintFormat("Cannot set property %d to %G. Error %d", property, value, GetLastError());
   return(false);
  }

bool PrepareSymbol()
  {
   ResetLastError();
   const bool created = InpOriginSymbol == ""
                        ? CustomSymbolCreate(InpCustomSymbol, "ORB")
                        : CustomSymbolCreate(InpCustomSymbol, "ORB", InpOriginSymbol);
   const int error = GetLastError();
   if(!created && error != 5304)
     {
      PrintFormat("CustomSymbolCreate failed. Error %d", error);
      return(false);
     }

   if(!CustomSymbolSetInteger(InpCustomSymbol, SYMBOL_DIGITS, InpDigits) ||
      !CustomSymbolSetInteger(InpCustomSymbol, SYMBOL_TRADE_CALC_MODE, SYMBOL_CALC_MODE_EXCH_STOCKS) ||
      !CustomSymbolSetInteger(InpCustomSymbol, SYMBOL_TRADE_MODE, SYMBOL_TRADE_MODE_FULL) ||
      !CustomSymbolSetInteger(InpCustomSymbol, SYMBOL_CHART_MODE, SYMBOL_CHART_MODE_BID) ||
      !CustomSymbolSetInteger(InpCustomSymbol, SYMBOL_SPREAD_FLOAT, true))
     {
      PrintFormat("Cannot configure integer symbol properties. Error %d", GetLastError());
      return(false);
     }
   if(!SetDoubleProperty(SYMBOL_POINT, 0.01) ||
      !SetDoubleProperty(SYMBOL_TRADE_TICK_SIZE, 0.01) ||
      !SetDoubleProperty(SYMBOL_TRADE_TICK_VALUE, 0.01) ||
      !SetDoubleProperty(SYMBOL_TRADE_TICK_VALUE_PROFIT, 0.01) ||
      !SetDoubleProperty(SYMBOL_TRADE_TICK_VALUE_LOSS, 0.01) ||
      !SetDoubleProperty(SYMBOL_TRADE_CONTRACT_SIZE, 1.0) ||
      !SetDoubleProperty(SYMBOL_VOLUME_MIN, 1.0) ||
      !SetDoubleProperty(SYMBOL_VOLUME_STEP, 1.0) ||
      !SetDoubleProperty(SYMBOL_VOLUME_MAX, 100000.0))
      return(false);
   if(!CustomSymbolSetString(InpCustomSymbol, SYMBOL_CURRENCY_BASE, "QQQ") ||
      !CustomSymbolSetString(InpCustomSymbol, SYMBOL_CURRENCY_PROFIT, "USD") ||
      !CustomSymbolSetString(InpCustomSymbol, SYMBOL_CURRENCY_MARGIN, "USD"))
     {
      PrintFormat("Cannot configure symbol currencies. Error %d", GetLastError());
      return(false);
     }
   for(int day = MONDAY; day <= FRIDAY; day++)
     {
      if(!CustomSymbolSetSessionQuote(InpCustomSymbol, (ENUM_DAY_OF_WEEK)day, 0, 0, 86399) ||
         !CustomSymbolSetSessionTrade(InpCustomSymbol, (ENUM_DAY_OF_WEEK)day, 0, 0, 86399))
        {
         PrintFormat("Cannot configure full-day weekday session %d. Error %d", day, GetLastError());
         return(false);
        }
     }
   return(true);
  }

bool LoadTicks(const string fileName, MqlTick &ticks[], datetime &sourceDay)
  {
   const int flags = FILE_READ | FILE_CSV | FILE_ANSI | (InpUseCommonFiles ? FILE_COMMON : 0);
   const int handle = FileOpen(fileName, flags, '\t', CP_UTF8);
   if(handle == INVALID_HANDLE)
     {
      PrintFormat("Cannot open %s. Error %d", fileName, GetLastError());
      return(false);
     }

   int count = 0;
   int capacity = 0;
   long previousMsc = -1;
   sourceDay = 0;
   while(!FileIsEnding(handle))
     {
      const string dateText = FileReadString(handle);
      const string timeText = FileReadString(handle);
      const string bidText = FileReadString(handle);
      const string askText = FileReadString(handle);
      const string lastText = FileReadString(handle);
      const string volumeText = FileReadString(handle);
      if(dateText == "" && FileIsEnding(handle))
         break;
      if(dateText == "<DATE>")
         continue;
      if(StringLen(dateText) != 10 || StringLen(timeText) != 12)
        {
         PrintFormat("Malformed date/time at data row %d: %s %s", count + 1, dateText, timeText);
         FileClose(handle);
         return(false);
        }
      const datetime seconds = StringToTime(dateText + " " + StringSubstr(timeText, 0, 8));
      const int millis = (int)StringToInteger(StringSubstr(timeText, 9, 3));
      const long timeMsc = (long)seconds * 1000 + millis;
      const datetime day = (datetime)(seconds - seconds % 86400);
      const double bid = StringToDouble(bidText);
      const double ask = StringToDouble(askText);
      const double last = StringToDouble(lastText);
      const long volume = StringToInteger(volumeText);
      if(seconds <= 0 || millis < 0 || millis > 999 || bid <= 0.0 || ask < bid ||
         last != 0.0 || volume != 0 || (previousMsc >= 0 && timeMsc < previousMsc) ||
         (sourceDay != 0 && day != sourceDay))
        {
         PrintFormat("Invalid or out-of-order tick at data row %d", count + 1);
         FileClose(handle);
         return(false);
        }
      if(sourceDay == 0)
         sourceDay = day;
      if(count == capacity)
        {
         capacity += 100000;
         if(ArrayResize(ticks, capacity, 100000) != capacity)
           {
            Print("Cannot allocate tick array");
            FileClose(handle);
            return(false);
           }
        }
      ticks[count].time = seconds;
      ticks[count].time_msc = timeMsc;
      ticks[count].bid = bid;
      ticks[count].ask = ask;
      ticks[count].last = 0.0;
      ticks[count].volume = 0;
      ticks[count].volume_real = 0.0;
      ticks[count].flags = TICK_FLAG_BID | TICK_FLAG_ASK;
      previousMsc = timeMsc;
      count++;
     }
   FileClose(handle);
   ArrayResize(ticks, count);
   if(count == 0)
     {
      Print("The input contains no tick rows");
      return(false);
     }
   return(true);
  }

bool ImportOneFile(const string fileName)
  {
   MqlTick ticks[];
   datetime sourceDay = 0;
   if(!LoadTicks(fileName, ticks, sourceDay))
      return(false);
   const long fromMsc = (long)sourceDay * 1000;
   const long toMsc = fromMsc + 86400000 - 1;
   ResetLastError();
   const int imported = CustomTicksReplace(InpCustomSymbol, fromMsc, toMsc, ticks);
   if(imported != ArraySize(ticks))
     {
      PrintFormat("Import failed: CustomTicksReplace returned %d for %d ticks. Error %d",
                  imported, ArraySize(ticks), GetLastError());
      return(false);
     }
   PrintFormat("Imported %s: %d WRDS price ticks into %s for %s (New York wall time)",
               fileName, imported, InpCustomSymbol, TimeToString(sourceDay, TIME_DATE));
   return(true);
  }

void OnStart()
  {
   if(!PrepareSymbol()) return;
   const bool wildcard = StringFind(InpFileName, "*") >= 0 || StringFind(InpFileName, "?") >= 0;
   if(!wildcard)
     {
      ImportOneFile(InpFileName);
      return;
     }
   string fileName = "";
   const int commonFlag = InpUseCommonFiles ? FILE_COMMON : 0;
   const long search = FileFindFirst(InpFileName, fileName, commonFlag);
   if(search == INVALID_HANDLE)
     {
      PrintFormat("No files match %s. Error %d", InpFileName, GetLastError());
      return;
     }
   int importedFiles = 0;
   int failedFiles = 0;
   do
     {
      if(ImportOneFile(fileName)) importedFiles++;
      else failedFiles++;
     }
   while(FileFindNext(search, fileName));
   FileFindClose(search);
   PrintFormat("WRDS batch complete: %d imported file(s), %d failed file(s)",
               importedFiles, failedFiles);
  }
