"""The learning-window gate and caller attribution (16 Sep 2026).

An on-demand retrain at 18:43 promoted new models into the live folder outside the 05:30 learning window, and its
caller could not be traced. Learning cycles that would touch the live champions are now refused before any write
outside 05:25-06:30, and every decision records who asked for it.
"""
from datetime import datetime

from flask import Flask

import src.daily_learning as dl
from src import model_promotion as mp
from src import runtime_paths as rp


def test_live_training_is_refused_outside_the_window(monkeypatch):
    monkeypatch.delenv(rp.MODELS_DIR_ENV, raising=False)
    monkeypatch.delenv(rp.OFFSCHEDULE_TRAINING_ENV, raising=False)
    allowed, reason = rp.live_training_allowed(now=datetime(2026, 9, 16, 18, 43))
    assert allowed is False and "05:25-06:30" in reason
    assert rp.live_training_allowed(now=datetime(2026, 9, 17, 5, 31))[0] is True


def test_owner_override_and_redirected_models_are_allowed(monkeypatch):
    monkeypatch.delenv(rp.MODELS_DIR_ENV, raising=False)
    monkeypatch.setenv(rp.OFFSCHEDULE_TRAINING_ENV, "1")
    assert rp.live_training_allowed(now=datetime(2026, 9, 16, 18, 43))[0] is True
    monkeypatch.delenv(rp.OFFSCHEDULE_TRAINING_ENV)
    monkeypatch.setenv(rp.MODELS_DIR_ENV, "C:/somewhere/else")
    assert rp.live_training_allowed(now=datetime(2026, 9, 16, 18, 43))[0] is True


def test_a_blocked_cycle_fetches_nothing_trains_nothing_and_records_its_caller(tmp_path, monkeypatch):
    monkeypatch.setattr(mp, "DECISIONS_PATH", tmp_path / "learning_decisions.json")
    monkeypatch.setattr(dl, "live_training_allowed", lambda: (False, "Blocked: test"))

    def must_not_run(*args, **kwargs):
        raise AssertionError("a blocked cycle must not fetch data or train")

    monkeypatch.setattr(dl, "fetch_real_data", must_not_run)
    monkeypatch.setattr(dl, "train_model", must_not_run)
    learner = dl.DailyLearner("XAUUSD")
    learner.history_dir = tmp_path
    result = learner.run_cycle("daily")
    assert result["status"] == "blocked_outside_learning_window"
    assert result["rf_promoted"] is False and result["lstm_promoted"] is False
    recorded = mp.load_decisions(limit=None)[-1]
    assert recorded["status"] == "blocked_outside_learning_window"
    assert recorded["caller"]["pid"] > 0 and "argv" in recorded["caller"]
    assert not (tmp_path / "xauusd_daily_history.json").exists(), "a blocked cycle is not a training run"


def test_training_pages_render_and_no_longer_use_get_for_training():
    import app as app_module

    client = app_module.app.test_client()
    for page in ("/learn", "/pipeline/training"):
        response = client.get(page)
        assert response.status_code == 200, page
        html = response.get_data(as_text=True)
        assert "method: 'POST'" in html and "X-Control-Secret" in html, page
    assert client.get("/api/train-weekly").status_code == 405


def test_caller_includes_the_http_request_inside_the_web_app():
    web = Flask("gate-test")
    with web.test_request_context("/api/train-daily", method="POST", headers={"User-Agent": "prefetch-bot"}):
        caller = mp.learning_caller_context()
    assert caller["http"]["path"] == "/api/train-daily"
    assert caller["http"]["method"] == "POST"
    assert "prefetch-bot" in caller["http"]["user_agent"]
