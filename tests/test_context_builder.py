"""A brief must fit its window, say what it left out, and never truncate the task.

i40 Pilot build map step 3. Acceptance test: a 4k brief and a 100k brief run the SAME task, differing
only in depth. That is what lets the system work offline on an 8,192-token local model without the
layers above knowing which model answered.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import context_builder as cb

IDENTITY = "i40 Pilot. Never invent data. Cite or omit."
ACCEPTANCE = "a figure from the record, with its date and source"


def _lines(n, prefix="measured result"):
    return [f"- {prefix} {i}  (2026-09-24 - BASELINE.md)" for i in range(n)]


def test_the_same_task_survives_at_every_budget():
    """The point of the whole layer: the task is identical, only the depth changes."""
    task = "does the gold model have an edge"
    # Enough lines that the small budget MUST choose - with only 200 both windows fit everything and
    # the comparison proves nothing.
    small = cb.assemble(task, budget_tokens=4_000, identity=IDENTITY, acceptance=ACCEPTANCE,
                        evidence=_lines(4_000))
    large = cb.assemble(task, budget_tokens=100_000, identity=IDENTITY, acceptance=ACCEPTANCE,
                        evidence=_lines(4_000))
    assert small.ok and large.ok
    assert task in small.prompt and task in large.prompt
    assert ACCEPTANCE in small.prompt and ACCEPTANCE in large.prompt
    assert large.prompt_tokens > small.prompt_tokens, "a bigger window must actually be used"


def test_a_brief_stays_inside_its_budget():
    out = cb.assemble("a task", budget_tokens=4_000, identity=IDENTITY, evidence=_lines(500))
    assert out.prompt_tokens <= 4_000


def test_room_is_reserved_for_the_answer():
    """Filling the whole window leaves nothing to reply with, and the failure then looks like a
    truncated answer rather than a context problem."""
    out = cb.assemble("a task", budget_tokens=10_000, identity=IDENTITY, evidence=_lines(5_000))
    assert out.prompt_tokens <= 10_000 * (1 - cb.OUTPUT_RESERVE) + 50


def test_the_task_is_never_truncated_it_refuses_instead():
    """A silently shortened prompt asks a different question than the caller believes it asked."""
    out = cb.assemble("word " * 4_000, budget_tokens=1_000, identity=IDENTITY)
    assert out.ok is False
    assert "Refused rather than truncated" in out.reason
    assert "1,000 budget" in out.reason, "the refusal must carry the numbers"


def test_what_was_left_out_is_reported_not_hidden():
    out = cb.assemble("a task", budget_tokens=3_000, identity=IDENTITY, evidence=_lines(400))
    slot = next(s for s in out.slots if s.name == "evidence")
    assert slot.kept < slot.available
    assert out.omitted and out.omitted[0]["slot"] == "evidence"
    assert out.omitted[0]["lost"], "the caller must be able to see WHICH lines were dropped"


def test_evidence_is_kept_before_recent_events():
    """Evidence is what stops the model inventing an answer the record already contradicts; a recent
    event only matters when it changes the next action.

    The contention has to be real for this to mean anything: with a budget that fits everything both
    slots keep everything, and unspent budget is deliberately handed down the priority order rather
    than wasted. So this asks for far more of both than can possibly fit.
    """
    out = cb.assemble("a task", budget_tokens=2_000, identity=IDENTITY,
                      evidence=_lines(400),
                      episodic=[f"- something happened {i}" for i in range(400)])
    ev = next(s for s in out.slots if s.name == "evidence")
    ep = next(s for s in out.slots if s.name == "episodic")
    assert ev.kept < ev.available and ep.kept < ep.available, "the budget must actually bite"
    assert ev.kept > ep.kept, f"evidence {ev.kept} should outrank episodic {ep.kept}"


def test_whole_lines_only_never_half_a_result():
    """Half a measured result reads as a complete statement while missing the qualifier that made it
    true - "wins 5 of 8 years" cut to "wins" is worse than omitting it."""
    lines = ["- gold SL 3000 wins the current era but loses 2019-2022  (2026-09-24 - BASELINE.md)"]
    kept, count = cb._fit_lines(lines, 5)
    assert kept == [] and count == 0, "it must drop the line, not cut it"


def test_an_unverified_claim_is_labelled_inline():
    """A hypothesis may still be shown, but never as though it had been measured."""
    hits = {"matches": [{"title": "the new filter improves profit factor to 1.40",
                         "date": "2026-09-24", "source": "NOTES.md", "text": "..."}]}
    lines, citations = cb.evidence_lines("filter", recall=lambda *a, **k: hits,
                                         tracer=lambda *a, **k: {"confidence": "none", "is_fact": False},
                                         rows=[])
    assert "[UNVERIFIED CLAIM]" in lines[0]
    assert citations[0]["confidence"] == "none"


def test_every_evidence_line_carries_its_source():
    """Cite or omit: a line a weaker model cannot attribute is a line it will confidently build on."""
    hits = {"matches": [{"title": "gold is worse than chance at the 0.6th percentile",
                         "date": "2026-09-24", "source": r"C:\x\BASELINE.md", "text": "..."}]}
    lines, citations = cb.evidence_lines("gold", recall=lambda *a, **k: hits,
                                         tracer=lambda *a, **k: {"confidence": "cited", "is_fact": True},
                                         rows=[])
    assert "BASELINE.md" in lines[0] and "2026-09-24" in lines[0]
    assert citations[0]["source"].endswith("BASELINE.md")


def test_a_bigger_budget_asks_for_more_evidence_and_deeper_passages():
    small_limit, small_excerpt = cb.depth_for(8_000)
    big_limit, big_excerpt = cb.depth_for(180_000)
    assert big_limit > small_limit
    assert small_excerpt == 0 and big_excerpt > 0, "the small window gets titles, the large gets passages"


def test_the_builder_never_calls_a_model():
    """Layer 2 assembles text. Anything that could hallucinate belongs above it, not here."""
    source = Path(cb.__file__).read_text(encoding="utf-8")
    for forbidden in ("ai_provider.complete", "subprocess", "urllib.request"):
        assert forbidden not in source, forbidden
