"""Self-learning curve (14 Sep 2026): is the daily training making the models better, worse, or is there no real change?

Reads what the learning already writes and never trains or trades:
- data/<symbol>_daily_history.json: one row per training run (live and newly tried accuracy, keep/reject);
- data/learning_decisions.json: the promotion gate's unseen-bar test (accuracy, trades, expectancy, return);
- the Strategy Lab registry and the paper trader state, for the other learning in the system.

Honest verdicts (the owner asked for the truth, nothing flattering):
- repeated runs on the same day count once (the last run of the day), so a result is not counted several times;
- a change is only called improving / getting worse when it is bigger than the chance band of one run's accuracy
  (95%: 1.96 * sqrt(p(1-p)/test bars)); anything smaller is "no real change";
- the old-vs-new tests on the same unseen bars are counted separately, and the verdict says when there are too few.

Page: /self-learning, API: /api/learning-curve.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .system_doctor import _read_json  # one JSON reader: returns None when the file is missing or unreadable

SYMBOLS = ("XAUUSD", "BTCUSD")
TREND_WINDOW = 7            # compare the last 7 days with the 7 before
TEST_FRACTION = 0.2         # train.py scores each run on the last 20% of its rows
DEFAULT_TEST_ROWS = 450     # used when a run did not record its row count
MIN_CHECKED_RUNS = 10       # same-bar old-vs-new tests needed before that evidence is trusted
STALE_HOURS = 30            # daily task at 05:30: more than this since the last run means learning stopped
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
NAMES = {"XAUUSD": "Gold", "BTCUSD": "Bitcoin"}


def _parse_time(value) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value)[:19], TIME_FORMAT)
    except (TypeError, ValueError):
        return None


def chance_band_pts(accuracy: float, test_rows: float) -> float:
    """95% chance band, in accuracy points, of one run scored on ``test_rows`` bars."""
    p = min(max(float(accuracy), 0.01), 0.99)
    return 1.96 * math.sqrt(p * (1 - p) / max(float(test_rows), 1.0)) * 100


def trend(values, window: int = TREND_WINDOW, test_rows: float = DEFAULT_TEST_ROWS) -> dict:
    """Label a series of daily accuracies: improving, worse, no_real_change, or insufficient (fewer than 4)."""
    vals = [float(v) for v in values if v is not None]
    base = {"n": len(vals), "latest": vals[-1] if vals else None,
            "best": max(vals) if vals else None, "worst": min(vals) if vals else None}
    if len(vals) < 4:
        return {**base, "label": "insufficient", "window": None, "recent_mean": None, "previous_mean": None,
                "change_pts": None, "noise_pts": None, "change_since_first_pts": None, "slope_pts_per_10_runs": None}
    w = min(window, len(vals) // 2)
    recent, previous = vals[-w:], vals[-2 * w:-w]
    recent_mean, previous_mean = sum(recent) / w, sum(previous) / w
    change = (recent_mean - previous_mean) * 100
    # Conservative on purpose: daily runs share most of their data, so they are not independent tests and
    # averaging them does not shrink the band. The change must beat one run's chance band.
    noise = chance_band_pts((recent_mean + previous_mean) / 2, test_rows)
    n = len(vals)
    x_mean, y_mean = (n - 1) / 2, sum(vals) / n
    denominator = sum((i - x_mean) ** 2 for i in range(n))
    slope = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(vals)) / denominator if denominator else 0.0
    label = "improving" if change >= noise else "worse" if change <= -noise else "no_real_change"
    return {**base, "label": label, "window": w, "recent_mean": recent_mean, "previous_mean": previous_mean,
            "change_pts": round(change, 2), "noise_pts": round(noise, 2),
            "change_since_first_pts": round((vals[-1] - vals[0]) * 100, 2),
            "slope_pts_per_10_runs": round(slope * 100 * 10, 2)}


def _point(row: dict) -> dict:
    return {
        "trained_at": row.get("trained_at"), "status": row.get("status"), "rows": row.get("rows"),
        "rf": row.get("accuracy"), "lstm": row.get("lstm_accuracy"),
        "rf_challenger": row.get("challenger_accuracy"), "lstm_challenger": row.get("challenger_lstm_accuracy"),
        "rf_promoted": row.get("rf_promoted"), "lstm_promoted": row.get("lstm_promoted"),
        "rf_decision": row.get("rf_decision") or row.get("reason"), "lstm_decision": row.get("lstm_decision"),
        "error": row.get("error"), "gated": row.get("rf_promoted") is not None, "counted": False,
    }


def _gate(row: dict) -> Optional[dict]:
    champion, challenger = row.get("rf_champion"), row.get("rf_challenger")
    champion = champion if isinstance(champion, dict) else None
    challenger = challenger if isinstance(challenger, dict) else None
    if champion is None and challenger is None:
        return None
    live = (challenger if (row.get("rf_promoted") or champion is None) else champion) or {}
    outcome = None
    if champion and challenger and champion.get("accuracy") is not None and challenger.get("accuracy") is not None:
        diff = float(challenger["accuracy"]) - float(champion["accuracy"])
        outcome = "better" if diff > 1e-9 else "worse" if diff < -1e-9 else "tie"
    return {
        "trained_at": row.get("trained_at"), "rf_promoted": row.get("rf_promoted"),
        "champion": champion, "challenger": challenger, "new_vs_old": outcome,
        "champion_return_pct": (champion or {}).get("total_return_pct"),
        "challenger_return_pct": (challenger or {}).get("total_return_pct"),
        "live_return_pct": live.get("total_return_pct"), "live_expectancy_pct": live.get("expectancy_pct"),
        "trades": live.get("trades"), "window": live.get("window"),
    }


def _symbol_curve(data_dir: Path, symbol: str, decisions: list) -> Optional[dict]:
    history = _read_json(data_dir / f"{symbol.lower()}_daily_history.json") or []
    history = [row for row in history if isinstance(row, dict)] if isinstance(history, list) else []
    mine = [row for row in decisions if isinstance(row, dict) and row.get("symbol") == symbol]
    seen = {row.get("trained_at") for row in history}
    # a run that crashed is recorded only in the decisions file; show it too
    extra = [row for row in mine if row.get("status") == "failed" and row.get("trained_at") not in seen]
    rows = sorted(history + extra, key=lambda row: str(row.get("trained_at") or ""))
    if not rows:
        return None
    points = [_point(row) for row in rows]
    last_of_day = {}
    for index, point in enumerate(points):
        if point["rf"] is not None:
            last_of_day[str(point["trained_at"])[:10]] = index
    for index in last_of_day.values():
        points[index]["counted"] = True
    daily = [p for p in points if p["counted"]]
    test_rows = [p["rows"] * TEST_FRACTION for p in daily[-2 * TREND_WINDOW:] if p.get("rows")]
    test_rows_mean = sum(test_rows) / len(test_rows) if test_rows else DEFAULT_TEST_ROWS
    gates = [gate for gate in (_gate(row) for row in mine) if gate]
    outcomes = [g["new_vs_old"] for g in gates if g["new_vs_old"]]
    returns = [g["live_return_pct"] for g in gates if g["live_return_pct"] is not None]
    status = [str(p.get("status") or "") for p in points]
    return {
        "points": points,
        "gates": gates,
        "latest": points[-1],
        "latest_gate": gates[-1] if gates else None,
        "trend": {"rf": trend([p["rf"] for p in daily], test_rows=test_rows_mean),
                  "lstm": trend([p["lstm"] for p in daily], test_rows=test_rows_mean)},
        "checked": {
            "runs": len(gates), "needed": MIN_CHECKED_RUNS, "enough": len(gates) >= MIN_CHECKED_RUNS,
            "new_better": outcomes.count("better"), "tie": outcomes.count("tie"), "new_worse": outcomes.count("worse"),
            "money_positive": sum(1 for r in returns if r > 0), "money_negative_or_zero": sum(1 for r in returns if r <= 0),
        },
        "summary": {
            "runs": len(points),
            "days": len(daily),
            "repeat_runs_same_day": sum(1 for p in points if p["rf"] is not None and not p["counted"]),
            "ungated_days": sum(1 for p in daily if not p["gated"]),
            "trained": status.count("trained"),
            "skipped": sum(1 for s in status if s.startswith("skipped") or s == "no_data"),
            "failed": status.count("failed"),
            "rf_kept": sum(1 for p in points if p["rf_promoted"] is True),
            "rf_rejected": sum(1 for p in points if p["rf_promoted"] is False and p["status"] == "trained"),
            "lstm_kept": sum(1 for p in points if p["lstm_promoted"] is True),
            "lstm_rejected": sum(1 for p in points if p["lstm_promoted"] is False and p["status"] == "trained"),
        },
    }


def _strategy_lab(data_dir: Path) -> dict:
    registry = data_dir / "strategy_lab" / "registry.json"
    if not registry.exists():
        return {"available": False, "reason": "Strategy Lab has not run yet."}
    try:
        from . import strategy_lab
        summary = strategy_lab.lab_summary(registry, data_dir / "strategy_lab" / "status.json", limit=1)
    except Exception as exc:  # optional section
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
    markets = [{"market": key, **{k: int((meta.get("counts") or {}).get(k) or 0) for k in ("evaluated", "validated", "holdout_passed")}}
               for key, meta in summary.get("markets", {}).items()]
    totals = {k: sum(m[k] for m in markets) for k in ("evaluated", "validated", "holdout_passed")}
    status = summary.get("status") or {}
    return {"available": True, "markets": markets, "totals": totals,
            "last_finished_at": status.get("last_finished_at"), "state": status.get("state")}


def paper_state_path(data_dir: Path) -> Path:
    """The paper trader's state file under ``data_dir`` (shared with src/obsidian_notes.py)."""
    from . import paper_trader
    relative = paper_trader.state_path()
    parts = relative.parts[1:] if relative.parts and relative.parts[0] == "data" else relative.parts
    return Path(data_dir).joinpath(*parts)


