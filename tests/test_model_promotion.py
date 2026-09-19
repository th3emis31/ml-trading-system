import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import model_promotion as mp
from src.data import generate_synthetic_data
from src.features import build_features

CHAMPION = {"accuracy": 0.55, "rows": 400, "trades": 0, "max_drawdown_pct": 0.0}


def _challenger(accuracy, trades=0, drawdown=0.0):
    return {"accuracy": accuracy, "rows": 400, "trades": trades, "max_drawdown_pct": drawdown}


def test_worse_challenger_keeps_champion():
    promoted, reason = mp.decide_rf(CHAMPION, _challenger(0.53), champion_age_days=2)
    assert promoted is False
    assert "restored" in reason


def test_equal_or_better_challenger_is_promoted():
    assert mp.decide_rf(CHAMPION, _challenger(0.55), champion_age_days=2)[0] is True
    assert mp.decide_rf(CHAMPION, _challenger(0.57), champion_age_days=2)[0] is True


def test_stale_champion_allows_small_tolerance():
    slightly_worse = _challenger(0.545)
    assert mp.decide_rf(CHAMPION, slightly_worse, champion_age_days=2)[0] is False
    assert mp.decide_rf(CHAMPION, slightly_worse, champion_age_days=mp.STALE_CHAMPION_DAYS + 1)[0] is True


def test_drawdown_guard_blocks_riskier_challenger():
    champion = {**CHAMPION, "trades": 10, "max_drawdown_pct": 10.0}
    promoted, reason = mp.decide_rf(champion, _challenger(0.58, trades=10, drawdown=20.0), champion_age_days=2)
    assert promoted is False
    assert "drawdown" in reason


def test_first_model_is_promoted():
    assert mp.decide_rf(None, _challenger(0.51), champion_age_days=None)[0] is True


def test_holdout_starts_after_challenger_training_rows():
    features = build_features(generate_synthetic_data("XAUUSD", n=800))
    holdout = mp.challenger_holdout(features)
    mask = features["quality_move"].astype(bool)
    rows = features[mask] if int(mask.sum()) >= 80 else features
    last_train_label = rows.index[int(len(rows) * 0.8) - 1]
    assert not holdout.empty
    assert holdout.index.min() >= features.index.get_loc(last_train_label) + 1 + mp.LABEL_HORIZON_BARS


def test_archive_and_restore_round_trip(tmp_path, monkeypatch):
    models = tmp_path / "models"
    models.mkdir()
    monkeypatch.setattr(mp, "MODELS_DIR", models)
    monkeypatch.setattr(mp, "ARCHIVE_DIR", models / "archive")
    (models / "xauusd_model.joblib").write_bytes(b"champion")
    (models / "xauusd_metrics.json").write_text('{"accuracy": 0.55}')

    archive = mp.archive_champion("XAUUSD")
    assert set(archive["files"]) == {"xauusd_model.joblib", "xauusd_metrics.json"}

    (models / "xauusd_model.joblib").write_bytes(b"challenger")
    mp.restore_files(archive, "XAUUSD", mp.RF_FILES)
    assert (models / "xauusd_model.joblib").read_bytes() == b"champion"
    assert Path(archive["path"]).exists()


def test_learner_refuses_synthetic_data(tmp_path, monkeypatch):
    """The guard has to survive the whole fallback chain: broker candles, then Yahoo, then generated prices.

    ``training_bars`` asks the app for broker candles before it ever calls ``fetch_real_data``, so
    patching only the Yahoo entry point leaves the learner training on whatever the live app happens
    to be serving and never reaches the guard at all. Both steps are blocked here.
    """
    import src.daily_learning as dl
    import src.mtf_data as mtf

    synthetic = generate_synthetic_data("BTCUSD", n=300)
    synthetic.attrs["source"] = "synthetic"
    monkeypatch.setattr(mtf, "fetch_app_bars", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(dl, "fetch_real_data", lambda *a, **k: synthetic)
    calls = []
    monkeypatch.setattr(dl, "train_model", lambda *a, **k: calls.append("trained"))
    monkeypatch.setattr(mp, "DECISIONS_PATH", tmp_path / "decisions.json")

    learner = dl.DailyLearner("BTCUSD")
    learner.history_dir = tmp_path
    entry = learner.run_cycle("daily")

    assert entry["status"] == "skipped_synthetic_data"
    assert calls == []
    assert mp.load_decisions()[-1]["status"] == "skipped_synthetic_data"


def test_learner_restores_champion_when_challenger_is_worse(tmp_path, monkeypatch):
    import src.daily_learning as dl

    models = tmp_path / "models"
    models.mkdir()
    monkeypatch.setattr(mp, "MODELS_DIR", models)
    monkeypatch.setattr(mp, "ARCHIVE_DIR", models / "archive")
    monkeypatch.setattr(mp, "DECISIONS_PATH", tmp_path / "decisions.json")
    (models / "xauusd_model.joblib").write_bytes(b"champion")
    (models / "xauusd_metrics.json").write_text(json.dumps({"accuracy": 0.56}))

    data = generate_synthetic_data("XAUUSD", n=900)
    data.attrs["source"] = "yahoo"
    monkeypatch.setattr(dl, "fetch_real_data", lambda *a, **k: data)

    def fake_train(df, symbol):
        (models / "xauusd_model.joblib").write_bytes(b"challenger")
        (models / "xauusd_metrics.json").write_text(json.dumps({"accuracy": 0.50}))
        return object(), {"accuracy": 0.50}

    monkeypatch.setattr(dl, "train_model", fake_train)
    monkeypatch.setattr(mp, "load_archived_rf", lambda archive, symbol: object())
    # run_cycle scores the challenger first, then the champion.
    scores = iter([_challenger(0.50), dict(CHAMPION, accuracy=0.56)])
    monkeypatch.setattr(mp, "evaluate_rf", lambda *a, **k: next(scores))

    class FakeLSTM:
        def __init__(self, symbol):
            pass

        def train(self, df):
            return None, {"accuracy": None, "status": "skipped_in_test"}

    monkeypatch.setattr(dl, "LSTMTrader", FakeLSTM)
    monkeypatch.setattr(dl, "save_lstm_metrics", lambda *a, **k: None)

    learner = dl.DailyLearner("XAUUSD")
    learner.history_dir = tmp_path
    entry = learner.run_cycle("daily")

    assert entry["rf_promoted"] is False
    assert (models / "xauusd_model.joblib").read_bytes() == b"champion"
    assert entry["accuracy"] == 0.56
    assert entry["challenger_accuracy"] == 0.50
    assert mp.load_decisions()[-1]["rf_promoted"] is False
