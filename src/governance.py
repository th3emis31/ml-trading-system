"""i40 Pilot layer 7 - what the system may do, and who has to say so. Build map step 6.

The owner's ask is a system that can use the PC fully when asked, build and deploy things, and trade.
That is a large surface, and the honest way to hold it is not a list of forbidden words but a tier
per action with a different bar for each:

    T0 read          read a file, fetch a page, query the account          nobody has to be asked
    T1 local write   write in the workspace, run a backtest, edit a draft  nobody, but it is logged
    T2 outward       send, post, publish, install                          explicit, per action
    T3 money/system  place an order, move funds, change settings, delete   explicit, WITH the figures

Three rules this module exists to enforce, all of them learned here rather than invented:

1. **Approval does not generalise.** One approved order is not approval for the next one, and
   yesterday's consent is not today's. ``execution_guard`` already refuses an approval whose price
   has drifted outside its band; this applies the same idea to every T2 and T3 action.
2. **A kill switch stops the system, not the trades.** The owner's standing rule is that positions
   close at their own stop, target or time exit - never by hand. Halting must therefore stop LOOPS
   and new actions while deliberately leaving open positions alone.
3. **New capabilities start in dry run.** Something that has never proved itself gets to produce its
   output and not act on it. ``demo_executor`` and the TradingView intake both already work this way.

Everything above this layer asks ``check_permission`` before acting, and the answer is a decision
with a reason, never a bare boolean - a refusal nobody can explain gets worked around.
"""
from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .runtime_paths import smartentry_data_dir

AUDIT_NAME = "governance_audit.jsonl"
HALT_NAME = "governance_halt.json"
DRY_RUN_NAME = "governance_dry_run.json"

T0_READ, T1_LOCAL, T2_OUTWARD, T3_CRITICAL = "T0", "T1", "T2", "T3"
TIER_ORDER = (T0_READ, T1_LOCAL, T2_OUTWARD, T3_CRITICAL)

TIER_MEANING = {
    T0_READ: "read only - nothing changes",
    T1_LOCAL: "changes something inside the workspace",
    T2_OUTWARD: "leaves this machine or changes what others see",
    T3_CRITICAL: "moves money, or changes the system or data irreversibly",
}

# Matched on the action name the caller declares. Deliberately blunt: when a name matches more than
# one tier the HIGHEST wins, because the cost of over-asking is a question and the cost of
# under-asking is an order nobody approved.
TIER_RULES = {
    T3_CRITICAL: ("order", "trade", "buy", "sell", "position", "withdraw", "deposit", "transfer",
                  "funds", "payment", "delete", "rm ", "drop", "truncate", "format", "registry",
                  "credential", "password", "secret", "key", "uninstall", "shutdown", "reboot",
                  "schedule", "task", "autostart", "firewall"),
    T2_OUTWARD: ("send", "email", "post", "publish", "deploy", "upload", "tweet", "message",
                 "install", "commit", "push", "pr ", "webhook", "notify", "share"),
    T1_LOCAL: ("write", "edit", "save", "create", "run", "backtest", "train", "build", "render",
               "download", "move", "copy"),
}


@dataclass
class Decision:
    allowed: bool
    tier: str
    reason: str
    needs: str = ""
    audit_id: Optional[str] = None

    def as_dict(self) -> dict:
        return {"allowed": self.allowed, "tier": self.tier, "reason": self.reason,
                "needs": self.needs, "audit_id": self.audit_id}


def _path(name: str, base: Optional[Path] = None) -> Path:
    return (Path(base) if base else smartentry_data_dir()) / name


def tier_for(action: str, declared: Optional[str] = None) -> str:
    """The tier an action falls in. ``declared`` raises it but can never lower it.

    A caller may say "this is T3" about something that looks harmless and be believed. A caller
    saying "this is only T0" about something containing the word `delete` is not - self-declaration
    downward is exactly how a guard gets walked around.
    """
    text = f" {(action or '').lower()} "
    found = T0_READ
    for tier in (T1_LOCAL, T2_OUTWARD, T3_CRITICAL):
        if any(word in text for word in TIER_RULES[tier]):
            found = tier
    if declared in TIER_ORDER and TIER_ORDER.index(declared) > TIER_ORDER.index(found):
        return declared
    return found


