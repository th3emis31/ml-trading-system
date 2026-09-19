//+------------------------------------------------------------------+
//|                                              AurumFlow_MT5.mq5   |
//|   MT5 port of "Aurum Flow.mq4" v7.05, Sahitya / Trade Smart FX   |
//|   Tools. Original: https://tradesmartfxtools.in                  |
//+------------------------------------------------------------------+
//| WHAT THIS IS
//|
//| A faithful port of the owner's MQL4 source to MQL5. Same entry rule, same
//| filters, same defaults, same licence check against the vendor's own server.
//| It is a translation, not a redesign: where MQL5 forces a different API the
//| behaviour is kept and the difference is commented with "PORT:".
//|
//| THE LICENCE CHECK IS INTACT AND UNCHANGED. It still calls
//| tradesmartfxtools.in and still refuses to initialise unless that server
//| answers OK (or a successful check is cached inside GraceHours). Nothing here
//| removes, weakens or works around the vendor's licensing, and this file is for
//| the owner's own account only.
//|
//| READ THIS BEFORE RUNNING IT ANYWHERE THAT MATTERS
//|
//| The defaults are the original's defaults, including EnableRecoveryMode2.
//| That is a martingale: once an open position is Recovery2TriggerPips against,
//| every further move of that size opens ANOTHER position in the same losing
//| direction with a linearly growing lot (0.01, 0.02, 0.03 ...) up to
//| Recovery2MaxTrades = 60, and those added positions are sent with NO stop loss
//| and NO take profit. CheckMultiDealBreakeven then closes the whole basket the
//| moment total floating profit reaches $0.01. The only backstop is
//| CheckFloatingLossLimit at a flat -$100 of floating loss, which is a fixed
//| dollar figure rather than a share of equity.
//|
//| Separately, the entry rule was replicated and tested on Vantage XAUUSD broker
//| candles (src/aurum_flow_lab.py, strategies/aurum_flow.md). Holdout, after
//| spread and swap: 15m PF 0.791, net -13.23%, 267 trades; 1h PF 0.747, net
//| -24.81%, 403 trades. Traded the OPPOSITE way the same signals also lose
//| (PF 0.768 and 0.706), which means the entry carries no directional edge.
//| The stop (2100 points) is larger than the target (1800 points), so
//| break-even needs a 53.8% win rate and the test measured 53.18% and 50.87%.
//|
//| Those are measurements, not instructions. What you run is your decision.
//+------------------------------------------------------------------+
#property copyright "Trade Smart FX Tools"
#property link      "https://tradesmartfxtools.in"
#property version   "7.05"
#property description "Aurum Flow (MT5 port)"
#property description "Aurum Flow combines supply & demand zones,"
#property description "volume logic, and pending order structure"
#property description "to capture high-probability market flows."
#property description ""
#property description "This EA uses market structure and predefined"
#property description "execution rules to reduce emotional trading."

#include <Trade\Trade.mqh>

CTrade g_trade;

string LicenseURL       = "https://tradesmartfxtools.in/LicenseKey/aurum-flow-lite.php";
int    LicenseTimeoutMs = 12000;
int    GraceHours       = 24;

// ===================== INPUTS =====================

input string  ___Structure___ = "=== STRUCTURE ENGINE ===";

input int    StructureDepth      = 200;  // AFCX Depth
input int    StructureSpacing     = 100;  // AFCX Spacing
input int    StructureRefreshBars = 40;   // AFCX Refresh Bars

//============= BASIC SETTINGS =============//
input string  ___Basic_Settings___ = "=== Basic Settings ===";

input double Lots            = 0.01; // Base Lot Size
input int    SL              = 2100; // Stop Loss
input int    TP              = 1800; // Take Profit
input int    Slippage        = 5;
// PORT: the original wrote 060701111. A leading zero is an OCTAL literal, so MT4
// actually used 12812873. This is that same number, written unambiguously.
input long   MagicNumber     = 12812873;
input bool   UseBodyInsteadOfRange = false; // Use Candle Body Instead Of Range

input bool UseZoneStopLoss = false; // Use Supply/Demand Zone SL
input int  ZoneSLBuffer    = 50;    // Extra buffer below/above zone

// ===================== FLOATING LOSS COOLDOWN =====================
input string  ____FloatingLossCooldown____ = "=== FLOATING LOSS COOLDOWN ===";
input bool   EnableFloatingLossCooldown  = true;
input double FloatingLossCooldownTrigger = -100.0;
input int    FloatingLossCooldownHours   = 24;

datetime g_floatingLossCooldownUntil = 0;

// ===================== MAX TRADE DURATION =====================
input string ___MaxTradeDuration___ = "=== MAX TRADE DURATION ===";
input bool   EnableMaxTradeDuration = true;
input int    MaxTradeDurationDays   = 5;

// ===================== SPREAD FILTER =====================
input string ___SPREAD_FILTER___ = "=== SPREAD FILTER ===";
input bool EnableSpreadFilter = true;
input int  MinSpreadPoints    = 9;
input int  MaxSpreadPoints    = 25;

input string ___Risk_Management___ = "=== Risk Management Settings ===";

// ===================== RECOVERY MODE 2 (CASCADE) =====================
string  ___RecoveryMode2___ = "=== RECOVERY MODE ===";
input bool EnableRecoveryMode2 = true;   // Enable Candle Based Recovery
int    Recovery2TriggerPips       = 200;
double Recovery2CloseProfit       = 0.01;
int    Recovery2MaxTrades         = 60;
double Recovery2SingleCloseProfit = 4.0;

// ===================== MA FILTER =====================
input string ___AFCXFilter___ = "=== AFCX FILTER ===";
input bool   EnableMAFilter = true;  // Enable Safe AFCX Mode
input int    MAPeriod       = 600;   // AFCX Time
input int    MAShift        = 0;     // AFCX Heigth
string       MAMethod       = "SMA"; // AFCX Code
input int    MAPrice        = 0;     // AFCX Width

input bool OnlyOneTradeAtATime = true; // Single Trade Cycle

bool  EnableLosingStreakPause = true;
input int LosingStreakTrigger = 1;
input int StreakPauseMinutes  = 500;

input bool TradeInNovember = false;
input bool TradeInDecember = false;

bool   EnableSizeAdjustment  = false;
double SizeAdjustmentStep    = 0.01;
int    RecoveryConfirmations = 2;
input double MaxLot          = 0.1;

input bool UsePendingOrders     = true;
input int  PendingOrderDistance = 130;
input int  PendingExpiryMinutes = 60;

input bool SafetyZoneFilter = false;
input int  SafetyZoneBars   = 100;

input int CooldownMinutes = 10;

// ===================== MULTI-DEAL BREAKEVEN =====================
input string ___MultiDealBreakeven___ = "=== MULTI-DEAL BREAKEVEN ===";
input bool   EnableMultiDealBreakeven = true;

//--------------------------------------------------------------------------------------------------
bool     g_licenseValid     = false;
datetime g_lastLicenseCheck = 0;

double WeekStartEquity = 0;
int    StoredWeek      = -1;
string AF_SUPPLY = "AF_SUPPLY_ZONE";
string AF_DEMAND = "AF_DEMAND_ZONE";

int ZoneLookbackBars = 380;

double g_maValue  = 0;
int    g_maHandle = INVALID_HANDLE;   // PORT: MQL5 indicators are handles + CopyBuffer

bool   g_recovery2Active = false;
ulong  g_recovery2Tickets[];          // PORT: MQL5 position tickets are ulong
int    g_recovery2Count = 0;
double g_recovery2OriginalDirection = 0;
double g_recovery2OriginalLot = 0;
datetime g_recovery2LastCandleTime = 0;
double g_recovery2LastAddedPrice = 0;
int    g_recovery2LastAddedLevel = 0;
bool   g_tpSlRemoved = false;

color SoftRed   = (color)0x005050C8;
color SoftGreen = (color)0x0080C850;

double g_supplyHigh = 0;
double g_supplyLow  = 0;
double g_demandHigh = 0;
double g_demandLow  = 0;

int    g_consecutiveLosses = 0;
double g_currentLot = 0.0;
int    g_consecutiveWins = 0;
datetime g_cooldownUntil = 0;
bool     g_redrawAfterCooldown = false;
datetime g_lastProcessedCloseTime = 0;

bool   g_lossRangeActive = false;
double g_lossRangeHigh   = 0.0;
double g_lossRangeLow    = 0.0;

bool   g_invalidSettings = false;
string g_invalidReason   = "";

string TL_HIGH = "TL_PEAKS_HIGH";
string TL_LOW  = "TL_PEAKS_LOW";

datetime g_lastBarTime = 0;
int      g_barsSinceRedraw = 999999;

