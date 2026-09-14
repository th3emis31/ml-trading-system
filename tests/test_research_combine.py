import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

from src import edge_research as er
from src import research_combine as rc
from src.data import generate_synthetic_data


def _candidate(config, model, pf, trades=60, folds=4, ret=5.0):
    return {"config": config, "model": model, "min_ev": 0.1, "quantile": None, "trades": trades,
            "profit_factor": pf, "total_return_pct": ret, "expectancy_pct": 0.1, "folds_profitable": folds}


def _piece(config, model, pf, holdout_pf, data_end="2026-09-11 17:00"):
    candidate = _candidate(config, model, pf)
    return {
        "available": True, "symbol": "BTCUSD", "interval": "4h", "bars": 1000, "data_start": "2020-01-01 00:00",
        "data_end": data_end, "mtf": False, "validation_folds": [1, 2, 3, 4, 5], "holdout_folds": [6, 7, 8],
        "candidates_all": [candidate],
        "candidate_holdouts": {er.candidate_key(candidate): {"trades": 120, "profit_factor": holdout_pf,
                                                            "max_drawdown_pct": 10.0, "total_return_pct": 12.0,
                                                            "buy_and_hold_pct": 5.0}},
        "baselines_by_config": {config: {"always_long": {"trades": 100}}},
    }


def test_combine_picks_on_validation_and_reports_that_candidates_holdout():
    pieces = [_piece("tight", "rf", pf=1.1, holdout_pf=3.0), _piece("wide", "xgb", pf=1.6, holdout_pf=0.8)]
    result = rc.combine_reports(pieces, selection="robust")
    # Chosen by validation PF (1.6), even though the other piece's holdout looks better.
    assert result["selection"]["chosen"]["model"] == "xgb"
    assert result["holdout"]["metrics"]["profit_factor"] == 0.8
    assert result["baselines_holdout"] == {"always_long": {"trades": 100}}
    assert result["configurations_tried"] == 2


def test_combine_refuses_pieces_from_different_data_windows():
    with pytest.raises(ValueError):
        rc.combine_reports([_piece("tight", "rf", 1.1, 1.0), _piece("wide", "rf", 1.2, 1.0, data_end="2026-09-12 17:00")])


def test_full_run_and_combined_pieces_choose_the_same_candidate():
    frame = generate_synthetic_data("XAUUSD", start_date="2024-01-01", end_date="2024-12-31", n=2600)
    frame["datetime"] = pd.date_range("2024-01-01", periods=2600, freq="h", tz="UTC")
    configs = [{"name": "tight", "sl_atr": 1.0, "tp_atr": 1.5, "horizon": 12},
               {"name": "balanced", "sl_atr": 1.0, "tp_atr": 2.0, "horizon": 24}]
    kwargs = dict(n_folds=5, holdout_folds=2, models=("logit",), data=frame, selection="robust")
    full = er.run_edge_research("XAUUSD", "1h", configs=configs, **kwargs)
    pieces = [er.run_edge_research("XAUUSD", "1h", configs=[cfg], **kwargs) for cfg in configs]
    combined = rc.combine_reports(pieces, selection="robust")
    assert er.candidate_key(combined["selection"]["chosen"]) == er.candidate_key(full["selection"]["chosen"])
    assert combined["holdout"]["metrics"]["trades"] == full["holdout"]["metrics"]["trades"]
