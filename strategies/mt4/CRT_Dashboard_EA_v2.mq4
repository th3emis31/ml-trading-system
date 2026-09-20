#property strict
#property version   "2.00"
#property description "CRT (Candle Range Theory) EA with dashboard for MT4 - v2"

// =====================================================================================
// CRT_Dashboard_EA v2 - research version. Separate file; v1 is untouched.
//
// NOTHING IS REMOVED. Every v1 input, feature and default still exists and still behaves
// exactly as before when the new switches are turned off. Three changes, each added
// beside the old behaviour rather than replacing it.
//
// WHY, measured on the owner's own broker candles, XAUUSD M15, locked holdout
// 2026-04-14 -> 2026-09-18, under the corrected cost model (2026-09-20):
//
//   v1 as coded         holdout 116 trades, PF 0.728, -3.12 %   <- loses
//   same entries, 2R    holdout  88 trades, PF 1.216, +5.56 %
//   same entries, 3R    holdout  82 trades, PF 1.381, +10.50 %
//
// The entries are not the problem. The exit is. Of 116 holdout trades, 91 were trailed
// out and 25 stopped:
//
//   25 hard stops  x -1.019 R = -25.47 R
//   91 trailed exits          = +14.35 R  ->  only +0.158 R each
//                               -11.12 R total
//
// To break even each trailed winner must return +0.280 R; it returns +0.158 R, 56 % of
// what is needed. The reason is scale: gold risk is about $20.42 per trade, while
// TrailStartPoints 300 = $3.00 = 0.15 R and TrailDistancePoints 180 = $1.80 = 0.09 R.
// A stop 0.09 R behind the high cannot survive ordinary noise, so every winner is cut to
// a fraction of R while every loser still pays a full R. That ratio is fixed by the
// inputs, not by the market, which is why no filter or timeframe ever rescued v1.
//
// CHANGE 1 - UseRBasedTrailing (default true). The trail starts at TrailStartR and
//            follows TrailDistanceR behind, both as multiples of the trade's own risk,
//            so it scales with the instrument instead of being a fixed cash distance.
//            Set it false and TrailStartPoints / TrailDistancePoints behave as in v1.
//
// CHANGE 2 - PersistInitialRisk (default true). Fixes a real bug in v1. v1 computed
//            "R" from the CURRENT stop:
//                double initialRiskPoints = MathAbs(openPrice - oldSL) / Point;
//            so as soon as break-even moved the stop, R collapsed from ~2042 points to
//            the lock distance of 15. Every R-based feature then silently rescaled: with
//            BreakEvenTriggerR 1.0 and PartialCloseAtR 2.0, the partial would fire at
//            about 0.015 R instead of 2 R. The defaults (both 1.0) hide it. v2 records
//            the true risk per ticket at open, in the same GlobalVariable style v1
//            already uses for its partial-close flag, and reads it back thereafter.
//            Set it false for v1's behaviour.
//            Known limit: a position already open when v2 first runs, whose stop has
//            ALREADY been moved, cannot have its original risk recovered - v2 adopts the
//            current distance and says so in the log.
//
// CHANGE 3 - UseRBasedBreakEvenLock (default true). Locks BreakEvenLockR of risk instead
//            of a fixed BreakEvenLockPoints, for the same scaling reason. v1's $0.15 lock
//            is 0.007 R on gold. Set it false for v1's behaviour.
//
// NOT CHANGED, deliberately: entries, the score, the session filter, MTF confirm, the
// regime engine, adaptive memory, risk sizing and every guard. Prior research found the
// score gate does nothing (every signal already scores 70+), that the M15 EMA50 filter's
// good holdout was regime luck and failed on older unseen bars, and that 4H/1d setups are
// too rare to trade (12-15 and 2-3 holdout trades). Those are not retried here.
//
// MEASURED RESULT of these changes (round 3, src/crt_lab.py, declared before it was run):
//
//                              XAUUSD M15 holdout        BTCUSD M15 holdout
//   V1 live exits              PF 0.728  -3.12 %         PF 0.420  -9.84 %
//   V2 break-even only         PF 1.201  +4.27 %         PF 1.233  +6.66 %
//   V2 default (BE + R-trail)  PF 1.074  +1.51 %         PF 1.078  +2.02 %
//   V2 R-trail only            PF 1.161  +3.82 %         PF 0.959  -1.98 %
//   V2 default at 3R           PF 1.016  +0.19 %         PF 0.964  -1.39 %
//
// Every v2 variant beats v1 on both markets, and the SAME variant is best on both, which is
// worth more than one market agreeing with itself. That is why the shipped default is
// break-even at 1 R locking 0.10 R with a 2 R target and the trail switched OFF.
//
// WHAT THIS IS NOT. Nothing here clears the owner's standing bar of deflated Sharpe 0.95: the
// best figure above is 0.352 on bitcoin and 0.286 on gold. On BITCOIN the search and validation
// windows LOSE heavily for every variant (PF 0.55-0.60, -27 % to -38 %), so nothing was
// selectable there and the positive holdout is one lucky window, not an edge - prior research
// had already concluded "stop CRT research on BTC 15m". On GOLD the M15 holdout has now been
// read across five rounds and is SPENT. The trail diagnosis stands on its own arithmetic, but
// these SETTINGS need FORWARD evidence before any money follows them. Not proven profitable.
// =====================================================================================

// ---------------------------- Inputs ----------------------------
input bool   EnableAutoTrading       = true;
input int    MagicNumber             = 260704;
input double RiskPercent             = 1.0;
input bool   UseFixedLotSize         = false;
input double FixedLotSize            = 0.01;
input bool   ManualOrderIgnoreGuards = false;
input double RiskReward              = 2.0;
input int    StopBufferPoints        = 50;
input int    MaxSpreadPoints         = 35;
input int    SlippagePoints          = 20;
input int    MaxOpenTradesPerSymbol  = 1;
input double MaxOpenRiskPercent      = 6.0;
input double MaxOpenRiskPercentXAU   = 5.0;
input double MaxOpenRiskPercentBTC   = 7.0;
input double MinSetupScore           = 62.0;
input double NearThresholdScoreWindow= 7.0;
input double StrongEntryMinScore     = 54.0;
input double MinBreakoutBodyPointsBypass = 90.0;
input int    FVGMinGapPoints         = 30;
input bool   EnableStatePersistence  = true;
input bool   UseSymbolPreset         = true;

input int    MinMiddleBars           = 1;
input int    MaxMiddleBars           = 4;
input double MinAnchorBodyPoints     = 120;
input double MiddleBodyMaxFactor     = 0.85;
input bool   RequireLiquiditySweep   = true;
input int    SweepMinPoints          = 20;
input int    ManipulationMaxPoints   = 120;

input bool   UseSessionFilter        = true;
input int    SessionStartHour        = 7;
input int    SessionEndHour          = 20;
input bool   UseATRStopBuffer        = true;
input int    ATRPeriod               = 14;
input double ATRMultiplier           = 0.80;
input int    CooldownBars            = 1;
input double DailyLossLimitPercent   = 4.0;
input bool   EnableBreakEven         = true;
input double BreakEvenTriggerR       = 1.0;
input int    BreakEvenLockPoints     = 15;
// DEFAULT CHANGED IN v2, from true. The trail is kept in full - both the v1 point form and the
// new R form below - but it is OFF by default, because measurement said so on BOTH markets.
// Holdout, corrected costs, 2026-09-20, same entries throughout, only the exit differing:
//                            XAUUSD M15                 BTCUSD M15
//   v1 trail (300/180 pts)   PF 0.728  -3.12 %          PF 0.420  -9.84 %   (91/116 and 108/128 trailed out)
//   R-trail 1.5R / 1.0R      PF 1.161  +3.82 %          PF 0.959  -1.98 %
//   break-even only, no trail PF 1.201 +4.27 %          PF 1.233  +6.66 %   <- best on both
//   break-even + R-trail     PF 1.074  +1.51 %          PF 1.078  +2.02 %
// So rescaling the trail helps, but REMOVING it helps more, on both markets independently. Set
// EnableTrailingStop true to get it back; with UseRBasedTrailing it then scales with risk.
input bool   EnableTrailingStop      = false;
input int    TrailStartPoints        = 300;
input int    TrailDistancePoints     = 180;
// --- v2 additions. With all three false the expert behaves exactly as v1. ---
input bool   UseRBasedTrailing       = true;   // trail in multiples of the trade's risk, not fixed points
input double TrailStartR             = 1.5;    // start trailing once this much R is in profit
input double TrailDistanceR          = 1.0;    // and follow this much R behind the best price
input bool   PersistInitialRisk      = true;   // remember the true risk at open, so R cannot collapse
input bool   UseRBasedBreakEvenLock  = true;   // lock a fraction of R at break-even, not fixed points
input double BreakEvenLockR          = 0.10;   // how much R to lock when break-even triggers
input bool   EnableEquityProtector   = true;
input double MaxEquityDrawdownPct    = 8.0;
input int    MaxConsecutiveLosses    = 3;
input bool   AutoResumeRiskPause     = true;
input int    AutoResumePauseHours    = 8;
input double EquityResumeDrawdownPct = 5.5;
input bool   UseNewsBlackout         = false;
input string NewsBlackoutWindows     = "13:25-13:40;15:55-16:10";
input bool   UseMTFConfirm           = true;
input bool   EnforceExecutionTF      = true;
input int    ExecutionTF             = PERIOD_M15;
input int    HTFBiasTF               = PERIOD_H4;
input double MTFSoftBufferATR        = 0.12;
input int    MTFTrendTF              = PERIOD_H1;
input int    MTFMaPeriod             = 50;
input int    MTFMaMethod             = MODE_EMA;
input int    MTFMaPrice              = PRICE_CLOSE;
input int    MaxOrderSendRetries     = 2;
input bool   EnablePartialClose      = true;
input double PartialCloseAtR         = 1.0;
input double PartialClosePercent     = 50.0;
input bool   EnableRegimeEngine      = true;
input int    RegimeAdxPeriod         = 14;
input double RegimeAdxTrendMin       = 23.0;
input int    RegimeAtrLookbackBars   = 60;
input double RegimeVolatilityHighFactor = 1.35;
input double RegimeVolatilityLowFactor  = 0.85;
input bool   EnableProfitModeControl = true;
input bool   EnableAdaptiveTradeMemory = true;
input int    AdaptiveLookbackTrades  = 30;
input int    AdaptiveMinTrades       = 12;
input double AdaptiveScoreStep       = 2.0;
input double AdaptiveScoreMaxShift   = 6.0;
input bool   EnableDynamicThreshold  = true;
input double DynamicThresholdMaxShift= 5.0;
input bool   EnableConfidenceLotSizing = true;
input double ConfidenceLotMinMul     = 0.70;
input double ConfidenceLotMaxMul     = 1.25;
input double ConfidenceScoreLow      = 55.0;
input double ConfidenceScoreHigh     = 78.0;
input bool   EnableSessionQualityMemory = true;
input int    SessionMemoryLookbackTrades = 80;
input int    SessionMemoryMinTrades  = 6;
input double SessionMemoryMinWinRate = 35.0;

input color  DashboardBgColor        = clrMidnightBlue;
input color  DashboardTextColor      = clrWhite;
input color  BuyColor                = clrLimeGreen;
input color  SellColor               = clrTomato;
input int    DashboardTheme          = 1;
input double DashboardScale          = 1.00;
input string DashboardFontName       = "Consolas";
input bool   ApplyChartColorTheme    = true;
input color  ChartBackgroundColor    = clrBlack;
input color  ChartGridColor          = clrDimGray;
input color  ChartForegroundColor    = clrSilver;
input color  ChartBullCandleColor    = clrLimeGreen;
input color  ChartBearCandleColor    = clrTomato;
input bool   ChartShowGrid           = true;

// ---------------------------- Globals ---------------------------
string g_prefix = "CRTDB_";
datetime g_lastBarTime = 0;
datetime g_lastTradeSignalTime = 0;
string g_lastSignal = "NONE";
string g_lastReason = "waiting";
string g_profileName = "CUSTOM";
double g_dayStartBalance = 0.0;
int g_dayOfYear = -1;
bool g_autoEnabledRuntime = true;
bool g_allowBuyRuntime = true;
bool g_allowSellRuntime = true;
bool g_riskPaused = false;
string g_riskPauseReason = "";
datetime g_riskPausedSince = 0;
double g_peakEquity = 0.0;
color g_uiBg = clrMidnightBlue;
color g_uiText = clrWhite;
color g_uiBorder = clrSlateGray;
color g_uiMuted = clrSilver;
int g_runtimeTheme = 1;
string g_runtimeThemeName = "Glass Dark";
double g_runtimeScale = 1.00;
string g_runtimeSizeName = "MEDIUM";
color g_chartBg = clrBlack;
color g_chartGrid = clrDimGray;
color g_chartFg = clrSilver;
color g_chartBull = clrLimeGreen;
color g_chartBear = clrTomato;
bool g_chartShowGrid = true;
bool g_showCRTZone = true;
bool g_showFVGZone = true;
bool g_showBreakoutZone = true;
bool g_dashboardVisible = true;
int g_dashboardMode = 0; // 0=PRO, 1=FOCUS
int g_regimeMode = 0; // 0=AUTO, 1=TREND, 2=RANGE, 3=VOL
int g_profitMode = 1; // 0=SAFE, 1=BALANCED, 2=AGGRESSIVE
string g_profitModeName = "BALANCED";
double g_runtimeRiskPercentMul = 1.00;
double g_runtimeMinScoreBias = 0.0;
double g_runtimeOpenRiskCapMul = 1.00;
double g_adaptiveScoreOffset = 0.0;
double g_sessionHourWinRate = 0.0;
int g_sessionHourTrades = 0;
string g_systemState = "IDLE";
string g_gateReasonCode = "INIT";
double g_opportunityScore = 0.0;
double g_liveMinScore = 0.0;
int g_candidateDirection = 0;
bool g_chkExecTf = false;
bool g_chkRisk = false;
bool g_chkSession = false;
bool g_chkSpread = false;
bool g_chkCapacity = false;
double g_dynamicThresholdOffset = 0.0;
double g_confidenceLotMultiplier = 1.0;

#define DECISION_LOG_CAP 20
string g_decisionLog[DECISION_LOG_CAP];
int g_decisionLogCount = 0;

int g_cfgStopBufferPoints = 0;
int g_cfgMaxSpreadPoints = 0;
int g_cfgSlippagePoints = 0;
int g_cfgMinMiddleBars = 0;
int g_cfgMaxMiddleBars = 0;
double g_cfgMinAnchorBodyPoints = 0.0;
double g_cfgMiddleBodyMaxFactor = 0.0;
bool g_cfgRequireLiquiditySweep = false;
int g_cfgSweepMinPoints = 0;
int g_cfgManipulationMaxPoints = 0;
double g_cfgRiskReward = 0.0;
// Position-management distances in points. They follow the symbol preset like
// the entry stop buffer does; on BTC the raw inputs (sized for gold) put a
// trailing stop a couple of dollars behind price, inside the spread.
int g_cfgBreakEvenLockPoints = 0;
int g_cfgTrailStartPoints = 0;
int g_cfgTrailDistancePoints = 0;

// ---------------------------- Helpers ---------------------------
double PipsToPrice(int points)
{
   return points * Point;
}

double CurrentSpreadPoints()
{
   return (Ask - Bid) / Point;
}

bool ContainsNoCase(string src, string token)
{
   if(StringLen(token) <= 0)
      return false;

   if(StringFind(src, token, 0) >= 0)
      return true;

   string s = src;
   string t = token;
   StringToUpper(s);
   StringToUpper(t);
   return (StringFind(s, t, 0) >= 0);
}

bool IsGoldLikeSymbol(string sym)
{
   return ContainsNoCase(sym, "XAU") || ContainsNoCase(sym, "GOLD");
}

bool IsBtcLikeSymbol(string sym)
{
   return ContainsNoCase(sym, "BTC") || ContainsNoCase(sym, "XBT");
}

int Ui(int px)
{
   double s = g_runtimeScale;
   if(s < 0.80)
      s = 0.80;
   if(s > 1.80)
      s = 1.80;
   return (int)MathMax(1, MathRound(px * s));
}

void ApplySizePreset(int sizeId)
{
   // 1=SMALL, 2=MEDIUM, 3=LARGE, 4=FULL
   if(sizeId == 1)
   {
      g_runtimeScale = 0.90;
      g_runtimeSizeName = "SMALL";
      return;
   }
   if(sizeId == 3)
   {
      g_runtimeScale = 1.20;
      g_runtimeSizeName = "LARGE";
      return;
   }
   if(sizeId == 4)
   {
      g_runtimeScale = 1.50;
      g_runtimeSizeName = "FULL";
      return;
   }

   g_runtimeScale = 1.00;
   g_runtimeSizeName = "MEDIUM";
}

void ApplyDashboardTheme()
{
   int theme = g_runtimeTheme;

   // Theme 0: legacy/custom, 1: glass dark, 2: high contrast.
   if(theme == 2)
   {
      g_runtimeThemeName = "High Contrast";
      g_uiBg = clrBlack;
      g_uiText = clrWhite;
      g_uiBorder = clrWhite;
      g_uiMuted = clrSilver;
      return;
   }

   if(theme == 1)
   {
      g_runtimeThemeName = "Glass Dark";
      g_uiBg = (color)0x2A1B10; // deep blue-tinted panel
      g_uiText = (color)0xE8F1FF;
      g_uiBorder = (color)0x5A4A30;
      g_uiMuted = (color)0xB8C7D9;
      return;
   }

   g_runtimeThemeName = "Classic";
   g_uiBg = DashboardBgColor;
   g_uiText = DashboardTextColor;
   g_uiBorder = clrSlateGray;
   g_uiMuted = clrSilver;
}