def _paper_trader(data_dir: Path) -> dict:
    try:
        path = paper_state_path(data_dir)
    except Exception as exc:  # optional section
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
    state = _read_json(path)
    if not isinstance(state, dict):
        return {"available": False, "reason": "The paper trader has not run yet."}
    decisions = state.get("decisions") or []
    closed = state.get("closed_trades") or []
    last = decisions[-1] if decisions else {}
    return {"available": True, "started_at": state.get("started_at"), "bars_decided": len(decisions),
            "closed_trades": len(closed), "open_trade": bool(state.get("open_trade")),
            "last_action": last.get("action"), "last_bar": last.get("bar_time")}


def _overall(symbols: dict, task: dict) -> dict:
    if not task["healthy"]:
        return {"label": "stopped", "headline": "Learning has stopped", "detail": task["message"]}
    parts, labels, facts = [], [], []
    for symbol, curve in symbols.items():
        name = NAMES.get(symbol, symbol)
        rf = curve["trend"]["rf"]
        labels.append(rf["label"])
        if rf["label"] == "insufficient":
            parts.append(f"{name}: not enough days yet")
        else:
            wording = {"improving": "real improvement", "worse": "real drop", "no_real_change": "no real change"}[rf["label"]]
            parts.append(f"{name}: {wording} ({rf['change_pts']:+.1f} pts; chance alone can move it ±{rf['noise_pts']:.1f})")
        checked = curve["checked"]
        facts.append(f"{name} {checked['runs']} of {checked['needed']} needed (new better {checked['new_better']}, "
                     f"tie {checked['tie']}, worse {checked['new_worse']})")
    judged = [label for label in labels if label != "insufficient"]
    if not judged:
        label, headline = "insufficient", "Not enough daily runs yet to tell"
    elif "improving" in judged and "worse" not in judged:
        label, headline = "improving", "Improving: a gain bigger than chance"
    elif "worse" in judged and "improving" not in judged:
        label, headline = "worse", "Getting worse: a drop bigger than chance"
    else:
        label, headline = "no_real_change", "No real change yet: the ups and downs are within chance"
    detail = "; ".join(parts) + "."
    if facts:
        detail += " Old-vs-new tests on the same unseen bars: " + "; ".join(facts) + "."
    losing = [NAMES.get(s, s) for s, c in symbols.items() if c.get("latest_gate") and (c["latest_gate"].get("live_return_pct") or 0) <= 0]
    if losing:
        detail += f" The live models for {', '.join(losing)} lose money on unseen bars: no trading edge yet."
    return {"label": label, "headline": headline, "detail": detail}


