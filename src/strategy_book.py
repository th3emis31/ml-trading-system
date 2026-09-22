"""Strategy book: the strategies worth keeping from the Strategy Lab, with the evidence behind them.

Research only - nothing here places orders. After every hourly Strategy Lab search this module:

* re-scores stored candidates once under the current cost model (spread + overnight swap), so old
  spread-only results never sit next to new ones;
* gathers extra evidence for the best candidates of each market:
    - Monte Carlo: the out-of-sample trades (validation + holdout) resampled 2,000 times -> chance of
      ending with a loss and the 95th-percentile drawdown;
    - neighbourhood: every one-step change of each setting must mostly stay profitable (a real edge
      sits on a plateau, a lucky one on a spike);
    - rolling consistency: share of 12-month windows (stepped quarterly) with a positive result;
* keeps each strategy in ``data/strategy_lab/strategy_book.json`` with a status:
    - ``approved_for_demo``: passed the holdout (deflated Sharpe included) and all evidence checks;
    - ``watchlist``: profitable on the holdout with enough trades, but not proven yet;
    - ``demoted``: was in the book, no longer qualifies on the newest data (kept, never deleted);
* re-checks every book entry on the newest bars each run and appends to its history (the holdout
  grows every day, so this is the forward test that keeps the book honest);
* writes an MT5 preset for gold EMA-pullback strategies (the SwingTrendPullback expert). Approved ones
  are also copied into the terminal's Presets folder. Loading one is the user's decision - demo first.

Run:  python -m src.strategy_book update [--minutes 15]
      python -m src.strategy_book rescore --markets XAUUSD:4h
      python -m src.strategy_book status
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from . import strategy_lab as lab

BOOK_PATH = lab.LAB_DIR / "strategy_book.json"
PRESET_DIR = lab.LAB_DIR / "presets"
MT5_PRESET_DIR = (Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
                  / "MetaQuotes" / "Terminal" / "D0E8209F77C8CF37AD8BF550E51FF075" / "MQL5" / "Presets")
BOOK_RULES = {
    "mc_max_loss_probability": 0.20,     # approved: at most 20% of resampled out-of-sample histories lose money
    "min_neighbour_share": 0.60,         # approved: at least 60% of one-step variants stay profitable
    "min_rolling_share": 0.55,           # approved: at least 55% of 12-month windows positive
    "watch_min_holdout_pf": 1.15,
    "watch_min_holdout_trades": 30,
    "candidates_per_market": 8,          # new candidates examined per market per update
}
MC_RUNS = 2000
WATCHLIST_PER_MARKET = 10  # active watchlist strategies kept per market; the rest are archived (kept, not re-checked)
TIMEFRAME_LABELS = {"15m": "M15", "1h": "H1", "4h": "H4", "1d": "D1"}


# --------------------------------------------------------------------------- evidence
def monte_carlo(returns_pct, runs: int = MC_RUNS, seed: int = 7) -> Optional[dict]:
    """Bootstrap the trade sequence: loss probability, median and 5th-percentile return, 95th-percentile drawdown."""
    values = np.asarray(returns_pct, dtype=float) / 100.0
    if len(values) < 10:
        return None
    rng = np.random.default_rng(seed)
    samples = values[rng.integers(0, len(values), size=(runs, len(values)))]
    equity = np.cumprod(1.0 + samples, axis=1)
    peaks = np.maximum.accumulate(np.concatenate([np.ones((runs, 1)), equity], axis=1), axis=1)[:, 1:]
    drawdown = 1.0 - equity / peaks
    final = equity[:, -1] - 1.0
    return {"runs": int(runs), "trades": int(len(values)),
            "loss_probability": round(float((final <= 0).mean()), 4),
            "median_return_pct": round(float(np.median(final)) * 100, 2),
            "p5_return_pct": round(float(np.percentile(final, 5)) * 100, 2),
            "p95_max_drawdown_pct": round(float(np.percentile(drawdown.max(axis=1), 95)) * 100, 2)}


def rolling_consistency(trades: list[dict], window_days: int = 365, step_days: int = 91, min_trades: int = 5) -> Optional[dict]:
    """Share of rolling windows (by entry date) that ended positive."""
    if not trades:
        return None
    frame = pd.DataFrame({"time": pd.to_datetime([t["entry_time"] for t in trades], utc=True),
                          "ret": [t["net_pct"] / 100.0 for t in trades]}).sort_values("time")
    start, end = frame["time"].iloc[0], frame["time"].iloc[-1]
    results = []
    cursor = start
    while cursor + pd.Timedelta(days=window_days) <= end + pd.Timedelta(days=1):
        window = frame[(frame["time"] >= cursor) & (frame["time"] < cursor + pd.Timedelta(days=window_days))]
        if len(window) >= min_trades:
            results.append(float(np.prod(1.0 + window["ret"].to_numpy()) - 1.0))
        cursor += pd.Timedelta(days=step_days)
    if not results:
        return None
    return {"windows": len(results), "positive_share": round(sum(1 for r in results if r > 0) / len(results), 3),
            "worst_window_pct": round(min(results) * 100, 2)}


def neighbours(spec: dict) -> list[dict]:
    """Every valid spec that moves one setting one step along its search grid (the side is never flipped)."""
    out, seen = [], {lab.spec_id(spec)}
    grids = [("params", lab.FAMILIES[spec["family"]]), ("exits", lab.EXIT_GRID)]
    for group, grid in grids:
        for key, values in grid.items():
            if key == "side" or len(values) < 2 or key not in spec[group]:
                continue
            current = spec[group][key]
            if current not in values:
                continue
            index = values.index(current)
            for step in (-1, 1):
                if 0 <= index + step < len(values):
                    child = copy.deepcopy({k: spec[k] for k in ("family", "params", "exits")})
                    child[group][key] = values[index + step]
                    if lab._valid_spec(child) and lab.spec_id(child) not in seen:
                        seen.add(lab.spec_id(child))
                        out.append(child)
    return out


def neighbour_share(market: lab.Market, spec: dict) -> Optional[dict]:
    rows = neighbours(spec)
    if not rows:
        return None
    good = 0
    for child in rows:
        record = lab.evaluate_candidate(market, child, with_holdout=False)
        s, v = record["search"], record["validation"]
        if ((s.get("total_return_pct") or 0) > 0 and (v.get("total_return_pct") or 0) > 0
                and lab._profit_factor(s) >= 1.0 and lab._profit_factor(v) >= 1.0):
            good += 1
    return {"variants": len(rows), "profitable": good, "share": round(good / len(rows), 3)}


def gather_evidence(market: lab.Market, spec: dict, n_trials: int, sr_variance: float) -> dict:
    record = lab.evaluate_candidate(market, spec, with_holdout=True)
    verdict = lab.holdout_verdict(record, n_trials, sr_variance)
    orders = lab.strategy_orders(market.ind, spec)
    trades = {split: market.simulate(spec, split, orders) for split in ("search", "validation", "holdout")}
    out_of_sample = [t["net_pct"] for t in trades["validation"] + trades["holdout"]]
    return {
        "record": record, "verdict": verdict,
        "monte_carlo": monte_carlo(out_of_sample),
        "rolling": rolling_consistency(trades["search"] + trades["validation"] + trades["holdout"]),
        "neighbours": neighbour_share(market, spec),
    }


def classify(evidence: dict) -> tuple[Optional[str], list[str]]:
    """Book status and the reasons it is not higher."""
    record, verdict = evidence["record"], evidence["verdict"] or {}
    holdout = record.get("holdout") or {}
    mc, rolling, nb = evidence.get("monte_carlo"), evidence.get("rolling"), evidence.get("neighbours")
    missing = []
    if not record.get("validated"):
        missing.append("fails search/validation gates: " + "; ".join(record.get("gate_reasons") or []))
    if not verdict.get("passed"):
        failed = [name for name, ok in (verdict.get("checks") or {}).items() if not ok]
        missing.append("holdout checks failed: " + ", ".join(failed or ["no holdout"]))
    if not mc or mc["loss_probability"] > BOOK_RULES["mc_max_loss_probability"]:
        missing.append(f"Monte Carlo loss probability {mc['loss_probability'] if mc else 'n/a'} > {BOOK_RULES['mc_max_loss_probability']}")
    if not nb or nb["share"] < BOOK_RULES["min_neighbour_share"]:
        missing.append(f"neighbour share {nb['share'] if nb else 'n/a'} < {BOOK_RULES['min_neighbour_share']}")
    if not rolling or rolling["positive_share"] < BOOK_RULES["min_rolling_share"]:
        missing.append(f"rolling 12-month share {rolling['positive_share'] if rolling else 'n/a'} < {BOOK_RULES['min_rolling_share']}")
    if not missing:
        return "approved_for_demo", []
    if (record.get("validated") and (holdout.get("trades") or 0) >= BOOK_RULES["watch_min_holdout_trades"]
            and lab._profit_factor(holdout) >= BOOK_RULES["watch_min_holdout_pf"] and (holdout.get("total_return_pct") or 0) > 0):
        return "watchlist", missing
    return None, missing


# --------------------------------------------------------------------------- presets
def preset_text(spec: dict, market_key: str, entry: Optional[dict] = None) -> Optional[str]:
    """SwingTrendPullback.mq5 inputs for a gold EMA-pullback strategy (None for other families or symbols)."""
    symbol, timeframe = market_key.split(":")
    if spec["family"] != "ema_pullback" or symbol.upper() != "XAUUSD":
        return None
    p, x = spec["params"], spec["exits"]
    entry = entry or {}
    hold = ((entry.get("latest") or {}).get("holdout") or {})
    header = [
        f"; SwingTrendPullback preset from the Strategy Lab book: {market_key} strategy {lab.spec_id(spec)}",
        f"; {lab.describe_spec(spec)}",
        f"; status {entry.get('status', 'n/a')}; holdout {hold.get('trades')} trades, PF {hold.get('profit_factor')}, "
        f"return {hold.get('total_return_pct')}% (costs incl. swap); generated {lab._iso(pd.Timestamp.now(tz='UTC'))} UTC",
        f"; Attach to XAUUSD {TIMEFRAME_LABELS.get(timeframe, timeframe)}. Research result, not a promise: demo account first.",
    ]
    rr = float(x.get("rr") or 0)
    trail = float(x.get("trail_atr") or 0)
    values = {
        "MagicNumber": 996611, "TradeSide": {"long": 0, "short": 1, "both": 2}[p["side"]],
        "EMAFastLen": p["ema_fast"], "EMASlowLen": p["ema_slow"], "EMASlopeLookback": p["slope_lookback"],
        "TrendFilterEmaLen": p.get("trend_ema", 0) or 0, "PushLookback": p["push_lookback"], "PushAtrMult": p["push_atr"],
        "PullbackTolMult": p["pullback_tol"], "RequireBullClose": bool(p["bull_close"]),
        "UseAdxFilter": False, "AdxLen": 14, "AdxThreshold": 20.0,
        "UseRsiFilter": bool(p.get("rsi_min")), "RsiLen": 14, "RsiMin": float(p.get("rsi_min") or 40), "MaxSpreadPoints": 0,
        "AtrLen": int(p.get("atr_len", 14)), "StopMode": 1 if x["stop"] == "atr" else 0, "AtrSLMult": x["sl_atr"],
        "UseTakeProfit": rr > 0, "UseFixedRR": True, "RrRatio": rr if rr > 0 else 2.0, "AtrTPMult": 3.0,
        "SwingLookback": int(x.get("swing_lookback") or 5),
        "LotMode": 0, "RiskPercent": 1.0, "FixedLot": 0.01, "MaxLot": 1.0, "MinLotMaxRiskPct": 0.0,
        "Tp1AtrMult": 1.5, "Tp3AtrMult": 4.5,
        "UseTrailingStop": trail > 0, "TrailAtrMult": trail if trail > 0 else 3.5,
        "UseMaxBarsExit": True, "MaxBarsInTrade": int(x["max_bars"]),
        "SlippagePoints": 30, "RetrySeconds": 60, "ExportForDashboard": True,
        "UsePopupAlerts": True, "UsePushNotifications": False, "ShowDashboard": True,
        "DashCorner": 0, "DashX": 8, "DashY": 8, "UseDateFilter": False,
    }
    body = [f"{k}={str(v).lower() if isinstance(v, bool) else v}" for k, v in values.items()]
    return "\r\n".join(header + body) + "\r\n"


def _write_presets(entry: dict, write_mt5: bool) -> dict:
    text = preset_text(entry["spec"], entry["market"], entry)
    if not text:
        return {}
    symbol, timeframe = entry["market"].split(":")
    name = f"SwingTrendPullback_LAB_{symbol}_{TIMEFRAME_LABELS.get(timeframe, timeframe)}_{entry['id']}.set"
    PRESET_DIR.mkdir(parents=True, exist_ok=True)
    (PRESET_DIR / name).write_text(text, encoding="ascii", errors="replace")
    written = {"file": str(PRESET_DIR / name)}
    if write_mt5 and entry.get("status") == "approved_for_demo" and MT5_PRESET_DIR.is_dir():
        (MT5_PRESET_DIR / name).write_text(text, encoding="ascii", errors="replace")
        written["mt5_file"] = str(MT5_PRESET_DIR / name)
    return written


# --------------------------------------------------------------------------- book storage
def load_book(path: Optional[Path] = None) -> dict:
    path = Path(path or BOOK_PATH)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"version": 1, "rules": BOOK_RULES, "entries": {}, "updates": []}


def save_book(book: dict, path: Optional[Path] = None) -> None:
    path = Path(path or BOOK_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(book, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _entry_rank(entry: dict) -> tuple:
    """Best first: deflated Sharpe, then lower Monte Carlo loss chance, then holdout profit factor and trade count."""
    latest = entry.get("latest") or {}
    holdout = latest.get("holdout") or {}
    loss = (latest.get("monte_carlo") or {}).get("loss_probability")
    return (latest.get("deflated_sharpe") or 0.0, -(loss if loss is not None else 1.0),
            holdout.get("profit_factor") or 0.0, holdout.get("trades") or 0)


def cap_watchlist(book: dict, per_market: int = WATCHLIST_PER_MARKET) -> dict:
    """Keep the best ``per_market`` watchlist strategies of each market active; archive the rest.

    Archived entries stay in the book with their evidence and history (nothing is deleted); they are no longer
    re-checked every hour, not used as search parents, and never re-added as new. Approved strategies are never archived.
    """
    by_market: dict[str, list] = {}
    for entry in book.get("entries", {}).values():
        if entry.get("status") == "watchlist":
            by_market.setdefault(entry.get("market"), []).append(entry)
    stamp = lab._iso(pd.Timestamp.now(tz="UTC"))
    archived: dict[str, int] = {}
    for market, entries in by_market.items():
        entries.sort(key=_entry_rank, reverse=True)
        for entry in entries[per_market:]:
            entry["status"] = "archived"
            entry["archived_at"] = stamp
            entry["archived_reason"] = f"outside the best {per_market} watchlist strategies of {market}"
            archived[market] = archived.get(market, 0) + 1
    return archived


def _result_signature(entry: dict) -> Optional[tuple]:
    """Identical trades in every period give identical numbers; None when an entry lacks the evidence to compare."""
    latest = entry.get("latest") or {}
    parts = []
    for period in ("search", "validation", "holdout"):
        summary = latest.get(period) or {}
        values = tuple(summary.get(k) for k in ("trades", "profit_factor", "total_return_pct", "max_drawdown_pct"))
        if values[0] is None:
            return None
        parts.append(values)
    return (entry.get("market"), entry.get("family"), *parts)


def dedupe_watchlist(book: dict) -> dict:
    """Archive watchlist strategies whose search, validation and holdout results are identical to a better-ranked one.

    Settings that never change a single trade (e.g. a push length or a trend filter that is always true) produce copies.
    The best-ranked copy stays (earliest added on a tie); approved strategies are never archived; nothing is deleted.
    """
    groups: dict[tuple, list] = {}
    for entry in book.get("entries", {}).values():
        if entry.get("status") in ("watchlist", "approved_for_demo"):
            signature = _result_signature(entry)
            if signature is not None:
                groups.setdefault(signature, []).append(entry)
    stamp = lab._iso(pd.Timestamp.now(tz="UTC"))
    archived: dict[str, int] = {}
    for entries in groups.values():
        if len(entries) < 2:
            continue
        entries.sort(key=lambda e: (e.get("status") == "approved_for_demo", _entry_rank(e),
                                    [-ord(ch) for ch in str(e.get("first_added") or "")], str(e.get("id"))), reverse=True)
        keeper = entries[0]
        for entry in entries[1:]:
            if entry.get("status") != "watchlist":
                continue
            entry["status"] = "archived"
            entry["archived_at"] = stamp
            entry["archived_reason"] = f"duplicate of {keeper.get('id')}: identical search, validation and holdout results"
            entry["duplicate_of"] = keeper.get("id")
            archived[entry.get("market")] = archived.get(entry.get("market"), 0) + 1
    return archived


LEADER_HISTORY_KEPT = 1000


def market_leaders(book: dict) -> dict:
    """The best non-archived strategy of each market (same ranking as the watchlist cap) with its key evidence."""
    best: dict[str, dict] = {}
    for entry in book.get("entries", {}).values():
        if entry.get("status") == "archived" or not entry.get("latest"):
            continue
        market = entry.get("market")
        if market not in best or _entry_rank(entry) > _entry_rank(best[market]):
            best[market] = entry
    leaders = {}
    for market, entry in sorted(best.items()):
        latest = entry["latest"]
        holdout = latest.get("holdout") or {}
        leaders[market] = {"id": entry.get("id"), "status": entry.get("status"), "deflated_sharpe": latest.get("deflated_sharpe"),
                           "n_trials": latest.get("n_trials"), "holdout_pf": holdout.get("profit_factor"),
                           "holdout_trades": holdout.get("trades"), "data_end": latest.get("data_end")}
    return leaders


def record_leaders(book: dict, at: str) -> None:
    """Append this update's per-market leaders so the best deflated Sharpe can be followed run over run."""
    book["leader_history"] = (book.get("leader_history") or [])[-(LEADER_HISTORY_KEPT - 1):] + [
        {"at": at, "markets": market_leaders(book)}]


