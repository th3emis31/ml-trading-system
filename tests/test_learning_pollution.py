"""Test-polluted learning runs stay on disk but are left out of the learning curve (15-16 Sep 2026).

Also checks the isolation itself: while pytest runs, the model and data roots point away from the live folders.
"""
import json
import os
from datetime import datetime
from pathlib import Path

from src import learning_curve as lc
from src.runtime_paths import smartentry_data_dir, smartentry_models_dir


def _json_to(path, rows):
    path.write_text(json.dumps(rows), encoding="utf-8")


def test_polluted_runs_are_excluded_from_the_curve_and_counted(tmp_path):
    clean = {"symbol": "XAUUSD", "status": "trained", "accuracy": 0.53, "trained_at": "2026-09-14 12:04:39", "rows": 2263}
    polluted = {"symbol": "XAUUSD", "status": "trained", "accuracy": 0.61, "trained_at": "2026-09-16 18:10:28", "rows": 2276}
    _json_to(tmp_path / "xauusd_daily_history.json", [clean, polluted])
    _json_to(tmp_path / "learning_decisions.json",
             [{**clean, "rf_promoted": True}, {**polluted, "rf_promoted": False, "test_polluted": True}])
    curve = lc.build_learning_curve(tmp_path, now=datetime(2026, 9, 16, 19, 0))
    gold = curve["symbols"]["XAUUSD"]
    assert [p["trained_at"] for p in gold["points"]] == ["2026-09-14 12:04:39"]
    assert curve["excluded_test_polluted_runs"] == 1


def test_tests_never_see_the_live_model_or_data_folders():
    live_root = Path(__file__).resolve().parents[1]
    assert os.environ.get("SMARTENTRY_MODELS_DIR") and os.environ.get("SMARTENTRY_DATA_DIR")
    assert smartentry_models_dir().resolve() != (live_root / "models").resolve()
    assert smartentry_data_dir().resolve() != (live_root / "data").resolve()


def test_model_status_last_trained_ignores_test_polluted_runs(tmp_path, monkeypatch):
    import app as app_module

    _json_to(tmp_path / "xauusd_daily_history.json", [
        {"symbol": "XAUUSD", "status": "trained", "trained_at": "2026-09-14 12:04:39", "rows": 2263},
        {"symbol": "XAUUSD", "status": "trained", "trained_at": "2026-09-16 18:24:43", "rows": 2276},
    ])
    _json_to(tmp_path / "learning_decisions.json", [
        {"symbol": "XAUUSD", "trained_at": "2026-09-16 18:24:43", "test_polluted": True},
    ])
    monkeypatch.setattr(app_module, "DATA_DIR", tmp_path)
    status = app_module.get_model_status("XAUUSD")
    assert status["last_trained_at"] == "2026-09-14 12:04:39"
    assert status["rows"] == 2263
