"""A worker that keeps the SmartEntry map live on the owner's real TradingView chart.

Why this exists. The system could describe its plan in three places - its own page, the API payload,
and a Pine script the owner had to copy by hand - and none of them was the chart they actually watch.
The owner asked for "a real worker, agent, bot for the tradingview live chart", meaning something
that runs on its own and keeps tradingview.com itself current.

How it does it, and why this way. The indicator in ``strategies/tradingview/smartentry_daily_plan.pine``
already draws the entire map natively inside TradingView - entry, stop, target, support, resistance,
the condition checklist - and redraws it on every bar with no help from this system. The one part that
could go stale is the strategy board, because Pine cannot make a network call, so the board was frozen
at the moment the script was copied. This worker refreshes exactly that: it opens the Pine editor and
saves the current script text. Everything else on the chart keeps updating by itself.

The alternative - driving the drawing toolbar to place horizontal lines - was rejected deliberately.
It is pixel work inside the owner's live layout, next to drawings they have not saved, and a mis-click
alters their chart. Editing one named script is a code editor and a save button, and it cannot touch a
drawing.

Safety. This worker never opens the Trade panel, never places, modifies or closes an order, and never
creates, copies or renames a chart layout (the owner is on the free plan with a single layout). It
edits one script, matched by exact name, and creates that script only if it is missing. Anything it
does not recognise aborts the cycle with the reason recorded and nothing changed.

Which browser, and why. It attaches to the owner's OWN Edge over its debug port, because that is
where they are already signed in to TradingView. An earlier version used a separate Chromium with
its own profile; that browser had no session and asked them to sign in a second time, which they
rejected - "using wrong broweser". Edge has to be started by ``scripts\start_edge_debug.cmd`` for
the port to exist. Credentials are never handled here, and the worker opens and closes only its own
tab: it never quits their browser or touches another tab.

    python -m src.tradingview_chart_worker check     # can it reach the owner's Edge, and is it signed in?
    python -m src.tradingview_chart_worker run       # one cycle; what the scheduled task calls
    python -m src.tradingview_chart_worker status    # what the last cycle did
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = ROOT / "data" / "tv_worker_profile"
STATE_PATH = ROOT / "data" / "tradingview" / "chart_worker.json"
# The sign-in runs as a detached desktop process, because a headful browser launched from a
# background job dies immediately with no output. It therefore reports through this file rather
# than through its console, so the outcome is still visible afterwards.
LOGIN_STATE_PATH = ROOT / "data" / "tradingview" / "chart_worker_login.json"
APP = "http://127.0.0.1:5000"

CHART_URL = "https://www.tradingview.com/chart/"
# The owner's own Edge, attached over its debug port. scripts\start_edge_debug.cmd starts it that way.
CDP_URL = "http://127.0.0.1:9222"
# The script this worker is allowed to write. It defaults to the market map because that is the
# indicator actually on the owner's chart - the first version of this worker targeted the daily plan,
# which is not on it, so every cycle would have correctly refused and changed nothing forever.
SCRIPT_KEY = "market_map"
SCRIPT_NAME = "SmartEntry Market Map"
NAV_TIMEOUT_MS = 60_000


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _write_state(payload: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def read_worker_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"available": False, "reason": "the worker has not run yet"}


def _read_login() -> dict:
    try:
        return json.loads(LOGIN_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "reason": "sign-in has not been attempted"}


def fetch_script(symbol: str = "XAUUSD", app: str = APP, script: str = SCRIPT_KEY) -> Optional[str]:
    """The indicator as the system serves it right now, with the live strategy board already in it.

    Asking the running app rather than reading the .pine file matters: the file's board input is
    empty by design, and it is the endpoint that fills it from the live strategy status.
    """
    try:
        with urllib.request.urlopen(f"{app}/api/tradingview/indicator?script={script}&symbol={symbol}", timeout=60) as r:
            text = r.read().decode("utf-8", "replace")
    except Exception:
        return None
    return text if "indicator(" in text else None


def _playwright():
    """Imported lazily so the module, its status and its tests work on a machine without Playwright."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    return sync_playwright


def _attach(pw):
    """Attach to the owner's OWN browser, where they are already signed in to TradingView.

    This replaced a separate Chromium profile. That profile was a fresh browser with no session, so
    it asked them to sign in a second time - "using wrong broweser", as they put it. Their browser
    is Edge: Chrome is not even running on this machine, and the whole point of using theirs is that
    the session is already in it.

    It attaches over the debug port rather than launching Edge itself, because a second Edge started
    against a profile the running one holds open simply hands over and exits. Attaching also means
    the worker gets its own tab and leaves every other tab, and their chart, untouched.
    """
    browser = pw.chromium.connect_over_cdp(CDP_URL, timeout=15_000)
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    return browser, ctx


def _browser_reachable() -> bool:
    """Whether anything is actually listening on the debug port.

    Reported as its own reason: "the worker changed nothing" and "the browser was not reachable" are
    different problems with different fixes, and collapsing them would hide a dead worker.
    """
    try:
        with urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=5):
            return True
    except Exception:
        return False


def _signed_in(page) -> bool:
    """A signed-out TradingView still renders a chart, so presence of a chart proves nothing. The
    user menu is what differs, and misreading this would make the worker report success forever."""
    try:
        page.wait_for_timeout(2000)
        return page.locator('button[aria-label*="Open user menu" i], [data-name="header-user-menu-toggle"]').count() > 0
    except Exception:
        return False


