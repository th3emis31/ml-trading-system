"""TradingView webhook intake: checks an alert before it goes near the trading pipeline.

Pure functions used by ``POST /webhook/tradingview`` in app.py (the secret check is shared with the older
``POST /api/tradingview``):

* ``tradingview_secret_matches`` - constant-time shared-secret check; the secret keys are stripped from the payload.
* ``resolve_dry_run`` - an alert is a dry run unless it sends ``"dry_run": false`` AND the webhook config
  (data/tradingview_webhook.json) has ``"allow_approval_queue": true``. That setting is off by default.
* ``evaluate_tradingview_alert`` - smart-entry checks on the parsed plan: tradable side, allowed symbol,
  entry/stop/target present and on the right sides, reward:risk, agreement with the system's own model,
  confidence, alert age and test alerts.
* ``record_intake_decision`` / ``load_intake_journal`` - a bounded JSON journal of every decision.

Nothing here places, queues, modifies or closes orders. Even a non-dry-run alert only reaches the
auto-trader's approval queue, where the owner still has to approve it.
"""
from __future__ import annotations

import hmac
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .ea_monitor import _write_json_atomic
from .system_doctor import _read_json

SECRET_KEYS = ("secret", "passphrase", "token")
TRADABLE_SIDES = ("BUY", "SELL")
DEFAULT_SYMBOLS = ("XAUUSD", "BTCUSD")
MIN_REWARD_RISK = 1.5
MIN_CONFIDENCE = 0.5
MAX_ALERT_AGE_SECONDS = 15 * 60
JOURNAL_KEEP = 500
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_TRUE_WORDS = {"1", "true", "yes", "on"}
_FALSE_WORDS = {"0", "false", "no", "off"}
_JOURNAL_LOCK = threading.Lock()


def tradingview_secret_matches(payload: dict, header_secret: Optional[str], expected: str,
                               keys: Iterable[str] = SECRET_KEYS) -> tuple[bool, dict]:
    """(secret correct?, payload without any secret key). The body's secret wins over the X-Webhook-Secret header."""
    keys = tuple(keys)
    supplied = str(next((payload.get(key) for key in keys if payload.get(key)), "") or header_secret or "")
    cleaned = {key: value for key, value in payload.items() if key not in keys}
    matched = bool(expected) and hmac.compare_digest(supplied.encode("utf-8"), str(expected).encode("utf-8"))
    return matched, cleaned


def _flag(value) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower() if value is not None else ""
    if text in _TRUE_WORDS:
        return True
    if text in _FALSE_WORDS:
        return False
    return None


def resolve_dry_run(payload: dict, config: Optional[dict]) -> tuple[bool, str]:
    """Dry run unless the alert explicitly asks for live intake and the owner enabled it in the webhook config."""
    requested = _flag((payload or {}).get("dry_run"))
    if requested is not False:
        return True, "dry run (the default)"
    if _flag((config or {}).get("allow_approval_queue")) is not True:
        return True, "dry run forced: allow_approval_queue is off in data/tradingview_webhook.json"
    return False, "live intake: an accepted alert goes to the approval queue and still needs the owner's approval"


def _number_or_none(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None  # NaN is not a number here


def _alert_time(value) -> Optional[datetime]:
    """TradingView sends {{timenow}} as ISO 8601; epoch seconds or milliseconds are accepted too."""
    if value in (None, ""):
        return None
    number = _number_or_none(value)
    if number is not None:
        try:
            return datetime.fromtimestamp(number / 1000.0 if number > 1e11 else number, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def evaluate_tradingview_alert(plan: dict, allowed_symbols: Optional[Iterable[str]] = None, payload: Optional[dict] = None,
                               now: Optional[datetime] = None, min_reward_risk: float = MIN_REWARD_RISK) -> dict:
    """Smart-entry checks on a plan from build_tradingview_plan(); ``accepted`` only when every check passes."""
    now = now or datetime.now(timezone.utc)
    payload = payload or {}
    allowed = {str(symbol).upper() for symbol in (allowed_symbols or DEFAULT_SYMBOLS)}
    side = str(plan.get("side") or "").upper()
    symbol = str(plan.get("symbol") or "").upper()
    entry, stop = _number_or_none(plan.get("entry")), _number_or_none(plan.get("stop_loss"))
    target = next((value for value in (_number_or_none(plan.get(key)) for key in
                                       ("take_profit_1", "take_profit", "take_profit_2", "take_profit_3")) if value is not None), None)
    reward_risk = _number_or_none(plan.get("risk_reward"))
    confidence = _number_or_none(plan.get("confidence"))
    checks: dict = {}
    reasons: list = []

    def check(name: str, passed: bool, reason: str) -> None:
        checks[name] = bool(passed)
        if not passed:
            reasons.append(reason)

    check("side", side in TRADABLE_SIDES,
          f"no tradable side ({side or 'missing'}): HOLD or unreadable alerts never enter the pipeline")
    check("symbol", symbol in allowed, f"symbol {symbol or 'missing'} is not allowed ({', '.join(sorted(allowed))})")
    check("levels", entry is not None and entry > 0 and stop is not None and target is not None,
          "entry, stop loss and a take profit are all required")
    in_order = False
    if side in TRADABLE_SIDES and None not in (entry, stop, target):
        in_order = stop < entry < target if side == "BUY" else target < entry < stop
    check("level_order", in_order, "stop loss and take profit must be on opposite sides of the entry, in the trade's direction")
    check("reward_risk", reward_risk is not None and reward_risk >= min_reward_risk,
          f"reward:risk {reward_risk} is below {min_reward_risk}")
    check("model_alignment", plan.get("alignment") != "Divergent",
          f"the system's own model points the other way ({plan.get('notes') or 'divergent'})")
    check("confidence", confidence is not None and confidence >= MIN_CONFIDENCE, f"confidence {confidence} is below {MIN_CONFIDENCE}")
    sent_at = _alert_time(payload.get("time") or payload.get("timenow") or payload.get("alert_time"))
    age = (now - sent_at).total_seconds() if sent_at else None
    check("fresh", age is None or age <= MAX_ALERT_AGE_SECONDS,
          f"alert is {int(age or 0)} s old (limit {MAX_ALERT_AGE_SECONDS} s)")
    check("not_test", str(plan.get("source") or "") != "dashboard-test", "test alerts are recorded but never enter the pipeline")
    return {"accepted": not reasons, "checks": checks, "reasons": reasons, "reward_risk": reward_risk,
            "alert_age_seconds": round(age, 1) if age is not None else None, "evaluated_at": now.strftime(TIME_FORMAT)}


def record_intake_decision(path, record: dict, keep: int = JOURNAL_KEEP, now: Optional[datetime] = None) -> dict:
    """Append one decision to the journal (newest last, at most ``keep`` rows)."""
    entry = dict(record, recorded_at=(now or datetime.now(timezone.utc)).strftime(TIME_FORMAT))
    with _JOURNAL_LOCK:
        journal = _read_json(Path(path))
        journal = journal if isinstance(journal, list) else []
        journal.append(entry)
        _write_json_atomic(Path(path), journal[-keep:])
    return entry


def load_intake_journal(path, limit: int = 50) -> list:
    """Most recent decisions first."""
    journal = _read_json(Path(path))
    return list(reversed(journal[-max(1, int(limit)):])) if isinstance(journal, list) else []
