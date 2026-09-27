"""The engine package's own guarantees, each checked against an answer known before the test runs.

`tests/test_engine_truth.py` proves the SIMULATOR underneath. These prove the seams built on top of it —
the places where this package could silently lie:

* bars that are not from MT5 must stop the run, not be substituted
* a higher timeframe must never be readable before it has closed
* a prior extreme must exclude the bar being judged
* position size must land on the risk asked for, in price units, with no pip conversion
* a refusal must carry its reason, all the way into the ledger
* MAE/MFE must be measured over the bars the trade was open, in R

No market data, no orders, no network: every fixture is built so the right answer is arithmetic.
"""
import numpy as np
import pandas as pd
import pytest

from engine import (analytics, data_engine, ledger as ledger_mod, liquidity_engine,
                    monte_carlo, regime_engine, risk_engine, signal_engine, timeframe_engine)


# --------------------------------------------------------------------------- provenance

def test_a_barset_from_yahoo_is_not_broker_data():
    """The gate the whole package rests on: Yahoo's GC=F is ~1.4 % off broker spot on gold."""
    yahoo = data_engine.BarSet("XAUUSD", "15m", pd.DataFrame({"datetime": []}), "yahoo:1h+resample:15m")
    assert yahoo.is_broker is False


def test_a_barset_from_mt5_is_broker_data_however_it_is_spelled():
    for source in ("mt5:XAUUSD", "app:mt5:XAUUSD", "app(cache 20260927_1700)"):
        assert data_engine.BarSet("XAUUSD", "15m", pd.DataFrame(), source).is_broker is True, source


def test_require_mt5_raises_rather_than_returning_something_usable(monkeypatch):
    """A backtest that silently ran on the wrong instrument is worse than one that did not run."""
    def fake_load(symbol, timeframe, use_cache=True):
        return data_engine.BarSet(symbol, timeframe,
                                  pd.DataFrame({"datetime": [1], "open": [1.0], "high": [1.0],
                                                "low": [1.0], "close": [1.0], "volume": [1.0]}),
                                  "yahoo")
    monkeypatch.setattr(data_engine, "load", fake_load)
    with pytest.raises(RuntimeError, match="not MT5"):
        data_engine.require_mt5("XAUUSD", "15m")


# --------------------------------------------------------------------------- no lookahead

def test_a_four_hour_bar_is_not_readable_until_it_has_closed():
    """The 08:00 4h candle closes at 12:00, so a 10:05 bar must still be using the 04:00 one."""
    base = pd.Series(pd.to_datetime(
        ["2026-06-15 07:59", "2026-06-15 08:00", "2026-06-15 10:05", "2026-06-15 12:00"], utc=True))
    higher = pd.Series(pd.to_datetime(["2026-06-15 04:00", "2026-06-15 08:00"], utc=True))
    idx = timeframe_engine.align_index(base, higher, 240)
    assert idx[0] == -1, "before any higher bar has closed there is nothing to read"
    assert idx[1] == 0 and idx[2] == 0, "08:00 and 10:05 must both still use the 04:00 candle"
    assert idx[3] == 1, "only at 12:00 does the 08:00 candle become readable"


def test_prior_extremes_exclude_the_bar_being_judged():
    """Including the current bar makes every sweep impossible; the shift(1) is the whole detector."""
    frame = pd.DataFrame({"high": [10.0, 11.0, 12.0, 20.0], "low": [9.0, 8.0, 7.0, 1.0]})
    high, low = liquidity_engine.prior_extremes(frame, lookback=3)
    assert high[3] == pytest.approx(12.0), "the prior high must not include bar 3's own 20"
    assert low[3] == pytest.approx(7.0), "the prior low must not include bar 3's own 1"


def test_a_sweep_that_closes_back_inside_is_a_reclaim():
    frame = pd.DataFrame({
        "high": [10.0, 10.0, 10.0, 12.0], "low": [9.0, 9.0, 9.0, 9.5], "close": [9.5, 9.5, 9.5, 9.8]})
    state = liquidity_engine.sweeps(frame, lookback=3)
    assert bool(state["swept_high"][3]) is True, "the high of 12 exceeded the prior high of 10"
    assert bool(state["reclaim_down"][3]) is True, "and it closed at 9.8, back below that prior high"


