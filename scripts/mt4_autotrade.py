"""Switch the auto-trade session between MT4 and MT5, and turn autonomy on or off.

Written 21 September 2026 after two attempts to do this from cmd.exe failed on its quoting: the
control secret has to be read out of a JSON file and passed in a header, and nesting quotes inside
`for /f` mangles the command. Python has no such problem, so the whole thing lives here and the .cmd
files are one-line wrappers.

    python scripts/mt4_autotrade.py enable  [--dry-run]
    python scripts/mt4_autotrade.py disable [--dry-run]
    python scripts/mt4_autotrade.py status

ENABLE points the auto-trade session at MT4 and turns autonomy on, which arms orders to the MT4
bridge account at 0.01 lots, capped at 10 trades a day with a 5 percent daily loss limit.
src/execution_guard.py still checks fresh bars, spread against the stop distance and news windows
before any order is sent.

DISABLE turns autonomy OFF FIRST, then moves the session back to MT5, so nothing can place an order
while the session is changing. It does not close anything already open.

Neither touches the four MT5 demo strategies (magics 440502, 440603, 440704, 440805). They run on
their own account through a different path.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:5000"
SECRET_FILE = ROOT / "data" / "control_api.json"

SESSION_MT4 = {"platform": "mt4", "mode": "mt4_live", "lot_size": 0.01, "risk_percent": 1.0,
               "daily_loss_limit_pct": 5, "max_trades_per_day": 10}
SESSION_MT5 = dict(SESSION_MT4, platform="mt5", mode="mt5_live")


def secret() -> str:
    try:
        return str(json.loads(SECRET_FILE.read_text(encoding="utf-8-sig"))["secret"])
    except Exception as exc:
        print(f"[X] could not read the control secret from {SECRET_FILE}: {exc}")
        raise SystemExit(2)


def call(method: str, path: str, body: dict | None = None, with_secret: bool = False,
         dry_run: bool = False) -> dict:
    url = BASE + path
    if dry_run:
        print(f"    [dry run] {method} {url}  body={json.dumps(body) if body else '-'}")
        return {"dry_run": True}
    data = json.dumps(body or {}).encode() if method == "POST" else None
    headers = {"Content-Type": "application/json"}
    if with_secret:
        headers["X-Control-Secret"] = secret()
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        print(f"    HTTP {exc.code}: {detail[:300]}")
        return {"error": detail, "status": exc.code}
    except Exception as exc:
        print(f"    request failed: {exc}")
        return {"error": str(exc)}


def show(label: str, payload: dict) -> None:
    print(f"    {label}: {json.dumps(payload, indent=1)[:600]}")


def show_wiring(dry_run: bool = False) -> int:
    print("\n=== auto-trade session ===")
    show("session", call("GET", "/api/auto-trade/status", dry_run=dry_run))
    print("\n=== autonomy ===")
    show("autonomy", call("GET", "/api/jarvis/autonomy-status", dry_run=dry_run))
    print("\n=== MT4 bridge ===")
    show("mt4", call("GET", "/api/mt4/status", dry_run=dry_run))
    print("\n=== account the strategies may trade ===")
    show("active_account", call("GET", "/api/active-account", dry_run=dry_run))
    return 0


def enable(dry_run: bool = False) -> int:
    print("\n=== 1/3  point the auto-trade session at MT4 ===")
    show("session", call("POST", "/api/auto-trade/start-session", SESSION_MT4,
                         with_secret=True, dry_run=dry_run))
    print("\n=== 2/3  enable autonomy (sets enabled AND auto_execute) ===")
    show("autonomy", call("POST", "/api/jarvis/autonomy-control", {"command": "enable"}, dry_run=dry_run))
    print("\n=== 3/3  scan once now rather than waiting up to 180 s ===")
    show("scan", call("POST", "/api/jarvis/autonomy-control", {"command": "scan_once"}, dry_run=dry_run))
    print("\n=== MT4 bridge ===")
    show("mt4", call("GET", "/api/mt4/status", dry_run=dry_run))
    return 0


def disable(dry_run: bool = False) -> int:
    # Autonomy off FIRST: nothing can place an order while the session is being moved.
    print("\n=== 1/2  disable autonomy ===")
    show("autonomy", call("POST", "/api/jarvis/autonomy-control", {"command": "disable"}, dry_run=dry_run))
    print("\n=== 2/2  put the session back on MT5 ===")
    show("session", call("POST", "/api/auto-trade/start-session", SESSION_MT5,
                         with_secret=True, dry_run=dry_run))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Arm or disarm MT4 auto-trading")
    parser.add_argument("command", choices=["enable", "disable", "status"])
    parser.add_argument("--dry-run", action="store_true",
                        help="print the calls that would be made and send nothing")
    args = parser.parse_args(argv)
    handler = {"enable": enable, "disable": disable, "status": show_wiring}[args.command]
    result = handler(dry_run=args.dry_run)
    print()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
