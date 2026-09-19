import numpy as np
import pandas as pd

from src import crt_forward


def _fake_fetch(end: str):
    def fetch(symbol, timeframe, count):
        freq = "15min" if timeframe == "15m" else "4h"
        times = pd.date_range(end=pd.Timestamp(end, tz="UTC"), periods=count, freq=freq)
        rng = np.random.default_rng(7 if timeframe == "15m" else 8)
        step = 1.5 if timeframe == "15m" else 8.0
        close = 4300 + np.cumsum(rng.normal(0, step, count))
        open_ = np.concatenate([[close[0]], close[:-1]])
        return pd.DataFrame({"datetime": times, "open": open_, "high": np.maximum(open_, close) + step,
                             "low": np.minimum(open_, close) - step, "close": close})
    return fetch


def test_no_bars_reports_unavailable_and_invents_nothing(tmp_path):
    path = tmp_path / "state.json"
    state = crt_forward.run_once(fetch=lambda s, t, c: pd.DataFrame(), now="2026-09-15 12:00", path=path)
    assert state["available"] is False and state["places_orders"] is False
    assert "start_at" not in state and "summary" not in state


def test_start_is_fixed_on_first_run_and_kept(tmp_path):
    path = tmp_path / "state.json"
    first = crt_forward.run_once(fetch=_fake_fetch("2026-09-15 11:45"), now="2026-09-15 12:00", path=path)
    assert first["available"] is True and first["start_at"] == "2026-09-15 12:00"
    assert first["summary"]["trades"] == 0 and first["verdict"]["status"] == "collecting"
    later = crt_forward.run_once(fetch=_fake_fetch("2026-09-20 11:45"), now="2026-09-20 12:00", path=path)
    assert later["start_at"] == "2026-09-15 12:00"
    assert all(t["entry_time"] >= "2026-09-15 12:00" for t in later["closed_trades"])
    assert later["verdict"]["status"] in ("collecting", "passed", "failed")


def test_progress_verdict_needs_thirty_trades():
    assert crt_forward.progress_verdict({"trades": 29, "profit_factor": 3.0})["status"] == "collecting"
    ok = crt_forward.progress_verdict({"trades": 30, "profit_factor": 1.3, "total_return_pct": 4.0, "max_drawdown_pct": 5.0})
    bad = crt_forward.progress_verdict({"trades": 30, "profit_factor": 1.1, "total_return_pct": 1.0, "max_drawdown_pct": 5.0})
    assert ok["status"] == "passed" and bad["status"] == "failed"


def test_mss_candidate_uses_5m_bars_without_h4_and_starts_clean(tmp_path):
    path = tmp_path / "mss.json"
    calls = []

    def fetch(symbol, timeframe, count):
        calls.append(timeframe)
        return _fake_fetch("2026-09-15 11:55")(symbol, "15m", count).assign(
            datetime=pd.date_range(end=pd.Timestamp("2026-09-15 11:55", tz="UTC"), periods=count, freq="5min"))

    state = crt_forward.run_once(fetch=fetch, now="2026-09-15 12:00", path=path, key="crt_mss_d1_5m")
    assert calls == ["5m"] and state["available"] is True and state["timeframe"] == "5m"
    assert state["start_at"] == "2026-09-15 12:00" and state["summary"]["trades"] == 0


def test_the_owners_manipulation_candle_is_registered_as_a_paper_candidate():
    """Added 19 Sep 2026. Pins the parameters, because the owner corrected my reading of the rule once
    already: it is the CONTINUATION (close beyond the level), not the fade, and it carries the EMA400
    filter without which it was a bet on gold's 2024-26 run rather than a structure."""
    from src import sweep_reversal

    candidate = crt_forward.CANDIDATES["sweep_continue_xau_4h"]
    assert candidate["symbol"] == "XAUUSD" and candidate["timeframe"] == "4h"
    assert candidate["htf_bars"] == 0, "everything the rule needs is on the 4H candle itself"
    params = candidate["spec"]["params"]
    assert params["mode"] == "continue", "the owner's words: if the close is above the previous high, buy"
    assert (params["lookback"], params["rr"], params["trend_ema"]) == (40, 2.0, 400)
    assert candidate["spec"] is not sweep_reversal.FORWARD_CANDIDATE, "the declared spec must not be mutated"
    assert params == sweep_reversal.FORWARD_CANDIDATE["params"]


def test_every_forward_candidate_has_its_own_state_file_and_none_can_place_an_order():
    """A shared state file would let one candidate's trades be counted as another's evidence."""
    paths = [c["state"] for c in crt_forward.CANDIDATES.values()]
    assert len(paths) == len({str(p) for p in paths})
    text = open(crt_forward.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    for forbidden in ("order_send", "OrderSend", "place_order", "auto_execute"):
        assert forbidden not in text


def test_the_new_candidate_starts_clean_and_counts_nothing_from_before_its_start(tmp_path):
    path = tmp_path / "sweep.json"
    state = crt_forward.run_once(fetch=_fake_fetch("2026-09-19 12:00"), now="2026-09-19 16:00",
                                path=path, key="sweep_continue_xau_4h")
    assert state["available"] is True and state["places_orders"] is False
    assert state["start_at"] == "2026-09-19 16:00"
    assert state["summary"]["trades"] == 0, "backtest history must never seed a forward test"
    assert state["verdict"] == {"status": "collecting", "trades": 0, "needed": 30}
