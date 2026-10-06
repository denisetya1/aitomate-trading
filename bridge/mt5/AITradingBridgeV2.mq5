#property copyright "Deni"
#property version   "2.00"
#property strict

input string AnalysisSymbol = "XAUUSDc";
input int PublishEverySeconds = 5;
input int CandleCount = 250;

string bridge_directory = "AITradingEngineV2";
string snapshot_file = "AITradingEngineV2\\market.json";
string temporary_file = "AITradingEngineV2\\market.json.tmp";

string JsonString(string value)
{
   StringReplace(value, "\\", "\\\\");
   StringReplace(value, "\"", "\\\"");
   StringReplace(value, "\r", "\\r");
   StringReplace(value, "\n", "\\n");
   return "\"" + value + "\"";
}

string Price(const double value, const int digits)
{
   return DoubleToString(value, digits);
}

string Number(const double value)
{
   return DoubleToString(value, 8);
}

string PositionJson(const int index, const int digits)
{
   ulong ticket = PositionGetTicket(index);
   if(ticket == 0)
      return "";

   string result = "{";
   result += "\"ticket\":" + StringFormat("%I64u", ticket);
   result += ",\"symbol\":" + JsonString(PositionGetString(POSITION_SYMBOL));
   result += ",\"type\":" + IntegerToString((int)PositionGetInteger(POSITION_TYPE));
   result += ",\"volume\":" + Number(PositionGetDouble(POSITION_VOLUME));
   result += ",\"price_open\":" + Price(PositionGetDouble(POSITION_PRICE_OPEN), digits);
   result += ",\"sl\":" + Price(PositionGetDouble(POSITION_SL), digits);
   result += ",\"tp\":" + Price(PositionGetDouble(POSITION_TP), digits);
   result += ",\"profit\":" + Number(PositionGetDouble(POSITION_PROFIT));
   result += ",\"time_msc\":" + StringFormat("%I64d", PositionGetInteger(POSITION_TIME_MSC));
   result += ",\"magic\":" + StringFormat("%I64d", PositionGetInteger(POSITION_MAGIC));
   result += ",\"comment\":" + JsonString(PositionGetString(POSITION_COMMENT));
   result += "}";
   return result;
}

string OrderJson(const int index, const int digits)
{
   ulong ticket = OrderGetTicket(index);
   if(ticket == 0)
      return "";

   string result = "{";
   result += "\"ticket\":" + StringFormat("%I64u", ticket);
   result += ",\"symbol\":" + JsonString(OrderGetString(ORDER_SYMBOL));
   result += ",\"type\":" + IntegerToString((int)OrderGetInteger(ORDER_TYPE));
   result += ",\"state\":" + IntegerToString((int)OrderGetInteger(ORDER_STATE));
   result += ",\"volume_initial\":" + Number(OrderGetDouble(ORDER_VOLUME_INITIAL));
   result += ",\"volume_current\":" + Number(OrderGetDouble(ORDER_VOLUME_CURRENT));
   result += ",\"price_open\":" + Price(OrderGetDouble(ORDER_PRICE_OPEN), digits);
   result += ",\"sl\":" + Price(OrderGetDouble(ORDER_SL), digits);
   result += ",\"tp\":" + Price(OrderGetDouble(ORDER_TP), digits);
   result += ",\"time_setup_msc\":" + StringFormat("%I64d", OrderGetInteger(ORDER_TIME_SETUP_MSC));
   result += ",\"magic\":" + StringFormat("%I64d", OrderGetInteger(ORDER_MAGIC));
   result += ",\"comment\":" + JsonString(OrderGetString(ORDER_COMMENT));
   result += "}";
   return result;
}

string PositionsJson(const int digits)
{
   string result = "[";
   bool first = true;
   for(int index = 0; index < PositionsTotal(); index++)
   {
      string item = PositionJson(index, digits);
      if(item == "")
         continue;
      if(!first)
         result += ",";
      result += item;
      first = false;
   }
   return result + "]";
}

string OrdersJson(const int digits)
{
   string result = "[";
   bool first = true;
   for(int index = 0; index < OrdersTotal(); index++)
   {
      string item = OrderJson(index, digits);
      if(item == "")
         continue;
      if(!first)
         result += ",";
      result += item;
      first = false;
   }
   return result + "]";
}

string CandlesJson(const ENUM_TIMEFRAMES timeframe, const int digits)
{
   MqlRates rates[];
   int copied = CopyRates(AnalysisSymbol, timeframe, 1, CandleCount, rates);
   if(copied <= 0)
      return "[]";

   int duration = PeriodSeconds(timeframe);
   string result = "[";
   for(int index = 0; index < copied; index++)
   {
      if(index > 0)
         result += ",";
      result += "{";
      result += "\"open_time\":" + StringFormat("%I64d", (long)rates[index].time);
      result += ",\"close_time\":" + StringFormat("%I64d", (long)rates[index].time + duration);
      result += ",\"open\":" + Price(rates[index].open, digits);
      result += ",\"high\":" + Price(rates[index].high, digits);
      result += ",\"low\":" + Price(rates[index].low, digits);
      result += ",\"close\":" + Price(rates[index].close, digits);
      result += ",\"tick_volume\":" + StringFormat("%I64d", rates[index].tick_volume);
      result += ",\"spread\":" + IntegerToString(rates[index].spread);
      result += "}";
   }
   return result + "]";
}

