"""Plan journal: the system learns from its own chart plans (owner request 2026-09-17: "the system to learn as well").

The TradingView SmartEntry Market Map draws two plans per asset from closed candles. This module records the same plans
from the broker's candles and settles them, so the system can see how its plans actually play out:

* Volatility Trend Breakout (src/volatility_trend_breakout.py, the owner's Pine rules): every closed 4H candle on which
  the breakout was ARMED (close above EMA50 and RSI above 52) is a plan for the next candle - trigger = previous 20-bar
  high + 0.35 x ATR, stop 1.5 ATR below, TP1 1.3R, TP2 2.8R. Outcome: whether the next candle closed above the trigger
  with the volume filter (a signal), and the result of that trade from the port's own backtest (TP1, TP2, stop, time
  exit, R per position). Plans dated on or after the journal's first run are marked forward: live evidence rather than
  history.
* Swing Trend Pullback (src/tradingview_plan.py): each newly closed plan trade is appended going forward.

What it learns: trigger rate, win rate and expectancy in R per asset, split by daily trend, 4H RSI band, trigger
session and year; any group under 30 closed trades is labelled insufficient. It changes no strategy, input or order.

Run: python -m src.plan_journal   (hourly task SmartEntry Plan Journal; reads broker bars through the app)
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from . import demo_executor, volatility_trend_breakout as vtb
from .event_defence import utc_timestamp
from .runtime_paths import read_latest_json, smartentry_data_dir

SYMBOLS = ("XAUUSD", "BTCUSD")
MIN_EVIDENCE = 30
KEEP_PLANS = 400


def journal_dir() -> Path:
    return smartentry_data_dir() / "plan_journal"


def forward_start() -> str:
    """The first run's time; plans from then on are forward (live) evidence."""
    meta = journal_dir() / "meta.json"
    if meta.exists():
        return json.loads(meta.read_text(encoding="utf-8"))["forward_start"]
    meta.parent.mkdir(parents=True, exist_ok=True)
    start = demo_executor._stamp(utc_timestamp())[:16]
    meta.write_text(json.dumps({"forward_start": start}, indent=1), encoding="utf-8")
    return start


def _rsi_band(rsi: Optional[float]) -> str:
    if rsi is None:
        return "unknown"
    return "52-60" if rsi < 60 else "60-70" if rsi < 70 else "70+"


def _session(ts: str) -> str:
    hour = int(ts[11:13])
    return "Asia (21-07 UTC)" if hour >= 21 or hour < 7 else "London (07-12 UTC)" if hour < 12 else "New York (12-21 UTC)"


def _daily_trend_lookup(daily: Optional[pd.DataFrame]) -> Callable[[str], str]:
    """Daily EMA50/200 trend of the last daily candle closed at or before a 4H candle's close."""
    if daily is None or daily.empty:
        return lambda ts: "unknown"
    d = daily.sort_values("datetime").reset_index(drop=True)
    close = d["close"].astype(float)
    e50, e200 = close.ewm(span=50, adjust=False).mean(), close.ewm(span=200, adjust=False).mean()
    labels = ["bullish" if c > a > b else "bearish" if c < a < b else "mixed" for c, a, b in zip(close, e50, e200)]
    closes = (pd.to_datetime(d["datetime"], utc=True) + pd.Timedelta(days=1)).dt.tz_convert(None).to_numpy(dtype="datetime64[ns]")

    def lookup(ts: str) -> str:
        candle_close = (pd.Timestamp(ts) + pd.Timedelta(hours=4)).to_datetime64()
        i = int(closes.searchsorted(candle_close, side="right")) - 1
        return labels[i] if i >= 200 else "unknown"
    return lookup


