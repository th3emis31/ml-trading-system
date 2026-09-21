"""Which broker account the system is allowed to trade, chosen by the owner instead of edited in code.

Written 21 September 2026 on the owner's instruction: "Why is so complicated can make on auto trade
to login to any demo account or live account foe mt4/5 to login from the system will be mess every
time when I want to change the account."

They were right. The account was fixed in three places at once - ``DEMO_ACCOUNT_LOGIN`` in
``src/demo_session_pullback.py``, ``MT4_ACCOUNT`` in the environment and ``MT5_PATH`` in
``start_trading.bat`` - so changing it meant editing source, a variable and a launcher, then
restarting. This file makes it one selection the owner controls.

WHAT THIS DOES NOT DO, and will not be extended to do:

* **It never handles a password.** Selecting an account here does not log anything in. Each
  MetaTrader terminal already stores its own credentials, so the system only ever chooses WHICH
  TERMINAL to attach to and inherits whichever account is logged into it. There is no field for a
  password and there must never be one.
* **It does not weaken the guard.** ``demo_session_pullback.check_demo_account`` still refuses any
  account that is not the expected one; the only change is that "expected" now comes from the
  owner's selection rather than a constant. A wrong selection still halts the strategy rather than
  trading the wrong account, which is exactly what happened - correctly - on 21 September when the
  connection drifted to account 25446287.
* **It does not make live trading one click away.** A live account is refused unless the selection
  carries BOTH ``allow_live`` and a matching ``confirm_live`` login, so arming one cannot be a
  slip of a dropdown. Demo remains the default and the fallback.

The default is the account the system has always traded, so behaviour is unchanged until the owner
selects something else.

    python -m src.active_account show
    python -m src.active_account select --login 11581419 --terminal "<path to terminal64.exe>"
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .runtime_paths import smartentry_data_dir
# The project's one tolerant JSON reader (UTF-8, BOM, or the ANSI MetaTrader writes). src/ea_monitor.py
# and src/forward_evidence.py import the same one rather than each adding another.
from .system_doctor import _read_json as _read_json_or_none

# The account and terminal the system has traded since 16 September. Used when nothing is selected,
# so an absent or unreadable config file changes nothing rather than opening anything up.
DEFAULT_LOGIN = 11581419
DEFAULT_TERMINAL = r"C:\Users\th_em\AppData\Roaming\MetaTrader\terminal64.exe"
DEFAULT_SERVER = "VantageMarkets-Demo"
# MT4 is a separate connection with its own account: the bridge expert serves whichever account its
# terminal is logged into. Kept in this same file so one place answers "which accounts am I using",
# rather than MT5 living here and MT4 living in an environment variable nobody remembers setting.
DEFAULT_MT4_LOGIN = 12755139
DEFAULT_LABEL = "Vantage demo 11581419 (the system's own account)"


def _as_int(value, fallback: int) -> int:
    try:
        return int(value) if value not in (None, "") else fallback
    except (TypeError, ValueError):
        return fallback


def config_path(data_dir: Optional[Path] = None) -> Path:
    return Path(data_dir or smartentry_data_dir()) / "active_account.json"


def selected(data_dir: Optional[Path] = None) -> dict:
    """The owner's selection, with the long-standing default filled in for anything missing."""
    raw = _read_json_or_none(config_path(data_dir)) or {}
    login = raw.get("login")
    try:
        login = int(login) if login not in (None, "") else DEFAULT_LOGIN
    except (TypeError, ValueError):
        login = DEFAULT_LOGIN
    return {
        "login": login,
        "terminal_path": str(raw.get("terminal_path") or DEFAULT_TERMINAL),
        "server": str(raw.get("server") or DEFAULT_SERVER),
        "label": str(raw.get("label") or (DEFAULT_LABEL if login == DEFAULT_LOGIN else f"account {login}")),
        "mt4_login": _as_int(raw.get("mt4_login"), DEFAULT_MT4_LOGIN),
        "allow_live": bool(raw.get("allow_live")),
        "platform": str(raw.get("platform") or "mt5").lower(),
        "updated_at": raw.get("updated_at"),
        "is_default": not raw,
    }


