"""Run this FIRST on a machine the system has never run on. It changes nothing and places no orders.

Build map step 9's remaining half. The path work is done and the system starts from the USB copy, but
"it works on another PC" is a claim that can only be settled on another PC. This is the thing that
settles it: one command that reports what this machine can and cannot give the system, names every
path that is wrong here, and writes the answer where the build map can read it.

    python scripts/first_run_on_new_machine.py

It is deliberately READ-ONLY apart from its own report. Nothing is installed, no config is rewritten,
no terminal is started and no order can be placed - because the first thing anyone does on a new
machine is run something to see if it works, and that must be the safest command in the project, not a
command that starts changing the machine to suit itself.

What it checks, in the order that decides whether anything else matters:

 1. Python and the packages the system cannot run without.
 2. Its own files - models, data, memory, the measured record - present and readable from here.
 3. This machine's installed software: MetaTrader terminals and the workbook, by name, found or not.
 4. An AI provider: the Claude CLI if there is internet, a local model if there is not.
 5. Whether it can actually READ a broker price here, which is the difference between installed and
    working.

The report goes to `data/system_health/first_run.json`, so when the owner brings the drive back the
question "did it work over there" has a recorded answer rather than a memory of one.
"""
from __future__ import annotations

import importlib
import json
import os
import platform
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Without these the system does not run at all. Anything not on this list degrades to a status field
# rather than a failure, which is the project's rule for optional integrations.
REQUIRED_PACKAGES = ("pandas", "numpy", "sklearn", "flask", "joblib")
OPTIONAL_PACKAGES = ("MetaTrader5", "tensorflow", "win32com", "openpyxl", "xgboost")

# The files that make this a system rather than a folder of code. BASELINE.md is first on purpose: it
# is every result ever measured, including the rejected ones, and it is what stops the same ground
# being re-walked.
OWN_FILES = (
    (".claude/memory/BASELINE.md", "every measured result, including the failures"),
    (".claude/memory/NOTES.md", "the handoff notes"),
    ("config/machine.json", "this machine's paths"),
    ("data", "the record of what the system actually did"),
    ("models", "the live champion models"),
    (".claude/skills", "the skills, each with its acceptance checks"),
)


def _ok(condition: bool) -> str:
    return "ok" if condition else "MISSING"


def check_python() -> dict:
    missing, present = [], []
    for name in REQUIRED_PACKAGES:
        try:
            importlib.import_module(name)
            present.append(name)
        except Exception:
            missing.append(name)
    optional = {}
    for name in OPTIONAL_PACKAGES:
        try:
            importlib.import_module(name)
            optional[name] = True
        except Exception:
            optional[name] = False
    version = tuple(sys.version_info[:2])
    return {"python": platform.python_version(), "executable": sys.executable,
            "version_ok": version >= (3, 9), "required_present": present,
            "required_missing": missing, "optional": optional,
            "ok": not missing and version >= (3, 9),
            "fix": "" if not missing else f"python -m pip install {' '.join(missing)}"}


def check_own_files() -> dict:
    rows = {}
    for relative, what in OWN_FILES:
        path = ROOT / relative
        exists = path.exists()
        size = 0
        if exists:
            try:
                size = (sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
                        if path.is_dir() else path.stat().st_size)
            except OSError:
                size = 0
        rows[relative] = {"what": what, "exists": exists, "megabytes": round(size / (1024 * 1024), 1)}
    return {"rows": rows, "ok": all(row["exists"] for row in rows.values()),
            "missing": [name for name, row in rows.items() if not row["exists"]]}


def check_machine() -> dict:
    from src.runtime_paths import machine_config_path, machine_report

    report = machine_report()
    missing = list(report["missing"])
    if not report["excel_workbook"]["exists"]:
        missing.append("excel_workbook")
    return {"home": report["home"], "config_file": str(machine_config_path()),
            "config_file_exists": report["config_file_exists"],
            "terminals": report["terminals"], "excel_workbook": report["excel_workbook"],
            "missing": missing, "ok": not missing,
            "fix": "" if not missing else (f"edit {machine_config_path()} so these point where they are "
                                           f"installed here: {', '.join(missing)}")}


def check_provider() -> dict:
    from src import ai_provider

    try:
        online = ai_provider.internet_reachable()
    except Exception:
        online = False
    try:
        status = ai_provider.provider_status()
    except Exception as exc:
        return {"ok": False, "internet": online, "reason": f"{type(exc).__name__}: {exc}"}

    # provider_status() returns {"providers": [ {...}, ... ], "usable": [...], "local_usable": [...],
    # "independent": bool} - a LIST of providers, not a mapping keyed by name. Reading it as a mapping
    # found nothing and reported "no AI provider" on a machine where the Claude CLI was working fine.
    usable = list(status.get("usable") or [])
    local_usable = list(status.get("local_usable") or [])
    return {"internet": online, "usable": usable, "local_usable": local_usable,
            "works_offline": bool(status.get("independent")),
            "verdict": status.get("verdict", ""),
            "providers": status.get("providers") or [],
            "ok": bool(usable),
            "fix": "" if status.get("independent") else
                   ("install Ollama and run 'ollama pull qwen2.5-coder:7b' for a provider that needs "
                    "no internet")}


