import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

from src import performance_analytics as pa


def _hourly(n=24 * 7 * 12, seed=1):
    rng = np.random.default_rng(seed)
    times = pd.date_range("2026-01-05", periods=n, freq="h", tz="UTC")  # starts on a Monday
    open_ = 100 + np.cumsum(rng.normal(0, 0.2, n))
    drift = np.where(times.hour == 14, 0.5, 0.0)  # a planted 14:00 up-move
    close = open_ + rng.normal(0, 0.1, n) + drift
    high = np.maximum(open_, close) + 0.05
    low = np.minimum(open_, close) - 0.05
    return pd.DataFrame({"datetime": times, "open": open_, "high": high, "low": low, "close": close})


def test_market_heatmap_finds_a_planted_hour_and_reports_counts():
    result = pa.market_heatmaps(_hourly())
    assert result["available"]
    grid = result["hour_weekday"]["avg_return_pct"]
    assert grid["rows"][0] == "Mon" and grid["columns"] == list(range(24))
    assert result["rankings"]["strongest_up_hours"][0]["hour"] == 14
    assert all(count == 12 for count in grid["counts"][0])  # 12 Mondays per hour cell
    json.dumps(result)


def test_year_month_returns_from_daily_bars():
    times = pd.date_range("2024-01-01", "2025-12-31", freq="D", tz="UTC")
    daily = pd.DataFrame({"datetime": times, "open": 1.0, "high": 1.0, "low": 1.0,
                          "close": np.linspace(100, 200, len(times))})
    result = pa.market_heatmaps(None, daily)
    assert result["year_month"]["rows"] == [2025, 2024]
    assert all(v is None or v > 0 for row in result["year_month"]["values"] for v in row)
    assert [y["year"] for y in result["yearly_return_pct"]] == [2025]


def test_trading_heatmaps_equity_drawdown_and_magic_filter():
    deals = [
        {"time": "2026-09-01 09:10", "profit": 10.0, "swap": 0, "commission": -1, "magic": 1, "symbol": "XAUUSD"},
        {"time": "2026-09-01 09:40", "profit": -20.0, "magic": 1, "symbol": "XAUUSD"},
        {"time": "2026-09-02 15:00", "profit": 30.0, "magic": 2, "symbol": "BTCUSD"},
    ]
    result = pa.trading_heatmaps(deals)
    assert result["trades"] == 3 and result["net"] == pytest.approx(19.0)
    assert result["max_drawdown"] == pytest.approx(-20.0)
    assert result["profit_factor"] == pytest.approx(39 / 20, rel=1e-3)
    rows = result["hour_weekday"]["net"]["rows"]
    assert rows == ["Tue", "Wed"]  # 1 Sep 2026 is a Tuesday, 2 Sep a Wednesday
    assert result["hour_weekday"]["net"]["values"][rows.index("Tue")][9] == pytest.approx(-11.0)
    assert result["hour_weekday"]["net"]["values"][rows.index("Wed")][15] == pytest.approx(30.0)
    assert result["hour_weekday"]["net"]["counts"][rows.index("Tue")][9] == 2
    only_one = pa.trading_heatmaps(deals, magic=1)
    assert only_one["trades"] == 2 and {m["magic"] for m in only_one["by_magic"]} == {1}
    assert pa.trading_heatmaps([])["available"] is False
    json.dumps(result)


def test_trackers():
    quality = pa.quality_tracker([{"generated_at": "2026-09-13 13:00:00", "overall": "healthy", "counts": {"ok": 11}},
                                  {"generated_at": "2026-09-13 13:30:00", "overall": "warnings", "counts": {"ok": 9, "warn": 2}}])
    assert [p["score"] for p in quality] == [100, 60]
    learning = pa.learning_tracker([{"trained_at": "2026-09-13T11:00:00", "symbol": "XAUUSD", "accuracy": 0.51, "lstm_accuracy": 0.54}])
    assert learning[0]["rf_accuracy"] == 0.51
    research = pa.research_tracker([{"generated_at": "2026-09-13 13:46", "system": {"strategy_lab": {"markets": {
        "XAUUSD:4h": {"evaluated": 10, "validated": 3, "holdout_passed": 0}, "BTCUSD:4h": {"evaluated": 5, "validated": 1}}}}}])
    assert research[0]["evaluated"] == 15 and research[0]["validated"] == 4
    paper = pa.paper_tracker([{"exit_time": "a", "net_pct": 10}, {"exit_time": "b", "net_pct": -10}])
    assert paper[-1]["equity_pct"] == pytest.approx(-1.0)


