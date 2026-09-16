from src import strategy_lab as lab
from src import stp_swap_lab as swap


def test_rules_only_change_exits_and_never_touch_the_base_spec():
    base = lab.EA_SPECS["tradingview"]
    before = repr(base)
    triple, any_night, day = (swap.apply_rule(base, r) for r in ("triple", "any", "day"))
    assert triple["exits"]["exit_before_triple_swap"] is True and "exit_before_rollover" not in triple["exits"]
    assert any_night["exits"]["exit_before_rollover"] is True
    assert day["exits"]["max_bars"] == 6 and triple["params"] == base["params"]
    assert swap.apply_rule(base, "none") == base and repr(base) == before


def test_base_specs_load_presets_from_registry_by_id():
    registry = {"candidates": {
        "a": {"id": "bf0f48e8cb55", "market": "XAUUSD:4h", "spec": {"family": "ema_pullback", "params": {"ema_fast": 13}, "exits": {}}},
        "b": {"id": "6e54ea0bf2ce", "market": "XAUUSD:4h", "spec": {"family": "ema_pullback", "params": {"ema_fast": 21}, "exits": {}}},
    }}
    specs = swap.base_specs(registry)
    assert set(specs) == {"tradingview_live", "steady", "trend_rider"}
    assert specs["steady"]["params"]["ema_fast"] == 13 and specs["trend_rider"]["params"]["ema_fast"] == 21
