"""The builder must never report success on the model's word.

Its whole value is that a spec rule and its evidence are joined by a number, and that the verdict is
computed from checks the model did not adjudicate. These tests pin that, plus the two mistakes made
while writing it: a trailing rule comment being swallowed into the shell command, and an unrun `run`
check being counted as a failure (which would make every verdict `failed`).
"""
from __future__ import annotations

import json

import pytest

from src.builder import (MAX_REPAIRS, Rule, Spec, coverage_gaps, draft_spec, parse_spec,
                         rule_checks, run_build, verdict_for)

GOOD = """RULES
1. The indicator marks the London sweep high and low on the 15m chart.
2. It draws nothing when fewer than 20 bars of the session exist.
3. An invalid symbol returns an empty result rather than raising.

CHECKS
R1 run: python -m pytest -q tests/test_sweep.py
R2 number: min_bars >= 20
R3 file: data/sweep.json contains "available": false
"""


def _completer(text, ok=True, error=""):
    def fake(prompt, task=""):        # same shape as ai_provider.complete
        return {"ok": ok, "text": text, "provider": "fake", "degraded": False, "error": error}
    return fake


def test_a_rule_and_its_check_are_joined_by_the_number():
    spec = parse_spec(GOOD, request="an indicator")
    assert [r.number for r in spec.rules] == [1, 2, 3]
    assert spec.rules[0].check == "run: python -m pytest -q tests/test_sweep.py"
    assert spec.rules[1].kind == "number"
    assert spec.uncovered == [] and spec.unattended == []


def test_an_unnumbered_rule_is_dropped_rather_than_auto_numbered():
    """Auto-numbering would attach a check to a requirement it was never written for."""
    spec = parse_spec("RULES\nit should be fast\n1. it returns within 2 seconds\n"
                      "CHECKS\nR1 number: seconds <= 2\n")
    assert [r.text for r in spec.rules] == ["it returns within 2 seconds"]


def test_the_rule_text_never_leaks_into_the_shell_command():
    """The parser's pattern ends in (.+?)$, so a trailing comment would become an argument."""
    from src.skill_acceptance import parse

    spec = parse_spec(GOOD, request="x")
    checks = parse(spec.acceptance_block())
    commands = [c.spec for c in checks if c.kind == "run"]
    assert commands == ["python -m pytest -q tests/test_sweep.py"]
    assert not any("#" in c.spec or "R1" in c.spec for c in checks)


def test_a_rule_with_no_check_caps_the_verdict_at_partial():
    spec = parse_spec("RULES\n1. a\n2. b\nCHECKS\nR1 number: x >= 1\n")
    assert spec.uncovered == [2]
    rows = rule_checks(spec, values={"x": 5})
    assert rows and rows[0]["passed"] is True
    out = verdict_for(spec, rows)
    assert out["verdict"] == "partial" and "R2" in out["why"]


def test_an_unrun_command_is_not_a_failure_and_not_a_pass():
    """With allow_run off a `run` check is unadjudicated. Either mislabel would be wrong."""
    spec = parse_spec("RULES\n1. it passes its tests\nCHECKS\nR1 run: python -c \"pass\"\n")
    rows = rule_checks(spec, allow_run=False)
    assert rows[0]["passed"] is False and rows[0]["adjudicated"] is False
    out = verdict_for(spec, rows)
    assert out["verdict"] == "partial", "an unrun command must not read as failed"
    assert out["pending_rules"] == [1]


def test_a_failing_check_is_failed_not_partial():
    spec = parse_spec("RULES\n1. the number is big\nCHECKS\nR1 number: x >= 100\n")
    out = verdict_for(spec, rule_checks(spec, values={"x": 3}))
    assert out["verdict"] == "failed" and out["failed_rules"] == [1]


def test_verified_needs_every_rule_covered_and_passing():
    spec = parse_spec("RULES\n1. a\n2. b\nCHECKS\nR1 number: x >= 1\nR2 number: y >= 2\n")
    out = verdict_for(spec, rule_checks(spec, values={"x": 1, "y": 2}))
    assert out["verdict"] == "verified" and out["passed"] == 2


def test_an_ask_only_rule_cannot_pass_unattended():
    spec = parse_spec("RULES\n1. it looks right\nCHECKS\nR1 ask: does the chart look right?\n")
    assert spec.unattended == [1]
    assert coverage_gaps(spec)["provable"] is False
    assert verdict_for(spec, rule_checks(spec))["verdict"] == "partial"


def test_no_spec_means_no_build_rather_than_building_anyway():
    out = run_build("make me something", completer=_completer("", ok=False, error="offline"),
                    record=False)
    assert out["ok"] is False and out["verdict"] == "no_spec" and "offline" in out["why"]