#define PANEL_NAME   "EA_STATS_PANEL"
#define PANEL_X      10
#define PANEL_Y      20
#define PANEL_W      360
#define PANEL_H      370

//+------------------------------------------------------------------+
//| PORT: price series helpers.                                       |
//| MQL4 has iOpen/iHigh/iLow/iClose/iTime/iBars/iHighest/iLowest as  |
//| language built-ins. Rather than rely on which MT5 build provides   |
//| which of them, these read the series directly with CopyX so the    |
//| file compiles on any MT5. Shift 0 is the forming bar, exactly as   |
//| in MQL4.                                                          |
//+------------------------------------------------------------------+
double AF_Open(int shift)
{
   double buf[];
   if(shift < 0 || CopyOpen(_Symbol, _Period, shift, 1, buf) != 1) return 0.0;
   return buf[0];
}

double AF_High(int shift)
{
   double buf[];
   if(shift < 0 || CopyHigh(_Symbol, _Period, shift, 1, buf) != 1) return 0.0;
   return buf[0];
}

double AF_Low(int shift)
{
   double buf[];
   if(shift < 0 || CopyLow(_Symbol, _Period, shift, 1, buf) != 1) return 0.0;
   return buf[0];
}

double AF_Close(int shift)
{
   double buf[];
   if(shift < 0 || CopyClose(_Symbol, _Period, shift, 1, buf) != 1) return 0.0;
   return buf[0];
}

datetime AF_Time(int shift)
{
   datetime buf[];
   if(shift < 0 || CopyTime(_Symbol, _Period, shift, 1, buf) != 1) return 0;
   return buf[0];
}

int AF_Bars()
{
   return Bars(_Symbol, _Period);
}

double AF_Bid() { return SymbolInfoDouble(_Symbol, SYMBOL_BID); }
double AF_Ask() { return SymbolInfoDouble(_Symbol, SYMBOL_ASK); }

// PORT: MQL4's iHighest / iLowest, rewritten. Returns a shift, or -1.
int AF_Highest(int count, int startShift)
{
   if(count <= 0) return -1;
   double buf[];
   if(CopyHigh(_Symbol, _Period, startShift, count, buf) != count) return -1;
   int best = 0;                       // CopyHigh returns oldest-first
   for(int i = 1; i < count; i++)
      if(buf[i] > buf[best]) best = i;
   return startShift + (count - 1 - best);
}

int AF_Lowest(int count, int startShift)
{
   if(count <= 0) return -1;
   double buf[];
   if(CopyLow(_Symbol, _Period, startShift, count, buf) != count) return -1;
   int best = 0;
   for(int i = 1; i < count; i++)
      if(buf[i] < buf[best]) best = i;
   return startShift + (count - 1 - best);
}

// PORT: MQL4's iBarShift.
int AF_BarShift(datetime when)
{
   int shift = Bars(_Symbol, _Period, when, TimeCurrent()) - 1;
   return (shift < 0 ? 0 : shift);
}

// PORT: MQL4's TimeMonth / TimeDayOfWeek / ... became MqlDateTime fields.
int AF_Month(datetime t)     { MqlDateTime s; TimeToStruct(t, s); return s.mon; }
int AF_Year(datetime t)      { MqlDateTime s; TimeToStruct(t, s); return s.year; }
int AF_DayOfWeek(datetime t) { MqlDateTime s; TimeToStruct(t, s); return s.day_of_week; }
int AF_DayOfYear(datetime t) { MqlDateTime s; TimeToStruct(t, s); return s.day_of_year; }
int AF_Hour(datetime t)      { MqlDateTime s; TimeToStruct(t, s); return s.hour; }
int AF_Minute(datetime t)    { MqlDateTime s; TimeToStruct(t, s); return s.min; }
int AF_Seconds(datetime t)   { MqlDateTime s; TimeToStruct(t, s); return s.sec; }

//+------------------------------------------------------------------+
//| CORE FUNCTIONS                                                   |
//+------------------------------------------------------------------+

void DrawZone(string name, datetime t1, double high, datetime t2, double low, color clr)
{
   if(ObjectFind(0, name) >= 0)
      ObjectDelete(0, name);

   ObjectCreate(0, name, OBJ_RECTANGLE, 0, t1, high, t2, low);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_BACK, true);
   ObjectSetInteger(0, name, OBJPROP_STYLE, STYLE_SOLID);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
}

// NOTE, carried over unchanged from the original: the four g_supply*/g_demand*
// values this sets are never read by any trading decision, and UseZoneStopLoss /
// ZoneSLBuffer are never used. The zones are drawing only. Kept so the chart
// looks the same as MT4.
void DrawSupplyDemandVisual()
{
   int lookback = MathMin(ZoneLookbackBars, AF_Bars() - 5);

   double bestSupplyStrength = 0;
   double bestDemandStrength = 0;

   int bestSupplyIndex = -1;
   int bestDemandIndex = -1;

   for(int i = 5; i < lookback; i++)
   {
      double open  = AF_Open(i);
      double close = AF_Close(i);
      double high  = AF_High(i);
      double low   = AF_Low(i);

      double body  = MathAbs(close - open);
      double range = high - low;

      if(range <= 0) continue;

      double strength = body / range;

      if(close > open && strength > 0.6)
      {
         if(AF_Close(i - 1) < AF_Open(i - 1))
         {
            if(strength > bestSupplyStrength)
            {
               bestSupplyStrength = strength;
               bestSupplyIndex = i;
            }
         }
      }

      if(close < open && strength > 0.6)
      {
         if(AF_Close(i - 1) > AF_Open(i - 1))
         {
            if(strength > bestDemandStrength)
            {
               bestDemandStrength = strength;
               bestDemandIndex = i;
            }
         }
      }
   }

   if(bestSupplyIndex > 0)
   {
      datetime t1 = AF_Time(bestSupplyIndex);
      datetime t2 = TimeCurrent();

      double high = AF_High(bestSupplyIndex);
      double low  = AF_Open(bestSupplyIndex);

      g_supplyHigh = high;
      g_supplyLow  = low;

      DrawZone(AF_SUPPLY, t1, high, t2, low, clrRed);
   }

   if(bestDemandIndex > 0)
   {
      datetime t1 = AF_Time(bestDemandIndex);
      datetime t2 = TimeCurrent();

      double high = AF_Open(bestDemandIndex);
      double low  = AF_Low(bestDemandIndex);
      g_demandHigh = high;
      g_demandLow  = low;
      DrawZone(AF_DEMAND, t1, high, t2, low, clrLime);
   }
}

//+------------------------------------------------------------------+
//| PANEL FUNCTIONS                                                  |
//+------------------------------------------------------------------+

void CreatePanel()
{
   if(ObjectFind(0, PANEL_NAME) >= 0) return;

   ObjectCreate(0, PANEL_NAME, OBJ_RECTANGLE_LABEL, 0, 0, 0);
   ObjectSetInteger(0, PANEL_NAME, OBJPROP_XDISTANCE, PANEL_X);
   ObjectSetInteger(0, PANEL_NAME, OBJPROP_YDISTANCE, PANEL_Y);
   ObjectSetInteger(0, PANEL_NAME, OBJPROP_XSIZE, PANEL_W);
   ObjectSetInteger(0, PANEL_NAME, OBJPROP_YSIZE, PANEL_H);
   ObjectSetInteger(0, PANEL_NAME, OBJPROP_BGCOLOR, clrBlack);
   ObjectSetInteger(0, PANEL_NAME, OBJPROP_BORDER_TYPE, BORDER_FLAT);
   ObjectSetInteger(0, PANEL_NAME, OBJPROP_COLOR, clrDimGray);
   ObjectSetInteger(0, PANEL_NAME, OBJPROP_BACK, false);
}

void DrawText(string name, string text, int x, int y, color clr)
{
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);

   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 9);
   ObjectSetString(0, name, OBJPROP_FONT, "Consolas");
   ObjectSetString(0, name, OBJPROP_TEXT, text);
}

