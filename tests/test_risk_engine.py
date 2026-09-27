"""STEP 3: spec-aware position sizing, and the refusal to make a size up.

The single most important thing pinned here is what this module does NOT do. It does not turn 0.0137
lots into 0.01. It does not raise a size up to the broker's minimum. It does not assume a currency rate
is 1.0, and it does not fall back to a plausible contract size for an instrument it has never measured.
Each of those would produce a confident, wrong position while the arithmetic still looked tidy — and a
wrong contract size is invisible precisely because nothing about the result looks odd.

The second thing pinned here is honesty about 1 %. `target_risk` is 1.00 % of equity exactly; the size
that can actually be traded is on a `lot_step` grid, so `actual_risk` almost never equals it. These
tests assert the DIFFERENCE is reported rather than asserting it away.

Deterministic specs are built inline wherever the point is arithmetic, so a broker re-measurement
cannot change a test's answer. Two tests deliberately use the real `config/instrument_specs.json`,
because "the shipped configuration is coherent" is itself worth testing.
"""
import pytest

from engine import instrument_specs as ins
from engine import risk_engine as r
from engine.reject_codes import RISK_CODES, RejectCode


def _spec(**over) -> ins.InstrumentSpec:
    """A deterministic gold-shaped spec: 0.01 tick, 100 per lot, 1.00 account-currency per tick.

    tick_value 1.0 makes the arithmetic checkable by hand — one full price unit on one lot is 100.00 —
    so a failure points at the code rather than at a rate that moved.
    """
    base = dict(symbol="TEST", tick_size=0.01, contract_size=100.0, min_lot=0.01, max_lot=100.0,
                lot_step=0.01, currency="USD", account_currency="USD", tick_value=1.0,
                tick_value_currency="USD", digits=2, measured_at="2026-09-27T21:00Z",
                source="deterministic test spec")
    base.update(over)
    return ins.InstrumentSpec(**base)


# --------------------------------------------------------------------------- existing API preserved

def test_the_existing_position_size_is_untouched():
    """Its callers must not notice STEP 3 happened. This is the same assertion the engine-package
    suite already makes, repeated here so a change to this module fails this file too."""
    lots = r.position_size(equity=10_000, risk_percent=1.0, stop_distance_price=8.30,
                           value_per_price_unit_per_lot=100)
    assert lots is not None
    assert 8.30 * 100 * lots == pytest.approx(100.0), "1 % of 10,000 is 100, whatever the stop"

    for bad in ({"stop_distance_price": 0.0}, {"stop_distance_price": -5.0},
                {"value_per_price_unit_per_lot": 0.0}, {"risk_percent": 0.0}):
        args = {"equity": 10_000, "risk_percent": 1.0, "stop_distance_price": 8.3,
                "value_per_price_unit_per_lot": 100, **bad}
        assert r.position_size(**args) is None, bad


def test_check_trade_and_its_refusal_are_unchanged():
    out = r.check_trade("XAUUSD", 4280, 4288, 4256, r.Exposure({"XAUUSD": 1}))
    assert out is not True and bool(out) is False
    assert "one open trade per asset" in out.reason


# --------------------------------------------------------------------------- 1 %: target vs actual

def test_target_risk_is_exactly_one_percent_and_actual_risk_usually_is_not():
    """The distinction the whole step turns on. 0.01 lots is the grid; 1 % of equity is not on it."""
    out = r.size_position(symbol="TEST", equity=10_000, entry=4286.12, stop=4294.42, spec=_spec())
    assert out, out
    assert out.target_risk == pytest.approx(100.0), "target IS exactly 1 % by construction"
    assert out.raw_position_size == pytest.approx(100.0 / (830.0 * 1.0), rel=1e-9)
    assert out.final_position_size == pytest.approx(0.12), "0.120481 floored to the 0.01 grid"
    assert out.actual_risk == pytest.approx(0.12 * 830.0, rel=1e-9)
    assert out.risk_error == pytest.approx(out.actual_risk - out.target_risk)
    assert out.risk_error < 0, "flooring can only ever risk LESS than the target"
    assert out.exact is False, "and the module must not claim otherwise"


