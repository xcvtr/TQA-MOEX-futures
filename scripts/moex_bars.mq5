//+------------------------------------------------------------------+
//|                                                    moex_bars.mq5 |
//| Writes M1 OHLCV to JSON Lines file every minute                  |
//+------------------------------------------------------------------+
#property strict

string TICKERS[] = {"Si-3.27","MM-3.27","GZ-3.27","BR-1.27","SV-3.27",
                    "CR-3.27","GD-3.27","RN-3.27","NG-1.27"};
string OUTPUT_FILE = "moex_bars.jsonl";
datetime last_minute = 0;

int OnInit() {
   EventSetTimer(1);
   return(INIT_SUCCEEDED);
}

void OnTimer() {
   datetime now = TimeCurrent();
   int sec = (int)now % 60;
   if (sec < 55 || now == last_minute) return;
   last_minute = now;
   
   string line = "";
   for (int i = 0; i < ArraySize(TICKERS); i++) {
      MqlRates rates[];
      if (CopyRates(TICKERS[i], PERIOD_M1, 0, 1, rates) == 1) {
         if (line != "") line += "\n";
         line += StringFormat("{\"t\":\"%s\",\"bt\":%d,\"o\":%g,\"h\":%g,\"l\":%g,\"c\":%g,\"v\":%d}",
            TICKERS[i], (int)rates[0].time, rates[0].open, rates[0].high,
            rates[0].low, rates[0].close, (int)rates[0].tick_volume);
      }
   }
   
   if (line != "") {
      int h = FileOpen(OUTPUT_FILE, FILE_WRITE|FILE_TXT|FILE_COMMON, 0, CP_ACP);
      if (h != INVALID_HANDLE) {
         FileWrite(h, line);
         FileClose(h);
      }
   }
}