def leader_trend(book: dict, points: int = 48) -> dict:
    """Per market: latest best deflated Sharpe, change since the previous update, whether the leader changed, recent series."""
    trend: dict[str, dict] = {}
    for row in book.get("leader_history") or []:
        for market, leader in (row.get("markets") or {}).items():
            trend.setdefault(market, {"series": []})["series"].append(
                {"at": row.get("at"), "id": leader.get("id"), "deflated_sharpe": leader.get("deflated_sharpe"),
                 "n_trials": leader.get("n_trials")})
    for market, item in trend.items():
        series = item["series"]
        last, prev = series[-1], (series[-2] if len(series) > 1 else None)
        dsr, prev_dsr = last.get("deflated_sharpe"), (prev or {}).get("deflated_sharpe")
        item.update({"latest_deflated_sharpe": dsr, "latest_id": last.get("id"), "updates": len(series),
                     "change_since_previous": round(dsr - prev_dsr, 4) if dsr is not None and prev_dsr is not None else None,
                     "leader_changed": bool(prev) and prev.get("id") != last.get("id"),
                     "best_ever": max((p["deflated_sharpe"] for p in series if p.get("deflated_sharpe") is not None), default=None)})
        item["series"] = series[-points:]
    return trend


def book_specs(market_key: str, path: Optional[Path] = None) -> list[dict]:
    """Specs of approved and watchlist strategies for one market (parents for the search's mutations)."""
    try:
        entries = load_book(path).get("entries", {}).values()
    except (OSError, ValueError):
        return []
    return [e["spec"] for e in entries if e.get("market") == market_key and e.get("status") in ("approved_for_demo", "watchlist")]


