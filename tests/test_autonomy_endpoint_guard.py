"""The two endpoints that can turn research into real orders must prove they hold the secret.

app.py's write-auth comment explains why only nine endpoints were guarded:

    "The server binds to the loopback interface ... so the only routes into it are this machine and
     the Cloudflare tunnel, which puts Cloudflare Access in front."

That premise stopped being true. `start_trading.bat:51` sets SMARTENTRY_BIND=0.0.0.0, the app default
is 127.0.0.1, and cloudflared is not running, so on 5 October 2026 the whole LAN could reach every
endpoint directly. Two of them completed a chain to a live order with no authentication at all:

    POST /api/auto-trade/start-session  {"mode": "mt5_live"}   arms the mode the execute core reads
    POST /api/jarvis/autonomy-control   {"command": "enable"}  sets autonomy.auto_execute = True

and the boot-time autonomy loop then calls the order core on its own timer. Neither carried a guard.

CLAUDE.md: "auto_execute under autonomy in data/auto_trader_state.json controls autonomous order
placement. Confirm with the user before enabling it." An unauthenticated POST is not confirmation.

These tests read app.py's source rather than starting the app, because importing app.py boots Flask,
the MetaTrader connections and the scheduler.
"""
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app.py"
SOURCE = APP.read_text(encoding="utf-8")

DANGEROUS = (
    ("/api/jarvis/autonomy-control", "sets autonomy.auto_execute = True"),
    ("/api/auto-trade/start-session", "arms the execution mode the order core reads"),
    ("/api/auto-trade/execute", "places the order"),
    ("/api/auto-trade/approve", "approves a trade for execution"),
)


def _handler_source(route):
    """(decorators between @app.route and def, the handler body) for one literal route."""
    pattern = re.compile(
        r"@app\.route\(\s*'" + re.escape(route) + r"'[^)]*\)\s*\n(?P<between>(?:@[^\n]*\n)*)def ",
        re.MULTILINE)
    match = pattern.search(SOURCE)
    assert match, f"{route} is not defined with a literal @app.route in app.py"
    return match.group("between"), SOURCE[match.end():match.end() + 1600]


@pytest.mark.parametrize("route,why", DANGEROUS)
def test_the_route_requires_the_control_secret_one_way_or_the_other(route, why):
    """Two shapes are acceptable, and both are real guards.

    `@require_control_secret` is the decorator. `/api/auto-trade/execute` instead checks
    `execution_guard.secret_matches` inline, which is STRONGER here: it also writes the refusal to
    data/execution_rejections.jsonl, so a rejected order attempt leaves a record. What is not
    acceptable is neither.
    """
    decorators, body = _handler_source(route)
    decorated = "@require_control_secret" in decorators
    inline = "secret_matches" in body and "403" in body
    assert decorated or inline, (
        f"{route} ({why}) has no control-secret check in either shape; "
        f"one unauthenticated POST reaches it")


@pytest.mark.parametrize("route,_why", DANGEROUS)
def test_the_route_is_also_on_the_documented_list(route, _why):
    """The comment and the code must agree, or the next reader trusts the wrong one."""
    block = SOURCE[SOURCE.index("SECRET_GUARDED_ENDPOINTS = ("):]
    block = block[:block.index("\n)")]
    assert f"'{route}'" in block, f"{route} is guarded in code but missing from SECRET_GUARDED_ENDPOINTS"


def test_the_guard_has_no_loopback_exemption():
    """A request from this machine needs the header exactly as a tunnelled one does.

    An origin exemption would be worthless here: anything running on the PC can claim loopback, and
    with the server on 0.0.0.0 the distinction buys nothing anyway.
    """
    start = SOURCE.index("def require_control_secret")
    body = SOURCE[start:start + 1400]
    assert "secret_matches" in body
    for exemption in ("remote_addr", "127.0.0.1", "_local_request_only"):
        assert exemption not in body, f"require_control_secret exempts {exemption}; it must not"


def test_the_page_attaches_the_secret_to_every_guarded_call_it_makes():
    """Guarding a route the dashboard calls without the header would just break the button.

    The /auto-trader page keeps a GUARDED list and attaches X-Control-Secret to anything on it.
    start-session is called from that page, so it has to be on that list too.
    """
    guarded_js = re.search(r"const GUARDED = \[(?P<items>[^\]]*)\]", SOURCE)
    assert guarded_js, "the page's GUARDED list is gone"
    items = guarded_js.group("items")
    for route in ("/api/auto-trade/execute", "/api/auto-trade/approve", "/api/auto-trade/start-session"):
        assert route in items, f"the page calls {route} but would not send the secret"


def test_autonomy_control_still_has_no_ui_caller():
    """It was guarded for free precisely because nothing in the dashboard calls it. If a button is
    added later it must join the page's GUARDED list, and this test is the reminder."""
    calls = [line for line in SOURCE.splitlines()
             if "jarvis/autonomy-control" in line and ("fetch" in line or "fetchJson" in line)]
    assert calls == [], f"a UI caller appeared; add it to the page's GUARDED list: {calls}"
