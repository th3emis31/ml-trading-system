"""The AI employee's prompt must fit whatever model will answer it, and say what it left out.

Measured on 26 September 2026: the employee's JSON prompt is about 24,000 tokens. Claude answers in
180,000, so it has always fitted and nothing here should change that - the first test exists to prove the
daily 07:15 run still sends the identical bytes. The local model the owner wants this system to fall back
on answers in 8,192, so on that provider the review could not run at all. That gap is what
`src/context_builder.py` was written for, and until this wiring nothing called it.

The dangerous failure is not a prompt that is too big - that fails loudly. It is a prompt QUIETLY
shortened, which asks a different question than the caller believes it asked, so several of these tests
are about refusing rather than trimming.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import ai_employee as ae
from src.ai_provider import estimate_tokens
from src.context_builder import OUTPUT_RESERVE

CONTEXT = {
    "generated_at": "2026-09-26 06:45:00", "local_date": "2026-09-26", "places_orders": False,
    "system_health": {"system_doctor": "ok", "failing_checks": []},
    "daily_market_report": {"xauusd": {"bias": "bullish", "atr14": 41.2}},
    "strategy_book": {"watchlist": 44, "approved": 0},
    "strategy_lab": {"candidates_tested": 395134, "cleared_holdout": 0},
    "swing_trend_pullback_ea": {"trades": 151, "net": 53.28},
    "paper_trading_and_demo": {"demo_breakout": {"open_legs": 0, "halted": False}},
    "recent_baseline_results": [{"date": "2026-09-25", "result": f"variant {i} avg_r -0.02"} for i in range(40)],
    "earlier_proposals": [{"title": f"proposal {i}", "status": "pending"} for i in range(12)],
    "memory_notes": [f"note {i}" for i in range(9)],
    "sources": {"daily_report": "data/daily_reports"},
}


def _big_context(rows: int = 900) -> dict:
    """A context too large for a small window, built from the same shape as the real one."""
    return dict(CONTEXT, recent_baseline_results=[
        {"date": "2026-09-25", "result": f"variant {i} avg_r {-0.01 * i:.4f} on 400 trades, spread+swap"}
        for i in range(rows)])


# --- the daily run must not change --------------------------------------------------------------

def test_at_claudes_window_the_prompt_is_the_same_bytes_it_has_always_been():
    """The whole point of a budget is that it does nothing when there is room."""
    prompt, shape = ae.prompt_for_budget(CONTEXT)
    assert prompt == ae.employee_prompt(CONTEXT)
    assert shape["shape"] == "json" and shape["ok"] is True and shape["omitted"] == []


def test_the_default_budget_is_the_answering_models_not_the_local_one():
    """A local 8k provider being installed must not shrink the prompt Claude is about to be sent."""
    assert ae.CLAUDE_CONTEXT_TOKENS >= 100_000
    _, shape = ae.prompt_for_budget(CONTEXT, None)
    assert shape["budget_tokens"] == ae.CLAUDE_CONTEXT_TOKENS


def test_run_employee_sends_the_whole_context_by_default(tmp_path):
    sent = []

    def fake(prompt):
        sent.append(prompt)
        return {"ok": True, "text": json.dumps({"summary": "fine", "system_health": "healthy"})}

    record = ae.run_employee(runner=fake, now=datetime(2026, 9, 26, 6, 15, tzinfo=timezone.utc),
                             base=tmp_path / "employee", root=tmp_path / "root")
    assert record["context_shape"]["shape"] == "json"
    assert "HARD RULES" in sent[0] and "CONTEXT (JSON" in sent[0]


# --- and a small window must actually work ------------------------------------------------------

def test_a_local_8k_model_gets_a_prompt_that_fits():
    prompt, shape = ae.prompt_for_budget(_big_context(), 8192)
    usable = int(8192 * (1 - OUTPUT_RESERVE))
    assert shape["shape"] == "brief" and shape["ok"] is True
    assert estimate_tokens(prompt) <= usable, f"{estimate_tokens(prompt)} tokens in a {usable} budget"
    assert estimate_tokens(ae.employee_prompt(_big_context())) > usable, "fixture must not already fit"


def test_the_rules_and_the_acceptance_test_survive_the_squeeze():
    """Identity and the definition of done are the two things that must never be trimmed away."""
    prompt, _ = ae.prompt_for_budget(_big_context(), 8192)
    assert "HARD RULES" in prompt
    assert "parse_employee_reply" in prompt, "the acceptance test must reach the model"


def test_what_was_left_out_is_reported_not_hidden():
    _, shape = ae.prompt_for_budget(_big_context(), 8192)
    assert shape["omitted"], "a brief that dropped lines must say so"
    for row in shape["omitted"]:
        assert row["kept"] < row["available"] and row["lost"], "the report must name examples"


def test_both_slots_keep_something_rather_than_one_swallowing_the_budget():
    """One fact per line is what makes this possible; a JSON dump can only drop whole branches."""
    _, shape = ae.prompt_for_budget(_big_context(), 8192)
    prompt, _ = ae.prompt_for_budget(_big_context(), 8192)
    assert "WORKING SET" in prompt and "RECENT EVENTS" in prompt
    assert shape["prompt_tokens"] > 0


# --- refusing, rather than truncating -----------------------------------------------------------

def test_a_budget_too_small_for_the_rules_is_refused_with_the_numbers():
    prompt, shape = ae.prompt_for_budget(CONTEXT, 600)
    assert prompt is None and shape["ok"] is False
    assert any(ch.isdigit() for ch in shape["note"]), f"a refusal must say by how much: {shape['note']}"


def test_run_employee_refuses_rather_than_asking_a_shortened_question(tmp_path):
    called = []
    record = ae.run_employee(runner=lambda p: called.append(p) or {"ok": True, "text": "{}"},
                             now=datetime(2026, 9, 26, 6, 15, tzinfo=timezone.utc),
                             base=tmp_path / "employee", root=tmp_path / "root", budget_tokens=600)
    assert record["status"] == "error" and not called, "the model must not be asked a question that was cut"
    assert record["context_shape"]["ok"] is False


# --- the slot split -----------------------------------------------------------------------------

def test_live_state_goes_to_working_and_history_to_episodic():
    slots = ae.context_slot_lines(CONTEXT)
    assert any(line.startswith("strategy_lab") for line in slots["working"])
    assert any(line.startswith("recent_baseline_results") for line in slots["episodic"])
    assert not any(line.startswith("recent_baseline_results") for line in slots["working"])


def test_every_line_names_the_field_it_came_from_so_a_finding_can_cite_it():
    slots = ae.context_slot_lines(CONTEXT)
    for line in slots["working"] + slots["episodic"]:
        assert ": " in line and not line.startswith(":")


def test_nested_values_are_flattened_one_fact_per_line():
    lines = ae.context_slot_lines(CONTEXT)["working"]
    assert "system_health.system_doctor: ok" in lines
    assert any(line.startswith("paper_trading_and_demo.demo_breakout.open_legs") for line in lines)


def test_the_evidence_slot_is_filled_from_the_record_not_from_live_state():
    """Evidence carries sources; live state does not. Mixing them is how an unattributed line gets trusted."""
    slots = ae.context_slot_lines(CONTEXT)
    assert set(slots) == {"working", "episodic"}
