"""Shared pytest fixtures."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

from src.data import generate_synthetic_data


@pytest.fixture(scope="module")
def bars():
    """3,000 synthetic 4h gold bars from 2021 (Strategy Lab and strategy book tests)."""
    frame = generate_synthetic_data("XAUUSD", start_date="2020-01-01", end_date="2024-12-31", n=3000).reset_index(drop=True)
    if "volume" not in frame.columns:
        frame["volume"] = 0.0
    frame["datetime"] = pd.date_range("2021-01-01", periods=len(frame), freq="4h", tz="UTC")
    return frame
