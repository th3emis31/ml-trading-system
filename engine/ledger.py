"""11. The trade ledger: every decision, every REJECTED setup, and the exact reason for each.

This is the part of the architecture most backtests skip, and skipping it is what makes a result
unanswerable afterwards. A ledger that records only the trades taken cannot answer the two questions that
matter most when a number looks wrong:

    how many setups were found, and why was each one NOT taken?
    what did the engine believe at the moment it entered?

So a rejection is a first-class row here, carrying the same weight as a fill, and its reason is mandatory.
The owner's live system already works this way — `execution_guard` appends every refusal to
`data/execution_rejections.jsonl` with its reason — and this is the backtest's equivalent.

Rows are plain dicts and the ledger is append-only. Nothing is summarised here; `analytics` computes the
numbers from these rows, so the figures and the narrative can never drift apart.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class Row:
    """One thing that happened at one bar. `kind` says which."""

    bar: int
    ts: str
    kind: str                       # setup | rejected | entry | manage | exit
    symbol: str = ""
    timeframe: str = ""
    side: Optional[str] = None      # BUY / SELL
    reason: str = ""                # why it was taken, or why it was NOT
    price: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Ledger:
    """Append-only record of a run. Read it with `funnel()` and `rejection_reasons()` first."""

    rows: List[Row] = field(default_factory=list)

    # -- writing -------------------------------------------------------------------------------
    def record(self, **kwargs) -> Row:
        row = Row(**kwargs)
        self.rows.append(row)
        return row

    def setup(self, bar: int, ts: str, **kw) -> Row:
        return self.record(bar=bar, ts=ts, kind="setup", **kw)

    def rejected(self, bar: int, ts: str, reason: str, **kw) -> Row:
        """A setup that was found and NOT taken. The reason is required — that is the whole point."""
        if not reason:
            raise ValueError("a rejection without a reason is not a ledger entry")
        return self.record(bar=bar, ts=ts, kind="rejected", reason=reason, **kw)

    def entry(self, bar: int, ts: str, **kw) -> Row:
        return self.record(bar=bar, ts=ts, kind="entry", **kw)

    def manage(self, bar: int, ts: str, **kw) -> Row:
        return self.record(bar=bar, ts=ts, kind="manage", **kw)

    def closed(self, bar: int, ts: str, **kw) -> Row:
        """Named `closed`, not `exit`: `exit` shadows the builtin and reads badly at a call site."""
        return self.record(bar=bar, ts=ts, kind="exit", **kw)

    # -- reading -------------------------------------------------------------------------------
    def of_kind(self, kind: str) -> List[Row]:
        return [r for r in self.rows if r.kind == kind]

    def rejection_reasons(self) -> Dict[str, int]:
        """Why setups were turned down, commonest first. The first thing to read when a run looks empty."""
        return dict(Counter(r.reason for r in self.of_kind("rejected")).most_common())

    def funnel(self) -> Dict[str, int]:
        """setup -> rejected -> entry -> manage -> exit. A funnel narrowing to nothing is a bug report."""
        counts = Counter(r.kind for r in self.rows)
        return {k: counts.get(k, 0) for k in ("setup", "rejected", "entry", "manage", "exit")}

    def write(self, path) -> Path:
        """One JSON object per line, so a long run streams rather than being held whole in memory."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for row in self.rows:
                fh.write(json.dumps(asdict(row), default=str) + "\n")
        return path

    def __len__(self) -> int:
        return len(self.rows)
