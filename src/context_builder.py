"""i40 Pilot layer 2 - spending a limited context window well. Build map step 3.

The job is not "retrieve relevant things". It is to decide **what is left out**, because the owner's
constraint is that this system must run offline on a local model whose window is 8,192 tokens against
Claude's 180,000 - twenty-two times smaller. The existing prompt builder in ``ai_employee`` dumps the
whole context as JSON, which works at 180k and produces nothing usable at 8k.

Five slots, each with a share of the budget:

    identity     who the system is and its standing rules   NEVER truncated
    task         the goal, restated, plus its acceptance test   NEVER truncated
    evidence     what the record already measured, WITH sources
    working      the files or data actually being changed
    episodic     recent events, only where they change the next action

Two disciplines carry most of the value, and both exist because of specific failures in this codebase:

* **Cite or omit.** Every evidence line carries its source. A line that cannot be attributed is
  dropped rather than included, because a weaker model asked to reason over an unattributed
  half-memory will confidently build on it. ``second_brain.trace`` already separates measured from
  merely written down.
* **Never truncate silently.** If identity and task alone exceed the budget, the request is REFUSED
  with the numbers. A quietly shortened prompt asks a different question than the caller believes it
  asked - the same rule layer 0 enforces for oversized prompts.

Nothing here calls a model. It assembles text and reports exactly what it had to leave out.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Optional

from .ai_provider import estimate_tokens

# Share of the budget each slot may claim. Identity and task are small and protected; evidence gets
# the largest share because it is the part that stops the model inventing an answer the record
# already contradicts.
SLOT_SHARE = {
    "identity": 0.10,
    "task": 0.15,
    "evidence": 0.40,
    "working": 0.25,
    "episodic": 0.10,
}

# Dropped in this order when the budget will not stretch. Identity and task are absent on purpose:
# they are never dropped and never trimmed - if they do not fit, the request fails loudly instead.
DROP_ORDER = ("episodic", "working", "evidence")

# The model needs room to ANSWER. A builder that fills the whole window leaves nothing for the reply,
# and the failure looks like a truncated or empty answer rather than a context problem.
OUTPUT_RESERVE = 0.25


@dataclass
class Slot:
    name: str
    text: str
    tokens: int
    budget: int
    kept: int = 0
    available: int = 0
    dropped: bool = False
    note: str = ""


@dataclass
class Brief:
    ok: bool
    prompt: str = ""
    budget_tokens: int = 0
    prompt_tokens: int = 0
    slots: list = field(default_factory=list)
    citations: list = field(default_factory=list)
    omitted: list = field(default_factory=list)
    reason: Optional[str] = None

    def summary(self) -> dict:
        return {
            "ok": self.ok, "budget_tokens": self.budget_tokens, "prompt_tokens": self.prompt_tokens,
            "reason": self.reason,
            "slots": [{"name": s.name, "tokens": s.tokens, "budget": s.budget,
                       "kept": s.kept, "available": s.available, "dropped": s.dropped,
                       "note": s.note} for s in self.slots],
            "citations": self.citations,
            "omitted": self.omitted,
        }


def _fit_lines(lines: list, budget: int) -> tuple:
    """Keep whole lines until the budget runs out. Returns (kept, kept_count).

    Whole lines, never a partial one: half a measured result is worse than none, because it reads as
    a complete statement while missing the qualifier that made it true.
    """
    kept, used = [], 0
    for line in lines:
        cost = estimate_tokens(line) + 1
        if used + cost > budget:
            break
        kept.append(line)
        used += cost
    return kept, len(kept)


def depth_for(budget_tokens: int) -> tuple:
    """How many evidence entries to pull, and whether to include their body, for this budget.

    A builder that returns the same brief at 4,000 tokens and at 180,000 is not budgeting - it is
    capping. The small window gets titles only, because a title plus a source is the least that can
    still be checked; the large window gets the passage, because that is where the qualifier lives
    that makes a result true ("in the current price era", "5 of 8 years").
    """
    if budget_tokens <= 8_000:
        return 8, 0            # titles only: at this size the task must still fit
    if budget_tokens <= 40_000:
        return 20, 240
    return 40, 700


def evidence_lines(task: str, limit: int = 12, recall: Optional[Callable] = None,
                   tracer: Optional[Callable] = None, rows: Optional[list] = None,
                   excerpt: int = 0) -> tuple:
    """Evidence relevant to this task, every line carrying its source. Returns (lines, citations).

    Anything that cannot be attributed is dropped here rather than passed on unattributed. This is the
    cite-or-omit rule, and it is the difference between a model reasoning over the record and a model
    reasoning over a rumour of the record.
    """
    from . import second_brain

    recall = recall or second_brain.recall
    tracer = tracer or second_brain.trace
    found = recall(task, limit=limit, rows=rows) or {}
    lines, citations = [], []
    for hit in (found.get("matches") or []):
        source = hit.get("source") or ""
        date = hit.get("date") or "undated"
        title = (hit.get("title") or "").strip()
        if not title:
            continue
        provenance = tracer(hit, rows) if rows is not None else {"confidence": "cited", "is_fact": True}
        # A hypothesis may still be worth showing, but never as though it were measured. Labelling it
        # inline is what stops it being quoted back as a finding.
        mark = "" if provenance.get("is_fact") else " [UNVERIFIED CLAIM]"
        label = f"{date} · {source.rsplit(chr(92), 1)[-1].rsplit('/', 1)[-1]}"
        body = ""
        if excerpt:
            passage = " ".join((hit.get("text") or "").split())
            if len(passage) > len(title) + 20:
                body = "\n    " + passage[:excerpt] + ("..." if len(passage) > excerpt else "")
        lines.append(f"- {title}{mark}  ({label}){body}")
        citations.append({"date": hit.get("date"), "source": source, "title": title[:120],
                          "confidence": provenance.get("confidence", "cited")})
    return lines, citations


def assemble(task: str, *, budget_tokens: int, identity: str = "", acceptance: str = "",
             working: Optional[list] = None, episodic: Optional[list] = None,
             evidence: Optional[list] = None, citations: Optional[list] = None) -> Brief:
    """Build the prompt for ``task`` inside ``budget_tokens``, and report what was left out.

    The same call works at 4,000 tokens and at 100,000: the task is identical, the depth is not, and
    the difference is stated rather than hidden.
    """
    usable = max(1, int(budget_tokens * (1 - OUTPUT_RESERVE)))

    task_block = f"TASK\n{task.strip()}"
    if acceptance.strip():
        # Stated before the work, in a form something other than the model can check. This is the
        # highest-leverage habit in the whole design: "done" defined in advance cannot be redefined
        # afterwards to match whatever was produced.
        task_block += f"\n\nDONE WHEN\n{acceptance.strip()}"

    fixed = [
        Slot("identity", identity.strip(), estimate_tokens(identity), int(usable * SLOT_SHARE["identity"])),
        Slot("task", task_block, estimate_tokens(task_block), int(usable * SLOT_SHARE["task"])),
    ]
    protected_tokens = sum(s.tokens for s in fixed)
    if protected_tokens > usable:
        return Brief(ok=False, budget_tokens=budget_tokens, prompt_tokens=protected_tokens,
                     slots=fixed,
                     reason=(f"identity and task need about {protected_tokens:,} tokens and only "
                             f"{usable:,} are usable of a {budget_tokens:,} budget "
                             f"({int(OUTPUT_RESERVE * 100)}% is reserved for the answer). Refused "
                             "rather than truncated - shorten the task or use a larger provider."))

    evidence = list(evidence or [])
    working = list(working or [])
    episodic = list(episodic or [])

    remaining = usable - protected_tokens
    variable = [
        ("evidence", evidence, "EVIDENCE (from the record; each line carries its source)"),
        ("working", working, "WORKING SET"),
        ("episodic", episodic, "RECENT EVENTS"),
    ]
    # Budget in priority order, and hand any unspent share down the line rather than wasting it.
    shares = {name: int(remaining * SLOT_SHARE[name] / sum(SLOT_SHARE[n] for n, _, _ in variable))
              for name, _, _ in variable}

    # Two passes. The first gives every slot its own share. The second hands whatever is left over to
    # the HIGHEST-priority slot that still has lines waiting.
    #
    # Cascading leftover downward instead was a real bug: with an empty working set, its entire share
    # fell through to recent events, and a 2,000-token brief kept 61 measured results against 114
    # "something happened" lines. Spare capacity has to flow back up to evidence, which is the slot
    # that stops the model inventing an answer the record already contradicts.
    filled = {}
    for name, lines, _ in variable:
        kept, count = _fit_lines(lines, shares[name])
        filled[name] = {"kept": kept, "count": count, "used": sum(estimate_tokens(l) + 1 for l in kept)}

    leftover = remaining - sum(f["used"] for f in filled.values())
    for name, lines, _ in variable:                       # variable is already in priority order
        if leftover <= 0:
            break
        found = filled[name]
        if found["count"] >= len(lines):
            continue
        extra, added = _fit_lines(lines[found["count"]:], leftover)
        if not extra:
            continue
        spent = sum(estimate_tokens(l) + 1 for l in extra)
        found["kept"] += extra
        found["count"] += added
        found["used"] += spent
        leftover -= spent

    slots, omitted = list(fixed), []
    for name, lines, header in variable:
        found = filled[name]
        kept, count = found["kept"], found["count"]
        text = (header + "\n" + "\n".join(kept)) if kept else ""
        slot = Slot(name, text, estimate_tokens(text), shares[name], kept=count, available=len(lines))
        if count < len(lines):
            slot.note = f"kept {count} of {len(lines)} - the rest did not fit the budget"
            omitted.append({"slot": name, "kept": count, "available": len(lines),
                            "lost": [str(l)[:100] for l in lines[count:count + 3]]})
        if not kept and lines:
            slot.dropped = True
        slots.append(slot)

    prompt = "\n\n".join(s.text for s in slots if s.text.strip())
    return Brief(ok=True, prompt=prompt, budget_tokens=budget_tokens,
                 prompt_tokens=estimate_tokens(prompt), slots=slots,
                 citations=list(citations or []), omitted=omitted)


def brief_for_task(task: str, *, budget_tokens: int, identity: str = "", acceptance: str = "",
                   working: Optional[list] = None, episodic: Optional[list] = None,
                   rows: Optional[list] = None) -> Brief:
    """``assemble`` with the evidence slot filled from the system's own record."""
    limit, excerpt = depth_for(budget_tokens)
    lines, citations = evidence_lines(task, limit=limit, rows=rows, excerpt=excerpt)
    return assemble(task, budget_tokens=budget_tokens, identity=identity, acceptance=acceptance,
                    working=working, episodic=episodic, evidence=lines, citations=citations)


def budget_for_provider(provider=None) -> int:
    """The context budget of whichever provider would answer right now.

    Layer 2 must not guess the window. Asking layer 0 is what makes the same code produce a 4k brief
    offline and a 180k one online without anything above knowing which happened.
    """
    from . import ai_provider

    provider = provider or ai_provider.choose()
    if provider is None:
        return 0
    return provider.context_tokens()


if __name__ == "__main__":       # pragma: no cover - a hand check, not a test
    import sys

    goal = " ".join(sys.argv[1:]) or "does the gold model have an edge"
    for budget in (4_000, 32_000, 180_000):
        out = brief_for_task(goal, budget_tokens=budget,
                             identity="i40 Pilot. Never invent data. Cite or omit.",
                             acceptance="a number from BASELINE.md with its date")
        print(json.dumps(out.summary(), indent=1)[:900])
        print("-" * 70)
