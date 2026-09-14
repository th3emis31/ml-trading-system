from __future__ import annotations

from datetime import datetime, timezone


class TradingCodingAssistant:
    """Generates starter MQL5 and Pine Script snippets for trading workflows."""

    def __init__(self):
        self.default_symbol = "XAUUSD"
        self.default_timeframe = "H1"

    def _now(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def supported(self) -> dict:
        return {
            "languages": ["mql5", "pine"],
            "templates": [
                "signal_strategy",
                "trend_follower",
                "range_scalper",
                "multi_timeframe",
                "risk_manager",
                "session_filter",
            ],
            "generated_at": self._now(),
        }

    def generate(self, language: str, template: str, symbol: str = "", timeframe: str = "") -> dict:
        lang = str(language or "mql5").strip().lower()
        kind = str(template or "signal_strategy").strip().lower()
        sym = str(symbol or self.default_symbol).strip().upper()
        tf = str(timeframe or self.default_timeframe).strip().upper()

        if lang not in {"mql5", "pine"}:
            return {"error": "unsupported language", "language": lang}

        if kind not in {"signal_strategy", "trend_follower", "range_scalper", "multi_timeframe", "risk_manager", "session_filter"}:
            return {"error": "unsupported template", "template": kind}

        if lang == "mql5":
            code = self._mql5_template(kind, sym, tf)
        else:
            code = self._pine_template(kind, sym, tf)

        return {
            "language": lang,
            "template": kind,
            "symbol": sym,
            "timeframe": tf,
            "code": code,
            "generated_at": self._now(),
        }

    def explain(self, code: str) -> dict:
        text = str(code or "")
        if not text.strip():
            return {"error": "code is required"}

        lines = [line.rstrip() for line in text.splitlines() if line.strip()]
        line_count = len(lines)
        has_entry = any(token in text.lower() for token in ["buy", "sell", "strategy.entry", "order_send", "order send"])
        has_risk = any(token in text.lower() for token in ["stop", "sl", "take", "tp", "risk"])

        summary = [
            f"Code has {line_count} non-empty lines.",
            "Contains entry logic." if has_entry else "Entry logic not detected.",
            "Contains risk/exit logic." if has_risk else "Risk/exit logic not detected.",
            "Review broker digits/point size and session timezone before live execution.",
        ]
        return {
            "summary": " ".join(summary),
            "line_count": line_count,
            "has_entry_logic": has_entry,
            "has_risk_logic": has_risk,
            "generated_at": self._now(),
        }

    def _mql5_template(self, template: str, symbol: str, timeframe: str) -> str:
        if template == "trend_follower":
            return f"""// MQL5 - Trend Follower Strategy for {symbol} {timeframe}
#property strict
input int RSIPeriod = 14;
input double RiskPercent = 1.0;
input int StopLossPips = 150;
input int TakeProfitPips = 300;

double GetRSI() {{ return iRSI(_Symbol, _Period, RSIPeriod, PRICE_CLOSE, 0); }}

void OnTick() {{
   double rsi = GetRSI();
   double ma20 = iMA(_Symbol, _Period, 20, 0, MODE_SMA, PRICE_CLOSE, 0);
   double ma50 = iMA(_Symbol, _Period, 50, 0, MODE_SMA, PRICE_CLOSE, 0);
   
   // Trend confirmation: price above MA50, MA50 above MA200, RSI > 50
   if(Close[0] > ma20 && ma20 > ma50 && rsi > 50) {{
      // LONG: Buy on trend continuation
      // OrderSend with SL={StopLossPips} pips, TP={TakeProfitPips} pips
   }}
   
   if(Close[0] < ma20 && ma20 < ma50 && rsi < 50) {{
      // SHORT: Sell on trend continuation
   }}
}}
"""
        
        if template == "range_scalper":
            return f"""// MQL5 - Range Scalper for {symbol} {timeframe}
#property strict
input int RangePips = 50;
input int StopLossPips = 40;
input int TakeProfitPips = 60;
input double RiskPercent = 0.5;

void OnTick() {{
   double high = iHigh(_Symbol, _Period, 0);
   double low = iLow(_Symbol, _Period, 0);
   double mid = (high + low) / 2.0;
   double range = (high - low) / _Point;
   
   if(range > RangePips) {{
      // Range detected, scalp towards middle
      if(Close[0] > mid) {{
         // Short scalp towards support
         // OrderSend SELL, SL={StopLossPips}, TP={TakeProfitPips}
      }}
      if(Close[0] < mid) {{
         // Long scalp towards resistance
         // OrderSend BUY, SL={StopLossPips}, TP={TakeProfitPips}
      }}
   }}
}}
"""
        
        if template == "multi_timeframe":
            return f"""// MQL5 - Multi-Timeframe Strategy for {symbol}
#property strict
input int FastPeriod = 20;
input int SlowPeriod = 50;
input double RiskPercent = 1.0;

double GetEMA(int period, int timeframe) {{
   return iMA(_Symbol, timeframe, period, 0, MODE_EMA, PRICE_CLOSE, 0);
}}

void OnTick() {{
   // H4 trend direction (slow)
   double h4_fast = GetEMA(FastPeriod, PERIOD_H4);
   double h4_slow = GetEMA(SlowPeriod, PERIOD_H4);
   
   // H1 confirmation (medium)
   double h1_fast = GetEMA(FastPeriod, PERIOD_H1);
   double h1_slow = GetEMA(SlowPeriod, PERIOD_H1);
   
   // M15 entry timing (fast)
   double m15_fast = GetEMA(FastPeriod, PERIOD_M15);
   double m15_slow = GetEMA(SlowPeriod, PERIOD_M15);
   
   // Confluence: All timeframes aligned bullish
   if(h4_fast > h4_slow && h1_fast > h1_slow && m15_fast > m15_slow) {{
      // HIGH CONFIDENCE BUY
   }}
   
   if(h4_fast < h4_slow && h1_fast < h1_slow && m15_fast < m15_slow) {{
      // HIGH CONFIDENCE SELL
   }}
}}
"""
        
        if template == "risk_manager":
            return f"""// Auto-generated MQL5 risk manager template
#property strict
input double RiskPercent = 1.0;
input double StopLossPips = 120;
input double TakeProfitPips = 240;

double CalculateLot(double balance, double riskPercent, double slPips) {{
   double riskMoney = balance * (riskPercent / 100.0);
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickValue <= 0 || tickSize <= 0 || slPips <= 0) return 0.01;
   double lot = riskMoney / ((slPips * _Point / tickSize) * tickValue);
   return MathMax(0.01, NormalizeDouble(lot, 2));
}}

void OnTick() {{
   // Add entry signal condition before placing orders.
   double lot = CalculateLot(AccountInfoDouble(ACCOUNT_BALANCE), RiskPercent, StopLossPips);
}}
"""

        if template == "session_filter":
            return f"""// Auto-generated MQL5 session filter template
#property strict
input int SessionStartHour = 7;
input int SessionEndHour = 17;

bool IsSessionOpen() {{
   datetime now = TimeCurrent();
   int hour = TimeHour(now);
   return (hour >= SessionStartHour && hour <= SessionEndHour);
}}

void OnTick() {{
   if(!IsSessionOpen()) return;
   // Add signal logic for {symbol} {timeframe}.
}}
"""

        return f"""// Auto-generated MQL5 signal strategy template
#property strict
input int FastMA = 20;
input int SlowMA = 50;
input double StopLossPips = 120;
input double TakeProfitPips = 240;

int OnInit() {{
   return(INIT_SUCCEEDED);
}}

void OnTick() {{
   double fast = iMA(_Symbol, PERIOD_{timeframe}, FastMA, 0, MODE_EMA, PRICE_CLOSE);
   double slow = iMA(_Symbol, PERIOD_{timeframe}, SlowMA, 0, MODE_EMA, PRICE_CLOSE);

   if(fast > slow) {{
      // BUY logic placeholder for {symbol}
   }}
   else if(fast < slow) {{
      // SELL logic placeholder for {symbol}
   }}
}}
"""

    def _pine_template(self, template: str, symbol: str, timeframe: str) -> str:
        if template == "trend_follower":
            return f"""//@version=5
strategy("Trend Follower - {symbol} {timeframe}", overlay=true, initial_capital=10000)
rsi = ta.rsi(close, 14)
ma20 = ta.sma(close, 20)
ma50 = ta.sma(close, 50)

// Uptrend: price > MA20 > MA50, RSI > 50
long = close > ma20 and ma20 > ma50 and rsi > 50
short = close < ma20 and ma20 < ma50 and rsi < 50

if long
    strategy.entry("Long", strategy.long, stop=close - 150*syminfo.mintick, limit=close + 300*syminfo.mintick)
if short
    strategy.entry("Short", strategy.short, stop=close + 150*syminfo.mintick, limit=close - 300*syminfo.mintick)

plot(ma20, "MA20", color.blue)
plot(ma50, "MA50", color.red)
"""
        
        if template == "range_scalper":
            return f"""//@version=5
strategy("Range Scalper - {symbol}", overlay=true)
rangePips = input.int(50, "Range Size Pips")
slPips = input.int(40, "Stop Loss Pips")
tpPips = input.int(60, "Take Profit Pips")

high = ta.highest(high, 20)
low = ta.lowest(low, 20)
mid = (high + low) / 2

range = (high - low) / syminfo.mintick

if range > rangePips
    if close > mid
        strategy.entry("Scalp-Short", strategy.short, stop=close + slPips*syminfo.mintick, limit=close - tpPips*syminfo.mintick)
    if close < mid
        strategy.entry("Scalp-Long", strategy.long, stop=close - slPips*syminfo.mintick, limit=close + tpPips*syminfo.mintick)

plot(high, "High", color.red)
plot(low, "Low", color.blue)
plot(mid, "Mid", color.gray)
"""
        
        if template == "multi_timeframe":
            return f"""//@version=5
strategy("Multi-TF Confluence - {symbol}", overlay=true)
fast_len = input.int(20, "Fast EMA")
slow_len = input.int(50, "Slow EMA")

// Get indicators from different timeframes
h4_fast = request.security(syminfo.tickerid, "240", ta.ema(close, fast_len))
h4_slow = request.security(syminfo.tickerid, "240", ta.ema(close, slow_len))
h1_fast = request.security(syminfo.tickerid, "60", ta.ema(close, fast_len))
h1_slow = request.security(syminfo.tickerid, "60", ta.ema(close, slow_len))
m15_fast = ta.ema(close, fast_len)
m15_slow = ta.ema(close, slow_len)

// Confluence: All TFs bullish
bullish = h4_fast > h4_slow and h1_fast > h1_slow and m15_fast > m15_slow
bearish = h4_fast < h4_slow and h1_fast < h1_slow and m15_fast < m15_slow

if bullish
    strategy.entry("Long", strategy.long)
if bearish
    strategy.entry("Short", strategy.short)
"""
        
        if template == "risk_manager":
            return f"""//@version=5
strategy("Risk Manager Template", overlay=true, initial_capital=10000)
riskPct = input.float(1.0, "Risk %", step=0.1)
slAtr = input.float(1.5, "SL ATR Mult")
tpAtr = input.float(3.0, "TP ATR Mult")
atrVal = ta.atr(14)

longSignal = ta.crossover(ta.ema(close, 20), ta.ema(close, 50))
if longSignal
    strategy.entry("L", strategy.long)
    strategy.exit("L-Exit", "L", stop=close - atrVal * slAtr, limit=close + atrVal * tpAtr)
"""

        if template == "session_filter":
            return f"""//@version=5
strategy("Session Filter Template", overlay=true)
sessionInput = input.session("0700-1700", "Trading Session")
inSession = not na(time(timeframe.period, sessionInput))
fast = ta.ema(close, 20)
slow = ta.ema(close, 50)

if inSession and ta.crossover(fast, slow)
    strategy.entry("Buy", strategy.long)
if inSession and ta.crossunder(fast, slow)
    strategy.entry("Sell", strategy.short)
"""

        return f"""//@version=5
strategy("Signal Strategy Template", overlay=true)
fastLen = input.int(20, "Fast EMA")
slowLen = input.int(50, "Slow EMA")
fast = ta.ema(close, fastLen)
slow = ta.ema(close, slowLen)
plot(fast, color=color.new(color.teal, 0))
plot(slow, color=color.new(color.orange, 0))

longSignal = ta.crossover(fast, slow)
shortSignal = ta.crossunder(fast, slow)

if longSignal
    strategy.entry("Long", strategy.long)
if shortSignal
    strategy.entry("Short", strategy.short)

// Market focus: {symbol} {timeframe}
"""
