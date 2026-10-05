"""A cycle must survive the app restarting, and must never retry a real answer.

On 5 October 2026 app.py crash-looped: 18 "Nothing listens on port 5000" entries in the doctor log and
11 failed cycle calls in one day. Only the app holds the MT5 connection, so each strategy's hourly
decision goes through it, and each made exactly ONE attempt. `demo_session_pullback.log` ends with
`cycle call failed: WinError 10061 ... actively refused it` - the app was back about half a minute
later, and the gold session pullback had lost that hour's decision with no retry.

The line these tests defend: retrying a REFUSED CONNECTION recovers a signal that was never seen;
retrying an HTTP ANSWER would be forcing a decision the app already made. The first is the fix. The
second would be a loop, and would mean a strategy arguing with its own gates.
"""
import json
import urllib.error

import pytest

from src import app_cycle

URL = "http://127.0.0.1:5000/api/demo-trading/cycle"
HEADERS = {"Content-Type": "application/json", "X-Control-Secret": "s"}


class _Body:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _refused():
    return urllib.error.URLError(ConnectionRefusedError(10061, "actively refused it"))


def _opener(script):
    """An opener that replays `script`: an exception is raised, anything else is returned as a body."""
    calls = []

    def opener(request, timeout=None):
        calls.append(request)
        item = script[min(len(calls) - 1, len(script) - 1)]
        if isinstance(item, BaseException):
            raise item
        return _Body(item)

    opener.calls = calls
    return opener


def test_a_cycle_lost_to_a_restart_is_recovered():
    """The exact 5 October failure: refused, refused, then the app comes back."""
    slept = []
    opener = _opener([_refused(), _refused(), {"decision": "entered", "reason": "setup"}])
    body = app_cycle.post_cycle(URL, HEADERS, opener=opener, sleep=slept.append)
    assert body["decision"] == "entered", "the signal must not be lost"
    assert len(opener.calls) == 3
    assert slept == [10, 20], "backoff between attempts, not a tight spin"


def test_the_first_attempt_is_immediate():
    """A healthy app must not pay a delay for a guard that exists for a broken one."""
    slept = []
    opener = _opener([{"decision": "no_setup", "reason": "nothing on this bar"}])
    body = app_cycle.post_cycle(URL, HEADERS, opener=opener, sleep=slept.append)
    assert body["decision"] == "no_setup"
    assert slept == [] and len(opener.calls) == 1


# HTTPError instances cannot be parametrize VALUES: pytest builds test ids by probing __name__, and
# HTTPError.__getattr__ reaches into tempfile internals and raises KeyError during collection. Pass
# the status code and build the error inside the test.
@pytest.mark.parametrize("status", [500, 403, 400, 404, 503])
def test_an_http_answer_is_NEVER_retried(status):
    answer = urllib.error.HTTPError(URL, status, "answered", {}, None)
    """The app replying IS a decision. Retrying it would be a strategy arguing with its own gates."""
    opener = _opener([answer])
    with pytest.raises(urllib.error.HTTPError):
        app_cycle.post_cycle(URL, HEADERS, opener=opener, sleep=lambda s: None)
    assert len(opener.calls) == 1, "a real answer must be accepted on the first attempt"


def test_it_gives_up_and_raises_the_last_error_rather_than_running_forever():
    """Bounded: the whole budget must stay well inside the hour so a retry cannot overlap the next
    scheduled cycle."""
    slept = []
    opener = _opener([_refused()])
    with pytest.raises(urllib.error.URLError):
        app_cycle.post_cycle(URL, HEADERS, opener=opener, sleep=slept.append)
    assert len(opener.calls) == len(app_cycle.RETRY_WAITS)
    assert sum(slept) <= 300, f"the retry budget is {sum(slept)}s; it must stay well inside the hour"


def test_the_retry_budget_cannot_overlap_the_next_hourly_cycle():
    assert sum(app_cycle.RETRY_WAITS) < 1800, "half an hour is the outer bound for an hourly task"
    assert app_cycle.RETRY_WAITS[0] == 0, "the first attempt is immediate"


@pytest.mark.parametrize("kind,transient", [
    ("urlerror_refused", True),
    ("urlerror_reset", True),
    ("refused", True),
    ("timeout", True),
    ("http_500", False),
    ("value_error", False),
])
def test_only_unreachable_counts_as_transient(kind, transient):
    exc = {
        "urlerror_refused": lambda: urllib.error.URLError(ConnectionRefusedError(10061, "refused")),
        "urlerror_reset": lambda: urllib.error.URLError(ConnectionResetError(10054, "reset")),
        "refused": lambda: ConnectionRefusedError(10061, "refused"),
        "timeout": lambda: TimeoutError("timed out"),
        "http_500": lambda: urllib.error.HTTPError(URL, 500, "boom", {}, None),
        "value_error": lambda: ValueError("bad json"),
    }[kind]()
    assert app_cycle._is_transient(exc) is transient


def test_all_three_strategies_use_the_shared_helper():
    """One helper, not three copies. Each of these lost a cycle on 5 October."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for name in ("demo_session_pullback", "demo_sweep_trader", "demo_plan_trader"):
        source = (root / "src" / f"{name}.py").read_text(encoding="utf-8")
        assert "from .app_cycle import post_cycle" in source, f"{name} does not import the helper"
        assert "post_cycle(CYCLE_URL" in source, f"{name} does not use it"
        assert "urllib.request.urlopen(request" not in source, f"{name} still has a bare single attempt"
