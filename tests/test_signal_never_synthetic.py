"""A signal built on invented prices must never reach a screen or an execution path.

On 24 September 2026 the owner asked why the dashboard showed BUY while gold was bearish. The cause:
fetch_real_data falls back to generate_synthetic_data - a random walk that always ends 2024-12-31 -
and NAS100 has no Yahoo ticker at all (404 "Quote not found"), so two of the four traded symbols were
publishing directional calls derived from a random number generator, on a bar 632 days old, with
nothing on any page saying so. CLAUDE.md already forbade it: "never invent data to fill a gap".
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SOURCE = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
_START = SOURCE.index("def _build_signal_payload_uncached")
# To the next top-level def, so the window always covers the whole builder however it grows.
_END = SOURCE.index(chr(10) + "def ", _START + 10)
BUILDER = SOURCE[_START:_END]


def test_the_builder_refuses_a_synthetic_frame():
    assert '"signal": "UNAVAILABLE"' in BUILDER
    assert 'synthetic' in BUILDER and '"tradable": False' in BUILDER


def test_it_does_not_call_the_synthetic_generator_any_more():
    """The old line was `if data.empty: data = generate_synthetic_data(symbol, n=500)`."""
    assert "generate_synthetic_data(symbol" not in BUILDER


def test_unavailable_is_not_hold():
    """HOLD is a real opinion meaning 'no trade here'. Missing data is the ABSENCE of an opinion, and
    collapsing the two would hide the fault on every screen that counts HOLDs."""
    assert '"signal": "UNAVAILABLE"' in BUILDER
    assert '"available": False' in BUILDER


def test_the_broker_feed_is_tried_before_yahoo():
    """The orders are priced from MT5, so the signals must be too - and get_bars never invents data."""
    broker = BUILDER.index("get_bars(symbol")
    yahoo = BUILDER.index("fetch_real_data(symbol")
    assert broker < yahoo, "broker bars must be attempted before the Yahoo fallback"


def test_every_row_says_which_feed_priced_it():
    assert '"data_source": bar_source' in BUILDER


def test_a_non_directional_signal_cannot_be_executed():
    """The standing rule in CLAUDE.md. UNAVAILABLE is not BUY or SELL, so the guard must reject it."""
    guard = (Path(__file__).resolve().parents[1] / "src" / "execution_guard.py").read_text(encoding="utf-8")
    assert 'side not in {"BUY", "SELL"}' in guard