void ResetRuntimeVisualSettings()
{
   g_runtimeTheme = DashboardTheme;
   g_runtimeScale = DashboardScale;
   if(g_runtimeScale <= 0.95)
      g_runtimeSizeName = "SMALL";
   else if(g_runtimeScale >= 1.40)
      g_runtimeSizeName = "FULL";
   else if(g_runtimeScale >= 1.10)
      g_runtimeSizeName = "LARGE";
   else
      g_runtimeSizeName = "MEDIUM";
   g_chartBg = ChartBackgroundColor;
   g_chartGrid = ChartGridColor;
   g_chartFg = ChartForegroundColor;
   g_chartBull = ChartBullCandleColor;
   g_chartBear = ChartBearCandleColor;
   g_chartShowGrid = ChartShowGrid;
}

void ApplyRecommendedVisualProfile()
{
   string s = Symbol();
   if(IsGoldLikeSymbol(s))
   {
      ApplyVisualPreset(1);   // GOLD PRO
      ApplySizePreset(3);     // LARGE
      return;
   }
   if(IsBtcLikeSymbol(s))
   {
      ApplyVisualPreset(2);   // BTC NEON
      ApplySizePreset(3);     // LARGE
      return;
   }

   ApplyVisualPreset(3);      // MINIMAL CLEAN
   ApplySizePreset(2);        // MEDIUM
}

void ApplyChartVisualTheme(bool forceApply = false)
{
   if(!ApplyChartColorTheme && !forceApply)
      return;

   long chartId = 0;
   ChartSetInteger(chartId, CHART_COLOR_BACKGROUND, 0, g_chartBg);
   ChartSetInteger(chartId, CHART_COLOR_FOREGROUND, 0, g_chartFg);
   ChartSetInteger(chartId, CHART_COLOR_GRID, 0, g_chartGrid);
   ChartSetInteger(chartId, CHART_COLOR_CHART_UP, 0, g_chartBull);
   ChartSetInteger(chartId, CHART_COLOR_CHART_DOWN, 0, g_chartBear);
   ChartSetInteger(chartId, CHART_COLOR_CANDLE_BULL, 0, g_chartBull);
   ChartSetInteger(chartId, CHART_COLOR_CANDLE_BEAR, 0, g_chartBear);
   ChartSetInteger(chartId, CHART_SHOW_GRID, 0, g_chartShowGrid ? 1 : 0);
}

void ApplyVisualPreset(int presetId)
{
   // 1=Gold Pro, 2=BTC Neon, 3=Minimal Clean
   if(presetId == 1)
   {
      g_runtimeTheme = 1;
      g_runtimeThemeName = "Gold Pro";
      g_chartBg = (color)0x0E0A08;
      g_chartGrid = (color)0x2A2A2A;
      g_chartFg = (color)0xC8C8C8;
      g_chartBull = clrGold;
      g_chartBear = clrTomato;
      g_chartShowGrid = true;
   }
   else if(presetId == 2)
   {
      g_runtimeTheme = 2;
      g_runtimeThemeName = "BTC Neon";
      g_chartBg = (color)0x15100A;
      g_chartGrid = (color)0x2A2020;
      g_chartFg = (color)0xC0D8FF;
      g_chartBull = (color)0x00FF66;
      g_chartBear = (color)0xFF335A;
      g_chartShowGrid = true;
   }
   else
   {
      g_runtimeTheme = 0;
      g_runtimeThemeName = "Minimal Clean";
      g_chartBg = clrWhite;
      g_chartGrid = (color)0xDCDCDC;
      g_chartFg = clrBlack;
      g_chartBull = (color)0x00AA55;
      g_chartBear = (color)0xDD3344;
      g_chartShowGrid = false;
   }

   ApplyDashboardTheme();
   ApplyChartVisualTheme(true);
}

double BrokerMinStopDistancePrice()
{
   double stopLevelPoints = MarketInfo(Symbol(), MODE_STOPLEVEL);
   if(stopLevelPoints < 0)
      stopLevelPoints = 0;
   return stopLevelPoints * Point;
}

void EnforceBrokerStops(int direction, double entry, double &sl, double &tp)
{
   double minDist = BrokerMinStopDistancePrice();
   if(minDist <= 0)
      return;

   if(direction > 0)
   {
      if((entry - sl) < minDist)
         sl = entry - minDist;
      if((tp - entry) < minDist)
         tp = entry + minDist;
   }
   else
   {
      if((sl - entry) < minDist)
         sl = entry + minDist;
      if((entry - tp) < minDist)
         tp = entry - minDist;
   }
}

// --- v2: remember each ticket's ORIGINAL risk, so "R" cannot shrink when the stop moves. ---
// Same GlobalVariable convention the partial-close flag already uses, namespaced by magic
// number so it can never collide with another expert's variables.
string RiskTagName(int ticket)
{
   return "CRT_R_" + IntegerToString(MagicNumber) + "_" + IntegerToString(ticket);
}

void StoreInitialRisk(int ticket, double riskPoints)
{
   if(ticket <= 0 || riskPoints <= 0.0)
      return;
   GlobalVariableSet(RiskTagName(ticket), riskPoints);
}

// 0.0 when nothing was recorded for this ticket.
double StoredInitialRisk(int ticket)
{
   string name = RiskTagName(ticket);
   if(!GlobalVariableCheck(name))
      return 0.0;
   double v = GlobalVariableGet(name);
   return (v > 0.0) ? v : 0.0;
}

// Drop the tags of tickets that are no longer open, so the terminal's global variable list
// does not grow without limit. Only ever touches this expert's own prefix.
void PurgeClosedTradeTags()
{
   string prefixR = "CRT_R_" + IntegerToString(MagicNumber) + "_";
   string prefixP = "CRT_PC_" + IntegerToString(MagicNumber) + "_";
   int total = GlobalVariablesTotal();
   for(int i = total - 1; i >= 0; i--)
   {
      string name = GlobalVariableName(i);
      string tail = "";
      if(StringFind(name, prefixR) == 0)
         tail = StringSubstr(name, StringLen(prefixR));
      else if(StringFind(name, prefixP) == 0)
         tail = StringSubstr(name, StringLen(prefixP));
      else
         continue;

      int ticket = (int)StringToInteger(tail);
      if(ticket <= 0)
         continue;
      if(OrderSelect(ticket, SELECT_BY_TICKET) && OrderCloseTime() == 0)
         continue;              // still open, keep it
      GlobalVariableDel(name);
   }
}

string PartialFlagName(int ticket)
{
   return "CRT_PC_" + IntegerToString(MagicNumber) + "_" + IntegerToString(ticket);
}

bool IsPartialMarked(int ticket)
{
   return GlobalVariableCheck(PartialFlagName(ticket));
}

void MarkPartialDone(int ticket)
{
   GlobalVariableSet(PartialFlagName(ticket), TimeCurrent());
}

void LogDecision(string msg)
{
   string row = TimeToString(TimeCurrent(), TIME_SECONDS) + " | " + msg;
   if(g_decisionLogCount < DECISION_LOG_CAP)
   {
      g_decisionLog[g_decisionLogCount] = row;
      g_decisionLogCount++;
      return;
   }

   for(int i = 1; i < DECISION_LOG_CAP; i++)
      g_decisionLog[i - 1] = g_decisionLog[i];
   g_decisionLog[DECISION_LOG_CAP - 1] = row;
}

bool ParseHHMM(string hhmm, int &minutes)
{
   if(StringLen(hhmm) != 5)
      return false;
   if(StringSubstr(hhmm, 2, 1) != ":")
      return false;

   int hh = StrToInteger(StringSubstr(hhmm, 0, 2));
   int mm = StrToInteger(StringSubstr(hhmm, 3, 2));
   if(hh < 0 || hh > 23 || mm < 0 || mm > 59)
      return false;

   minutes = hh * 60 + mm;
   return true;
}

bool ParseWindowToken(string token, int &startMin, int &endMin)
{
   StringReplace(token, " ", "");
   int dash = StringFind(token, "-", 0);
   if(dash <= 0)
      return false;

   string s1 = StringSubstr(token, 0, dash);
   string s2 = StringSubstr(token, dash + 1);
   if(!ParseHHMM(s1, startMin))
      return false;
   if(!ParseHHMM(s2, endMin))
      return false;
   return true;
}

bool IsInsideNewsBlackout()
{
   if(!UseNewsBlackout)
      return false;
   if(StringLen(NewsBlackoutWindows) < 9)
      return false;

   int nowMin = TimeHour(TimeCurrent()) * 60 + TimeMinute(TimeCurrent());
   string all = NewsBlackoutWindows;

   while(true)
   {
      int semi = StringFind(all, ";", 0);
      string token = (semi >= 0) ? StringSubstr(all, 0, semi) : all;

      int startMin = 0;
      int endMin = 0;
      if(ParseWindowToken(token, startMin, endMin))
      {
         if(startMin <= endMin)
         {
            if(nowMin >= startMin && nowMin <= endMin)
               return true;
         }
         else
         {
            if(nowMin >= startMin || nowMin <= endMin)
               return true;
         }
      }

      if(semi < 0)
         break;
      all = StringSubstr(all, semi + 1);
   }

   return false;
}

string TfValueToText(int tf)
{
   switch(tf)
   {
      case PERIOD_M1:  return "M1";
      case PERIOD_M5:  return "M5";
      case PERIOD_M15: return "M15";
      case PERIOD_M30: return "M30";
      case PERIOD_H1:  return "H1";
      case PERIOD_H4:  return "H4";
      case PERIOD_D1:  return "D1";
      case PERIOD_W1:  return "W1";
      case PERIOD_MN1: return "MN1";
      default:         return IntegerToString(tf);
   }
}

bool ExecutionTfOk()
{
   if(!EnforceExecutionTF)
      return true;
   return Period() == ExecutionTF;
}

bool MTFTrendOk(int direction)
{
   if(!UseMTFConfirm)
      return true;

   int biasTf = (HTFBiasTF > 0) ? HTFBiasTF : MTFTrendTF;
   double ma = iMA(NULL, biasTf, MTFMaPeriod, 0, MTFMaMethod, MTFMaPrice, 1);
   double closeTf = iClose(NULL, biasTf, 1);
   if(ma <= 0 || closeTf <= 0)
      return false;

   double atrTf = iATR(NULL, biasTf, ATRPeriod, 1);
   double softBuf = MathMax(0.0, MTFSoftBufferATR) * atrTf;

   if(direction > 0)
      return closeTf > ma || (softBuf > 0.0 && (ma - closeTf) <= softBuf);
   if(direction < 0)
      return closeTf < ma || (softBuf > 0.0 && (closeTf - ma) <= softBuf);
   return false;
}

void SetRiskPause(string reason)
{
   if(!g_riskPaused)
      g_riskPausedSince = TimeCurrent();
   g_riskPaused = true;
   g_riskPauseReason = reason;
}

void ClearRiskPause(string resumeReason)
{
   g_riskPaused = false;
   g_riskPauseReason = "";
   g_riskPausedSince = 0;
   if(StringLen(resumeReason) > 0)
   {
      g_lastReason = resumeReason;
      LogDecision(g_lastReason);
   }
}

void UpdatePeakEquity()
{
   double eq = AccountEquity();
   if(g_peakEquity <= 0.0)
      g_peakEquity = eq;
   if(eq > g_peakEquity)
      g_peakEquity = eq;
}

bool EquityProtectorOk()
{
   if(!EnableEquityProtector)
      return true;
   UpdatePeakEquity();
   if(g_peakEquity <= 0.0)
      return true;

   double dd = ((g_peakEquity - AccountEquity()) / g_peakEquity) * 100.0;
   if(dd >= MaxEquityDrawdownPct)
   {
      SetRiskPause("equity DD limit");
      return false;
   }
   return true;
}

int CountConsecutiveLosses()
{
   int cnt = 0;
   for(int i = OrdersHistoryTotal() - 1; i >= 0; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_HISTORY))
         continue;
      if(OrderMagicNumber() != MagicNumber)
         continue;
      if(OrderSymbol() != Symbol())
         continue;

      int t = OrderType();
      if(t != OP_BUY && t != OP_SELL)
         continue;

      double p = OrderProfit() + OrderSwap() + OrderCommission();
      if(p < 0.0)
         cnt++;
      else
         break;
   }
   return cnt;
}

bool LossStreakOk()
{
   if(MaxConsecutiveLosses <= 0)
      return true;

   int streak = CountConsecutiveLosses();
   if(streak >= MaxConsecutiveLosses)
   {
      SetRiskPause("loss streak limit");
      return false;
   }
   return true;
}

void UpdateRiskPauseRecovery()
{
   if(!g_riskPaused)
      return;
   if(!AutoResumeRiskPause)
      return;

   // Keep operator-forced pause modes sticky until manual resume.
   if(g_riskPauseReason == "manual pause" || g_riskPauseReason == "emergency flat")
      return;

   bool canResume = false;

   if(g_riskPauseReason == "loss streak limit")
   {
      if(CountConsecutiveLosses() < MaxConsecutiveLosses)
         canResume = true;
   }
   else if(g_riskPauseReason == "equity DD limit")
   {
      UpdatePeakEquity();
      if(g_peakEquity > 0.0)
      {
         double dd = ((g_peakEquity - AccountEquity()) / g_peakEquity) * 100.0;
         if(dd <= EquityResumeDrawdownPct)
            canResume = true;
      }
   }

   if(!canResume && AutoResumePauseHours > 0 && g_riskPausedSince > 0)
   {
      if((TimeCurrent() - g_riskPausedSince) >= (AutoResumePauseHours * 3600))
      {
         if(DailyLossGuardOk())
            canResume = true;
      }
   }

   if(canResume)
      ClearRiskPause("risk auto-resumed");
}

void ComputePerformanceStats(int &closed, int &wins, double &net, double &grossProfit, double &grossLoss)
{
   closed = 0;
   wins = 0;
   net = 0.0;
   grossProfit = 0.0;
   grossLoss = 0.0;

   for(int i = 0; i < OrdersHistoryTotal(); i++)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_HISTORY))
         continue;
      if(OrderMagicNumber() != MagicNumber)
         continue;
      if(OrderSymbol() != Symbol())
         continue;

      int t = OrderType();
      if(t != OP_BUY && t != OP_SELL)
         continue;

      double p = OrderProfit() + OrderSwap() + OrderCommission();
      closed++;
      net += p;
      if(p >= 0.0)
      {
         wins++;
         grossProfit += p;
      }
      else
      {
         grossLoss += -p;
      }
   }
}

void AnalyzeRecentTradeMemory(double &winRate, double &net, int &trades)
{
   winRate = 0.0;
   net = 0.0;
   trades = 0;

   int lookback = MathMax(1, AdaptiveLookbackTrades);
   int wins = 0;
   for(int i = OrdersHistoryTotal() - 1; i >= 0 && trades < lookback; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_HISTORY))
         continue;
      if(OrderMagicNumber() != MagicNumber)
         continue;
      if(OrderSymbol() != Symbol())
         continue;

      int t = OrderType();
      if(t != OP_BUY && t != OP_SELL)
         continue;

      double p = OrderProfit() + OrderSwap() + OrderCommission();
      net += p;
      if(p > 0.0)
         wins++;
      trades++;
   }

   if(trades > 0)
      winRate = (100.0 * wins / trades);
}

double ComputeAdaptiveScoreOffset()
{
   if(!EnableAdaptiveTradeMemory)
      return 0.0;

   double winRate = 0.0;
   double net = 0.0;
   int trades = 0;
   AnalyzeRecentTradeMemory(winRate, net, trades);

   if(trades < MathMax(1, AdaptiveMinTrades))
      return 0.0;

   double shift = 0.0;
   if(winRate < 45.0 || net < 0.0)
      shift += AdaptiveScoreStep;
   if(winRate < 35.0 && net < 0.0)
      shift += AdaptiveScoreStep;
   if(winRate > 60.0 && net > 0.0)
      shift -= AdaptiveScoreStep;

   double maxShift = MathMax(0.0, AdaptiveScoreMaxShift);
   if(shift > maxShift)
      shift = maxShift;
   if(shift < -maxShift)
      shift = -maxShift;
   return shift;
}

bool SessionHourQualityOk(string &reason)
{
   reason = "";
   g_sessionHourTrades = 0;
   g_sessionHourWinRate = 0.0;

   if(g_profitMode == 2)
      return true;

   if(!EnableSessionQualityMemory)
      return true;

   int nowHour = TimeHour(TimeCurrent());
   int lookback = MathMax(1, SessionMemoryLookbackTrades);
   int wins = 0;

   for(int i = OrdersHistoryTotal() - 1; i >= 0 && g_sessionHourTrades < lookback; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_HISTORY))
         continue;
      if(OrderMagicNumber() != MagicNumber)
         continue;
      if(OrderSymbol() != Symbol())
         continue;

      int t = OrderType();
      if(t != OP_BUY && t != OP_SELL)
         continue;
      if(TimeHour(OrderCloseTime()) != nowHour)
         continue;

      double p = OrderProfit() + OrderSwap() + OrderCommission();
      if(p > 0.0)
         wins++;
      g_sessionHourTrades++;
   }

   if(g_sessionHourTrades < MathMax(1, SessionMemoryMinTrades))
      return true;

   g_sessionHourWinRate = 100.0 * wins / g_sessionHourTrades;
   if(g_sessionHourWinRate < SessionMemoryMinWinRate)
   {
      reason = "weak session hour";
      return false;
   }

   return true;
}