void UpdateStatsPanel()
{
   CreatePanel();

   int y = PANEL_Y + 10;
   int x = PANEL_X + 10;

   DrawText("hdr", "Aurum Flow", x, y, clrWhite);
   y += 16;

   DrawText("sep1", "----------------------------", x, y, clrDimGray);
   y += 18;

   DrawText("lic", "License        : " + (g_licenseValid ? "VALID" : "EXPIRED"),
            x, y, g_licenseValid ? clrLime : clrRed);
   y += 16;

   bool allowed = IsTradingAllowedNow();

   DrawText("st2", "Trading Status   : " + (allowed ? "ALLOWED" : "BLOCKED"),
            x, y, allowed ? clrLime : clrRed);
   y += 16;

   DrawText("st3", "Block Reason     : " + GetBlockedReason(),
            x, y, allowed ? clrSilver : clrRed);
   y += 22;

   DrawText("lot1", "Current Lot      : " + DoubleToString(g_currentLot, 2), x, y, clrWhite); y += 16;
   DrawText("lot2", "Initial Lot      : " + DoubleToString(Lots, 2), x, y, clrSilver); y += 16;
   DrawText("lot3", "Win Streak       : " + IntegerToString(g_consecutiveWins), x, y, clrLime); y += 16;
   DrawText("lot4", "Loss Streak      : " + IntegerToString(g_consecutiveLosses), x, y, clrRed); y += 22;

   double curProfit = AccountInfoDouble(ACCOUNT_EQUITY) - AccountInfoDouble(ACCOUNT_BALANCE);
   datetime now = TimeCurrent();
   double weekProfit  = ClosedProfitFrom(StartOfWeek(now));
   double monthProfit = ClosedProfitFrom(StartOfMonth(now));
   double curSpread = GetSpreadPoints();

   DrawText("p2", "This Week Profit : " + DoubleToString(weekProfit, 2), x, y, clrWhite); y += 16;
   DrawText("p3", "This Month Profit: " + DoubleToString(monthProfit, 2), x, y, clrWhite); y += 16;
   DrawText("p4", "Current Profit   : " + DoubleToString(curProfit, 2), x, y, clrWhite); y += 22;

   bool linesOK = (ObjectFind(0, TL_HIGH) >= 0 && ObjectFind(0, TL_LOW) >= 0);
   DrawText("tr1", "Trendline Status : " + (linesOK ? "VALID" : "MISSING"),
            x, y, linesOK ? clrLime : clrRed); y += 16;

   DrawText("tr2", "Last Redraw      : " + IntegerToString(g_barsSinceRedraw) + " bars ago",
            x, y, clrSilver); y += 22;

   DrawText("lr1", "Loss Range Lock  : " + (g_lossRangeActive ? "ACTIVE" : "OFF"),
            x, y, g_lossRangeActive ? clrRed : clrLime); y += 16;

   DrawText("spr", "Spread           : " + DoubleToString(curSpread, 0) + " pts",
            x, y, IsSpreadOK() ? clrLime : clrRed);
   y += 16;

   if(g_lossRangeActive)
   {
      DrawText("lr2", "Range High       : " + DoubleToString(g_lossRangeHigh, _Digits), x, y, clrSilver); y += 16;
      DrawText("lr3", "Range Low        : " + DoubleToString(g_lossRangeLow, _Digits), x, y, clrSilver); y += 22;
   }
   else
   {
      DrawText("lr2", "Range High       : -", x, y, clrDimGray); y += 16;
      DrawText("lr3", "Range Low        : -",  x, y, clrDimGray); y += 22;
   }

   string nextAction = GetNextAction();
   DrawText("na", "Next Action      : " + nextAction, x, y, clrAqua);
}

void DrawAuthorLabel()
{
   string name = "tradesmartfxtools.author";
   string text = "Made by Sahitya, tradesmartfxtools.in";
   string font = "Arial";

   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);

   ObjectSetInteger(0, name, OBJPROP_CORNER,     CORNER_LEFT_LOWER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE,  12);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE,  35);
   ObjectSetInteger(0, name, OBJPROP_COLOR,      clrYellow);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE,   15);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTED,   false);
   ObjectSetString(0, name, OBJPROP_TEXT,        text);
   ObjectSetString(0, name, OBJPROP_FONT,        font);
}

//+------------------------------------------------------------------+
//| HELPER FUNCTIONS                                                 |
//+------------------------------------------------------------------+

bool IsNewBar()
{
   datetime t = AF_Time(0);
   if(t != g_lastBarTime)
   {
      g_lastBarTime = t;
      return true;
   }
   return false;
}

double CandleSize(int shift)
{
   if(shift < 0) return 0;
   if(UseBodyInsteadOfRange)
      return MathAbs(AF_Close(shift) - AF_Open(shift));
   return AF_High(shift) - AF_Low(shift);
}

bool IsSwingHigh(int i)
{
   if(i <= 0 || i >= AF_Bars() - 1) return false;
   double h  = AF_High(i);
   double h1 = AF_High(i + 1);
   double h2 = AF_High(i - 1);
   return (h > h1 && h > h2);
}

bool IsSwingLow(int i)
{
   if(i <= 0 || i >= AF_Bars() - 1) return false;
   double l  = AF_Low(i);
   double l1 = AF_Low(i + 1);
   double l2 = AF_Low(i - 1);
   return (l < l1 && l < l2);
}

// PORT: MQL4's OrdersTotal() covered open trades AND pending orders in one list.
// MQL5 splits them, so this asks both.
bool HasOpenTradeOrPending()
{
   return (HasMyMarketOrder() || HasMyPendingOrder());
}

double TrendlinePriceAt(string name, datetime t)
{
   if(ObjectFind(0, name) < 0) return EMPTY_VALUE;
   return ObjectGetValueByTime(0, name, t, 0);
}

bool FindTwoHighPeaks(int &idx1, int &idx2)
{
   idx1 = -1; idx2 = -1;
   double best1 = -1, best2 = -1;

   int start = MathMin(StructureDepth, AF_Bars() - 3);
   for(int i = 2; i <= start; i++)
   {
      if(!IsSwingHigh(i)) continue;
      double h = AF_High(i);

      if(h > best1)
      {
         best2 = best1; idx2 = idx1;
         best1 = h;     idx1 = i;
      }
      else if(h > best2)
      {
         best2 = h; idx2 = i;
      }
   }

   if(idx1 < 0) return false;

   if(idx2 < 0 || MathAbs(idx2 - idx1) < StructureSpacing)
   {
      best2 = -1; idx2 = -1;
      for(int j = 2; j <= start; j++)
      {
         if(j == idx1) continue;
         if(MathAbs(j - idx1) < StructureSpacing) continue;
         if(!IsSwingHigh(j)) continue;
         double hj = AF_High(j);
         if(hj > best2)
         {
            best2 = hj; idx2 = j;
         }
      }
   }

   if(idx2 < 0) return false;
   return true;
}

bool FindTwoLowPeaks(int &idx1, int &idx2)
{
   idx1 = -1; idx2 = -1;
   double best1 = 1e50, best2 = 1e50;

   int start = MathMin(StructureDepth, AF_Bars() - 3);
   for(int i = 2; i <= start; i++)
   {
      if(!IsSwingLow(i)) continue;
      double l = AF_Low(i);

      if(l < best1)
      {
         best2 = best1; idx2 = idx1;
         best1 = l;     idx1 = i;
      }
      else if(l < best2)
      {
         best2 = l; idx2 = i;
      }
   }

   if(idx1 < 0) return false;

   if(idx2 < 0 || MathAbs(idx2 - idx1) < StructureSpacing)
   {
      best2 = 1e50; idx2 = -1;
      for(int j = 2; j <= start; j++)
      {
         if(j == idx1) continue;
         if(MathAbs(j - idx1) < StructureSpacing) continue;
         if(!IsSwingLow(j)) continue;
         double lj = AF_Low(j);
         if(lj < best2)
         {
            best2 = lj; idx2 = j;
         }
      }
   }

   if(idx2 < 0) return false;
   return true;
}

void DrawTrendline(string name, int idxA, double priceA, int idxB, double priceB, bool rayRight)
{
   datetime tA = AF_Time(idxA);
   datetime tB = AF_Time(idxB);

   if(ObjectFind(0, name) >= 0) ObjectDelete(0, name);

   ObjectCreate(0, name, OBJ_TREND, 0, tA, priceA, tB, priceB);
   ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, rayRight);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
   ObjectSetInteger(0, name, OBJPROP_BACK, false);

   if(name == TL_HIGH) ObjectSetInteger(0, name, OBJPROP_COLOR, SoftGreen);
   if(name == TL_LOW)  ObjectSetInteger(0, name, OBJPROP_COLOR, SoftRed);
}

bool RebuildTrendlines()
{
   int h1, h2, l1, l2;

   bool okH = FindTwoHighPeaks(h1, h2);
   bool okL = FindTwoLowPeaks(l1, l2);

   if(!okH || !okL) return false;

   if(h1 < h2) { int tmp = h1; h1 = h2; h2 = tmp; }
   if(l1 < l2) { int tmp2 = l1; l1 = l2; l2 = tmp2; }

   double ph1 = AF_High(h1);
   double ph2 = AF_High(h2);
   double pl1 = AF_Low(l1);
   double pl2 = AF_Low(l2);

   DrawTrendline(TL_HIGH, h1, ph1, h2, ph2, true);
   DrawTrendline(TL_LOW,  l1, pl1, l2, pl2, true);

   g_barsSinceRedraw = 0;
   return true;
}

