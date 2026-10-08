"""Wrong split boundaries make every measurement a lie, so they must be checked, not trusted.

WHAT HAPPENED
-------------
`data/strategy_lab/market_meta.json` decides where each market's search, validation and holdout
windows begin. On 8 October 2026 it claimed a holdout starting 2022-02-15 while the registry said
2024-08-30 - an extra 2.5 years of bars that selection had already seen, counted as out-of-sample.

Nothing detected it. An afternoon of survey numbers was measured against something that was not a
holdout, and the error was only found by chance while checking an unrelated claim. The same CISD spec
read 81 trades at PF 1.41 under the bad boundaries and 41 at PF 1.482 under the real ones.

A wrong holdout does not look wrong. It produces plausible numbers with more trades, which is exactly
what makes it dangerous: it reads as a better result.

These tests never touch the live registry. Each builds its own pair of files.
"""
import json

import pytest

from src import strategy_lab as lab
from src.system_doctor import check_split_boundaries

REAL = {"validation_start": "2022-08-22 01:00", "holdout_start": "2024-08-30 17:00"}
WRONG = {"validation_start": "2021-11-17 00:00", "holdout_start": "2022-02-15 00:00"}


def _pair(tmp_path, registry_markets, cached_markets):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"version": 1, "markets": registry_markets, "candidates": {}}),
                        encoding="utf-8")
    cache = tmp_path / "meta.json"
    if cached_markets is not None:
        cache.write_text(json.dumps({"markets": cached_markets}), encoding="utf-8")
    return registry, cache


# ------------------------------------------------------------------ the failure that happened
def test_the_exact_boundary_drift_is_detected(tmp_path):
    registry, cache = _pair(
        tmp_path,
        {"XAUUSD:4h": {"candidates_tried": 97133, "boundaries": REAL}},
        {"XAUUSD:4h": {"candidates_tried": 25, "boundaries": WRONG}})

    rows = lab.market_meta_mismatches(path=registry, meta_path=cache)
    fields = {r["field"] for r in rows}
    assert "boundaries" in fields, "the holdout moved and nothing noticed"
    assert "candidates_tried" in fields


def test_agreement_is_silent(tmp_path):
    registry, cache = _pair(tmp_path,
                            {"XAUUSD:4h": {"candidates_tried": 97133, "boundaries": REAL}},
                            {"XAUUSD:4h": {"candidates_tried": 97133, "boundaries": REAL}})
    assert lab.market_meta_mismatches(path=registry, meta_path=cache) == []


def test_a_market_missing_from_the_cache_is_reported(tmp_path):
    """A missing market gets default boundaries computed from the bar count, which is a different
    split again - silently."""
    registry, cache = _pair(tmp_path,
                            {"XAUUSD:4h": {"candidates_tried": 1, "boundaries": REAL},
                             "BTCUSD:4h": {"candidates_tried": 1, "boundaries": REAL}},
                            {"XAUUSD:4h": {"candidates_tried": 1, "boundaries": REAL}})
    rows = lab.market_meta_mismatches(path=registry, meta_path=cache)
    assert [r["market"] for r in rows] == ["BTCUSD:4h"]


def test_a_market_the_registry_does_not_have_is_reported(tmp_path):
    registry, cache = _pair(tmp_path,
                            {"XAUUSD:4h": {"candidates_tried": 1, "boundaries": REAL}},
                            {"XAUUSD:4h": {"candidates_tried": 1, "boundaries": REAL},
                             "GHOST:4h": {"candidates_tried": 9, "boundaries": WRONG}})
    rows = lab.market_meta_mismatches(path=registry, meta_path=cache)
    assert [r["market"] for r in rows] == ["GHOST:4h"]


# ------------------------------------------------------------------ absent is not wrong
def test_no_sidecar_at_all_is_not_a_mismatch(tmp_path):
    """It gets rebuilt on the next read. Reporting that as a fault would train people to ignore it."""
    registry, cache = _pair(tmp_path, {"XAUUSD:4h": {"candidates_tried": 1, "boundaries": REAL}}, None)
    assert lab.market_meta_mismatches(path=registry, meta_path=cache) == []


def test_no_registry_is_not_a_mismatch(tmp_path):
    assert lab.market_meta_mismatches(path=tmp_path / "nope.json", meta_path=tmp_path / "m.json") == []


def test_an_unreadable_sidecar_is_reported_rather_than_trusted(tmp_path):
    registry, cache = _pair(tmp_path, {"XAUUSD:4h": {"candidates_tried": 1, "boundaries": REAL}}, None)
    cache.write_text("not json", encoding="utf-8")
    rows = lab.market_meta_mismatches(path=registry, meta_path=cache)
    assert rows and rows[0]["cached"] == "unreadable"


# ------------------------------------------------------------------ the doctor reports it loudly
def test_the_doctor_check_passes_when_the_live_files_agree():
    result = check_split_boundaries()
    assert result["status"] in {"ok", "info"}, result["summary"]


def test_the_doctor_calls_a_drift_a_failure_not_a_warning(monkeypatch):
    """A warning is something to read later. Every number produced while this is true is void, so it
    has to be the loudest level available."""
    monkeypatch.setattr(lab, "market_meta_mismatches",
                        lambda *a, **k: [{"market": "XAUUSD:4h", "field": "boundaries",
                                          "cached": WRONG, "registry": REAL}])
    result = check_split_boundaries()
    assert result["status"] == "fail"
    assert "XAUUSD:4h" in result["summary"]
    assert "holdout" in result["summary"].lower()
    assert "market_meta.json" in result["detail"]["fix"], "say how to fix it, not just that it is broken"


def test_the_doctor_check_never_raises(monkeypatch):
    """It runs every 30 minutes. A thrown exception here would take the whole health report down."""
    def explode(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(lab, "market_meta_mismatches", explode)
    assert check_split_boundaries()["status"] == "warn"


def test_the_check_is_wired_into_the_doctor():
    import inspect

    from src import system_doctor
    source = inspect.getsource(system_doctor)
    assert "check_split_boundaries," in source, \
        "the guard exists but the doctor never calls it, which is the same as not having it"
