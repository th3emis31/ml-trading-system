import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.daily_learning import DailyLearner
from src.lstm_model import LSTMTrader
from src.data import generate_synthetic_data


def test_daily_learner_runs():
    learner = DailyLearner("XAUUSD")
    result = learner.run_cycle()
    assert result["status"] == "trained"


def test_lstm_trainer_builds_model():
    data = generate_synthetic_data("BTCUSD", start_date="2024-01-01", end_date="2024-06-30", n=220)
    trainer = LSTMTrader("BTCUSD")
    model = trainer.train(data)
    assert model is not None
