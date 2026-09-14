import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import learning_curve as lc


def test_trend_needs_a_change_bigger_than_chance():
    assert lc.trend([0.50] * 7 + [0.56] * 7)["label"] == "improving"
    assert lc.trend([0.56] * 7 + [0.50] * 7)["label"] == "worse"
    small = lc.trend([0.547] * 7 + [0.523] * 7)  # the -2.3 pt gold case: inside the chance band
    assert small["label"] == "no_real_change"
    assert small["noise_pts"] > abs(small["change_pts"])
    assert lc.trend([0.5, 0.6, None])["label"] == "insufficient"


def test_chance_band_shrinks_with_more_test_bars():
    assert round(lc.chance_band_pts(0.5, 450), 1) == 4.6
    assert lc.chance_band_pts(0.5, 5000) < lc.chance_band_pts(0.5, 450)


def test_trend_uses_shorter_windows_with_few_days():
    result = lc.trend([0.50, 0.50, 0.50, 0.57, 0.57, 0.57])
    assert result["label"] == "improving"
    assert result["window"] == 3
    assert round(result["change_pts"], 1) == 7.0


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)  # also used by tests/test_obsidian_notes.py
    path.write_text(json.dumps(data), encoding="utf-8")


def test_repeat_runs_on_one_day_count_once(tmp_path):
    rows = [{"symbol": "XAUUSD", "status": "trained", "rows": 2250, "accuracy": 0.57, "trained_at": f"2026-07-19 11:3{i}:00"}
            for i in range(4)]
    rows.append({"symbol": "XAUUSD", "status": "trained", "rows": 2250, "accuracy": 0.51, "trained_at": "2026-07-20 05:30:00"})
    _write(tmp_path / "xauusd_daily_history.json", rows)
    gold = lc.build_learning_curve(tmp_path, now=datetime(2026, 7, 20, 6, 0))["symbols"]["XAUUSD"]
    assert gold["summary"]["runs"] == 5
    assert gold["summary"]["days"] == 2
    assert gold["summary"]["repeat_runs_same_day"] == 3
    assert [p["counted"] for p in gold["points"]] == [False, False, False, True, True]


def test_build_learning_curve_reads_history_and_gates(tmp_path):
    _write(tmp_path / "xauusd_daily_history.json", [
        {"symbol": "XAUUSD", "status": "trained", "rows": 2250, "accuracy": 0.50 + i * 0.005, "lstm_accuracy": 0.5,
         "trained_at": f"2026-09-{i + 1:02d} 05:30:00"} for i in range(10)
    ] + [{"symbol": "XAUUSD", "status": "skipped_synthetic_data", "trained_at": "2026-09-11 05:30:00"}])
    _write(tmp_path / "learning_decisions.json", [
        {"symbol": "XAUUSD", "trained_at": "2026-09-10 05:30:00", "rf_promoted": False,
         "rf_champion": {"rows": 400, "accuracy": 0.53, "trades": 30, "expectancy_pct": 0.01, "total_return_pct": 0.3},
         "rf_challenger": {"rows": 400, "accuracy": 0.52, "trades": 40, "expectancy_pct": -0.02, "total_return_pct": -0.8}},
    ])
    curve = lc.build_learning_curve(tmp_path, now=datetime(2026, 9, 11, 12, 0))
    gold = curve["symbols"]["XAUUSD"]
    assert curve["available"] and curve["places_orders"] is False
    assert gold["summary"]["runs"] == 11 and gold["summary"]["skipped"] == 1 and gold["summary"]["days"] == 10
    assert gold["trend"]["rf"]["label"] == "no_real_change"  # +2.5 pts is inside the chance band
    assert gold["checked"] == {"runs": 1, "needed": 10, "enough": False, "new_better": 0, "tie": 0, "new_worse": 1,
                               "money_positive": 1, "money_negative_or_zero": 0}
    assert gold["latest_gate"]["live_return_pct"] == 0.3  # rejected challenger: the champion stays live
    assert curve["overall"]["label"] == "no_real_change"
    assert "1 of 10 needed" in curve["overall"]["detail"]
    assert curve["task"]["healthy"] is True
    assert curve["strategy_lab"]["available"] is False and curve["paper_trader"]["available"] is False


def test_task_is_unhealthy_when_learning_stops(tmp_path):
    _write(tmp_path / "btcusd_daily_history.json", [{"symbol": "BTCUSD", "status": "trained", "accuracy": 0.5,
                                                    "trained_at": "2026-09-01 05:30:00"}])
    curve = lc.build_learning_curve(tmp_path, now=datetime(2026, 9, 5, 5, 30))
    assert curve["task"]["healthy"] is False
    assert curve["overall"]["label"] == "stopped"


def test_empty_data_dir(tmp_path):
    curve = lc.build_learning_curve(tmp_path, now=datetime(2026, 9, 5))
    assert curve["available"] is True
    assert curve["symbols"] == {}
    assert curve["task"]["healthy"] is False
