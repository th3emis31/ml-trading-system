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
