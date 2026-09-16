"""Execution safety 1b: a symbol outside the approved list must never reach an order.

The approval gate used to read
    require_user_approval and symbol in allowed_symbols and not internal_auto_execute
so any symbol that was not in allowed_symbols (default XAUUSD/BTCUSD) skipped approval entirely and went straight to
the broker. allowed_symbols is a whitelist of what may be traded, so such a symbol is refused instead.

tests/test_execute_api_security.py has a ``client`` fixture for an INACTIVE session; these checks need the opposite
(an active session that reaches the approval gate), so they build their own client through the helper below.
"""
import json
from pathlib import Path

import app as app_module
from src import execution_guard as guard


def _active_session_client(monkeypatch, tmp_path, allowed=("XAUUSD", "BTCUSD")):
    """Test client whose auto-trade state has an active demo session and the given approved-symbol list."""
    monkeypatch.setattr(guard, "SECRET_PATH", tmp_path / "control_api.json")
    monkeypatch.setattr(guard, "REJECTIONS_PATH", tmp_path / "rejections.jsonl")
    state = {
        "session": {"active": True, "mode": "demo", "platform": "mt5", "risk_percent": 1.0, "lot_size": 0.01,
                    "starting_balance": 10000.0, "current_balance": 10000.0, "max_trades_per_day": 10},
        "trades": [],
        "jarvis": {"approval": {"require_user_approval": True, "allowed_symbols": list(allowed),
                                "pending_requests": []}},
    }
    monkeypatch.setattr(app_module, "load_auto_trader_state", lambda: json.loads(json.dumps(state)))
    monkeypatch.setattr(app_module, "save_auto_trader_state", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "build_signal_payload",
                        lambda: [{"symbol": s, "signal": "BUY", "confidence": 0.8} for s in ("XAUUSD", "EURUSD")])
    monkeypatch.setattr(app_module, "_jarvis_trade_readiness_report",
                        lambda symbol, timeframe="15m", tz_offset_min=None: {"recommendation": {"action": "BUY"}})

    def refuse_order(*args, **kwargs):
        raise AssertionError("an order was placed for a symbol that should have been blocked")

    if getattr(app_module, "MT5_ENGINE", None) is not None:
        monkeypatch.setattr(app_module.MT5_ENGINE, "place_market_order", refuse_order, raising=False)
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def _execute(http, symbol, tmp_path):
    secret = guard.load_or_create_secret(tmp_path / "control_api.json")
    return http.post("/api/auto-trade/execute", json={"symbol": symbol}, headers={guard.SECRET_HEADER: secret})


def test_symbol_outside_the_allowed_list_is_refused(monkeypatch, tmp_path):
    http = _active_session_client(monkeypatch, tmp_path)
    response = _execute(http, "EURUSD", tmp_path)
    assert response.status_code == 403
    body = response.get_json()
    assert body["status"] == "blocked" and "not in the approved symbol list" in body["error"]
    assert body["allowed_symbols"] == ["BTCUSD", "XAUUSD"]


def test_allowed_symbol_is_not_blocked_by_the_whitelist(monkeypatch, tmp_path):
    http = _active_session_client(monkeypatch, tmp_path)
    response = _execute(http, "XAUUSD", tmp_path)
    assert response.status_code != 403, "an allowed symbol must not be refused by the whitelist"
    body = response.get_json() or {}
    if body.get("status") == "approval_required":
        assert body.get("requires_user_approval") is True


def test_the_gate_no_longer_depends_on_the_symbol_list():
    source = Path(app_module.__file__).read_text(encoding="utf-8")
    assert "approval_required = bool(approval.get('require_user_approval', True) and not internal_auto_execute)" in source
    assert "is not in the approved symbol list" in source
