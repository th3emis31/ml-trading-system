import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

from src import paper_trader as pt
from src.data import generate_synthetic_data
from src.edge_research import INTERVAL_SPECS, load_research_frame, triple_barrier_outcomes
from src.mtf_data import resample_bars
from src.walkforward_backtest import BACKTEST_COSTS

N_BARS = 1500
TIGHT = next(c for c in INTERVAL_SPECS["4h"]["configs"] if c["name"] == "tight")


@pytest.fixture(scope="module")
def frames():
    bars = generate_synthetic_data("XAUUSD", start_date="2024-01-01", end_date="2024-12-31", n=N_BARS)
    bars = bars.reset_index(drop=True)
    if "volume" not in bars.columns:
        bars["volume"] = 0.0
    bars["datetime"] = pd.date_range("2024-01-01", periods=N_BARS, freq="4h", tz="UTC")
    return bars, resample_bars(bars, "1D")


def _state(**overrides):
    return pt.new_state({**pt.PAPER_SETUP, "name": "test", "model": "logit", **overrides})


def _closed_now(bars):
    return pd.Timestamp(bars["datetime"].iloc[-1]) + pd.Timedelta(hours=4, minutes=1)


def test_drop_forming_bars_keeps_only_closed_bars(frames):
    bars, _ = frames
    last_open = pd.Timestamp(bars["datetime"].iloc[-1])
    assert len(pt.drop_forming_bars(bars, 240, last_open + pd.Timedelta(hours=1))) == N_BARS - 1
    assert len(pt.drop_forming_bars(bars, 240, last_open + pd.Timedelta(hours=4))) == N_BARS


def test_cycle_decides_once_per_closed_bar(frames):
    bars, daily = frames
    now = _closed_now(bars)
    state = pt.run_paper_cycle(_state(), bars, daily, now=now)
    assert state["last_error"] is None
    assert len(state["decisions"]) == 1
    decision = state["decisions"][0]
    assert decision["bar_time"] == pt._iso(bars["datetime"].iloc[-1])
    assert decision["action"] in {"BUY", "SELL", "NO_TRADE"}
    assert (state["open_trade"] is not None) == (decision["action"] != "NO_TRADE")
    # Running again on the same bars must not add a second decision.
    state = pt.run_paper_cycle(state, bars, daily, now=now)
    assert len(state["decisions"]) == 1
    assert "already decided" in state["last_message"]


def test_open_trade_settles_with_the_research_exit_rules(frames):
    bars, daily = frames
    first = bars.iloc[:1400].reset_index(drop=True)
    state = pt.run_paper_cycle(_state(quantile=None, min_ev=-99.0), first, daily, now=_closed_now(first))
    opened = state["open_trade"]
    assert opened is not None and opened["signal_bar"] == pt._iso(first["datetime"].iloc[-1])

    now = _closed_now(bars)
    state = pt.run_paper_cycle(state, bars, daily, now=now)
    trade = state["closed_trades"][0]
    df, _, _, _ = load_research_frame("XAUUSD", "4h", mtf=True, data=pt.drop_forming_bars(bars, 240, now),
                                      htf_data={"1d": pt.drop_forming_bars(daily, 1440, now)})
    side = 1 if opened["side"] == "BUY" else -1
    path = triple_barrier_outcomes(df, TIGHT["sl_atr"], TIGHT["tp_atr"], TIGHT["horizon"])[side]
    t = 1399
    cost = BACKTEST_COSTS["XAUUSD"]["round_trip_pct"]
    assert trade["outcome"] == path["hit"][t]
    assert trade["gross_pct"] == round(float(path["gross"][t]) * 100, 4)
    assert trade["net_pct"] == round((float(path["gross"][t]) - cost) * 100, 4)
    assert trade["entry_price"] == round(float(df["open"].iloc[t + 1]), 3)
    assert pt.summarize(state)["closed_trades"] == 1


def test_paper_trader_has_no_order_path():
    source = Path(pt.__file__).read_text(encoding="utf-8")
    for forbidden in ("mt4_service", "mt5_service", "MetaTrader5", "order_send", "OrderSend", "place_order"):
        assert forbidden not in source
    assert pt.PAPER_SETUP["places_orders"] is False


def test_hand_to_demo_executor_forwards_only_a_signal_decided_this_run(monkeypatch):
    calls = []

    def fake_post(path, payload):
        calls.append((path, payload))
        return {"events": []} if path.endswith("sync") else {"event": "dry_run"}

    monkeypatch.setattr(pt, "_post_app", fake_post)
    state = _state()
    state["last_decision_bar"] = "2026-09-14 01:00"
    state["open_trade"] = {"signal_bar": "2026-09-14 01:00", "side": "SELL", "symbol": "XAUUSD", "atr": 12.5,
                           "sl_atr": 1.0, "tp_atr": 1.5, "bar_minutes": 240, "horizon_bars": 6, "p_win": 0.5,
                           "ev_r": 0.3, "threshold_r": 0.2}
    pt.hand_to_demo_executor(state, decided_now=False)
    assert [path for path, _ in calls] == ["/api/demo-model/sync"]
    pt.hand_to_demo_executor(state, decided_now=True)
    path, signal = calls[-1]
    assert path == "/api/demo-model/execute"
    assert signal["side"] == "SELL" and signal["bar_time"] == "2026-09-14 01:00" and signal["atr"] == 12.5
    assert state["open_trade"]["demo_execution"] == {"event": "dry_run"}
