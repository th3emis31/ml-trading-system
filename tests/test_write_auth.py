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
NINE = (
    "/api/jarvis-command",
    "/api/quality-retrain",
    "/api/auto-trade/approve",
    "/api/auto-trade/execute",
    "/api/brain/execute-approved-trade",
    "/api/brain/approve-plan",
    "/api/brain/think-and-plan",
    "/api/screenshot-learn",
    "/api/strategy-lab/run",
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


@pytest.mark.parametrize("path", NINE)
def test_every_guarded_endpoint_refuses_a_request_with_no_secret(guarded_client, path):
    """Loopback is not a pass. The test client's requests come from 127.0.0.1 and must still be refused."""
    response = guarded_client.post(path, json={})
    assert response.status_code == 403, f"{path} answered {response.status_code} without the secret"


@pytest.mark.parametrize("path", NINE)
def test_every_guarded_endpoint_refuses_a_wrong_secret(guarded_client, path):
    response = guarded_client.post(path, json={}, headers={execution_guard.SECRET_HEADER: "not-the-secret"})
    assert response.status_code == 403, f"{path} answered {response.status_code} for a wrong secret"


def test_the_approved_list_matches_the_endpoints_that_are_guarded():
    assert set(app_module.SECRET_GUARDED_ENDPOINTS) == set(NINE)


def test_the_server_binds_to_loopback_by_default():
    """A regression to host='0.0.0.0' would re-expose the 94 write endpoints that are NOT on the list."""
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
    for path in NINE:
        for match in re.finditer(r"(fetchJson|fetch)\(\s*'" + re.escape(path) + r"'", source):
            if match.group(1) == "fetchJson" and path.startswith(helper_prefixes):
                continue  # the helper attaches the header for this prefix
            window = source[match.start(): match.start() + 700]
            head = source[max(0, match.start() - 700): match.start()]
            if "X-Control-Secret" not in window and "X-Control-Secret" not in head:
                line = source[: match.start()].count("\n") + 1
                unprotected.append(f"{path} at app.py:{line}")
    assert not unprotected, "these callers would get a 403 on click: " + "; ".join(unprotected)
