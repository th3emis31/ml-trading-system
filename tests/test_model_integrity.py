"""System Doctor model-integrity check: live champions may only change in the 05:30 learning window or by a logged restore."""
import hashlib
import json
import os
from datetime import datetime

from src import system_doctor as doc


def _model_file_written_at(folder, name, when, content=b"model"):
    path = folder / name
    path.write_bytes(content)
    stamp = when.timestamp()
    os.utime(path, (stamp, stamp))
    return path


def test_files_written_in_the_learning_window_pass(tmp_path):
    _model_file_written_at(tmp_path, "xauusd_model.joblib", datetime(2026, 9, 16, 5, 31))
    result = doc.check_model_integrity(models_dir=tmp_path, restores_path=tmp_path / "no_restores.json")
    assert result["status"] == "ok"


def test_a_file_written_by_a_test_run_fails(tmp_path):
    _model_file_written_at(tmp_path, "btcusd_model.joblib", datetime(2026, 9, 16, 18, 25))
    result = doc.check_model_integrity(models_dir=tmp_path, restores_path=tmp_path / "no_restores.json")
    assert result["status"] == "fail" and "btcusd_model.joblib" in result["summary"]


def test_a_logged_restore_passes_outside_the_window_until_the_file_changes(tmp_path):
    path = _model_file_written_at(tmp_path, "xauusd_model.joblib", datetime(2026, 9, 14, 12, 4), content=b"champion")
    log = tmp_path / "model_restores.json"
    log.write_text(json.dumps([{"files": {"xauusd_model.joblib": hashlib.sha256(b"champion").hexdigest()}}]), encoding="utf-8")
    assert doc.check_model_integrity(models_dir=tmp_path, restores_path=log)["status"] == "ok"
    path.write_bytes(b"overwritten by a test")
    stamp = datetime(2026, 9, 16, 18, 25).timestamp()
    os.utime(path, (stamp, stamp))
    assert doc.check_model_integrity(models_dir=tmp_path, restores_path=log)["status"] == "fail"


def test_the_check_runs_in_every_doctor_pass():
    source = open(doc.__file__, encoding="utf-8").read()
    assert "check_resources, check_model_integrity]" in source