bool IsNewBar()
{
   if(Time[0] != g_lastBarTime)
   {
      g_lastBarTime = Time[0];
      return true;
   }
   return false;
}

bool IsSessionOpen()
{
   if(!UseSessionFilter)
      return true;

   int h = TimeHour(TimeCurrent());
   if(SessionStartHour <= SessionEndHour)
      return (h >= SessionStartHour && h < SessionEndHour);

   return (h >= SessionStartHour || h < SessionEndHour);
}

void ResetRuntimeConfig()
{
   g_cfgStopBufferPoints = StopBufferPoints;
   g_cfgMaxSpreadPoints = MaxSpreadPoints;
   g_cfgSlippagePoints = SlippagePoints;
   g_cfgMinMiddleBars = MinMiddleBars;
   g_cfgMaxMiddleBars = MaxMiddleBars;
   g_cfgMinAnchorBodyPoints = MinAnchorBodyPoints;
   g_cfgMiddleBodyMaxFactor = MiddleBodyMaxFactor;
   g_cfgRequireLiquiditySweep = RequireLiquiditySweep;
   g_cfgSweepMinPoints = SweepMinPoints;
   g_cfgManipulationMaxPoints = ManipulationMaxPoints;
   g_cfgRiskReward = RiskReward;
   g_cfgBreakEvenLockPoints = BreakEvenLockPoints;
   g_cfgTrailStartPoints = TrailStartPoints;
   g_cfgTrailDistancePoints = TrailDistancePoints;
   g_profileName = "CUSTOM";
}

void ApplySymbolPreset()
{
   ResetRuntimeConfig();
   if(!UseSymbolPreset)
      return;

   string sym = Symbol();

   // Gold preset tuned for XAUUSD-style volatility on intraday charts.
   if(IsGoldLikeSymbol(sym))
   {
      g_profileName = "XAU PRESET";
      g_cfgStopBufferPoints = MathMax(g_cfgStopBufferPoints, 120);
      g_cfgMaxSpreadPoints = MathMax(g_cfgMaxSpreadPoints, 70);
      g_cfgSlippagePoints = MathMax(g_cfgSlippagePoints, 30);
      g_cfgMinMiddleBars = 1;
      g_cfgMaxMiddleBars = 5;
      g_cfgMinAnchorBodyPoints = MathMax(g_cfgMinAnchorBodyPoints, 180);
      g_cfgMiddleBodyMaxFactor = 0.90;
      g_cfgRequireLiquiditySweep = true;
      g_cfgSweepMinPoints = MathMax(g_cfgSweepMinPoints, 35);
      g_cfgManipulationMaxPoints = MathMax(g_cfgManipulationMaxPoints, 300);
      g_cfgRiskReward = MathMax(g_cfgRiskReward, 2.0);
      return;
   }

   // BTC preset allows wider spread/volatility and larger manipulative legs.
   if(IsBtcLikeSymbol(sym))
   {
      g_profileName = "BTC PRESET";
      g_cfgStopBufferPoints = MathMax(g_cfgStopBufferPoints, 1500);
      g_cfgMaxSpreadPoints = MathMax(g_cfgMaxSpreadPoints, 3000);
      g_cfgSlippagePoints = MathMax(g_cfgSlippagePoints, 80);
      g_cfgMinMiddleBars = 1;
      g_cfgMaxMiddleBars = 6;
      g_cfgMinAnchorBodyPoints = MathMax(g_cfgMinAnchorBodyPoints, 2200);
      g_cfgMiddleBodyMaxFactor = 0.92;
      g_cfgRequireLiquiditySweep = true;
      g_cfgSweepMinPoints = MathMax(g_cfgSweepMinPoints, 700);
      g_cfgManipulationMaxPoints = MathMax(g_cfgManipulationMaxPoints, 8000);
      g_cfgRiskReward = MathMax(g_cfgRiskReward, 2.2);
      // Same 12.5x scale as the stop buffer (1500 vs gold's 120).
      g_cfgBreakEvenLockPoints = MathMax(g_cfgBreakEvenLockPoints, 190);
      g_cfgTrailStartPoints = MathMax(g_cfgTrailStartPoints, 3750);
      g_cfgTrailDistancePoints = MathMax(g_cfgTrailDistancePoints, 2250);
      return;
   }
}

void UpdateDayState()
{
   int today = TimeDayOfYear(TimeCurrent());
   if(today != g_dayOfYear)
   {
      g_dayOfYear = today;
      g_dayStartBalance = AccountBalance();

      // New day: release automatic pauses and re-evaluate with fresh conditions.
      if(g_riskPaused && g_riskPauseReason != "manual pause" && g_riskPauseReason != "emergency flat")
         ClearRiskPause("risk pause cleared on new day");
   }
}

bool DailyLossGuardOk()
{
   if(DailyLossLimitPercent <= 0.0)
      return true;

   UpdateDayState();
   if(g_dayStartBalance <= 0.0)
      return true;

   double dayLossPct = ((g_dayStartBalance - AccountEquity()) / g_dayStartBalance) * 100.0;
   return dayLossPct < DailyLossLimitPercent;
}

double CurrentDayLossPercent()
{
   UpdateDayState();
   if(g_dayStartBalance <= 0.0)
      return 0.0;
   return ((g_dayStartBalance - AccountEquity()) / g_dayStartBalance) * 100.0;
}

int EffectiveStopBufferPoints()
{
   int dynPoints = g_cfgStopBufferPoints;
   if(!UseATRStopBuffer)
      return MathMax(1, dynPoints);

   double atr = iATR(NULL, 0, ATRPeriod, 1);
   if(atr > 0)
   {
      int atrPoints = (int)MathRound((atr * ATRMultiplier) / Point);
      dynPoints = MathMax(dynPoints, atrPoints);
   }

   return MathMax(1, dynPoints);
}

bool CooldownOk()
{
   if(CooldownBars <= 0 || g_lastTradeSignalTime <= 0)
      return true;

   int barsSince = iBarShift(NULL, 0, g_lastTradeSignalTime, false);
   if(barsSince < 0)
      return true;

   return barsSince >= CooldownBars;
}

int BarsSinceLastTradeSignal()
{
   if(g_lastTradeSignalTime <= 0)
      return 999;

   int barsSince = iBarShift(NULL, 0, g_lastTradeSignalTime, false);
   if(barsSince < 0)
      return 999;
   return barsSince;
}

string DashboardGateState()
{
   if(!g_autoEnabledRuntime)
      return "BLOCKED: auto disabled";
   if(!ExecutionTfOk())
      return "BLOCKED: exec TF=" + TfValueToText(ExecutionTF);
   if(g_riskPaused)
      return "BLOCKED: risk paused";
   if(!DailyLossGuardOk())
      return "BLOCKED: daily loss guard";
   if(!EquityProtectorOk())
      return "BLOCKED: equity protector";
   if(!LossStreakOk())
      return "BLOCKED: loss streak";
   if(IsInsideNewsBlackout())
      return "BLOCKED: news blackout";
   if(!IsSessionOpen())
      return "BLOCKED: outside session";
   if(!CooldownOk())
      return "BLOCKED: cooldown";
   if(!SpreadOk())
      return "BLOCKED: spread";
   if(CurrentOpenRiskPercent() >= ActiveOpenRiskCapPercent())
      return "BLOCKED: open risk cap";
   if(CountOpenTradesForSymbol() >= MaxOpenTradesPerSymbol)
      return "BLOCKED: max open trades";
   return "READY";
}

string SystemStateText()
{
   return g_systemState + " / " + g_gateReasonCode;
}

double ComputeDynamicThresholdOffset()
{
   if(!EnableDynamicThreshold)
      return 0.0;

   double shift = 0.0;
   int mode = ActiveRegimeMode();
   if(mode == 2)
      shift += 1.2;
   else if(mode == 3)
      shift += (g_profitMode == 2) ? 0.5 : 2.0;

   if(g_cfgMaxSpreadPoints > 0)
   {
      double spreadRatio = CurrentSpreadPoints() / g_cfgMaxSpreadPoints;
      if(spreadRatio >= 0.85)
         shift += 1.5;
      else if(spreadRatio >= 0.70)
         shift += 0.8;
      else if(spreadRatio <= 0.45)
         shift -= 0.8;
   }

   double winRate = 0.0;
   double net = 0.0;
   int trades = 0;
   AnalyzeRecentTradeMemory(winRate, net, trades);
   if(trades >= MathMax(1, AdaptiveMinTrades))
   {
      if(winRate < 45.0 || net < 0.0)
         shift += 1.2;
      if(winRate < 35.0 && net < 0.0)
         shift += 1.0;
      if(winRate > 60.0 && net > 0.0)
         shift -= 0.9;
   }

   double maxShift = MathMax(0.0, DynamicThresholdMaxShift);
   if(shift > maxShift)
      shift = maxShift;
   if(shift < -maxShift)
      shift = -maxShift;
   return shift;
}

double ComputeConfidenceLotMultiplier(double signalScore)
{
   if(UseFixedLotSize || !EnableConfidenceLotSizing)
      return 1.0;

   double minMul = MathMax(0.30, ConfidenceLotMinMul);
   double maxMul = MathMax(minMul, ConfidenceLotMaxMul);
   double low = ConfidenceScoreLow;
   double high = MathMax(low + 1.0, ConfidenceScoreHigh);

   double mul = minMul;
   if(signalScore >= high)
      mul = maxMul;
   else if(signalScore > low)
      mul = minMul + ((signalScore - low) / (high - low)) * (maxMul - minMul);

   double winRate = 0.0;
   double net = 0.0;
   int trades = 0;
   AnalyzeRecentTradeMemory(winRate, net, trades);
   if(trades >= MathMax(1, AdaptiveMinTrades))
   {
      if(winRate < 45.0 || net < 0.0)
         mul *= 0.90;
      if(winRate > 60.0 && net > 0.0)
         mul *= 1.05;
   }

   int mode = ActiveRegimeMode();
   if(mode == 3 && g_profitMode != 2)
      mul *= 0.92;
   else if(mode == 1)
      mul *= 1.04;

   if(g_cfgMaxSpreadPoints > 0)
   {
      double spreadRatio = CurrentSpreadPoints() / g_cfgMaxSpreadPoints;
      if(spreadRatio > 0.80)
         mul *= 0.88;
      else if(spreadRatio < 0.45)
         mul *= 1.03;
   }

   if(mul < minMul)
      mul = minMul;
   if(mul > maxMul)
      mul = maxMul;
   return mul;
}

void UpdateSystemSnapshot()
{
   g_systemState = "SCAN";
   g_gateReasonCode = "READY";
   g_opportunityScore = 0.0;
   g_candidateDirection = 0;
   g_confidenceLotMultiplier = 1.0;
   g_liveMinScore = EffectiveMinSetupScore();

   g_chkExecTf = ExecutionTfOk();
   g_chkRisk = (!g_riskPaused && DailyLossGuardOk() && EquityProtectorOk() && LossStreakOk() && CurrentOpenRiskPercent() < ActiveOpenRiskCapPercent());
   g_chkSession = (!IsInsideNewsBlackout() && IsSessionOpen());
   g_chkSpread = SpreadOk();
   g_chkCapacity = (CountOpenTradesForSymbol() < MaxOpenTradesPerSymbol);

   if(!g_autoEnabledRuntime)
   {
      g_systemState = "LOCK";
      g_gateReasonCode = "BLK_AUTO";
      return;
   }
   if(!g_chkExecTf)
   {
      g_systemState = "LOCK";
      g_gateReasonCode = "BLK_EXEC_TF";
      return;
   }
   if(!g_chkRisk)
   {
      g_systemState = "LOCK";
      g_gateReasonCode = "BLK_RISK";
      return;
   }
   if(!g_chkSession)
   {
      g_systemState = "LOCK";
      g_gateReasonCode = "BLK_SESSION";
      return;
   }
   if(!CooldownOk())
   {
      g_systemState = "COOLDOWN";
      g_gateReasonCode = "BLK_COOLDOWN";
      return;
   }
   if(!g_chkSpread)
   {
      g_systemState = "LOCK";
      g_gateReasonCode = "BLK_SPREAD";
      return;
   }
   if(!g_chkCapacity)
   {
      g_systemState = "LOCK";
      g_gateReasonCode = "BLK_CAPACITY";
      return;
   }

   g_systemState = "QUALIFY";
   int direction = 0;
   double stopRefLow = 0.0;
   double stopRefHigh = 0.0;
   datetime signalTime = 0;
   int middleBars = 0;
   double signalScore = 0.0;

   if(!DetectCRTSignal(direction, stopRefLow, stopRefHigh, signalTime, middleBars, signalScore))
   {
      g_gateReasonCode = "NO_SETUP";
      g_systemState = "SCAN";
      return;
   }

   g_candidateDirection = direction;
   g_opportunityScore = signalScore;
   g_confidenceLotMultiplier = ComputeConfidenceLotMultiplier(signalScore);

   if(signalScore < g_liveMinScore && !AllowNearThresholdPass(signalScore, g_liveMinScore))
   {
      g_gateReasonCode = "BLK_SCORE";
      return;
   }

   if(!MTFTrendOk(direction))
   {
      g_gateReasonCode = "BLK_HTF";
      return;
   }

   string regimeReason = "";
   if(!RegimeGateOk(direction, signalScore, regimeReason))
   {
      g_gateReasonCode = "BLK_REGIME";
      return;
   }

   string sessionReason = "";
   if(!SessionHourQualityOk(sessionReason))
   {
      g_gateReasonCode = "BLK_HOUR_EDGE";
      return;
   }

   g_systemState = "READY";
   g_gateReasonCode = "READY";
}

double CurrentOpenRiskAmount()
{
   double totalRisk = 0.0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(OrderMagicNumber() != MagicNumber)
         continue;
      if(OrderSymbol() != Symbol())
         continue;

      int t = OrderType();
      if(t != OP_BUY && t != OP_SELL)
         continue;

      double sl = OrderStopLoss();
      if(sl <= 0.0)
         continue;

      double riskPoints = MathAbs(OrderOpenPrice() - sl) / Point;
      if(riskPoints <= 0.0)
         continue;

      double tickValue = MarketInfo(Symbol(), MODE_TICKVALUE);
      totalRisk += riskPoints * tickValue * OrderLots();
   }
   return totalRisk;
}

double CurrentOpenRiskPercent()
{
   double bal = AccountBalance();
   if(bal <= 0.0)
      return 0.0;
   return (CurrentOpenRiskAmount() / bal) * 100.0;
}

double CurrentMarginLevelPercent()
{
   double m = AccountMargin();
   if(m <= 0.0)
      return 0.0;
   return (AccountEquity() / m) * 100.0;
}

double ActiveOpenRiskCapPercent()
{
   string s = Symbol();
   double cap = MaxOpenRiskPercent;
   if(IsGoldLikeSymbol(s) && MaxOpenRiskPercentXAU > 0.0)
      cap = MaxOpenRiskPercentXAU;
   if(IsBtcLikeSymbol(s) && MaxOpenRiskPercentBTC > 0.0)
      cap = MaxOpenRiskPercentBTC;

   cap *= g_runtimeOpenRiskCapMul;
   if(cap < 1.0)
      cap = 1.0;
   return cap;
}

string RuntimeStateKey(string suffix)
{
   return "CRT_STATE_" + IntegerToString(MagicNumber) + "_" + Symbol() + "_" + suffix;
}

void SaveRuntimeState()
{
   if(!EnableStatePersistence)
      return;

   GlobalVariableSet(RuntimeStateKey("auto"), g_autoEnabledRuntime ? 1.0 : 0.0);
   GlobalVariableSet(RuntimeStateKey("buy"), g_allowBuyRuntime ? 1.0 : 0.0);
   GlobalVariableSet(RuntimeStateKey("sell"), g_allowSellRuntime ? 1.0 : 0.0);
   GlobalVariableSet(RuntimeStateKey("riskPaused"), g_riskPaused ? 1.0 : 0.0);
   GlobalVariableSet(RuntimeStateKey("riskPausedSince"), (double)g_riskPausedSince);
   GlobalVariableSet(RuntimeStateKey("theme"), g_runtimeTheme);
   GlobalVariableSet(RuntimeStateKey("scale"), g_runtimeScale);
   GlobalVariableSet(RuntimeStateKey("showCRT"), g_showCRTZone ? 1.0 : 0.0);
   GlobalVariableSet(RuntimeStateKey("showFVG"), g_showFVGZone ? 1.0 : 0.0);
   GlobalVariableSet(RuntimeStateKey("showBRK"), g_showBreakoutZone ? 1.0 : 0.0);
   GlobalVariableSet(RuntimeStateKey("showDash"), g_dashboardVisible ? 1.0 : 0.0);
   GlobalVariableSet(RuntimeStateKey("regimeMode"), g_regimeMode);
   GlobalVariableSet(RuntimeStateKey("profitMode"), g_profitMode);
}

