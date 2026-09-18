"""The learning-window gate and caller attribution (16 Sep 2026).

An on-demand retrain at 18:43 promoted new models into the live folder outside the 05:30 learning window, and its
caller could not be traced. Learning cycles that would touch the live champions are now refused before any write
outside 05:25-06:30, and every decision records who asked for it.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask

import src.daily_learning as dl
from src import model_promotion as mp
from src import runtime_paths as rp


def test_live_training_is_refused_outside_the_window(monkeypatch, tmp_path):
    monkeypatch.delenv(rp.MODELS_DIR_ENV, raising=False)
    monkeypatch.delenv(rp.OFFSCHEDULE_TRAINING_ENV, raising=False)
    marker = tmp_path / "marker.json"
    rp.write_daily_learning_marker(now=datetime(2026, 9, 16, 18, 42), path=marker)
    allowed, reason = rp.live_training_allowed(now=datetime(2026, 9, 16, 18, 43), marker_path=marker)
    assert allowed is False and "outside the learning window" in reason, "a fresh marker never opens the gate outside the window"


def test_tomorrows_real_0530_local_run_is_allowed_with_its_marker(monkeypatch, tmp_path):
    """The task triggers at 05:30 Windows local time; the gate must accept that run in UTC, whatever the offset."""
    monkeypatch.delenv(rp.MODELS_DIR_ENV, raising=False)
    monkeypatch.delenv(rp.OFFSCHEDULE_TRAINING_ENV, raising=False)
    tomorrow = (datetime.now().astimezone() + timedelta(days=1)).date()
    run_local = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 5, 30).astimezone()   # OS zone and DST for that date
    run_utc = run_local.astimezone(timezone.utc)
    start, end = rp.learning_window_utc(run_utc)
    assert (start, end) == (run_utc - timedelta(minutes=5), run_utc + timedelta(minutes=60))

    marker = tmp_path / "learning" / "daily_learning_task_marker.json"
    assert rp.live_training_allowed(now=run_utc + timedelta(seconds=40), marker_path=marker)[0] is False, "no marker yet"
    rp.write_daily_learning_marker("SmartEntry Daily Learning", now=run_utc + timedelta(seconds=2), path=marker)
    allowed, reason = rp.live_training_allowed(now=run_utc + timedelta(seconds=40), marker_path=marker)
    assert allowed is True, reason
    assert rp.live_training_allowed(now=run_local.replace(tzinfo=None) + timedelta(minutes=2), marker_path=marker)[0] is True, \
        "a naive local clock reading gives the same answer"
    # the second symbol's cycle a minute later still passes; after the window it does not
    assert rp.live_training_allowed(now=run_utc + timedelta(minutes=2), marker_path=marker)[0] is True
    assert rp.live_training_allowed(now=run_utc + timedelta(minutes=61), marker_path=marker)[0] is False


def test_inside_the_window_a_missing_stale_or_foreign_marker_is_refused(monkeypatch, tmp_path):
    monkeypatch.delenv(rp.MODELS_DIR_ENV, raising=False)
    monkeypatch.delenv(rp.OFFSCHEDULE_TRAINING_ENV, raising=False)
    london = ZoneInfo("Europe/London")
    run = datetime(2026, 9, 17, 5, 30, tzinfo=london)
    marker = tmp_path / "marker.json"
    assert "no start marker" in rp.live_training_allowed(now=run, local_tz=london, marker_path=marker)[1]
    rp.write_daily_learning_marker(now=run - timedelta(days=1), path=marker)            # yesterday's run
    allowed, reason = rp.live_training_allowed(now=run, local_tz=london, marker_path=marker)
    assert allowed is False and "stale" in reason
    rp.write_daily_learning_marker("Someone Else", now=run, path=marker)
    assert rp.live_training_allowed(now=run, local_tz=london, marker_path=marker)[0] is False
    rp.write_daily_learning_marker(now=run - timedelta(minutes=10), path=marker)       # before the window opened
    allowed, reason = rp.live_training_allowed(now=run, local_tz=london, marker_path=marker)
    assert allowed is False and "outside this learning window" in reason


def test_window_follows_daylight_saving_in_utc():
    london = ZoneInfo("Europe/London")
    summer = rp.learning_window_utc(datetime(2026, 9, 17, 5, 40, tzinfo=london), local_tz=london)
    winter = rp.learning_window_utc(datetime(2026, 11, 2, 5, 40, tzinfo=london), local_tz=london)
    assert [f"{t:%H:%M}" for t in summer] == ["04:25", "05:30"]     # BST: 05:30 local = 04:30 UTC
    assert [f"{t:%H:%M}" for t in winter] == ["05:25", "06:30"]     # GMT: 05:30 local = 05:30 UTC
    change_day = rp.learning_window_utc(datetime(2026, 3, 29, 5, 30, tzinfo=london), local_tz=london)   # clocks went forward at 01:00 UTC
    assert f"{change_day[0]:%H:%M}" == "04:25"
    assert rp.inside_learning_window(datetime(2026, 9, 17, 4, 30, tzinfo=timezone.utc), local_tz=london)
    assert not rp.inside_learning_window(datetime(2026, 9, 17, 5, 31, tzinfo=timezone.utc), local_tz=london)


def test_task_script_writes_the_marker_before_training_and_trigger_matches_the_task():
    from src import system_doctor

    script = (Path(__file__).resolve().parents[1] / "scripts" / "run_daily_learning.cmd").read_text(encoding="utf-8")
    mark = script.index('-m src.runtime_paths --mark-task "SmartEntry Daily Learning"')
    assert mark < script.index("-m src.daily_learning")
    schedule = system_doctor.TASKS[rp.DAILY_LEARNING_TASK]["schedule"]
    assert schedule[schedule.index("/st") + 1] == rp.DAILY_LEARNING_TRIGGER_LOCAL


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


def test_learning_trains_on_broker_candles_and_says_so(monkeypatch):
    """Until 2026-09-18 the daily learning trained on Yahoo, which proxies XAUUSD with the GC=F futures contract and
    sits about 1 % away from the broker's spot. The models were learning a price series they would never trade at."""
    import pandas as pd

    from src import daily_learning

    broker = pd.DataFrame({"datetime": pd.date_range("2026-09-01", periods=50, freq="h", tz="UTC"),
                           "open": 4300.0, "high": 4310.0, "low": 4290.0, "close": 4305.0, "volume": 1.0})
    broker.attrs["source"] = "app:mt5:XAUUSD"
    monkeypatch.setattr(daily_learning, "fetch_app_bars", lambda *a, **k: broker, raising=False)
    monkeypatch.setitem(__import__("sys").modules, "src.mtf_data",
                        type("m", (), {"fetch_app_bars": staticmethod(lambda *a, **k: broker)}))

    frame = daily_learning.training_bars("XAUUSD", "120d", "1h")
    assert frame.attrs["source"] == "broker", "a run must record which prices it learned from"
    assert float(frame["close"].iloc[-1]) == 4305.0

    # the broker being unreadable falls back to Yahoo, and the record says it was a fallback rather than a choice
    empty = pd.DataFrame()
    monkeypatch.setitem(__import__("sys").modules, "src.mtf_data",
                        type("m", (), {"fetch_app_bars": staticmethod(lambda *a, **k: empty)}))
    yahoo = pd.DataFrame({"datetime": pd.date_range("2026-09-01", periods=10, freq="h"), "close": 4400.0})
    yahoo.attrs["source"] = "yahoo"
    monkeypatch.setattr(daily_learning, "fetch_real_data", lambda *a, **k: yahoo)
    assert daily_learning.training_bars("XAUUSD", "120d", "1h").attrs["source"] == "yahoo_fallback"
