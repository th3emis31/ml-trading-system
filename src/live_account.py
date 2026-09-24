"""Whether real money may trade, for MT4 and MT5, and exactly why not when the answer is no.

Written 24 September 2026 on the owner's instruction: a live-account tab for both platforms, minimum
start 300 GBP, no maximum, "with minimum risk best higher quality".

The whole design follows from one fact: every guard in this system has only ever been tested against
demo fills. On 23 September a single threshold change produced ten losing trades in ninety minutes.
That cost nothing on demo. So this module's job is not to enable live trading - it is to refuse it
until a specific, checkable list of conditions is true, and to say which one failed.

It never handles a password. Each MetaTrader terminal already holds its own credentials; the system
only ever attaches to a terminal and inherits whichever account is logged into it. There is no field
for a password here and there must never be one.

Nothing here places an order. It answers a question, and the execution guard and the per-strategy
account checks still apply on top of every answer it gives.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

from . import active_account

# The owner's floor: below this a single normal loss is a large share of the account, and position
# sizing cannot round to anything sensible. There is deliberately no ceiling - they asked for none.
MIN_BALANCE_GBP = 300.0
# A live loss is permanent. These are stricter than the demo settings on purpose.
MAX_RISK_PERCENT = 1.0
MAX_DAILY_LOSS_PERCENT = 3.0
# Real money follows evidence, and this is the cheapest honest form of it: the system must have a
# positive settled record on demo before the same code is trusted with an account that can lose.
MIN_SETTLED_TRADES = 30


def _money(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _verdict(name: str, ok: bool, detail: str, blocking: bool = True) -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail, "blocking": bool(blocking)}


def account_checks(account: Optional[dict], platform: str) -> list[dict]:
    """The checks that depend on the broker account itself.

    An unreadable account is treated as a failure, never as a pass. "Could not read the balance" and
    "the balance is fine" must never produce the same verdict, because one of them is a guess.
    """
    if not isinstance(account, dict) or not account:
        return [_verdict(f"{platform} account readable", False,
                      f"{platform} is not connected, so its balance and mode cannot be read")]
    login = account.get("login") or account.get("account")
    balance = _money(account.get("balance"))
    currency = str(account.get("currency") or "").upper()
    # trade_mode 0 = demo for MT5; MT4 is judged on the server name, which is all the bridge reports.
    mode = account.get("trade_mode")
    server = str(account.get("server") or "")
    is_demo = (mode == 0) if mode is not None else ("demo" in server.lower())

    checks = [_verdict(f"{platform} account readable", True, f"account {login} on {server or 'unknown server'}")]
    checks.append(_verdict(
        f"{platform} is a LIVE account", not is_demo,
        f"account {login} is {'a demo' if is_demo else 'live'} ({server})."
        f"{' Live trading is not needed on a demo - the demo path already trades it.' if is_demo else ''}",
        blocking=False))
    if balance is None:
        checks.append(_verdict(f"{platform} balance", False, "the balance could not be read"))
    else:
        checks.append(_verdict(
            f"{platform} balance at least {MIN_BALANCE_GBP:.0f}", balance >= MIN_BALANCE_GBP,
            f"{balance:,.2f} {currency or ''}".strip() +
            (f" - below the {MIN_BALANCE_GBP:.0f} floor" if balance < MIN_BALANCE_GBP else "")))
        if currency and currency != "GBP":
            checks.append(_verdict(f"{platform} currency", True,
                                f"account is in {currency}; the {MIN_BALANCE_GBP:.0f} floor is compared "
                                f"against the raw balance, not converted", blocking=False))
    return checks


def evidence_checks(growth: Optional[dict]) -> list[dict]:
    """Has the system earned the right to trade real money, on its own settled record?

    Deliberately modest: a positive net over a minimum number of SETTLED trades. It is not the 0.95
    deflated Sharpe bar, which governs whether a researched strategy may be promoted at all; this is
    the separate question of whether the live path has a track record behind it.
    """
    if not isinstance(growth, dict) or not growth.get("available"):
        return [_verdict("Demo track record", False, "no closed trades recorded yet, so there is nothing to judge")]
    totals = growth.get("totals") or {}
    trades = int(totals.get("trades") or 0)
    net = _money(totals.get("net")) or 0.0
    factor = totals.get("profit_factor")
    return [
        _verdict("Enough settled trades", trades >= MIN_SETTLED_TRADES,
              f"{trades} closed trades (need {MIN_SETTLED_TRADES})"),
        _verdict("Demo record is positive", net > 0,
              f"net {net:+.2f} over {trades} trades"
              + (f", profit factor {factor}" if factor else "")),
    ]


def risk_checks(settings: Optional[dict]) -> list[dict]:
    """Caps that are stricter for live than for demo, because a live loss does not reset."""
    settings = settings if isinstance(settings, dict) else {}
    risk = _money(settings.get("risk_percent"))
    daily = _money(settings.get("daily_loss_limit_pct"))
    return [
        _verdict(f"Risk per trade at most {MAX_RISK_PERCENT}%",
              risk is not None and risk <= MAX_RISK_PERCENT,
              f"{risk}%" if risk is not None else "not set"),
        _verdict(f"Daily loss limit at most {MAX_DAILY_LOSS_PERCENT}%",
              daily is not None and 0 < daily <= MAX_DAILY_LOSS_PERCENT,
              f"{daily}%" if daily is not None else "not set"),
    ]


def arming_checks(selection: Optional[dict]) -> list[dict]:
    """The owner's own double confirmation, which active_account already enforces.

    Both must be present and must agree, so arming a live account cannot be a slip of a dropdown.
    """
    selection = selection if isinstance(selection, dict) else {}
    allow = bool(selection.get("allow_live"))
    confirm = selection.get("confirm_live")
    login = selection.get("login")
    agreed = allow and confirm is not None and str(confirm) == str(login)
    return [_verdict("Owner armed live trading", agreed,
                  "allow_live and a matching confirm_live are both set" if agreed
                  else "not armed: live needs allow_live AND confirm_live set to the same login")]


def live_readiness(mt5_account: Optional[dict] = None, mt4_account: Optional[dict] = None,
              growth: Optional[dict] = None, settings: Optional[dict] = None,
              selection: Optional[dict] = None, data_dir=None) -> dict:
    """One verdict per platform, plus every reason behind it.

    ``ready`` is true only when every BLOCKING check passes. Non-blocking checks are shown but do not
    decide, so the page can report something worth knowing without it becoming a veto.
    """
    if selection is None:
        try:
            selection = active_account.selected(data_dir)
        except Exception:
            selection = {}

    platforms = {}
    for platform, account in (("MT5", mt5_account), ("MT4", mt4_account)):
        checks = account_checks(account, platform) + arming_checks(selection) \
            + risk_checks(settings) + evidence_checks(growth)
        blocking_failures = [c for c in checks if c["blocking"] and not c["ok"]]
        platforms[platform] = {
            "ready": not blocking_failures,
            "blocked_by": [c["name"] for c in blocking_failures],
            "first_reason": blocking_failures[0]["detail"] if blocking_failures else None,
            "checks": checks,
        }

    return {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "platforms": platforms,
        "any_ready": any(p["ready"] for p in platforms.values()),
        "limits": {"min_balance_gbp": MIN_BALANCE_GBP, "max_balance": None,
                   "max_risk_percent": MAX_RISK_PERCENT,
                   "max_daily_loss_percent": MAX_DAILY_LOSS_PERCENT,
                   "min_settled_trades": MIN_SETTLED_TRADES,
                   "one_trade_per_asset": True},
        "places_orders": False,
        "note": ("This reports readiness only. It places no orders, holds no password, and the "
                 "execution guard and per-strategy account checks still apply on top of it."),
    }
