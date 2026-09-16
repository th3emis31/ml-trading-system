"""Every app path that retrains needs POST and the control secret (16 Sep 2026).

After /api/train-daily and /api/train-weekly were locked, four more paths could still start a full retrain:
GET /api/chart-learn/<symbol>, POST /api/quality-retrain, POST /api/screenshot-learn and the JARVIS
"check learning" / "system update" intents. A stand-in learner fails the test if any of them trains without the secret.
"""
import io
from pathlib import Path

import pytest

import app as app_module
from src import execution_guard


class _LearnerThatMustNotRun:
    calls = []

    def __init__(self, symbol):
        self.symbol = symbol

    def run_cycle(self, *args, **kwargs):
        _LearnerThatMustNotRun.calls.append(self.symbol)
        return {"symbol": self.symbol, "status": "trained"}


@pytest.fixture
def locked_client(tmp_path, monkeypatch):
    monkeypatch.setattr(execution_guard, "SECRET_PATH", tmp_path / "control_api.json")
    _LearnerThatMustNotRun.calls = []
    monkeypatch.setattr(app_module, "DailyLearner", _LearnerThatMustNotRun)
    monkeypatch.setattr(app_module, "build_chart_insights", lambda symbol: {"symbol": symbol})
    app_module.app.config["TESTING"] = True
    secret = execution_guard.load_or_create_secret(tmp_path / "control_api.json")
    return app_module.app.test_client(), {execution_guard.SECRET_HEADER: secret}


def test_chart_learn_is_post_only_with_the_secret(locked_client):
    http, auth = locked_client
    assert http.get("/api/chart-learn/XAUUSD").status_code == 405
    assert http.post("/api/chart-learn/XAUUSD").status_code == 403
    assert _LearnerThatMustNotRun.calls == []
    response = http.post("/api/chart-learn/XAUUSD", headers=auth)
    assert response.status_code == 200 and _LearnerThatMustNotRun.calls == ["XAUUSD"]


def test_quality_retrain_needs_the_secret(locked_client):
    http, _ = locked_client
    assert http.post("/api/quality-retrain", json={}).status_code == 403
    assert _LearnerThatMustNotRun.calls == []


def test_screenshot_learn_retrain_needs_the_secret(locked_client):
    http, _ = locked_client
    form = {"symbol": "XAUUSD", "file": (io.BytesIO(b"not really a png"), "chart.png")}
    response = http.post("/api/screenshot-learn", data=form, content_type="multipart/form-data")
    assert response.status_code == 403
    assert _LearnerThatMustNotRun.calls == []


def test_jarvis_learning_intents_only_retrain_with_the_secret(locked_client):
    http, auth = locked_client
    with app_module.app.test_request_context("/api/jarvis-command", method="POST"):
        assert app_module._learning_retrain_secret_ok() is False
    with app_module.app.test_request_context("/api/jarvis-command", method="POST", headers=auth):
        assert app_module._learning_retrain_secret_ok() is True
    source = Path(app_module.__file__).read_text(encoding="utf-8")
    for intent in ("check_learning", "system_update"):
        block = source.split(f"elif intent == '{intent}':", 1)[1][:1200]
        assert block.index("_learning_retrain_secret_ok()") < block.index("run_cycle('daily')"), intent
