"""The nine secret-guarded endpoints, and the two ways this guard has silently failed before.

Owner decision, 27 September 2026: Flask binds to the loopback interface and phone access goes through
a Cloudflare tunnel with Cloudflare Access in front, rather than a blanket header rule over all 103
write endpoints. These checks pin that decision.

Nothing here can place an order: the secret path points at a temp folder and every request is made
WITHOUT a valid secret, so each guarded view returns before its body runs.
"""
import re
from pathlib import Path

import pytest

import app as app_module
from src import execution_guard

# The list the owner approved, in their order. A route added to SECRET_GUARDED_ENDPOINTS without a
# decorator, or decorated without being listed, is the failure this pair of assertions catches.
GUARDED = (
    "/api/jarvis-command",
    "/api/quality-retrain",
    "/api/auto-trade/approve",
    "/api/auto-trade/execute",
    "/api/brain/execute-approved-trade",
    "/api/brain/approve-plan",
    "/api/brain/think-and-plan",
    "/api/screenshot-learn",
    "/api/strategy-lab/run",
    # Added 5 October 2026. The loopback premise above stopped holding: start_trading.bat:51 sets
    # SMARTENTRY_BIND=0.0.0.0 and cloudflared was not running, so the whole LAN reached every endpoint
    # unauthenticated. These two complete the chain to a live order - start-session arms the mode the
    # execute core reads, autonomy-control sets auto_execute true, and the boot-time autonomy loop
    # then calls the order core on its own timer.
    "/api/jarvis/autonomy-control",
    "/api/auto-trade/start-session",
)


@pytest.fixture()
def guarded_client(tmp_path, monkeypatch):
    """Deliberately separate from test_execute_api_security's `client`, which stubs the auto-trader
    state. These tests must exercise the real views, so nothing but the secret paths is redirected -
    safe because every request below is made without a valid secret and never reaches a body."""
    monkeypatch.setattr(execution_guard, "SECRET_PATH", tmp_path / "control_api.json")
    monkeypatch.setattr(execution_guard, "REJECTIONS_PATH", tmp_path / "rejections.jsonl")
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


@pytest.mark.parametrize("path", GUARDED)
def test_every_guarded_endpoint_refuses_a_request_with_no_secret(guarded_client, path):
    """Loopback is not a pass. The test client's requests come from 127.0.0.1 and must still be refused."""
    response = guarded_client.post(path, json={})
    assert response.status_code == 403, f"{path} answered {response.status_code} without the secret"


@pytest.mark.parametrize("path", GUARDED)
def test_every_guarded_endpoint_refuses_a_wrong_secret(guarded_client, path):
    response = guarded_client.post(path, json={}, headers={execution_guard.SECRET_HEADER: "not-the-secret"})
    assert response.status_code == 403, f"{path} answered {response.status_code} for a wrong secret"


def test_the_approved_list_matches_the_endpoints_that_are_guarded():
    assert set(app_module.SECRET_GUARDED_ENDPOINTS) == set(GUARDED)


def test_the_loopback_assumption_is_either_true_or_compensated_for():
    """This exists because the test below was toothless, and an audit caught it on 5 October 2026.

    That test reads app.py's SOURCE, so it passed happily while the server was actually serving
    0.0.0.0 to the whole LAN: the override lives in start_trading.bat, which it never opens. A guard
    that cannot see the thing that disables it is not a guard.

    The invariant here is deliberately CONDITIONAL, and deliberately not "the launcher must bind
    loopback". Where the dashboard is reachable from is the owner's decision - he added that line
    himself after a loopback bind cut his phone off. What is not negotiable is that WHEN the server is
    exposed past loopback, every endpoint on the chain to a live order is guarded.
    """
    launcher = Path(app_module.__file__).resolve().parent / "start_trading.bat"
    if not launcher.exists():
        pytest.skip("no launcher on this machine")
    bind = re.search(r"^[ \t]*set[ \t]+SMARTENTRY_BIND[ \t]*=[ \t]*(\S+)",
                     launcher.read_text(encoding="utf-8"), re.MULTILINE | re.IGNORECASE)
    if not bind or bind.group(1).strip() == "127.0.0.1":
        return                      # loopback: the original design holds and nothing more is needed
    for route in ("/api/jarvis/autonomy-control", "/api/auto-trade/start-session",
                  "/api/auto-trade/execute", "/api/auto-trade/approve"):
        assert route in app_module.SECRET_GUARDED_ENDPOINTS, (
            f"the launcher binds {bind.group(1).strip()}, so the server is reachable beyond this "
            f"machine and {route} MUST be guarded: it is on the chain from an unauthenticated POST "
            f"to a real order")


def test_the_server_binds_to_loopback_by_default():
    """A regression to host='0.0.0.0' IN THE CODE would re-expose every endpoint not on the list.

    Note what this does NOT see: the SMARTENTRY_BIND override in start_trading.bat. That is what
    test_the_loopback_assumption_is_either_true_or_compensated_for above is for.
    """
    source = Path(app_module.__file__).read_text(encoding="utf-8")
    run_call = source.split("if __name__ == '__main__':", 1)[1]
    assert "app.run(" in run_call
    assert "'0.0.0.0'" not in run_call.split("app.run(", 1)[1].split(")", 1)[0], \
        "app.run must not bind 0.0.0.0; the override belongs in the SMARTENTRY_BIND environment variable"
    assert "SMARTENTRY_BIND" in run_call and "127.0.0.1" in run_call


def test_every_dashboard_caller_of_a_guarded_endpoint_sends_the_header():
    """The quiet failure mode: the route is guarded, the button is not, and it returns 403 on click.

    Both regressions this catches are real. `fetchJson` attached the header only to paths starting
    /api/auto-trade/execute, so guarding /api/auto-trade/approve would have broken three Approve
    buttons; and the Strategy Lab Run button sent no header at all.
    """
    source = Path(app_module.__file__).read_text(encoding="utf-8")

    # A call through the auto-trader page's fetchJson helper is covered centrally, but only for the
    # prefixes that helper lists. Read the list out of the source rather than restating it here, so the
    # test tracks the helper instead of drifting from it.
    helper = re.search(r"const GUARDED = \[(.*?)\];", source, re.S)
    assert helper, "the fetchJson helper's GUARDED list is gone; header attachment is now unverified"
    helper_prefixes = tuple(re.findall(r"'([^']+)'", helper.group(1)))

    unprotected = []
    for path in GUARDED:
        for match in re.finditer(r"(fetchJson|fetch)\(\s*'" + re.escape(path) + r"'", source):
            if match.group(1) == "fetchJson" and path.startswith(helper_prefixes):
                continue  # the helper attaches the header for this prefix
            window = source[match.start(): match.start() + 700]
            head = source[max(0, match.start() - 700): match.start()]
            if "X-Control-Secret" not in window and "X-Control-Secret" not in head:
                line = source[: match.start()].count("\n") + 1
                unprotected.append(f"{path} at app.py:{line}")
    assert not unprotected, "these callers would get a 403 on click: " + "; ".join(unprotected)