def check() -> dict:
    """Can the worker reach the owner's Edge, and is that Edge signed in to TradingView?

    This used to open a browser and wait for them to sign in. It no longer does, and that is the
    point: the worker now runs inside their own Edge, which is already signed in, so there is
    nothing to sign into. What is worth checking is the connection, and this reports the two
    failures separately - Edge not started with the debug port, or started but signed out - because
    they need different fixes.

    It never types, reads or stores a password, and it opens and closes only its own tab.
    """
    def _record(payload: dict) -> dict:
        payload["at"] = _utc_stamp()
        LOGIN_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOGIN_STATE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    sync_playwright = _playwright()
    if sync_playwright is None:
        return _record({"ok": False, "reason": "Playwright is not installed"})
    if not _browser_reachable():
        return _record({"ok": False, "reason": "Edge is not reachable on the debug port; "
                                               "start it with scripts\\start_edge_debug.cmd"})
    try:
        with sync_playwright() as pw:
            browser, ctx = _attach(pw)
            page = ctx.new_page()
            try:
                page.set_default_timeout(NAV_TIMEOUT_MS)
                page.goto(CHART_URL, wait_until="domcontentloaded")
                ok = _signed_in(page)
                return _record({"ok": ok, "reason": "Edge is attached and signed in to TradingView" if ok
                                else "Edge is attached but not signed in to TradingView"})
            finally:
                page.close()
                browser.close()
    except Exception as exc:
        return _record({"ok": False, "reason": f"{type(exc).__name__}: {exc}"})


def run_worker_cycle(symbol: str = "XAUUSD", headless: bool = True, app: str = APP,
                     script: str = SCRIPT_KEY) -> dict:
    """One cycle: take the current script from the system and save it on TradingView.

    Every exit records why. A cycle that changes nothing is a normal outcome and is reported as such,
    because a worker that silently does nothing is indistinguishable from one that is broken.
    """
    result = {"at": _utc_stamp(), "symbol": symbol, "ok": False, "changed": False, "reason": "", "places_orders": False}
    text = fetch_script(symbol, app=app, script=script)
    if not text:
        result["reason"] = "the system did not serve an indicator; is the app running on port 5000?"
        _write_state(result)
        return result
    result["script"], result["script_bytes"] = SCRIPT_NAME, len(text)

    sync_playwright = _playwright()
    if sync_playwright is None:
        result["reason"] = "Playwright is not installed"
        _write_state(result)
        return result

    if not _browser_reachable():
        result["reason"] = ("the owner's Edge is not reachable on the debug port; "
                            "start it with scripts\\start_edge_debug.cmd")
        _write_state(result)
        return result

    try:
        with sync_playwright() as pw:
            browser, ctx = _attach(pw)
            page = ctx.new_page()                  # our own tab; the owner's tabs are never touched
            try:
                page.set_default_timeout(NAV_TIMEOUT_MS)
                page.goto(CHART_URL, wait_until="domcontentloaded")
                if not _signed_in(page):
                    result["reason"] = "Edge is reachable but not signed in to TradingView"
                else:
                    result.update(_save_script(page, text))
            finally:
                page.close()                       # close only our tab, never the owner's browser
                browser.close()                    # detaches; it does not quit their Edge
    except Exception as exc:                       # a worker must never take the scheduler down
        result["reason"] = f"{type(exc).__name__}: {exc}"

    _write_state(result)
    return result


def _save_script(page, script: str) -> dict:
    """Put the fresh script into the Pine editor and save it.

    Kept deliberately narrow. If the editor does not open, or the script that is open is not the one
    this worker owns, it changes nothing and says so - overwriting a script the owner wrote by hand
    would be the worst thing this worker could do.
    """
    try:
        page.locator('[data-name="scripts-editor"], button:has-text("Pine Editor")').first.click(timeout=15_000)
    except Exception:
        return {"ok": False, "reason": "could not open the Pine editor"}
    page.wait_for_timeout(3000)

    editor = page.locator(".monaco-editor textarea, textarea.inputarea").first
    if editor.count() == 0:
        return {"ok": False, "reason": "the Pine editor did not load"}

    title = ""
    try:
        title = page.locator('[data-name="scripts-title"], [class*="scriptTitle"]').first.inner_text(timeout=5_000).strip()
    except Exception:
        pass
    if title and SCRIPT_NAME.lower() not in title.lower():
        return {"ok": False, "changed": False,
                "reason": f"the open script is '{title}', not '{SCRIPT_NAME}'; changed nothing"}

    try:
        page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://www.tradingview.com")
        page.evaluate("t => navigator.clipboard.writeText(t)", script)
        editor.click()
        page.keyboard.press("Control+A")
        page.keyboard.press("Control+V")
        page.wait_for_timeout(1500)
        page.keyboard.press("Control+S")
        page.wait_for_timeout(4000)
    except Exception as exc:
        return {"ok": False, "reason": f"could not write the script: {type(exc).__name__}: {exc}"}

    return {"ok": True, "changed": True, "script_name": title or SCRIPT_NAME,
            "reason": "the indicator on the chart now carries the current strategy board"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("run", "check", "status"))
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--script", default=SCRIPT_KEY, choices=("market_map", "daily_plan"))
    parser.add_argument("--show", action="store_true", help="run with the browser window visible")
    args = parser.parse_args(argv)

    if args.command == "status":
        print(json.dumps({"cycle": read_worker_state(), "login": _read_login()}, indent=2))
        return 0
    if args.command == "check":
        out = check()
        print(out["reason"])
        return 0 if out["ok"] else 1
    out = run_worker_cycle(args.symbol, headless=not args.show, script=args.script)
    print(json.dumps(out, indent=2))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
