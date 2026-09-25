"""Memory must be able to say where a claim came from - i40 Pilot build map step 2.

Acceptance test for the step: any fact can be traced to the event or measurement that produced it.
Without that, a guess written down on a bad day is indistinguishable six months later from a result
measured over 7,669 trades, and both get cited with the same confidence.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import second_brain as sb


def _memory_fixture(tmp_path, baseline="", lessons="", notes=""):
    (tmp_path / "BASELINE.md").write_text(baseline, encoding="utf-8")
    (tmp_path / "LESSONS.md").write_text(lessons, encoding="utf-8")
    (tmp_path / "NOTES.md").write_text(notes, encoding="utf-8")
    (tmp_path / "BACKLOG.md").write_text("", encoding="utf-8")
    return sb.entries(memory_dir=tmp_path, data_dir=tmp_path, strategy_dir=tmp_path)


BASELINE = """## 2026-09-24 - gold stop sweep

The stop is a fixed point count and gold tripled, so the optimum rises with the price era.

| SL | net | PF |
|---|---|---|
| 2100 | +1234.62 | 1.12 |
| 3000 | +1911.43 | 1.17 |
"""


def test_the_four_memory_kinds_are_distinguished(tmp_path):
    """They have different rules: evidence is append-only, episodic is prunable, semantic is not."""
    rows = _memory_fixture(tmp_path, BASELINE, "- 2026-09-24 [x]: a lesson\n", "- 2026-09-24: a note\n")
    kinds = {sb.memory_kind(r) for r in rows}
    assert {"evidence", "semantic", "episodic"} <= kinds
    assert sb.KIND_RULES["result"]["append_only"] is True
    assert sb.KIND_RULES["note"]["prunable"] is True
    assert sb.KIND_RULES["lesson"]["prunable"] is False, "a truth is corrected, not pruned"


def test_baseline_prose_is_indexed_not_only_table_rows(tmp_path):
    """309 of BASELINE.md's 652 lines were headings and prose, and none of it was searchable -
    so recall could not see what had been measured, rejected, or why."""
    rows = _memory_fixture(tmp_path, BASELINE)
    text = " ".join(r["text"] for r in rows if r["kind"] == "result")
    assert "fixed point count" in text, "the section's prose must be indexed"


def test_a_row_inherits_its_section_date_rather_than_its_first_cell(tmp_path):
    """A sweep row's first cell is a parameter value, not a date. Reading it as one left every
    finding recorded on 24 September with no date at all."""
    rows = [r for r in _memory_fixture(tmp_path, BASELINE) if r["kind"] == "result"]
    assert rows, "sections and rows should both be indexed"
    assert all(r["date"] == "2026-09-24" for r in rows), [r["date"] for r in rows]


def test_an_explicit_citation_is_trusted(tmp_path):
    rows = _memory_fixture(tmp_path, BASELINE,
                  lessons="- 2026-09-24 [gold]: widen the stop (ref: gold stop sweep)\n")
    lesson = next(r for r in rows if r["kind"] == "lesson")
    found = sb.trace(lesson, rows)
    assert found["confidence"] == "cited" and found["is_fact"] is True


def test_a_citation_that_points_at_nothing_is_not_a_fact(tmp_path):
    """Citing a source that does not exist is worse than citing none: it looks verified."""
    rows = _memory_fixture(tmp_path, BASELINE,
                  lessons="- 2026-09-24 [x]: a claim (ref: a study nobody ever ran)\n")
    found = sb.trace(next(r for r in rows if r["kind"] == "lesson"), rows)
    assert found["confidence"] == "cited_but_missing" and found["is_fact"] is False


def test_a_same_day_measurement_supports_a_claim_but_is_labelled_inferred(tmp_path):
    rows = _memory_fixture(tmp_path, BASELINE,
                  notes="- 2026-09-24: the gold stop sweep shows the optimum rises with the price era\n")
    found = sb.trace(next(r for r in rows if r["kind"] == "note"), rows)
    assert found["confidence"] == "inferred" and found["is_fact"] is True
    assert "not cited" in found["note"], "inferred support must never be reported as a citation"


def test_an_unsupported_numeric_claim_is_a_hypothesis(tmp_path):
    rows = _memory_fixture(tmp_path, "", notes="- 2026-09-24: the new filter improves profit factor to 1.40\n")
    found = sb.trace(next(r for r in rows if r["kind"] == "note"), rows)
    assert found["is_fact"] is False and "HYPOTHESIS" in found["note"]


def test_an_ordinary_note_without_figures_is_not_flagged(tmp_path):
    """A checker that cries wolf gets ignored. A tooling note that merely contains the word 'edge'
    is an instruction, not an unsupported finding."""
    rows = _memory_fixture(tmp_path, "",
                  notes="- 2026-09-24: Git Bash rewrites CLI paths; set MSYS_NO_PATHCONV at the edge\n")
    found = sb.trace(next(r for r in rows if r["kind"] == "note"), rows)
    assert "HYPOTHESIS" not in found["note"], found["note"]


def test_compaction_refuses_while_anything_awaits_promotion(tmp_path):
    """Order matters: compacting first destroys the evidence for a pattern as it becomes visible."""
    notes = ("- 2026-08-01: the spread filter blocked entries at rollover again\n"
             "- 2026-08-09: the spread filter blocked entries at rollover again today\n")
    rows = _memory_fixture(tmp_path, "", notes=notes)
    pending = sb.promotion_candidates(rows)
    assert pending["count"] >= 1, "a subject on two separate days is a pattern"
    out = sb.compaction_candidates(rows)
    assert out["safe"] is False and out["count"] == 0
    assert "awaiting promotion" in out["reason"]


def test_promotion_and_compaction_only_ever_propose(tmp_path):
    """Writing a note up into a truth is the owner's decision; a system that promotes its own notes
    without review is how a guess becomes doctrine."""
    source = Path(sb.__file__).read_text(encoding="utf-8")
    block = source[source.index("def promotion_candidates"):source.index("def provenance_report")]
    for forbidden in ("write_text", "unlink", "open(", ".pop(", "del "):
        assert forbidden not in block, forbidden
    assert "Proposed only" in block


def test_the_provenance_report_counts_what_can_be_traced(tmp_path):
    rows = _memory_fixture(tmp_path, BASELINE,
                  lessons="- 2026-09-24 [gold]: widen the stop (ref: gold stop sweep)\n",
                  notes="- 2026-09-24: the new filter improves profit factor to 1.40\n")
    report = sb.provenance_report(rows)
    assert report["total"] == len(rows)
    assert report["traceable"] >= 1 and report["hypotheses"] >= 1
    assert report["by_memory"]["evidence"]["cited"] >= 1, "a measurement is its own source"
