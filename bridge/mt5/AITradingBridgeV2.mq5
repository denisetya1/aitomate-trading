#property copyright "Deni"
#property version   "2.00"
#property strict

#include <Trade/Trade.mqh>

input string AnalysisSymbol = "XAUUSDc";
input int PublishEverySeconds = 5;
input int ScreenshotEverySeconds = 30;
input int CandleCount = 250;
input bool AllowRealExecution = true;
input double FixedVolume = 0.01;
input long ExpertMagic = 2601001;

string bridge_directory = "AITradingEngineV2";
string snapshot_file = "AITradingEngineV2\\market.json";
string temporary_file = "AITradingEngineV2\\market.json.tmp";
string request_file = "AITradingEngineV2\\request.txt";
string receipt_file = "AITradingEngineV2\\receipt.json";
string screenshot_file = "AITradingEngineV2\\latest.png";
datetime last_screenshot_at = 0;
CTrade trade;

bool CaptureChartScreenshot()
{
   datetime now = TimeLocal();
   if(last_screenshot_at > 0 && now - last_screenshot_at < MathMax(1, ScreenshotEverySeconds))
      return true;

   FolderCreate(bridge_directory);
   if(!ChartScreenShot(0, screenshot_file, 1440, 900, ALIGN_RIGHT))
   {
      Print("AITradingBridgeV2 screenshot failed: ", GetLastError());
      return false;
   }
   last_screenshot_at = now;
   return true;
}

void WriteReceipt(const string request_id, const string setup_id, const bool accepted,
                  const string status, const ulong order_ticket, const ulong deal_ticket)
{
   string temporary_receipt = receipt_file + ".tmp";
   int handle = FileOpen(temporary_receipt, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(handle == INVALID_HANDLE)
   {
      Print("AITradingBridgeV2 cannot write receipt: ", GetLastError());
      return;
   }
   string payload = "{";
   payload += "\"request_id\":" + JsonString(request_id);
   payload += ",\"setup_id\":" + JsonString(setup_id);
   payload += ",\"accepted\":" + (accepted ? "true" : "false");
   payload += ",\"status\":" + JsonString(status);
   payload += ",\"order_ticket\":" + StringFormat("%I64u", order_ticket);
   payload += ",\"deal_ticket\":" + StringFormat("%I64u", deal_ticket);
   payload += ",\"recorded_at\":" + StringFormat("%I64d", (long)TimeTradeServer());
   payload += "}";
   FileWriteString(handle, payload);
   FileFlush(handle);
   FileClose(handle);
   FileDelete(receipt_file, FILE_COMMON);
   FileMove(temporary_receipt, FILE_COMMON, receipt_file, FILE_COMMON);
}

bool WasProcessed(const string comment)
{
   for(int index = 0; index < PositionsTotal(); index++)
   {
      if(PositionGetTicket(index) > 0 && PositionGetInteger(POSITION_MAGIC) == ExpertMagic &&
         PositionGetString(POSITION_COMMENT) == comment)
         return true;
   }
   for(int index = 0; index < OrdersTotal(); index++)
   {
      if(OrderGetTicket(index) > 0 && OrderGetInteger(ORDER_MAGIC) == ExpertMagic &&
         OrderGetString(ORDER_COMMENT) == comment)
         return true;
   }
   if(!HistorySelect(TimeCurrent() - 30 * 86400, TimeCurrent()))
      return false;
   for(int index = HistoryDealsTotal() - 1; index >= 0; index--)
   {
      ulong ticket = HistoryDealGetTicket(index);
      if(ticket > 0 && HistoryDealGetInteger(ticket, DEAL_MAGIC) == ExpertMagic &&
         HistoryDealGetString(ticket, DEAL_COMMENT) == comment)
         return true;
   }
   return false;
}

void RejectRequest(const string request_id, const string setup_id, const string reason)
{
   WriteReceipt(request_id, setup_id, false, reason, 0, 0);
   FileDelete(request_file, FILE_COMMON);
   Print("AITradingBridgeV2 rejected request: ", reason);
}

void ProcessRequest()
{
   if(!FileIsExist(request_file, FILE_COMMON))
      return;

   int handle = FileOpen(request_file, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(handle == INVALID_HANDLE)
      return;
   string line = FileReadString(handle);
   FileClose(handle);

   string fields[];
   if(StringSplit(line, '|', fields) != 11)
   {
      RejectRequest("unknown", "unknown", "invalid_request_format");
      return;
   }

   string request_id = fields[1];
   string setup_id = fields[2];
   long expires_at = (long)StringToInteger(fields[4]);
   string symbol = fields[5];
   string direction = fields[6];
   double requested_volume = StringToDouble(fields[7]);
   double stop_loss = StringToDouble(fields[8]);
   double take_profit = StringToDouble(fields[9]);
   int deviation = (int)StringToInteger(fields[10]);

   if(!AllowRealExecution)
   {
      RejectRequest(request_id, setup_id, "real_execution_disabled");
      return;
   }
   if(fields[0] != "1" || symbol != AnalysisSymbol || expires_at < (long)TimeTradeServer())
   {
      RejectRequest(request_id, setup_id, "request_invalid_or_expired");
      return;
   }
   if(MathAbs(requested_volume - FixedVolume) > 0.0000001 || MathAbs(FixedVolume - 0.01) > 0.0000001)
   {
      RejectRequest(request_id, setup_id, "volume_must_be_0.01");
      return;
   }
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))
   {
      RejectRequest(request_id, setup_id, "terminal_or_account_trade_disabled");
      return;
   }

   MqlTick tick;
   if(!SymbolInfoTick(symbol, tick))
   {
      RejectRequest(request_id, setup_id, "quote_unavailable");
      return;
   }
   bool is_buy = direction == "BUY";
   bool is_sell = direction == "SELL";
   if(!is_buy && !is_sell)
   {
      RejectRequest(request_id, setup_id, "invalid_direction");
      return;
   }
   if((is_buy && !(stop_loss < tick.ask && take_profit > tick.ask)) ||
      (is_sell && !(stop_loss > tick.bid && take_profit < tick.bid)))
   {
      RejectRequest(request_id, setup_id, "invalid_stop_or_target_side");
      return;
   }

   string comment = "Vission-" + StringSubstr(request_id, 0, 16);
   if(WasProcessed(comment))
   {
      WriteReceipt(request_id, setup_id, true, "duplicate_already_processed", 0, 0);
      FileDelete(request_file, FILE_COMMON);
      return;
   }

   trade.SetExpertMagicNumber(ExpertMagic);
   trade.SetDeviationInPoints(MathMax(0, deviation));
   trade.SetTypeFillingBySymbol(symbol);
   bool sent = is_buy
      ? trade.Buy(FixedVolume, symbol, 0.0, stop_loss, take_profit, comment)
      : trade.Sell(FixedVolume, symbol, 0.0, stop_loss, take_profit, comment);
   uint retcode = trade.ResultRetcode();
   bool accepted = sent && (retcode == TRADE_RETCODE_DONE || retcode == TRADE_RETCODE_PLACED ||
                            retcode == TRADE_RETCODE_DONE_PARTIAL);
   WriteReceipt(request_id, setup_id, accepted,
                accepted ? "executed" : "broker_rejected_" + IntegerToString((int)retcode),
                trade.ResultOrder(), trade.ResultDeal());
   FileDelete(request_file, FILE_COMMON);
}

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
   result += ",\"position_id\":" + StringFormat("%I64d", PositionGetInteger(POSITION_IDENTIFIER));
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