def breakout_plans(candles: list, daily: Optional[pd.DataFrame] = None, cfg: vtb.Config = vtb.Config()) -> list[dict]:
    """Every armed breakout plan with its outcome, oldest first."""
    if len(candles) < 250:
        return []
    closes = [c.close for c in candles]
    ema = vtb.ema(closes, cfg.ema_len)
    atr = vtb.atr(candles, cfg.atr_len)
    rsi = vtb.rsi(closes, cfg.rsi_len)
    upper = vtb.rolling_max([c.high for c in candles], cfg.donchian_len)
    signals = {s.index for s in vtb.generate_signals(candles, cfg)}
    result = vtb.backtest(candles, cfg)
    trades: dict[str, dict] = {}
    for leg in result.legs:
        trade = trades.setdefault(leg.entry_ts, {"r": 0.0, "exits": [], "closed_ts": leg.exit_ts})
        trade["r"] += leg.r_multiple
        trade["exits"].append(leg.kind)
        trade["closed_ts"] = max(trade["closed_ts"], leg.exit_ts)
    trend_at = _daily_trend_lookup(daily)
    plans = []
    for t in range(cfg.ema_len, len(candles)):
        if None in (ema[t], atr[t], rsi[t], upper[t]) or not (closes[t] > ema[t] and rsi[t] > cfg.rsi_min):
            continue
        trigger = upper[t] + cfg.atr_mult * 0.25 * atr[t]
        risk = cfg.sl_atr_mult * atr[t]
        plan = {"plan_candle": candles[t].ts, "trigger": round(trigger, 2), "stop": round(trigger - risk, 2),
                "tp1": round(trigger + cfg.tp1_r * risk, 2), "tp2": round(trigger + cfg.tp2_r * risk, 2),
                "atr": round(atr[t], 3), "rsi_band": _rsi_band(rsi[t]), "daily_trend": trend_at(candles[t].ts),
                "gap_to_trigger_atr": round((trigger - closes[t]) / atr[t], 2)}
        if t + 1 >= len(candles):
            plan["outcome"] = "pending"
        elif t + 1 not in signals:
            plan["outcome"] = "not_triggered"
        else:
            trigger_ts = candles[t + 1].ts
            plan["trigger_candle"], plan["session"] = trigger_ts, _session(trigger_ts)
            trade = trades.get(trigger_ts)
            if trade is None:
                plan["outcome"] = "triggered_while_in_trade"
            elif "eod" in trade["exits"]:
                plan.update(outcome="open", r_so_far=round(trade["r"], 3))
            else:
                plan.update(outcome="win" if trade["r"] > 0 else "loss", r=round(trade["r"], 3), exits=trade["exits"],
                            closed=trade["closed_ts"])
        plans.append(plan)
    return plans


def summarise(plans: list[dict]) -> dict:
    closed = [p for p in plans if p["outcome"] in ("win", "loss")]
    decided = [p for p in plans if p["outcome"] != "pending"]
    triggered = [p for p in decided if p["outcome"] not in ("not_triggered",)]
    r = [p["r"] for p in closed]
    return {
        "armed_plans": len(decided),
        "triggered": len(triggered),
        "trigger_rate_pct": round(100 * len(triggered) / len(decided), 1) if decided else None,
        "closed_trades": len(closed),
        "win_rate_pct": round(100 * sum(1 for x in r if x > 0) / len(r), 1) if r else None,
        "expectancy_r": round(sum(r) / len(r), 3) if r else None,
        "total_r": round(sum(r), 2),
        "tp1_reached_pct": round(100 * sum(1 for p in closed if "tp1" in p["exits"]) / len(closed), 1) if closed else None,
        "tp2_reached_pct": round(100 * sum(1 for p in closed if "tp2" in p["exits"]) / len(closed), 1) if closed else None,
        "stopped_before_tp1_pct": round(100 * sum(1 for p in closed if p["exits"] and p["exits"][0] == "stop") / len(closed), 1)
        if closed else None,
        "evidence": "sufficient" if len(closed) >= MIN_EVIDENCE else f"insufficient (under {MIN_EVIDENCE} closed trades)",
    }


def learn(plans: list[dict], forward_from: str) -> dict:
    """Stats overall, forward only, last 12 months, and by daily trend / RSI band / session / year."""
    def group(key: Callable[[dict], str]) -> dict:
        buckets: dict[str, list] = defaultdict(list)
        for p in plans:
            buckets[key(p)].append(p)
        return {k: summarise(v) for k, v in sorted(buckets.items())}

    last_year = demo_executor._stamp(utc_timestamp(pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=365)))[:16]
    by_trend = group(lambda p: p["daily_trend"])
    lessons = []
    for name, stats in {**{f"daily trend {k}": v for k, v in by_trend.items()},
                        **{f"4H RSI {k}": v for k, v in group(lambda p: p["rsi_band"]).items()}}.items():
        if stats["closed_trades"] >= MIN_EVIDENCE and stats["expectancy_r"] is not None:
            lessons.append({"group": name, "closed_trades": stats["closed_trades"], "expectancy_r": stats["expectancy_r"],
                            "win_rate_pct": stats["win_rate_pct"]})
    lessons.sort(key=lambda x: x["expectancy_r"], reverse=True)
    return {
        "all_history": summarise(plans),
        "forward_since_" + forward_from: summarise([p for p in plans if p["plan_candle"] >= forward_from]),
        "last_12_months": summarise([p for p in plans if p["plan_candle"] >= last_year]),
        "by_daily_trend": by_trend,
        "by_rsi_band": group(lambda p: p["rsi_band"]),
        "by_trigger_session": group(lambda p: p.get("session", "not triggered")),
        "by_year": group(lambda p: p["plan_candle"][:4]),
        "lessons": {"note": f"Groups with at least {MIN_EVIDENCE} closed trades, best expectancy first. Descriptive history, "
                            "not a validated filter: a group chosen from this table must be confirmed on later data.",
                    "groups": lessons},
    }


