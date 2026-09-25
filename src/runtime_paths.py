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


def _owned_dir(env_name: str, folder: str) -> Path:
    """One of the folders the system owns, in precedence order.

    1. its own variable, which the test suite sets to a temporary folder;
    2. ``I40_HOME``/<folder>, so one variable moves the whole system to a USB drive or a second PC;
    3. the plain relative name, which is what the app has always used.

    Three is still the default precisely because it is the behaviour on this machine: setting nothing
    changes nothing, and portability is something the owner opts into by naming a home.
    """
    override = os.environ.get(env_name)
    if override:
        return Path(override)
    home = os.environ.get(HOME_ENV)
    if home:
        return Path(home) / folder
    return Path(folder)


def smartentry_models_dir() -> Path:
    """Folder holding the live champion models (``models`` unless redirected - see ``_owned_dir``)."""
    return _owned_dir(MODELS_DIR_ENV, "models")


def smartentry_data_dir() -> Path:
    """Folder holding learning decisions and learning history (``data`` unless redirected)."""
    return _owned_dir(DATA_DIR_ENV, "data")


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


def cycle_health(last_cycle: Optional[dict], every_minutes: int, now) -> dict:
    """Whether the scheduled task is still running this strategy, from the last cycle's own timestamp.

    A strategy that stops being called looks identical to one that finds no setup, so the age of the last cycle is
    reported explicitly and called late once it passes twice its interval.
    """
    stamp = (last_cycle or {}).get("at")
    if not stamp:
        return {"available": False, "reason": "no cycle has run yet", "every_minutes": every_minutes}
    ran = None
    for pattern, width in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16)):   # the daily agent stamps to the minute
        try:
            ran = datetime.strptime(str(stamp)[:width], pattern).replace(tzinfo=timezone.utc)
            break
        except ValueError:
            continue
    if ran is None:
        return {"available": False, "reason": f"the last cycle time {stamp!r} is unreadable", "every_minutes": every_minutes}
    age = (now.astimezone(timezone.utc) - ran).total_seconds() / 60
    late = age > every_minutes * 2
    return {"available": True, "last_cycle_utc": str(stamp)[:19], "age_minutes": round(age, 1),
            "every_minutes": every_minutes, "late": late,
            "note": (f"the last cycle was {age:.0f} min ago; the task runs every {every_minutes} min, so it looks "
                     "stopped or blocked") if late else f"running on schedule, every {every_minutes} min"}


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


# ---------------------------------------------------------------------------
# Where this MACHINE keeps things - i40 Pilot build map step 9 (portability).
#
# The system's own files already travel: models/ and data/ resolve relative to wherever it runs, so
# copying the folder is enough. What did NOT travel were the paths to things INSTALLED on a
# particular PC - the MetaTrader terminals above all - which were written into the source. On another
# machine those are simply wrong, and a system that cannot be moved is not independent.
#
# So machine-specific locations live in ONE json file under the home folder, and every default here
# is the value this machine already used - nothing changes on this PC, and a new machine edits a file
# rather than the code.
# ---------------------------------------------------------------------------

HOME_ENV = "I40_HOME"
MACHINE_CONFIG_NAME = "machine.json"

# The terminals as this machine has them, used when the config file says nothing. Keys are stable
# names the code asks for; the values are what a different machine would change.
DEFAULT_MACHINE = {
    "terminals": {
        "mt5_strategies": "C:/Users/th_em/AppData/Roaming/MetaTrader/terminal64.exe",
        "mt5_panel": "C:/Program Files/MetaTrader 5/terminal64.exe",
        "mt4_bridge": "C:/Users/th_em/AppData/Roaming/CMC Markets MetaTrader 4/terminal.exe",
        "mt5_tester": "C:/Users/th_em/MT5_SwingTrend_Tester/terminal64.exe",
    },
    # A terminal's DATA folder is machine-specific twice over: the user profile AND the per-install
    # hash differ on another PC, so it cannot be derived from the executable path.
    "terminal_data": {
        "mt5_tester": ("C:/Users/th_em/AppData/Roaming/MetaQuotes/Terminal/"
                       "5163829A6BDAF7E3A6FE2C0F431EFD6B"),
    },
    "excel_workbook": "C:/Users/th_em/Desktop/Trading Dashboard/Trading-Business-Dashboard.xlsx",
    "note": ("Machine-specific paths. Edit these when the system moves to another PC; nothing else "
             "needs changing. A path that does not exist is reported, never guessed at."),
}


def i40_home() -> Path:
    """The portable root: everything the system owns lives under here.

    Defaults to the folder holding this repository, so behaviour is unchanged. Set ``I40_HOME`` to
    run the same code against a different copy - a USB drive, a second machine, a restored backup.
    """
    override = os.environ.get(HOME_ENV)
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1]


def machine_config_path() -> Path:
    return i40_home() / "config" / MACHINE_CONFIG_NAME


def machine_config() -> dict:
    """This machine's paths, falling back to the defaults above so a missing file changes nothing."""
    import json

    merged = {key: (dict(value) if isinstance(value, dict) else value)
              for key, value in DEFAULT_MACHINE.items()}
    try:
        stored = json.loads(machine_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return merged
    for key, value in (stored or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def installed_terminal(name: str) -> str:
    """One terminal's executable path by stable name, e.g. ``mt5_strategies``.

    Distinct from active_account.terminal_path, which answers "which terminal am I TRADING
    through"; this one answers "where is that terminal INSTALLED on this machine".

    Returns the configured string even when the file is absent: whether it EXISTS is a separate
    question, and the caller that checks can then say "configured here, not found" rather than
    silently falling back to whatever happens to exist on this machine.
    """
    return str((machine_config().get("terminals") or {}).get(name) or "")


def terminal_data_dir(name: str) -> str:
    """One terminal's DATA folder - where its MQL5/, .ini files and tester reports live."""
    return str((machine_config().get("terminal_data") or {}).get(name) or "")


def same_path(left, right) -> bool:
    """Do two path strings name the same file? Windows is case-insensitive and mixes separators.

    The config writes forward slashes, because a json file full of escaped backslashes is a trap to
    edit by hand, while Windows reports a process's ExecutablePath with backslashes. Comparing those
    as plain strings never matches, which would turn "the terminal is running" into a silent and
    permanent failure - so every comparison against a configured path goes through here.
    """
    if not left or not right:
        return False
    return os.path.normcase(os.path.normpath(str(left))) == os.path.normcase(os.path.normpath(str(right)))


def machine_report() -> dict:
    """Which configured paths are actually present. For the doctor, and for moving machines."""
    config = machine_config()
    terminals = {}
    for name, path in (config.get("terminals") or {}).items():
        terminals[name] = {"path": path, "exists": bool(path) and Path(path).exists()}
    workbook = config.get("excel_workbook") or ""
    return {
        "home": str(i40_home()),
        "config_file": str(machine_config_path()),
        "config_file_exists": machine_config_path().exists(),
        "terminals": terminals,
        "excel_workbook": {"path": workbook, "exists": bool(workbook) and Path(workbook).exists()},
        "missing": [name for name, row in terminals.items() if not row["exists"]],
    }
