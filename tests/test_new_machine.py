"""The setup guidance must state a cost for every step, and must not lie about which ones are free.

The bug these exist to prevent already happened once: the page reported that Ollama needed paying for,
because the filter matched the word "subscription" inside the sentence explaining that Ollama's paid
tier is deliberately AVOIDED. The owner had already had to stop work to ask about that exact cost, so
getting it backwards on the page would have been the worst possible place to be wrong.
"""
from __future__ import annotations

from src import new_machine as nm


def test_every_step_states_a_cost_and_whether_it_is_free():
    for step in nm.STEPS:
        assert step.get("cost"), f"{step['id']} has no cost"
        assert isinstance(step.get("free"), bool), f"{step['id']} must say explicitly whether it is free"
        assert step.get("why"), f"{step['id']} must say why it matters"
        assert isinstance(step.get("required"), bool)


def test_free_and_paid_come_from_the_flag_not_from_the_prose():
    """The cost TEXT for the local model contains the word 'subscription' while being free."""
    state = nm.setup_state(include_live=False)
    assert "local_model" in state["costs_nothing"], "Ollama's local use is free - the page must say so"
    assert "local_model" not in state["needs_paying_for"]
    local = next(step for step in nm.STEPS if step["id"] == "local_model")
    assert "subscription" in local["cost"].lower(), (
        "this test is only meaningful while that word is still in the text - it is what broke the filter")
    assert local["free"] is True


def test_the_claude_cli_is_optional_so_the_system_never_requires_a_subscription():
    """The owner's stated goal is a system that runs without one. That has to hold structurally."""
    cli = next(step for step in nm.STEPS if step["id"] == "claude_cli")
    assert cli["required"] is False and cli["free"] is False
    required_paid = [s["id"] for s in nm.STEPS if s["required"] and not s["free"]]
    assert required_paid == [], f"a required step costs money: {required_paid}"


def test_the_local_model_is_the_one_that_fits_the_weakest_machine():
    """Measured, not preferred: a 7B died with std::bad_alloc here while this 3B loaded in 12 s."""
    assert nm.LOCAL_MODEL == "granite4:micro-h"
    assert nm.LOCAL_MODEL_GB < 3
    step = next(s for s in nm.STEPS if s["id"] == "local_model")
    assert "std::bad_alloc" in step["notes"], "the note must carry the measurement, not just the choice"


def test_the_required_minimum_does_not_include_metatrader_or_a_model():
    """A machine with neither still runs, learns and backtests on the stored record. Saying otherwise
    would make the system look harder to move than it is."""
    required = {step["id"] for step in nm.STEPS if step["required"]}
    assert "metatrader" not in required and "local_model" not in required
    assert {"copy", "python", "first_run", "run"} <= required


def test_the_first_command_is_the_read_only_one():
    state = nm.setup_state(include_live=False)
    assert "first_run_on_new_machine" in state["first_run_command"]
    first_run = next(s for s in nm.STEPS if s["id"] == "first_run")
    assert "cannot place an order" in first_run["why"] or "no orders" in first_run["why"].lower()
    assert state["places_orders"] is False


def test_every_step_carries_commands_or_says_why_not():
    for step in nm.STEPS:
        assert step.get("commands"), f"{step['id']} gives no command to run"


def test_state_without_live_checks_does_not_touch_the_machine():
    """The page must render on a machine where nothing is installed, so the checks are optional."""
    state = nm.setup_state(include_live=False)
    assert state["checked"] == 0 and state["blocking"] == []
    assert len(state["steps"]) == len(nm.STEPS)
    assert all("status" not in step for step in state["steps"])


def test_excel_is_not_counted_as_a_cost_of_this_system():
    """The owner corrected this on 25 September 2026: they already have Excel licensed, so listing it
    as something to pay for overstates what the system costs. There is also a genuinely free path -
    openpyxl reads and writes .xlsx with no Excel installed at all."""
    excel = next(step for step in nm.STEPS if step["id"] == "excel")
    assert excel["free"] is True
    assert "openpyxl" in excel["cost"], "the no-Excel-needed path belongs in the cost line"
    state = nm.setup_state(include_live=False)
    assert "excel" in state["costs_nothing"]


def test_only_the_claude_cli_costs_anything_and_it_names_its_free_alternative():
    """The answer to 'what is the alternative for Claude CLI' must live in the page, not only in chat."""
    state = nm.setup_state(include_live=False)
    assert state["needs_paying_for"] == ["claude_cli"]
    cli = next(step for step in nm.STEPS if step["id"] == "claude_cli")
    alt = cli.get("alternative") or ""
    assert alt, "the one paid step must say what to use instead"
    assert "local model" in alt, "the alternative already installed is the first answer"
    for free_tier in ("Gemini", "Groq", "Mistral"):
        assert free_tier in alt, f"{free_tier} is a current no-card free tier and should be listed"
    # And it must not oversell a free tier as independence.
    assert "not independence" in alt or "cheaper dependency" in alt
