"""Run SmartEntry V9 backtests headlessly and rank them. Nothing here trades.

Uses the owner's SEPARATE tester install at MT5_SwingTrend_Tester, which is not the
terminal their strategies run on - so this cannot disturb live trading. That install is on
VantageMarkets-Demo while their live SmartEntry runs on FTMO, so absolute figures will not match the
result they quoted; the baseline is therefore re-run here too and every candidate is judged against
THAT, not against a number from a different broker.

Every variant is generated from the immutable repo baseline, never from the terminal's own copy,
because MT5 rewrites the .ini it loaded with the settings in force after a run.
"""
import html
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.runtime_paths import installed_terminal, terminal_data_dir  # noqa: E402

INSTALL = Path(installed_terminal("mt5_tester")).parent
DATA = Path(terminal_data_dir("mt5_tester"))
GOLDEN = ROOT / "strategies/smartentry_v9/BASELINE_v04_XAUUSD_M1.ini"
RESULTS = ROOT / "data/smartentry_tests/sweep.json"

BASE_LINES = GOLDEN.read_bytes().decode("utf-16").replace("\r\n", "\n").split("\n")


def build(name: str, changes: dict) -> Path:
    out, seen = [], set()
    for line in BASE_LINES:
        key = line.split("=", 1)[0].strip()
        if key in changes and "||" in line:
            parts = line.split("=", 1)[1].split("||")
            parts[0] = str(changes[key])
            out.append(f"{key}={'||'.join(parts)}")
            seen.add(key)
        elif line.strip() == "[Tester]":
            out += ["[Tester]", f"Report={name}", "ReplaceReport=1", "ShutdownTerminal=1"]
        elif line.startswith(";"):
            out.append(f";sweep {name}: {changes}")
        else:
            out.append(line)
    missing = set(changes) - seen
    if missing:
        raise SystemExit(f"{name}: inputs not found {missing}")
    p = INSTALL / f"sweep_{name}.ini"
    p.write_bytes(("\r\n".join(out)).encode("utf-16"))
    return p


NUM = r"\s*([\-0-9 .]+)"


def parse(name: str) -> dict:
    rep = DATA / f"{name}.htm"
    if not rep.exists():
        return {"error": "no report"}
    text = html.unescape(re.sub(r"<[^>]+>", " ", rep.read_bytes().decode("utf-16")))
    text = re.sub(r"[ \t\xa0]+", " ", text)

    def grab(label, cast=float):
        m = re.search(re.escape(label) + ":" + NUM, text)
        if not m:
            return None
        try:
            return cast(m.group(1).replace(" ", ""))
        except ValueError:
            return None

    dd = re.search(r"Balance Drawdown Maximal:\s*([\-0-9 .]+)\(([\d.]+)%\)", text)
    return {
        "net": grab("Total Net Profit"),
        "profit_factor": grab("Profit Factor"),
        "expected_payoff": grab("Expected Payoff"),
        "trades": grab("Total Trades", lambda v: int(float(v))),
        "sharpe": grab("Sharpe Ratio"),
        "max_dd_pct": float(dd.group(2)) if dd else None,
    }


def run(name: str, changes: dict, timeout: int = 420) -> dict:
    ini = build(name, changes)
    started = time.monotonic()
    try:
        subprocess.run([str(INSTALL / "terminal64.exe"), f"/config:{ini}"],
                       capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"name": name, **changes, "error": f"timed out after {timeout}s"}
    row = {"name": name, **changes, **parse(name), "seconds": round(time.monotonic() - started, 1)}
    ini.unlink(missing_ok=True)
    return row


if __name__ == "__main__":
    jobs = [("baseline", {})]
    for tp in range(1200, 2401, 150):
        for cap in range(15, 46, 5):
            if tp == 1800 and cap == 30:
                continue                      # that is the baseline itself
            jobs.append((f"tp{tp}_sp{cap}", {"TP": tp, "MaxSpreadPoints": cap}))

    only = int(sys.argv[1]) if len(sys.argv) > 1 else len(jobs)
    jobs = jobs[:only]
    rows = []
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    print(f"{len(jobs)} runs", flush=True)
    for i, (name, changes) in enumerate(jobs, 1):
        row = run(name, changes)
        rows.append(row)
        # Written after EVERY run: this machine kills long jobs, and a sweep that can only lose its
        # current step is the difference between a usable result and seventy wasted minutes.
        RESULTS.write_text(json.dumps(rows, indent=1), encoding="utf-8")
        print(f"  [{i:3}/{len(jobs)}] {name:16} net={row.get('net')} pf={row.get('profit_factor')} "
              f"trades={row.get('trades')} dd={row.get('max_dd_pct')} {row.get('error','')}", flush=True)
    print("done ->", RESULTS, flush=True)