void LoadRuntimeState()
{
   if(!EnableStatePersistence)
      return;

   if(GlobalVariableCheck(RuntimeStateKey("auto")))
      g_autoEnabledRuntime = (GlobalVariableGet(RuntimeStateKey("auto")) > 0.5);
   if(GlobalVariableCheck(RuntimeStateKey("buy")))
      g_allowBuyRuntime = (GlobalVariableGet(RuntimeStateKey("buy")) > 0.5);
   if(GlobalVariableCheck(RuntimeStateKey("sell")))
      g_allowSellRuntime = (GlobalVariableGet(RuntimeStateKey("sell")) > 0.5);
   if(GlobalVariableCheck(RuntimeStateKey("riskPaused")))
      g_riskPaused = (GlobalVariableGet(RuntimeStateKey("riskPaused")) > 0.5);
   if(GlobalVariableCheck(RuntimeStateKey("riskPausedSince")))
      g_riskPausedSince = (datetime)MathRound(GlobalVariableGet(RuntimeStateKey("riskPausedSince")));
   if(g_riskPaused && g_riskPausedSince <= 0)
      g_riskPausedSince = TimeCurrent();
   if(GlobalVariableCheck(RuntimeStateKey("theme")))
      g_runtimeTheme = (int)MathRound(GlobalVariableGet(RuntimeStateKey("theme")));
   if(GlobalVariableCheck(RuntimeStateKey("scale")))
      g_runtimeScale = GlobalVariableGet(RuntimeStateKey("scale"));
   if(GlobalVariableCheck(RuntimeStateKey("showCRT")))
      g_showCRTZone = (GlobalVariableGet(RuntimeStateKey("showCRT")) > 0.5);
   if(GlobalVariableCheck(RuntimeStateKey("showFVG")))
      g_showFVGZone = (GlobalVariableGet(RuntimeStateKey("showFVG")) > 0.5);
   if(GlobalVariableCheck(RuntimeStateKey("showBRK")))
      g_showBreakoutZone = (GlobalVariableGet(RuntimeStateKey("showBRK")) > 0.5);
   if(GlobalVariableCheck(RuntimeStateKey("showDash")))
      g_dashboardVisible = (GlobalVariableGet(RuntimeStateKey("showDash")) > 0.5);
   if(GlobalVariableCheck(RuntimeStateKey("regimeMode")))
      g_regimeMode = (int)MathRound(GlobalVariableGet(RuntimeStateKey("regimeMode")));
   if(g_regimeMode < 0 || g_regimeMode > 3)
      g_regimeMode = 0;
   if(GlobalVariableCheck(RuntimeStateKey("profitMode")))
      g_profitMode = (int)MathRound(GlobalVariableGet(RuntimeStateKey("profitMode")));
   if(g_profitMode < 0 || g_profitMode > 2)
      g_profitMode = 1;
   ApplyProfitMode(g_profitMode);

   if(g_runtimeScale <= 0.95)
      g_runtimeSizeName = "SMALL";
   else if(g_runtimeScale >= 1.40)
      g_runtimeSizeName = "FULL";
   else if(g_runtimeScale >= 1.10)
      g_runtimeSizeName = "LARGE";
   else
      g_runtimeSizeName = "MEDIUM";
}

int CountOpenTradesForSymbol()
{
   int count = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(OrderMagicNumber() != MagicNumber)
         continue;
      if(OrderSymbol() != Symbol())
         continue;
      if(OrderType() == OP_BUY || OrderType() == OP_SELL)
         count++;
   }
   return count;
}

double FloatingPnLForSymbol()
{
   double pnl = 0.0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(OrderMagicNumber() != MagicNumber)
         continue;
      if(OrderSymbol() != Symbol())
         continue;
      if(OrderType() != OP_BUY && OrderType() != OP_SELL)
         continue;
      pnl += OrderProfit() + OrderSwap() + OrderCommission();
   }
   return pnl;
}

double NormalizeLot(double lots)
{
   double minLot = MarketInfo(Symbol(), MODE_MINLOT);
   double maxLot = MarketInfo(Symbol(), MODE_MAXLOT);
   double lotStep = MarketInfo(Symbol(), MODE_LOTSTEP);

   lots = MathMax(minLot, MathMin(maxLot, lots));
   if(lotStep > 0)
      lots = MathFloor(lots / lotStep) * lotStep;

   return NormalizeDouble(lots, 2);
}

double ComputeRiskLot(double entry, double stop, double signalScore)
{
   if(UseFixedLotSize)
   {
      double fixedLots = NormalizeLot(FixedLotSize);
      if(fixedLots <= 0.0)
      {
         g_lastReason = "invalid fixed lot";
         return 0.0;
      }
      return fixedLots;
   }

   double stopPoints = MathAbs(entry - stop) / Point;
   if(stopPoints <= 0)
      return 0.0;

   double riskMoney = AccountBalance() * (EffectiveRiskPercent() / 100.0);
   double tickValue = MarketInfo(Symbol(), MODE_TICKVALUE);
   if(tickValue <= 0)
      return 0.0;

   double lots = riskMoney / (stopPoints * tickValue);
   double confMul = ComputeConfidenceLotMultiplier(signalScore);
   g_confidenceLotMultiplier = confMul;
   lots *= confMul;
   return NormalizeLot(lots);
}

bool SpreadOk()
{
   double spread = (Ask - Bid) / Point;
   return spread <= g_cfgMaxSpreadPoints;
}

string RegimeModeText(int mode)
{
   if(mode == 1)
      return "TREND";
   if(mode == 2)
      return "RANGE";
   if(mode == 3)
      return "VOL";
   return "AUTO";
}

string ProfitModeText(int mode)
{
   if(mode == 0)
      return "SAFE";
   if(mode == 2)
      return "AGGRESSIVE";
   return "BALANCED";
}

void ApplyProfitMode(int mode)
{
   if(mode < 0)
      mode = 0;
   if(mode > 2)
      mode = 2;

   g_profitMode = mode;
   g_profitModeName = ProfitModeText(mode);

   // Dynamic tuning: safer mode filters harder and risks less; aggressive mode relaxes filters.
   if(mode == 0)
   {
      g_runtimeRiskPercentMul = 0.65;
      g_runtimeMinScoreBias = 4.0;
      g_runtimeOpenRiskCapMul = 0.80;
      return;
   }

   if(mode == 2)
   {
      g_runtimeRiskPercentMul = 1.35;
      g_runtimeMinScoreBias = IsBtcLikeSymbol(Symbol()) ? -8.0 : -5.0;
      g_runtimeOpenRiskCapMul = 1.25;
      return;
   }

   g_runtimeRiskPercentMul = 1.00;
   g_runtimeMinScoreBias = 0.0;
   g_runtimeOpenRiskCapMul = 1.00;
}

double EffectiveRiskPercent()
{
   double rp = RiskPercent * g_runtimeRiskPercentMul;
   if(rp < 0.10)
      rp = 0.10;
   if(rp > 3.00)
      rp = 3.00;
   return rp;
}

int DetectMarketRegime()
{
   int lookback = MathMax(10, RegimeAtrLookbackBars);
   int maxBars = MathMin(lookback, Bars - 5);
   if(maxBars <= 5)
      return 1;

   double atrNow = iATR(NULL, 0, ATRPeriod, 1) / Point;
   double atrSum = 0.0;
   int atrCount = 0;
   for(int i = 2; i < (2 + maxBars); i++)
   {
      double a = iATR(NULL, 0, ATRPeriod, i) / Point;
      if(a > 0.0)
      {
         atrSum += a;
         atrCount++;
      }
   }

   double atrAvg = (atrCount > 0) ? (atrSum / atrCount) : atrNow;
   double adx = iADX(NULL, 0, RegimeAdxPeriod, PRICE_CLOSE, MODE_MAIN, 1);

   if(atrAvg > 0.0 && atrNow >= (atrAvg * RegimeVolatilityHighFactor))
      return 3;
   if(adx >= RegimeAdxTrendMin)
      return 1;
   if(atrAvg > 0.0 && atrNow <= (atrAvg * RegimeVolatilityLowFactor) && adx < (RegimeAdxTrendMin * 0.80))
      return 2;

   return (adx >= (RegimeAdxTrendMin * 0.90)) ? 1 : 2;
}

int ActiveRegimeMode()
{
   if(!EnableRegimeEngine)
      return 0;
   if(g_regimeMode == 0)
      return DetectMarketRegime();
   return g_regimeMode;
}

bool RegimeGateOk(int direction, double signalScore, string &reason)
{
   reason = "";
   if(!EnableRegimeEngine)
      return true;

   int mode = ActiveRegimeMode();
   double spread = CurrentSpreadPoints();
   double effectiveMin = MinSetupScore;

   if(mode == 1)
   {
      if(!MTFTrendOk(direction))
      {
         reason = "regime trend filter";
         return false;
      }
      return true;
   }

   if(mode == 2)
   {
      double needScore = (g_profitMode == 2) ? effectiveMin : (effectiveMin + 2.0);
      if(signalScore < needScore)
      {
         reason = "regime range score";
         return false;
      }
      double spreadCap = (g_profitMode == 2) ? 1.00 : 0.90;
      if(spread > (g_cfgMaxSpreadPoints * spreadCap))
      {
         reason = "regime range spread";
         return false;
      }
      return true;
   }

   if(mode == 3)
   {
      double needScore = (g_profitMode == 2) ? (StrongEntryMinScore - 4.0) : StrongEntryMinScore;
      if(signalScore < needScore)
      {
         reason = "regime vol score";
         return false;
      }
      double spreadCap = (g_profitMode == 2) ? 0.90 : 0.75;
      if(spread > (g_cfgMaxSpreadPoints * spreadCap))
      {
         reason = "regime vol spread";
         return false;
      }
      return true;
   }

   return true;
}

bool MiddleCandleRulesOk(int anchorShift, bool bullish, bool &sweepSeen)
{
   double anchorHigh = High[anchorShift];
   double anchorLow = Low[anchorShift];
   double anchorBody = MathAbs(Open[anchorShift] - Close[anchorShift]);

   sweepSeen = false;

   for(int s = anchorShift - 1; s >= 2; s--)
   {
      double body = MathAbs(Open[s] - Close[s]);
      if(anchorBody > 0 && body > anchorBody * g_cfgMiddleBodyMaxFactor)
         return false;

      // Reject extreme manipulations beyond user tolerance.
      if(High[s] > anchorHigh + PipsToPrice(g_cfgManipulationMaxPoints))
         return false;
      if(Low[s] < anchorLow - PipsToPrice(g_cfgManipulationMaxPoints))
         return false;

      // Keep middle closes inside anchor range to model accumulation/compression.
      if(Close[s] > anchorHigh || Close[s] < anchorLow)
         return false;

      if(bullish)
      {
         if(Low[s] < anchorLow - PipsToPrice(g_cfgSweepMinPoints))
            sweepSeen = true;
      }
      else
      {
         if(High[s] > anchorHigh + PipsToPrice(g_cfgSweepMinPoints))
            sweepSeen = true;
      }
   }

   return true;
}

double CalculateSetupScore(bool bullish, double anchorHigh, double anchorLow, int anchorShift, int breakoutShift, bool sweepSeen, int middleBars)
{
   double score = 45.0;
   double anchorBodyPts = MathAbs(Close[anchorShift] - Open[anchorShift]) / Point;
   double breakoutPts = bullish
      ? ((Close[breakoutShift] - anchorHigh) / Point)
      : ((anchorLow - Close[breakoutShift]) / Point);

   if(anchorBodyPts > 0.0)
      score += MathMin(18.0, MathMax(0.0, breakoutPts / anchorBodyPts) * 18.0);

   score += MathMin(12.0, (anchorBodyPts / MathMax(1.0, g_cfgMinAnchorBodyPoints)) * 12.0);

   if(sweepSeen)
      score += 10.0;
   if(middleBars >= 1 && middleBars <= 4)
      score += 8.0;
   else
      score += 4.0;

   if(score < 0.0)
      score = 0.0;
   if(score > 100.0)
      score = 100.0;
   return score;
}

bool DetectCRTSignal(int &direction, double &stopRefLow, double &stopRefHigh, datetime &signalTime, int &middleBars, double &signalScore)
{
   direction = 0;
   stopRefLow = 0;
   stopRefHigh = 0;
   signalTime = 0;
   middleBars = 0;
   signalScore = 0.0;

   double bestScore = -1.0;
   int bestDirection = 0;
   double bestStopRefLow = 0.0;
   double bestStopRefHigh = 0.0;
   datetime bestSignalTime = 0;
   int bestMiddleBars = 0;

   int maxShift = MathMax(2 + g_cfgMinMiddleBars, 2 + g_cfgMaxMiddleBars);
   if(Bars <= maxShift + 5)
      return false;

   int breakoutShift = 1; // last closed bar
   double minAnchorPoints = g_cfgMinAnchorBodyPoints;
   bool requireSweep = g_cfgRequireLiquiditySweep;
   if(g_profitMode == 2)
   {
      minAnchorPoints *= IsBtcLikeSymbol(Symbol()) ? 0.65 : 0.75;
      requireSweep = false;
   }

   for(int anchorShift = 2 + g_cfgMinMiddleBars; anchorShift <= 2 + g_cfgMaxMiddleBars; anchorShift++)
   {
      if(anchorShift >= Bars - 1)
         continue;

      middleBars = anchorShift - 2;

      bool anchorBull = Close[anchorShift] > Open[anchorShift];
      bool anchorBear = Close[anchorShift] < Open[anchorShift];
      double anchorBodyPoints = MathAbs(Close[anchorShift] - Open[anchorShift]) / Point;
      if(anchorBodyPoints < minAnchorPoints)
         continue;

      double anchorHigh = High[anchorShift];
      double anchorLow = Low[anchorShift];

      bool sweepSeen = false;

      // Bullish CRT: bearish anchor -> compression/manipulation -> bullish breakout above anchor high.
      if(anchorBear && Close[breakoutShift] > Open[breakoutShift] && Close[breakoutShift] > anchorHigh)
      {
         if(MiddleCandleRulesOk(anchorShift, true, sweepSeen))
         {
            if(!requireSweep || sweepSeen || Low[breakoutShift] < anchorLow - PipsToPrice(g_cfgSweepMinPoints))
            {
               double s = CalculateSetupScore(true, anchorHigh, anchorLow, anchorShift, breakoutShift, sweepSeen, middleBars);
               if(s > bestScore)
               {
                  bestScore = s;
                  bestDirection = 1;
                  bestStopRefLow = MathMin(anchorLow, Low[breakoutShift]);
                  bestStopRefHigh = anchorHigh;
                  bestSignalTime = Time[breakoutShift];
                  bestMiddleBars = middleBars;
               }
            }
         }
      }

      // Bearish CRT: bullish anchor -> compression/manipulation -> bearish breakout below anchor low.
      if(anchorBull && Close[breakoutShift] < Open[breakoutShift] && Close[breakoutShift] < anchorLow)
      {
         if(MiddleCandleRulesOk(anchorShift, false, sweepSeen))
         {
            if(!requireSweep || sweepSeen || High[breakoutShift] > anchorHigh + PipsToPrice(g_cfgSweepMinPoints))
            {
               double s = CalculateSetupScore(false, anchorHigh, anchorLow, anchorShift, breakoutShift, sweepSeen, middleBars);
               if(s > bestScore)
               {
                  bestScore = s;
                  bestDirection = -1;
                  bestStopRefHigh = MathMax(anchorHigh, High[breakoutShift]);
                  bestStopRefLow = anchorLow;
                  bestSignalTime = Time[breakoutShift];
                  bestMiddleBars = middleBars;
               }
            }
         }
      }
   }

   if(bestScore < 0.0)
      return false;

   direction = bestDirection;
   stopRefLow = bestStopRefLow;
   stopRefHigh = bestStopRefHigh;
   signalTime = bestSignalTime;
   middleBars = bestMiddleBars;
   signalScore = bestScore;
   return true;
}

double EffectiveMinSetupScore()
{
   g_adaptiveScoreOffset = ComputeAdaptiveScoreOffset();
   g_dynamicThresholdOffset = ComputeDynamicThresholdOffset();
   double effectiveMin = MinSetupScore + g_runtimeMinScoreBias + g_adaptiveScoreOffset + g_dynamicThresholdOffset;
   double spread = CurrentSpreadPoints();

   if(g_cfgMaxSpreadPoints > 0)
   {
      double spreadRatio = spread / g_cfgMaxSpreadPoints;
      if(spreadRatio <= 0.45)
         effectiveMin -= 3.0;
      else if(spreadRatio <= 0.65)
         effectiveMin -= 2.0;
      else if(spreadRatio <= 0.85)
         effectiveMin -= 1.0;
   }

   if(g_cfgRequireLiquiditySweep)
      effectiveMin -= 1.0;

   if(effectiveMin < 45.0)
      effectiveMin = 45.0;

   return effectiveMin;
}

bool AllowNearThresholdPass(double signalScore, double effectiveMinScore)
{
   double bypassFloor = MathMax(46.0, effectiveMinScore - NearThresholdScoreWindow);
   if(signalScore < bypassFloor)
      return false;
   if(signalScore < StrongEntryMinScore)
      return false;

   // Only bypass when breakout structure is strong and costs are still favorable.
   double breakoutBodyPoints = MathAbs(Close[1] - Open[1]) / Point;
   if(breakoutBodyPoints < MinBreakoutBodyPointsBypass)
      return false;

   if(g_cfgMaxSpreadPoints > 0 && CurrentSpreadPoints() > (g_cfgMaxSpreadPoints * 0.80))
      return false;

   return true;
}

