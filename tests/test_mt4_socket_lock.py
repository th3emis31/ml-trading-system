"""Every path that destroys an MT4 socket or context must hold _io_lock.

WHY THIS EXISTS
---------------
On 6 October 2026 at 10:28 the live trading app died after thirteen hours up. Its last line was:

    Resource temporarily unavailable (...bundled_libzmq-src\\src\\ctx.cpp:185)

That is a libzmq errno_assert. It does not raise a Python exception that something upstream can
catch and report - it calls abort(), so the whole Flask app goes down and every strategy stops with
it. CLAUDE.md's rule that "optional integrations degrade rather than fail" cannot hold when a dead
optional integration can abort the process.

The cause was that `_io_lock` existed but was acquired in exactly ONE place, inside `_send_command`.
`_close_sockets`, `_connect` and `close` all tore the context down with no lock at all. So:

    thread A   /api/mt4/status -> _send_command -> holds _io_lock, blocked in recv (up to 2s)
    thread B   another caller  -> _connect -> _try_port -> _close_sockets -> context.term()

and libzmq aborts, because a context was terminated while another thread was inside a socket on it.

Flask serves requests on threads and `MT4_ENGINE` in app.py is one module-level instance shared by
all of them, so A and B are ordinary concurrent dashboard and doctor polls.

THE WINDOW IS WIDEST WHEN THE BRIDGE IS DOWN, which is the cruel part: a healthy bridge answers in
microseconds, but an unreachable one makes every probe sit in the socket lifecycle for the full
receive timeout across four port sets. The outage makes the crash likely, so the two failures that
were being investigated separately were the same failure.

`close()` matters most of the three: `__del__` calls it, and `__del__` runs on whichever thread
happens to trigger collection, so that teardown arrives at a moment no code chose.

These tests never open a socket, never reach the network and never place an order.
"""
import pytest

from trading.mt4_service import MT4Service


class RecordingLock:
    """Stands in for the RLock and records whether it was actually entered."""

    def __init__(self):
        self.entered = 0
        self.depth = 0
        self.max_depth = 0

    def __enter__(self):
        self.entered += 1
        self.depth += 1
        self.max_depth = max(self.max_depth, self.depth)
        return self

    def __exit__(self, *exc):
        self.depth -= 1
        return False


class ExplodingSocket:
    """A socket whose close() would be the dangerous call. Records that it happened."""

    def __init__(self, log, name):
        self.log = log
        self.name = name

    def close(self):
        self.log.append(f"close:{self.name}")


class ExplodingContext:
    def __init__(self, log):
        self.log = log

    def term(self):
        self.log.append("term:context")


def _bare_service():
    """Build the object without __init__, so nothing tries to find a bridge."""
    svc = MT4Service.__new__(MT4Service)
    svc._io_lock = RecordingLock()
    svc.connected = False
    return svc


def _with_sockets(svc):
    log = []
    svc.cmd_socket = ExplodingSocket(log, "cmd")
    svc.resp_socket = ExplodingSocket(log, "resp")
    svc.context = ExplodingContext(log)
    return log


# --------------------------------------------------------------- the three teardown paths
def test_close_sockets_holds_the_lock_while_terminating_the_context():
    svc = _bare_service()
    log = _with_sockets(svc)

    svc._close_sockets()

    assert svc._io_lock.entered >= 1, (
        "_close_sockets terminated the context without holding _io_lock; a thread inside "
        "_send_command's recv would have been pulled out from under, and libzmq aborts the process")
    assert log == ["close:cmd", "close:resp", "term:context"]


def test_close_holds_the_lock_because_it_runs_from_the_garbage_collector():
    svc = _bare_service()
    log = _with_sockets(svc)

    svc.close()

    assert svc._io_lock.entered >= 1, "close() must hold _io_lock: __del__ calls it from any thread"
    assert "term:context" in log
    assert svc.connected is False


def test_close_survives_an_object_whose_init_never_finished():
    """__del__ fires on a half-constructed object too, and must not raise from there."""
    svc = MT4Service.__new__(MT4Service)          # no _io_lock at all
    svc.close()                                    # must be a silent no-op, not AttributeError


def test_the_whole_port_scan_is_one_critical_section(monkeypatch):
    """Locking inside each _try_port would still let a second scan interleave between ports
    and reassign cmd_socket/resp_socket under the first one."""
    svc = _bare_service()
    svc.available = True
    svc._preferred_port = None
    svc._candidate_ports = [32768, 32778, 32788, 32798]
    svc._last_error = ""

    seen = []

    def never_finds_a_bridge(port):
        # The lock must already be held for every single attempt, not taken per attempt.
        seen.append((port, svc._io_lock.depth))
        svc._last_error = "bridge ping failed"
        return False

    monkeypatch.setattr(svc, "_try_port", never_finds_a_bridge)

    assert svc._connect() is False
    assert len(seen) == 4, "every fallback port set should still be scanned"
    assert all(depth >= 1 for _port, depth in seen), (
        "the lock was not held for the whole scan: a second thread could start its own scan "
        "between two port attempts")


# --------------------------------------------------------------- the lock must stay re-entrant
def test_the_lock_is_reentrant_or_the_fix_deadlocks_itself():
    """_connect -> _try_port -> _close_sockets -> _send_command all acquire it in one chain.
    A plain Lock here would hang the request thread forever, which is worse than the crash."""
    import inspect
    import threading

    # The live object must be built with an RLock, not a Lock.
    source = inspect.getsource(MT4Service.__init__)
    assert "RLock" in source, "_io_lock must stay an RLock; a plain Lock deadlocks the chain above"

    real = threading.RLock()
    with real:
        with real:
            pass    # an RLock allows this re-entry; a Lock would hang here forever


def test_send_command_still_holds_the_lock():
    """The one place that was already correct must not be lost while fixing the others."""
    import inspect
    source = inspect.getsource(MT4Service._send_command)
    assert "_io_lock" in source, "_send_command stopped guarding its send/recv pair"


@pytest.mark.parametrize("method", ["_close_sockets", "close", "_connect"])
def test_each_lifecycle_method_references_the_lock_in_its_own_source(method):
    """A behavioural test can be satisfied by a caller holding the lock. These three must take it
    themselves, because each is reachable directly."""
    import inspect
    source = inspect.getsource(getattr(MT4Service, method))
    assert "_io_lock" in source, f"{method} no longer takes _io_lock itself"