def expected_login(data_dir: Optional[Path] = None) -> int:
    """The login every strategy guard compares the logged-in account against."""
    return int(selected(data_dir)["login"])


def mt4_expected_login(data_dir: Optional[Path] = None) -> int:
    """The account the MT4 bridge must report. MT4_ACCOUNT in the environment still wins if set."""
    return _as_int(os.environ.get("MT4_ACCOUNT"), int(selected(data_dir)["mt4_login"]))


def terminal_path(data_dir: Optional[Path] = None) -> str:
    """The MetaTrader terminal to attach to. MT5_PATH in the environment still wins if set."""
    return os.environ.get("MT5_PATH", "").strip() or selected(data_dir)["terminal_path"]


def select(login: int, terminal: Optional[str] = None, server: Optional[str] = None,
           label: Optional[str] = None, allow_live: bool = False,
           confirm_live: Optional[int] = None, platform: str = "mt5",
           mt4_login: Optional[int] = None, data_dir: Optional[Path] = None) -> dict:
    """Record the owner's choice. Returns {"ok": bool, "reason": str, "selection": {...}}.

    ``allow_live`` alone is not enough to arm a live account: ``confirm_live`` must repeat the same
    login. Two separate acts, so a live account cannot be selected by mistake.
    """
    try:
        login = int(login)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "login must be a number", "selection": selected(data_dir)}
    if login <= 0:
        return {"ok": False, "reason": "login must be a positive account number", "selection": selected(data_dir)}
    if allow_live and int(confirm_live or 0) != login:
        return {"ok": False,
                "reason": f"a live account needs confirm_live to repeat the login ({login}); "
                          f"got {confirm_live!r}. Demo accounts do not need it.",
                "selection": selected(data_dir)}
    if terminal and not Path(terminal).exists():
        return {"ok": False, "reason": f"no terminal executable at {terminal}", "selection": selected(data_dir)}

    current = selected(data_dir)
    record = {
        "login": login,
        "terminal_path": str(terminal or current["terminal_path"]),
        "server": str(server or ("" if login != current["login"] else current["server"])),
        "label": str(label or f"account {login}"),
        "mt4_login": _as_int(mt4_login, current["mt4_login"]),
        "allow_live": bool(allow_live),
        "platform": str(platform or "mt5").lower(),
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "note": ("The strategy guards compare the logged-in account against this login and refuse "
                 "anything else. No password is stored here or anywhere else in the system: the "
                 "terminal holds its own login, and selecting an account only chooses which "
                 "terminal to attach to."),
    }
    path = config_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=1), encoding="utf-8")
    return {"ok": True, "reason": f"active account set to {login}", "selection": selected(data_dir)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Choose the account the system may trade (no passwords, ever)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show")
    pick = sub.add_parser("select")
    pick.add_argument("--login", type=int, required=True)
    pick.add_argument("--terminal", default=None)
    pick.add_argument("--server", default=None)
    pick.add_argument("--label", default=None)
    pick.add_argument("--platform", default="mt5")
    pick.add_argument("--mt4-login", type=int, default=None, help="the account the MT4 bridge must report")
    pick.add_argument("--allow-live", action="store_true", help="a live account also needs --confirm-live")
    pick.add_argument("--confirm-live", type=int, default=None)
    args = parser.parse_args(argv)

    if args.command == "show":
        print(json.dumps(selected(), indent=1))
        return 0
    result = select(args.login, terminal=args.terminal, server=args.server, label=args.label,
                    allow_live=args.allow_live, confirm_live=args.confirm_live, platform=args.platform,
                    mt4_login=args.mt4_login)
    print(json.dumps(result, indent=1))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
