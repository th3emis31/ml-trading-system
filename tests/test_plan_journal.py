"""Plan journal: armed breakout plans, outcomes from the port's backtest, learning splits, pullback trades recorded once."""
import json

import pandas as pd
import pytest

from src import plan_journal as pj
from src import volatility_trend_breakout as vtb


def rising_candles(n=320, start="2026-01-01 01:00"):
    times = pd.date_range(start, periods=n, freq="4h")
    candles = []
    for i, ts in enumerate(times):
        base = 1000 + i * 2.0
        candles.append(vtb.Candle(ts=ts.strftime("%Y-%m-%d %H:%M"), open=base, high=base + 3, low=base - 3, close=base + 1,
                                  volume=100.0))
    return candles


def test_armed_plans_carry_script_levels_and_outcomes(monkeypatch):
    candles = rising_candles()
    t = 300
    monkeypatch.setattr(pj.vtb, "generate_signals", lambda c, cfg=None: [vtb.Signal(t + 1, c[t + 1].ts, "long", 0, 0, 0, 0, 0, "x")])
    legs = [vtb.Leg(candles[t + 1].ts, candles[t + 5].ts, "long", 0, 0, 1, 10, 0.65, "tp1"),
            vtb.Leg(candles[t + 1].ts, candles[t + 9].ts, "long", 0, 0, 1, 20, 1.4, "tp2")]
    monkeypatch.setattr(pj.vtb, "backtest", lambda c, cfg=None: vtb.Result(legs=legs))
    plans = pj.breakout_plans(candles)
    assert plans and all(p["trigger"] > 0 and p["stop"] < p["trigger"] < p["tp1"] < p["tp2"] for p in plans)
    won = [p for p in plans if p["outcome"] == "win"]
    assert len(won) == 1 and won[0]["plan_candle"] == candles[t].ts and won[0]["r"] == pytest.approx(2.05)
    assert won[0]["exits"] == ["tp1", "tp2"] and won[0]["session"]
    assert plans[-1]["outcome"] == "pending", "the last closed candle's plan waits for the next candle"
    assert sum(1 for p in plans if p["outcome"] == "not_triggered") == len(plans) - 2
    cfg = vtb.Config()
    atr = vtb.atr(candles, cfg.atr_len)[t]
    upper = vtb.rolling_max([c.high for c in candles], cfg.donchian_len)[t]
    assert won[0]["trigger"] == pytest.approx(round(upper + 0.35 * atr, 2))
    assert won[0]["stop"] == pytest.approx(round(upper + 0.35 * atr - 1.5 * atr, 2))


def test_summary_and_lessons_label_small_groups_insufficient():
    plans = ([{"plan_candle": "2026-01-01 01:00", "outcome": "win", "r": 1.0, "exits": ["tp1", "tp2"], "daily_trend": "bullish",
               "rsi_band": "60-70", "session": "Asia (21-07 UTC)"}] * 30
             + [{"plan_candle": "2026-01-02 01:00", "outcome": "loss", "r": -1.0, "exits": ["stop"], "daily_trend": "bearish",
                 "rsi_band": "52-60", "session": "New York (12-21 UTC)"}] * 5
             + [{"plan_candle": "2026-01-03 01:00", "outcome": "not_triggered", "daily_trend": "mixed", "rsi_band": "52-60"}] * 65)
    stats = pj.learn(plans, "2026-01-02 00:00")
    overall = stats["all_history"]
    assert overall["armed_plans"] == 100 and overall["closed_trades"] == 35 and overall["trigger_rate_pct"] == 35.0
    assert overall["expectancy_r"] == pytest.approx(round(25 / 35, 3)) and overall["stopped_before_tp1_pct"] == pytest.approx(14.3)
    assert stats["by_daily_trend"]["bearish"]["evidence"].startswith("insufficient")
    assert [g["group"] for g in stats["lessons"]["groups"]] == ["daily trend bullish", "4H RSI 60-70"]
    assert stats["forward_since_2026-01-02 00:00"]["closed_trades"] == 5


def test_pullback_trade_is_recorded_once(tmp_path, monkeypatch):
    monkeypatch.setattr(pj, "journal_dir", lambda: tmp_path)
    plan = {"last_closed_trade": {"entry": 4300.0, "exit": 4390.0, "reason": "take profit", "opened": "2026-09-10 05:00",
                                  "closed": "2026-09-12 01:00", "result_pct": 2.09}}
    assert pj.record_pullback_trade("XAUUSD", plan)["strategy"] == "swing_trend_pullback"
    assert pj.record_pullback_trade("XAUUSD", plan) is None
    lines = (tmp_path / "xauusd_pullback_trades.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["opened"] == "2026-09-10 05:00"


def test_api_reports_not_run_yet_then_the_latest_summary(tmp_path, monkeypatch):
    import app as app_module

    monkeypatch.setattr(pj, "journal_dir", lambda: tmp_path)
    client = app_module.app.test_client()
    assert client.get("/api/plan-journal").get_json()["available"] is False
    (tmp_path / "latest.json").write_text(json.dumps({"generated_at": "2026-09-17 15:40", "symbols": {}}), encoding="utf-8")
    body = client.get("/api/plan-journal").get_json()
    assert body["available"] is True and body["generated_at"] == "2026-09-17 15:40"
