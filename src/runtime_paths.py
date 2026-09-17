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

import json
import os
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

MODELS_DIR_ENV = "SMARTENTRY_MODELS_DIR"
DATA_DIR_ENV = "SMARTENTRY_DATA_DIR"


def smartentry_models_dir() -> Path:
    """Folder holding the live champion models (``models`` unless the test suite redirects it)."""
    return Path(os.environ.get(MODELS_DIR_ENV) or "models")


def smartentry_data_dir() -> Path:
    """Folder holding learning decisions and learning history (``data`` unless the test suite redirects it)."""
    return Path(os.environ.get(DATA_DIR_ENV) or "data")


# The live champions may only be retrained around the SmartEntry Daily Learning task's own trigger. On 16 Sep 2026 an
# on-demand retrain at 18:43 promoted new XAUUSD/BTCUSD models into the live folder outside it, and training writes the
# challenger straight over the live files, so the gate refuses before any write.
#
# Clock: the task triggers at 05:30 Windows local time. The window is built from that trigger on the day in question,
# converted to UTC at runtime with the OS time-zone rules for that date, and compared with ``now`` in UTC, so a
# daylight-saving change moves the window with the task instead of breaking it.
#
# Marker: inside the window the gate also needs a fresh marker that the task writes as it starts
# (scripts/run_daily_learning.cmd -> ``python -m src.runtime_paths --mark-task``). An on-demand retrain that happens
# to fall inside the window has no fresh marker and is refused.
DAILY_LEARNING_TASK = "SmartEntry Daily Learning"
DAILY_LEARNING_TRIGGER_LOCAL = "05:30"   # must match the task definition (src/system_doctor.py TASKS)
WINDOW_BEFORE_MIN = 5
WINDOW_AFTER_MIN = 60
MARKER_MAX_AGE_MIN = 60
LEARNING_WINDOW = ("05:25", "06:30")     # the same window as local clock strings, for messages
OFFSCHEDULE_TRAINING_ENV = "SMARTENTRY_ALLOW_OFFSCHEDULE_TRAINING"


def _to_utc(moment: datetime | None, local_tz: tzinfo | None = None) -> datetime:
    """Aware UTC time. A naive ``moment`` is wall-clock time in ``local_tz`` (the OS zone when None)."""
    if moment is None:
        return datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=local_tz) if local_tz else moment.astimezone()
    return moment.astimezone(timezone.utc)


def learning_window_utc(now: datetime | None = None, local_tz: tzinfo | None = None) -> tuple[datetime, datetime]:
    """The learning window (UTC start, UTC end) containing ``now``, or else the next one.

    Built from the task's local trigger time on yesterday's, today's and tomorrow's local date (so a trigger near
    midnight also works), each converted to UTC with that date's offset.
    """
    now_utc = _to_utc(now, local_tz)
    local_day = (now_utc.astimezone(local_tz) if local_tz else now_utc.astimezone()).date()
    hour, minute = (int(x) for x in DAILY_LEARNING_TRIGGER_LOCAL.split(":"))
    windows = []
    for offset in (-1, 0, 1):
        day = local_day + timedelta(days=offset)
        trigger = _to_utc(datetime(day.year, day.month, day.day, hour, minute), local_tz)
        windows.append((trigger - timedelta(minutes=WINDOW_BEFORE_MIN), trigger + timedelta(minutes=WINDOW_AFTER_MIN)))
    for start, end in windows:
        if start <= now_utc <= end:
            return start, end
    return next((w for w in windows if w[0] > now_utc), windows[-1])


def inside_learning_window(moment: datetime | None = None, local_tz: tzinfo | None = None) -> bool:
    start, end = learning_window_utc(moment, local_tz)
    return start <= _to_utc(moment, local_tz) <= end


def read_latest_json(path, missing_reason: str) -> dict:
    """A module's cached "latest" file as a dict, or {"available": False, "reason": ...} when it has not been written
    yet or cannot be read. Shared by src/plan_journal.py and src/positioning.py."""
    path = Path(path)
    if not path.exists():
        return {"available": False, "reason": missing_reason}
    try:
        return {"available": True, **json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, ValueError) as exc:
        return {"available": False, "reason": f"{path} is unreadable: {exc}"}