void TryEntries()
{
   if(!IsTradingAllowedNow()) return;

   if(g_invalidSettings) return;

   if(EnableSpreadFilter && !IsSpreadOK())
      return;

   if(OnlyOneTradeAtATime && HasOpenTradeOrPending()) return;

   if(ObjectFind(0, TL_HIGH) < 0) return;
   if(ObjectFind(0, TL_LOW)  < 0) return;

   if(SafetyZoneFilter && g_lossRangeActive)
   {
      double mid = (AF_Bid() + AF_Ask()) * 0.5;
      if(mid >= g_lossRangeLow && mid <= g_lossRangeHigh)
         return;
   }

   datetime t1 = AF_Time(1);
   datetime t2 = AF_Time(2);

   double upper1 = TrendlinePriceAt(TL_HIGH, t1);
   double lower1 = TrendlinePriceAt(TL_LOW,  t1);
   double upper2 = TrendlinePriceAt(TL_HIGH, t2);
   double lower2 = TrendlinePriceAt(TL_LOW,  t2);

   if(upper1 == EMPTY_VALUE || lower1 == EMPTY_VALUE ||
      upper2 == EMPTY_VALUE || lower2 == EMPTY_VALUE) return;

   double h1 = AF_High(1);
   double l1 = AF_Low(1);
   double h2 = AF_High(2);
   double l2 = AF_Low(2);

   bool candle1CrossUpper = (h1 >= upper1);
   bool candle1CrossLower = (l1 <= lower1);

   bool candle2CrossUpper = (h2 >= upper2);
   bool candle2CrossLower = (l2 <= lower2);

   bool validSellSignal = (candle1CrossLower && candle2CrossLower);
   bool validBuySignal  = (candle1CrossUpper && candle2CrossUpper);

   if(validSellSignal && validBuySignal) return;

   if(EnableMAFilter)
   {
      double maValue = GetMAValue();
      if(maValue <= 0) return;

      double currentPrice = (AF_Bid() + AF_Ask()) * 0.5;

      if(validSellSignal)
      {
         if(currentPrice > maValue) return;
      }
      else if(validBuySignal)
      {
         if(currentPrice < maValue) return;
      }
   }

   double pp = P();

   if(validSellSignal)
   {
      double entry = l1 - PendingOrderDistance * pp;
      double sl = entry + SL * pp;
      double tp = entry - TP * pp;

      PlacePending(ORDER_TYPE_SELL_STOP, entry, sl, tp, PendingExpiryMinutes, "TL Touch SELLSTOP (2-Candle)", clrRed);
      return;
   }

   if(validBuySignal)
   {
      double entry = h1 + PendingOrderDistance * pp;
      double sl = entry - SL * pp;
      double tp = entry + TP * pp;

      PlacePending(ORDER_TYPE_BUY_STOP, entry, sl, tp, PendingExpiryMinutes, "TL Touch BUYSTOP (2-Candle)", clrLime);
      return;
   }
}

double P()
{
   if(_Digits == 3 || _Digits == 5) return _Point * 10.0;
   return _Point;
}

// PORT: in MQL5 OrdersTotal() is pending orders only, which is exactly what this
// wants; the MQL4 original had to test the type of every entry.
void DeleteMyPendingOrders()
{
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0) continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      if((long)OrderGetInteger(ORDER_MAGIC) != MagicNumber) continue;

      if(!g_trade.OrderDelete(ticket))
         Print("Pending delete failed. Err=", GetLastError());
   }
}

bool HasMyPendingOrder()
{
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0) continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      if((long)OrderGetInteger(ORDER_MAGIC) != MagicNumber) continue;
      return true;
   }
   return false;
}

bool HasMyMarketOrder()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((long)PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      return true;
   }
   return false;
}

// PORT: MQL5 positions carry profit and swap but not commission (that lives on
// the deals), and the breakeven logic here triggers at $0.01, so commission has
// to be included or the basket would close early.
double PositionNetProfit(ulong positionTicket)
{
   if(!PositionSelectByTicket(positionTicket)) return 0.0;
   double total = PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
   long positionId = PositionGetInteger(POSITION_IDENTIFIER);
   if(HistorySelectByPosition(positionId))
   {
      int deals = HistoryDealsTotal();
      for(int i = 0; i < deals; i++)
      {
         ulong dealTicket = HistoryDealGetTicket(i);
         if(dealTicket == 0) continue;
         total += HistoryDealGetDouble(dealTicket, DEAL_COMMISSION);
      }
   }
   return total;
}

double PositionNetProfitCurrent()
{
   return PositionNetProfit((ulong)PositionGetInteger(POSITION_TICKET));
}

//+------------------------------------------------------------------+
//| RISK MANAGEMENT FUNCTIONS                                        |
//+------------------------------------------------------------------+

// PORT: MQL4 read closed trades from the order history. MQL5 keeps deals, so the
// newest closing deal (DEAL_ENTRY_OUT) for this symbol and magic is the "last
// closed trade", and its profit is profit + swap + commission.
void CheckLastClosedTradeLoss()
{
   datetime newestClose  = g_lastProcessedCloseTime;
   double   newestProfit = 0;
   bool     found = false;

   datetime from = (datetime)(TimeCurrent() - 30 * 24 * 60 * 60);
   if(!HistorySelect(from, TimeCurrent())) return;

   int deals = HistoryDealsTotal();
   for(int i = deals - 1; i >= 0; i--)
   {
      ulong dealTicket = HistoryDealGetTicket(i);
      if(dealTicket == 0) continue;
      if(HistoryDealGetString(dealTicket, DEAL_SYMBOL) != _Symbol) continue;
      if(HistoryDealGetInteger(dealTicket, DEAL_MAGIC) != MagicNumber) continue;
      if(HistoryDealGetInteger(dealTicket, DEAL_ENTRY) != DEAL_ENTRY_OUT) continue;

      datetime ct = (datetime)HistoryDealGetInteger(dealTicket, DEAL_TIME);
      if(ct <= g_lastProcessedCloseTime) break;

      double p = HistoryDealGetDouble(dealTicket, DEAL_PROFIT)
               + HistoryDealGetDouble(dealTicket, DEAL_SWAP)
               + HistoryDealGetDouble(dealTicket, DEAL_COMMISSION);

      if(ct > newestClose)
      {
         newestClose  = ct;
         newestProfit = p;
         found = true;
      }
   }

   if(!found) return;

   g_lastProcessedCloseTime = newestClose;

   if(newestProfit < 0)
   {
      Print("========================================");
      Print("LOSS DETECTED! Amount: $", newestProfit);
      Print("========================================");

      g_consecutiveLosses++;

      DeleteMyPendingOrders();

      SetLossRangeFromCloseTime(newestClose);

      int seconds = MathMax(0, CooldownMinutes) * 60;
      g_cooldownUntil = TimeCurrent() + seconds;
      g_redrawAfterCooldown = true;

      if(EnableLosingStreakPause && g_consecutiveLosses >= LosingStreakTrigger)
      {
         int streakSeconds = MathMax(0, StreakPauseMinutes) * 60;
         datetime untilStreak = TimeCurrent() + streakSeconds;
         if(untilStreak > g_cooldownUntil) g_cooldownUntil = untilStreak;
      }

      if(EnableSizeAdjustment)
      {
         g_consecutiveWins = 0;
         g_currentLot = NormalizeLotLinear(g_currentLot + SizeAdjustmentStep);
         Print("LOSS -> lot increased to: ", DoubleToString(g_currentLot, 2));
      }

      return;
   }

   if(newestProfit > 0)
   {
      g_consecutiveLosses = 0;

      if(EnableSizeAdjustment)
      {
         g_consecutiveWins++;
         Print("WIN -> lot stays: ", DoubleToString(g_currentLot, 2),
               " | win streak=", g_consecutiveWins);

         if(g_consecutiveWins >= RecoveryConfirmations)
         {
            g_currentLot = Lots;
            g_consecutiveWins = 0;
            Print("RESET -> lot back to: ", DoubleToString(g_currentLot, 2));
         }
      }
   }
}

void SetLossRangeFromCloseTime(datetime lossCloseTime)
{
   if(!SafetyZoneFilter) return;

   int lossShift = AF_BarShift(lossCloseTime);
   if(lossShift < 0) lossShift = 1;

   int count = SafetyZoneBars;
   if(count < 10) count = 10;

   if(lossShift + count >= AF_Bars()) count = AF_Bars() - lossShift - 1;
   if(count < 10) return;

   int hiIndex = AF_Highest(count, lossShift);
   int loIndex = AF_Lowest(count, lossShift);

   if(hiIndex < 0 || loIndex < 0) return;

   g_lossRangeHigh = AF_High(hiIndex);
   g_lossRangeLow  = AF_Low(loIndex);

   g_lossRangeActive = true;

   Print("LOSS RANGE SET: High=", DoubleToString(g_lossRangeHigh, _Digits),
         " Low=", DoubleToString(g_lossRangeLow, _Digits),
         " (bars=", count, " from shift=", lossShift, ")");
}

