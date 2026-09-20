"""The second brain: askable durable memory that never answers in its own words."""
import json

import pytest

from src import second_brain as sb


@pytest.fixture()
def records(tmp_path):
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "BASELINE.md").write_text(
        "| date | commit | type | strategy | notes |\n|---|---|---|---|---|\n"
        "| 2026-09-15 | abc | backtest | CRT higher timeframes | classic CRT loses on every market |\n"
        "| 2026-09-18 | def | backtest | morning star candles | positive on all three splits |\n", encoding="utf-8")
    (memory / "LESSONS.md").write_text(
        "# Lessons\n\n- 2026-09-14 [costs]: the spread was six times too harsh -> measure it\n", encoding="utf-8")
    (memory / "NOTES.md").write_text("- 2026-09-19: the trailing stop capped every winner\n", encoding="utf-8")
    (memory / "BACKLOG.md").write_text("- [ ] wire the plan into the demo executor\n"
                                       "- [x] already finished thing\n", encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir()
    (data / "learning_decisions.json").write_text(json.dumps(
        [{"symbol": "XAUUSD", "trained_at": "2026-09-20 05:30:12", "status": "trained",
          "rf_promoted": False, "accuracy": 0.53, "data_source": "broker"}]), encoding="utf-8")
    strategies = tmp_path / "strategies"
    strategies.mkdir()
    (strategies / "sweep.md").write_text("# The 4H manipulation candle\n\nDeclared 2026-09-19.\n", encoding="utf-8")
    return sb.entries(memory_dir=memory, data_dir=data, strategy_dir=strategies)


def test_it_reads_every_kind_of_durable_record(records):
    kinds = {r["kind"] for r in records}
    assert kinds == {"result", "lesson", "note", "backlog", "learning_decision", "strategy_doc"}
    assert len([r for r in records if r["kind"] == "result"]) == 2, "the header row is not a result"
    assert len([r for r in records if r["kind"] == "backlog"]) == 1, "a finished item is not open work"


def test_a_recall_points_at_a_real_record_with_its_date_and_source(records):
    out = sb.recall("classic CRT higher timeframes", rows=records)
    assert out["match_count"] >= 1
    best = out["matches"][0]
    assert best["kind"] == "result" and best["date"] == "2026-09-15"
    assert "BASELINE.md" in best["source"]
    assert "loses on every market" in best["excerpt"]


def test_nothing_recorded_says_nothing_rather_than_inventing(records):
    out = sb.recall("quantum entanglement arbitrage", rows=records)
    assert out["matches"] == [] and out["match_count"] == 0
    assert "has not been written down" in out["answer"]


def test_prior_work_is_the_question_that_stops_repeated_work(records):
    """The failure it exists to prevent: proposing something the record already settled."""
    out = sb.prior_work("CRT higher timeframes", rows=records)
    assert out["tried_before"] is True and out["confidence"] == "recorded"
    assert "Read them before repeating the work" in out["verdict"]
    assert any(e["date"] == "2026-09-15" for e in out["evidence"])


def test_prior_work_does_not_claim_something_will_work_just_because_it_is_unrecorded(records):
    out = sb.prior_work("an idea nobody has ever had", rows=records)
    assert out["tried_before"] is False and out["confidence"] == "none"
    assert "Nothing here says it will work either" in out["verdict"]


def test_a_mention_without_a_result_is_not_reported_as_tried(records):
    """Notes and backlog items are discussion, not measurement, and must not read as evidence."""
    out = sb.prior_work("wire the plan into the demo executor", rows=records)
    assert out["tried_before"] is False
    assert out["confidence"] == "mentioned" and "discussed rather than measured" in out["verdict"]


def test_the_excerpt_shows_the_part_that_matched(records):
    out = sb.recall("trailing stop capped", rows=records)
    assert "trailing stop capped" in out["matches"][0]["excerpt"]


def test_status_counts_what_it_holds_without_summarising_it(records):
    state = sb.status(rows=records)
    assert state["available"] and state["records"] == len(records)
    assert state["by_kind"]["result"] == 2
    assert state["earliest"] <= state["latest"]


def test_it_stores_nothing_and_cannot_trade():
    text = open(sb.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    for forbidden in ("order_send", "OrderSend", "place_order", "auto_execute", "MetaTrader5"):
        assert forbidden not in text
    assert "write_text" not in text, "the second brain reads the record; it never writes to it"
