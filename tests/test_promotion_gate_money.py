"""The promotion gate decides on money, not on accuracy.

Measured against the system's own record on 20 September 2026: across 41 head-to-head runs the accuracy
winner and the money winner agreed 17 times and disagreed 17 - a coin flip - and the old gate, which tested
accuracy first and only reached the money checks afterwards, KEPT THE MODEL THAT MADE LESS MONEY IN 17 OF 41
RUNS. Replaying all 41 through the new gate picks the money winner 41 times.
"""
import pytest

from src.model_promotion import accuracy_chance_band, decide_rf, promoted_model_is_profitable


def _model(accuracy, ret, dd=5.0, trades=40, rows=565):
    return {"accuracy": accuracy, "total_return_pct": ret, "max_drawdown_pct": dd,
            "trades": trades, "rows": rows}


def test_the_chance_band_widens_as_the_sample_shrinks():
    assert accuracy_chance_band(565) == pytest.approx(0.0583, abs=0.001)
    assert accuracy_chance_band(100) > accuracy_chance_band(565)
    assert accuracy_chance_band(0) == 1.0, "nothing to compare means every gap is noise"


def test_the_real_20_september_gold_decision_is_reversed():
    """The old gate kept -5.653 % over -2.239 % because its accuracy was 3.2 points higher - a gap
    inside the 5.8-point chance band."""
    champion = _model(0.5345, -5.653, dd=6.51)
    challenger = _model(0.5027, -2.239, dd=4.20)
    promote, why = decide_rf(champion, challenger, champion_age_days=3)
    assert promote is True
    assert "after-cost return" in why and "inside" in why


def test_the_real_20_september_bitcoin_decision_is_reversed():
    promote, _ = decide_rf(_model(0.5279, -11.613, dd=12.11), _model(0.5105, -10.518, dd=11.01), 3)
    assert promote is True


def test_a_real_accuracy_collapse_is_still_refused():
    """Accuracy is demoted to a sanity check, not removed: a gap far outside the chance band still blocks."""
    promote, why = decide_rf(_model(0.60, -5.0), _model(0.40, +9.0), champion_age_days=3)
    assert promote is False and "chance band" in why
    assert "beyond" in why


def test_a_worse_drawdown_still_blocks_a_better_return():
    promote, why = decide_rf(_model(0.52, -1.0, dd=4.0), _model(0.52, +1.0, dd=9.0), 3)
    assert promote is False and "drawdown" in why


def test_the_champion_stays_when_the_challenger_earns_less():
    promote, why = decide_rf(_model(0.50, +4.0), _model(0.53, +1.0), 3)
    assert promote is False, "a higher accuracy does not buy a promotion any more"
    assert "champion stays live on return" in why


def test_accuracy_decides_only_when_there_is_no_return_to_compare():
    champion = {"accuracy": 0.50, "rows": 565, "trades": 0, "max_drawdown_pct": 0.0}
    better = {"accuracy": 0.55, "rows": 565, "trades": 0, "max_drawdown_pct": 0.0}
    worse = {"accuracy": 0.45, "rows": 565, "trades": 0, "max_drawdown_pct": 0.0}
    assert decide_rf(champion, better, 3)[0] is True
    assert decide_rf(champion, worse, 3)[0] is False


def test_a_promotion_is_not_the_same_as_a_profitable_model():
    """Every gated run on record had the live model losing money, so a promotion can mean 'loses less'."""
    losing = {"rf_promoted": True, "rf_challenger": {"total_return_pct": -2.239}}
    winning = {"rf_promoted": True, "rf_challenger": {"total_return_pct": +3.1}}
    kept = {"rf_promoted": False, "rf_champion": {"total_return_pct": -5.653}}
    assert promoted_model_is_profitable(losing) is False
    assert promoted_model_is_profitable(winning) is True
    assert promoted_model_is_profitable(kept) is False, "it judges whatever now serves live"
    assert promoted_model_is_profitable({"rf_promoted": True}) is None


def test_the_gate_still_handles_the_empty_cases():
    assert decide_rf(None, None, None)[0] is True
    assert decide_rf({"accuracy": 0.5}, None, None)[0] is False
    assert decide_rf(None, _model(0.5, 1.0), None)[0] is True