def test_a_size_that_lands_on_the_grid_is_reported_as_exact():
    """Not impossible, just uncommon — and when it happens it must be distinguishable."""
    # 0.10 lots x 100 ticks x 1.00 = 100.00 exactly, with a 1.00 price stop.
    out = r.size_position(symbol="TEST", equity=10_000, entry=100.0, stop=101.0, spec=_spec())
    assert out.final_position_size == pytest.approx(1.0)
    assert out.actual_risk == pytest.approx(100.0)
    assert out.risk_error == pytest.approx(0.0)
    assert out.exact is True


def test_the_reported_error_is_the_real_money_difference():
    out = r.size_position(symbol="TEST", equity=10_000, entry=4286.12, stop=4294.42, spec=_spec())
    money_per_lot = (out.stop_distance / out.tick_size) * out.tick_value
    assert out.actual_risk == pytest.approx(out.final_position_size * money_per_lot, rel=1e-9)
    assert out.risk_error_percent_of_target == pytest.approx(
        out.risk_error / out.target_risk * 100.0, rel=1e-6)


@pytest.mark.parametrize("equity", [1_000, 5_000, 10_000, 87_839.31, 250_000])
def test_target_risk_tracks_equity_exactly_at_one_percent(equity):
    out = r.size_position(symbol="TEST", equity=equity, entry=4286.12, stop=4294.42, spec=_spec())
    assert out.target_risk == pytest.approx(equity * 0.01)
    assert out.actual_risk <= out.target_risk + 1e-9, "never MORE than asked"


@pytest.mark.parametrize("stop_distance", [0.5, 1.0, 8.3, 25.0, 120.0])
def test_a_wider_stop_gives_a_smaller_size_and_never_more_risk(stop_distance):
    out = r.size_position(symbol="TEST", equity=100_000, entry=4286.12,
                          stop=4286.12 + stop_distance, spec=_spec())
    assert out, out
    assert out.stop_distance == pytest.approx(stop_distance)
    assert out.actual_risk <= out.target_risk + 1e-9


# --------------------------------------------------------------------------- the real shipped specs

def test_the_shipped_specification_file_is_coherent():
    """A broken config would refuse every trade, and the refusal would look like a code bug."""
    specs = ins.load_specs()
    assert {"XAUUSD", "BTCUSD"} <= set(specs)
    for spec in specs.values():
        ins.validate(spec)
        assert spec.account_currency == "GBP", "the measured account settles in GBP"
        assert spec.currency == "USD", "both instruments profit in USD"
        assert spec.needs_conversion is False, "because tick_value is already reported in GBP"


def test_the_two_shipped_tick_values_imply_the_SAME_exchange_rate():
    """This is what makes the readings measured rather than coincidental: gold's 1.00 USD per tick per
    lot and bitcoin's 0.01 USD must convert at one rate, on two different contract sizes."""
    specs = ins.load_specs()
    rates = {}
    for name in ("XAUUSD", "BTCUSD"):
        spec = specs[name]
        usd_per_tick_per_lot = spec.tick_size * spec.contract_size
        rates[name] = spec.tick_value / usd_per_tick_per_lot
    assert rates["XAUUSD"] == pytest.approx(rates["BTCUSD"], rel=1e-9), rates
    assert 0.5 < rates["XAUUSD"] < 1.5, f"a USD->GBP rate should be near parity, got {rates}"


def test_xauusd_sizes_from_the_shipped_spec_and_reports_its_rounding_error():
    out = r.size_position(symbol="XAUUSD", equity=10_000, entry=4286.12, stop=4294.42)
    assert out, out
    assert out.instrument == "XAUUSD" and out.account_currency == "GBP"
    assert out.contract_size == 100.0 and out.tick_size == 0.01
    assert out.target_risk == pytest.approx(100.0)
    assert out.final_position_size == pytest.approx(0.15)
    assert out.actual_risk == pytest.approx(94.059518, abs=1e-5)
    assert out.risk_error == pytest.approx(-5.940482, abs=1e-5)
    assert out.exact is False


