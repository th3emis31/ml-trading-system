"""Choosing the traded account from the system, without weakening the guard that refuses the wrong one.

Written 21 September 2026 after the owner said changing account should not mean editing source, an
environment variable and a launcher. The point of these tests is the second half of that sentence:
the selection must be easy to change and must NOT make it easier to trade the wrong account.
"""
import json

import pytest

from src import active_account
from src import demo_session_pullback as pullback


@pytest.fixture()
def data_dir(tmp_path):
    return tmp_path


def _demo(login: int, server: str = "VantageMarkets-Demo") -> dict:
    """An MT5 account snapshot shaped as demo_executor.is_demo_account expects it."""
    return {"login": login, "server": server, "trade_mode": 0, "balance": 1000.0}


# --- the default -----------------------------------------------------------------

def test_with_no_selection_the_long_standing_account_is_used(data_dir):
    """An absent config must change nothing, not open anything up."""
    chosen = active_account.selected(data_dir)
    assert chosen["login"] == active_account.DEFAULT_LOGIN == 11581419
    assert chosen["is_default"] is True
    assert chosen["allow_live"] is False


def test_an_unreadable_config_falls_back_rather_than_failing_open(data_dir):
    active_account.config_path(data_dir).write_text("{ not json", encoding="utf-8")
    assert active_account.expected_login(data_dir) == active_account.DEFAULT_LOGIN


# --- selecting -------------------------------------------------------------------

def test_selecting_a_demo_account_needs_no_confirmation(data_dir):
    result = active_account.select(99887766, data_dir=data_dir)
    assert result["ok"] is True
    assert active_account.expected_login(data_dir) == 99887766


def test_no_password_is_ever_stored(data_dir):
    """The system chooses a terminal; the terminal holds the credentials. Nothing here may."""
    active_account.select(99887766, label="some broker", data_dir=data_dir)
    written = json.loads(active_account.config_path(data_dir).read_text(encoding="utf-8"))
    assert not any("pass" in key.lower() or "secret" in key.lower() or "token" in key.lower()
                   for key in written)


def test_a_live_account_needs_the_login_repeated(data_dir):
    refused = active_account.select(5551234, allow_live=True, data_dir=data_dir)
    assert refused["ok"] is False
    assert "confirm_live" in refused["reason"]
    assert active_account.expected_login(data_dir) == active_account.DEFAULT_LOGIN

    armed = active_account.select(5551234, allow_live=True, confirm_live=5551234, data_dir=data_dir)
    assert armed["ok"] is True


def test_a_terminal_path_that_does_not_exist_is_refused(data_dir):
    result = active_account.select(123456, terminal=str(data_dir / "nope" / "terminal64.exe"), data_dir=data_dir)
    assert result["ok"] is False
    assert active_account.expected_login(data_dir) == active_account.DEFAULT_LOGIN


# --- the guard still refuses -----------------------------------------------------

def test_the_guard_accepts_only_the_selected_account(monkeypatch):
    monkeypatch.setattr(active_account, "expected_login", lambda *a, **k: 4242)
    ok, _ = pullback.check_demo_account(_demo(4242))
    assert ok is True
    refused, reason = pullback.check_demo_account(_demo(25446287))
    assert refused is False
    assert "25446287" in reason


def test_selecting_an_account_does_not_let_a_live_one_trade(monkeypatch):
    """allow_live changes the selection, never is_demo_account's demo requirement."""
    monkeypatch.setattr(active_account, "expected_login", lambda *a, **k: 5551234)
    live = {"login": 5551234, "server": "VantageMarkets-Live", "trade_mode": 2, "balance": 10.0}
    ok, reason = pullback.check_demo_account(live)
    assert ok is False
    assert "not a demo account" in reason


def test_a_broken_selection_falls_back_to_the_default_login(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("config unreadable")
    monkeypatch.setattr(active_account, "expected_login", boom)
    ok, _ = pullback.check_demo_account(_demo(pullback.DEMO_ACCOUNT_LOGIN))
    assert ok is True
    refused, _ = pullback.check_demo_account(_demo(25446287))
    assert refused is False


# --- MT4 lives in the same file as MT5 ---------------------------------------

def test_mt4_account_defaults_to_the_bridge_account(data_dir):
    assert active_account.selected(data_dir)["mt4_login"] == active_account.DEFAULT_MT4_LOGIN == 12755139


def test_mt4_account_can_be_chosen_alongside_mt5(data_dir):
    """One selection answers both platforms, so changing broker is one act, not two."""
    active_account.select(11581419, mt4_login=777888, data_dir=data_dir)
    chosen = active_account.selected(data_dir)
    assert chosen["login"] == 11581419
    assert chosen["mt4_login"] == 777888


def test_changing_only_mt5_leaves_the_mt4_account_alone(data_dir):
    active_account.select(11581419, mt4_login=777888, data_dir=data_dir)
    active_account.select(22223333, data_dir=data_dir)
    assert active_account.selected(data_dir)["mt4_login"] == 777888


def test_the_mt4_environment_override_still_wins(data_dir, monkeypatch):
    active_account.select(11581419, mt4_login=777888, data_dir=data_dir)
    monkeypatch.setenv("MT4_ACCOUNT", "999000")
    assert active_account.mt4_expected_login(data_dir) == 999000
