import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import tradingview_intake as intake

NOW = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)


def _plan(**overrides):
    plan = {"symbol": "XAUUSD", "side": "BUY", "entry": 4300.0, "stop_loss": 4280.0, "take_profit_1": 4340.0,
            "risk_reward": 2.0, "confidence": 0.7, "alignment": "Aligned", "notes": "System bias: BUY.", "source": "tradingview"}
    plan.update(overrides)
    return plan


def test_secret_check_body_header_wrong_and_stripped():
    ok, cleaned = intake.tradingview_secret_matches({"secret": "s3cret", "side": "BUY"}, None, "s3cret")
    assert ok and cleaned == {"side": "BUY"}
    ok, cleaned = intake.tradingview_secret_matches({"side": "BUY", "token": ""}, "s3cret", "s3cret")
    assert ok and "token" not in cleaned
    assert not intake.tradingview_secret_matches({"passphrase": "nope"}, None, "s3cret")[0]
    assert not intake.tradingview_secret_matches({"secret": "s3cret"}, None, "")[0]          # no configured secret: refuse
    assert not intake.tradingview_secret_matches({"secret": "sécret ☃"}, None, "s3cret")[0]  # non-ASCII must not crash


def test_dry_run_is_the_default_and_needs_the_owner_setting_to_turn_off():
    assert intake.resolve_dry_run({}, {})[0] is True
    assert intake.resolve_dry_run({"dry_run": True}, {"allow_approval_queue": True})[0] is True
    forced, reason = intake.resolve_dry_run({"dry_run": False}, {})
    assert forced is True and "allow_approval_queue" in reason
    assert intake.resolve_dry_run({"dry_run": "false"}, {"allow_approval_queue": "yes"})[0] is False
    assert intake.resolve_dry_run({"dry_run": "maybe"}, {"allow_approval_queue": True})[0] is True


def test_a_complete_aligned_alert_is_accepted():
    decision = intake.evaluate_tradingview_alert(_plan(), payload={"time": (NOW - timedelta(minutes=2)).isoformat()}, now=NOW)
    assert decision["accepted"] and decision["reasons"] == [] and all(decision["checks"].values())
    assert decision["alert_age_seconds"] == 120.0
    sell = intake.evaluate_tradingview_alert(_plan(side="SELL", entry=4300.0, stop_loss=4320.0, take_profit_1=4260.0,
                                                   alignment="Unknown"), now=NOW)
    assert sell["accepted"]


def test_each_check_rejects_with_a_reason():
    cases = {
        "side": _plan(side="HOLD"),
        "symbol": _plan(symbol="EURUSD"),
        "levels": _plan(stop_loss=None),
        "level_order": _plan(stop_loss=4320.0),
        "reward_risk": _plan(risk_reward=1.2),
        "model_alignment": _plan(alignment="Divergent"),
        "confidence": _plan(confidence=0.3),
        "not_test": _plan(source="dashboard-test"),
    }
    for check, plan in cases.items():
        decision = intake.evaluate_tradingview_alert(plan, now=NOW)
        assert not decision["accepted"] and decision["checks"][check] is False and decision["reasons"], check
    stale = intake.evaluate_tradingview_alert(_plan(), payload={"timenow": (NOW - timedelta(hours=1)).timestamp() * 1000}, now=NOW)
    assert not stale["accepted"] and stale["checks"]["fresh"] is False
    unknown_side = intake.evaluate_tradingview_alert(_plan(side="UNKNOWN", risk_reward=None), now=NOW)
    assert not unknown_side["accepted"] and "HOLD or unreadable" in unknown_side["reasons"][0]
    assert not intake.evaluate_tradingview_alert(_plan(), allowed_symbols=["BTCUSD"], now=NOW)["accepted"]


def test_journal_keeps_the_newest_rows_and_lists_newest_first(tmp_path):
    path = tmp_path / "journal.json"
    for i in range(7):
        intake.record_intake_decision(path, {"n": i, "dry_run": True}, keep=5, now=NOW + timedelta(seconds=i))
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert [row["n"] for row in stored] == [2, 3, 4, 5, 6] and stored[-1]["recorded_at"] == "2026-09-14 10:00:06"
    assert [row["n"] for row in intake.load_intake_journal(path, limit=2)] == [6, 5]
    assert intake.load_intake_journal(tmp_path / "missing.json") == []
