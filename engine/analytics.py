"""12. Analytics: win rate, profit factor, expectancy, R-multiple, drawdown, MAE/MFE, streaks, sessions.

Most of this delegates to `src.walkforward_backtest.summarize_trades`, which is already the agreed metric
set and was independently re-derived on 27 September 2026 — fourteen trade lists across seven families,
75 to 1,585 trades, zero mismatches against a second implementation.

MAE and MFE are the exception: they are in the owner's architecture and the system did not compute them.
They cannot come from the trade record, because a trade only stores its entry, exit and result — the
excursion happens BETWEEN those, so it has to be measured from the bars the trade was open over.

Why they matter more than they look: a strategy whose winners routinely sit 1.5 R underwater before paying
is not the same strategy as one whose winners never go against it, even when both show the same
expectancy. The first cannot be traded at that stop size by a human, and it will not survive a slightly
worse fill. MAE is the number that tells you which one you have.
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from src.walkforward_backtest import summarize_trades


def core(trades: List[dict], *, test_start, test_end, test_bars: int) -> dict:
    """Trades, win rate, profit factor, expectancy, avg_r, expectancy_r, drawdown, Sharpe, streaks."""
    bars_in_market = int(sum(t.get("bars_held") or 0 for t in trades))
    return summarize_trades(trades, test_start=test_start, test_end=test_end,
                            test_bars=test_bars, bars_in_market=bars_in_market)


def excursions(trades: List[dict], frame: pd.DataFrame) -> List[dict]:
    """MAE and MFE per trade, in R, measured over the bars the trade was actually open.

    MAE (maximum adverse excursion) is the worst the trade went AGAINST the entry before it closed.
    MFE (maximum favourable excursion) is the best it went FOR it.

    Both are expressed in R — the trade's own risk distance — because points are not comparable across
    trades with different stops, and that incomparability is what makes raw-point excursion charts
    misleading.
    """
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    out: List[dict] = []
    for t in trades:
        start = t.get("entry_idx")
        held = t.get("bars_held")
        entry = t.get("entry_price")
        if start is None or held is None or entry is None:
            out.append({"mae_r": None, "mfe_r": None})
            continue
        # The trade record carries no stop level, but the risk distance is recoverable from it:
        #     r_multiple = (gross_pct / 100) * entry / risk      =>   risk = gross_pct/100 * entry / r
        # Checked against a real stopped SELL on 27 Sep 2026: gross -0.2126 %, entry 2494.23, r -1.0
        # gives 5.303, which is exactly exit_price - entry_price for a trade that hit its stop.
        risk = None
        r_mult, gross = t.get("r_multiple"), t.get("gross_pct")
        if r_mult not in (None, 0) and gross is not None:
            risk = abs(float(gross) / 100.0 * float(entry) / float(r_mult))
        elif t.get("exit_price") is not None:
            risk = abs(float(t["exit_price"]) - float(entry))   # last resort, only exact on a stop-out
        if risk is None or not np.isfinite(risk) or risk <= 0:
            out.append({"mae_r": None, "mfe_r": None})
            continue
        a = int(start) + 1                      # the fill is the NEXT bar's open
        b = min(a + int(held), len(high) - 1)
        if b < a:
            out.append({"mae_r": None, "mfe_r": None})
            continue
        window_high = float(np.nanmax(high[a:b + 1]))
        window_low = float(np.nanmin(low[a:b + 1]))
        if str(t.get("side")) == "BUY":
            adverse, favourable = float(entry) - window_low, window_high - float(entry)
        else:
            adverse, favourable = window_high - float(entry), float(entry) - window_low
        out.append({"mae_r": round(max(0.0, adverse) / risk, 3),
                    "mfe_r": round(max(0.0, favourable) / risk, 3)})
    return out


def excursion_summary(trades: List[dict], frame: pd.DataFrame) -> dict:
    """What the excursions say as a whole, split by whether the trade ended up a winner.

    The comparison is the point: if winners and losers have the SAME median MAE, the stop is not
    separating them and the entry is not as precise as the headline suggests.
    """
    rows = excursions(trades, frame)
    mae = [r["mae_r"] for r in rows if r["mae_r"] is not None]
    mfe = [r["mfe_r"] for r in rows if r["mfe_r"] is not None]
    if not mae:
        return {"available": False, "reason": "trades carry no entry/stop/bars_held to measure against"}
    won = [i for i, t in enumerate(trades) if (t.get("net_pct") or 0) > 0]
    lost = [i for i, t in enumerate(trades) if (t.get("net_pct") or 0) <= 0]

    def median_of(idx, key):
        vals = [rows[i][key] for i in idx if i < len(rows) and rows[i][key] is not None]
        return round(float(np.median(vals)), 3) if vals else None

    return {
        "available": True,
        "median_mae_r": round(float(np.median(mae)), 3),
        "median_mfe_r": round(float(np.median(mfe)), 3),
        "worst_mae_r": round(float(np.max(mae)), 3),
        "winners_median_mae_r": median_of(won, "mae_r"),
        "losers_median_mae_r": median_of(lost, "mae_r"),
        "winners_median_mfe_r": median_of(won, "mfe_r"),
        "trades_measured": len(mae),
    }


def by_session(trades: List[dict]) -> Dict[str, dict]:
    """Performance split by UTC session, so "it only works in London" is answerable rather than assumed."""
    def session_of(ts) -> str:
        hour = pd.Timestamp(ts).hour
        if 0 <= hour < 8:
            return "asia"
        if 8 <= hour < 13:
            return "london"
        if 13 <= hour < 17:
            return "overlap"
        return "newyork_late"

    buckets: Dict[str, List[dict]] = {}
    for t in trades:
        buckets.setdefault(session_of(t.get("entry_time")), []).append(t)
    out = {}
    for name, rows in buckets.items():
        nets = [r.get("net_pct") or 0.0 for r in rows]
        wins = [n for n in nets if n > 0]
        out[name] = {"trades": len(rows),
                     "win_rate_pct": round(100 * len(wins) / len(rows), 2) if rows else None,
                     "net_pct": round(float(np.sum(nets)), 3)}
    return dict(sorted(out.items()))


def by_regime(trades: List[dict], regime_state: dict) -> Dict[str, dict]:
    """Performance split by the regime that was in force at the entry bar."""
    labels = regime_state.get("trend")
    if labels is None:
        return {}
    buckets: Dict[str, List[float]] = {}
    for t in trades:
        i = t.get("entry_idx")
        if i is None or i >= len(labels):
            continue
        buckets.setdefault(str(labels[i]), []).append(t.get("net_pct") or 0.0)
    return {k: {"trades": len(v), "net_pct": round(float(np.sum(v)), 3)}
            for k, v in sorted(buckets.items())}


def outcome_mix(trades: List[dict]) -> Dict[str, int]:
    """How trades ended — target, stop, trail, time exit. A run that is all time-exits is a broken target."""
    return dict(Counter(str(t.get("outcome")) for t in trades).most_common())