bool PlaceTrade(int direction, double stopRefLow, double stopRefHigh, double signalScore)
{
   RefreshRates();

   double entry = (direction > 0) ? Ask : Bid;
   double sl = 0.0;
   double tp = 0.0;
   int stopBufferPoints = EffectiveStopBufferPoints();

   if(direction > 0)
   {
      sl = stopRefLow - PipsToPrice(stopBufferPoints);
      double risk = entry - sl;
      if(risk <= 0)
         return false;
      tp = entry + (risk * g_cfgRiskReward);
   }
   else
   {
      sl = stopRefHigh + PipsToPrice(stopBufferPoints);
      double risk = sl - entry;
      if(risk <= 0)
         return false;
      tp = entry - (risk * g_cfgRiskReward);
   }

   EnforceBrokerStops(direction, entry, sl, tp);

   double lots = ComputeRiskLot(entry, sl, signalScore);
   if(lots <= 0)
   {
      g_lastReason = "lot calculation failed";
      return false;
   }

   int type = (direction > 0) ? OP_BUY : OP_SELL;
   color arrow = (direction > 0) ? BuyColor : SellColor;

   int maxTries = MathMax(1, MaxOrderSendRetries + 1);
   int ticket = -1;
   int lastErr = 0;
   for(int attempt = 1; attempt <= maxTries; attempt++)
   {
      RefreshRates();
      entry = (direction > 0) ? Ask : Bid;
      EnforceBrokerStops(direction, entry, sl, tp);
      ticket = OrderSend(Symbol(), type, lots, entry, g_cfgSlippagePoints, sl, tp, "CRT EA", MagicNumber, 0, arrow);
      if(ticket >= 0)
         break;
      lastErr = GetLastError();
      g_lastReason = "OrderSend retry " + IntegerToString(attempt) + "/" + IntegerToString(maxTries) + " err " + IntegerToString(lastErr);
      if(attempt < maxTries)
         Sleep(250);
   }

   if(ticket < 0)
   {
      g_lastReason = "OrderSend failed err " + IntegerToString(lastErr);
      return false;
   }

   // v2: record the risk this trade was actually opened with, before any stop management
   // can move the stop and make the distance unrecoverable.
   if(PersistInitialRisk && OrderSelect(ticket, SELECT_BY_TICKET))
   {
      double openedRisk = MathAbs(OrderOpenPrice() - OrderStopLoss()) / Point;
      if(OrderStopLoss() > 0.0 && openedRisk > 0.0)
         StoreInitialRisk(ticket, openedRisk);
   }

   g_lastReason = "trade opened ticket " + IntegerToString(ticket);
   return true;
}

bool PlaceManualTrade(int direction)
{
   if(direction == 0)
      return false;

   // Manual entries always respect side locks.
   if(direction > 0 && !g_allowBuyRuntime)
   {
      g_lastReason = "manual blocked: buy side locked";
      LogDecision(g_lastReason);
      return false;
   }
   if(direction < 0 && !g_allowSellRuntime)
   {
      g_lastReason = "manual blocked: sell side locked";
      LogDecision(g_lastReason);
      return false;
   }

   // Optional override: allow manual entries without runtime guards.
   if(!ManualOrderIgnoreGuards && g_riskPaused)
   {
      g_lastReason = "manual blocked: risk paused";
      LogDecision(g_lastReason);
      return false;
   }
   if(!ManualOrderIgnoreGuards && (!EquityProtectorOk() || !LossStreakOk()))
   {
      g_lastReason = "manual blocked: risk protector";
      LogDecision(g_lastReason);
      return false;
   }
   if(!ManualOrderIgnoreGuards && CountOpenTradesForSymbol() >= MaxOpenTradesPerSymbol)
   {
      g_lastReason = "manual blocked: max open trades reached";
      LogDecision(g_lastReason);
      return false;
   }
   if(!ManualOrderIgnoreGuards && !SpreadOk())
   {
      g_lastReason = "manual blocked: spread too high";
      LogDecision(g_lastReason);
      return false;
   }

   double stopRefLow = Low[1];
   double stopRefHigh = High[1];
   for(int i = 2; i <= 10 && i < Bars; i++)
   {
      if(Low[i] < stopRefLow)
         stopRefLow = Low[i];
      if(High[i] > stopRefHigh)
         stopRefHigh = High[i];
   }

   bool ok = PlaceTrade(direction, stopRefLow, stopRefHigh, g_opportunityScore);
   if(ok)
   {
      g_lastSignal = (direction > 0) ? "MANUAL BUY" : "MANUAL SELL";
      LogDecision("manual trade opened: " + g_lastSignal);
   }
   else
   {
      LogDecision("manual trade failed: " + g_lastReason);
   }
   return ok;
}

int ResolveManualTradeDirection()
{
   int direction = 0;
   double stopRefLow = 0.0;
   double stopRefHigh = 0.0;
   datetime signalTime = 0;
   int middleBars = 0;
   double signalScore = 0.0;

   // Prefer current CRT direction when available.
   if(DetectCRTSignal(direction, stopRefLow, stopRefHigh, signalTime, middleBars, signalScore) && direction != 0)
      return direction;

   // Fallback: bias from HTF MA relationship.
   int biasTf = (HTFBiasTF > 0) ? HTFBiasTF : MTFTrendTF;
   double ma = iMA(NULL, biasTf, MTFMaPeriod, 0, MTFMaMethod, MTFMaPrice, 1);
   double closeTf = iClose(NULL, biasTf, 1);
   if(ma > 0.0 && closeTf > 0.0)
      return (closeTf >= ma) ? 1 : -1;

   return 1;
}

string TfToText()
{
   switch(Period())
   {
      case PERIOD_M1:  return "M1";
      case PERIOD_M5:  return "M5";
      case PERIOD_M15: return "M15";
      case PERIOD_M30: return "M30";
      case PERIOD_H1:  return "H1";
      case PERIOD_H4:  return "H4";
      case PERIOD_D1:  return "D1";
      default:         return IntegerToString(Period());
   }
}

void CreateOrUpdateButton(string name, string text, int x, int y, int w, int h, color bg, color fg)
{
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_BUTTON, 0, 0, 0);

   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, Ui(x));
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, Ui(y));
   ObjectSetInteger(0, name, OBJPROP_XSIZE, Ui(w));
   ObjectSetInteger(0, name, OBJPROP_YSIZE, Ui(h));
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, bg);
   ObjectSetInteger(0, name, OBJPROP_COLOR, fg);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, Ui(8));
   ObjectSetString(0, name, OBJPROP_FONT, DashboardFontName);
}

void CreateOrUpdateCard(string name, int x, int y, int w, int h, color bg, color border)
{
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_RECTANGLE_LABEL, 0, 0, 0);

   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, Ui(x));
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, Ui(y));
   ObjectSetInteger(0, name, OBJPROP_XSIZE, Ui(w));
   ObjectSetInteger(0, name, OBJPROP_YSIZE, Ui(h));
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, bg);
   ObjectSetInteger(0, name, OBJPROP_COLOR, border);
   ObjectSetInteger(0, name, OBJPROP_BACK, false);
}

void CloseAllSymbolTrades()
{
   RefreshRates();
   int closed = 0;

   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(OrderMagicNumber() != MagicNumber)
         continue;
      if(OrderSymbol() != Symbol())
         continue;

      int t = OrderType();
      if(t != OP_BUY && t != OP_SELL)
         continue;

      double price = (t == OP_BUY) ? Bid : Ask;
      if(OrderClose(OrderTicket(), OrderLots(), price, g_cfgSlippagePoints, clrSilver))
         closed++;
   }

   if(closed > 0)
      g_lastReason = "manual close all: " + IntegerToString(closed) + " positions";
   else
      g_lastReason = "manual close all: no positions";
}

void ManageOpenPositions()
{
   if(!EnableBreakEven && !EnableTrailingStop && !EnablePartialClose)
      return;

   RefreshRates();

   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(OrderMagicNumber() != MagicNumber)
         continue;
      if(OrderSymbol() != Symbol())
         continue;

      int t = OrderType();
      if(t != OP_BUY && t != OP_SELL)
         continue;

      double openPrice = OrderOpenPrice();
      double oldSL = OrderStopLoss();
      double tp = OrderTakeProfit();
      if(oldSL <= 0.0)
         continue;

      // v1 read R off the CURRENT stop, so R collapsed the moment break-even moved it.
      // v2 prefers the risk recorded at open. Nothing changes when PersistInitialRisk is false.
      double initialRiskPoints = MathAbs(openPrice - oldSL) / Point;
      if(initialRiskPoints <= 0.0)
         continue;

      double riskPoints = initialRiskPoints;
      if(PersistInitialRisk)
      {
         double storedRisk = StoredInitialRisk(OrderTicket());
         if(storedRisk > 0.0)
         {
            riskPoints = storedRisk;
         }
         else
         {
            // No record: a position opened by v1, by hand, or before this build. Adopt the
            // current distance and say so, because if the stop has already been moved this
            // understates the true risk and cannot be recovered from the terminal.
            StoreInitialRisk(OrderTicket(), initialRiskPoints);
            LogDecision("adopted current stop distance as R for ticket "
                        + IntegerToString(OrderTicket()) + " (no record at open)");
         }
      }

      double profitPoints = 0.0;
      if(t == OP_BUY)
         profitPoints = (Bid - openPrice) / Point;
      else
         profitPoints = (openPrice - Ask) / Point;

      double targetSL = oldSL;

      if(EnablePartialClose && !IsPartialMarked(OrderTicket()) && PartialClosePercent > 0.0)
      {
         double rReached = riskPoints * PartialCloseAtR;
         if(profitPoints >= rReached)
         {
            double minLot = MarketInfo(Symbol(), MODE_MINLOT);
            double closeLots = NormalizeLot(OrderLots() * (PartialClosePercent / 100.0));
            double remainLots = OrderLots() - closeLots;

            if(closeLots >= minLot && remainLots >= minLot)
            {
               double closePrice = (t == OP_BUY) ? Bid : Ask;
               if(OrderClose(OrderTicket(), closeLots, closePrice, g_cfgSlippagePoints, clrSilver))
               {
                  MarkPartialDone(OrderTicket());
                  g_lastReason = "partial close done ticket " + IntegerToString(OrderTicket());
                  LogDecision(g_lastReason);
               }
            }
            else
            {
               MarkPartialDone(OrderTicket());
            }
         }
      }

      if(EnableBreakEven && profitPoints >= (riskPoints * BreakEvenTriggerR))
      {
         // v2: lock a fraction of R rather than a fixed cash distance. v1's 15 points is
         // $0.15, which is 0.007 R on gold - effectively no protection at all.
         double lockPoints = (double)g_cfgBreakEvenLockPoints;
         if(UseRBasedBreakEvenLock && BreakEvenLockR > 0.0)
            lockPoints = riskPoints * BreakEvenLockR;

         double beSL = (t == OP_BUY)
            ? (openPrice + (lockPoints * Point))
            : (openPrice - (lockPoints * Point));

         if(t == OP_BUY)
            targetSL = MathMax(targetSL, beSL);
         else
            targetSL = (targetSL <= 0.0) ? beSL : MathMin(targetSL, beSL);
      }

      // v2: the trail in multiples of risk. THIS IS THE CHANGE THAT MATTERS - see the header.
      // v1's fixed 300/180 points are 0.15 R / 0.09 R on gold, which caps winners at about
      // +0.158 R while losers still pay -1.019 R.
      double trailStartPoints = (double)g_cfgTrailStartPoints;
      double trailDistPoints  = (double)g_cfgTrailDistancePoints;
      if(UseRBasedTrailing && TrailStartR > 0.0 && TrailDistanceR > 0.0)
      {
         trailStartPoints = riskPoints * TrailStartR;
         trailDistPoints  = riskPoints * TrailDistanceR;
      }

      if(EnableTrailingStop && profitPoints >= trailStartPoints)
      {
         double trailSL = (t == OP_BUY)
            ? (Bid - (trailDistPoints * Point))
            : (Ask + (trailDistPoints * Point));

         if(t == OP_BUY)
            targetSL = MathMax(targetSL, trailSL);
         else
            targetSL = (targetSL <= 0.0) ? trailSL : MathMin(targetSL, trailSL);
      }

      // Never pull a stop inside the broker's stop/freeze level or the current
      // spread: the broker rejects it, or it is hit by ordinary noise within
      // a second of being placed.
      double guardDist = MathMax(BrokerMinStopDistancePrice(),
                                 MarketInfo(Symbol(), MODE_FREEZELEVEL) * Point);
      guardDist = MathMax(guardDist, Ask - Bid);
      if(t == OP_BUY)
         targetSL = MathMin(targetSL, Bid - guardDist);
      else
         targetSL = MathMax(targetSL, Ask + guardDist);

      bool improve = false;
      if(t == OP_BUY && targetSL > oldSL + (Point * 0.5))
         improve = true;
      if(t == OP_SELL && targetSL < oldSL - (Point * 0.5))
         improve = true;

      if(!improve)
         continue;

      if(!OrderModify(OrderTicket(), openPrice, NormalizeDouble(targetSL, Digits), tp, 0, clrSilver))
         g_lastReason = "manage error " + IntegerToString(GetLastError());
   }
}

void CreateOrUpdateLabel(string name, string text, int x, int y, color c, int size)
{
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);

   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, Ui(x));
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, Ui(y));
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, c);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, Ui(size));
   ObjectSetString(0, name, OBJPROP_FONT, DashboardFontName);
}

void DeleteOverlayObjects()
{
   ObjectDelete(0, g_prefix + "Z_CRT");
   ObjectDelete(0, g_prefix + "Z_FVG");
   ObjectDelete(0, g_prefix + "Z_BRK");
}

void DeleteDashboardUiObjects(bool keepShowButton)
{
   ObjectDelete(0, g_prefix + "PANEL");
   ObjectDelete(0, g_prefix + "BOX_LEFT");
   ObjectDelete(0, g_prefix + "BOX_MID");
   ObjectDelete(0, g_prefix + "BOX_RIGHT");
   ObjectDelete(0, g_prefix + "CHIP_GATE");
   ObjectDelete(0, g_prefix + "CHIP_MODE");
   ObjectDelete(0, g_prefix + "L1");
   ObjectDelete(0, g_prefix + "L2");
   ObjectDelete(0, g_prefix + "L3");
   ObjectDelete(0, g_prefix + "L4");
   ObjectDelete(0, g_prefix + "L5");
   ObjectDelete(0, g_prefix + "L6");
   ObjectDelete(0, g_prefix + "L7");
   ObjectDelete(0, g_prefix + "L8");
   ObjectDelete(0, g_prefix + "L9");
   ObjectDelete(0, g_prefix + "L10");
   ObjectDelete(0, g_prefix + "L11");
   ObjectDelete(0, g_prefix + "L12");
   ObjectDelete(0, g_prefix + "L13");
   ObjectDelete(0, g_prefix + "L14");
   ObjectDelete(0, g_prefix + "L15");
   ObjectDelete(0, g_prefix + "L16");
   ObjectDelete(0, g_prefix + "L17");
   ObjectDelete(0, g_prefix + "L18");
   ObjectDelete(0, g_prefix + "L19");
   ObjectDelete(0, g_prefix + "L20");
   ObjectDelete(0, g_prefix + "L21");
   ObjectDelete(0, g_prefix + "L22");
   ObjectDelete(0, g_prefix + "L23");
   ObjectDelete(0, g_prefix + "L24");
   ObjectDelete(0, g_prefix + "L25");
   ObjectDelete(0, g_prefix + "L26");
   ObjectDelete(0, g_prefix + "L27");
   ObjectDelete(0, g_prefix + "L28");
   ObjectDelete(0, g_prefix + "L29");
   ObjectDelete(0, g_prefix + "L30");
   ObjectDelete(0, g_prefix + "L31");
   ObjectDelete(0, g_prefix + "L32");
   ObjectDelete(0, g_prefix + "L33");
   ObjectDelete(0, g_prefix + "L34");
   ObjectDelete(0, g_prefix + "L35");
   ObjectDelete(0, g_prefix + "L36");
   ObjectDelete(0, g_prefix + "L37");
   ObjectDelete(0, g_prefix + "L38");
   ObjectDelete(0, g_prefix + "L39");
   ObjectDelete(0, g_prefix + "L40");
   ObjectDelete(0, g_prefix + "L41");
   ObjectDelete(0, g_prefix + "L42");
   ObjectDelete(0, g_prefix + "L43");
   ObjectDelete(0, g_prefix + "BTN_REG_A");
   ObjectDelete(0, g_prefix + "BTN_REG_T");
   ObjectDelete(0, g_prefix + "BTN_REG_R");
   ObjectDelete(0, g_prefix + "BTN_REG_V");
   ObjectDelete(0, g_prefix + "BTN_AUTO");
   ObjectDelete(0, g_prefix + "BTN_CLOSE");
   ObjectDelete(0, g_prefix + "BTN_BUY");
   ObjectDelete(0, g_prefix + "BTN_SELL");
   ObjectDelete(0, g_prefix + "BTN_RESET");
   ObjectDelete(0, g_prefix + "BTN_OPEN");
   ObjectDelete(0, g_prefix + "BTN_THEME");
   ObjectDelete(0, g_prefix + "BTN_CRT");
   ObjectDelete(0, g_prefix + "BTN_FVG");
   ObjectDelete(0, g_prefix + "BTN_BRK");
   ObjectDelete(0, g_prefix + "BTN_PRE_GOLD");
   ObjectDelete(0, g_prefix + "BTN_PRE_BTC");
   ObjectDelete(0, g_prefix + "BTN_PRE_MIN");
   ObjectDelete(0, g_prefix + "BTN_REC");
   ObjectDelete(0, g_prefix + "BTN_SIZE_S");
   ObjectDelete(0, g_prefix + "BTN_SIZE_M");
   ObjectDelete(0, g_prefix + "BTN_SIZE_L");
   ObjectDelete(0, g_prefix + "BTN_SIZE_F");
   ObjectDelete(0, g_prefix + "BTN_PAUSE");
   ObjectDelete(0, g_prefix + "BTN_RESUME");
   ObjectDelete(0, g_prefix + "BTN_EMERG");
   ObjectDelete(0, g_prefix + "BTN_HIDE");
   ObjectDelete(0, g_prefix + "BTN_PM_SAFE");
   ObjectDelete(0, g_prefix + "BTN_PM_BAL");
   ObjectDelete(0, g_prefix + "BTN_PM_AGG");
   ObjectDelete(0, g_prefix + "BTN_MODE_PRO");
   ObjectDelete(0, g_prefix + "BTN_MODE_FOCUS");
   if(!keepShowButton)
      ObjectDelete(0, g_prefix + "BTN_SHOW");
}