void UpdateLossRangeLock()
{
   if(!SafetyZoneFilter) return;
   if(!g_lossRangeActive) return;

   if(AF_Bid() > g_lossRangeHigh || AF_Ask() < g_lossRangeLow)
   {
      g_lossRangeActive = false;
      Print("LOSS RANGE UNLOCKED (price exited range).");
   }
}

double NormalizeLotLinear(double lot)
{
   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLotB = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step    = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   double maxAllowed = MathMin(maxLotB, MaxLot);

   if(lot < minLot) lot = minLot;
   if(lot > maxAllowed) lot = maxAllowed;

   if(step > 0) lot = MathRound(lot / step) * step;
   lot = NormalizeDouble(lot, 2);

   if(lot < minLot) lot = minLot;
   if(lot > maxAllowed) lot = maxAllowed;

   return lot;
}

double GetTradeLot()
{
   if(!EnableSizeAdjustment) return NormalizeLotLinear(Lots);

   if(g_currentLot <= 0.0) g_currentLot = Lots;

   return NormalizeLotLinear(g_currentLot);
}

//+------------------------------------------------------------------+
//| FILTER FUNCTIONS                                                 |
//+------------------------------------------------------------------+

bool IsMonthBlocked()
{
   int month = AF_Month(TimeCurrent());

   if(month == 11 && !TradeInNovember)
      return true;

   if(month == 12 && !TradeInDecember)
      return true;

   return false;
}

string GetBlockedReason()
{
   if(!g_licenseValid)
      return "License EXPIRED - Please Renew";

   if(g_invalidSettings) return g_invalidReason;

   if(IsMonthBlocked())
      return "Season Filter (Nov-Dec)";

   if(EnableSpreadFilter)
   {
      if(IsSpreadTooHigh())
         return "Spread Too High (" + DoubleToString(GetSpreadPoints(), 0) + " pts)";

      if(IsSpreadTooLow())
         return "Spread Too Low (" + DoubleToString(GetSpreadPoints(), 0) + " pts)";
   }

   if(TimeCurrent() < g_cooldownUntil)
   {
      int minsLeft = (int)((g_cooldownUntil - TimeCurrent()) / 60);
      if(minsLeft < 0) minsLeft = 0;
      return "Cooldown (" + IntegerToString(minsLeft) + "m left)";
   }

   if(SafetyZoneFilter && g_lossRangeActive)
      return "Loss-Range Lock Active";

   if(EnableFloatingLossCooldown && TimeCurrent() < g_floatingLossCooldownUntil)
   {
      int hoursLeft = (int)((g_floatingLossCooldownUntil - TimeCurrent()) / 3600);
      int minsLeft2 = (int)(((g_floatingLossCooldownUntil - TimeCurrent()) % 3600) / 60);
      if(hoursLeft > 0)
         return "Floating Loss Cooldown (" + IntegerToString(hoursLeft) + "h " + IntegerToString(minsLeft2) + "m left)";
      return "Floating Loss Cooldown (" + IntegerToString(minsLeft2) + "m left)";
   }

   return "-";
}

// PORT: deal-based, same reasoning as CheckLastClosedTradeLoss.
double ClosedProfitFrom(datetime fromTime)
{
   double sum = 0.0;

   if(!HistorySelect(fromTime, TimeCurrent())) return 0.0;

   int deals = HistoryDealsTotal();
   for(int i = 0; i < deals; i++)
   {
      ulong dealTicket = HistoryDealGetTicket(i);
      if(dealTicket == 0) continue;
      if(HistoryDealGetString(dealTicket, DEAL_SYMBOL) != _Symbol) continue;
      if(HistoryDealGetInteger(dealTicket, DEAL_MAGIC) != MagicNumber) continue;
      if(HistoryDealGetInteger(dealTicket, DEAL_ENTRY) != DEAL_ENTRY_OUT) continue;

      datetime ct = (datetime)HistoryDealGetInteger(dealTicket, DEAL_TIME);
      if(ct < fromTime) continue;

      sum += HistoryDealGetDouble(dealTicket, DEAL_PROFIT)
           + HistoryDealGetDouble(dealTicket, DEAL_SWAP)
           + HistoryDealGetDouble(dealTicket, DEAL_COMMISSION);
   }
   return sum;
}

datetime StartOfWeek(datetime t)
{
   int dow = AF_DayOfWeek(t);
   int daysBack = (dow == 0) ? 6 : (dow - 1);

   datetime d = t - daysBack * 86400;
   return (d - (AF_Hour(d) * 3600 + AF_Minute(d) * 60 + AF_Seconds(d)));
}

datetime StartOfMonth(datetime t)
{
   int y = AF_Year(t);
   int m = AF_Month(t);

   string s = StringFormat("%04d.%02d.01 00:00", y, m);
   return StringToTime(s);
}

string GetNextAction()
{
   if(g_invalidSettings)
      return "Step shouldn't be higher than 0.02";

   if(IsMonthBlocked())
      return "Season Filter (Month Blocked)";

   if(EnableSpreadFilter && !IsSpreadOK())
   {
      double sp = GetSpreadPoints();

      if(sp > MaxSpreadPoints)
         return "No Trading: Spread High (" + DoubleToString(sp, 0) + " > " + IntegerToString(MaxSpreadPoints) + " pts)";

      if(sp < MinSpreadPoints)
         return "No Trading: Spread Low (" + DoubleToString(sp, 0) + " < " + IntegerToString(MinSpreadPoints) + " pts)";

      return "No Trading: Spread Block";
   }

   if(TimeCurrent() < g_cooldownUntil)
   {
      int minsLeft = (int)((g_cooldownUntil - TimeCurrent()) / 60);
      if(minsLeft < 0) minsLeft = 0;
      return "Cooldown (" + IntegerToString(minsLeft) + "m left)";
   }

   if(SafetyZoneFilter && g_lossRangeActive)
      return "Waiting: Break Loss-Range";

   if(HasMyPendingOrder())
      return "Pending Order Active";

   if(HasMyMarketOrder())
      return "Managing Open Trade";

   return "Waiting for Breakout";
}

bool IsSpreadOK()
{
   if(!EnableSpreadFilter) return true;

   double spreadPoints = MathRound((AF_Ask() - AF_Bid()) / _Point);

   if(spreadPoints < MinSpreadPoints) return false;
   if(spreadPoints > MaxSpreadPoints) return false;

   return true;
}

double GetSpreadPoints()
{
   return MathRound((AF_Ask() - AF_Bid()) / _Point);
}

bool IsSpreadTooHigh()
{
   if(!EnableSpreadFilter) return false;
   return (GetSpreadPoints() > MaxSpreadPoints);
}

bool IsSpreadTooLow()
{
   if(!EnableSpreadFilter) return false;
   return (GetSpreadPoints() < MinSpreadPoints);
}

bool IsTradingAllowedNow()
{
   if(!g_licenseValid) return false;

   if(g_invalidSettings) return false;
   if(IsMonthBlocked()) return false;
   if(EnableSpreadFilter && !IsSpreadOK()) return false;
   if(TimeCurrent() < g_cooldownUntil) return false;
   if(SafetyZoneFilter && g_lossRangeActive) return false;

   if(EnableFloatingLossCooldown && TimeCurrent() < g_floatingLossCooldownUntil)
      return false;

   return true;
}

void ValidateSettings()
{
   g_invalidSettings = false;
   g_invalidReason   = "";

   if(EnableSizeAdjustment && SizeAdjustmentStep > 0.02)
   {
      g_invalidSettings = true;
      g_invalidReason   = "Invalid Setting: Size Step > 0.02";
   }

   if(EnableSizeAdjustment && SizeAdjustmentStep < 0.0)
   {
      g_invalidSettings = true;
      g_invalidReason   = "Invalid Setting: Size Step < 0";
   }
}

void TrackWeekStart()
{
   int currentWeek = AF_DayOfYear(TimeCurrent()) / 7;

   if(currentWeek != StoredWeek)
   {
      WeekStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
      StoredWeek = currentWeek;
   }
}

//+------------------------------------------------------------------+
//| RECOVERY MODE 2 FUNCTIONS (CASCADE)                              |
//|                                                                  |
//| This is the martingale described in the header. Ported unchanged: |
//| same trigger distance, same linear lot growth, same 60-position   |
//| cap, and the added positions still carry no stop loss and no take |
//| profit, exactly as in the MQL4 original.                          |
//+------------------------------------------------------------------+