def test_btcusd_sizes_from_the_shipped_spec_and_reports_its_rounding_error():
    out = r.size_position(symbol="BTCUSD", equity=10_000, entry=84711.67, stop=85911.67)
    assert out, out
    assert out.contract_size == 1.0, "1 BTC per lot, which the holding-cost record agrees with"
    assert out.target_risk == pytest.approx(100.0)
    assert out.final_position_size == pytest.approx(0.11)
    assert out.actual_risk == pytest.approx(99.725754, abs=1e-5)
    assert out.risk_error == pytest.approx(-0.274246, abs=1e-5)
    assert out.exact is False


def test_gold_rounds_far_worse_than_bitcoin_at_the_same_risk():
    """Not a defect — a consequence of gold's money-per-lot being large against a 0.01 step. Worth a
    test because it is the number a reader would otherwise assume is small on both."""
    gold = r.size_position(symbol="XAUUSD", equity=10_000, entry=4286.12, stop=4294.42)
    btc = r.size_position(symbol="BTCUSD", equity=10_000, entry=84711.67, stop=85911.67)
    assert abs(gold.risk_error) > abs(btc.risk_error) * 10


# --------------------------------------------------------------------------- NO SILENT CLAMPING

def test_a_size_below_the_minimum_lot_is_REFUSED_not_raised_to_the_minimum():
    """Raising to min_lot would risk MORE than asked, which is the one direction that must never
    happen by accident. The refusal states what the minimum would have cost."""
    out = r.size_position(symbol="TEST", equity=100, entry=4286.12, stop=4294.42, spec=_spec())
    assert out is not True and bool(out) is False
    assert out.code is RejectCode.MIN_LOT
    assert out.inputs["min_lot"] == 0.01
    assert out.inputs["risk_at_min_lot"] > out.inputs["target_risk"], \
        "the refusal must show that the minimum would have over-risked"
    assert "refused rather than raised" in out.message


def test_a_size_above_the_maximum_lot_is_REFUSED_not_cut_down():
    out = r.size_position(symbol="TEST", equity=10_000_000, entry=100.0, stop=100.01,
                          spec=_spec(max_lot=1.0))
    assert out.code is RejectCode.MAX_LOT
    assert out.inputs["max_lot"] == 1.0
    assert "refused rather than cut" in out.message


def test_flooring_is_the_declared_policy_and_it_is_named_in_the_result():
    out = r.size_position(symbol="TEST", equity=10_000, entry=4286.12, stop=4294.42, spec=_spec())
    assert out.rounding == "down"
    assert out.final_position_size <= out.raw_position_size, "floor, never ceiling"


def test_exact_rounding_refuses_rather_than_resizing():
    """For a caller who would rather not trade than trade a size they did not ask for."""
    out = r.size_position(symbol="TEST", equity=10_000, entry=4286.12, stop=4294.42,
                          spec=_spec(), rounding="exact")
    assert out.code is RejectCode.INVALID_LOT
    assert "refused rather than resized" in out.message

    on_grid = r.size_position(symbol="TEST", equity=10_000, entry=100.0, stop=101.0,
                              spec=_spec(), rounding="exact")
    assert on_grid, "a size already on the grid must still pass under exact rounding"
    assert on_grid.exact is True


def test_an_unknown_rounding_policy_is_refused_rather_than_defaulted():
    out = r.size_position(symbol="TEST", equity=10_000, entry=100.0, stop=101.0,
                          spec=_spec(), rounding="nearest")
    assert out.code is RejectCode.RISK
    assert "never guessed" in out.message


def test_the_step_grid_arithmetic_survives_binary_floating_point():
    """0.13 / 0.01 is 12.999999999999998 in binary, so a bare floor divide loses a whole step and every
    position comes out one step too small."""
    assert r._floor_to_step(0.13, 0.01) == pytest.approx(0.13)
    assert r._floor_to_step(0.1299, 0.01) == pytest.approx(0.12)
    assert r._floor_to_step(0.29, 0.01) == pytest.approx(0.29)
    assert r._floor_to_step(1.0, 0.1) == pytest.approx(1.0)


