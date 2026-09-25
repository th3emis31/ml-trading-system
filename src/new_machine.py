"""Everything needed to put this system on a new laptop or PC, in the order that matters.

Served at `/new-machine` and by `GET /api/setup/new-machine`, so the guidance lives INSIDE the system
rather than in a note that goes stale. Two rules shape it:

**Every item states its cost.** On 25 September 2026 the owner stopped a piece of work to ask "But
Ollama I have to pay?" - a fair question, because Ollama's site leads with its paid cloud tiers even
though local use is free. A recommendation without a price attached makes the owner go and check, and a
pricing page is built to sell the paid tier. So `cost` is a required field on every step here, and
anything that would require a subscription to RUN the system is marked optional and says so.

**It reports this machine, not a generic one.** Each step carries a live `status` where one can be
checked, so the same page that tells a new PC what to install also shows the current PC passing. A
guide nobody can see working is a guide nobody trusts.

The companion is `scripts/first_run_on_new_machine.py`, which CHECKS a machine and changes nothing.
This module INSTRUCTS. They must not disagree, so anything checkable here is checked by the same
underlying functions the script uses, never by a second copy of the logic.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Kept to the packages the system genuinely cannot start without, matching REQUIRED_PACKAGES in
# scripts/first_run_on_new_machine.py. Optional ones are listed separately with what they unlock,
# because installing everything is not required to get a working system.
CORE_PACKAGES = ("pandas", "numpy", "scikit-learn", "flask", "joblib")
OPTIONAL_PACKAGES = {
    "MetaTrader5": "broker prices, the account, and placing orders - without it the system is read-only",
    "tensorflow": "the LSTM half of the models; the RandomForest half works without it",
    "pywin32": "reading and writing the Excel dashboard",
    "openpyxl": "reading Excel files without Excel installed",
    "pyzmq": "the MT4 bridge (DWX ZeroMQ)",
}

LOCAL_MODEL = "granite4:micro-h"
LOCAL_MODEL_GB = 2.1


def _python_status() -> dict:
    import sys

    missing = []
    for name in ("pandas", "numpy", "sklearn", "flask", "joblib"):
        try:
            __import__(name)
        except ImportError:
            missing.append(name)
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if missing:
        return {"ok": False, "detail": f"Python {version}; missing {', '.join(missing)}"}
    return {"ok": True, "detail": f"Python {version} with every core package present"}


def _files_status() -> dict:
    from .runtime_paths import i40_home, smartentry_data_dir, smartentry_models_dir

    home = i40_home()
    wanted = {"models": Path(smartentry_models_dir()), "data": Path(smartentry_data_dir()),
              "memory": home / ".claude" / "memory"}
    absent = [name for name, path in wanted.items() if not path.exists()]
    if absent:
        return {"ok": False, "detail": f"missing from {home}: {', '.join(absent)}"}
    return {"ok": True, "detail": f"models, data and memory all present under {home}"}


def _machine_status() -> dict:
    from .runtime_paths import machine_report

    report = machine_report()
    missing = list(report["missing"])
    if not report["excel_workbook"]["exists"]:
        missing.append("excel_workbook")
    if missing:
        return {"ok": False, "detail": f"not found here: {', '.join(missing)} - edit {report['config_file']}"}
    return {"ok": True, "detail": f"all {len(report['terminals'])} terminals and the workbook found"}


def _provider_status() -> dict:
    try:
        from .ai_provider import provider_status

        state = provider_status()
        if state["independent"]:
            return {"ok": True, "detail": state["verdict"]}
        return {"ok": False, "detail": state["verdict"]}
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def _broker_status() -> dict:
    """Installed is not the same as working: this asks whether a price can actually be read."""
    try:
        from .mtf_data import fetch_app_bars

        frame = fetch_app_bars("XAUUSD", "1h", 5)
        if frame is None or len(frame) == 0:
            return {"ok": False, "detail": "no bars came back - the terminal is not serving prices here"}
        return {"ok": True, "detail": f"read {len(frame)} XAUUSD bars, latest {frame['datetime'].iloc[-1]}"}
    except Exception as exc:
        return {"ok": False, "detail": f"could not read a price: {type(exc).__name__}: {exc}"}


# The steps, in dependency order. `required` False means the system runs without it and says what is
# lost. `cost` is never omitted - see the module docstring.
STEPS = (
    {"id": "copy", "title": "Copy the system onto the new machine", "required": True,
     "free": True, "cost": "free",
     "why": ("Everything the system knows travels as one folder: the code, the models, every measured "
             "result in .claude/memory/BASELINE.md, and data/ - the record of what it actually did. "
             "Those two are the irreplaceable parts; the code could be rewritten, the record could not."),
     "commands": ("robocopy \"D:\\A. Trading system Themis\\i40 Pilot full system <date>\\ml_trading_system\" "
                  "\"C:\\Users\\<you>\\ml_trading_system\" /E /R:1 /W:1 /XJ",),
     "notes": ("/E copies subfolders, and there is no /MIR or /PURGE anywhere in this project - a backup "
               "that deletes is not a backup. The USB copy is made by scripts/backup_to_usb.ps1."),
     "verify": "the folder exists and contains app.py, models/, data/ and .claude/memory/",
     "check": _files_status},
    {"id": "python", "title": "Install Python 3.10 and the core packages", "required": True,
     "free": True, "cost": "free (Python is open source; the packages are all free)",
     "why": ("The system is Python end to end. 3.10 specifically, because that is what the models were "
             "pickled with - a different minor version can refuse to load them."),
     "commands": ("winget install Python.Python.3.10",
                  f"python -m pip install {' '.join(CORE_PACKAGES)}"),
     "notes": ("Then the optional ones as needed: " +
               "; ".join(f"{name} ({what})" for name, what in OPTIONAL_PACKAGES.items())),
     "verify": "python -c \"import pandas, numpy, sklearn, flask, joblib; print('ok')\"",
     "check": _python_status},
    {"id": "metatrader", "title": "Install MetaTrader and point the config at it", "required": False,
     "free": True, "cost": "free (the terminals are free; a broker account may have its own terms)",
     "why": ("MetaTrader is where real prices, the account and order placement come from. Without it the "
             "system still runs, learns and backtests on its stored data - it just cannot see the live "
             "market or trade. This is the one step that is genuinely machine-specific."),
     "commands": ("(install each terminal and log in normally, then:)",
                  "notepad config\\machine.json      # set each path to where it is installed HERE",
                  "python -m src.system_doctor        # 'Machine paths' lists each one found / NOT FOUND"),
     "notes": ("The names the code asks for are mt5_strategies, mt5_panel, mt4_bridge and mt5_tester. "
               "Use forward slashes in the json. A path that is wrong is REPORTED by name, never guessed "
               "at, so the doctor tells you exactly what to fix."),
     "verify": "the doctor's 'Machine paths' check is ok, and a price can be read",
     "check": _machine_status},
    {"id": "local_model", "title": "Install Ollama and pull the local model (works with no internet)",
     "required": False, "free": True,
     "cost": ("FREE. Ollama's local use is free and MIT-licensed and needs no account. Its Cloud/Pro "
              "tiers ($20-$100 a month) are for running models on Ollama's own GPUs - this system never "
              "uses them, because that would put a subscription and an internet connection back in the "
              "middle of it."),
     "why": ("This is what makes the system independent: it can think with no internet and no "
             "subscription. Without it the system still works, but only while online and only through "
             "the Claude CLI."),
     "commands": ("winget install Ollama.Ollama",
                  f"ollama pull {LOCAL_MODEL}",
                  "python -m src.ai_provider            # should say Independent"),
     "notes": (f"{LOCAL_MODEL} is a 3B hybrid, about {LOCAL_MODEL_GB} GB on disk and roughly 2 GB in "
               "RAM. Chosen by measurement, not preference: a 7B model died with std::bad_alloc on a "
               "7.4 GB machine while this one loaded in 12 seconds. On a machine with 16 GB or more, "
               "set a bigger model in data/ai_provider.json - the default is the one that works on the "
               "weakest PC. Expect roughly 9 tokens/sec on a CPU with no GPU: fine for writing a spec, "
               "slow for generating a large file."),
     "verify": "python -m src.ai_provider reports independent, having actually answered once",
     "check": _provider_status},
    {"id": "claude_cli", "title": "Install the Claude CLI (optional, faster while online)",
     "required": False, "free": False,
     "cost": ("needs a Claude subscription - the only paid item on this page, and it is optional. "
              "Skip it and the system runs on the local model alone."),
     "alternative": (
         "You already have the free alternative: the local model from the step above, which the "
         "system prefers anyway and which needs no account and no internet. The Claude CLI is a "
         "speed and quality upgrade while online, never a requirement.|"
         "If you want something stronger than a 3B model WITHOUT paying, several hosted APIs have "
         "free tiers that need no card: Google Gemini Flash (~50 requests a day, 1M-token context), "
         "Groq (Llama 3.3 70B at ~320 tokens/sec, ~1,000 requests a day), Cloudflare Workers AI, "
         "and Mistral's Experiment tier (~1 billion tokens a month - but only if you opt IN to your "
         "data being used for training, which is why it is last). Each needs a small provider class "
         "in src/ai_provider.py beside ClaudeCliProvider and OllamaProvider.|"
         "The trade-off, stated plainly: a free tier needs an account and an internet connection, "
         "sends your prompts to someone else's machine, and can be changed or withdrawn at any "
         "time. That is a cheaper dependency, not independence. The local model is the only option "
         "nobody can take away."),
     "why": ("Much faster and stronger than a 3B local model, so it is preferred while there is "
             "internet. It is deliberately NOT required: the whole point of the step above is that "
             "nothing breaks when this is absent."),
     "commands": ("npm install -g @anthropic-ai/claude-code", "claude --version"),
     "notes": "The system falls back between providers on its own and records which one answered.",
     "verify": "claude --version prints a version",
     "check": None},
    {"id": "first_run", "title": "Run the first-run check before anything else", "required": True,
     "free": True, "cost": "free",
     "why": ("It is the safest command in the project: read-only, changes nothing, starts no terminal "
             "and cannot place an order. It reports what this machine can and cannot give the system, "
             "and writes the answer to data/system_health/first_run.json so 'did it work over there' "
             "has a recorded answer."),
     "commands": ("python scripts/first_run_on_new_machine.py",),
     "notes": "Run this BEFORE starting the app. It tells you which of the steps above still need doing.",
     "verify": "it prints a line per area and writes data/system_health/first_run.json",
     "check": None},
    {"id": "broker", "title": "Confirm a real price can be read", "required": False,
     "free": True, "cost": "free",
     "why": ("The difference between installed and working. A terminal can be running and still serve "
             "no prices - this is the check that separates the two."),
     "commands": ("python -c \"from src.mtf_data import fetch_app_bars; "
                  "print(fetch_app_bars('XAUUSD','1h',5).tail(2))\"",),
     "notes": "If this fails but the terminal is open, the terminal is logged out or the symbol differs.",
     "verify": "recent XAUUSD bars print, with a timestamp close to now",
     "check": _broker_status},
    {"id": "run", "title": "Start the app", "required": True, "free": True, "cost": "free",
     "why": "The dashboard, the APIs and every page. It serves on port 5000.",
     "commands": ("python app.py",
                  "(then open http://localhost:5000/system-doctor and /i40-build-map)"),
     "notes": ("Start ONE copy only - two would both try to listen on port 5000. On the original "
               "machine start_trading.bat does this at logon and relaunches it 5 s after it exits."),
     "verify": "http://localhost:5000/system-doctor loads and shows the checks",
     "check": None},
    {"id": "schedule", "title": "Register the scheduled work (only when this becomes the live machine)",
     "required": False, "free": True, "cost": "free",
     "why": ("The hourly and daily jobs - paper trader, strategy lab, doctor, daily learning, reports. "
             "Leave this OFF on a second machine: two machines running the same schedule against the "
             "same account is how duplicate orders happen."),
     "commands": ("python -m src.system_doctor --fix     # recreates any missing scheduled task",),
     "notes": ("Check what a task really points at rather than trusting a note: "
               "(Get-ScheduledTask -TaskName '<name>').Actions[0].Execute"),
     "verify": "the doctor's 'Scheduled tasks' check is ok",
     "check": None},
    {"id": "excel", "title": "Excel dashboard (optional)", "required": False, "free": True,
     "cost": ("free - the workbook and the code cost nothing, and the owner already has Excel "
              "licensed. Reading and writing .xlsx needs no Excel at all (openpyxl); only the hourly "
              "LIVE refresh drives desktop Excel through COM, and LibreOffice Calc or Excel for the "
              "web open the workbook for free."),
     "why": "The Trading Business Dashboard, refreshed hourly from the running system.",
     "commands": ("python -m pip install pywin32 openpyxl",
                  "python scripts/excel_refresh.py"),
     "notes": ("Set the workbook path in config/machine.json under excel_workbook. The refresh attaches "
               "to an open Excel or starts a private one, and only closes what it opened itself."),
     "verify": "the script prints the balance, net, win rate and trade count it wrote",
     "check": None},
)


def setup_state(include_live: bool = True) -> dict:
    """The guidance, plus how THIS machine currently stands against each step that can be checked."""
    from .runtime_paths import i40_home, machine_config_path

    steps = []
    for step in STEPS:
        row = {key: value for key, value in step.items() if key != "check"}
        row["commands"] = list(step.get("commands") or ())
        if include_live and step.get("check"):
            try:
                row["status"] = step["check"]()
            except Exception as exc:                      # a broken check is a finding, not a crash
                row["status"] = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        steps.append(row)

    checked = [s for s in steps if s.get("status")]
    required_bad = [s["id"] for s in steps if s.get("required") and s.get("status")
                    and not s["status"]["ok"]]
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "home": str(i40_home()), "machine_config": str(machine_config_path()),
        "steps": steps,
        "required_total": len([s for s in STEPS if s["required"]]),
        "checked": len(checked),
        "passing": len([s for s in checked if s["status"]["ok"]]),
        "blocking": required_bad,
        "core_packages": list(CORE_PACKAGES),
        "optional_packages": dict(OPTIONAL_PACKAGES),
        "first_run_command": "python scripts/first_run_on_new_machine.py",
        "minimum": ("Copy the folder, install Python 3.10 and the core packages, then run the first-run "
                    "check. That alone gives a system that reads its own record, backtests and learns. "
                    "MetaTrader adds the live market, and the local model makes it work offline."),
        # From the explicit `free` flag, never from the prose. The cost TEXT for the local model
        # explains that Ollama's paid tier is deliberately avoided, and matching the word
        # "subscription" in that explanation made this page report that Ollama must be paid for -
        # the precise opposite of the truth, on the one subject the owner had already had to ask about.
        "costs_nothing": [step["id"] for step in STEPS if step.get("free")],
        "needs_paying_for": [step["id"] for step in STEPS if not step.get("free")],
        "places_orders": False,
    }


def main(argv=None) -> int:
    state = setup_state()
    print("i40 Pilot - putting this system on a new laptop or PC")
    print(f"  home {state['home']}")
    print(f"  {state['passing']}/{state['checked']} checkable steps pass here"
          + (f"; BLOCKING: {', '.join(state['blocking'])}" if state["blocking"] else ""))
    print()
    for index, step in enumerate(state["steps"], 1):
        mark = "  " if not step.get("status") else ("ok" if step["status"]["ok"] else "NO")
        need = "required" if step["required"] else "optional"
        print(f"[{mark}] {index}. {step['title']}  ({need}, {step['cost'][:60]})")
        if step.get("status"):
            print(f"        here: {step['status']['detail'][:100]}")
    print()
    print(f"Start with: {state['first_run_command']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
