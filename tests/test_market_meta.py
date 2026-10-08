"""Reading two timestamps must not cost a 617 MB JSON parse.

WHY THIS EXISTS
---------------
`direction_sweep.evaluate_family` called `lab.load_registry()` to read one thing: the split dates in
`registry["markets"][key]["boundaries"]`. That function runs once per family, and a second call sat
inside the per-candidate save loop, so a survey parsed `data/strategy_lab/registry.json` - 617 MB,
essentially all of it candidate history - a dozen times or more. Each parse holds the file as a string
and then as Python objects, several gigabytes at a time.

On 8 October 2026 that killed the whole-system survey twice on a 7.5 GB machine: once at full scope,
and again after it was narrowed to a single symbol and a single timeframe. The lab had become unable
to survey its own strategies, and the symptom looked like ordinary memory pressure rather than one
function reading a 617 MB file for a few dozen bytes.

`market_meta()` streams only the registry's `markets` object, caches the result in a sidecar of about
a kilobyte, and returns in a millisecond thereafter. Measured against the real registry: 4.38 s on the
first call, 0.001 s after, identical output.

These tests build their own registries in tmp_path. Nothing here reads the real 617 MB file.
"""
import json
import os

import pytest

from src import strategy_lab as lab

BOUNDS_4H = {"validation_start": "2022-08-22 01:00", "holdout_start": "2024-08-30 17:00"}
BOUNDS_1H = {"validation_start": "2023-05-04 05:00", "holdout_start": "2025-01-08 01:00"}


def _registry(tmp_path, *, big_candidates=0, markets=None):
    """A registry shaped like the real one: markets first, then the candidate bulk."""
    payload = {
        "version": 1,
        "markets": markets if markets is not None else {
            "XAUUSD:4h": {"candidates_tried": 96952, "validation_srs": [[0.1, 20]] * 50,
                          "boundaries": BOUNDS_4H},
            "XAUUSD:1h": {"candidates_tried": 105602, "validation_srs": [], "boundaries": BOUNDS_1H},
        },
        "candidates": {f"c{i}": {"spec": {"family": "ema_pullback"}, "holdout": {"trades": i}}
                       for i in range(big_candidates)},
    }
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ------------------------------------------------------------------ it returns the right thing
def test_it_returns_the_boundaries_and_trial_counts(tmp_path):
    registry = _registry(tmp_path)
    meta = lab.market_meta(path=registry, meta_path=tmp_path / "meta.json")

    assert meta["XAUUSD:4h"]["boundaries"] == BOUNDS_4H
    assert meta["XAUUSD:4h"]["candidates_tried"] == 96952
    assert meta["XAUUSD:1h"]["boundaries"] == BOUNDS_1H


def test_it_agrees_with_the_slow_path_it_replaces(tmp_path):
    """The whole point is to be cheaper, not different."""
    registry = _registry(tmp_path, big_candidates=200)
    slow = lab.load_registry(registry)["markets"]
    fast = lab.market_meta(path=registry, meta_path=tmp_path / "meta.json")

    for key, value in fast.items():
        assert value["boundaries"] == slow[key]["boundaries"]
        assert value["candidates_tried"] == slow[key]["candidates_tried"]


# ------------------------------------------------------------------ it does not read the bulk
def test_it_never_reads_the_candidate_bulk(tmp_path, monkeypatch):
    """The guard that matters. If this ever falls back to load_registry on the happy path, the
    memory cost returns and the survey starts dying again."""
    registry = _registry(tmp_path, big_candidates=500)
    called = []
    monkeypatch.setattr(lab, "load_registry", lambda *a, **k: called.append(1) or {"markets": {}})

    meta = lab.market_meta(path=registry, meta_path=tmp_path / "meta.json")

    assert meta["XAUUSD:4h"]["boundaries"] == BOUNDS_4H
    assert called == [], "market_meta fell back to parsing the entire registry"


