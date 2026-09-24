"""Backtest any list of broker symbols on equal terms and rank them by what they actually earn.

Written 24 September 2026: the owner wants to know which markets are worth the system's attention,
across 823 stocks, 73 ETFs, 61 cryptos, indices and forex, and asked for "the most accurate and best
option" rather than another quick pass.

Four things make this more accurate than the ad-hoc runs it replaces, and each one was chosen because
the coarser alternative produces a specific, known error:

1. **H4 bars, not daily.** The engine resolves a bar that touches both stop and target by booking the
   stop - a conservative choice, but a choice. That ambiguity is far more common on daily bars than
   on H4, so a daily test systematically understates. H4 also yields roughly six times the trades,
   which is what turns a number into evidence.

2. **Costs MEASURED per symbol, never assumed.** A symbol with no measured spread falls through to a
   generic 0.1 %, and at this scale that assumption would decide the ranking rather than the market:
   for XRPUSD the real cost is 1.64 % and the generic one is sixteen times too cheap. Every symbol's
   round trip is measured from its own M1 bars using the broker's spread column, by the same
   convention the existing cost model uses - 90th percentile, doubled for slippage.

3. **An evidence gate, stated rather than implied.** Under MIN_TRADES_FOR_EVIDENCE the result is
   labelled insufficient and is not ranked. XRPUSD's six-trade, 5.96 profit factor is exactly the
   artefact this exists to catch.

4. **One symbol per process.** Five symbols in one process was killed twice by this machine's memory
   ceiling. Running them separately is not a workaround - it is what lets the fold count go up
   instead of down.

Nothing here trades. It reads bars and reports.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
RESULTS_PATH = ROOT / "data" / "market_screen.json"

MIN_TRADES_FOR_EVIDENCE = 100
SPREAD_SAMPLE_BARS = 60_000          # 100,000 is the terminal's cap and fails silently at it
DEFAULT_FOLDS = 5
DEFAULT_BARS = 12_000                # H4: about 8 years


def measure_round_trip(symbol: str, terminal_path: Optional[str] = None,
                       sample: int = SPREAD_SAMPLE_BARS) -> Optional[dict]:
    """The symbol's real round-trip cost, from the broker's own per-minute spread column.

    Same convention as ``walkforward_backtest.BACKTEST_COSTS``: 90th-percentile spread, doubled, so
    half the charge is a bad-moment spread and half a slippage allowance. Returns None when the
    spread cannot be measured - and a caller must then refuse to rank the symbol rather than guess,
    because a guessed cost decides the ranking instead of the market.
    """
    import MetaTrader5 as mt5
    import numpy as np

    if terminal_path:
        mt5.initialize(path=terminal_path)
    else:
        mt5.initialize()
    try:
        if not mt5.symbol_select(symbol, True):
            return None
        info = mt5.symbol_info(symbol)
        bars = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, sample)
        if info is None or bars is None or len(bars) < 1000:
            return None
        price = bars["close"].astype(float)
        pct = (bars["spread"].astype(float) * info.point) / price * 100.0
        pct = pct[np.isfinite(pct) & (price > 0)]
        if pct.size < 1000:
            return None
        p50, p90 = float(np.percentile(pct, 50)), float(np.percentile(pct, 90))
        return {"round_trip_pct": round(p90 * 2 / 100.0, 8), "p50_pct": round(p50, 5),
                "p90_pct": round(p90, 5), "minutes": int(pct.size)}
    finally:
        mt5.shutdown()


def rank_key(row: dict) -> tuple:
    """Order candidates by after-cost expectancy, with anything short of evidence pushed to the back.

    Expectancy rather than total return, because return rewards whichever symbol happened to trend
    hardest over the window; expectancy is per trade and comparable between markets. Profit factor
    breaks ties.
    """
    if not row.get("evidence"):
        return (1, 0.0, 0.0)
    return (0, -float(row.get("expectancy_pct") or 0.0), -float(row.get("profit_factor") or 0.0))


def _plain(value):
    """numpy scalars do not survive json.dumps, and each symbol is screened in its own process, so a
    numpy float here fails the whole run at the subprocess boundary rather than in the maths."""
    if value is None:
        return None
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return value


def screen_row(symbol: str, metrics: dict, cost: dict, bars: int, years: float) -> dict:
    trades = int(metrics.get("trades") or 0)
    return {
        "symbol": symbol, "bars": bars, "years": round(years, 1), "trades": trades,
        "evidence": trades >= MIN_TRADES_FOR_EVIDENCE,
        "evidence_note": None if trades >= MIN_TRADES_FOR_EVIDENCE
        else f"{trades} trades is under the {MIN_TRADES_FOR_EVIDENCE}-trade minimum; not ranked",
        "win_rate": _plain(metrics.get("win_rate")),
        "profit_factor": _plain(metrics.get("profit_factor")),
        "expectancy_pct": _plain(metrics.get("expectancy_pct")),
        "total_return_pct": _plain(metrics.get("total_return_pct")),
        "max_drawdown_pct": _plain(metrics.get("max_drawdown_pct")),
        "sharpe": _plain(metrics.get("sharpe")),
        "exposure_pct": _plain(metrics.get("exposure_pct")),
        "round_trip_pct": cost.get("round_trip_pct"),
        "spread_p90_pct": cost.get("p90_pct"),
        "cost_measured": True,
    }


def screen_one(symbol: str, terminal_path: Optional[str] = None, bars: int = DEFAULT_BARS,
               folds: int = DEFAULT_FOLDS) -> dict:
    """Measure the symbol's cost, then walk-forward it on H4 bars. Runs inside its own process."""
    import warnings
    warnings.filterwarnings("ignore")
    import MetaTrader5 as mt5
    import pandas as pd

    cost = measure_round_trip(symbol, terminal_path)
    if not cost:
        return {"symbol": symbol, "skipped": "spread could not be measured, so its cost would be a "
                                              "guess and the ranking would reflect the guess"}

    from .walkforward_backtest import BACKTEST_COSTS, run_walkforward_backtest
    # The measured figure wins over any stored constant, so every symbol is charged its own cost.
    BACKTEST_COSTS[symbol.upper()] = {"round_trip_pct": cost["round_trip_pct"],
                                      "note": f"measured on {cost['minutes']} M1 bars, p90 doubled"}

    mt5.initialize(path=terminal_path) if terminal_path else mt5.initialize()
    mt5.symbol_select(symbol, True)
    raw = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H4, 0, bars)
    mt5.shutdown()
    if raw is None or len(raw) < 500:
        return {"symbol": symbol, "skipped": f"only {0 if raw is None else len(raw)} H4 bars available"}

    frame = pd.DataFrame(raw)
    frame["datetime"] = pd.to_datetime(frame["time"], unit="s", utc=True)
    frame = frame[["datetime", "open", "high", "low", "close", "tick_volume"]].rename(
        columns={"tick_volume": "volume"}).reset_index(drop=True)
    years = (frame["datetime"].iloc[-1] - frame["datetime"].iloc[0]).days / 365.25

    # rf_proba, not the live engine, and this is a screening decision rather than a shortcut.
    #
    # "live_engine" re-predicts every out-of-sample bar through the full ensemble, which is right when
    # judging ONE strategy because it reproduces exactly what the dashboard would have done. Measured
    # on 12,000 gold H4 bars it takes over 350 seconds against 26 for rf_proba - fifteen times - so a
    # twelve-symbol screen becomes many hours on a machine that kills long jobs, which is how the
    # first attempt lost seventy minutes and produced nothing.
    #
    # The screener's question is which markets deserve attention, and that is a RELATIVE ranking. The
    # same signal path applied to every symbol answers it honestly. What this result must not be
    # treated as is a verdict on a market: anything that ranks well here should then be re-run through
    # the live-engine path before a single decision is made on it.
    out = run_walkforward_backtest(symbol.upper(), "5y", data=frame, n_folds=folds,
                                   signal_mode="rf_proba")
    if not out.get("available"):
        return {"symbol": symbol, "skipped": str(out.get("reason"))[:120]}
    return screen_row(symbol, out.get("metrics") or {}, cost, len(frame), years)


