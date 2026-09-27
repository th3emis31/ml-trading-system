"""MT5Service must notice when its terminal restarted underneath it.

The MetaTrader5 Python link belongs to one terminal PROCESS, and the terminal updates itself. On
27 September 2026 at 13:30 the account-11581419 terminal ran LiveUpdate, exited and came back as build
6230 - healthy, re-authorised, 1230 symbols - while the app kept the dead handle from before. `status()`
was `self._connected or self.connect()`, so once the flag was True it never reconnected: for the rest of
the day symbol_info reported XAUUSD "not found on broker", positions_get returned None, account_info
returned null, and the System Doctor still said "MT5 connected" because it asks `status()`.

Nothing here touches a real terminal or places an order: a fake MetaTrader5 module stands in, and the
test drives it from alive to dead and back.
"""
import pytest

from trading.mt5_service import MT5Service


class FakeMT5:
    """The parts of the MetaTrader5 module MT5Service uses, with a switch for 'the terminal went away'.

    A terminal that has gone returns None from terminal_info()/account_info() rather than raising, which
    is what makes the failure quiet - every call simply answers "nothing".
    """

    def __init__(self):
        self.alive = True
        self.initialize_calls = 0

    # -- liveness -------------------------------------------------------------------------------
    def terminal_info(self):
        return object() if self.alive else None

    def account_info(self):
        return object() if self.alive else None

    # -- connection -----------------------------------------------------------------------------
    def initialize(self, path=None):
        self.initialize_calls += 1
        # A restarted terminal accepts a fresh initialize; that is what recovery looks like.
        self.alive = True
        return True

    def login(self, login, password=None, server=None):
        return True

    def last_error(self):
        return (0, "ok")


@pytest.fixture()
def service(monkeypatch):
    # No MT5_LOGIN/PASSWORD/SERVER, so connect() takes the "already logged in" branch and does not
    # attempt a login - the same shape as this machine, where the terminal holds the session.
    for name in ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER", "MT5_PATH"):
        monkeypatch.delenv(name, raising=False)
    svc = MT5Service()
    fake = FakeMT5()
    svc._mt5 = fake
    svc._connected = False
    return svc, fake


def test_a_live_link_reports_connected_without_reinitialising(service):
    svc, fake = service
    assert svc.status()["connected"] is True
    first = fake.initialize_calls
    assert first == 1, "the first status() should connect once"
    assert svc.status()["connected"] is True
    assert fake.initialize_calls == first, "a live link must not be reinitialised on every status() call"


def test_a_dead_terminal_is_noticed_and_reconnected(service):
    """The regression. Before the fix this returned connected=True forever and never reinitialised."""
    svc, fake = service
    assert svc.status()["connected"] is True
    calls_before = fake.initialize_calls

    fake.alive = False                      # the terminal exited to update itself
    status = svc.status()

    assert fake.initialize_calls > calls_before, "a dead link must trigger a reconnect, not be trusted"
    assert status["connected"] is True, "after reconnecting to the new terminal it is connected again"


def test_a_terminal_that_will_not_come_back_reports_disconnected(service):
    """Fails in the safe direction: say disconnected rather than claim a link that cannot answer.

    Every caller that matters already handles this correctly - src/demo_executor.py refuses a signal
    with "could not read open MT5 positions" rather than trading blind - so a truthful False is what
    keeps the system from acting on nothing.
    """
    svc, fake = service
    assert svc.status()["connected"] is True

    fake.alive = False
    monkey_initialize_fails = lambda path=None: False
    fake.initialize = monkey_initialize_fails

    status = svc.status()
    assert status["connected"] is False
    assert svc._connected is False


def test_positions_returns_none_rather_than_an_empty_list_when_disconnected(service):
    """None and [] mean different things here, and the executor branches on it.

    [] is "I looked, nothing is open" and permits a new trade. None is "I could not look" and must not.
    """
    svc, fake = service
    fake.alive = False
    fake.initialize = lambda path=None: False
    assert svc.positions(symbol="XAUUSD", magic=440401) is None