def test_a_brace_inside_a_string_does_not_end_the_scan_early(tmp_path):
    """The scan tracks brace depth. A '}' inside a string value would truncate the markets object and
    silently lose every market after it."""
    registry = _registry(tmp_path, markets={
        "XAUUSD:4h": {"candidates_tried": 1, "note": "a } brace and a { brace", "boundaries": BOUNDS_4H},
        "XAUUSD:1h": {"candidates_tried": 2, "boundaries": BOUNDS_1H},
    })
    meta = lab.market_meta(path=registry, meta_path=tmp_path / "meta.json")

    assert set(meta) == {"XAUUSD:4h", "XAUUSD:1h"}, "a market was lost to a brace inside a string"


def test_an_escaped_quote_does_not_confuse_the_scan(tmp_path):
    registry = _registry(tmp_path, markets={
        "XAUUSD:4h": {"candidates_tried": 1, "note": "he said \\\" } \\\" here", "boundaries": BOUNDS_4H},
        "XAUUSD:1h": {"candidates_tried": 2, "boundaries": BOUNDS_1H},
    })
    meta = lab.market_meta(path=registry, meta_path=tmp_path / "meta.json")
    assert set(meta) == {"XAUUSD:4h", "XAUUSD:1h"}


# ------------------------------------------------------------------ the sidecar
def test_the_sidecar_is_written_and_is_small(tmp_path):
    registry = _registry(tmp_path, big_candidates=400)
    cache = tmp_path / "meta.json"
    lab.market_meta(path=registry, meta_path=cache)

    assert cache.exists()
    assert cache.stat().st_size < 10_000, "the sidecar must stay tiny or it defeats its own purpose"
    assert cache.stat().st_size < registry.stat().st_size / 10


def test_the_second_call_uses_the_sidecar_and_not_the_registry(tmp_path):
    registry = _registry(tmp_path)
    cache = tmp_path / "meta.json"
    first = lab.market_meta(path=registry, meta_path=cache)

    registry.write_text("{ this is no longer valid json", encoding="utf-8")
    os.utime(registry, (cache.stat().st_mtime - 60, cache.stat().st_mtime - 60))

    assert lab.market_meta(path=registry, meta_path=cache) == first


def test_a_sidecar_older_than_the_registry_is_rebuilt(tmp_path):
    """Boundaries move when the lab re-splits a market. A stale cache would pin a strategy to the
    wrong holdout, which is worse than being slow."""
    registry = _registry(tmp_path)
    cache = tmp_path / "meta.json"
    lab.market_meta(path=registry, meta_path=cache)

    moved = {"validation_start": "2099-01-01 00:00", "holdout_start": "2099-06-01 00:00"}
    registry.write_text(json.dumps({"version": 1,
                                    "markets": {"XAUUSD:4h": {"candidates_tried": 7, "boundaries": moved}},
                                    "candidates": {}}), encoding="utf-8")
    newer = cache.stat().st_mtime + 60
    os.utime(registry, (newer, newer))

    assert lab.market_meta(path=registry, meta_path=cache)["XAUUSD:4h"]["boundaries"] == moved


def test_a_corrupt_sidecar_falls_back_rather_than_returning_rubbish(tmp_path):
    registry = _registry(tmp_path)
    cache = tmp_path / "meta.json"
    cache.write_text("not json at all", encoding="utf-8")
    os.utime(cache, (registry.stat().st_mtime + 60, registry.stat().st_mtime + 60))

    assert lab.market_meta(path=registry, meta_path=cache)["XAUUSD:4h"]["boundaries"] == BOUNDS_4H


# ------------------------------------------------------------------ missing and malformed inputs
def test_a_missing_registry_is_an_empty_dict_not_an_error(tmp_path):
    assert lab.market_meta(path=tmp_path / "nope.json", meta_path=tmp_path / "meta.json") == {}