def _load_previous() -> dict:
    """Symbols already screened in an earlier run, so a killed job resumes instead of restarting.

    Keyed by symbol. Re-running the same list after a kill costs only the symbol that was in flight.
    """
    try:
        prior = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for row in (prior.get("ranked") or []) + (prior.get("skipped") or []):
        if row.get("symbol"):
            out[row["symbol"]] = row
    return out


def _save_report(rows: list, skipped: list, settings: dict) -> dict:
    ranked = sorted(rows, key=rank_key)
    report = {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "settings": settings, "ranked": ranked, "skipped": skipped,
        "with_evidence": [r for r in ranked if r.get("evidence")],
        "done": len(rows) + len(skipped),
        "places_orders": False,
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = RESULTS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=1), encoding="utf-8")
    tmp.replace(RESULTS_PATH)
    return report


def screen(symbols: Iterable[str], terminal_path: Optional[str] = None, bars: int = DEFAULT_BARS,
           folds: int = DEFAULT_FOLDS, timeout: int = 900) -> dict:
    """Screen each symbol in its OWN process, then rank.

    Separate processes are what make the higher fold count affordable: five symbols in one process
    was killed twice by this machine's memory ceiling, and the reflex fix - fewer folds - would have
    bought speed by making every number less reliable.
    """
    # Results are written after EVERY symbol, not at the end. The first run of this lost seventy
    # minutes of completed work when the machine's memory ceiling killed it on symbol seven of
    # twelve: eleven finished backtests existed only in a list in a process that no longer did. A
    # long job on a machine that kills long jobs has to be able to lose only its current step.
    settings = {"timeframe": "H4", "bars": bars, "folds": folds,
                "min_trades_for_evidence": MIN_TRADES_FOR_EVIDENCE,
                "costs": "measured per symbol from broker M1 spread, p90 doubled"}
    rows, skipped = [], []
    done = _load_previous()
    report = _save_report(rows, skipped, settings)
    for symbol in symbols:
        if symbol in done:
            (skipped if done[symbol].get("skipped") else rows).append(done[symbol])
            continue
        cmd = [sys.executable, "-c",
               "import json,sys;from src.market_screener import screen_one;"
               f"print('@@'+json.dumps(screen_one({symbol!r}, {terminal_path!r}, {bars}, {folds})))"]
        try:
            proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)
            line = next((l for l in (proc.stdout or "").splitlines() if l.startswith("@@")), None)
            row = json.loads(line[2:]) if line else {"symbol": symbol, "skipped": "no result returned"}
        except subprocess.TimeoutExpired:
            row = {"symbol": symbol, "skipped": f"timed out after {timeout}s"}
        except Exception as exc:
            row = {"symbol": symbol, "skipped": f"{type(exc).__name__}: {exc}"}
        (skipped if row.get("skipped") else rows).append(row)
        report = _save_report(rows, skipped, settings)     # after EVERY symbol, so a kill costs one step

    return report