@pytest.mark.parametrize("lot_step,raw_equity,expected", [
    (0.01, 10_000, 0.12),
    (0.1, 10_000, 0.1),
    (1.0, 1_000_000, 12.0),
])
def test_the_lot_step_actually_changes_the_size(lot_step, raw_equity, expected):
    out = r.size_position(symbol="TEST", equity=raw_equity, entry=4286.12, stop=4294.42,
                          spec=_spec(lot_step=lot_step, min_lot=lot_step))
    assert out, out
    assert out.final_position_size == pytest.approx(expected)


# --------------------------------------------------------------------------- invalid inputs

@pytest.mark.parametrize("kw,code", [
    (dict(entry=4286.12, stop=4286.12), RejectCode.INVALID_STOP),
    (dict(entry=4286.12, stop=None), RejectCode.INVALID_STOP),
    (dict(entry=None, stop=4294.42), RejectCode.INVALID_STOP),
    (dict(entry=4286.12, stop=4294.42, equity=0), RejectCode.RISK),
    (dict(entry=4286.12, stop=4294.42, equity=-5_000), RejectCode.RISK),
    (dict(entry=4286.12, stop=4294.42, risk_percent=0), RejectCode.RISK),
    (dict(entry=4286.12, stop=4294.42, risk_percent=-1), RejectCode.RISK),
])
def test_an_impossible_input_is_refused_with_a_code_and_its_inputs(kw, code):
    args = {"symbol": "TEST", "equity": 10_000, "spec": _spec(), **kw}
    out = r.size_position(**args)
    assert out is not True and bool(out) is False
    assert out.code is code, f"{kw}: got {out.code}"
    assert out.message and out.inputs, "every refusal carries a message and its inputs"
    assert out.code in RISK_CODES or out.code is RejectCode.INVALID_STOP


def test_a_zero_stop_distance_is_a_stop_problem_not_a_lot_problem():
    """The deepest reason, not the last test to run: there is no risk unit, so nothing can be sized."""
    out = r.size_position(symbol="TEST", equity=10_000, entry=100.0, stop=100.0, spec=_spec())
    assert out.code is RejectCode.INVALID_STOP
    assert out.inputs["stop_distance"] == 0


def test_risk_above_the_ceiling_is_refused_and_NOT_quietly_reduced_to_it():
    out = r.size_position(symbol="TEST", equity=10_000, entry=4286.12, stop=4294.42,
                          spec=_spec(), risk_percent=5.0)
    assert out.code is RejectCode.RISK_TOO_LARGE
    assert out.inputs["limit_risk_percent"] == 1.0
    assert "not quietly applied in its place" in out.message


def test_a_higher_ceiling_can_be_declared_before_the_run():
    out = r.size_position(symbol="TEST", equity=10_000, entry=4286.12, stop=4294.42,
                          spec=_spec(), risk_percent=2.0, limits=r.Limits(risk_percent=2.0))
    assert out, out
    assert out.target_risk == pytest.approx(200.0)


# --------------------------------------------------------------------------- instrument and spec

def test_an_unmeasured_instrument_is_refused_rather_than_given_a_default():
    """A default contract size is the most dangerous number a risk engine can hold."""
    out = r.size_position(symbol="EURUSD", equity=10_000, entry=1.08, stop=1.075)
    assert out.code is RejectCode.INVALID_INSTRUMENT
    assert "EURUSD" in out.message and "known" in out.inputs
    assert "XAUUSD" in out.inputs["known"], "the refusal names what IS measured"


@pytest.mark.parametrize("broken,why", [
    (dict(tick_size=0), "a zero tick size divides by nothing"),
    (dict(contract_size=0), "a zero contract size makes every lot worthless"),
    (dict(min_lot=0), "a zero minimum is not a volume"),
    (dict(lot_step=0), "a zero step is not a grid"),
    (dict(min_lot=5.0, max_lot=1.0), "min above max makes every size unfillable"),
    (dict(lot_step=1.0, min_lot=0.01), "a step larger than the minimum is off its own grid"),
    (dict(tick_value=-1.0), "a negative tick value inverts the sizing"),
    (dict(currency=""), "no currency means no money figure can be trusted"),
])
def test_an_incoherent_specification_is_refused_with_INVALID_SPEC(broken, why):
    out = r.size_position(symbol="TEST", equity=10_000, entry=100.0, stop=101.0,
                          spec=_spec(**broken))
    assert out.code is RejectCode.INVALID_SPEC, why
    assert out.message and out.inputs