def test_an_answer_with_no_numbered_rules_is_refused():
    out = run_build("x", completer=_completer("Sure! I will build that for you."), record=False)
    assert out["verdict"] == "no_spec" and "no numbered rules" in out["why"]


def test_a_spec_whose_artefact_does_not_exist_yet_is_failed():
    """The spec parses and the rules are covered, but nothing has been built, so its own checks say no.

    This is the behaviour that matters: a good-looking spec is not a result. R1 is an unrun command
    and R2 an unmeasured number (both merely unknown), while R3 names a file that is genuinely absent
    - and one real failure is enough to be `failed` rather than `partial`.
    """
    out = run_build("an indicator", completer=_completer(GOOD), record=False)
    assert out["verdict"] == "failed" and out["ok"] is False
    assert out["places_orders"] is False
    assert out["coverage"]["rules"] == 3
    by_rule = {row["rule"]: row for row in out["checks"]}
    assert by_rule[1]["adjudicated"] is False, "an unrun command is unknown, not judged"
    assert by_rule[2]["adjudicated"] is False, "an unmeasured number is unknown, not judged"
    assert by_rule[3]["adjudicated"] is True and by_rule[3]["passed"] is False


def test_ok_is_true_only_for_verified():
    """The one path that sets ok=True: every rule covered by a machine check, every one passing."""
    spec_text = ("RULES\n1. profit factor beats one\n2. it takes at least thirty trades\n"
                 "CHECKS\nR1 number: profit_factor >= 1.0\nR2 number: trades >= 30\n")
    out = run_build("a measured thing", completer=_completer(spec_text),
                    values={"profit_factor": 1.62, "trades": 101}, record=False)
    assert out["verdict"] == "verified" and out["ok"] is True
    assert out["coverage"]["provable"] is True


def test_every_build_is_recorded_including_the_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTENTRY_DATA_DIR", str(tmp_path))
    run_build("x", completer=_completer("nothing parseable here"))
    line = json.loads((tmp_path / "builds" / "builds.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert line["verdict"] == "no_spec"


def test_the_repair_loop_is_capped_because_the_loop_is_not_the_lever():
    """arXiv:2607.06636 - an AlphaCodium-style loop only matched the baseline; grounding did the work."""
    assert MAX_REPAIRS <= 3


def test_the_spec_prompt_asks_for_one_check_per_rule():
    """Test COUNT is measurably not the lever, so the prompt must not invite padding."""
    from src.builder import SPEC_PROMPT

    assert "one check per rule" in SPEC_PROMPT.lower()
    assert "testable" in SPEC_PROMPT.lower()
    # The four boundary CATEGORIES, named generically. The measured baseline missed exactly these
    # kinds, and naming the category is grounding; naming the holdout's specific cases would be
    # fitting to the benchmark, which is why the words here are generic.
    lower = SPEC_PROMPT.lower()
    for category in ("nothing", "wrong", "edge", "result"):
        assert category in lower, f"the prompt must name the {category!r} boundary category"
    assert "at least 4" in lower, "a floor on the rule count: one task produced a single rule"


def test_a_rule_number_without_punctuation_still_parses():
    """The bug that cost a whole run: a 3B model wrote `R1 text`, not `R1. text`, and the regex
    demanded punctuation - so nine good rules parsed as zero and the build reported no_spec."""
    spec = parse_spec("R1 the function returns None for an empty list\n"
                      "R2 rejects an hour outside 0-23\n"
                      "R1 run: python -c \"pass\"\n")
    assert [r.number for r in spec.rules] == [1, 2]
    assert spec.rules[0].text == "the function returns None for an empty list"


def test_one_line_carrying_rule_and_check_is_understood():
    """The pipe form exists because asked for two sections a small model wrote the rules and silently
    skipped the checks. This shape makes a rule without a check impossible to express."""
    spec = parse_spec("R1 | returns None when the bar list is empty | run: python -m pytest -q\n"
                      "R2 | rejects an hour outside 0-23 | number: max_hour <= 23\n")
    assert len(spec.rules) == 2 and spec.uncovered == []
    assert spec.rules[0].check == "run: python -m pytest -q"
    assert spec.rules[1].kind == "number"


def test_the_prompt_shows_the_shape_it_wants():
    """A small model copies an example far more reliably than it follows a description of a format."""
    from src.builder import SPEC_PROMPT

    assert "R<number> |" in SPEC_PROMPT
    assert "R1 |" in SPEC_PROMPT, "it must show a worked example, not only describe the form"
