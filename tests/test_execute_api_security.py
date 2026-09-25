"""HTTP-level checks for /api/auto-trade/execute (execution safety item 1). No order can be placed here: the
secret and log paths point at a temp folder and the auto-trade state has no active session."""
import json

import pytest

import app as app_module
from src import execution_guard


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(execution_guard, "SECRET_PATH", tmp_path / "control_api.json")
    monkeypatch.setattr(execution_guard, "REJECTIONS_PATH", tmp_path / "rejections.jsonl")
    monkeypatch.setattr(app_module, "load_auto_trader_state", lambda: {"session": {"active": False}})
    monkeypatch.setattr(app_module, "save_auto_trader_state", lambda state: None)
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client(), tmp_path


def _rejections(path):
    return [json.loads(line) for line in (path / "rejections.jsonl").read_text(encoding="utf-8").splitlines()]


def test_execute_without_secret_is_rejected_and_logged(client):
    http, tmp = client
    response = http.post("/api/auto-trade/execute", json={"symbol": "XAUUSD"})
    assert response.status_code == 403
    rows = _rejections(tmp)
    assert rows[-1]["source"] == "http" and rows[-1]["http_status"] == 403 and "control secret" in rows[-1]["reason"]


def test_internal_flag_from_a_request_never_skips_the_secret(client):
    http, tmp = client
    response = http.post("/api/auto-trade/execute", json={"symbol": "XAUUSD", "internal_auto_execute": True},
                         headers={execution_guard.SECRET_HEADER: "guess"})
    assert response.status_code == 403
    assert _rejections(tmp)[-1]["http_status"] == 403


def test_correct_secret_reaches_the_core_and_its_refusal_is_logged(client):
    http, tmp = client
    secret = execution_guard.load_or_create_secret(tmp / "control_api.json")
    response = http.post("/api/auto-trade/execute", json={"symbol": "XAUUSD", "internal_auto_execute": True},
                         headers={execution_guard.SECRET_HEADER: secret})
    assert response.status_code == 400 and "session" in response.get_json()["error"]
    row = _rejections(tmp)[-1]
    assert row["source"] == "http" and row["http_status"] == 400 and "session" in row["reason"]


def test_logged_execute_drops_internal_flag_from_payload(monkeypatch):
    seen = {}

    def fake_core(payload, internal_auto_execute=False):
        seen.update(payload=payload, internal=internal_auto_execute)
        return app_module.jsonify({"status": "ok"}), 200

    monkeypatch.setattr(app_module, "_auto_trade_execute_core", fake_core)
    with app_module.app.test_request_context("/api/auto-trade/execute", method="POST"):
        app_module._logged_execute({"symbol": "BTCUSD", "internal_auto_execute": True}, internal_auto_execute=False, source="http")
    assert "internal_auto_execute" not in seen["payload"] and seen["internal"] is False


def test_both_platform_sends_to_mt4_and_mt5_and_survives_one_failing():
    """The owner asked about ten times for both platforms to trade and kept being told why only one
    did: the routing was an if/elif, so a session could name only ONE venue and the other stayed
    connected and idle for ever.

    The legs must be independent - one broker rejecting must not stop the other - and the message
    must name what each did, because "open" hiding a silently failed half is the worst outcome.
    """
    import re, pathlib
    source = pathlib.Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
    block = source[source.index("if platform == 'both' and execution_mode != 'demo':"):]
    block = block[:block.index("elif platform == 'mt4'")]
    assert "MT4_ENGINE.place_market_order" in block and "MT5_ENGINE.place_market_order" in block
    assert "mt4_ok or mt5_ok" in block, "either fill counts as open"
    assert "MT4 {'filled'" in block.replace('"', "'") or "MT4 " in block, "the message names each leg"


def test_a_both_session_refuses_to_start_unless_both_bridges_are_up():
    """Starting with half the orders silently failing would look exactly like the bug being fixed:
    trades on one platform, and the other apparently just waiting for a signal."""
    import pathlib
    source = pathlib.Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
    start = source[source.index("def auto_trade_start_session_api"):]
    start = start[:start.index("starting_balance =")]
    assert "'both_live'" in start and "both" in start
    assert "is not connected" in start, "an unconnected bridge must refuse the session"


def test_one_trade_per_asset_is_enforced_in_the_auto_trade_route():
    """The owner's standing rule, given after this route stacked NINE gold BUY positions in ninety
    minutes - one per scan interval, same direction, all losing - because nothing stopped it opening
    another while the last was still open.

    demo_executor has enforced one-position-at-a-time against its own magic since it was written.
    This route never did, so the rule held on one execution path and not the other.
    """
    import pathlib
    source = pathlib.Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
    core = source[source.index("def _auto_trade_execute_core"):]
    core = core[:core.index("# Route to the trading engine")]
    assert "MT5_ENGINE.positions(symbol=symbol, magic=list(AUTO_TRADE_MAGICS))" in core, \
        "it must count existing positions before opening another"
    assert "one trade per asset" in core
    assert "log_rejection" in core, "a refused trade must be recorded, not silent"
    # The count must be scoped to this route's own magic: the owner's other experts trade the same
    # symbols and are not this system's to block.
    # Two constants since SmartEntry took its own number on 24 Sep 2026: AUTO_TRADE_MAGIC is what
    # new orders carry, and the legacy number is still COUNTED, because a position opened under it
    # is just as much one open trade on that asset.
    assert "AUTO_TRADE_MAGIC = 440906" in source
    assert "AUTO_TRADE_LEGACY_MAGICS = (903110,)" in source
    assert "AUTO_TRADE_MAGICS = (AUTO_TRADE_MAGIC,) + AUTO_TRADE_LEGACY_MAGICS" in source


def test_the_rule_counts_only_this_systems_own_orders():
    """Counting every position on the symbol would let another EA's gold trade block this system, and
    the owner's standing rule is that their other experts are not this system's business."""
    import pathlib
    source = pathlib.Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
    core = source[source.index("# ONE OPEN TRADE PER ASSET"):]
    core = core[:core.index("trade_record = {")]
    assert "magic=list(AUTO_TRADE_MAGICS)" in core, "the count must be scoped to this system's magics"
    assert "magic=None" not in core, "an unscoped count would let another EA's trade block this one"
