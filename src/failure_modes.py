"""i40 Pilot layer 3 - criticise the plan before running it. Build map step 7.

Every entry below is a way work has actually gone wrong in THIS repository. None is a general caution
about AI or about trading; each cites the occasion, and each carries the one check that would have
caught it *before* the result was reported rather than after.

That ordering is the whole point. The pinned lesson in this project is:

    Run the cheap check that would falsify a claim BEFORE stating it.

A critique pass asks one question of a plan - *what would make this wrong, and what does it cost to
check?* - and answers it from the register rather than from imagination. That matters most offline,
where the model is small: a weaker model cannot be relied on to invent the right doubt, but it can
follow a checklist of doubts that have already been earned.

``critique(plan)`` returns the modes that apply and the checks to run first. ``coverage()`` is the
acceptance test for this build step: every recorded failure has a line that would have caught it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Mode:
    id: str
    name: str
    happened: str          # the occasion, so it is never mistaken for a general caution
    check: str             # the cheap thing that would have caught it BEFORE the claim
    triggers: tuple        # words in a plan that make this mode relevant
    cost: str = "cheap"


# Ordered roughly by how expensive the mistake was.
REGISTER = (
    Mode("in-sample", "Choosing on the window you then report",
         "The Volatility Trend Breakout cleared its bar at the 97.7th percentile on the full window "
         "and sat at the 46.7th - dead centre of random - on the four years predating its tuning. "
         "The MaxSpreadPoints gain looked like 12x and vanished entirely out of sample.",
         "Name the window the parameters were chosen on, and report a window that never touched it. "
         "If the two are the same, say so before quoting any number.",
         ("backtest", "optimis", "optimiz", "sweep", "tune", "parameter", "improve", "best", "fit")),

    Mode("spike-not-plateau", "A single good cell mistaken for an effect",
         "Gold spacing 111 beat the baseline by +417 with 108 at -211 and 114 at -1258 either side. "
         "Bitcoin SL 120000 was the only positive cell between two much worse ones. Both rejected; "
         "the accepted findings instead sat on smooth hills - SL 3000/4000/5000 all beat 2100.",
         "Test the neighbours. An effect is a hill; a fluke is a spike. Report the cells either side "
         "of any winner before calling it one.",
         ("sweep", "grid", "best", "optimis", "optimiz", "parameter", "winner", "candidate")),

    Mode("outlier-window", "Fitting on an unrepresentative period",
         "Two rounds of SmartEntry candidates found nothing because every one was tuned on 2024 - "
         "which turned out to be that EA's second-worst year of eight (+16 against a +2,007 total).",
         "Look at the year-by-year distribution BEFORE choosing a fitting window, and say where the "
         "chosen window sits in it.",
         ("backtest", "tune", "fit", "window", "period", "year", "optimis", "optimiz")),

    Mode("wrong-period-measurement", "Measuring the input on a different period than the test",
         "The spread cap was raised because gold's spread never exceeded 28 - measured over "
         "August-September 2026, while the backtest ran on 2024. The trades the looser cap admitted "
         "were real and they lost.",
         "Measure the distribution INSIDE the test window, not in the most recent data the API "
         "happens to return.",
         ("spread", "filter", "threshold", "cap", "measure", "distribution", "backtest")),

    Mode("foreign-attribution", "Counting somebody else's work as the system's",
         "94 of 100 trades in a headline reading 'the system is profitable, net +366.16' carried "
         "mt5_service's DEFAULT magic 903110. The system's own journal recorded eight events and "
         "four tickets. This account holds 34 distinct magic numbers.",
         "Attribute a trade only when the system's own journal records placing it. A magic number is "
         "a label anything can write; the journal is evidence.",
         ("profit", "performance", "trades", "account", "magic", "earned", "result", "track")),

    Mode("counter-not-reality", "Reporting a counter instead of the thing it describes",
         "'closed_trades: 0' was reported as 'nothing is trading' while a winning demo position was "
         "open. The counter was a config file with no runtime state in it.",
         "Query the live source - the account, the endpoint, the log - not the field that is supposed "
         "to summarise it.",
         ("status", "report", "nothing", "idle", "count", "state", "check", "trading")),

    Mode("invented-data", "Filling a gap with something plausible",
         "Yahoo had no ticker for NAS100, so the signal builder fell through to a RANDOM WALK ending "
         "2024-12-31 and published BUY calls from it, on a bar 632 days old, with nothing on any "
         "page saying so.",
         "Return available: false rather than a plausible-looking number, and make the data source "
         "visible on whatever displays the result.",
         ("signal", "data", "fetch", "fallback", "source", "feed", "display", "dashboard")),

    Mode("silent-truncation", "Quietly shortening the question",
         "A prompt larger than a provider's window would have been cut to fit, so the caller would "
         "have trusted the answer to a question it did not ask.",
         "Refuse with the numbers instead of truncating. Whole units only - half a measured result "
         "reads as a complete statement while missing the qualifier that made it true.",
         ("prompt", "context", "window", "token", "budget", "summar", "trim", "fit")),

    Mode("self-inflicted-outage", "A diagnostic that changes the live system",
         "A probe comparing raw against calibrated output called train_model(), which PERSISTS to "
         "models/, overwriting the live XAUUSD and BTCUSD champions. Separately, repeated "
         "mt5.initialize() calls against the app's terminal halted two strategies for two hours.",
         "Before running a diagnostic, name what it writes and what it connects to. Build estimators "
         "directly rather than calling training entry points; read through the app's endpoints "
         "rather than opening a second MT5 session.",
         ("diagnos", "probe", "investigat", "check", "train", "model", "mt5", "compare", "measure")),

    Mode("duplicate-build", "Writing what already exists",
         "A new src/providers.py was started from scratch while src/ai_provider.py already held "
         "Provider, OllamaProvider and complete. The architecture document had listed that layer as "
         "empty purely because nobody had looked.",
         "Search for the capability before building it: grep the definitions, list the module names, "
         "run the duplicate check.",
         ("build", "create", "new module", "implement", "add", "write", "layer", "step")),

    Mode("uncheckable-check", "A check that cannot pass, or that checks itself",
         "Eight skills were given acceptance specs written as prose - 'contains the rejection and "
         "its reason' - which no file could ever satisfy. A test asserting a module contained no "
         "'flatten' matched that module's own docstring explaining why it does not flatten.",
         "Run the check once against reality before trusting it. A check that never passes, or that "
         "matches its own explanation, is worse than none - it looks like verification.",
         ("test", "check", "assert", "verify", "acceptance", "validate", "guard")),

    Mode("tuning-before-looking", "Adjusting the judgement before inspecting the input",
         "Three rounds were spent tuning a keyword heuristic for unsupported claims. The actual bug "
         "was that BASELINE.md's prose was never indexed at all, so 309 of 652 lines were invisible.",
         "Print what the function actually sees before changing how it decides.",
         ("heuristic", "tune", "threshold", "classif", "detect", "match", "filter", "score")),

    Mode("budget-ignored", "A limit declared and then not applied",
         "The first context builder returned an identical brief at 4,000 and 180,000 tokens, because "
         "the retrieval was hard-capped at 12 entries. A budgeted thing that ignores its budget is a cap.",
         "Vary the limit and assert the output changes. If it does not, the limit is decorative.",
         ("budget", "limit", "cap", "size", "window", "scale", "depth")),

    Mode("unowned-baseline", "Measuring against a reference nobody confirmed",
         "Every SmartEntry figure was measured against an .ini baseline while the owner's reported "
         "~1100 could not be reproduced from either that file or the .set - so the comparison point "
         "itself was never agreed.",
         "State the baseline and how it was obtained, and flag it when it cannot be reproduced. A "
         "result is only as good as the reference it is measured against.",
         ("compare", "baseline", "improve", "better", "against", "versus", "result")),
)

# Layer 3 may call layer 1, so the tokeniser is imported rather than copied. The duplicate check
# caught a second definition here and it was right to: two tokenisers drifting apart is how a match
# starts behaving differently in two places for no visible reason.
from .second_brain import _words


def critique(plan: str, limit: int = 5, register: tuple = REGISTER) -> dict:
    """What would make this plan wrong, and what does it cost to check?

    Answered from the register rather than from imagination. A mode with no trigger word in the plan
    is not raised - a critique that fires on everything gets skimmed and then ignored.
    """
    text = (plan or "").lower()
    words = _words(text)
    scored = []
    for mode in register:
        hits = [t for t in mode.triggers if (t in text if len(t) > 4 else t in words)]
        if hits:
            scored.append((len(hits), mode, hits))
    scored.sort(key=lambda row: -row[0])
    applies = [{"id": m.id, "name": m.name, "check": m.check, "happened": m.happened,
                "matched": hits} for _, m, hits in scored[:limit]]
    return {
        "plan": (plan or "")[:300],
        "applies": applies,
        "count": len(applies),
        "checks_first": [a["check"] for a in applies],
        "note": ("Each of these has happened here. Run the checks BEFORE stating a result, not after "
                 "- being the one who catches your own error after reporting it is not the same as "
                 "being right."),
    }


def register_is_sound() -> dict:
    """Does every mode carry a real occasion and a real check? The acceptance test for step 7."""
    problems = []
    for mode in REGISTER:
        if len(mode.happened) < 60:
            problems.append(f"{mode.id}: the occasion is too thin to be a real one")
        if len(mode.check) < 40:
            problems.append(f"{mode.id}: the check is not specific enough to run")
        if not mode.triggers:
            problems.append(f"{mode.id}: no triggers, so it would never be raised")
        # A mode that names no measurement, file, window or source is a caution, not a check.
        if not any(word in mode.check.lower() for word in
                   ("measure", "report", "run", "check", "test", "search", "name", "print",
                    "query", "return", "state", "vary", "look", "attribute", "refuse")):
            problems.append(f"{mode.id}: the check does not say what to DO")
    return {"modes": len(REGISTER), "problems": problems, "sound": not problems,
            "ids": [m.id for m in REGISTER]}


def as_brief(limit: int = 0) -> str:
    """The register as text, for the identity slot of a context brief."""
    rows = REGISTER[:limit] if limit else REGISTER
    return "KNOWN FAILURE MODES (each has happened here; check before claiming)\n" + "\n".join(
        f"- {m.name}: {m.check}" for m in rows)


if __name__ == "__main__":       # pragma: no cover - a hand check, not a test
    import sys

    plan = " ".join(sys.argv[1:]) or "sweep the EA parameters and pick the best"
    print(json.dumps(critique(plan), indent=1))