def halt_state(base: Optional[Path] = None) -> dict:
    try:
        return json.loads(_path(HALT_NAME, base).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"halted": False}


def halt(reason: str, *, by: str = "owner", base: Optional[Path] = None) -> dict:
    """Stop the system taking new actions. Open positions are deliberately untouched.

    The owner's standing rule is that a trade closes at its own stop, target or time exit and never
    by hand. A kill switch that flattened the book would be obeying a panic and breaking a rule, so
    this halts LOOPS and new actions only - what is already open stays open and exits on its terms.
    """
    state = {"halted": True, "reason": reason, "by": by,
             "at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
             "note": "New actions are refused. Open positions are NOT closed - they exit on their "
                     "own stop, target or time exit, as the owner requires."}
    target = _path(HALT_NAME, base)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state, indent=1), encoding="utf-8")
    record_action("governance.halt", tier=T3_CRITICAL, allowed=True, reason=reason, base=base)
    return state


def resume(by: str = "owner", base: Optional[Path] = None) -> dict:
    state = {"halted": False, "by": by,
             "at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}
    _path(HALT_NAME, base).write_text(json.dumps(state, indent=1), encoding="utf-8")
    record_action("governance.resume", tier=T3_CRITICAL, allowed=True, reason="owner resumed", base=base)
    return state


def dry_run_state(base: Optional[Path] = None) -> dict:
    """Capabilities still in dry run. Absent means dry run is ON - unproven defaults to not acting."""
    try:
        return json.loads(_path(DRY_RUN_NAME, base).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def in_dry_run(capability: str, base: Optional[Path] = None) -> bool:
    state = dry_run_state(base)
    if capability in state:
        return bool(state[capability])
    return True                    # unknown capability: dry run until it has proved itself


def record_action(action: str, *, tier: str, allowed: bool, reason: str = "",
                  approved_by: str = "", detail: Optional[dict] = None,
                  base: Optional[Path] = None) -> str:
    """Append one line to the audit. Every T1 and above, allowed or refused.

    Refusals are recorded as carefully as actions: a trade that does not happen has to be as visible
    as one that does, or the next person sees an idle system and no reason for it.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    audit_id = f"{stamp.replace(' ', 'T')}-{os.getpid()}"
    row = {"id": audit_id, "at": stamp, "action": str(action)[:200], "tier": tier,
           "allowed": bool(allowed), "reason": str(reason)[:300],
           "approved_by": approved_by, "detail": detail or {},
           "host": socket.gethostname(), "pid": os.getpid()}
    target = _path(AUDIT_NAME, base)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, default=str) + "\n")
    return audit_id


def check_permission(action: str, *, declared_tier: Optional[str] = None,
                     approval: Optional[dict] = None, capability: str = "",
                     figures: Optional[dict] = None, base: Optional[Path] = None) -> Decision:
    """May this action proceed? A decision with a reason, never a bare boolean.

    ``approval`` is what the owner said, and it must name THIS action: ``{"action": ..., "by": ...,
    "figures": {...}}``. An approval for a different action, or one carrying different figures, is
    refused - approval does not generalise, and "they said yes earlier" is how an unreviewed order
    gets placed.
    """
    tier = tier_for(action, declared_tier)

    if tier != T0_READ:
        state = halt_state(base)
        if state.get("halted"):
            reason = f"the system is halted: {state.get('reason') or 'no reason recorded'}"
            return Decision(False, tier, reason,
                            needs="resume the system before anything can act",
                            audit_id=record_action(action, tier=tier, allowed=False, reason=reason,
                                                   base=base))

    if tier == T0_READ:
        return Decision(True, tier, "read-only: nothing changes")

    # Dry run is checked BEFORE approval and at every tier above read. An unproven capability that
    # happened to carry an owner's approval would otherwise act on its very first outing, which is
    # the opposite of what dry run is for - and the higher the tier, the more that matters.
    if capability and in_dry_run(capability, base):
        reason = (f"'{capability}' is still in dry run, so it produces output but does not act. "
                  "A capability proves itself before it is allowed to change anything.")
        return Decision(False, tier, reason, needs=f"take '{capability}' out of dry run",
                        audit_id=record_action(action, tier=tier, allowed=False, reason=reason,
                                               base=base))

    if tier == T1_LOCAL:
        return Decision(True, tier, "local change, logged",
                        audit_id=record_action(action, tier=tier, allowed=True,
                                               reason="local change", base=base))

    # T2 and T3 both need the owner. T3 additionally needs the figures to match.
    approval = approval or {}
    named = str(approval.get("action") or "")
    if not named:
        reason = f"{TIER_MEANING[tier]} - this needs the owner's explicit approval, and none was given"
        return Decision(False, tier, reason, needs="explicit approval naming this exact action",
                        audit_id=record_action(action, tier=tier, allowed=False, reason=reason,
                                               base=base))
    if named.strip().lower() != str(action).strip().lower():
        reason = (f"the approval names {named!r} but this action is {action!r}. Approval does not "
                  "carry from one action to another.")
        return Decision(False, tier, reason, needs="approval naming this action",
                        audit_id=record_action(action, tier=tier, allowed=False, reason=reason,
                                               base=base))

    if tier == T3_CRITICAL:
        approved_figures = approval.get("figures") or {}
        if not figures:
            reason = ("a T3 action must state its figures so the owner approves the actual numbers, "
                      "not the idea of the action")
            return Decision(False, tier, reason, needs="the exact figures for this action",
                            audit_id=record_action(action, tier=tier, allowed=False, reason=reason,
                                                   base=base))
        drifted = {k: (approved_figures.get(k), v) for k, v in figures.items()
                   if str(approved_figures.get(k)) != str(v)}
        if drifted:
            reason = ("the figures changed since approval: "
                      + "; ".join(f"{k} approved {a!r}, now {b!r}" for k, (a, b) in list(drifted.items())[:4]))
            return Decision(False, tier, reason, needs="fresh approval for the current figures",
                            audit_id=record_action(action, tier=tier, allowed=False, reason=reason,
                                                   detail={"drifted": list(drifted)}, base=base))

    return Decision(True, tier, f"{TIER_MEANING[tier]}; approved by {approval.get('by') or 'owner'}",
                    audit_id=record_action(action, tier=tier, allowed=True,
                                           reason="approved", approved_by=str(approval.get("by") or "owner"),
                                           detail={"figures": figures or {}}, base=base))


def read_audit(limit: int = 500, base: Optional[Path] = None) -> list:
    target = _path(AUDIT_NAME, base)
    if not target.exists():
        return []
    rows = []
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def audit_summary(limit: int = 500, base: Optional[Path] = None) -> dict:
    rows = read_audit(limit, base)
    by_tier: dict = {}
    for row in rows:
        entry = by_tier.setdefault(row.get("tier") or "?", {"allowed": 0, "refused": 0})
        entry["allowed" if row.get("allowed") else "refused"] += 1
    halted = halt_state(base)
    return {"actions": len(rows), "by_tier": by_tier,
            "halted": bool(halted.get("halted")), "halt_reason": halted.get("reason"),
            "dry_run": dry_run_state(base),
            "recent_refusals": [r for r in rows if not r.get("allowed")][-5:],
            "rule": ("Approval names one action and does not carry to the next. A halt stops new "
                     "actions and leaves open positions to exit on their own terms.")}


if __name__ == "__main__":       # pragma: no cover - a hand check, not a test
    print(json.dumps(audit_summary(), indent=1))