string BuildSnapshot()
{
   MqlTick tick;
   if(!SymbolInfoTick(AnalysisSymbol, tick))
      return "";

   int digits = (int)SymbolInfoInteger(AnalysisSymbol, SYMBOL_DIGITS);
   string result = "{";
   result += "\"schema_version\":2";
   result += ",\"snapshot_id\":" + JsonString(StringFormat("%I64d", tick.time_msc));
   result += ",\"captured_at\":" + StringFormat("%I64d", tick.time_msc / 1000);
   result += ",\"published_at\":" + StringFormat("%I64d", (long)TimeLocal());
   result += ",\"server_time\":" + StringFormat("%I64d", (long)TimeTradeServer());
   result += ",\"symbol\":" + JsonString(AnalysisSymbol);
   result += ",\"quote\":{";
   result += "\"captured_at_msc\":" + StringFormat("%I64d", tick.time_msc);
   result += ",\"bid\":" + Price(tick.bid, digits);
   result += ",\"ask\":" + Price(tick.ask, digits);
   result += "}";
   result += ",\"symbol_spec\":{";
   result += "\"digits\":" + IntegerToString(digits);
   result += ",\"point\":" + Number(SymbolInfoDouble(AnalysisSymbol, SYMBOL_POINT));
   result += ",\"tick_size\":" + Number(SymbolInfoDouble(AnalysisSymbol, SYMBOL_TRADE_TICK_SIZE));
   result += ",\"tick_value\":" + Number(SymbolInfoDouble(AnalysisSymbol, SYMBOL_TRADE_TICK_VALUE));
   result += ",\"contract_size\":" + Number(SymbolInfoDouble(AnalysisSymbol, SYMBOL_TRADE_CONTRACT_SIZE));
   result += ",\"volume_min\":" + Number(SymbolInfoDouble(AnalysisSymbol, SYMBOL_VOLUME_MIN));
   result += ",\"volume_max\":" + Number(SymbolInfoDouble(AnalysisSymbol, SYMBOL_VOLUME_MAX));
   result += ",\"volume_step\":" + Number(SymbolInfoDouble(AnalysisSymbol, SYMBOL_VOLUME_STEP));
   result += ",\"stops_level_points\":" + IntegerToString((int)SymbolInfoInteger(AnalysisSymbol, SYMBOL_TRADE_STOPS_LEVEL));
   result += ",\"freeze_level_points\":" + IntegerToString((int)SymbolInfoInteger(AnalysisSymbol, SYMBOL_TRADE_FREEZE_LEVEL));
   int filling_mode = (int)SymbolInfoInteger(AnalysisSymbol, SYMBOL_FILLING_MODE);
   result += ",\"filling_mode_flags\":" + IntegerToString(filling_mode);
   result += ",\"filling_modes\":[" + IntegerToString(filling_mode) + "]";
   result += ",\"trade_mode\":" + IntegerToString((int)SymbolInfoInteger(AnalysisSymbol, SYMBOL_TRADE_MODE));
   result += "}";
   result += ",\"account\":{";
   result += "\"balance\":" + Number(AccountInfoDouble(ACCOUNT_BALANCE));
   result += ",\"equity\":" + Number(AccountInfoDouble(ACCOUNT_EQUITY));
   result += ",\"free_margin\":" + Number(AccountInfoDouble(ACCOUNT_MARGIN_FREE));
   result += ",\"currency\":" + JsonString(AccountInfoString(ACCOUNT_CURRENCY));
   result += ",\"trade_allowed\":" + (AccountInfoInteger(ACCOUNT_TRADE_ALLOWED) ? "true" : "false");
   result += "}";
   result += ",\"positions\":" + PositionsJson(digits);
   result += ",\"orders\":" + OrdersJson(digits);
   result += ",\"candles\":{";
   result += "\"M5\":" + CandlesJson(PERIOD_M5, digits);
   result += ",\"M15\":" + CandlesJson(PERIOD_M15, digits);
   result += ",\"H1\":" + CandlesJson(PERIOD_H1, digits);
   result += ",\"H4\":" + CandlesJson(PERIOD_H4, digits);
   result += ",\"D1\":" + CandlesJson(PERIOD_D1, digits);
   result += "}}";
   return result;
}

bool PublishSnapshot()
{
   string snapshot = BuildSnapshot();
   if(snapshot == "")
      return false;

   FolderCreate(bridge_directory, FILE_COMMON);
   int handle = FileOpen(temporary_file, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(handle == INVALID_HANDLE)
   {
      Print("AITradingBridgeV2 FileOpen failed: ", GetLastError());
      return false;
   }
   FileWriteString(handle, snapshot);
   FileFlush(handle);
   FileClose(handle);

   FileDelete(snapshot_file, FILE_COMMON);
   if(!FileMove(temporary_file, FILE_COMMON, snapshot_file, FILE_COMMON))
   {
      Print("AITradingBridgeV2 FileMove failed: ", GetLastError());
      return false;
   }
   return true;
}

int OnInit()
{
   if(!SymbolSelect(AnalysisSymbol, true))
   {
      Print("AITradingBridgeV2 cannot select symbol: ", AnalysisSymbol);
      return INIT_FAILED;
   }
   EventSetTimer(MathMax(1, PublishEverySeconds));
   PublishSnapshot();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

void OnTimer()
{
   PublishSnapshot();
}