def test_a_registry_with_no_markets_key_falls_back_to_the_slow_path(tmp_path, monkeypatch):
    """Giving up quietly and returning nothing would make every Market use default boundaries, which
    silently changes which bars are the holdout. Correctness beats speed here."""
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"version": 1, "candidates": {}}), encoding="utf-8")
    used = []
    monkeypatch.setattr(lab, "load_registry",
                        lambda *a, **k: used.append(1) or {"markets": {"X:4h": {"boundaries": BOUNDS_4H}}})

    meta = lab.market_meta(path=path, meta_path=tmp_path / "meta.json")
    assert used == [1], "an unparseable markets block must fall back, not return empty"
    assert meta["X:4h"]["boundaries"] == BOUNDS_4H


# ------------------------------------------------------------------ writing keeps the cache honest
def test_save_registry_invalidates_the_sidecar_rather_than_rewriting_it(tmp_path):
    """The first version of this REWROTE the sidecar from the in-memory registry, and that corrupted
    the live one on 8 October 2026.

    Two processes save the registry concurrently. Each would publish its own view of `markets`, and the
    last sidecar write wins regardless of which registry write won. The live sidecar ended up claiming
    candidates_tried 25 and a holdout starting 2022-02-15 while the registry on disk said 97,133 and
    2024-08-30. Every Market built from it used a holdout 2.5 years longer than the real one, which
    silently includes bars selection had already seen, so an afternoon of survey numbers was measured
    against something that was not a holdout.

    Deleting cannot publish a wrong value.
    """
    registry = tmp_path / "registry.json"
    cache = tmp_path / "meta.json"
    cache.write_text(json.dumps({"markets": {"stale": {"boundaries": None}}}), encoding="utf-8")
    lab.save_registry({"version": 1, "markets": {"XAUUSD:4h": {"candidates_tried": 5,
                                                               "boundaries": BOUNDS_4H}},
                       "candidates": {}}, path=registry, meta_path=cache)

    assert not cache.exists(), "a stale sidecar survived a registry save and will be served as current"
    assert lab.market_meta(path=registry, meta_path=cache)["XAUUSD:4h"]["boundaries"] == BOUNDS_4H


def test_a_save_of_another_registry_never_touches_the_live_sidecar(tmp_path):
    """The second half of the same bug: meta_path defaulted to the module-level MARKET_META_PATH, so a
    save of ANY other registry - sandboxed, temporary, a test - reached the live sidecar."""
    other = tmp_path / "some_other_registry.json"
    lab.save_registry({"version": 1, "markets": {}, "candidates": {}}, path=other)

    assert lab._meta_path_for(other) != lab.MARKET_META_PATH
    assert lab._meta_path_for(other).parent == other.parent, \
        "a registry's sidecar must live beside that registry, not beside the live one"


def test_the_live_registry_still_maps_to_the_live_sidecar():
    assert lab._meta_path_for(lab.REGISTRY_PATH) == lab.MARKET_META_PATH


def _retired_save_registry_refreshes_the_sidecar(tmp_path):
    """Retired on 8 October 2026: refreshing the sidecar on save is what corrupted it. Kept as a
    record of the behaviour that was wrong, renamed so pytest does not collect it."""
    registry = tmp_path / "registry.json"
    cache = tmp_path / "meta.json"
    lab.save_registry({"version": 1, "markets": {"XAUUSD:4h": {"candidates_tried": 5,
                                                               "boundaries": BOUNDS_4H}},
                       "candidates": {}}, path=registry, meta_path=cache)

    assert cache.exists()
    assert json.loads(cache.read_text(encoding="utf-8"))["markets"]["XAUUSD:4h"]["boundaries"] == BOUNDS_4H
    assert cache.stat().st_mtime >= registry.stat().st_mtime, \
        "the sidecar must not be older than the registry it describes, or it reads as stale forever"


def test_the_survey_no_longer_parses_the_whole_registry():
    """The regression that caused the outage: a load_registry() call on a per-family or per-candidate
    path. Both call sites in direction_sweep must stay on the cheap accessor."""
    import inspect

    from src import direction_sweep
    source = inspect.getsource(direction_sweep)
    assert "lab.market_meta()" in source
    assert "lab.load_registry()" not in source, \
        "direction_sweep is parsing the full registry again; that is what killed the survey"