def daily_learning_marker_path() -> Path:
    return smartentry_data_dir() / "learning" / "daily_learning_task_marker.json"


def write_daily_learning_marker(task: str = DAILY_LEARNING_TASK, now: datetime | None = None,
                                path: Path | None = None) -> dict:
    """Written by the Daily Learning task as it starts: its task name and start time (UTC)."""
    record = {"task": task, "started_utc": _to_utc(now).strftime("%Y-%m-%d %H:%M:%S"), "pid": os.getpid()}
    path = Path(path) if path else daily_learning_marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=1), encoding="utf-8")
    return record


def daily_learning_marker_status(now: datetime | None = None, local_tz: tzinfo | None = None,
                                 path: Path | None = None) -> tuple[bool, str]:
    """Whether the task's marker is fresh: the right task name, started inside the current window, at most
    MARKER_MAX_AGE_MIN minutes ago and not in the future."""
    path = Path(path) if path else daily_learning_marker_path()
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        started = datetime.strptime(record["started_utc"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except FileNotFoundError:
        return False, f"there is no start marker from the {DAILY_LEARNING_TASK} task ({path})"
    except (OSError, ValueError, KeyError, TypeError):
        return False, f"the start marker {path} is unreadable"
    if record.get("task") != DAILY_LEARNING_TASK:
        return False, f"the marker names task {record.get('task')!r}, not {DAILY_LEARNING_TASK!r}"
    now_utc = _to_utc(now, local_tz)
    age_min = (now_utc - started).total_seconds() / 60
    if age_min < -1 or age_min > MARKER_MAX_AGE_MIN:
        return False, f"the marker is stale (task started {record['started_utc']} UTC, {age_min:.0f} min ago)"
    start, end = learning_window_utc(now_utc, local_tz)
    if not start <= started <= end:
        return False, f"the marker start {record['started_utc']} UTC is outside this learning window"
    return True, f"{DAILY_LEARNING_TASK} started {record['started_utc']} UTC"


def live_training_allowed(now: datetime | None = None, local_tz: tzinfo | None = None,
                          marker_path: Path | None = None) -> tuple[bool, str]:
    """Whether a learning cycle may touch the live champion models now, and why.

    ``now`` may be aware (any zone) or naive (wall-clock time in ``local_tz``, the OS zone by default)."""
    if os.environ.get(MODELS_DIR_ENV):
        return True, "models folder redirected away from the live champions (tests or a sandbox)"
    if os.environ.get(OFFSCHEDULE_TRAINING_ENV) == "1":
        return True, f"owner override {OFFSCHEDULE_TRAINING_ENV}=1"
    now_utc = _to_utc(now, local_tz)
    start, end = learning_window_utc(now_utc, local_tz)
    window = (f"{start:%H:%M}-{end:%H:%M} UTC (the task's {DAILY_LEARNING_TRIGGER_LOCAL} local trigger "
              f"-{WINDOW_BEFORE_MIN}/+{WINDOW_AFTER_MIN} min)")
    tail = (f"The live champions are retrained only by the {DAILY_LEARNING_TASK} task; set {OFFSCHEDULE_TRAINING_ENV}=1 "
            "for a deliberate owner run. Nothing was trained and no model file was written.")
    if not start <= now_utc <= end:
        return False, f"Blocked: {now_utc:%Y-%m-%d %H:%M} UTC is outside the learning window {window}. {tail}"
    fresh, marker_reason = daily_learning_marker_status(now_utc, local_tz, marker_path)
    if not fresh:
        return False, f"Blocked: inside the learning window {window}, but {marker_reason}. {tail}"
    return True, f"inside the learning window {window}; {marker_reason}"


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Learning-window gate and the Daily Learning task's start marker.")
    parser.add_argument("--mark-task", metavar="TASK_NAME", help="write the start marker for this task")
    args = parser.parse_args(argv)
    if args.mark_task:
        record = write_daily_learning_marker(args.mark_task)
        print(f"marker {json.dumps(record)} -> {daily_learning_marker_path()}")
    start, end = learning_window_utc()
    print(f"window {start:%Y-%m-%d %H:%M}-{end:%H:%M} UTC; gate: {live_training_allowed()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
