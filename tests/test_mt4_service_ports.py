"""The MT4 bridge client stays on one bridge when a terminal runs several.

Written 21 September 2026. The owner's MT4 terminal (PID 9848, one process) was found holding nine
bridge ports - 32768/69/70, 32778/79/80, 32788/89/90 - because three copies of the DWX expert were
attached and each stepped past the occupied set. Every copy serves the same account, so the client's
account check passes on all of them and the in-order scan could move between them after any dropped
heartbeat. It had: the expert on 32768 sat at "Client: idle (2011s)" while the app was connected on
32778 and reporting itself healthy.

These tests cover the ORDER only. No socket is opened; ``_scan_order`` is pure.
"""
from trading.mt4_service import MT4Service


def _client() -> MT4Service:
    """A client with no ZeroMQ available, so __init__ never opens a socket."""
    service = MT4Service.__new__(MT4Service)
    service._candidate_ports = [32768, 32778, 32788, 32798]
    service._preferred_port = None
    return service


def test_without_a_previous_connection_the_order_is_the_plain_scan():
    assert _client()._scan_order() == [32768, 32778, 32788, 32798]


def test_the_port_that_last_verified_is_tried_first():
    service = _client()
    service._preferred_port = 32778
    assert service._scan_order() == [32778, 32768, 32788, 32798]


def test_every_other_port_is_still_tried_so_a_dead_bridge_is_replaced():
    """The preference must not become a pin: a bridge that has gone away is still swapped out."""
    service = _client()
    service._preferred_port = 32788
    assert sorted(service._scan_order()) == sorted(service._candidate_ports)
    assert len(service._scan_order()) == len(service._candidate_ports)


def test_a_preference_outside_the_candidate_list_is_ignored():
    service = _client()
    service._preferred_port = 40000
    assert service._scan_order() == [32768, 32778, 32788, 32798]


def test_a_client_that_has_never_connected_has_no_preference():
    """Guards the attribute's existence: _scan_order runs before any connection attempt."""
    assert getattr(_client(), "_preferred_port") is None
