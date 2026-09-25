"""The second brain: everything the system has already established, made askable.

The i40 Pilot is the FIRST brain - the live state, rebuilt hourly, describing what is running right now.
It forgets: each brief replaces the last, and it counts records rather than reading them.

This is the second: the durable side. About 320 KB of results, lessons, notes and decisions sit in
``.claude/memory/`` and ``data/`` and nothing could search them, so the same ground kept being covered
twice. On 20 September 2026 a four-hour timeframe CRT idea was proposed that earlier research had already
killed - only a subagent reading twelve BASELINE rows caught it. That is the cost this module exists to
remove, and the reason it leads with ``prior_work()`` rather than with search.

WHAT IT IS NOT. It stores nothing new, summarises nothing, and never answers from its own words. Every
result carries the file and the date it came from, so a recall can be checked against the record. When
nothing matches it says so; an empty answer is a real answer and is never padded.

    python -m src.second_brain recall "crt trailing stop"
    python -m src.second_brain tried "4h gold breakout"
    python -m src.second_brain status
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .runtime_paths import smartentry_data_dir

ROOT = Path(__file__).resolve().parents[1]
MEMORY_DIR = ROOT / ".claude" / "memory"
STRATEGY_DIR = ROOT / "strategies"

DATE = re.compile(r"(20\d\d-\d\d-\d\d)")
WORD = re.compile(r"[a-z0-9_.]+")
# Words too common in this repo to carry meaning; matching on them returns everything.
STOP = {"the", "a", "an", "and", "or", "of", "to", "on", "in", "is", "was", "for", "it", "its", "with",
        "that", "this", "at", "by", "as", "be", "not", "no", "so", "than", "then", "from", "over",
        "trade", "trades", "test", "tested", "run", "ran", "result", "results", "system", "one", "two"}


def _words(text: str) -> list[str]:
    return [w for w in WORD.findall((text or "").lower()) if w not in STOP and len(w) > 1]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _baseline_entries(path: Path) -> list[dict]:
    """Recorded results: one entry per SECTION, plus one per table row.

    This used to index table rows only, and that made the evidence store half-blind to itself. Of
    BASELINE.md's 652 lines, 309 are headings and prose - the part that says what was measured, what
    was rejected and why - and none of it was searchable. Worse, a row's date was read from its first
    cell, which on a parameter sweep is a spacing value, not a date; the result was that every finding
    recorded on 24 September 2026 had no date at all and could not be found by when it happened.

    ``recall`` and ``prior_work`` exist to stop settled questions being re-derived. They can only do
    that if they can see the answers, so sections are indexed whole and every row inherits its
    section's date and heading.
    """
    out = []
    heading, heading_date, buffer = None, None, []

    def flush():
        if heading and buffer:
            body = " ".join(line.strip() for line in buffer if line.strip())
            out.append({"kind": "result", "date": heading_date, "source": str(path),
                        "title": heading[:160], "text": f"{heading} {body}"[:4000]})

    for line in _read(path).splitlines():
        if line.startswith("## "):
            flush()
            heading = line[3:].strip()
            found = DATE.search(heading)
            heading_date, buffer = (found.group(1) if found else None), []
            continue
        buffer.append(line)
        if not line.startswith("|") or line.startswith("|---") or "| date |" in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        found = DATE.search(cells[0])
        out.append({"kind": "result",
                    # The row's own date when it carries one, otherwise the section it sits under -
                    # never the first cell blindly, which is how sweep rows acquired dates of "114".
                    "date": found.group(1) if found else heading_date,
                    "source": str(path),
                    "title": (f"{heading}: " if heading else "") + (cells[3][:120] if len(cells) > 3 else cells[0]),
                    "text": (f"{heading} " if heading else "") + " ".join(cells)})
    flush()
    return out


def _bullet_entries(path: Path, kind: str) -> list[dict]:
    out = []
    for line in _read(path).splitlines():
        stripped = line.strip()
        if not stripped.startswith("- ") or stripped.startswith("- [x]"):
            continue
        body = stripped[2:].strip()
        if not body:
            continue
        found = DATE.search(body)
        out.append({"kind": kind, "date": found.group(1) if found else None, "source": str(path),
                    "title": body[:160], "text": body})
    return out


def _strategy_entries(directory: Path) -> list[dict]:
    out = []
    for path in sorted(directory.glob("*.md")):
        text = _read(path)
        if not text:
            continue
        found = DATE.search(text)
        heading = next((l.lstrip("# ").strip() for l in text.splitlines() if l.startswith("#")), path.stem)
        out.append({"kind": "strategy_doc", "date": found.group(1) if found else None, "source": str(path),
                    "title": heading[:160], "text": text})
    return out


def _learning_entries(path: Path) -> list[dict]:
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        promoted = row.get("rf_promoted") or row.get("lstm_promoted")
        stamp = str(row.get("trained_at") or "")[:10] or None
        out.append({"kind": "learning_decision", "date": stamp, "source": str(path),
                    "title": (f"{row.get('symbol')} {'promoted' if promoted else 'kept the champion'}"
                              f" (accuracy {row.get('accuracy')})"),
                    "text": " ".join(str(row.get(k)) for k in
                                     ("symbol", "status", "rf_decision", "lstm_decision", "data_source"))})
    return out


def entries(memory_dir: Optional[Path] = None, data_dir: Optional[Path] = None,
            strategy_dir: Optional[Path] = None) -> list[dict]:
    """Every durable record the system holds, in one shape. Nothing is generated here."""
    memory_dir = Path(memory_dir or MEMORY_DIR)
    data_dir = Path(data_dir or smartentry_data_dir())
    strategy_dir = Path(strategy_dir or STRATEGY_DIR)
    found: list[dict] = []
    found += _baseline_entries(memory_dir / "BASELINE.md")
    found += _bullet_entries(memory_dir / "LESSONS.md", "lesson")
    found += _bullet_entries(memory_dir / "NOTES.md", "note")
    found += _bullet_entries(memory_dir / "BACKLOG.md", "backlog")
    found += _learning_entries(data_dir / "learning_decisions.json")
    found += _strategy_entries(strategy_dir)
    for index, entry in enumerate(found):
        entry["id"] = index
    return found


def _index(rows: Iterable[dict]) -> dict:
    """Document frequency per term, so a word in every row counts for less than a rare one."""
    frequency: Counter = Counter()
    for row in rows:
        frequency.update(set(_words(row["text"])))
    return frequency


def recall(query: str, limit: int = 6, rows: Optional[list[dict]] = None) -> dict:
    """What the record already says about ``query``, best match first, with its source and date.

    Scoring is deliberately plain term overlap weighted by rarity - no model, no embedding, nothing that
    can hallucinate a match. A recall either points at a real line in a real file or returns nothing.
    """
    rows = rows if rows is not None else entries()
    terms = _words(query)
    if not terms:
        return {"query": query, "matches": [], "searched": len(rows),
                "answer": "No searchable terms in that query."}
    frequency = _index(rows)
    total = max(1, len(rows))
    scored = []
    for row in rows:
        words = Counter(_words(row["text"]))
        score = 0.0
        hit = 0
        for term in terms:
            if words.get(term):
                hit += 1
                score += (1.0 + math.log(words[term])) * math.log(total / (1 + frequency.get(term, 0)))
        if hit:
            scored.append((score, hit, row))
    scored.sort(key=lambda item: (-item[0], -item[1]))
    matches = [{"kind": r["kind"], "date": r["date"], "title": r["title"], "source": r["source"],
                "terms_matched": hit, "of_terms": len(terms), "score": round(score, 3),
                "excerpt": _excerpt(r["text"], terms)}
               for score, hit, r in scored[:limit]]
    answer = (f"{len(scored)} of {len(rows)} records mention it." if scored
              else "Nothing in the record mentions that. It has not been written down.")
    return {"query": query, "terms": terms, "searched": len(rows), "match_count": len(scored),
            "matches": matches, "answer": answer}


def _excerpt(text: str, terms: list[str], width: int = 260) -> str:
    """The part of the record that actually matched, not the first N characters of it."""
    lowered = text.lower()
    best, position = -1, 0
    for term in terms:
        found = lowered.find(term)
        if found >= 0 and (best < 0 or found < best):
            best, position = found, found
    start = max(0, position - width // 3)
    piece = " ".join(text[start:start + width].split())
    return ("..." if start else "") + piece + ("..." if start + width < len(text) else "")


def prior_work(topic: str, rows: Optional[list[dict]] = None) -> dict:
    """Has this been tried before, and what happened? The question that stops work being repeated.

    Leading with this rather than with search is the point of the module: the failure it exists to prevent
    is proposing something the record already settled.
    """
    found = recall(topic, limit=5, rows=rows)
    results = [m for m in found["matches"] if m["kind"] in ("result", "strategy_doc")]
    lessons = [m for m in found["matches"] if m["kind"] == "lesson"]
    if not found["matches"]:
        return {"topic": topic, "tried_before": False, "confidence": "none",
                "verdict": "No record of this being tried. Nothing here says it will work either.",
                "evidence": [], "lessons": []}
    dates = sorted({m["date"] for m in found["matches"] if m["date"]})
    verdict = (f"Tried before: {len(results)} recorded result(s)"
               + (f", earliest {dates[0]}, latest {dates[-1]}" if dates else "")
               + ". Read them before repeating the work.") if results else \
              ("Mentioned in the record but with no recorded RESULT - notes or backlog only, "
               "so it may have been discussed rather than measured.")
    return {"topic": topic, "tried_before": bool(results), "confidence": "recorded" if results else "mentioned",
            "verdict": verdict, "evidence": results, "lessons": lessons,
            "searched": found["searched"], "match_count": found["match_count"]}


def status(rows: Optional[list[dict]] = None) -> dict:
    rows = rows if rows is not None else entries()
    by_kind = Counter(r["kind"] for r in rows)
    dated = sorted(r["date"] for r in rows if r["date"])
    return {"available": bool(rows), "records": len(rows), "by_kind": dict(sorted(by_kind.items())),
            "earliest": dated[0] if dated else None, "latest": dated[-1] if dated else None,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "note": "Durable records only. This brain stores nothing of its own and answers only with sources."}


def _console_safe() -> None:
    """Windows consoles default to cp1252, and the records are full of characters it cannot encode.

    A recall that crashes on a minus sign (U+2212) in its own evidence is useless, and the failure is in
    the PRINTING, not the answer - so replace what cannot be encoded rather than lose the result.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv=None) -> int:
    _console_safe()
    parser = argparse.ArgumentParser(description="The system's second brain: recall what is already established.")
    sub = parser.add_subparsers(dest="command", required=True)
    ask = sub.add_parser("recall", help="what does the record say about this?")
    ask.add_argument("query", nargs="+")
    ask.add_argument("--limit", type=int, default=6)
    tried = sub.add_parser("tried", help="has this been tried before?")
    tried.add_argument("topic", nargs="+")
    sub.add_parser("status")
    args = parser.parse_args(argv)

    if args.command == "status":
        state = status()
        print(f"second brain: {state['records']} durable records, {state['earliest']} to {state['latest']}")
        for kind, count in state["by_kind"].items():
            print(f"  {kind:18s} {count}")
        return 0

    if args.command == "tried":
        out = prior_work(" ".join(args.topic))
        print(f"topic: {out['topic']}")
        print(f"verdict: {out['verdict']}")
        for item in out["evidence"]:
            print(f"  [{item['kind']}] {item['date'] or 'undated'}  {item['title']}")
            print(f"      {item['excerpt'][:200]}")
        for item in out["lessons"]:
            print(f"  [lesson] {item['title']}")
        return 0

    out = recall(" ".join(args.query), limit=args.limit)
    print(f"query: {out['query']}   terms {out.get('terms')}")
    print(out["answer"])
    for item in out["matches"]:
        print(f"\n  [{item['kind']}] {item['date'] or 'undated'}  ({item['terms_matched']}/{item['of_terms']} terms)")
        print(f"  {item['title']}")
        print(f"      {item['excerpt']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------------------
# Memory kinds and provenance - i40 Pilot build map step 2.
#
# The four kinds already existed as files; what was missing is that they were all treated the same.
# They are not the same, and the differences are what make the record trustworthy:
#
#   evidence    measured, append-only, permanent      BASELINE.md, learning decisions
#   episodic    what happened and when, prunable      NOTES.md
#   semantic    what is true here, correctable        LESSONS.md
#   procedural  how a thing is done here, versioned   strategies/
#
# The rule this section enforces: **a claim is a FACT only when it traces to a measurement or a dated
# event. Otherwise it is a HYPOTHESIS and is labelled one.** Without that distinction, a guess written
# down on a bad day becomes indistinguishable six months later from a result measured over 7,669
# trades, and the system will cite both with the same confidence.
# ---------------------------------------------------------------------------

KIND_RULES = {
    "result":   {"memory": "evidence",   "append_only": True,  "prunable": False, "is_measured": True},
    "learning": {"memory": "evidence",   "append_only": True,  "prunable": False, "is_measured": True},
    "note":     {"memory": "episodic",   "append_only": False, "prunable": True,  "is_measured": False},
    "lesson":   {"memory": "semantic",   "append_only": False, "prunable": False, "is_measured": False},
    "strategy": {"memory": "procedural", "append_only": False, "prunable": False, "is_measured": False},
    "backlog":  {"memory": "episodic",   "append_only": False, "prunable": True,  "is_measured": False},
}

# An explicit citation written into an entry, e.g. "(ref: BASELINE 2026-09-24 permutation)". Explicit
# beats inferred every time, which is why the convention exists; inference below is only the fallback.
REF = re.compile(r"\(ref:\s*([^)]{3,120})\)", re.I)

# Words that mean the writer is reporting a measurement rather than a plan. Used only to decide
# whether an untraced entry asserts a result (which needs a source) or records an intention (which
# does not), so that ordinary working notes are not flagged as unsupported claims.
# A figure: 1.12, 85.8%, +404, 7,669. Its presence is what separates "the edge is real" (an opinion,
# and fine to write down) from "the edge is 0.0823 per trade" (a measurement, which must be traceable).
NUMERIC = re.compile(r"\d+[\d,.]*\s*%?")

CLAIM_WORDS = {"profitable", "better", "worse", "beats", "improves", "improved", "loses", "wins",
               "profit", "expectancy", "drawdown", "accuracy", "percentile", "edge", "factor"}


def memory_kind(entry: dict) -> str:
    """Which of the four stores this entry belongs to."""
    return KIND_RULES.get(entry.get("kind"), {}).get("memory", "episodic")


def _refs(text: str) -> list:
    return [m.strip() for m in REF.findall(text or "")]


def trace(entry: dict, rows=None, limit: int = 4) -> dict:
    """Where this entry's claim came from: explicit citation, then dated measurement, then nothing.

    Three levels, and the third is the one that matters:

    * ``cited``    - the entry names its source and that source exists. Trusted.
    * ``inferred`` - a measured result from the same day shares its vocabulary. Plausible, and
      labelled inferred so it is never quoted as though it had been cited.
    * ``none``     - nothing supports it. If the entry asserts a result, it is a HYPOTHESIS.
    """
    rows = rows if rows is not None else entries()
    text = entry.get("text") or ""
    measured = [r for r in rows if KIND_RULES.get(r.get("kind"), {}).get("is_measured")]

    cited = _refs(text)
    if cited:
        hits = []
        for ref in cited:
            terms = [w for w in _words(ref) if w not in STOP]
            for row in measured:
                body = (row.get("text") or "").lower()
                if terms and sum(1 for t in terms if t in body) >= max(1, len(terms) // 2):
                    hits.append({"id": row.get("id"), "date": row.get("date"),
                                 "source": row.get("source"), "title": row.get("title")})
        if hits:
            return {"confidence": "cited", "refs": cited, "supports": hits[:limit], "is_fact": True,
                    "note": "the entry names its source and that source is in the record"}
        return {"confidence": "cited_but_missing", "refs": cited, "supports": [], "is_fact": False,
                "note": "the entry cites a source that is NOT in the record - treat as unverified"}

    same_day = [r for r in measured if r.get("date") and r.get("date") == entry.get("date")]
    terms = {w for w in _words(text) if w not in STOP and len(w) > 3}
    scored = []
    for row in same_day:
        overlap = terms & {w for w in _words(row.get("text") or "") if w not in STOP}
        if len(overlap) >= 3:
            scored.append((len(overlap), row))
    scored.sort(key=lambda pair: -pair[0])
    if scored:
        return {"confidence": "inferred", "refs": [],
                "supports": [{"id": r.get("id"), "date": r.get("date"), "source": r.get("source"),
                              "title": r.get("title"), "shared_terms": n} for n, r in scored[:limit]],
                "is_fact": True,
                "note": "a measured result from the same day shares its vocabulary - inferred, not cited"}

    # A claim word ALONE is far too blunt: a tooling lesson that happens to contain the word "edge"
    # was flagged as an unsupported finding, and a checker that cries wolf gets ignored, which is
    # worse than not having it. A MEASURED claim carries a NUMBER - "profit factor 1.12", "5 of 8
    # years", "0.6th percentile". Opinions and instructions do not. Requiring both cuts the false
    # positives without letting a real unsupported result through.
    # Every entry begins with its own date, and a date is made of digits - so the numeric test has to
    # ignore dates or it matches everything and discriminates nothing.
    figures = NUMERIC.search(DATE.sub(" ", text))
    asserts = bool(terms & CLAIM_WORDS) and bool(figures)
    return {"confidence": "none", "refs": [], "supports": [], "is_fact": False,
            "note": ("this asserts a result but nothing in the record measures it - HYPOTHESIS, not fact"
                     if asserts else
                     "nothing measures it, and it claims no result - an ordinary working note")}


def hypotheses(rows=None) -> dict:
    """Entries that read like findings but trace to no measurement.

    These are the dangerous ones. Read back months later they look exactly like results, and the
    difference between "we measured this" and "we thought this" is the difference between a decision
    and a guess.
    """
    rows = rows if rows is not None else entries()
    out = []
    for entry in rows:
        if memory_kind(entry) not in ("semantic", "episodic"):
            continue
        found = trace(entry, rows)
        if found["confidence"] in ("none", "cited_but_missing") and not found["is_fact"]:
            if found["confidence"] == "none" and "HYPOTHESIS" not in found["note"]:
                continue                       # a plain note claiming nothing is not a hypothesis
            out.append({"id": entry.get("id"), "date": entry.get("date"), "kind": entry.get("kind"),
                        "title": entry.get("title"), "why": found["note"]})
    return {"count": len(out), "entries": out,
            "note": ("A hypothesis is not a mistake - it is a claim not yet measured. It becomes one "
                     "only when it is cited as though it had been.")}


def promotion_candidates(rows=None, min_repeats: int = 2) -> dict:
    """Episodic entries that have earned a place in semantic memory.

    Promotion is proposed, never performed: writing to LESSONS.md is the owner's call, and a system
    that promotes its own notes to truths without review is how a guess becomes doctrine.

    A note earns promotion by RECURRING - the same subject appearing on two or more separate days is
    a pattern, where one mention is an incident.
    """
    rows = rows if rows is not None else entries()
    notes = [r for r in rows if r.get("kind") == "note" and r.get("date")]
    lessons = [r for r in rows if r.get("kind") == "lesson"]
    lesson_terms = [{w for w in _words(l.get("text") or "") if w not in STOP} for l in lessons]

    groups: dict = {}
    for note in notes:
        terms = frozenset(w for w in _words(note.get("text") or "")
                          if w not in STOP and len(w) > 4)
        if len(terms) < 5:
            continue
        placed = False
        for key in list(groups):
            if len(key & terms) >= 5:
                groups[key].append(note)
                placed = True
                break
        if not placed:
            groups[terms] = [note]

    out = []
    for key, found in groups.items():
        days = {n["date"] for n in found}
        if len(days) < min_repeats:
            continue
        if any(len(key & lt) >= 5 for lt in lesson_terms):
            continue                            # semantic memory already says this
        out.append({"days": sorted(days), "occurrences": len(found),
                    "subject": sorted(key)[:8],
                    "examples": [n.get("title") for n in found[:3]]})
    out.sort(key=lambda row: -row["occurrences"])
    return {"count": len(out), "candidates": out,
            "note": "Proposed only. Promotion into LESSONS.md is the owner's decision."}


def compaction_candidates(rows=None, keep_days: int = 45) -> dict:
    """Episodic entries old enough to prune - and only once nothing in them is awaiting promotion.

    The ordering is the whole point: compacting before promoting destroys the evidence for a pattern
    just as it becomes visible. Nothing here deletes anything; it proposes.
    """
    from datetime import datetime, timezone

    rows = rows if rows is not None else entries()
    pending = promotion_candidates(rows)
    if pending["count"]:
        return {"safe": False, "count": 0, "candidates": [],
                "reason": (f"{pending['count']} note group(s) are awaiting promotion. Compacting now "
                           "would delete the evidence for a pattern at the moment it became visible."),
                "awaiting_promotion": pending["candidates"][:5]}
    today = datetime.now(timezone.utc).date()
    old = []
    for entry in rows:
        if not KIND_RULES.get(entry.get("kind"), {}).get("prunable") or not entry.get("date"):
            continue
        try:
            age = (today - datetime.strptime(entry["date"], "%Y-%m-%d").date()).days
        except ValueError:
            continue
        if age > keep_days:
            old.append({"id": entry.get("id"), "date": entry.get("date"), "age_days": age,
                        "kind": entry.get("kind"), "title": entry.get("title")})
    return {"safe": True, "count": len(old), "candidates": old[:40], "keep_days": keep_days,
            "note": "Proposed only. Nothing is pruned without the owner saying so."}


def provenance_report(rows=None) -> dict:
    """How much of the record can actually be traced - the acceptance test for build map step 2."""
    rows = rows if rows is not None else entries()
    by_memory: dict = {}
    for entry in rows:
        bucket = by_memory.setdefault(memory_kind(entry),
                                      {"entries": 0, "cited": 0, "inferred": 0, "none": 0, "facts": 0})
        bucket["entries"] += 1
        if KIND_RULES.get(entry.get("kind"), {}).get("is_measured"):
            bucket["cited"] += 1                # a measurement is its own source
            bucket["facts"] += 1
            continue
        found = trace(entry, rows)
        key = {"cited": "cited", "inferred": "inferred"}.get(found["confidence"], "none")
        bucket[key] += 1
        bucket["facts"] += 1 if found["is_fact"] else 0
    total = sum(b["entries"] for b in by_memory.values())
    traced = sum(b["cited"] + b["inferred"] for b in by_memory.values())
    return {"total": total, "traceable": traced,
            "traceable_pct": round(100.0 * traced / total, 1) if total else None,
            "by_memory": by_memory,
            "hypotheses": hypotheses(rows)["count"],
            "promotion_pending": promotion_candidates(rows)["count"],
            "note": ("Traceable means it cites a source that exists, or a measurement from the same "
                     "day shares its vocabulary. Everything else is a hypothesis until measured.")}
