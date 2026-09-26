"""The orchestration design prompt must carry the system's REAL state, or it designs for a fiction.

The failure this guards against is specific and was nearly made: writing the design prompt as a document
with the day's numbers typed into it. Loop coverage moved three times in one afternoon (14.3 % to 42.9 % to
78.6 %) while this was being built. A model handed the first of those designs a system that no longer
exists, and the design would read perfectly well - which is worse than one that is obviously wrong.

So: every fact is read at render time (proved by replacing the reader and watching the prompt change), a
fact that cannot be read says so rather than being filled in, and no number is hard-coded in the source.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import orchestration
from src.ai_provider import estimate_tokens
from src.context_builder import OUTPUT_RESERVE

SOURCE = (Path(__file__).resolve().parents[1] / "src" / "orchestration.py").read_text(encoding="utf-8")


# --- the facts are live ---------------------------------------------------------------------------

def test_the_loop_coverage_in_the_prompt_comes_from_the_ledger_not_from_the_source(monkeypatch):
    import src.loop_ledger as ledger

    def fake_report(*args, **kwargs):
        return {"reporting": 3, "coverage_pct": 21.4, "rule": "the rule",
                "loops": [{"loop": "invented_loop", "state": "SILENT"},
                          {"loop": "paper_trader", "state": "CLOSED"}]}

    monkeypatch.setattr(ledger, "closure_report", fake_report)
    built = orchestration.design_prompt(refresh=True)
    assert "21.4% coverage" in built["prompt"]
    assert "invented_loop" in built["prompt"], "a SILENT loop must be named, not summarised away"


def _source_code_only() -> str:
    """The module's code, without its docstring or comments.

    The prose is allowed to quote a past measurement - the module docstring explains the drift problem by
    naming the coverage numbers it saw. What must never carry one is the code that builds the prompt, since
    that is what a model is told is true right now.
    """
    body = SOURCE.split('"""', 2)[2]          # everything after the module docstring
    return chr(10).join(line for line in body.splitlines() if not line.lstrip().startswith("#"))


def test_no_measured_number_is_written_into_the_code():
    """A number typed here would be a number that stops being true, silently."""
    code = _source_code_only()
    for stale in ("14.3", "42.9", "78.6", "85.7", "6,071", "395,134"):
        assert stale not in code, f"{stale} is a measurement and must be read, not written down"


def test_the_provider_windows_are_read_from_the_provider_layer(monkeypatch):
    from src import ai_provider

    class Fake:
        def __init__(self, name, window, ok):
            self._name, self._window, self._ok = name, window, ok

        def available(self):
            return self._ok, "a fixture"

        def context_tokens(self):
            return self._window

        def describe(self):
            return {"name": self._name}

    monkeypatch.setattr(ai_provider, "providers", lambda *a, **k: [Fake("fixture_model", 4321, True)])
    monkeypatch.setattr(ai_provider, "choose", lambda *a, **k: Fake("fixture_model", 4321, True))
    prompt = orchestration.design_prompt(refresh=True)["prompt"]
    assert "fixture_model" in prompt and "4,321 tokens" in prompt


def test_a_fact_that_cannot_be_read_is_reported_unavailable_and_never_invented(monkeypatch):
    from src import ai_employee

    def boom(*args, **kwargs):
        raise RuntimeError("no context today")

    monkeypatch.setattr(ai_employee, "build_employee_context", boom)
    found = orchestration.facts(refresh=True)
    assert "unavailable" in found["daily_review_prompt"]
    assert "no context today" in found["daily_review_prompt"]["unavailable"]
    prompt = orchestration.design_prompt(refresh=True)["prompt"]
    assert "could not be read" in prompt


def test_every_fact_line_names_where_it_came_from():
    lines = orchestration.facts_lines(orchestration.facts(refresh=True))
    assert lines
    for line in lines:
        assert ": " in line, f"a fact with no source cannot be cited: {line}"


# --- what the prompt must contain -----------------------------------------------------------------

def test_the_hard_rules_are_present_because_they_are_what_the_design_may_not_trade_away():
    prompt = orchestration.design_prompt()["prompt"]
    for rule in ("execution guard", "Evidence gates money", "no paid AI subscription", "Never invent a number"):
        assert rule in prompt


def test_done_when_is_stated_before_the_work_and_is_checkable():
    prompt = orchestration.design_prompt()["prompt"]
    assert "DONE WHEN" in prompt
    assert "src.loop_ledger" in prompt, "closure recording is one of the acceptance items"
    numbered = re.findall(r"^\d\.", orchestration.ACCEPTANCE, flags=re.M)
    assert len(numbered) >= 7, "each acceptance item must be separately checkable"


def test_the_design_is_asked_to_be_additive_and_to_name_what_it_calls():
    prompt = orchestration.design_prompt()["prompt"]
    assert "do not propose replacing them" in prompt
    assert "WHAT IT CALLS" in prompt


def test_it_asks_what_would_make_the_design_wrong():
    """Without this the answer is a wish list; with it, it is a design that can be refuted."""
    assert "WHAT WOULD MAKE THIS DESIGN WRONG" in orchestration.design_prompt()["prompt"]


def test_the_questions_are_questions_not_a_feature_list():
    assert len(orchestration.QUESTIONS) >= 6
    assert all(q.strip().endswith(("?", ".)", ".")) for q in orchestration.QUESTIONS)
    assert sum(1 for q in orchestration.QUESTIONS if "?" in q) >= 6


# --- budgets -------------------------------------------------------------------------------------

def test_with_no_budget_the_whole_prompt_is_returned_unshortened():
    built = orchestration.design_prompt()
    assert built["shape"]["shape"] == "full" and built["shape"]["omitted"] == []
    assert built["shape"]["prompt_tokens"] == estimate_tokens(built["prompt"])


def test_at_a_local_models_window_it_fits_and_says_what_it_left_out():
    built = orchestration.design_prompt(8192)
    usable = int(8192 * (1 - OUTPUT_RESERVE))
    assert built["shape"]["ok"] is True
    assert estimate_tokens(built["prompt"]) <= usable
    assert built["shape"]["shape"] == "brief"


def test_a_window_too_small_is_refused_with_the_numbers_rather_than_trimmed():
    built = orchestration.design_prompt(500)
    assert built["prompt"] is None and built["shape"]["ok"] is False
    assert any(ch.isdigit() for ch in str(built["shape"]["note"]))


# --- it is a tool, not a loop ---------------------------------------------------------------------

def test_it_writes_no_closure_record_under_a_name_the_ledger_does_not_know():
    """A record written under an unknown loop name is never read by closure_report - one more instrument
    with nothing plugged into it, which is the thing this module exists to argue against."""
    from src.loop_ledger import KNOWN_LOOPS

    assert "orchestration_design" not in KNOWN_LOOPS
    assert "closing_run" not in SOURCE


def test_it_places_no_orders():
    assert "order_send" not in SOURCE and "execute" not in SOURCE.split("HARD RULES")[-1].split('"""')[0]


def test_the_cache_is_short_lived_so_a_render_is_never_stale_by_much():
    """Caching the reads is a courtesy to the network, not a licence to answer from yesterday."""
    assert orchestration.FRESH_SECONDS <= 300
    first = orchestration.facts(refresh=True)
    assert orchestration.facts() is first, "a second render inside the window must not re-probe"
    assert orchestration.facts(refresh=True) is not first, "refresh must actually re-read"
