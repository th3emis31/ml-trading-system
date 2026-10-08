"""A strategy family the survey could not evaluate must reach a human.

WHY THIS EXISTS
---------------
`src/direction_sweep.py` surveys every strategy family the system owns and writes any it could not run
into a `failures` list inside its own report. On 27 September 2026 that list held:

    {"family": "sweep_reclaim", "market": "XAUUSD:30m", "error": "no registered builder"}

`sweep_reclaim` is the owner's own 4H manipulation rule, in his words from 26 September. The survey
that claims to test every strategy skipped it, wrote the reason to disk, and reported success. Nobody
read that file for eleven days. When the rule was finally measured on 8 October it came out positive
on all three splits with the highest after-cost expectancy of any candidate in the book, so the cost of
the silence was eleven days of not knowing the best thing the project had.

The underlying bug is already fixed. This check exists because nothing would have SAID so, and the
next silent skip would cost another eleven days.

These tests never run a backtest and never read the real reports: each builds its own survey file in a
tmp_path, so they say the same thing on any machine and in any order.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.system_doctor import SURVEY_MAX_AGE_DAYS, check_strategy_families

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def _survey(tmp_path, side, failures=(), tested=229, profitable=58, age_days=1.0):
    path = tmp_path / f"direction_sweep_{side}_20261008.json"
    path.write_text(json.dumps({
        "side": side, "variants_tested": tested, "profitable_on_holdout": profitable,
        "failures": list(failures), "places_orders": False,
    }), encoding="utf-8")
    stamp = (NOW - timedelta(days=age_days)).timestamp()
    import os
    os.utime(path, (stamp, stamp))
    return path


# ------------------------------------------------------------------ the failure that was missed
def test_the_exact_failure_that_hid_the_owners_rule_is_reported(tmp_path):
    _survey(tmp_path, "long", failures=[
        {"family": "sweep_reclaim", "market": "XAUUSD:30m", "error": "no registered builder"}])
    result = check_strategy_families(lab_dir=tmp_path, now=NOW)

    assert result["status"] == "warn", "a family the survey could not run must not read as healthy"
    assert "sweep_reclaim" in result["summary"]
    assert "no registered builder" in result["summary"]


def test_every_failing_family_is_listed_not_just_the_first(tmp_path):
    _survey(tmp_path, "long", failures=[
        {"family": "sweep_reclaim", "market": "XAUUSD:30m", "error": "no registered builder"},
        {"family": "crt_htf", "market": "BTCUSD:30m", "error": "variants failed: '30m'"}])
    result = check_strategy_families(lab_dir=tmp_path, now=NOW)

    assert "sweep_reclaim" in result["summary"] and "crt_htf" in result["summary"]
    rows = result["detail"]["surveys"]["long"]["failures"]
    assert {r["family"] for r in rows} == {"sweep_reclaim", "crt_htf"}


def test_both_direction_passes_are_checked_independently(tmp_path):
    _survey(tmp_path, "long")
    _survey(tmp_path, "short", failures=[
        {"family": "sweep_reclaim", "market": "XAUUSD:4h", "error": "no registered builder"}])
    result = check_strategy_families(lab_dir=tmp_path, now=NOW)

    assert result["status"] == "warn"
    assert "short" in result["summary"], "the failing pass must be named, or it cannot be found"
    assert result["detail"]["surveys"]["long"]["failures"] == []


# ------------------------------------------------------------------ a clean survey must stay quiet
def test_a_clean_recent_survey_is_ok(tmp_path):
    _survey(tmp_path, "long")
    _survey(tmp_path, "short")
    result = check_strategy_families(lab_dir=tmp_path, now=NOW)

    assert result["status"] == "ok"
    assert "229" in result["summary"], "say what was actually covered, not just that it passed"


# ------------------------------------------------------------------ a survey that describes old code
def test_a_stale_survey_is_flagged_even_when_nothing_failed(tmp_path):
    """The 27 September report kept showing a pre-fix error long after the fix landed, and looked
    exactly like current evidence. Age is part of whether a survey means anything."""
    _survey(tmp_path, "long", age_days=SURVEY_MAX_AGE_DAYS + 5)
    result = check_strategy_families(lab_dir=tmp_path, now=NOW)

    assert result["status"] == "warn"
    assert "old" in result["summary"] or "days" in result["summary"]


def test_a_survey_inside_the_age_limit_is_not_flagged_for_age(tmp_path):
    _survey(tmp_path, "long", age_days=SURVEY_MAX_AGE_DAYS - 1)
    result = check_strategy_families(lab_dir=tmp_path, now=NOW)

    assert result["status"] == "ok"


# ------------------------------------------------------------------ missing and broken inputs
def test_no_survey_at_all_is_info_not_a_failure(tmp_path):
    """Never having run the survey is a gap to fill, not a fault to alarm on."""
    result = check_strategy_families(lab_dir=tmp_path, now=NOW)

    assert result["status"] == "info"
    assert "direction_sweep" in result["summary"], "say the command that fixes it"


def test_a_missing_directory_does_not_raise(tmp_path):
    result = check_strategy_families(lab_dir=tmp_path / "does-not-exist", now=NOW)
    assert result["status"] == "info"


def test_an_unreadable_report_is_reported_rather_than_crashing_the_doctor(tmp_path):
    """The doctor runs every 30 minutes; one corrupt file must not take the whole run down."""
    (tmp_path / "direction_sweep_long_20261008.json").write_text("{not json", encoding="utf-8")
    result = check_strategy_families(lab_dir=tmp_path, now=NOW)

    assert result["status"] == "warn"
    assert "none could be read" in result["summary"]


def test_the_newest_report_per_side_wins(tmp_path):
    """Old reports stay on disk forever. A fixed survey must not be judged by a superseded one."""
    import os
    old = tmp_path / "direction_sweep_long_20260927.json"
    old.write_text(json.dumps({"side": "long", "variants_tested": 229, "profitable_on_holdout": 58,
                               "failures": [{"family": "sweep_reclaim", "market": "XAUUSD:30m",
                                             "error": "no registered builder"}]}), encoding="utf-8")
    stamp = (NOW - timedelta(days=11)).timestamp()
    os.utime(old, (stamp, stamp))
    _survey(tmp_path, "long", age_days=0.1)          # the fixed re-run, same side, newer

    result = check_strategy_families(lab_dir=tmp_path, now=NOW)
    assert result["status"] == "ok", "the newer clean survey for that side must supersede the old one"


# ------------------------------------------------------------------ the check is wired in
def test_the_check_runs_as_part_of_the_doctor():
    """A check nothing calls is the same as no check, which is the failure being fixed here."""
    import inspect

    from src import system_doctor
    source = inspect.getsource(system_doctor)
    assert "check_strategy_families," in source or "check_strategy_families)" in source, \
        "check_strategy_families is defined but never added to the doctor's list of checks"


def test_it_reads_reports_and_never_runs_a_backtest():
    """This runs every 30 minutes. If it ever starts evaluating candidates it will fight the lab for
    the machine, which is how the doctor stops being safe to run often."""
    import inspect

    source = inspect.getsource(check_strategy_families)
    for forbidden in ("evaluate_candidate", "simulate", "Market(", "load_bars", "strategy_orders"):
        assert forbidden not in source, f"check_strategy_families must not call {forbidden}"