string DealsJson(const int digits)
{
   if(!HistorySelect(TimeCurrent() - 90 * 86400, TimeCurrent()))
      return "[]";
   string result = "[";
   bool first = true;
   for(int index = 0; index < HistoryDealsTotal(); index++)
   {
      ulong ticket = HistoryDealGetTicket(index);
      if(ticket == 0 || HistoryDealGetInteger(ticket, DEAL_MAGIC) != ExpertMagic)
         continue;
      if(!first)
         result += ",";
      result += "{";
      result += "\"ticket\":" + StringFormat("%I64u", ticket);
      result += ",\"position_id\":" + StringFormat("%I64d", HistoryDealGetInteger(ticket, DEAL_POSITION_ID));
      result += ",\"entry\":" + IntegerToString((int)HistoryDealGetInteger(ticket, DEAL_ENTRY));
      result += ",\"type\":" + IntegerToString((int)HistoryDealGetInteger(ticket, DEAL_TYPE));
      result += ",\"volume\":" + Number(HistoryDealGetDouble(ticket, DEAL_VOLUME));
      result += ",\"price\":" + Price(HistoryDealGetDouble(ticket, DEAL_PRICE), digits);
      result += ",\"profit\":" + Number(HistoryDealGetDouble(ticket, DEAL_PROFIT));
      result += ",\"commission\":" + Number(HistoryDealGetDouble(ticket, DEAL_COMMISSION));
      result += ",\"swap\":" + Number(HistoryDealGetDouble(ticket, DEAL_SWAP));
      result += ",\"time_msc\":" + StringFormat("%I64d", HistoryDealGetInteger(ticket, DEAL_TIME_MSC));
      result += ",\"comment\":" + JsonString(HistoryDealGetString(ticket, DEAL_COMMENT));
      result += "}";
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
   result += ",\"terminal_trade_allowed\":" + (TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) ? "true" : "false");
   result += "}";
   result += ",\"positions\":" + PositionsJson(digits);
   result += ",\"orders\":" + OrdersJson(digits);
   result += ",\"deals\":" + DealsJson(digits);
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
   trade.SetExpertMagicNumber(ExpertMagic);
   PublishSnapshot();
   CaptureChartScreenshot();
   ProcessRequest();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

void OnTimer()
{
   PublishSnapshot();
   CaptureChartScreenshot();
   ProcessRequest();
}