# --------------------------------------------------------------------------- rescore and update
def rescore_market(registry: dict, market: lab.Market, deadline: Optional[float] = None,
                   on_progress: Optional[Callable] = None) -> dict:
    """Re-evaluate stored candidates of one market that were scored under an older cost model (never deletes).

    ``on_progress`` is called every 25 candidates (keeps the run lock's heartbeat fresh during long re-scores).
    """
    done = skipped = 0
    for key, record in list(registry["candidates"].items()):
        if record.get("market") != market.key or record.get("tag"):
            continue
        if record.get("cost_model") == market.info["cost_model"]:
            continue
        if deadline is not None and time.monotonic() > deadline:
            skipped += 1
            continue
        fresh = lab.evaluate_candidate(market, record["spec"], with_holdout=bool(record.get("holdout")))
        fresh["previous_cost_model"] = record.get("cost_model") or "spread-only"
        fresh["previous"] = {split: {k: (record.get(split) or {}).get(k) for k in ("trades", "profit_factor", "total_return_pct")}
                             for split in ("search", "validation", "holdout") if record.get(split)}
        fresh["rescored_at"] = lab._iso(pd.Timestamp.now(tz="UTC"))
        if not fresh["validated"]:
            fresh["status"] = "rejected_after_rescore"
        registry["candidates"][key] = fresh
        done += 1
        if on_progress is not None and done % 25 == 0:
            on_progress()
    return {"rescored": done, "remaining": skipped}