def record_pullback_trade(symbol: str, plan: dict) -> Optional[dict]:
    """Append the system plan's last closed pullback trade once (keyed by its open time)."""
    trade = (plan or {}).get("last_closed_trade")
    if not trade:
        return None
    path = journal_dir() / f"{symbol.lower()}_pullback_trades.jsonl"
    seen = set()
    if path.exists():
        seen = {json.loads(line).get("opened") for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
    if trade["opened"] in seen:
        return None
    record = {"recorded_at": demo_executor._stamp(utc_timestamp())[:16], "symbol": symbol, "strategy": "swing_trend_pullback", **trade}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    return record


def candles_from_frame(frame: pd.DataFrame) -> list:
    rows = frame.sort_values("datetime").reset_index(drop=True)
    return [vtb.Candle(ts=pd.Timestamp(r.datetime).strftime("%Y-%m-%d %H:%M"), open=float(r.open), high=float(r.high),
                       low=float(r.low), close=float(r.close), volume=float(r.volume)) for r in rows.itertuples(index=False)]


def run_journal(load_bars: Callable[[str, str, int], pd.DataFrame], now=None) -> dict:
    """Rebuild each asset's breakout journal from closed broker candles and record new pullback trades."""
    from .paper_trader import drop_forming_bars
    from .tradingview_plan import build_daily_plan

    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    start = forward_start()
    summary = {"generated_at": demo_executor._stamp(utc_timestamp(now))[:16], "forward_start": start, "symbols": {}}
    for symbol in SYMBOLS:
        h4 = load_bars(symbol, "4h", 50000)
        daily = load_bars(symbol, "1d", 6000)
        if h4 is None or h4.empty:
            summary["symbols"][symbol] = {"available": False, "reason": "no broker 4H bars"}
            continue
        h4 = drop_forming_bars(h4.assign(datetime=pd.to_datetime(h4["datetime"], utc=True)), 240, now)
        daily = None if daily is None or daily.empty else drop_forming_bars(
            daily.assign(datetime=pd.to_datetime(daily["datetime"], utc=True)), 1440, now)
        plans = breakout_plans(candles_from_frame(h4), daily)
        stats = learn(plans, start)
        latest = plans[-1] if plans and plans[-1]["outcome"] == "pending" else None
        out = {"symbol": symbol, "generated_at": demo_executor._stamp(utc_timestamp(now))[:16], "bars": len(h4), "first_candle": demo_executor._stamp(utc_timestamp(h4["datetime"].iloc[0]))[:16],
               "last_closed_candle": demo_executor._stamp(utc_timestamp(h4["datetime"].iloc[-1]))[:16], "forward_start": start,
               "armed_now": latest, "stats": stats, "recent_plans": plans[-KEEP_PLANS:]}
        path = journal_dir() / f"{symbol.lower()}_breakout.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, indent=1), encoding="utf-8")
        pullback_recorded = None
        if daily is not None and len(h4) >= 300 and len(daily) >= 60:
            try:
                pullback_recorded = record_pullback_trade(symbol, build_daily_plan(h4, daily, symbol, now=now))
            except Exception as exc:  # the pullback part is optional; the breakout journal stands on its own
                pullback_recorded = {"error": str(exc)}
        summary["symbols"][symbol] = {"available": True, "armed_now": latest, "all_history": stats["all_history"],
                                      "last_12_months": stats["last_12_months"], "lessons": stats["lessons"]["groups"][:3],
                                      "pullback_trade_recorded": pullback_recorded}
    (journal_dir() / "latest.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary


def read_latest() -> dict:
    return read_latest_json(journal_dir() / "latest.json",
                            "the plan journal has not run yet (task SmartEntry Plan Journal)")


if __name__ == "__main__":
    from .mtf_data import fetch_app_bars

    result = run_journal(lambda symbol, tf, count: fetch_app_bars(symbol, tf, count))
    for symbol, info in result["symbols"].items():
        if not info.get("available"):
            print(symbol, "unavailable:", info.get("reason"))
            continue
        a, y = info["all_history"], info["last_12_months"]
        armed = info["armed_now"]
        print(f"{symbol}: all history {a['closed_trades']} trades, win {a['win_rate_pct']}%, exp {a['expectancy_r']}R | "
              f"last 12 months {y['closed_trades']} trades, exp {y['expectancy_r']}R | "
              f"armed now: {('trigger ' + str(armed['trigger'])) if armed else 'no'}")
