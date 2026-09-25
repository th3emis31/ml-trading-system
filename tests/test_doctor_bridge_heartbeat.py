"""A running terminal process is not a working bridge, and the report must not imply it is.

On 24 September 2026 the MT4 terminal held its three ZeroMQ ports for hours while never answering a
HEARTBEAT. The doctor printed "Broker connections: MT4 bridge disconnected" and, four lines later,
"MetaTrader terminals: All 3 required terminals are running". Both were true. Read together they say
the bridge is fine, which sent the owner to restart a terminal that did not need restarting.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import system_doctor as sd


class _Proc:
    def __init__(self, stdout):
        self.stdout, self.stderr, self.returncode = stdout, "", 0


def _all_running(*_args, **_kwargs):
    # backslashes, as Windows reports them, against the config's forward slashes
    return _Proc("\n".join(
        path.replace("/", chr(92)) for path in sd.required_terminals()))


def _brokers(connected, message="no answer"):
    return {"detail": {"mt4": {"connected": connected, "message": message}}}


def test_a_silent_bridge_is_not_reported_as_healthy_terminals():
    out = sd.check_terminals(run=_all_running, brokers=_brokers(False))
    assert out["status"] == "warn", "every process running must not read as OK when the bridge is dead"
    assert out["detail"]["mt4_bridge_silent"] is True


def test_it_names_the_repair_that_actually_works():
    """Restarting the terminal is the obvious move and the wrong one - the process was never down."""
    summary = sd.check_terminals(run=_all_running, brokers=_brokers(False))["summary"]
    assert "attach it again" in summary and "DWX EA" in summary
    assert "start_everything" not in summary


def test_a_connected_bridge_still_reports_ok():
    out = sd.check_terminals(run=_all_running, brokers=_brokers(True))
    assert out["status"] == "ok"


def test_without_a_broker_result_the_check_behaves_as_before():
    """check_brokers can itself fail; the terminal check must not turn that into a false alarm."""
    assert sd.check_terminals(run=_all_running)["status"] == "ok"
    assert sd.check_terminals(run=_all_running, brokers={})["status"] == "ok"


def test_a_missing_process_still_outranks_the_bridge_message():
    """A terminal that is not running is a FAIL and its own remedy; the bridge line must not mask it."""
    out = sd.check_terminals(run=lambda *a, **k: _Proc(""), brokers=_brokers(False))
    assert out["status"] == "fail" and "start_everything.ps1" in out["summary"]