void CheckCloseSingleDealAtProfit()
{
   if(Recovery2SingleCloseProfit <= 0) return;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((long)PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;

      string comment = PositionGetString(POSITION_COMMENT);
      if(StringFind(comment, "Recovery_Trade") >= 0) continue;

      double profit = PositionNetProfit(ticket);

      if(profit >= Recovery2SingleCloseProfit)
      {
         Print("SINGLE DEAL PROFIT TARGET REACHED! Ticket: #", ticket, " Profit: $", profit);

         if(g_trade.PositionClose(ticket))
         {
            if(g_recovery2Active)
            {
               for(int j = 0; j < ArraySize(g_recovery2Tickets); j++)
               {
                  if(g_recovery2Tickets[j] == ticket)
                  {
                     g_recovery2Tickets[j] = 0;
                     g_recovery2Count--;
                     break;
                  }
               }
            }
         }
      }
   }
}

bool IsPriceLevelAlreadyHasTrade(double price, int radiusPips)
{
   double pipSize = GetPipSize();
   double radius = radiusPips * pipSize;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((long)PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;

      double existingPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      if(MathAbs(price - existingPrice) < radius)
         return true;
   }
   return false;
}

void AddRecovery2Trade(int direction, double baseLotSize, double targetPrice = 0)
{
   if(g_recovery2Count >= Recovery2MaxTrades)
   {
      Print("Recovery Mode 2: Max trades reached (", Recovery2MaxTrades, ")");
      return;
   }

   string comment = "Recovery2_Cascade";

   double linearLot = baseLotSize * g_recovery2Count;
   linearLot = NormalizeLotLinear(linearLot);

   Print("Linear lot calculation: Base=", baseLotSize, " Count=", g_recovery2Count, " Calculated=", linearLot);

   double entryPrice = (direction == 1) ? AF_Ask() : AF_Bid();
   entryPrice = NormalizeDouble(entryPrice, _Digits);

   if(IsPriceLevelAlreadyHasTrade(entryPrice, 10))
      return;

   // PORT: MQL4 used a per-order slippage argument; CTrade carries a deviation.
   g_trade.SetDeviationInPoints((ulong)MathMax(Slippage, 30));

   bool ok = false;
   if(direction == 1)
      ok = g_trade.Buy(linearLot, _Symbol, 0.0, 0.0, 0.0, comment);   // no SL, no TP, as the original
   else
      ok = g_trade.Sell(linearLot, _Symbol, 0.0, 0.0, 0.0, comment);

   g_trade.SetDeviationInPoints((ulong)MathMax(Slippage, 0));

   if(ok)
   {
      ulong ticket = g_trade.ResultOrder();
      int size = ArraySize(g_recovery2Tickets);
      for(int i = 0; i < size; i++)
      {
         if(g_recovery2Tickets[i] == 0)
         {
            g_recovery2Tickets[i] = ticket;
            break;
         }
      }
      g_recovery2Count++;
      g_recovery2LastAddedPrice = entryPrice;

      Print("Recovery Mode 2: Added trade #", g_recovery2Count);
      Print("   Ticket: ", ticket);
      Print("   Direction: ", (direction == 1 ? "BUY" : "SELL"));
      Print("   Entry: ", entryPrice);
      Print("   Lot: ", linearLot, " (Linear level ", g_recovery2Count, ")");
   }
   else
   {
      Print("Failed to add Recovery Mode 2 trade. Error: ", GetLastError());
   }
}

double GetRecovery2TotalProfit()
{
   double total = 0;

   for(int i = 0; i < ArraySize(g_recovery2Tickets); i++)
   {
      ulong ticket = g_recovery2Tickets[i];
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      total += PositionNetProfit(ticket);
   }

   return total;
}

void CloseAllRecovery2Trades()
{
   int closed = 0;
   double totalClosed = 0;

   for(int i = 0; i < ArraySize(g_recovery2Tickets); i++)
   {
      ulong ticket = g_recovery2Tickets[i];
      if(ticket == 0) continue;

      if(PositionSelectByTicket(ticket))
      {
         double profit = PositionNetProfit(ticket);

         if(g_trade.PositionClose(ticket))
         {
            closed++;
            totalClosed += profit;
            Print("Closed Recovery Mode 2 trade: Ticket=", ticket, " Profit=$", profit);
         }
      }

      g_recovery2Tickets[i] = 0;
   }

   g_recovery2Count = 0;
   Print("Recovery Mode 2: Closed ", closed, " trades. Total profit: $", totalClosed);
}

void ResetRecoveryMode2()
{
   g_recovery2Active = false;
   g_recovery2Count = 0;
   g_recovery2OriginalDirection = 0;
   g_recovery2OriginalLot = 0;
   g_recovery2LastCandleTime = 0;
   g_recovery2LastAddedLevel = 0;
   g_recovery2LastAddedPrice = 0;
   g_tpSlRemoved = false;

   ArrayResize(g_recovery2Tickets, 0);
}

//+------------------------------------------------------------------+
//| LICENSE FUNCTIONS - unchanged behaviour, MQL5 API                 |
//+------------------------------------------------------------------+

string LicenseCacheKey()
{
   return "AF_LIC_OK_" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN));
}

string UrlEncode(string str)
{
   string out = str;

   StringReplace(out, " ", "%20");
   StringReplace(out, "#", "%23");
   StringReplace(out, "&", "%26");
   StringReplace(out, "+", "%2B");

   return out;
}

bool HttpGet(const string fullUrl, string &response)
{
   char result[];
   char data[];
   string resultHeaders = "";

   ResetLastError();

   int code = WebRequest("GET", fullUrl, "", LicenseTimeoutMs, data, result, resultHeaders);

   if(code == -1)
   {
      int err = GetLastError();
      Print("License request failed. Error=", err);
      return false;
   }

   response = CharArrayToString(result);

   return (code >= 200 && code < 300);
}

int CheckAccountWithServer(string &respOut)
{
   string url = LicenseURL +
                "?acc=" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) +
                "&server=" + UrlEncode(AccountInfoString(ACCOUNT_SERVER)) +
                "&broker=" + UrlEncode(AccountInfoString(ACCOUNT_COMPANY)) +
                "&balance=" + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2);

   string resp = "";

   if(!HttpGet(url, resp))
   {
      respOut = "SERVER_UNREACHABLE";
      return -1;
   }

   // PORT: MQL5's StringTrimLeft/Right modify in place and return a count, so
   // they cannot be nested the way the MQL4 original nested them.
   StringTrimRight(resp);
   StringTrimLeft(resp);
   respOut = resp;

   if(resp == "OK")
   {
      Print("License VALID for account: ", AccountInfoInteger(ACCOUNT_LOGIN));
      return 1;
   }
   if(resp == "DENIED")
   {
      Print("License DENIED for account: ", AccountInfoInteger(ACCOUNT_LOGIN));
      return 0;
   }
   if(resp == "EXPIRED")
   {
      Print("License EXPIRED for account: ", AccountInfoInteger(ACCOUNT_LOGIN));
      return 0;
   }

   return 0;
}

bool GraceAllowed()
{
   if(GraceHours <= 0)
      return false;

   if(!GlobalVariableCheck(LicenseCacheKey()))
      return false;

   datetime lastOk = (datetime)GlobalVariableGet(LicenseCacheKey());
   int hours = (int)((TimeCurrent() - lastOk) / 3600);

   return (hours <= GraceHours);
}

//+------------------------------------------------------------------+
//| PENDING ORDERS                                                    |
//+------------------------------------------------------------------+

struct PendingOrderExpiry
{
   ulong    ticket;
   datetime expiryTime;
};

PendingOrderExpiry g_pendingExpiries[];

bool PlacePending(ENUM_ORDER_TYPE type, double entry, double sl, double tp, int expiryMinutes, string comment, color clr)
{
   entry = NormalizeDouble(entry, _Digits);
   sl    = NormalizeDouble(sl, _Digits);
   tp    = NormalizeDouble(tp, _Digits);

   double lot = GetTradeLot();

   // PORT: the original tracked expiry itself rather than using the broker's
   // order expiry, because MT4 needed that. Keeping the manual tracking means
   // this also works on MT5 brokers that reject ORDER_TIME_SPECIFIED.
   bool ok = false;
   if(type == ORDER_TYPE_BUY_STOP)
      ok = g_trade.BuyStop(lot, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, comment);
   else if(type == ORDER_TYPE_SELL_STOP)
      ok = g_trade.SellStop(lot, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, comment);

   if(!ok)
   {
      Print("OrderSend pending failed. Err=", GetLastError(), " type=", EnumToString(type), " entry=", entry);
      return false;
   }

   ulong ticket = g_trade.ResultOrder();

   if(expiryMinutes > 0 && ticket > 0)
   {
      int size = ArraySize(g_pendingExpiries);
      ArrayResize(g_pendingExpiries, size + 1);
      g_pendingExpiries[size].ticket = ticket;
      g_pendingExpiries[size].expiryTime = TimeCurrent() + expiryMinutes * 60;
      Print("Pending order #", ticket, " will expire at ", TimeToString(g_pendingExpiries[size].expiryTime));
   }

   return true;
}