def check_broker() -> dict:
    """Installed is not the same as working: can a price actually be read on this machine?"""
    try:
        import MetaTrader5 as mt5
    except Exception as exc:
        return {"ok": False, "reason": f"the MetaTrader5 package is not installed here ({type(exc).__name__})"}
    from src.runtime_paths import installed_terminal

    path = installed_terminal("mt5_strategies")
    try:
        started = mt5.initialize(path=path) if path else mt5.initialize()
        if not started:
            return {"ok": False, "terminal": path, "reason": f"initialize failed: {mt5.last_error()}"}
        info = mt5.account_info()
        tick = mt5.symbol_info_tick("XAUUSD")
        mt5.shutdown()
        return {"ok": bool(tick), "terminal": path,
                "login": getattr(info, "login", None), "server": getattr(info, "server", None),
                "xauusd_bid": getattr(tick, "bid", None),
                "reason": "" if tick else "connected, but no XAUUSD price came back"}
    except Exception as exc:
        return {"ok": False, "terminal": path, "reason": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    print("i40 Pilot - first run on this machine. Nothing is changed and nothing can trade.")
    print(f"  host      {socket.gethostname()}   {platform.system()} {platform.release()}")
    print(f"  folder    {ROOT}")
    print(f"  home      {os.environ.get('I40_HOME') or '(not set - using the folder above)'}")
    print()

    checks = {"python": check_python(), "own_files": check_own_files(),
              "machine": check_machine(), "provider": check_provider(), "broker": check_broker()}

    py = checks["python"]
    print(f"[{_ok(py['ok'])}] Python {py['python']}")
    if py["required_missing"]:
        print(f"         missing: {', '.join(py['required_missing'])}")
        print(f"         fix: {py['fix']}")
    print(f"         optional: " + ", ".join(f"{k}{'' if v else ' (absent)'}"
                                             for k, v in py["optional"].items()))

    files = checks["own_files"]
    print(f"[{_ok(files['ok'])}] its own files")
    for name, row in files["rows"].items():
        print(f"         {'ok ' if row['exists'] else 'NO '} {name:<28} {row['megabytes']:>9,.1f} MB  {row['what']}")

    machine = checks["machine"]
    print(f"[{_ok(machine['ok'])}] what THIS machine provides")
    for name, row in machine["terminals"].items():
        print(f"         {'ok ' if row['exists'] else 'NO '} {name:<19} {row['path']}")
    workbook = machine["excel_workbook"]
    print(f"         {'ok ' if workbook['exists'] else 'NO '} {'excel_workbook':<19} {workbook['path']}")
    if machine["fix"]:
        print(f"         fix: {machine['fix']}")

    provider = checks["provider"]
    print(f"[{_ok(provider['ok'])}] an AI provider")
    print(f"         internet {'reachable' if provider.get('internet') else 'NOT reachable'}"
          f"   usable: {', '.join(provider.get('usable') or []) or 'none'}")
    for row in provider.get("providers") or []:
        print(f"         {'ok ' if row.get('available') else 'NO '} {row.get('name'):<12} {str(row.get('reason'))[:74]}")
    print(f"         works with no internet: {'YES' if provider.get('works_offline') else 'NO'}")
    if provider.get("fix"):
        print(f"         fix: {provider['fix']}")

    broker = checks["broker"]
    print(f"[{_ok(broker['ok'])}] a real broker price")
    if broker["ok"]:
        print(f"         account {broker.get('login')} on {broker.get('server')}, "
              f"XAUUSD bid {broker.get('xauusd_bid')}")
    else:
        print(f"         {broker.get('reason')}")

    blocking = [name for name in ("python", "own_files") if not checks[name]["ok"]]
    degraded = [name for name in ("machine", "provider", "broker") if not checks[name]["ok"]]
    if blocking:
        verdict = (f"NOT READY. {', '.join(blocking)} must be fixed before anything runs here.")
    elif degraded:
        verdict = ("RUNS, with gaps. The system itself is intact and will start, but "
                   f"{', '.join(degraded)} {'needs' if len(degraded) == 1 else 'need'} attention "
                   "before it can trade or reason here.")
    else:
        verdict = "READY. Everything the system needs is present on this machine."

    print()
    print(verdict)
    print("  next: python -m src.system_doctor        (full health, never trades)")

    report = {"at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
              "host": socket.gethostname(), "platform": f"{platform.system()} {platform.release()}",
              "folder": str(ROOT), "i40_home": os.environ.get("I40_HOME") or "",
              "checks": checks, "verdict": verdict,
              "blocking": blocking, "degraded": degraded, "places_orders": False}
    out = ROOT / "data" / "system_health" / "first_run.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(f"  recorded: {out}")
    return 1 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