void DrawZoneRectangle(string name, datetime t1, datetime t2, double pTop, double pBottom, color zoneColor)
{
   if(t1 <= 0 || t2 <= 0)
      return;
   if(pTop <= 0.0 || pBottom <= 0.0)
      return;

   if(pBottom > pTop)
   {
      double tmp = pTop;
      pTop = pBottom;
      pBottom = tmp;
   }

   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_RECTANGLE, 0, t1, pTop, t2, pBottom);

   ObjectMove(0, name, 0, t1, pTop);
   ObjectMove(0, name, 1, t2, pBottom);
   ObjectSetInteger(0, name, OBJPROP_COLOR, zoneColor);
   ObjectSetInteger(0, name, OBJPROP_BACK, true);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
   ObjectSetInteger(0, name, OBJPROP_STYLE, STYLE_SOLID);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTED, false);
}

bool FindBestCRTZone(int &direction, int &anchorShift, int &breakoutShift, double &anchorHigh, double &anchorLow, double &bestScore)
{
   direction = 0;
   anchorShift = -1;
   breakoutShift = 1;
   anchorHigh = 0.0;
   anchorLow = 0.0;
   bestScore = -1.0;

   int maxShift = MathMax(2 + g_cfgMinMiddleBars, 2 + g_cfgMaxMiddleBars);
   if(Bars <= maxShift + 5)
      return false;

   double minAnchorPoints = g_cfgMinAnchorBodyPoints;
   bool requireSweep = g_cfgRequireLiquiditySweep;
   if(g_profitMode == 2)
   {
      minAnchorPoints *= IsBtcLikeSymbol(Symbol()) ? 0.65 : 0.75;
      requireSweep = false;
   }

   for(int a = 2 + g_cfgMinMiddleBars; a <= 2 + g_cfgMaxMiddleBars; a++)
   {
      if(a >= Bars - 1)
         continue;

      int middleBars = a - 2;
      bool anchorBull = Close[a] > Open[a];
      bool anchorBear = Close[a] < Open[a];
      double anchorBodyPoints = MathAbs(Close[a] - Open[a]) / Point;
      if(anchorBodyPoints < minAnchorPoints)
         continue;

      double aHigh = High[a];
      double aLow = Low[a];
      bool sweepSeen = false;

      if(anchorBear && Close[1] > Open[1] && Close[1] > aHigh)
      {
         if(MiddleCandleRulesOk(a, true, sweepSeen))
         {
            if(!requireSweep || sweepSeen || Low[1] < aLow - PipsToPrice(g_cfgSweepMinPoints))
            {
               double s = CalculateSetupScore(true, aHigh, aLow, a, 1, sweepSeen, middleBars);
               if(s > bestScore)
               {
                  bestScore = s;
                  direction = 1;
                  anchorShift = a;
                  anchorHigh = aHigh;
                  anchorLow = aLow;
               }
            }
         }
      }

      if(anchorBull && Close[1] < Open[1] && Close[1] < aLow)
      {
         if(MiddleCandleRulesOk(a, false, sweepSeen))
         {
            if(!requireSweep || sweepSeen || High[1] > aHigh + PipsToPrice(g_cfgSweepMinPoints))
            {
               double s = CalculateSetupScore(false, aHigh, aLow, a, 1, sweepSeen, middleBars);
               if(s > bestScore)
               {
                  bestScore = s;
                  direction = -1;
                  anchorShift = a;
                  anchorHigh = aHigh;
                  anchorLow = aLow;
               }
            }
         }
      }
   }

   return (bestScore >= 0.0 && direction != 0 && anchorShift > 1);
}

bool FindRecentFVG(datetime &t1, datetime &t2, double &top, double &bottom, int &direction)
{
   t1 = 0;
   t2 = 0;
   top = 0.0;
   bottom = 0.0;
   direction = 0;

   int minGap = MathMax(1, FVGMinGapPoints);
   int maxScan = MathMin(40, Bars - 4);
   if(maxScan < 1)
      return false;

   for(int n = 1; n <= maxScan; n++)
   {
      int old = n + 2;
      if(old >= Bars)
         break;

      if(Low[n] > High[old])
      {
         double gapPts = (Low[n] - High[old]) / Point;
         if(gapPts >= minGap)
         {
            direction = 1;
            top = Low[n];
            bottom = High[old];
            t1 = Time[old];
            t2 = Time[n];
            return true;
         }
      }

      if(High[n] < Low[old])
      {
         double gapPts = (Low[old] - High[n]) / Point;
         if(gapPts >= minGap)
         {
            direction = -1;
            top = Low[old];
            bottom = High[n];
            t1 = Time[old];
            t2 = Time[n];
            return true;
         }
      }
   }

   return false;
}

bool FindBreakoutZone(datetime &t1, datetime &t2, double &top, double &bottom, int &direction)
{
   t1 = 0;
   t2 = 0;
   top = 0.0;
   bottom = 0.0;
   direction = 0;

   int lookback = 12;
   if(Bars < lookback + 4)
      return false;

   double hh = High[2];
   double ll = Low[2];
   for(int i = 3; i <= lookback + 1; i++)
   {
      if(High[i] > hh)
         hh = High[i];
      if(Low[i] < ll)
         ll = Low[i];
   }

   if(Close[1] > hh)
      direction = 1;
   else if(Close[1] < ll)
      direction = -1;
   else
      return false;

   t1 = Time[lookback + 1];
   t2 = Time[0];
   top = hh;
   bottom = ll;
   return true;
}

void DrawMarketOverlays()
{
   if(Bars < 20)
   {
      DeleteOverlayObjects();
      return;
   }

   if(!g_showCRTZone)
      ObjectDelete(0, g_prefix + "Z_CRT");
   else
   {
      int crtDir = 0;
      int anchorShift = -1;
      int breakoutShift = 1;
      double anchorHigh = 0.0;
      double anchorLow = 0.0;
      double score = -1.0;
      if(FindBestCRTZone(crtDir, anchorShift, breakoutShift, anchorHigh, anchorLow, score))
      {
         color c = (crtDir > 0) ? BuyColor : SellColor;
         DrawZoneRectangle(g_prefix + "Z_CRT", Time[anchorShift], Time[breakoutShift], anchorHigh, anchorLow, c);
      }
      else
      {
         ObjectDelete(0, g_prefix + "Z_CRT");
      }
   }

   if(!g_showFVGZone)
      ObjectDelete(0, g_prefix + "Z_FVG");
   else
   {
      datetime fvgT1 = 0;
      datetime fvgT2 = 0;
      double fvgTop = 0.0;
      double fvgBottom = 0.0;
      int fvgDir = 0;
      if(FindRecentFVG(fvgT1, fvgT2, fvgTop, fvgBottom, fvgDir))
      {
         color c = (fvgDir > 0) ? (color)0x80B060 : (color)0x6060D0;
         DrawZoneRectangle(g_prefix + "Z_FVG", fvgT1, fvgT2, fvgTop, fvgBottom, c);
      }
      else
      {
         ObjectDelete(0, g_prefix + "Z_FVG");
      }
   }

   if(!g_showBreakoutZone)
      ObjectDelete(0, g_prefix + "Z_BRK");
   else
   {
      datetime bT1 = 0;
      datetime bT2 = 0;
      double bTop = 0.0;
      double bBottom = 0.0;
      int bDir = 0;
      if(FindBreakoutZone(bT1, bT2, bTop, bBottom, bDir))
      {
         color c = (bDir > 0) ? (color)0x30A0F0 : (color)0x2040C0;
         DrawZoneRectangle(g_prefix + "Z_BRK", bT1, bT2, bTop, bBottom, c);
      }
      else
      {
         ObjectDelete(0, g_prefix + "Z_BRK");
      }
   }
}

string ClipText(string v, int maxChars)
{
   if(maxChars <= 0)
      return "";
   if(StringLen(v) <= maxChars)
      return v;
   if(maxChars <= 3)
      return StringSubstr(v, 0, maxChars);
   return StringSubstr(v, 0, maxChars - 3) + "...";
}

