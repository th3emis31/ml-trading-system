"""The daily plan's missing half: an execution path, switched off until the owner turns it on.

Until 20 Sep 2026 build_daily_plan had no route to an order at all - it was read only by things that
display or journal. These tests pin the safety, not the profitability: the plan has NOT cleared the
evidence bar and its last closed plan trade was a stop at -2.36 %.
"""
import pandas as pd
import pytest

from src import demo_plan_trader as pt
from src import demo_session_pullback as dsp
from test_demo_session_pullback import DEMO, PullbackEngine, fresh_files  # noqa: F401  (autouse fixture)

@pytest.fixture(autouse=True)
def _clean_plan_state():
    """A halt is deliberately STICKY in production - once the account is refused the strategy stays
    stopped until a human clears it. That is correct, and it means each test must start from a clean
    state or the account-refusal case silently disables every case after it."""
    for key in ("state", "trade_memory", "log"):
        path = pt.plan_paths()[key]
        if path.exists():
            path.unlink()
    yield


NOW = pd.Timestamp("2026-09-21 09:05", tz="UTC")
SENDING = {**pt.DEFAULT_CONFIG, "enabled": True, "dry_run": False}

GOOD_PLAN = {"status": "trade", "side": "long", "entry": 2000.0, "stop_loss": 1985.0,
             "take_profit": 2030.0, "atr": 10.0, "date": "2026-09-21", "headline": "H4 pullback"}


def _plan(**kw):
    return lambda symbol: {**GOOD_PLAN, **kw}


def test_it_ships_switched_off():
    """The DEFAULT must be dry: an order path that has never been asked to place one must not.

    This deliberately checks DEFAULT_CONFIG and not the live config file. The owner enabled real demo
    orders on 20 Sep 2026, so asserting the live config is dry would be asserting that their decision
    never happened - the invariant that matters is that a fresh install places nothing until asked.
    """
    assert pt.DEFAULT_CONFIG["dry_run"] is True
    assert pt.DEFAULT_CONFIG["max_entries_per_day"] == 1
    assert pt.DEFAULT_CONFIG["volume"] == 0.01


def test_no_configuration_can_point_it_at_another_account():
    """The account is hard-coded, not configured: whatever the config says, only 11581419 may trade."""
    assert pt.plan_status()["demo_account"] == dsp.DEMO_ACCOUNT_LOGIN == 11581419
    source = open(pt.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    assert "DEMO_ACCOUNT_LOGIN" not in source.split("def plan_cycle")[0].split("DEFAULT_CONFIG")[1].split("}")[0],         "the account must never be a config key"
    engine = PullbackEngine(account={**DEMO, "login": 99999999, "trade_mode": 2})
    pt.plan_cycle(engine, _plan(), now=NOW, config={**SENDING, "enabled": True})
    assert engine.sent == [], "a foreign account sends nothing, whatever the config says"


def test_a_live_account_is_refused_and_nothing_is_sent():
    engine = PullbackEngine(account={**DEMO, "login": 20250101, "trade_mode": 2, "server": "Vantage-Live"})
    pt.plan_cycle(engine, _plan(), now=NOW, config=SENDING)
    assert engine.sent == []
    assert pt.load_plan_state()["halted"]["kind"] == "account_refused"


def test_a_dry_run_decides_everything_and_sends_nothing():
    engine = PullbackEngine()
    out = pt.plan_cycle(engine, _plan(), now=NOW, config={**pt.DEFAULT_CONFIG, "dry_run": True})
    assert out["decision"] == "dry_run_order"
    assert "NOT sent" in out["reason"]
    assert engine.sent == [], "dry run must place nothing"


def test_a_no_trade_plan_is_never_turned_into_an_order():
    """The plan says no_trade far more often than not - on 20 Sep three of six conditions failed."""
    engine = PullbackEngine()
    out = pt.plan_cycle(engine, lambda s: {"status": "no_trade", "headline": "the H4 uptrend is not in place"},
                        now=NOW, config=SENDING)
    assert out["decision"] == "no_setup" and engine.sent == []
    assert "no_trade" in out["reason"]


def test_a_plan_missing_its_levels_is_refused_rather_than_filled_in():
    engine = PullbackEngine()
    out = pt.plan_cycle(engine, _plan(stop_loss=None), now=NOW, config=SENDING)
    assert out["decision"] == "no_setup" and engine.sent == []
    assert "missing stop_loss" in out["reason"]


def test_price_that_has_run_past_the_entry_is_refused():
    """A late fill is a different trade from the one planned."""
    engine = PullbackEngine()
    out = pt.plan_cycle(engine, _plan(entry=1900.0), now=NOW, config=SENDING)
    assert out["decision"] == "refused" and engine.sent == []
    assert "no longer the planned trade" in out["reason"]


def test_levels_that_do_not_bracket_the_price_are_refused():
    engine = PullbackEngine()
    out = pt.plan_cycle(engine, _plan(stop_loss=2010.0, take_profit=2030.0), now=NOW, config=SENDING)
    assert out["decision"] == "refused" and engine.sent == []
    assert "do not bracket" in out["reason"]


def test_a_valid_plan_sends_one_order_with_the_plans_own_levels_and_journals_it():
    engine = PullbackEngine()
    out = pt.plan_cycle(engine, _plan(), now=NOW, config=SENDING)
    assert out["decision"] == "opened"
    assert len(engine.sent) == 1
    request = engine.sent[0]
    assert request["magic"] == pt.MAGIC == 440704, "its own magic, never another strategy's"
    assert request["stop_loss"] == 1985.0 and request["take_profit"] == 2030.0
    assert request["volume"] == 0.01 and request["allow_retry_without_stops"] is False
    rows = [r for r in dsp.read_jsonl(pt.plan_paths()["trade_memory"]) if r.get("event") == "opened"]
    assert len(rows) == 1 and rows[0]["ticket"] == request.get("ticket", rows[0]["ticket"])
    assert rows[0]["plan_date"] == "2026-09-21"


def test_one_position_at_a_time_and_one_entry_a_day():
    engine = PullbackEngine()
    assert pt.plan_cycle(engine, _plan(), now=NOW, config=SENDING)["decision"] == "opened"
    later = NOW + pd.Timedelta(hours=1)
    second = pt.plan_cycle(engine, _plan(), now=later, config=SENDING)
    assert second["decision"] in ("hold", "done_today")
    assert len(engine.sent) == 1, "it must not stack positions"


def test_disabled_does_nothing_at_all():
    engine = PullbackEngine()
    out = pt.plan_cycle(engine, _plan(), now=NOW, config={**SENDING, "enabled": False})
    assert out["decision"] == "disabled" and engine.sent == []
