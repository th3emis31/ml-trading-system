"""Confirmation must make the bar reachable WITHOUT lowering it, and must not be back-datable.

Two things could quietly ruin this module. The first is lowering the 0.95 deflated-Sharpe bar, which is a
standing owner rule and is asserted against here. The second is letting the forward window start earlier
than registration, which would score a candidate on the data it was chosen from and manufacture a pass.
"""
from __future__ import annotations

import json
import math

import pytest

from src import strategy_confirm as sc


def test_the_evidence_bar_itself_is_untouched():
    """The fix is the trial count, never the bar. Lowering it is forbidden by a standing owner rule."""
    from src.strategy_lab import HOLDOUT_CRITERIA

    assert HOLDOUT_CRITERIA["min_deflated_sharpe"] == 0.95
    assert HOLDOUT_CRITERIA["min_trades"] == 30
    assert HOLDOUT_CRITERIA["min_profit_factor"] == 1.2


def test_the_slot_count_cannot_exceed_what_real_edges_can_clear():
    """The finding that overturned the first design: at three slots the floor already exceeds the live
    EA's measured per-trade Sharpe of 0.0777, so nothing real could pass. Two is the honest maximum."""
    assert sc.CONFIRM_SLOTS <= 2
    assert sc.floor_for(sc.CONFIRM_SLOTS) < 0.0777, "a real strategy must be able to clear the floor"
    assert sc.floor_for(3) > 0.0777, "this is why the slot count is capped; if it changes, re-derive it"


def test_the_floor_rises_with_the_trial_count_which_is_the_whole_problem():
    floors = [sc.floor_for(n) for n in (2, 10, 1_000, 50_000)]
    assert floors == sorted(floors), "more trials must never lower the floor"
    assert sc.floor_for(50_000) > 8 * sc.floor_for(2), "the lab-scale floor is the thing being escaped"


def test_an_edge_below_the_floor_can_never_pass_and_says_so():
    """None, not a huge number: 'cannot pass' and 'needs a lot of evidence' are different answers."""
    assert sc.trades_needed(0.01) is None
    assert sc.trades_needed(sc.floor_for(sc.CONFIRM_SLOTS)) is None      # exactly at the floor
    assert sc.trades_needed(0.25) is not None


def test_trades_needed_falls_as_the_edge_strengthens():
    needs = [sc.trades_needed(sr) for sr in (0.08, 0.15, 0.25, 0.40)]
    assert all(n is not None for n in needs)
    assert needs == sorted(needs, reverse=True)


def test_the_wait_is_reported_in_years_not_just_trades():
    """4,090 trades at 28 a year is not patience, it is 146 years - and that changes the decision."""
    assert sc.years_to_prove(0.0777, 28) > 100
    assert sc.years_to_prove(0.0777, 400) < 15
    assert sc.years_to_prove(0.01, 28) is None          # below the floor, no wait will do


def test_implied_sharpe_inverts_the_labs_own_deflated_sharpe():
    """Round-trip against the real function: recover sr from the DSR it produces."""
    import numpy as np

    from src.strategy_lab import deflated_sharpe

    rng = np.random.default_rng(11)
    returns = rng.normal(0.004, 0.02, 400)              # a modest positive edge
    n_trials = 500
    dsr = deflated_sharpe(returns, n_trials, sc.SR_VARIANCE)
    assert dsr is not None
    recovered = sc.implied_sharpe(dsr, len(returns), n_trials)
    actual = float(returns.mean() / returns.std(ddof=1))
    # denom = 1 is an approximation, so allow a tolerance; the point is it lands in the right place.
    assert abs(recovered - actual) < 0.05, f"recovered {recovered} vs actual {actual}"


def test_erfinv_matches_the_standard_normal_quantile():
    assert abs(math.sqrt(2) * sc._erfinv(2 * 0.95 - 1) - 1.6449) < 1e-3
    assert abs(math.erf(sc._erfinv(0.5)) - 0.5) < 1e-9


def test_registration_records_the_data_end_so_the_window_cannot_be_backdated(tmp_path):
    cand = [{"market": "XAUUSD:4h", "id": "abc123", "family": "donchian_breakout",
             "description": "a rule", "spec": {"family": "donchian_breakout"},
             "validation_sr": 0.25, "data_end": "2026-09-21 09:00"}]
    out = sc.register(cand, base=tmp_path)
    assert out["ok"] and out["registered"] == 1
    row = out["candidates"][0]
    assert row["data_end_at_registration"] == "2026-09-21 09:00"
    assert row["state"] == "watching"
    assert row["n_trials"] == sc.CONFIRM_SLOTS
    assert row["trades_needed"] == sc.trades_needed(0.25)
    assert row["places_orders"] is False


def test_the_slots_cannot_be_exceeded(tmp_path):
    """Growing the slot count later would retroactively raise the bar on candidates already registered."""
    many = [{"market": f"X{i}:4h", "id": f"id{i}", "spec": {}, "validation_sr": 0.3,
             "data_end": "2026-09-21 09:00"} for i in range(sc.CONFIRM_SLOTS + 4)]
    first = sc.register(many, base=tmp_path)
    assert first["registered"] == sc.CONFIRM_SLOTS
    second = sc.register(many, base=tmp_path)
    assert second["ok"] is False and second["registered"] == 0
    assert "slots are in use" in second["reason"]


def test_the_same_candidate_is_not_registered_twice(tmp_path):
    cand = [{"market": "XAUUSD:4h", "id": "same", "spec": {}, "validation_sr": 0.3,
             "data_end": "2026-09-21 09:00"}]
    sc.register(cand, base=tmp_path)
    again = sc.register(cand, base=tmp_path)
    assert again["registered"] == 0


def test_the_ledger_is_append_only_and_the_latest_row_wins(tmp_path):
    cand = [{"market": "XAUUSD:4h", "id": "one", "spec": {}, "validation_sr": 0.3,
             "data_end": "2026-09-21 09:00"}]
    sc.register(cand, base=tmp_path)
    path = sc.confirm_ledger(tmp_path)
    before = len(path.read_text(encoding="utf-8").splitlines())
    row = sc.read_confirmations(tmp_path)[0]
    row["state"] = "confirmed"
    sc._append_row(row, tmp_path)
    after = len(path.read_text(encoding="utf-8").splitlines())
    assert after == before + 1, "history must be kept, not overwritten"
    assert sc.read_confirmations(tmp_path)[0]["state"] == "confirmed"


def test_status_reports_both_floors_so_the_gap_is_visible(tmp_path):
    s = sc.confirm_status(tmp_path)
    assert s["floor_sr0"] < s["floor_at_lab_scale"]
    assert s["slots"] == sc.CONFIRM_SLOTS
    assert s["places_orders"] is False
    assert "unchanged" in s["note"]


def test_a_confirm_run_on_a_row_with_no_data_end_refuses_rather_than_guessing():
    out = sc.confirm_one({"slot_id": "x", "market": "XAUUSD:4h", "spec": {}})
    assert out["ok"] is False and "forward window" in out["reason"]


def test_a_bad_market_key_is_refused():
    out = sc.confirm_one({"slot_id": "x", "market": "nonsense", "spec": {},
                          "data_end_at_registration": "2026-09-21 09:00"})
    assert out["ok"] is False and "bad market key" in out["reason"]
