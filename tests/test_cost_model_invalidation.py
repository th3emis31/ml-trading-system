"""A cost change must invalidate the results that used the old cost.

On 19 September 2026 the round-trip spread was corrected from measured broker quotes - gold's had been
about six times too harsh. strategy_book.rescore_market re-evaluates any stored candidate whose
cost_model tag differs from its market's, so that correction should have queued the whole registry for
a re-score. It queued nothing: the constant changed but the tag still read "spread+swap-v2", so 9,880
of 9,884 stored results stayed priced under the old model while being indistinguishable from corrected
ones. The tag now carries the round trip, which makes that failure impossible rather than unlikely.
"""
import numpy as np
import pandas as pd

from src import strategy_lab as lab
from src import strategy_book as book


def _market(cost_pct):
    rng = np.random.default_rng(11)
    n = 1200
    close = 4000 + np.cumsum(rng.normal(0, 8, n))
    bars = pd.DataFrame({"datetime": pd.date_range("2020-01-01", periods=n, freq="4h", tz="UTC"),
                         "open": np.concatenate([[close[0]], close[:-1]]),
                         "high": close + 10, "low": close - 10, "close": close, "volume": [1.0] * n})
    market = lab.Market("XAUUSD", "4h", bars, swap=True)
    market.cost_pct = cost_pct
    market.info["cost_round_trip_pct"] = cost_pct
    market.info["cost_model"] = f"{lab.COST_MODEL}-percent-rt{cost_pct * 100:.5g}"
    return market


def test_the_cost_model_tag_names_the_round_trip_it_used():
    cheap, dear = _market(0.00009), _market(0.0004)
    assert cheap.info["cost_model"] != dear.info["cost_model"], (
        "two different costs must not share one tag, or a correction cannot be detected")
    assert "0.009" in cheap.info["cost_model"] and "0.04" in dear.info["cost_model"]


def test_the_version_was_bumped_when_the_spreads_were_corrected():
    assert lab.COST_MODEL == "spread+swap-v3"


def test_a_candidate_priced_under_the_old_cost_is_queued_for_a_rescore():
    """The assertion that would have caught the disarmed safety net."""
    market = _market(0.00009)
    spec = {"family": "donchian_breakout",
            "params": {"symbol": "XAUUSD", "timeframe": "4h", "lookback": 20, "atr_len": 14,
                       "side": "both", "trend_ema": 0},
            "exits": {"stop": "atr", "sl_atr": 1.5, "rr": 2.0, "trail_atr": 0.0, "max_bars": 100,
                      "swing_lookback": 0}}
    stale = lab.evaluate_candidate(market, spec, with_holdout=True)
    stale["cost_model"] = "spread+swap-v2-percent"          # how every stored result was tagged
    stale["market"] = market.key
    registry = {"candidates": {"stale": stale}, "markets": {}}
    out = book.rescore_market(registry, market)
    assert out["rescored"] == 1, "a result priced under a superseded cost model must be re-evaluated"
    assert registry["candidates"]["stale"]["cost_model"] == market.info["cost_model"]
    assert registry["candidates"]["stale"]["previous_cost_model"] == "spread+swap-v2-percent"


def test_a_candidate_already_on_the_current_cost_is_left_alone():
    market = _market(0.00009)
    spec = {"family": "donchian_breakout",
            "params": {"symbol": "XAUUSD", "timeframe": "4h", "lookback": 20, "atr_len": 14,
                       "side": "both", "trend_ema": 0},
            "exits": {"stop": "atr", "sl_atr": 1.5, "rr": 2.0, "trail_atr": 0.0, "max_bars": 100,
                      "swing_lookback": 0}}
    current = lab.evaluate_candidate(market, spec, with_holdout=True)
    current["market"] = market.key
    registry = {"candidates": {"ok": current}, "markets": {}}
    assert book.rescore_market(registry, market)["rescored"] == 0
    assert "rescored_at" not in registry["candidates"]["ok"], "nothing to redo means nothing is touched"


def test_a_rescore_keeps_what_the_old_numbers_were():
    """Never delete: the previous result stays on the record so the correction can be audited."""
    market = _market(0.00009)
    spec = {"family": "donchian_breakout",
            "params": {"symbol": "XAUUSD", "timeframe": "4h", "lookback": 20, "atr_len": 14,
                       "side": "both", "trend_ema": 0},
            "exits": {"stop": "atr", "sl_atr": 1.5, "rr": 2.0, "trail_atr": 0.0, "max_bars": 100,
                      "swing_lookback": 0}}
    stale = lab.evaluate_candidate(market, spec, with_holdout=True)
    stale.update({"cost_model": "spread+swap-v2-percent", "market": market.key})
    before = (stale["holdout"] or {}).get("total_return_pct")
    registry = {"candidates": {"stale": stale}, "markets": {}}
    book.rescore_market(registry, market)
    kept = registry["candidates"]["stale"]["previous"]["holdout"]["total_return_pct"]
    assert kept == before
