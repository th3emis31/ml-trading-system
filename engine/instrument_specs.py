"""Instrument contract specifications: measured, validated, and never guessed.

WHY THIS IS A SEPARATE LAYER
----------------------------
A contract size is not a parameter, it is a fact about the instrument. Get it wrong and every position
is sized by the same wrong factor, which is invisible because the arithmetic still looks tidy. This
project has already paid for that class of error once: break-even distances in raw points sized for
gold were applied to BTCUSD where point is 0.01, which trailed a dollar-eighty behind price and stopped
four trades out within a second of the modify.

So the specifications live in `config/instrument_specs.json`, every row stamped with the account it was
measured on and the time it was read, and this module's job is to load them, validate them, and
**refuse** rather than substitute when one is missing or incoherent.

THE RULE THAT MATTERS MOST
-------------------------
An unknown instrument is a REFUSAL, not a default. `instrument_for("SOMETHING")` raises rather than returning
a plausible row, because a default contract size is the most dangerous number in a risk engine: it
sizes every trade confidently and wrongly.

TICK VALUE ALREADY CARRIES THE CURRENCY CONVERSION
-------------------------------------------------
The account settles in GBP; both instruments profit in USD. The broker's `trade_tick_value` is reported
**in the account currency**, so it has the conversion baked in — XAUUSD's tick_size 0.01 x contract_size
100 is 1.00 USD per tick per lot, reported as 0.7554981 GBP, and BTCUSD's 0.01 USD is reported as
0.0075550 GBP. The same implied rate on two instruments with different contract sizes is what makes
those readings consistent rather than coincidental.

That is the project's existing conversion mechanism and this module uses it rather than inventing one.
The consequence is that `tick_value` is only as current as the rate it embeds, which is why
`measured_at` is part of every row and `staleness_days` is reported rather than hidden.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "instrument_specs.json"


class SpecError(Exception):
    """A specification is missing, incomplete or incoherent.

    Deliberately an exception rather than a None: a caller that forgets to check a None sizes the trade
    anyway, and this is the one place where failing loudly is cheaper than failing safe.
    """

    def __init__(self, message: str, *, code: str, inputs: Optional[dict] = None):
        super().__init__(message)
        self.message, self.code, self.inputs = message, code, dict(inputs or {})


@dataclass(frozen=True)
class InstrumentSpec:
    """One instrument's contract facts, as read from the broker.

    Frozen because a spec is a measurement. Code that wants different numbers is testing a different
    instrument and should say so by constructing a different spec.
    """

    symbol: str
    tick_size: float
    contract_size: float
    min_lot: float
    max_lot: float
    lot_step: float
    currency: str                      # the instrument's PROFIT currency (USD for both of ours)
    account_currency: str              # what the account settles in (GBP here)
    tick_value: Optional[float] = None         # per lot, ALREADY in account_currency, when the broker gives it
    tick_value_currency: Optional[str] = None
    digits: Optional[int] = None
    measured_at: Optional[str] = None
    source: str = ""

    @property
    def needs_conversion(self) -> bool:
        """True when money computed from tick_size x contract_size is NOT in the account currency.

        False whenever a usable `tick_value` exists, because the broker already converted it.
        """
        if self.tick_value is not None and self.tick_value_currency == self.account_currency:
            return False
        return self.currency != self.account_currency

    def value_per_tick_per_lot(self, conversion_rate: Optional[float] = None) -> float:
        """What one tick of movement on one lot is worth, in the ACCOUNT currency.

        Two routes, and the first is preferred because it is measured rather than derived:

        1. The broker's `tick_value`, already in the account currency. No rate is needed or used.
        2. `tick_size * contract_size`, which is money in the instrument's PROFIT currency, multiplied
           by an explicitly supplied conversion rate. A missing rate is a refusal, never a 1.0.
        """
        if self.tick_value is not None and self.tick_value_currency == self.account_currency:
            return float(self.tick_value)

        in_profit_currency = float(self.tick_size) * float(self.contract_size)
        if self.currency == self.account_currency:
            return in_profit_currency

        if conversion_rate is None:
            raise SpecError(
                f"{self.symbol} profits in {self.currency} but the account is in "
                f"{self.account_currency}, and no tick_value in {self.account_currency} or conversion "
                f"rate was supplied. A rate is never assumed to be 1.0.",
                code="REJECT_CURRENCY_CONVERSION",
                inputs={"symbol": self.symbol, "currency": self.currency,
                        "account_currency": self.account_currency,
                        "tick_value": self.tick_value,
                        "tick_value_currency": self.tick_value_currency,
                        "conversion_rate": None})
        if not (conversion_rate > 0) or conversion_rate != conversion_rate:      # NaN-safe
            raise SpecError(
                f"conversion rate {conversion_rate!r} for {self.currency}->{self.account_currency} is "
                "not a positive finite number",
                code="REJECT_CURRENCY_CONVERSION",
                inputs={"symbol": self.symbol, "conversion_rate": conversion_rate})
        return in_profit_currency * float(conversion_rate)

    def staleness_days(self, now: Optional[datetime] = None) -> Optional[float]:
        """How old the measurement is, in days. None when the row carries no timestamp.

        Reported rather than enforced: how stale is too stale depends on what the caller is doing, and
        a hard limit here would refuse a backtest for a reason that only applies to live sizing.
        """
        if not self.measured_at:
            return None
        try:
            when = datetime.fromisoformat(str(self.measured_at).replace("Z", "+00:00"))
        except ValueError:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return round(((now or datetime.now(timezone.utc)) - when).total_seconds() / 86400.0, 3)

    def as_dict(self) -> dict:
        return {"symbol": self.symbol, "tick_size": self.tick_size,
                "tick_value": self.tick_value, "tick_value_currency": self.tick_value_currency,
                "contract_size": self.contract_size, "min_lot": self.min_lot,
                "max_lot": self.max_lot, "lot_step": self.lot_step, "digits": self.digits,
                "currency": self.currency, "account_currency": self.account_currency,
                "needs_conversion": self.needs_conversion,
                "measured_at": self.measured_at, "source": self.source}


_REQUIRED_POSITIVE = ("tick_size", "contract_size", "min_lot", "max_lot", "lot_step")


def validate(spec: InstrumentSpec) -> InstrumentSpec:
    """Refuse an incoherent specification rather than sizing a trade from it.

    These are not defensive-programming checks for their own sake. Each one is a value that would
    produce a confident, wrong position size: a zero tick_size divides by nothing, a min_lot above
    max_lot makes every size unfillable, and a lot_step larger than min_lot means the smallest
    tradable position cannot be expressed on the step grid.
    """
    for name in _REQUIRED_POSITIVE:
        value = getattr(spec, name)
        if value is None or not value > 0 or value != value:
            raise SpecError(f"{spec.symbol}: {name} is {value!r}, which cannot be used to size a trade",
                            code="REJECT_INVALID_SPEC",
                            inputs={"symbol": spec.symbol, name: value})
    if spec.min_lot > spec.max_lot:
        raise SpecError(f"{spec.symbol}: min_lot {spec.min_lot} is above max_lot {spec.max_lot}",
                        code="REJECT_INVALID_SPEC",
                        inputs={"symbol": spec.symbol, "min_lot": spec.min_lot,
                                "max_lot": spec.max_lot})
    if spec.lot_step > spec.min_lot:
        raise SpecError(f"{spec.symbol}: lot_step {spec.lot_step} is larger than min_lot "
                        f"{spec.min_lot}, so the smallest tradable size is not on the step grid",
                        code="REJECT_INVALID_SPEC",
                        inputs={"symbol": spec.symbol, "lot_step": spec.lot_step,
                                "min_lot": spec.min_lot})
    if spec.tick_value is not None and not spec.tick_value > 0:
        raise SpecError(f"{spec.symbol}: tick_value is {spec.tick_value!r}",
                        code="REJECT_INVALID_SPEC",
                        inputs={"symbol": spec.symbol, "tick_value": spec.tick_value})
    if not spec.currency or not spec.account_currency:
        raise SpecError(f"{spec.symbol}: currency or account_currency is missing, so no money figure "
                        "from this spec can be trusted",
                        code="REJECT_INVALID_SPEC",
                        inputs={"symbol": spec.symbol, "currency": spec.currency,
                                "account_currency": spec.account_currency})
    return spec


def load_specs(path: Optional[Path] = None) -> Dict[str, InstrumentSpec]:
    """Every validated specification in the config file, keyed by upper-case symbol."""
    path = Path(path) if path else CONFIG_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SpecError(f"instrument specifications could not be read from {path}: {exc}",
                        code="REJECT_INVALID_SPEC", inputs={"path": str(path)}) from exc
    except ValueError as exc:
        raise SpecError(f"instrument specifications at {path} are not valid JSON: {exc}",
                        code="REJECT_INVALID_SPEC", inputs={"path": str(path)}) from exc

    account_currency = ((payload.get("account") or {}).get("account_currency") or "").upper()
    if not account_currency:
        raise SpecError(f"{path} names no account_currency, so no money figure can be trusted",
                        code="REJECT_INVALID_SPEC", inputs={"path": str(path)})

    out: Dict[str, InstrumentSpec] = {}
    for symbol, row in (payload.get("instruments") or {}).items():
        spec = InstrumentSpec(
            symbol=str(row.get("symbol") or symbol).upper(),
            tick_size=row.get("tick_size"), contract_size=row.get("contract_size"),
            min_lot=row.get("min_lot"), max_lot=row.get("max_lot"), lot_step=row.get("lot_step"),
            currency=str(row.get("currency") or "").upper(), account_currency=account_currency,
            tick_value=row.get("tick_value"),
            tick_value_currency=(str(row["tick_value_currency"]).upper()
                                 if row.get("tick_value_currency") else None),
            digits=row.get("digits"), measured_at=row.get("measured_at"),
            source=str(row.get("source") or ""))
        out[spec.symbol] = validate(spec)
    return out


def instrument_for(symbol: str, path: Optional[Path] = None,
             specs: Optional[Dict[str, InstrumentSpec]] = None) -> InstrumentSpec:
    """The specification for one symbol, or a refusal naming what is known instead.

    Named `instrument_for`, not `spec_for`: `carry_short.spec_for` already exists and builds a STRATEGY
    spec. Two functions called spec_for, one returning contract facts and one returning strategy
    parameters, is how a caller ends up passing the wrong dict into a sizing calculation.

    An unknown symbol RAISES. A default contract size is the most dangerous number a risk engine can
    hold, because it sizes every trade confidently and wrongly, and the error surfaces as a loss rather
    than as an exception.
    """
    table = specs if specs is not None else load_specs(path)
    key = str(symbol or "").upper()
    if key not in table:
        raise SpecError(
            f"no instrument specification for {symbol!r}. Measure it from the broker and add it to "
            f"{(path or CONFIG_PATH).name}; nothing here is assumed.",
            code="REJECT_INVALID_INSTRUMENT",
            inputs={"symbol": symbol, "known": sorted(table)})
    return table[key]


def refresh_from_broker(symbols=("XAUUSD", "BTCUSD"), path: Optional[Path] = None) -> dict:
    """Re-measure the specifications from MetaTrader5 and rewrite the config file.

    Kept out of the sizing path on purpose: a deterministic risk calculation must never reach for the
    network or the terminal mid-decision, or the same inputs stop producing the same answer. This is an
    explicit maintenance command — `python -m engine.instrument_specs --refresh`.
    """
    import MetaTrader5 as mt5                                  # noqa: PLC0415 - optional dependency

    path = Path(path) if path else CONFIG_PATH
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"instruments": {}}
    if not mt5.initialize():
        raise SpecError(f"MetaTrader5 did not initialise: {mt5.last_error()}",
                        code="REJECT_INVALID_SPEC", inputs={"symbols": list(symbols)})
    try:
        account = mt5.account_info()
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        payload.setdefault("account", {})
        payload["account"].update({"login": getattr(account, "login", None),
                                   "server": getattr(account, "server", None),
                                   "account_currency": getattr(account, "currency", None)})
        changed = {}
        for symbol in symbols:
            info = mt5.symbol_info(symbol)
            if info is None:
                changed[symbol] = "no symbol_info; left as it was"
                continue
            row = payload.setdefault("instruments", {}).setdefault(symbol, {})
            row.update({
                "symbol": symbol,
                "tick_size": float(info.trade_tick_size), "tick_value": float(info.trade_tick_value),
                "tick_value_currency": getattr(account, "currency", None),
                "contract_size": float(info.trade_contract_size),
                "min_lot": float(info.volume_min), "max_lot": float(info.volume_max),
                "lot_step": float(info.volume_step), "digits": int(info.digits),
                "point": float(info.point), "currency": str(info.currency_profit),
                "currency_base": str(info.currency_base),
                "measured_at": stamp,
                "source": f"MetaTrader5 symbol_info, {getattr(account, 'server', '?')} "
                          f"{getattr(account, 'login', '?')}",
            })
            changed[symbol] = "re-measured"
    finally:
        mt5.shutdown()

    path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    load_specs(path)                                   # validate what was just written, or raise
    return {"path": str(path), "account_currency": payload["account"].get("account_currency"),
            "symbols": changed}


if __name__ == "__main__":       # pragma: no cover - a maintenance command, not a test
    import sys

    if "--refresh" in sys.argv:
        print(json.dumps(refresh_from_broker(), indent=1))
    else:
        for name, spec in sorted(load_specs().items()):
            print(f"{name}: {json.dumps(spec.as_dict(), default=str)}")
            print(f"    measured {spec.staleness_days()} day(s) ago; "
                  f"needs_conversion={spec.needs_conversion}")