// PORT: renamed from ArrayRemove, which is a built-in function name in MQL5.
void RemovePendingExpiryAt(int index, int count)
{
   int size = ArraySize(g_pendingExpiries);
   if(index < 0 || index >= size) return;

   for(int i = index; i < size - count; i++)
      g_pendingExpiries[i] = g_pendingExpiries[i + count];

   ArrayResize(g_pendingExpiries, size - count);
}

void CleanupExpiredPendingOrders()
{
   for(int i = ArraySize(g_pendingExpiries) - 1; i >= 0; i--)
   {
      if(TimeCurrent() >= g_pendingExpiries[i].expiryTime)
      {
         ulong ticket = g_pendingExpiries[i].ticket;
         if(OrderSelect(ticket))
         {
            if(g_trade.OrderDelete(ticket))
               Print("Deleted expired pending order #", ticket);
            else
               Print("Failed to delete expired pending order #", ticket, " Error: ", GetLastError());
         }
         RemovePendingExpiryAt(i, 1);
      }
   }
}

// Check Recovery Mode 2 (cascade adding - at candle open when the trigger distance is exceeded)
void CheckRecoveryMode2()
{
   if(!EnableRecoveryMode2) return;

   CheckCloseSingleDealAtProfit();

   bool   hasOpenTrade = false;
   ulong  firstTicket = 0;
   double firstEntry = 0;
   int    firstDirection = 0;
   double firstLot = 0;
   double currentPrice = 0;

   // PORT: MQL5 positions only - pending orders live in a separate list, so the
   // original's "skip pending orders" branch is no longer needed here.
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((long)PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;

      string comment = PositionGetString(POSITION_COMMENT);
      if(StringFind(comment, "Recovery_Trade") >= 0) continue;
      if(StringFind(comment, "Recovery2_Cascade") >= 0) continue;

      hasOpenTrade = true;
      firstTicket = ticket;
      firstEntry = PositionGetDouble(POSITION_PRICE_OPEN);
      firstDirection = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? 1 : -1;
      firstLot = PositionGetDouble(POSITION_VOLUME);
      break;
   }

   if(!hasOpenTrade)
   {
      if(g_recovery2Active)
      {
         Print("Recovery Mode 2: No open market trades, resetting");
         ResetRecoveryMode2();
      }
      return;
   }

   currentPrice = (firstDirection == 1) ? AF_Bid() : AF_Ask();

   double pipSize = GetPipSize();
   double lossDistancePips = 0;

   if(firstDirection == 1)
      lossDistancePips = (firstEntry - currentPrice) / pipSize;
   else
      lossDistancePips = (currentPrice - firstEntry) / pipSize;

   if(!g_recovery2Active)
   {
      if(lossDistancePips >= Recovery2TriggerPips)
      {
         g_recovery2Active = true;
         g_recovery2OriginalDirection = firstDirection;
         g_recovery2OriginalLot = firstLot;
         g_recovery2Count = 1;
         g_recovery2LastAddedLevel = (int)(lossDistancePips / Recovery2TriggerPips);
         g_recovery2LastAddedPrice = firstEntry;
         g_recovery2LastCandleTime = 0;

         ArrayResize(g_recovery2Tickets, Recovery2MaxTrades);
         for(int i = 0; i < Recovery2MaxTrades; i++)
            g_recovery2Tickets[i] = 0;
         g_recovery2Tickets[0] = firstTicket;

         Print("Recovery Mode 2 activated. Max trades: ", Recovery2MaxTrades,
               " | close target $", Recovery2CloseProfit,
               " | loss level ", g_recovery2LastAddedLevel);
      }
      else
      {
         return;
      }
   }

   if(!g_recovery2Active) return;

   if(lossDistancePips > 0)
   {
      int currentTriggerLevel = (int)(lossDistancePips / Recovery2TriggerPips);

      if(currentTriggerLevel > g_recovery2LastAddedLevel &&
         g_recovery2Count < Recovery2MaxTrades)
      {
         datetime currentCandleTime = AF_Time(0);

         if(currentCandleTime != g_recovery2LastCandleTime)
         {
            g_recovery2LastCandleTime = currentCandleTime;

            double targetPrice;
            if(firstDirection == 1)
               targetPrice = firstEntry - (currentTriggerLevel * Recovery2TriggerPips * pipSize);
            else
               targetPrice = firstEntry + (currentTriggerLevel * Recovery2TriggerPips * pipSize);

            if(IsPriceLevelAlreadyHasTrade(targetPrice, Recovery2TriggerPips))
            {
               Print("Recovery Mode 2: price level already has a trade within ", Recovery2TriggerPips, " pips");
               g_recovery2LastAddedLevel = currentTriggerLevel;
               return;
            }

            double candleOpenPrice = AF_Open(0);
            AddRecovery2Trade(firstDirection, g_recovery2OriginalLot, candleOpenPrice);
            g_recovery2LastAddedLevel = currentTriggerLevel;
         }
      }
   }

   double totalProfit = GetRecovery2TotalProfit();

   if(totalProfit >= Recovery2CloseProfit && g_recovery2Count > 0)
   {
      Print("RECOVERY MODE 2: BREAKEVEN TARGET REACHED. Total profit $", totalProfit,
            " (target $", Recovery2CloseProfit, "), trades ", g_recovery2Count);

      CloseAllRecovery2Trades();
      ResetRecoveryMode2();
   }
}

//+------------------------------------------------------------------+
//| MULTI-DEAL BREAKEVEN                                              |
//+------------------------------------------------------------------+
void CheckMultiDealBreakeven()
{
   if(!EnableMultiDealBreakeven) return;

   int totalDeals = 0;
   double totalProfit = 0;
   int buyCount = 0;
   int sellCount = 0;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((long)PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;

      totalDeals++;
      totalProfit += PositionNetProfit(ticket);

      if(PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) buyCount++;
      else sellCount++;
   }

   if(totalDeals > 1 && totalProfit >= 0.01)
   {
      Print("========================================");
      Print("MULTI-DEAL BREAKEVEN TRIGGERED!");
      Print("Total open deals: ", totalDeals, " (", buyCount, " BUY, ", sellCount, " SELL)");
      Print("Total profit: $", DoubleToString(totalProfit, 2));
      Print("========================================");

      int closedCount = 0;
      double totalClosed = 0;

      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong ticket = PositionGetTicket(i);
         if(ticket == 0) continue;
         if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
         if((long)PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;

         double profit = PositionNetProfit(ticket);

         if(g_trade.PositionClose(ticket))
         {
            closedCount++;
            totalClosed += profit;
            Print("Closed deal #", ticket, " | Profit: $", DoubleToString(profit, 2));
         }
         else
         {
            Print("Failed to close deal #", ticket, " Error: ", GetLastError());
         }
      }

      Print("MULTI-DEAL BREAKEVEN COMPLETED. Closed ", closedCount,
            " deals, total $", DoubleToString(totalClosed, 2));

      if(g_recovery2Active)
         ResetRecoveryMode2();

      return;
   }
}

double GetPipSize()
{
   if(_Digits == 5 || _Digits == 3)
      return _Point * 10;
   return _Point;
}

//+------------------------------------------------------------------+
//| CLOSE ALL DEALS AT FLOATING LOSS LIMIT                           |
//+------------------------------------------------------------------+
void CheckFloatingLossLimit()
{
   double lossLimit = FloatingLossCooldownTrigger;

   double totalFloatingLoss = 0;
   int totalDeals = 0;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((long)PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;

      totalDeals++;
      totalFloatingLoss += PositionNetProfit(ticket);
   }

   if(totalFloatingLoss <= lossLimit && totalDeals > 0)
   {
      int closedCount = 0;
      double totalClosed = 0;

      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong ticket = PositionGetTicket(i);
         if(ticket == 0) continue;
         if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
         if((long)PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;

         double profit = PositionNetProfit(ticket);

         if(g_trade.PositionClose(ticket))
         {
            closedCount++;
            totalClosed += profit;
            Print("Closed deal #", ticket, " | Loss: $", DoubleToString(profit, 2));
         }
         else
         {
            Print("Failed to close deal #", ticket, " Error: ", GetLastError());
         }
      }

      Print("FLOATING LOSS CLOSE COMPLETED. Closed ", closedCount,
            " deals, total $", DoubleToString(totalClosed, 2));

      if(g_recovery2Active)
         ResetRecoveryMode2();

      if(EnableFloatingLossCooldown && totalClosed <= FloatingLossCooldownTrigger)
      {
         int cooldownSeconds = FloatingLossCooldownHours * 3600;
         g_floatingLossCooldownUntil = TimeCurrent() + cooldownSeconds;

         Print("FLOATING LOSS COOLDOWN ACTIVATED. Loss $", DoubleToString(totalClosed, 2),
               " | trigger $", DoubleToString(FloatingLossCooldownTrigger, 2),
               " | ", FloatingLossCooldownHours, "h, resuming ",
               TimeToString(g_floatingLossCooldownUntil));
      }
   }
}

