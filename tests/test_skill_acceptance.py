"""A skill may not report success on the model's own say-so - i40 Pilot build map step 4.

A model asked "did that work?" usually answers yes. That is not dishonesty; it has no independent
view of the artefact it just produced. So success requires at least one check adjudicated by
something else: a command's exit code, a file's contents, a number against a threshold, or the owner.

This matters most offline, which is the owner's constraint - a local 7B declares victory over broken
work more readily than Claude does, and a check the model does not adjudicate is the only defence
that scales down to a weaker model.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import skill_acceptance as sa


def _skill(*checks):
    return sa.SkillAcceptance("demo", "demo/SKILL.md", [sa.Check(k, v) for k, v in checks])


def test_a_skill_with_no_declared_check_may_not_claim_success():
    out = sa.may_report_success(_skill())
    assert out["may_report_success"] is False
    assert "model's own say-so" in out["reason"]


def test_a_passing_independent_check_allows_success():
    skill = _skill(("run", "true"))
    skill.checks[0].passed = True
    assert sa.may_report_success(skill)["may_report_success"] is True


def test_any_failure_blocks_success_even_beside_passes():
    skill = _skill(("run", "a"), ("run", "b"))
    skill.checks[0].passed, skill.checks[1].passed = True, False
    skill.checks[1].detail = "exit 3"
    out = sa.may_report_success(skill)
    assert out["may_report_success"] is False and "exit 3" in out["reason"]


def test_unknown_is_not_a_pass():
    """An unmeasured threshold and an unanswered question both leave the work unproven, and unproven
    must never read as done."""
    skill = _skill(("ask", "did it look right?"))
    sa.run_checks(skill)
    assert skill.checks[0].passed is None
    assert sa.may_report_success(skill)["may_report_success"] is False


def test_a_shell_check_is_not_run_unless_governance_permits_it():
    """Executing a command a skill file names is a real action; layer 7 decides, not this module.
    Refusing to run it is reported honestly as unknown - never as a pass."""
    skill = _skill(("run", "python -c pass"))
    sa.run_checks(skill, allow_run=False)
    assert skill.checks[0].passed is None
    assert "governance" in skill.checks[0].detail


def test_a_permitted_shell_check_really_adjudicates():
    ok = _skill(("run", 'python -c "import sys;sys.exit(0)"'))
    sa.run_checks(ok, allow_run=True)
    assert ok.checks[0].passed is True

    bad = _skill(("run", 'python -c "import sys;sys.exit(3)"'))
    sa.run_checks(bad, allow_run=True)
    assert bad.checks[0].passed is False and "exit 3" in bad.checks[0].detail


def test_a_number_nobody_measured_is_unknown_not_met():
    """A threshold nobody measured is not a threshold met."""
    skill = _skill(("number", "profit_factor >= 1.0"))
    sa.run_checks(skill, values=None)
    assert skill.checks[0].passed is None and "not a pass" in skill.checks[0].detail


def test_a_number_is_compared_against_what_was_measured():
    met = _skill(("number", "trades >= 100"))
    sa.run_checks(met, values={"trades": 7669})
    assert met.checks[0].passed is True

    missed = _skill(("number", "trades >= 100"))
    sa.run_checks(missed, values={"trades": 6})
    assert missed.checks[0].passed is False and "6" in missed.checks[0].detail


def test_a_directory_target_does_not_crash():
    """Reading a folder as text raises PermissionError on Windows, which took down the first run
    across all twelve skills."""
    skill = _skill(("file", "src"))
    sa.run_checks(skill)
    assert skill.checks[0].passed is True

    named = _skill(("file", "src contains second_brain"))
    sa.run_checks(named)
    assert named.checks[0].passed is True, named.checks[0].detail


def test_appended_asks_whether_the_work_was_recorded_today(tmp_path, monkeypatch):
    monkeypatch.setattr(sa, "ROOT", tmp_path)
    fresh = tmp_path / "NOTES.md"
    fresh.write_text("written now", encoding="utf-8")
    skill = _skill(("appended", "NOTES.md"))
    sa.run_checks(skill)
    assert skill.checks[0].passed is True

    missing = _skill(("appended", "nothing.md"))
    sa.run_checks(missing)
    assert missing.checks[0].passed is False


def test_every_skill_in_the_repo_declares_an_independent_check():
    """The acceptance test for step 4 itself. A skill added later without one fails this."""
    found = sa.coverage()
    assert found["total"] >= 12
    assert found["undeclared"] == [], f"these declare no check: {found['undeclared']}"
    assert found["coverage_pct"] == 100.0


def test_declared_checks_are_parseable_not_prose():
    """A check that can never pass is worse than no check - it looks like verification and is not.
    The first draft used descriptions like 'contains the rejection and its reason', which no file
    could ever satisfy."""
    for skill in sa.read_skills():
        for check in skill.checks:
            assert check.spec.strip(), f"{skill.name}: empty spec"
            if check.kind == "number":
                assert sa.NUMBER_RULE.match(check.spec.strip()), f"{skill.name}: {check.spec}"
            if check.kind == "file" and " contains " in check.spec:
                target, needle = check.spec.split(" contains ", 1)
                assert len(needle.split()) <= 6, (
                    f"{skill.name}: {needle!r} reads as prose, not literal text to search for")


def test_all_five_skill_families_have_a_skill():
    """Build map step 8. A family with no skill is a gap, reported rather than quietly missing."""
    found = sa.coverage()
    assert found["families_missing"] == [], f"no skill for: {found['families_missing']}"
    assert found["families_covered"] == 5
    for family in sa.FAMILIES:
        assert found["by_family"].get(family), family


def test_every_skill_declares_which_family_it_belongs_to():
    untagged = sa.coverage()["by_family"].get("untagged", [])
    assert untagged == [], f"these declare no family: {untagged}"


def test_the_business_skill_passes_its_own_acceptance_end_to_end():
    """The step-8 acceptance test: one skill per family, proven rather than asserted.

    Figures are the measured BTCUSD case - 3,544 trades, net -308.69, spread 0.1694 per trade -
    where the edge is real (0.0823 per trade gross) and the venue charges twice it.
    """
    skill = next(s for s in sa.read_skills() if s.name == "unit-economics")
    net, units, cost_per_unit = -308.69, 3544, 1694 * 0.01 * 0.01
    costs = cost_per_unit * units
    gross = net + costs
    error = abs(gross - costs - net)

    sa.run_checks(skill, values={"reconciliation_error": error, "units": units})
    out = sa.may_report_success(skill)
    assert out["may_report_success"] is True, out["reason"]
    assert error < 0.01, "gross - costs must equal net, or the three describe different sets"