void DrawDashboard()
{
   static int s_lastDashboardMode = -1;
   if(g_dashboardMode < 0 || g_dashboardMode > 1)
      g_dashboardMode = 0;

   if(s_lastDashboardMode != g_dashboardMode)
   {
      DeleteDashboardUiObjects(false);
      s_lastDashboardMode = g_dashboardMode;
   }

   if(!g_dashboardVisible)
   {
      DeleteDashboardUiObjects(true);
      CreateOrUpdateButton(g_prefix + "BTN_SHOW", "SHOW DASHBOARD", 12, 18, 124, 22, clrDodgerBlue, clrWhite);
      return;
   }

   ObjectDelete(0, g_prefix + "BTN_SHOW");
   UpdateSystemSnapshot();

   string panel = g_prefix + "PANEL";
   if(ObjectFind(0, panel) < 0)
      ObjectCreate(0, panel, OBJ_RECTANGLE_LABEL, 0, 0, 0);

   ObjectSetInteger(0, panel, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, panel, OBJPROP_XDISTANCE, Ui(10));
   ObjectSetInteger(0, panel, OBJPROP_YDISTANCE, Ui(18));
   ObjectSetInteger(0, panel, OBJPROP_XSIZE, Ui(760));
   ObjectSetInteger(0, panel, OBJPROP_YSIZE, Ui(478));
   ObjectSetInteger(0, panel, OBJPROP_BGCOLOR, g_uiBg);
   ObjectSetInteger(0, panel, OBJPROP_COLOR, g_uiBorder);

   color cardBg = (color)0x1F140D;
   color cardBorder = (color)0x3A2A1A;

   double spread = CurrentSpreadPoints();
   int openTrades = CountOpenTradesForSymbol();
   double pnl = FloatingPnLForSymbol();
   double dayLossPct = CurrentDayLossPercent();
   double atr = iATR(NULL, 0, ATRPeriod, 1) / Point;
   double openRiskPct = CurrentOpenRiskPercent();
   double marginLvl = CurrentMarginLevelPercent();
   double bal = AccountBalance();
   double eq = AccountEquity();
   double freeM = AccountFreeMargin();
   int barsSinceSignal = BarsSinceLastTradeSignal();
   bool sessionOpen = IsSessionOpen();
   bool spreadGood = (spread <= g_cfgMaxSpreadPoints);
   bool openRiskOk = (openRiskPct < ActiveOpenRiskCapPercent());
   string updatedAt = TimeToString(TimeCurrent(), TIME_SECONDS);
   string gate = (g_gateReasonCode == "READY") ? "READY" : ("BLOCKED: " + g_gateReasonCode);
   color gateColor = (g_gateReasonCode == "READY") ? BuyColor : SellColor;
   int closed = 0;
   int wins = 0;
   double net = 0.0;
   double gp = 0.0;
   double gl = 0.0;
   ComputePerformanceStats(closed, wins, net, gp, gl);
   double winRate = (closed > 0) ? (100.0 * wins / closed) : 0.0;
   double pf = (gl > 0.0) ? (gp / gl) : ((gp > 0.0) ? 999.0 : 0.0);
   int logCount = g_decisionLogCount;
   string log1 = (logCount >= 1) ? g_decisionLog[logCount - 1] : "-";
   string log2 = (logCount >= 2) ? g_decisionLog[logCount - 2] : "-";
   string log3 = (logCount >= 3) ? g_decisionLog[logCount - 3] : "-";
   string shortStatus = ClipText(g_lastReason, 30);
   string shortSignal = ClipText(g_lastSignal, 30);
   int regimeDetected = DetectMarketRegime();
   int regimeActive = ActiveRegimeMode();

   string themeName = g_runtimeThemeName;
   string topMeta = ClipText(Symbol() + "  " + TfToText() + "  |  " + g_profileName + "  |  " + themeName, 26);
   string gateChipText = ClipText(gate, 18);
   string modeChipText = ClipText(g_profitModeName + " / " + RegimeModeText(g_regimeMode), 22);

    color modeProBg = (g_dashboardMode == 0) ? clrDodgerBlue : clrDimGray;
    color modeFocusBg = (g_dashboardMode == 1) ? clrDodgerBlue : clrDimGray;
    color autoBg = g_autoEnabledRuntime ? BuyColor : SellColor;
    color buyBg = g_allowBuyRuntime ? BuyColor : clrDimGray;
    color sellBg = g_allowSellRuntime ? SellColor : clrDimGray;

   CreateOrUpdateButton(g_prefix + "CHIP_GATE", gateChipText, 162, 26, 138, 18, gateColor, clrWhite);
   CreateOrUpdateButton(g_prefix + "CHIP_MODE", modeChipText, 306, 26, 170, 18, clrDodgerBlue, clrWhite);

   if(g_dashboardMode == 1)
   {
      ObjectSetInteger(0, panel, OBJPROP_YSIZE, Ui(356));
      CreateOrUpdateCard(g_prefix + "BOX_LEFT", 18, 82, 372, 256, cardBg, cardBorder);
      CreateOrUpdateCard(g_prefix + "BOX_RIGHT", 400, 82, 348, 256, cardBg, cardBorder);
      ObjectDelete(0, g_prefix + "BOX_MID");

      string sizeTextFocus = UseFixedLotSize
         ? ("Lot / RR: " + DoubleToString(NormalizeLot(FixedLotSize), 2) + " / " + DoubleToString(g_cfgRiskReward, 2))
         : ("Risk / RR: " + DoubleToString(EffectiveRiskPercent(), 2) + "% / " + DoubleToString(g_cfgRiskReward, 2));
      string tfMatrixFocus = TfValueToText((HTFBiasTF > 0) ? HTFBiasTF : MTFTrendTF) + "->" + TfValueToText(ExecutionTF);
      string setupDir = (g_candidateDirection > 0) ? "BUY" : ((g_candidateDirection < 0) ? "SELL" : "NONE");
      string checklist = "TF=" + (g_chkExecTf ? "OK" : "NO") + " Risk=" + (g_chkRisk ? "OK" : "NO") + " Sess=" + (g_chkSession ? "OK" : "NO") + " Spr=" + (g_chkSpread ? "OK" : "NO") + " Cap=" + (g_chkCapacity ? "OK" : "NO");

      CreateOrUpdateLabel(g_prefix + "L1", "CRT EA Dashboard Focus", 24, 28, g_uiText, 11);
      CreateOrUpdateLabel(g_prefix + "L2", ClipText(Symbol() + "  " + TfToText() + "  |  " + g_profileName, 42), 24, 48, g_uiMuted, 9);
      CreateOrUpdateLabel(g_prefix + "L3", "AUTO " + (g_autoEnabledRuntime ? "ON" : "OFF") + " | BUY " + (g_allowBuyRuntime ? "ALLOW" : "BLOCK") + " | SELL " + (g_allowSellRuntime ? "ALLOW" : "BLOCK"), 24, 74, g_autoEnabledRuntime ? BuyColor : SellColor, 9);
      CreateOrUpdateLabel(g_prefix + "L4", "State: " + SystemStateText(), 24, 96, gateColor, 10);
      CreateOrUpdateLabel(g_prefix + "L5", "SetupScore: " + DoubleToString(g_opportunityScore, 1) + " / Min " + DoubleToString(g_liveMinScore, 1) + " | Dir " + setupDir, 24, 118, g_uiText, 9);
      CreateOrUpdateLabel(g_prefix + "L6", "Checklist: " + checklist, 24, 138, g_uiText, 8);
      CreateOrUpdateLabel(g_prefix + "L7", "Signal: " + ClipText(g_lastSignal, 44), 24, 158, g_uiText, 9);
      CreateOrUpdateLabel(g_prefix + "L8", "Status: " + ClipText(g_lastReason, 44), 24, 178, g_uiText, 9);
      CreateOrUpdateLabel(g_prefix + "L9", "Float: " + DoubleToString(pnl, 2) + " | Day DD: " + DoubleToString(dayLossPct, 2) + "%", 24, 198, (pnl >= 0 ? BuyColor : SellColor), 9);
      CreateOrUpdateLabel(g_prefix + "L10", "Open: " + IntegerToString(openTrades) + "/" + IntegerToString(MaxOpenTradesPerSymbol) + " | Spread: " + DoubleToString(spread, 1) + "/" + IntegerToString(g_cfgMaxSpreadPoints) + " pts", 24, 218, spreadGood ? g_uiText : SellColor, 9);
      CreateOrUpdateLabel(g_prefix + "L11", sizeTextFocus, 24, 238, g_uiText, 9);
      CreateOrUpdateLabel(g_prefix + "L12", "Regime: " + RegimeModeText(regimeActive) + " | Conf x" + DoubleToString(g_confidenceLotMultiplier, 2) + " | Updated " + updatedAt, 24, 258, g_uiMuted, 8);

      CreateOrUpdateLabel(g_prefix + "L28", "Focus Controls", 414, 96, g_uiMuted, 10);
      CreateOrUpdateButton(g_prefix + "BTN_MODE_PRO", "PRO", 414, 114, 86, 22, modeProBg, clrWhite);
      CreateOrUpdateButton(g_prefix + "BTN_MODE_FOCUS", "FOCUS", 506, 114, 86, 22, modeFocusBg, clrWhite);
      CreateOrUpdateButton(g_prefix + "BTN_REG_A", "AUTO", 598, 114, 34, 22, g_regimeMode == 0 ? clrDodgerBlue : clrDimGray, clrWhite);
      CreateOrUpdateButton(g_prefix + "BTN_REG_T", "TRE", 634, 114, 34, 22, g_regimeMode == 1 ? clrDodgerBlue : clrDimGray, clrWhite);
      CreateOrUpdateButton(g_prefix + "BTN_REG_R", "RNG", 670, 114, 34, 22, g_regimeMode == 2 ? clrDodgerBlue : clrDimGray, clrWhite);
      CreateOrUpdateButton(g_prefix + "BTN_REG_V", "VOL", 706, 114, 34, 22, g_regimeMode == 3 ? clrDodgerBlue : clrDimGray, clrWhite);
      CreateOrUpdateButton(g_prefix + "BTN_AUTO", g_autoEnabledRuntime ? "AUTO ON" : "AUTO OFF", 414, 144, 160, 28, autoBg, clrWhite);
      CreateOrUpdateButton(g_prefix + "BTN_OPEN", "OPEN TRADE", 586, 144, 150, 28, clrTeal, clrWhite);
      CreateOrUpdateButton(g_prefix + "BTN_CLOSE", "CLOSE ALL", 414, 176, 160, 26, clrOrangeRed, clrWhite);
      CreateOrUpdateButton(g_prefix + "BTN_RESET", "RISK RESET", 586, 176, 150, 26, clrDodgerBlue, clrWhite);
      CreateOrUpdateButton(g_prefix + "BTN_BUY", g_allowBuyRuntime ? "BUY ALLOW" : "BUY BLOCK", 414, 206, 76, 24, buyBg, clrWhite);
      CreateOrUpdateButton(g_prefix + "BTN_SELL", g_allowSellRuntime ? "SELL ALLOW" : "SELL BLOCK", 498, 206, 76, 24, sellBg, clrWhite);
      CreateOrUpdateButton(g_prefix + "BTN_CRT", g_showCRTZone ? "CRT ON" : "CRT OFF", 586, 206, 46, 24, g_showCRTZone ? clrSeaGreen : clrDimGray, clrWhite);
      CreateOrUpdateButton(g_prefix + "BTN_FVG", g_showFVGZone ? "FVG ON" : "FVG OFF", 636, 206, 46, 24, g_showFVGZone ? clrSeaGreen : clrDimGray, clrWhite);
      CreateOrUpdateButton(g_prefix + "BTN_BRK", g_showBreakoutZone ? "BRK ON" : "BRK OFF", 686, 206, 50, 24, g_showBreakoutZone ? clrSeaGreen : clrDimGray, clrWhite);
      CreateOrUpdateButton(g_prefix + "BTN_PAUSE", "PAUSE", 414, 236, 76, 24, clrDarkOrange, clrWhite);
      CreateOrUpdateButton(g_prefix + "BTN_RESUME", "RESUME", 498, 236, 76, 24, clrSeaGreen, clrWhite);
      CreateOrUpdateButton(g_prefix + "BTN_EMERG", "EMERGENCY FLAT", 586, 236, 150, 24, clrCrimson, clrWhite);
      CreateOrUpdateButton(g_prefix + "BTN_HIDE", "HIDE PANEL", 414, 266, 322, 22, clrDimGray, clrWhite);

      if(EnableProfitModeControl)
      {
         CreateOrUpdateButton(g_prefix + "BTN_PM_SAFE", "SAFE", 24, 292, 64, 22, g_profitMode == 0 ? clrDodgerBlue : clrDimGray, clrWhite);
         CreateOrUpdateButton(g_prefix + "BTN_PM_BAL", "BAL", 92, 292, 64, 22, g_profitMode == 1 ? clrDodgerBlue : clrDimGray, clrWhite);
         CreateOrUpdateButton(g_prefix + "BTN_PM_AGG", "AGG", 160, 292, 64, 22, g_profitMode == 2 ? clrDodgerBlue : clrDimGray, clrWhite);
      }
      else
      {
         ObjectDelete(0, g_prefix + "BTN_PM_SAFE");
         ObjectDelete(0, g_prefix + "BTN_PM_BAL");
         ObjectDelete(0, g_prefix + "BTN_PM_AGG");
      }

      ObjectDelete(0, g_prefix + "BTN_THEME");
      ObjectDelete(0, g_prefix + "BTN_PRE_GOLD");
      ObjectDelete(0, g_prefix + "BTN_PRE_BTC");
      ObjectDelete(0, g_prefix + "BTN_PRE_MIN");
      ObjectDelete(0, g_prefix + "BTN_REC");
      ObjectDelete(0, g_prefix + "BTN_SIZE_S");
      ObjectDelete(0, g_prefix + "BTN_SIZE_M");
      ObjectDelete(0, g_prefix + "BTN_SIZE_L");
      ObjectDelete(0, g_prefix + "BTN_SIZE_F");
      ObjectDelete(0, g_prefix + "L13");
      ObjectDelete(0, g_prefix + "L14");
      ObjectDelete(0, g_prefix + "L15");
      ObjectDelete(0, g_prefix + "L16");
      ObjectDelete(0, g_prefix + "L17");
      ObjectDelete(0, g_prefix + "L18");
      ObjectDelete(0, g_prefix + "L19");
      ObjectDelete(0, g_prefix + "L20");
      ObjectDelete(0, g_prefix + "L21");
      ObjectDelete(0, g_prefix + "L22");
      ObjectDelete(0, g_prefix + "L23");
      ObjectDelete(0, g_prefix + "L24");
      ObjectDelete(0, g_prefix + "L25");
      ObjectDelete(0, g_prefix + "L26");
      ObjectDelete(0, g_prefix + "L27");
      ObjectDelete(0, g_prefix + "L29");
      ObjectDelete(0, g_prefix + "L30");
      ObjectDelete(0, g_prefix + "L31");
      ObjectDelete(0, g_prefix + "L32");
      ObjectDelete(0, g_prefix + "L33");
      ObjectDelete(0, g_prefix + "L34");
      ObjectDelete(0, g_prefix + "L35");
      ObjectDelete(0, g_prefix + "L36");
      ObjectDelete(0, g_prefix + "L37");
      ObjectDelete(0, g_prefix + "L38");
      ObjectDelete(0, g_prefix + "L39");
      ObjectDelete(0, g_prefix + "L40");

      return;
   }

   ObjectSetInteger(0, panel, OBJPROP_YSIZE, Ui(478));
   CreateOrUpdateCard(g_prefix + "BOX_LEFT", 18, 82, 258, 346, cardBg, cardBorder);
   CreateOrUpdateCard(g_prefix + "BOX_MID", 286, 82, 258, 346, cardBg, cardBorder);
   CreateOrUpdateCard(g_prefix + "BOX_RIGHT", 556, 82, 192, 386, cardBg, cardBorder);

   CreateOrUpdateLabel(g_prefix + "L1", "CRT EA Dashboard Pro", 24, 28, g_uiText, 11);
   CreateOrUpdateLabel(g_prefix + "L2", topMeta, 24, 48, g_uiMuted, 10);
   CreateOrUpdateLabel(g_prefix + "L30", "Panel Size: " + g_runtimeSizeName + " (" + DoubleToString(g_runtimeScale, 2) + "x)", 292, 226, g_uiMuted, 8);
   CreateOrUpdateLabel(g_prefix + "L3", "AUTO " + (g_autoEnabledRuntime ? "ON" : "OFF") + " | BUY " + (g_allowBuyRuntime ? "ALLOW" : "BLOCK") + " | SELL " + (g_allowSellRuntime ? "ALLOW" : "BLOCK") + (g_riskPaused ? " | RISK PAUSED" : ""), 24, 70, g_autoEnabledRuntime ? BuyColor : SellColor, 9);
   CreateOrUpdateLabel(g_prefix + "L4", "Session   : " + (sessionOpen ? "OPEN" : "CLOSED"), 24, 94, sessionOpen ? BuyColor : SellColor, 9);
   CreateOrUpdateLabel(g_prefix + "L5", "Spread    : " + DoubleToString(spread, 1) + " / " + IntegerToString(g_cfgMaxSpreadPoints) + " pts", 24, 112, (spread <= g_cfgMaxSpreadPoints ? BuyColor : SellColor), 9);
   CreateOrUpdateLabel(g_prefix + "L6", "Open Pos  : " + IntegerToString(openTrades) + "/" + IntegerToString(MaxOpenTradesPerSymbol), 24, 130, g_uiText, 9);
   CreateOrUpdateLabel(g_prefix + "L7", "Float PnL : " + DoubleToString(pnl, 2), 24, 148, (pnl >= 0 ? BuyColor : SellColor), 9);
   CreateOrUpdateLabel(g_prefix + "L8", "Day DD%   : " + DoubleToString(dayLossPct, 2) + " / " + DoubleToString(DailyLossLimitPercent, 2), 24, 166, (dayLossPct < DailyLossLimitPercent ? BuyColor : SellColor), 9);
   string sizeText = UseFixedLotSize
      ? ("Lot / RR  : " + DoubleToString(NormalizeLot(FixedLotSize), 2) + " / " + DoubleToString(g_cfgRiskReward, 2))
      : ("Risk / RR : " + DoubleToString(EffectiveRiskPercent(), 2) + "% / " + DoubleToString(g_cfgRiskReward, 2));
   CreateOrUpdateLabel(g_prefix + "L9", sizeText, 24, 184, g_uiText, 9);
   CreateOrUpdateLabel(g_prefix + "L10", "ATR / SL  : " + DoubleToString(atr, 0) + " pts / " + IntegerToString(EffectiveStopBufferPoints()) + " pts", 24, 202, g_uiText, 9);
   CreateOrUpdateLabel(g_prefix + "L11", "Cooldown  : " + IntegerToString(barsSinceSignal) + " bars | need " + IntegerToString(CooldownBars), 24, 220, g_uiText, 9);
   CreateOrUpdateLabel(g_prefix + "L12", "Signal    : " + shortSignal, 24, 238, g_uiText, 9);
   CreateOrUpdateLabel(g_prefix + "L13", "Gate      : " + gate, 24, 256, gateColor, 9);
   CreateOrUpdateLabel(g_prefix + "L14", "Status    : " + shortStatus, 24, 274, g_uiText, 9);
   CreateOrUpdateLabel(g_prefix + "L15", "Execution", 24, 300, g_uiMuted, 9);

   CreateOrUpdateLabel(g_prefix + "L16", "Retries   : " + IntegerToString(MaxOrderSendRetries), 24, 318, g_uiText, 9);
   CreateOrUpdateLabel(g_prefix + "L17", "Partial   : " + (EnablePartialClose ? "ON" : "OFF") + " @ " + DoubleToString(PartialCloseAtR, 1) + "R", 24, 336, g_uiText, 9);
   CreateOrUpdateLabel(g_prefix + "L18", "BE / Trail: " + (EnableBreakEven ? "ON" : "OFF") + " / " + (EnableTrailingStop ? "ON" : "OFF"), 24, 354, g_uiText, 9);
   CreateOrUpdateLabel(g_prefix + "L19", "News blk  : " + (UseNewsBlackout ? "ON" : "OFF") + " | ChartTheme=" + (ApplyChartColorTheme ? "ON" : "OFF"), 24, 372, g_uiText, 9);
   double liveMinScore = EffectiveMinSetupScore();
   CreateOrUpdateLabel(g_prefix + "L37", "Profit: " + g_profitModeName + " | MinScore=" + DoubleToString(liveMinScore, 1) + " (A:" + DoubleToString(g_adaptiveScoreOffset, 1) + ", D:" + DoubleToString(g_dynamicThresholdOffset, 1) + ")", 24, 390, g_uiText, 9);

   CreateOrUpdateLabel(g_prefix + "L20", "Performance", 304, 54, g_uiMuted, 10);
   CreateOrUpdateLabel(g_prefix + "L21", "Closed: " + IntegerToString(closed), 292, 70, g_uiText, 9);
   CreateOrUpdateLabel(g_prefix + "L22", "WinRate: " + DoubleToString(winRate, 1) + "%", 292, 88, g_uiText, 9);
   CreateOrUpdateLabel(g_prefix + "L23", "PF: " + DoubleToString(pf, 2) + " | Net: " + DoubleToString(net, 2), 292, 106, (net >= 0 ? BuyColor : SellColor), 9);
   CreateOrUpdateLabel(g_prefix + "L24", "Decision Log", 292, 136, g_uiMuted, 10);
   CreateOrUpdateLabel(g_prefix + "L25", ClipText(log1, 37), 292, 158, g_uiText, 8);
   CreateOrUpdateLabel(g_prefix + "L26", ClipText(log2, 37), 292, 176, g_uiText, 8);
   CreateOrUpdateLabel(g_prefix + "L27", ClipText(log3, 37), 292, 194, g_uiText, 8);
   CreateOrUpdateLabel(g_prefix + "L31", "Account Telemetry", 292, 272, g_uiMuted, 10);
   CreateOrUpdateLabel(g_prefix + "L32", "Bal: " + DoubleToString(bal, 2) + " | Eq: " + DoubleToString(eq, 2), 292, 294, g_uiText, 8);
   CreateOrUpdateLabel(g_prefix + "L33", "Free Margin: " + DoubleToString(freeM, 2), 292, 312, g_uiText, 8);
   CreateOrUpdateLabel(g_prefix + "L34", "Margin Lv: " + DoubleToString(marginLvl, 1) + "%", 292, 330, (marginLvl >= 200.0 ? BuyColor : SellColor), 8);
   CreateOrUpdateLabel(g_prefix + "L35", "Open Risk: " + DoubleToString(openRiskPct, 2) + "% / " + DoubleToString(ActiveOpenRiskCapPercent(), 2) + "%", 292, 348, (openRiskPct < ActiveOpenRiskCapPercent() ? BuyColor : SellColor), 8);
   string tfMatrix = TfValueToText((HTFBiasTF > 0) ? HTFBiasTF : MTFTrendTF) + "->" + TfValueToText(ExecutionTF);
   CreateOrUpdateLabel(g_prefix + "L36", ClipText("Regime: " + RegimeModeText(g_regimeMode) + " | Live: " + RegimeModeText(regimeDetected) + " | Act: " + RegimeModeText(regimeActive) + " | TF " + tfMatrix, 52), 292, 366, g_uiText, 8);
   CreateOrUpdateLabel(g_prefix + "L38", ClipText("Health: Sess=" + (sessionOpen ? "OK" : "OFF") + " | Spread=" + (spreadGood ? "OK" : "HIGH") + " | Risk=" + (openRiskOk ? "OK" : "FULL"), 52), 292, 384, (sessionOpen && spreadGood && openRiskOk) ? BuyColor : SellColor, 8);
   CreateOrUpdateLabel(g_prefix + "L39", ClipText("Updated: " + updatedAt + " | Theme: " + themeName + " | HrWR:" + DoubleToString(g_sessionHourWinRate, 1) + "%/" + IntegerToString(g_sessionHourTrades) + " | BufATR:" + DoubleToString(MTFSoftBufferATR, 2), 52), 292, 402, g_uiMuted, 8);
   string setupDirPro = (g_candidateDirection > 0) ? "BUY" : ((g_candidateDirection < 0) ? "SELL" : "NONE");
   string algoChecklist = "TF=" + (g_chkExecTf ? "OK" : "NO") + " R=" + (g_chkRisk ? "OK" : "NO") + " S=" + (g_chkSession ? "OK" : "NO") + " Sp=" + (g_chkSpread ? "OK" : "NO") + " C=" + (g_chkCapacity ? "OK" : "NO");
   CreateOrUpdateLabel(g_prefix + "L41", ClipText("System: " + SystemStateText(), 52), 292, 420, gateColor, 8);
   CreateOrUpdateLabel(g_prefix + "L42", ClipText("Score: " + DoubleToString(g_opportunityScore, 1) + " / Min " + DoubleToString(g_liveMinScore, 1) + " | Dir " + setupDirPro + " | Conf x" + DoubleToString(g_confidenceLotMultiplier, 2), 52), 292, 438, g_uiText, 8);
   CreateOrUpdateLabel(g_prefix + "L43", ClipText("Checklist: " + algoChecklist, 52), 292, 456, g_uiMuted, 8);
   ObjectDelete(0, g_prefix + "L40");

   color sizeS = (g_runtimeSizeName == "SMALL") ? clrDodgerBlue : clrDimGray;
   color sizeM = (g_runtimeSizeName == "MEDIUM") ? clrDodgerBlue : clrDimGray;
   color sizeL = (g_runtimeSizeName == "LARGE") ? clrDodgerBlue : clrDimGray;
   color sizeF = (g_runtimeSizeName == "FULL") ? clrDodgerBlue : clrDimGray;

   CreateOrUpdateLabel(g_prefix + "L28", "Controls / Regime", 560, 48, g_uiMuted, 10);
   CreateOrUpdateLabel(g_prefix + "L29", "Visual Presets", 560, 262, g_uiMuted, 9);
   CreateOrUpdateButton(g_prefix + "BTN_MODE_PRO", "PRO", 560, 22, 86, 20, modeProBg, clrWhite);
   CreateOrUpdateButton(g_prefix + "BTN_MODE_FOCUS", "FOCUS", 654, 22, 86, 20, modeFocusBg, clrWhite);
   CreateOrUpdateButton(g_prefix + "BTN_REG_A", "AUTO", 560, 44, 42, 20, g_regimeMode == 0 ? clrDodgerBlue : clrDimGray, clrWhite);
   CreateOrUpdateButton(g_prefix + "BTN_REG_T", "TRE", 606, 44, 42, 20, g_regimeMode == 1 ? clrDodgerBlue : clrDimGray, clrWhite);
   CreateOrUpdateButton(g_prefix + "BTN_REG_R", "RNG", 652, 44, 42, 20, g_regimeMode == 2 ? clrDodgerBlue : clrDimGray, clrWhite);
   CreateOrUpdateButton(g_prefix + "BTN_REG_V", "VOL", 698, 44, 42, 20, g_regimeMode == 3 ? clrDodgerBlue : clrDimGray, clrWhite);
   CreateOrUpdateButton(g_prefix + "BTN_AUTO", g_autoEnabledRuntime ? "AUTO ON" : "AUTO OFF", 560, 70, 180, 26, autoBg, clrWhite);
   CreateOrUpdateButton(g_prefix + "BTN_CLOSE", "CLOSE ALL", 560, 102, 180, 26, clrOrangeRed, clrWhite);
   CreateOrUpdateButton(g_prefix + "BTN_BUY", g_allowBuyRuntime ? "BUY ALLOW" : "BUY BLOCK", 560, 134, 86, 24, buyBg, clrWhite);
   CreateOrUpdateButton(g_prefix + "BTN_SELL", g_allowSellRuntime ? "SELL ALLOW" : "SELL BLOCK", 654, 134, 86, 24, sellBg, clrWhite);
   CreateOrUpdateButton(g_prefix + "BTN_RESET", "RISK RESET", 560, 164, 180, 26, clrDodgerBlue, clrWhite);
   CreateOrUpdateButton(g_prefix + "BTN_OPEN", "OPEN TRADE", 560, 196, 180, 26, clrTeal, clrWhite);
   CreateOrUpdateButton(g_prefix + "BTN_THEME", "APPLY CHART THEME", 560, 228, 180, 26, clrMediumPurple, clrWhite);
   CreateOrUpdateButton(g_prefix + "BTN_CRT", g_showCRTZone ? "CRT ON" : "CRT OFF", 560, 252, 58, 22, g_showCRTZone ? clrSeaGreen : clrDimGray, clrWhite);
   CreateOrUpdateButton(g_prefix + "BTN_FVG", g_showFVGZone ? "FVG ON" : "FVG OFF", 621, 252, 58, 22, g_showFVGZone ? clrSeaGreen : clrDimGray, clrWhite);
   CreateOrUpdateButton(g_prefix + "BTN_BRK", g_showBreakoutZone ? "BRK ON" : "BRK OFF", 682, 252, 58, 22, g_showBreakoutZone ? clrSeaGreen : clrDimGray, clrWhite);
   CreateOrUpdateButton(g_prefix + "BTN_PRE_GOLD", "GOLD PRO", 560, 280, 180, 24, (color)0xB38B00, clrWhite);
   CreateOrUpdateButton(g_prefix + "BTN_PRE_BTC", "BTC NEON", 560, 308, 180, 24, (color)0x008850, clrWhite);
   CreateOrUpdateButton(g_prefix + "BTN_PRE_MIN", "MINIMAL CLEAN", 560, 336, 180, 24, clrLightGray, clrBlack);
   CreateOrUpdateButton(g_prefix + "BTN_REC", "RECOMMENDED", 560, 364, 180, 22, clrDodgerBlue, clrWhite);
   CreateOrUpdateButton(g_prefix + "BTN_PAUSE", "PAUSE ALL", 560, 392, 86, 24, clrDarkOrange, clrWhite);
   CreateOrUpdateButton(g_prefix + "BTN_RESUME", "RESUME", 654, 392, 86, 24, clrSeaGreen, clrWhite);
   CreateOrUpdateButton(g_prefix + "BTN_EMERG", "EMERGENCY FLAT", 560, 420, 180, 24, clrCrimson, clrWhite);
   CreateOrUpdateButton(g_prefix + "BTN_HIDE", "HIDE PANEL", 560, 446, 180, 22, clrDimGray, clrWhite);
   if(EnableProfitModeControl)
   {
      CreateOrUpdateButton(g_prefix + "BTN_PM_SAFE", "SAFE", 24, 412, 54, 22, g_profitMode == 0 ? clrDodgerBlue : clrDimGray, clrWhite);
      CreateOrUpdateButton(g_prefix + "BTN_PM_BAL", "BAL", 82, 412, 54, 22, g_profitMode == 1 ? clrDodgerBlue : clrDimGray, clrWhite);
      CreateOrUpdateButton(g_prefix + "BTN_PM_AGG", "AGG", 140, 412, 54, 22, g_profitMode == 2 ? clrDodgerBlue : clrDimGray, clrWhite);
   }
   else
   {
      ObjectDelete(0, g_prefix + "BTN_PM_SAFE");
      ObjectDelete(0, g_prefix + "BTN_PM_BAL");
      ObjectDelete(0, g_prefix + "BTN_PM_AGG");
   }
   CreateOrUpdateButton(g_prefix + "BTN_SIZE_S", "SMALL", 292, 244, 58, 20, sizeS, clrWhite);
   CreateOrUpdateButton(g_prefix + "BTN_SIZE_M", "MEDIUM", 354, 244, 62, 20, sizeM, clrWhite);
   CreateOrUpdateButton(g_prefix + "BTN_SIZE_L", "LARGE", 420, 244, 58, 20, sizeL, clrWhite);
   CreateOrUpdateButton(g_prefix + "BTN_SIZE_F", "FULL", 482, 244, 58, 20, sizeF, clrWhite);
}