def build_learning_curve(data_dir=Path("data"), now: Optional[datetime] = None) -> dict:
    data_dir = Path(data_dir)
    now = now or datetime.now()  # training times are written in local time
    decisions = _read_json(data_dir / "learning_decisions.json") or []
    decisions = decisions if isinstance(decisions, list) else []
    symbols = {}
    for symbol in SYMBOLS:
        curve = _symbol_curve(data_dir, symbol, decisions)
        if curve:
            symbols[symbol] = curve
    times = [t for t in (_parse_time(c["latest"]["trained_at"]) for c in symbols.values()) if t]
    last = max(times) if times else None
    hours = round((now - last).total_seconds() / 3600, 1) if last else None
    healthy = hours is not None and hours <= STALE_HOURS
    if last is None:
        message = "No training run has been recorded yet."
    elif healthy:
        message = f"Last run {hours} hours ago; the next one is due at 05:30."
    else:
        message = f"No training run for {hours} hours (limit {STALE_HOURS}). Check the SmartEntry Daily Learning task and data/learning/daily_learning.log."
    task = {"last_run": last.strftime(TIME_FORMAT) if last else None, "hours_since": hours, "healthy": healthy,
            "message": message, "next_run": "daily 05:30", "stale_after_hours": STALE_HOURS}
    return {
        "available": True,
        "places_orders": False,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "task": task,
        "overall": _overall(symbols, task),
        "symbols": symbols,
        "strategy_lab": _strategy_lab(data_dir),
        "paper_trader": _paper_trader(data_dir),
        "rules": {"trend_window": TREND_WINDOW, "min_checked_runs": MIN_CHECKED_RUNS, "stale_hours": STALE_HOURS,
                  "test_fraction": TEST_FRACTION, "chance_band": "95%, one run's accuracy", "one_run_per_day": True},
    }