//+------------------------------------------------------------------+
//| CLOSE TRADES OLDER THAN X DAYS                                   |
//+------------------------------------------------------------------+
void CheckMaxTradeDuration()
{
   if(!EnableMaxTradeDuration) return;
   if(MaxTradeDurationDays <= 0) return;

   datetime now = TimeCurrent();
   int maxDurationSeconds = MaxTradeDurationDays * 24 * 60 * 60;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((long)PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;

      datetime openTime = (datetime)PositionGetInteger(POSITION_TIME);
      int secondsOpen = (int)(now - openTime);

      if(secondsOpen >= maxDurationSeconds)
      {
         if(g_trade.PositionClose(ticket))
         {
            Print("Trade #", ticket, " closed due to max duration limit");

            if(g_recovery2Active)
               ResetRecoveryMode2();
         }
         else
         {
            Print("Failed to close trade #", ticket, " Error: ", GetLastError());
         }
      }
   }
}

//+------------------------------------------------------------------+
//| GET MA VALUE                                                     |
//| PORT: MQL4's iMA() returned a value; MQL5 needs a handle created  |
//| once and read with CopyBuffer. The applied-price constants also   |
//| shifted by one between the languages, so MAPrice is mapped.       |
//+------------------------------------------------------------------+
ENUM_MA_METHOD MaMethodFromName(string name)
{
   if(name == "EMA")  return MODE_EMA;
   if(name == "SMMA") return MODE_SMMA;
   if(name == "LWMA") return MODE_LWMA;
   return MODE_SMA;
}

ENUM_APPLIED_PRICE MaAppliedPrice(int mql4Price)
{
   switch(mql4Price)
   {
      case 0:  return PRICE_CLOSE;     // MQL4 PRICE_CLOSE == 0, MQL5 PRICE_CLOSE == 1
      case 1:  return PRICE_OPEN;
      case 2:  return PRICE_HIGH;
      case 3:  return PRICE_LOW;
      case 4:  return PRICE_MEDIAN;
      case 5:  return PRICE_TYPICAL;
      case 6:  return PRICE_WEIGHTED;
      default: return PRICE_CLOSE;
   }
}

double GetMAValue()
{
   if(g_maHandle == INVALID_HANDLE) return 0.0;

   double buf[];
   if(CopyBuffer(g_maHandle, 0, 1, 1, buf) != 1) return 0.0;   // bar 1, as in the original
   return buf[0];
}

//+------------------------------------------------------------------+
//| MAIN MT5 EVENTS                                                  |
//+------------------------------------------------------------------+

int OnInit()
{
   g_lastBarTime = 0;
   g_barsSinceRedraw = 999999;

   g_cooldownUntil = 0;
   g_redrawAfterCooldown = false;
   g_lastProcessedCloseTime = 0;

   g_trade.SetExpertMagicNumber((ulong)MagicNumber);
   g_trade.SetDeviationInPoints((ulong)MathMax(Slippage, 0));
   g_trade.SetTypeFillingBySymbol(_Symbol);

   g_maHandle = iMA(_Symbol, _Period, MAPeriod, MAShift, MaMethodFromName(MAMethod), MaAppliedPrice(MAPrice));
   if(EnableMAFilter && g_maHandle == INVALID_HANDLE)
   {
      Print("Could not create the moving average handle. Err=", GetLastError());
      return(INIT_FAILED);
   }

   DrawAuthorLabel();

   g_currentLot = Lots;
   g_consecutiveWins = 0;
   g_consecutiveLosses = 0;

   ValidateSettings();

   if(g_invalidSettings)
   {
      Print("EA BLOCKED: ", g_invalidReason);
      return(INIT_FAILED);
   }

   Print("Starting license check...");

   string resp = "";
   int status = CheckAccountWithServer(resp);

   if(status == 1)
   {
      GlobalVariableSet(LicenseCacheKey(), (double)TimeCurrent());
      g_licenseValid = true;
      Comment("");
      Print("License OK. Account registered: ", AccountInfoInteger(ACCOUNT_LOGIN));
      return(INIT_SUCCEEDED);
   }

   if(status == 0)
   {
      g_licenseValid = false;
      if(resp == "EXPIRED")
      {
         Comment("EA EXPIRED\nAccount: ", AccountInfoInteger(ACCOUNT_LOGIN), "\nPlease renew.");
         Print("License EXPIRED for account: ", AccountInfoInteger(ACCOUNT_LOGIN));
      }
      else
      {
         Comment("EA NOT ACTIVATED\nAccount not registered: ", AccountInfoInteger(ACCOUNT_LOGIN),
                 "\nPlease contact support.");
         Print("License DENIED for account: ", AccountInfoInteger(ACCOUNT_LOGIN));
      }
      return(INIT_FAILED);
   }

   if(status == -1)
   {
      if(GraceAllowed())
      {
         Comment("License server unreachable.\nGrace active (", GraceHours, "h).\nAccount: ",
                 AccountInfoInteger(ACCOUNT_LOGIN));
         Print("Server unreachable. Grace active. Account: ", AccountInfoInteger(ACCOUNT_LOGIN));
         g_licenseValid = true;
         return(INIT_SUCCEEDED);
      }

      g_licenseValid = false;
      Comment("LICENSE CHECK FAILED\nServer unreachable / WebRequest blocked.\n",
              "Add URL in MT5:\nTools > Options > Expert Advisors > Allow WebRequest\n",
              "https://tradesmartfxtools.in\n",
              "Account: ", AccountInfoInteger(ACCOUNT_LOGIN));
      Print("Server unreachable and no grace available. Blocking EA.");
      return(INIT_FAILED);
   }

   return(INIT_SUCCEEDED);
}

void OnTick()
{
   // STEP 1: licence check first, exactly as the original
   if(TimeCurrent() - g_lastLicenseCheck >= 60)
   {
      g_lastLicenseCheck = TimeCurrent();

      string resp = "";
      int status = CheckAccountWithServer(resp);

      if(status == 1)
      {
         g_licenseValid = true;
         GlobalVariableSet(LicenseCacheKey(), (double)TimeCurrent());
      }
      else if(status == -1 && GraceAllowed())
      {
         g_licenseValid = true;
         Print("License server unreachable. Grace active (", GraceHours, "h)");
      }
      else
      {
         g_licenseValid = false;
         Print("License check failed. Account: ", AccountInfoInteger(ACCOUNT_LOGIN), " | Status: ", resp);
         DeleteMyPendingOrders();
      }
   }

   // STEP 2
   UpdateStatsPanel();

   // STEP 3
   if(!g_licenseValid)
   {
      Comment("LICENSE EXPIRED - EA BLOCKED");
      DeleteMyPendingOrders();
      return;
   }

   // STEP 4
   CheckMultiDealBreakeven();
   CheckFloatingLossLimit();
   CheckMaxTradeDuration();
   CleanupExpiredPendingOrders();

   if(!IsNewBar()) return;

   CheckRecoveryMode2();

   if(EnableFloatingLossCooldown && TimeCurrent() < g_floatingLossCooldownUntil)
   {
      DeleteMyPendingOrders();
      return;
   }

   if(TimeCurrent() < g_cooldownUntil)
      return;

   if(EnableSpreadFilter && !IsSpreadOK())
      DeleteMyPendingOrders();

   if(IsMonthBlocked())
      return;

   DrawSupplyDemandVisual();
   ValidateSettings();
   TrackWeekStart();
   CheckLastClosedTradeLoss();

   if(g_redrawAfterCooldown)
   {
      RebuildTrendlines();
      g_redrawAfterCooldown = false;
      g_barsSinceRedraw = 0;
   }

   g_barsSinceRedraw++;
   if(g_barsSinceRedraw >= StructureRefreshBars)
      RebuildTrendlines();

   if(ObjectFind(0, TL_HIGH) < 0 || ObjectFind(0, TL_LOW) < 0)
      RebuildTrendlines();

   UpdateLossRangeLock();
   TryEntries();
}

void OnDeinit(const int reason)
{
   Comment("");
   if(g_maHandle != INVALID_HANDLE)
      IndicatorRelease(g_maHandle);
   Print("Aurum Flow EA stopped. Reason: ", reason);
}
//+------------------------------------------------------------------+