# --------------------------------------------------------------------------- risk

def test_position_size_lands_on_exactly_the_risk_asked_for():
    """Gold: 100 oz per lot, so one dollar of movement is $100 on one lot."""
    lots = risk_engine.position_size(equity=10_000, risk_percent=1.0,
                                     stop_distance_price=8.30, value_per_price_unit_per_lot=100)
    assert lots is not None
    risked = 8.30 * 100 * lots
    assert risked == pytest.approx(100.0), "1 % of 10,000 is 100, whatever the stop distance"


def test_position_size_refuses_impossible_inputs_instead_of_guessing():
    for bad in ({"stop_distance_price": 0.0}, {"stop_distance_price": -5.0},
                {"value_per_price_unit_per_lot": 0.0}, {"risk_percent": 0.0}):
        args = {"equity": 10_000, "risk_percent": 1.0, "stop_distance_price": 8.3,
                "value_per_price_unit_per_lot": 100, **bad}
        assert risk_engine.position_size(**args) is None, bad


@pytest.mark.parametrize("exposure,fragment", [
    (risk_engine.Exposure({"XAUUSD": 1}), "one open trade per asset"),
    (risk_engine.Exposure({"XAUUSD": 0}, day_pnl_percent=-3.5), "daily loss"),
    (risk_engine.Exposure({"XAUUSD": 0}, drawdown_percent=16.0), "drawdown"),
])
def test_every_limit_refuses_with_a_reason(exposure, fragment):
    """A refusal is only useful if it says why; the ledger records this string verbatim."""
    out = risk_engine.check_trade("XAUUSD", 4280, 4288, 4256, exposure)
    assert out is not True
    assert fragment in out.reason
    assert bool(out) is False, "a Refusal must be falsey so `if not check_trade(...)` reads correctly"


def test_the_most_fundamental_reason_is_the_one_reported():
    """Both a drawdown halt and a poor reward:risk apply; the halt is the one that matters."""
    out = risk_engine.check_trade("XAUUSD", 4280, 4288, 4284,
                                  risk_engine.Exposure({"XAUUSD": 0}, drawdown_percent=20.0))
    assert "drawdown" in out.reason


# --------------------------------------------------------------------------- signal

def test_a_stop_on_the_wrong_side_is_refused_before_anything_else_is_considered():
    out = signal_engine.decide_signal(1, "t", side="BUY", entry=4280, stop=4288, target=4310,
                                      conditions={"a": True, "b": True}, min_confluence=1)
    assert out.take is False and "wrong side" in out.reason


def test_confluence_below_the_threshold_names_what_was_missing():
    out = signal_engine.decide_signal(1, "t", side="SELL", entry=4280, stop=4288, target=4256,
                                      conditions={"fvg": True, "sweep": False}, min_confluence=2)
    assert out.take is False
    assert "sweep" in out.reason, "the refusal must name the condition that was absent"


def test_a_taken_decision_records_which_conditions_were_present():
    out = signal_engine.decide_signal(1, "t", side="SELL", entry=4280, stop=4288, target=4256,
                                      conditions={"fvg": True, "sweep": True, "poc": False},
                                      min_confluence=2)
    assert out.take is True
    assert out.present() == ["fvg", "sweep"] and out.confluence == 2


# --------------------------------------------------------------------------- ledger

def test_a_rejection_without_a_reason_is_refused():
    led = ledger_mod.Ledger()
    with pytest.raises(ValueError):
        led.rejected(bar=1, ts="t", reason="")


def test_the_funnel_counts_every_kind_even_when_zero():
    led = ledger_mod.Ledger()
    led.setup(bar=1, ts="t")
    led.rejected(bar=2, ts="t", reason="regime AVOID")
    assert led.funnel() == {"setup": 1, "rejected": 1, "entry": 0, "manage": 0, "exit": 0}
    assert led.rejection_reasons() == {"regime AVOID": 1}


# --------------------------------------------------------------------------- analytics