def _take_lock(status_path: Optional[Path], task: str) -> tuple[Optional[dict], dict]:
    """(previous status, lock) or (None, reason) when a Strategy Lab run is active."""
    status = lab.read_status(status_path)
    if lab._run_is_active(status):
        return None, {"skipped": True, "reason": "a Strategy Lab run is active"}
    lock = {"state": "running", "task": task, "pid": os.getpid(),
            "started_at": lab._iso(pd.Timestamp.now(tz="UTC")), "heartbeat": lab._iso(pd.Timestamp.now(tz="UTC"))}
    lab.write_status(lock, status_path)
    return status, lock


def _beat(lock: dict, status_path: Optional[Path]) -> None:
    lock["heartbeat"] = lab._iso(pd.Timestamp.now(tz="UTC"))
    lab.write_status(lock, status_path)


def _release(previous: dict, status_path: Optional[Path], key: str) -> None:
    restored = dict(previous) if previous.get("state") != "running" else {"state": "idle"}
    restored[key] = lab._iso(pd.Timestamp.now(tz="UTC"))
    lab.write_status(restored, status_path)


def _snapshot(evidence: dict, status: Optional[str], reasons: list[str]) -> dict:
    record, verdict = evidence["record"], evidence["verdict"] or {}
    return {
        "checked_at": lab._iso(pd.Timestamp.now(tz="UTC")), "data_end": record.get("data_end"), "status": status,
        "cost_model": record.get("cost_model"),
        "search": {k: record["search"].get(k) for k in ("trades", "profit_factor", "total_return_pct", "max_drawdown_pct")},
        "validation": {k: record["validation"].get(k) for k in ("trades", "profit_factor", "total_return_pct", "max_drawdown_pct")},
        "holdout": {k: (record.get("holdout") or {}).get(k) for k in ("trades", "win_rate_pct", "profit_factor", "total_return_pct",
                                                                        "max_drawdown_pct", "buy_and_hold_pct", "period")},
        "deflated_sharpe": verdict.get("deflated_sharpe"), "n_trials": verdict.get("n_trials"),
        "holdout_checks": verdict.get("checks"), "monte_carlo": evidence.get("monte_carlo"),
        "rolling": evidence.get("rolling"), "neighbours": evidence.get("neighbours"), "missing": reasons,
    }