void CleanupDashboard()
{
   DeleteDashboardUiObjects(false);
   DeleteOverlayObjects();
}

void ProcessSignal()
{
   if(!ExecutionTfOk())
   {
      g_lastReason = "execution timeframe required: " + TfValueToText(ExecutionTF);
      return;
   }

   int direction = 0;
   double stopRefLow = 0.0;
   double stopRefHigh = 0.0;
   datetime signalTime = 0;
   int middleBars = 0;
   double signalScore = 0.0;

   if(!DetectCRTSignal(direction, stopRefLow, stopRefHigh, signalTime, middleBars, signalScore))
   {
      g_lastSignal = "NONE";
      g_lastReason = "no CRT setup";
      return;
   }

   g_lastSignal = (direction > 0 ? "BULLISH CRT" : "BEARISH CRT") + " (mid=" + IntegerToString(middleBars) + ", score=" + DoubleToString(signalScore, 1) + ")";

   double effectiveMinScore = EffectiveMinSetupScore();
   bool nearThresholdPass = false;

   if(signalScore < effectiveMinScore)
   {
      nearThresholdPass = AllowNearThresholdPass(signalScore, effectiveMinScore);
      if(!nearThresholdPass)
      {
         g_lastReason = "score too low " + DoubleToString(signalScore, 1) + " < " + DoubleToString(effectiveMinScore, 1);
         LogDecision(g_lastReason);
         return;
      }

      g_lastReason = "near-threshold pass score " + DoubleToString(signalScore, 1) + " / " + DoubleToString(effectiveMinScore, 1);
      LogDecision(g_lastReason);
   }

   if(signalTime == g_lastTradeSignalTime)
   {
      g_lastReason = "signal already processed";
      LogDecision(g_lastReason);
      return;
   }

   if(!g_autoEnabledRuntime)
   {
      g_lastReason = "auto trading disabled";
      LogDecision(g_lastReason);
      return;
   }

   if(g_riskPaused)
   {
      g_lastReason = "risk paused: " + g_riskPauseReason;
      LogDecision(g_lastReason);
      return;
   }

   if(direction > 0 && !g_allowBuyRuntime)
   {
      g_lastReason = "buy side locked";
      LogDecision(g_lastReason);
      return;
   }

   if(direction < 0 && !g_allowSellRuntime)
   {
      g_lastReason = "sell side locked";
      LogDecision(g_lastReason);
      return;
   }

   if(!DailyLossGuardOk())
   {
      g_lastReason = "daily loss guard active";
      LogDecision(g_lastReason);
      return;
   }

   if(!EquityProtectorOk())
   {
      g_lastReason = "equity protector active";
      LogDecision(g_lastReason);
      return;
   }

   if(!LossStreakOk())
   {
      g_lastReason = "loss streak protection active";
      LogDecision(g_lastReason);
      return;
   }

   if(IsInsideNewsBlackout())
   {
      g_lastReason = "news blackout window";
      LogDecision(g_lastReason);
      return;
   }

   if(!IsSessionOpen())
   {
      g_lastReason = "outside session";
      LogDecision(g_lastReason);
      return;
   }

   string sessionMemReason = "";
   if(!SessionHourQualityOk(sessionMemReason))
   {
      g_lastReason = "memory blocked: " + sessionMemReason;
      LogDecision(g_lastReason);
      return;
   }

   if(!CooldownOk())
   {
      g_lastReason = "cooldown active";
      LogDecision(g_lastReason);
      return;
   }

   if(!MTFTrendOk(direction))
   {
      bool forcePass = (g_profitMode == 2 && signalScore >= (effectiveMinScore + 6.0));
      if(!forcePass)
      {
         g_lastReason = "MTF trend filter blocked";
         LogDecision(g_lastReason);
         return;
      }

      g_lastReason = "aggressive pass: MTF soft override";
      LogDecision(g_lastReason);
   }

   string regimeBlockReason = "";
   if(!RegimeGateOk(direction, signalScore, regimeBlockReason))
   {
      g_lastReason = "regime blocked: " + regimeBlockReason;
      LogDecision(g_lastReason);
      return;
   }

   if(!SpreadOk())
   {
      g_lastReason = "spread too high";
      LogDecision(g_lastReason);
      return;
   }

   if(CurrentOpenRiskPercent() >= ActiveOpenRiskCapPercent())
   {
      g_lastReason = "open risk cap reached";
      LogDecision(g_lastReason);
      return;
   }

   if(CountOpenTradesForSymbol() >= MaxOpenTradesPerSymbol)
   {
      g_lastReason = "max open trades reached";
      LogDecision(g_lastReason);
      return;
   }

   if(PlaceTrade(direction, stopRefLow, stopRefHigh, signalScore))
   {
      g_lastTradeSignalTime = signalTime;
      LogDecision("trade opened: " + g_lastSignal);
   }
   else
   {
      LogDecision("trade failed: " + g_lastReason);
   }
}

int OnInit()
{
   ResetRuntimeVisualSettings();
   g_autoEnabledRuntime = EnableAutoTrading;
   g_allowBuyRuntime = true;
   g_allowSellRuntime = true;
   g_riskPaused = false;
   g_riskPauseReason = "";
   g_riskPausedSince = 0;
   ApplyProfitMode(1);
   LoadRuntimeState();
   ApplyDashboardTheme();
   ApplyChartVisualTheme();
   g_peakEquity = AccountEquity();
   g_decisionLogCount = 0;
   ApplySymbolPreset();
   g_dayOfYear = TimeDayOfYear(TimeCurrent());
   g_dayStartBalance = AccountBalance();
   g_lastBarTime = Time[0];
   g_systemState = "SCAN";
   g_gateReasonCode = "READY";
   g_opportunityScore = 0.0;
   g_liveMinScore = MinSetupScore;
   g_candidateDirection = 0;
   g_dynamicThresholdOffset = 0.0;
   g_confidenceLotMultiplier = 1.0;
   LogDecision("EA initialized");
   DrawDashboard();
   DrawMarketOverlays();
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   SaveRuntimeState();
   CleanupDashboard();
}

void OnTick()
{
   UpdateRiskPauseRecovery();
   ManageOpenPositions();
   DrawDashboard();
   DrawMarketOverlays();

   if(IsNewBar())
   {
      // v2: once per bar, drop the risk and partial tags of tickets that have closed, so the
      // terminal's global variable list cannot grow without limit. v1 leaked its partial flags.
      if(PersistInitialRisk)
         PurgeClosedTradeTags();
      ProcessSignal();
   }
}

void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if(id != CHARTEVENT_OBJECT_CLICK)
      return;

   if(sparam == g_prefix + "BTN_SHOW")
   {
      g_dashboardVisible = true;
      g_lastReason = "dashboard shown";
      LogDecision(g_lastReason);
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_HIDE")
   {
      g_dashboardVisible = false;
      g_lastReason = "dashboard hidden";
      LogDecision(g_lastReason);
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_MODE_PRO")
   {
      g_dashboardMode = 0;
      g_lastReason = "dashboard mode: PRO";
      LogDecision(g_lastReason);
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_MODE_FOCUS")
   {
      g_dashboardMode = 1;
      g_lastReason = "dashboard mode: FOCUS";
      LogDecision(g_lastReason);
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_AUTO")
   {
      g_autoEnabledRuntime = !g_autoEnabledRuntime;
      g_lastReason = g_autoEnabledRuntime ? "auto enabled from dashboard" : "auto disabled from dashboard";
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_REG_A")
   {
      g_regimeMode = 0;
      g_lastReason = "regime mode: AUTO";
      LogDecision(g_lastReason);
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_REG_T")
   {
      g_regimeMode = 1;
      g_lastReason = "regime mode: TREND";
      LogDecision(g_lastReason);
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_REG_R")
   {
      g_regimeMode = 2;
      g_lastReason = "regime mode: RANGE";
      LogDecision(g_lastReason);
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_REG_V")
   {
      g_regimeMode = 3;
      g_lastReason = "regime mode: VOL";
      LogDecision(g_lastReason);
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_PM_SAFE" && EnableProfitModeControl)
   {
      ApplyProfitMode(0);
      g_lastReason = "profit mode: SAFE";
      LogDecision(g_lastReason);
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_PM_BAL" && EnableProfitModeControl)
   {
      ApplyProfitMode(1);
      g_lastReason = "profit mode: BALANCED";
      LogDecision(g_lastReason);
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_PM_AGG" && EnableProfitModeControl)
   {
      ApplyProfitMode(2);
      g_lastReason = "profit mode: AGGRESSIVE";
      LogDecision(g_lastReason);
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_CLOSE")
   {
      CloseAllSymbolTrades();
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_BUY")
   {
      g_allowBuyRuntime = !g_allowBuyRuntime;
      g_lastReason = g_allowBuyRuntime ? "buy side enabled" : "buy side locked";
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_SELL")
   {
      g_allowSellRuntime = !g_allowSellRuntime;
      g_lastReason = g_allowSellRuntime ? "sell side enabled" : "sell side locked";
      LogDecision(g_lastReason);
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_RESET")
   {
      ClearRiskPause("");
      g_peakEquity = AccountEquity();
      g_lastReason = "risk pause reset";
      LogDecision(g_lastReason);
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_OPEN")
   {
      int d = ResolveManualTradeDirection();
      PlaceManualTrade(d);
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_THEME")
   {
      ApplyChartVisualTheme();
      g_lastReason = "chart theme applied";
      LogDecision(g_lastReason);
      DrawDashboard();
      DrawMarketOverlays();
      return;
   }

   if(sparam == g_prefix + "BTN_CRT")
   {
      g_showCRTZone = !g_showCRTZone;
      g_lastReason = g_showCRTZone ? "CRT zone drawing ON" : "CRT zone drawing OFF";
      LogDecision(g_lastReason);
      DrawDashboard();
      DrawMarketOverlays();
      return;
   }

   if(sparam == g_prefix + "BTN_FVG")
   {
      g_showFVGZone = !g_showFVGZone;
      g_lastReason = g_showFVGZone ? "FVG drawing ON" : "FVG drawing OFF";
      LogDecision(g_lastReason);
      DrawDashboard();
      DrawMarketOverlays();
      return;
   }

   if(sparam == g_prefix + "BTN_BRK")
   {
      g_showBreakoutZone = !g_showBreakoutZone;
      g_lastReason = g_showBreakoutZone ? "Breakout zone drawing ON" : "Breakout zone drawing OFF";
      LogDecision(g_lastReason);
      DrawDashboard();
      DrawMarketOverlays();
      return;
   }

   if(sparam == g_prefix + "BTN_PRE_GOLD")
   {
      ApplyVisualPreset(1);
      g_lastReason = "preset applied: Gold Pro";
      LogDecision(g_lastReason);
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_PRE_BTC")
   {
      ApplyVisualPreset(2);
      g_lastReason = "preset applied: BTC Neon";
      LogDecision(g_lastReason);
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_PRE_MIN")
   {
      ApplyVisualPreset(3);
      g_lastReason = "preset applied: Minimal Clean";
      LogDecision(g_lastReason);
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_REC")
   {
      ApplyRecommendedVisualProfile();
      g_lastReason = "preset applied: Recommended";
      LogDecision(g_lastReason);
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_SIZE_S")
   {
      ApplySizePreset(1);
      g_lastReason = "size set: SMALL";
      LogDecision(g_lastReason);
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_SIZE_M")
   {
      ApplySizePreset(2);
      g_lastReason = "size set: MEDIUM";
      LogDecision(g_lastReason);
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_SIZE_L")
   {
      ApplySizePreset(3);
      g_lastReason = "size set: LARGE";
      LogDecision(g_lastReason);
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_SIZE_F")
   {
      ApplySizePreset(4);
      g_lastReason = "size set: FULL";
      LogDecision(g_lastReason);
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_PAUSE")
   {
      g_autoEnabledRuntime = false;
      SetRiskPause("manual pause");
      g_lastReason = "system paused";
      LogDecision(g_lastReason);
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_RESUME")
   {
      ClearRiskPause("");
      g_autoEnabledRuntime = true;
      g_lastReason = "system resumed";
      LogDecision(g_lastReason);
      DrawDashboard();
      return;
   }

   if(sparam == g_prefix + "BTN_EMERG")
   {
      CloseAllSymbolTrades();
      g_autoEnabledRuntime = false;
      SetRiskPause("emergency flat");
      g_lastReason = "emergency flat executed";
      LogDecision(g_lastReason);
      DrawDashboard();
      return;
   }
}