def test_trading_heatmaps_can_keep_only_this_systems_own_experts():
    """The account carries other people's experts. Mixing their trades with these makes the page unable to answer
    "what did this system earn?", so the filter takes a list and the page asks for it by default."""
    deals = [
        {"time": 1789000000, "net": 10.0, "symbol": "XAUUSD", "magic": 440502},   # gold session pullback
        {"time": 1789003600, "net": -4.0, "symbol": "XAUUSD", "magic": 440603},   # volatility breakout
        {"time": 1789007200, "net": 500.0, "symbol": "XAUUSD", "magic": 888888},  # someone else's expert
        {"time": 1789010800, "net": 250.0, "symbol": "XAUUSD", "magic": 0},       # a manual trade
    ]
    mine = pa.trading_heatmaps(deals, magic=list(pa.SYSTEM_MAGICS))
    assert mine["available"] and mine["trades"] == 2
    assert round(mine["net"], 2) == 6.0, "only this system's two strategies count"

    everything = pa.trading_heatmaps(deals)
    assert everything["trades"] == 4 and round(everything["net"], 2) == 756.0, "no filter still means the whole account"

    one = pa.trading_heatmaps(deals, magic=440502)
    assert one["trades"] == 1 and round(one["net"], 2) == 10.0, "a single number still works"
    assert pa.trading_heatmaps(deals, magic=[999999])["available"] is False, "an expert with no trades says so"


def _deal(day, net, magic=440603, symbol="BTCUSD"):
    return {"time": f"2026-09-{day:02d}T12:00:00Z", "net": net, "magic": magic, "symbol": symbol}


def test_growth_tracker_reports_won_and_lost_separately_not_just_the_net():
    """A month that made 500 and lost 480 nets 20, and so does one that made 30 and lost 10. They are
    not the same month, and a single net figure hides which one you had."""
    deals = [_deal(1, 500.0), _deal(2, -480.0)]
    out = pa.growth_tracker(deals)
    t = out["totals"]
    assert t["won"] == 500.0 and t["lost"] == 480.0 and t["net"] == 20.0
    assert t["wins"] == 1 and t["losses"] == 1 and t["win_rate"] == 0.5


def test_growth_tracker_buckets_by_day_week_month_and_year():
    out = pa.growth_tracker([_deal(1, 10.0), _deal(2, -4.0), _deal(9, 6.0)])
    assert set(out["periods"]) == {"daily", "weekly", "monthly", "yearly"}
    assert len(out["periods"]["daily"]) == 3, "three separate days"
    assert len(out["periods"]["monthly"]) == 1, "all in September"
    assert out["periods"]["yearly"][0]["net"] == 12.0
    assert out["periods"]["monthly"][0]["period"] == "2026-09"


def test_a_running_balance_is_only_shown_when_a_start_is_given():
    """Inventing a starting balance would turn a P/L series into a fake account curve."""
    plain = pa.growth_tracker([_deal(1, 10.0)])
    assert plain["periods"]["daily"][0]["balance"] is None
    started = pa.growth_tracker([_deal(1, 10.0), _deal(2, -3.0)], starting_balance=300.0)
    assert [r["balance"] for r in started["periods"]["daily"]] == [310.0, 307.0]


def test_break_even_trades_do_not_dilute_the_win_rate():
    """A zero-net trade is neither a win nor a loss; counting it as a loss understates the rate."""
    out = pa.growth_tracker([_deal(1, 5.0), _deal(2, 0.0), _deal(3, -5.0)])
    t = out["totals"]
    assert t["trades"] == 3 and t["wins"] == 1 and t["losses"] == 1
    assert t["win_rate"] == 0.5, "decided trades only"


def test_growth_splits_by_symbol_and_by_strategy():
    out = pa.growth_tracker([_deal(1, 10.0, magic=440603, symbol="BTCUSD"),
                             _deal(1, -2.0, magic=440502, symbol="XAUUSD")])
    assert out["by_symbol"]["BTCUSD"]["net"] == 10.0
    assert out["by_strategy"]["440502"]["net"] == -2.0


def test_open_positions_are_not_counted_as_growth():
    """Only closed deals arrive here. Floating profit makes a losing run look like a winning one
    right up until it closes, so an empty set must say so rather than invent a zero."""
    assert pa.growth_tracker([])["available"] is False


def test_every_strategy_magic_is_counted_as_this_system():
    """SYSTEM_MAGICS listed only two for a long time, so the page's "what did this system earn?"
    filter silently excluded four of the six - the sweep, the plan executor, the model executor and
    the auto-trade route."""
    for magic in (440401, 440502, 440603, 440704, 440805, 903110):
        assert magic in pa.SYSTEM_MAGICS
