from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data import generate_synthetic_data
from src.features import build_features
from src.train import train_model


def test_synthetic_data_generation():
    df = generate_synthetic_data("XAUUSD", start_date="2024-01-01", end_date="2024-02-01", n=120)
    assert not df.empty
    assert {"open", "high", "low", "close", "volume"}.issubset(df.columns)


def test_feature_pipeline():
    df = generate_synthetic_data("BTCUSD", start_date="2024-01-01", end_date="2024-02-01", n=120)
    enriched = build_features(df)
    assert "target" in enriched.columns
    assert "return_1d" in enriched.columns
    assert enriched.shape[0] > 0


def test_model_training():
    df = generate_synthetic_data("XAUUSD", start_date="2024-01-01", end_date="2024-02-01", n=180)
    enriched = build_features(df)
    model, metrics = train_model(enriched, "XAUUSD")
    assert model is not None
    assert metrics["accuracy"] >= 0.0
