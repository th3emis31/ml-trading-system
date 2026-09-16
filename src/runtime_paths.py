"""Where the system reads and writes its live models and learning records.

The defaults are the paths the app has always used: ``models`` and ``data``, relative to the working folder.
The test suite points them at a temporary folder through the environment variables below (tests/conftest.py sets
them before any ``src`` module is imported), so no test can overwrite the live champions or the learning history.

Why this exists: from 2026-09-15 18:38:59 until 2026-09-16, test_lstm.py, test_pipeline.py and
test_ensemble_integration.py trained straight into the live ``models/`` folder and wrote
``data/learning_decisions.json`` and the per-symbol learning history, replacing the models behind /api/signals.
The live app never sets these variables.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

MODELS_DIR_ENV = "SMARTENTRY_MODELS_DIR"
DATA_DIR_ENV = "SMARTENTRY_DATA_DIR"


def smartentry_models_dir() -> Path:
    """Folder holding the live champion models (``models`` unless the test suite redirects it)."""
    return Path(os.environ.get(MODELS_DIR_ENV) or "models")


def smartentry_data_dir() -> Path:
    """Folder holding learning decisions and learning history (``data`` unless the test suite redirects it)."""
    return Path(os.environ.get(DATA_DIR_ENV) or "data")


# The live champions may only be retrained in this local-time window: the SmartEntry Daily Learning task runs at
# 05:30. On 16 Sep 2026 an on-demand retrain at 18:43 promoted new XAUUSD/BTCUSD models into the live folder
# outside it, and training writes the challenger straight over the live files, so the gate refuses before any write.
LEARNING_WINDOW = ("05:25", "06:30")
OFFSCHEDULE_TRAINING_ENV = "SMARTENTRY_ALLOW_OFFSCHEDULE_TRAINING"


def live_training_allowed(now: datetime | None = None) -> tuple[bool, str]:
    """Whether a learning cycle may touch the live champion models now, and why."""
    if os.environ.get(MODELS_DIR_ENV):
        return True, "models folder redirected away from the live champions (tests or a sandbox)"
    if os.environ.get(OFFSCHEDULE_TRAINING_ENV) == "1":
        return True, f"owner override {OFFSCHEDULE_TRAINING_ENV}=1"
    start, end = LEARNING_WINDOW
    clock = (now or datetime.now()).strftime("%H:%M")
    if start <= clock <= end:
        return True, f"inside the {start}-{end} learning window"
    return False, (f"Blocked: {clock} is outside the {start}-{end} learning window. The live champions are retrained only "
                   f"by the SmartEntry Daily Learning task at 05:30; set {OFFSCHEDULE_TRAINING_ENV}=1 for a deliberate "
                   "owner run. Nothing was trained and no model file was written.")