def update_book(markets: Optional[list] = None, minutes: float = 20.0, loader: Optional[Callable] = None,
                registry_path: Optional[Path] = None, book_path: Optional[Path] = None, status_path: Optional[Path] = None,
                write_mt5_presets: bool = True, now=None) -> dict:
    started = time.monotonic()
    deadline = started + minutes * 60
    status, lock = _take_lock(status_path, "strategy_book")
    if status is None:
        return lock
    registry = lab.load_registry(registry_path)
    book = load_book(book_path)
    loader = loader or lab._default_loader
    markets = markets or sorted(registry["markets"])
    report = {"markets": {}, "problems": {}}
    try:
        for key in markets:
            symbol, timeframe = key.split(":")
            market_key = f"{symbol.upper()}:{timeframe}"
            try:
                meta = registry["markets"].get(market_key) or {}
                market = lab.Market(symbol, timeframe, loader(symbol, timeframe), boundaries=meta.get("boundaries"), now=now)
            except Exception as exc:  # a market without data is reported, the others still update
                report["problems"][market_key] = str(exc)
                continue
            _beat(lock, status_path)
            market_report = {"rescore": rescore_market(registry, market, deadline, on_progress=lambda: _beat(lock, status_path))}
            lab.save_registry(registry, registry_path)
            meta = registry["markets"].get(market_key) or {}
            trials = int(meta.get("candidates_tried") or 1)
            variance = lab._variance(meta.get("validation_srs") or [])
            known_ids = {e["id"] for e in book["entries"].values() if e.get("market") == market_key}
            existing = {e["id"]: e for e in book["entries"].values()
                        if e.get("market") == market_key and e.get("status") != "archived"}  # archived: kept, not re-checked
            fresh = [rec for rec in registry["candidates"].values()
                     if rec.get("market") == market_key and not rec.get("tag") and rec.get("validated") and rec.get("holdout")
                     and rec["id"] not in known_ids and rec.get("cost_model") == market.info["cost_model"]]
            fresh.sort(key=lambda rec: (bool((lab._public_record(registry, rec).get("holdout_verdict") or {}).get("passed")),
                                        lab._profit_factor(rec["holdout"]), rec.get("score") or -999), reverse=True)
            to_check = list(existing.values()) + [{"id": rec["id"], "spec": rec["spec"], "market": market_key, "new": True}
                                                  for rec in fresh[:BOOK_RULES["candidates_per_market"]]]
            counts = {"checked": 0, "added": 0, "approved_for_demo": 0, "watchlist": 0, "demoted": 0}
            for item in to_check:
                if time.monotonic() > deadline:
                    break
                # THE TRIAL COUNT BELONGS TO THE STRATEGY, NOT TO THE CLOCK.
                #
                # This used to pass the market's CURRENT candidates_tried to every entry, including
                # ones selected thousands of trials ago. Measured on 22 Sep 2026, the BTCUSD:1h
                # leader's deflated Sharpe fell 0.4786 -> 0.3305 over fourteen hourly updates with
                # its holdout profit factor fixed at 1.763 on the same 36 trades. Nothing about the
                # strategy changed; the Lab had simply run 8,423 more candidates around it, and the
                # deflation penalty grew for a selection those searches played no part in.
                #
                # Deflated Sharpe's N is the number of trials that COMPETED FOR THIS SELECTION.
                # Searches performed after a strategy was chosen cannot have biased choosing it, so
                # counting them overstates N. This is a correction to a mis-specified figure, not a
                # relaxation: the 0.95 bar is untouched, a freshly selected candidate still carries
                # the full current count, and an entry's count is frozen the first time it is seen
                # rather than back-dated, so nothing already in the book is retroactively flattered.
                existing_entry = book["entries"].get(f"{market_key}|{item['id']}")
                entry_trials = int((existing_entry or {}).get("trials_at_selection") or trials)
                evidence = gather_evidence(market, item["spec"], entry_trials, variance)
                status_now, reasons = classify(evidence)
                counts["checked"] += 1
                book_key = f"{market_key}|{item['id']}"
                entry = book["entries"].get(book_key)
                if entry is None:
                    if status_now is None:
                        continue
                    entry = {"id": item["id"], "market": market_key, "family": item["spec"]["family"], "spec": item["spec"],
                             "description": lab.describe_spec(item["spec"]), "first_added": lab._iso(pd.Timestamp.now(tz="UTC")),
                             # The number of trials that competed for THIS selection, frozen here.
                             "trials_at_selection": trials,
                             "history": []}
                    book["entries"][book_key] = entry
                    counts["added"] += 1
                # Entries that pre-date this field freeze at today's count: conservative, since it
                # keeps the larger penalty they have been carrying rather than back-dating a smaller one.
                entry.setdefault("trials_at_selection", entry_trials)
                previous = entry.get("status")
                entry["status"] = status_now or "demoted"
                if previous in ("approved_for_demo", "watchlist") and status_now is None:
                    entry["demoted_at"] = lab._iso(pd.Timestamp.now(tz="UTC"))
                entry["latest"] = _snapshot(evidence, entry["status"], reasons)
                entry["history"] = (entry.get("history") or [])[-199:] + [
                    {k: entry["latest"][k] for k in ("checked_at", "data_end", "status", "deflated_sharpe")}
                    | {"holdout_trades": entry["latest"]["holdout"].get("trades"),
                       "holdout_pf": entry["latest"]["holdout"].get("profit_factor"),
                       "holdout_return_pct": entry["latest"]["holdout"].get("total_return_pct"),
                       "mc_loss_probability": (entry["latest"]["monte_carlo"] or {}).get("loss_probability")}]
                entry["presets"] = _write_presets(entry, write_mt5_presets)
                counts[entry["status"]] = counts.get(entry["status"], 0) + 1
                _beat(lock, status_path)
            duplicates = dedupe_watchlist(book)
            counts["duplicates_archived"] = duplicates.get(market_key, 0)
            archived = cap_watchlist(book)
            counts["archived"] = archived.get(market_key, 0)
            market_report["book"] = counts
            report["markets"][market_key] = market_report
            save_book(book, book_path)
    finally:
        finished = lab._iso(pd.Timestamp.now(tz="UTC"))
        book["updated_at"] = finished
        book["rules"] = BOOK_RULES
        try:
            record_leaders(book, finished)
        except Exception as exc:  # the trend is monitoring only; never block saving the book
            report["problems"]["leader_history"] = str(exc)
        book["updates"] = (book.get("updates") or [])[-99:] + [{"finished_at": finished, **report,
                                                                 "minutes": round((time.monotonic() - started) / 60, 1)}]
        save_book(book, book_path)
        _release(status, status_path, "last_book_update")
    report["minutes"] = round((time.monotonic() - started) / 60, 1)
    return report


