"""Ask the running app to run one strategy cycle, and do not lose the signal if it is restarting.

WHY THIS EXISTS
---------------
Only the app holds the MT5 connection, so every demo strategy's hourly decision is made by POSTing to
the running app. Each of them did that with a SINGLE attempt:

    try:
        with urllib.request.urlopen(request, timeout=300) as response: ...
    except Exception as exc:
        print("cycle call failed: ...")
        return 1

On 5 October 2026 app.py crash-looped: 18 "Nothing listens on port 5000" entries in the doctor log and
11 failed cycle calls in one day. `demo_session_pullback.log` ends with

    2026-10-05 11:01:47 cycle call failed: <urlopen error [WinError 10061] No connection could be made
    because the target machine actively refused it>

The app came back about half a minute later. The cycle was simply gone, with no retry, and the gold
session pullback - the strategy that actually trades - lost that hour's decision. Three of the four
demo strategies lost cycles the same way.

THIS WEAKENS NO GATE, AND BLOCKS NOTHING
----------------------------------------
Retrying a connection that was refused is not a second attempt at a trade. The app has not seen the
request at all, so nothing has been decided and nothing has been sent. When the app finally answers it
evaluates every condition from scratch - freshness, spread, session window, the one-position rule, the
kill switches - exactly as it would have on the first call. A signal that has gone stale in the
meantime is refused by the strategy's own freshness check, which is the correct outcome and the same
one it would have reached anyway.

What it does change: a signal that was good, and was lost only because the app happened to be
restarting, now gets taken. That is recovering a good signal, not forcing a bad one.

WHAT IT WILL NOT RETRY
----------------------
Only the failures that mean "the app is not there right now": refused connections, resets, timeouts
and DNS. An HTTP reply - 400, 403, 500 - is the app ANSWERING, and a real answer is never retried;
retrying those would be a loop, not a recovery. The whole budget is bounded well inside the hour so a
retry can never overlap the next scheduled cycle.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Callable, Optional

#: Waits between attempts, in seconds. Five attempts spanning 100 s: app.py takes roughly 25-40 s from
#: launch to binding port 5000 (it imports Keras and yfinance first), and the relaunch loop waits 5 s
#: after an exit, so this covers an ordinary restart about twice over without ever approaching the
#: next hourly trigger.
RETRY_WAITS = (0, 10, 20, 30, 40)

#: Errors that mean "the app is not answering yet". Anything else is the app's own reply and is final.
TRANSIENT_ERRNOS = {10061, 10054, 10060, 111, 104, 110}   # refused, reset, timed out (Windows + POSIX)


def _is_transient(exc: BaseException) -> bool:
    """True when the app was not reachable, as opposed to the app answering with an error."""
    if isinstance(exc, urllib.error.HTTPError):
        return False                       # the app answered. A real answer is never retried.
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (TimeoutError, ConnectionError)):
            return True
        return getattr(reason, "errno", None) in TRANSIENT_ERRNOS or "refused" in str(reason).lower()
    return getattr(exc, "errno", None) in TRANSIENT_ERRNOS


def post_cycle(url: str, headers: dict, *, payload: bytes = b"{}", timeout: float = 300.0,
               waits=RETRY_WAITS, sleep: Callable[[float], None] = time.sleep,
               opener: Optional[Callable] = None, log: Optional[Callable[[str], None]] = None) -> dict:
    """POST one cycle request, retrying only while the app is unreachable.

    Returns the decoded JSON body. Raises the LAST exception when every attempt failed, so the caller
    reports and exits exactly as it did before.
    """
    opener = opener or urllib.request.urlopen
    attempts = max(1, len(waits))
    last: Optional[BaseException] = None
    for index in range(attempts):
        wait = waits[index]
        if wait:
            if log:
                log(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} the app is not answering "
                    f"({type(last).__name__}); retrying in {wait}s "
                    f"(attempt {index + 1} of {attempts}) - the signal is not lost yet")
            sleep(wait)
        request = urllib.request.Request(url, data=payload, method="POST", headers=headers)
        try:
            with opener(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
            if index and log:
                log(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} the app answered on attempt "
                    f"{index + 1}; the cycle was recovered rather than dropped")
            return body
        except Exception as exc:            # noqa: BLE001 - the caller decides what to do with it
            last = exc
            if not _is_transient(exc):
                raise                        # the app answered. Do not retry a real answer.
    raise last if last is not None else RuntimeError("no attempt was made")