def test_a_missing_specification_file_is_refused_not_ignored(tmp_path):
    out = r.size_position(symbol="XAUUSD", equity=10_000, entry=4286.12, stop=4294.42,
                          specs_path=tmp_path / "does_not_exist.json")
    assert out.code is RejectCode.INVALID_SPEC
    assert "could not be read" in out.message


def test_a_specification_file_that_names_no_account_currency_is_refused(tmp_path):
    import json
    path = tmp_path / "specs.json"
    path.write_text(json.dumps({"instruments": {"TEST": {}}}), encoding="utf-8")
    out = r.size_position(symbol="TEST", equity=10_000, entry=100.0, stop=101.0, specs_path=path)
    assert out.code is RejectCode.INVALID_SPEC
    assert "account_currency" in out.message


# --------------------------------------------------------------------------- currency conversion

def test_same_currency_needs_no_rate():
    spec = _spec(currency="USD", account_currency="USD", tick_value=None, tick_value_currency=None)
    assert spec.needs_conversion is False
    out = r.size_position(symbol="TEST", equity=10_000, entry=100.0, stop=101.0, spec=spec)
    assert out, out
    assert out.tick_value == pytest.approx(1.0), "0.01 tick x 100 per lot = 1.00, already in USD"
    assert out.conversion_rate is None


def test_a_different_currency_uses_the_rate_it_is_given():
    spec = _spec(currency="USD", account_currency="GBP", tick_value=None, tick_value_currency=None)
    assert spec.needs_conversion is True
    out = r.size_position(symbol="TEST", equity=10_000, entry=100.0, stop=101.0, spec=spec,
                          conversion_rate=0.7554981376970905)
    assert out, out
    assert out.tick_value == pytest.approx(0.7554981376970905)
    assert out.conversion_rate == pytest.approx(0.7554981376970905)
    assert out.actual_risk <= out.target_risk + 1e-9


def test_a_missing_conversion_is_an_explicit_rejection_never_a_rate_of_one():
    spec = _spec(currency="USD", account_currency="GBP", tick_value=None, tick_value_currency=None)
    out = r.size_position(symbol="TEST", equity=10_000, entry=100.0, stop=101.0, spec=spec)
    assert out.code is RejectCode.CURRENCY_CONVERSION
    assert "never assumed to be 1.0" in out.message
    assert out.inputs["account_currency"] == "GBP" and out.inputs["currency"] == "USD"


@pytest.mark.parametrize("rate", [0, -0.75, float("nan")])
def test_an_invalid_conversion_rate_is_refused(rate):
    spec = _spec(currency="USD", account_currency="GBP", tick_value=None, tick_value_currency=None)
    out = r.size_position(symbol="TEST", equity=10_000, entry=100.0, stop=101.0, spec=spec,
                          conversion_rate=rate)
    assert out.code is RejectCode.CURRENCY_CONVERSION
    assert "positive finite" in out.message


def test_a_broker_tick_value_wins_over_a_supplied_rate():
    """The broker's figure is measured; a caller's rate is an argument. When both exist, prefer the
    measurement, or the same trade sizes differently depending on who called it."""
    spec = _spec(currency="USD", account_currency="GBP", tick_value=0.75, tick_value_currency="GBP")
    out = r.size_position(symbol="TEST", equity=10_000, entry=100.0, stop=101.0, spec=spec,
                          conversion_rate=999.0)
    assert out.tick_value == pytest.approx(0.75), "the 999 rate must be ignored, not multiplied in"


def test_a_tick_value_in_the_WRONG_currency_is_not_treated_as_converted():
    spec = _spec(currency="USD", account_currency="GBP", tick_value=1.0, tick_value_currency="USD")
    assert spec.needs_conversion is True
    out = r.size_position(symbol="TEST", equity=10_000, entry=100.0, stop=101.0, spec=spec)
    assert out.code is RejectCode.CURRENCY_CONVERSION