def book_summary(path: Optional[Path] = None) -> dict:
    book = load_book(path)
    entries = sorted((e for e in book.get("entries", {}).values() if e.get("status") != "archived"),
                     key=lambda e: ({"approved_for_demo": 0, "watchlist": 1, "demoted": 2}.get(e.get("status"), 3),
                                    -((e.get("latest") or {}).get("holdout") or {}).get("profit_factor", 0) if
                                    ((e.get("latest") or {}).get("holdout") or {}).get("profit_factor") is not None else 0))
    counts: dict[str, int] = {}
    for entry in book.get("entries", {}).values():  # archived entries are counted but not listed
        counts[entry.get("status") or "unknown"] = counts.get(entry.get("status") or "unknown", 0) + 1
    return {"updated_at": book.get("updated_at"), "rules": book.get("rules") or BOOK_RULES, "counts": counts,
            "entries": [{k: v for k, v in e.items() if k != "history"} | {"history": (e.get("history") or [])[-30:]}
                        for e in entries],
            "last_update": (book.get("updates") or [None])[-1], "leader_trend": leader_trend(book)}


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(description="Strategy book: keep the proven strategies (research only).")
    sub = parser.add_subparsers(dest="command", required=True)
    update = sub.add_parser("update", help="re-score, gather evidence and update the book")
    update.add_argument("--markets", nargs="+", default=None)
    update.add_argument("--minutes", type=float, default=20.0)
    update.add_argument("--no-mt5-presets", action="store_true")
    rescore = sub.add_parser("rescore", help="only re-score stored candidates under the current cost model")
    rescore.add_argument("--markets", nargs="+", default=None)
    rescore.add_argument("--minutes", type=float, default=60.0)
    cap = sub.add_parser("cap", help=f"archive watchlist strategies outside the best {WATCHLIST_PER_MARKET} per market")
    cap.add_argument("--per-market", type=int, default=WATCHLIST_PER_MARKET)
    sub.add_parser("dedupe", help="archive watchlist strategies with results identical to a better-ranked one")
    sub.add_parser("status", help="print the book summary")
    args = parser.parse_args(argv)
    if args.command == "dedupe":
        previous, lock = _take_lock(None, "strategy_book_dedupe")
        if previous is None:
            print(json.dumps(lock))
            return
        try:
            book = load_book()
            archived = dedupe_watchlist(book)
            save_book(book)
            print(json.dumps({"duplicates_archived": archived, "counts": book_summary()["counts"]}, indent=1))
        finally:
            _release(previous, None, "last_book_dedupe")
        return
    if args.command == "cap":
        previous, lock = _take_lock(None, "strategy_book_cap")
        if previous is None:
            print(json.dumps(lock))
            return
        try:
            book = load_book()
            archived = cap_watchlist(book, args.per_market)
            save_book(book)
            print(json.dumps({"archived": archived, "counts": book_summary()["counts"]}, indent=1))
        finally:
            _release(previous, None, "last_book_cap")
        return
    if args.command == "update":
        print(json.dumps(update_book(args.markets, minutes=args.minutes, write_mt5_presets=not args.no_mt5_presets),
                         indent=1, default=str))
    elif args.command == "rescore":
        previous, lock = _take_lock(None, "strategy_book_rescore")
        if previous is None:
            print(json.dumps(lock))
            return
        try:
            registry = lab.load_registry()
            deadline = time.monotonic() + args.minutes * 60
            for key in args.markets or sorted(registry["markets"]):
                symbol, timeframe = key.split(":")
                meta = registry["markets"].get(f"{symbol.upper()}:{timeframe}") or {}
                _beat(lock, None)
                market = lab.Market(symbol, timeframe, lab._default_loader(symbol, timeframe), boundaries=meta.get("boundaries"))
                print(key, rescore_market(registry, market, deadline, on_progress=lambda: _beat(lock, None)), flush=True)
                lab.save_registry(registry)
        finally:
            _release(previous, None, "last_rescore")
    else:
        summary = book_summary()
        print(json.dumps({"updated_at": summary["updated_at"], "counts": summary["counts"],
                          "entries": [(e["market"], e["status"], e["description"],
                                       ((e.get("latest") or {}).get("holdout") or {}).get("profit_factor"))
                                      for e in summary["entries"][:20]]}, indent=1, default=str))


if __name__ == "__main__":
    main()