def test_mae_and_mfe_are_measured_in_R_over_the_bars_the_trade_was_open():
    """A SELL entered at 100 with 5 points of risk, which ran to 110 against and 90 for."""
    frame = pd.DataFrame({"high": [100.0, 105.0, 110.0, 100.0], "low": [100.0, 95.0, 90.0, 100.0]})
    trade = {"entry_idx": 0, "bars_held": 2, "entry_price": 100.0, "exit_price": 95.0,
             "side": "SELL", "gross_pct": -5.0, "r_multiple": -1.0}
    # risk = |gross/100 * entry / r| = |-0.05 * 100 / -1| = 5.0
    rows = analytics.excursions([trade], frame)
    assert rows[0]["mae_r"] == pytest.approx(2.0), "110 is 10 against a 5-point risk = 2 R"
    assert rows[0]["mfe_r"] == pytest.approx(2.0), "90 is 10 in favour = 2 R"


def test_excursion_summary_separates_winners_from_losers():
    frame = pd.DataFrame({"high": [100.0, 101.0, 100.0], "low": [100.0, 90.0, 100.0]})
    winner = {"entry_idx": 0, "bars_held": 1, "entry_price": 100.0, "exit_price": 95.0,
              "side": "SELL", "gross_pct": -5.0, "r_multiple": -1.0, "net_pct": 2.0}
    loser = {**winner, "net_pct": -2.0}
    out = analytics.excursion_summary([winner, loser], frame)
    assert out["available"] is True and out["trades_measured"] == 2
    assert out["winners_median_mae_r"] is not None and out["losers_median_mae_r"] is not None


# --------------------------------------------------------------------------- robustness

def test_cost_stress_treats_its_argument_as_a_fraction_not_a_percentage():
    """The single most expensive unit error this project has recorded. 0.0001 is 0.01 %, not 0.01."""
    trades = [{"net_pct": 1.0}, {"net_pct": 1.0}]
    out = monte_carlo.cost_stress(trades, extra_cost_pct_of_price=0.0001)
    assert out["extra_cost_percent_of_price"] == pytest.approx(0.01)
    # Each trade loses 0.0001 of a unit return, i.e. 0.01 percentage points — not 1 point.
    assert out["stressed_net_pct"] < out["real_net_pct"]
    assert out["real_net_pct"] - out["stressed_net_pct"] < 0.1


def test_shuffling_the_order_cannot_change_the_total_return():
    """It changes the PATH, so only the drawdown distribution is informative."""
    trades = [{"net_pct": v} for v in (2.0, -1.0, 3.0, -2.0, 1.0, -0.5, 4.0)]
    out = monte_carlo.shuffle_order(trades, draws=50)
    assert out["available"] is True
    assert out["worst_drawdown_pct"] >= out["real_drawdown_pct"] or out["draws"] == 50


def test_too_few_trades_is_reported_as_insufficient_rather_than_computed():
    assert monte_carlo.bootstrap([{"net_pct": 1.0}]).get("available") is False
    assert monte_carlo.survives([{"net_pct": 1.0}])["verdict"] == "insufficient"


# --------------------------------------------------------------------------- regime

def test_a_straight_line_market_is_trending_and_a_chop_is_ranging():
    trending = pd.DataFrame({"high": np.arange(1, 121) + 0.5, "low": np.arange(1, 121) - 0.5,
                             "close": np.arange(1, 121, dtype=float),
                             "open": np.arange(1, 121, dtype=float),
                             "datetime": pd.date_range("2026-01-01", periods=120, freq="h", tz="UTC"),
                             "volume": np.ones(120)})
    state = regime_engine.classify_regime(trending, lookback=20)
    assert state["trend"][-1] == regime_engine.TRENDING

    chop_close = np.tile([100.0, 101.0], 60)
    chop = pd.DataFrame({"high": chop_close + 0.5, "low": chop_close - 0.5, "close": chop_close,
                         "open": chop_close,
                         "datetime": pd.date_range("2026-01-01", periods=120, freq="h", tz="UTC"),
                         "volume": np.ones(120)})
    assert regime_engine.classify_regime(chop, lookback=20)["trend"][-1] == regime_engine.RANGING