# --------------------------------------------------------------------------- staleness and audit

def test_a_spec_reports_how_old_its_measurement_is():
    from datetime import datetime, timezone
    spec = _spec(measured_at="2026-09-20T00:00Z")
    days = spec.staleness_days(now=datetime(2026, 9, 27, tzinfo=timezone.utc))
    assert days == pytest.approx(7.0)
    assert _spec(measured_at=None).staleness_days() is None


def test_the_audit_carries_every_field_needed_to_reconstruct_the_calculation():
    out = r.size_position(symbol="XAUUSD", equity=10_000, entry=4286.12, stop=4294.42).as_dict()
    for field in ("equity", "risk_percent", "target_risk", "entry", "stop", "stop_distance",
                  "tick_size", "tick_value", "contract_size", "raw_position_size",
                  "final_position_size", "actual_risk", "risk_error", "instrument", "currency",
                  "account_currency", "rounding", "ticks_to_stop", "exact"):
        assert field in out, field
    # and the figures must actually reconcile, not merely be present
    money_per_lot = (out["stop_distance"] / out["tick_size"]) * out["tick_value"]
    assert out["raw_position_size"] == pytest.approx(out["target_risk"] / money_per_lot, rel=1e-6)
    assert out["actual_risk"] == pytest.approx(out["final_position_size"] * money_per_lot, rel=1e-6)
    assert out["risk_error"] == pytest.approx(out["actual_risk"] - out["target_risk"], abs=1e-6)


def test_a_rejection_serialises_to_code_message_and_inputs():
    out = r.size_position(symbol="EURUSD", equity=10_000, entry=1.08, stop=1.075).as_dict()
    assert set(out) == {"code", "message", "inputs"}
    assert out["code"] == "REJECT_INVALID_INSTRUMENT", "the stable string, not a repr"


# --------------------------------------------------------------------------- coded limit checks

@pytest.mark.parametrize("exposure,code", [
    (r.Exposure({"XAUUSD": 0}, drawdown_percent=20.0), RejectCode.MAX_DRAWDOWN),
    (r.Exposure({"XAUUSD": 0}, day_pnl_percent=-4.0), RejectCode.DAILY_LOSS),
    (r.Exposure({"XAUUSD": 1}), RejectCode.MAX_EXPOSURE),
])
def test_the_coded_limit_check_names_which_rule_refused(exposure, code):
    out = r.check_trade_coded("XAUUSD", 4280, 4288, 4256, exposure)
    assert out.code is code, out.message
    assert out.message and "drawdown_percent" in out.inputs


def test_a_poor_reward_risk_is_its_own_code():
    out = r.check_trade_coded("XAUUSD", 4280, 4288, 4284, r.Exposure({"XAUUSD": 0}))
    assert out.code is RejectCode.REWARD_RISK


def test_the_coded_check_and_the_original_never_disagree_about_ALLOWING_a_trade():
    """They must differ only in how much detail a refusal carries. If they ever disagree about whether
    a trade may be taken, one of them is deciding something the other is not."""
    cases = [
        ("XAUUSD", 4280, 4288, 4256, r.Exposure({"XAUUSD": 0})),
        ("XAUUSD", 4280, 4288, 4256, r.Exposure({"XAUUSD": 1})),
        ("XAUUSD", 4280, 4288, 4284, r.Exposure({"XAUUSD": 0})),
        ("XAUUSD", 4280, 4280, 4256, r.Exposure({"XAUUSD": 0})),
        ("BTCUSD", 84_000, 85_000, 82_000, r.Exposure({"BTCUSD": 0}, drawdown_percent=20.0)),
    ]
    for symbol, entry, stop, target, exposure in cases:
        plain = r.check_trade(symbol, entry, stop, target, exposure)
        coded = r.check_trade_coded(symbol, entry, stop, target, exposure)
        assert (plain is True) == (coded is True), (symbol, entry, stop, target)
        if plain is not True:
            assert coded.message == plain.reason, "the wording must be identical too"
