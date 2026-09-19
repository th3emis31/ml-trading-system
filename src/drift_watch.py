"""Does the system notice when it is getting worse? Read-only; never trades, never retrains.

On 19 September 2026 the owner asked for nothing to be ignored. Four things were being ignored, and
each is now a check here rather than something a person has to remember to look for.

1. **No baseline.** The daily learning gate records an accuracy — 0.5371 for gold, 0.5639 for
   bitcoin — and compares it with **nothing**. A binary label whose majority class is 0.506 makes
   0.5371 a 3-point edge, while a label at 0.55 would make it worse than guessing the common class
   every time. The number alone cannot say which, so this computes the majority-class rate on the
   same bars and reports the gap. This is the check that would have caught the dead 1h model on its
   own rather than after four hand-run experiments.

2. **A frozen number read as a fresh one.** ``accuracy`` in ``data/learning_decisions.json`` is the
   *champion's* figure from the day it was promoted, not a new measurement. Gold's has been
   identical to sixteen decimal places on 17, 18 and 19 September, because the champion was not
   replaced.

   A correction worth recording, because the first version of this module got it wrong: the
   promotion **decision** is sound. ``model_promotion.evaluate_rf`` re-scores the champion *and* the
   challenger on the same current holdout, so a Yahoo-trained champion is re-tested on broker bars
   every day and can lose. What is stale is only the number that gets **recorded and displayed**.
   The honest figure is already computed daily and kept as ``rf_champion.accuracy`` — on 19 September
   that was 0.5345 for gold against a displayed 0.5371, and 0.5312 for bitcoin against a displayed
   0.5639, an overstatement of 3.3 points. This module reads the re-scored figure.

3. **A silent change of price source.** The learning history switched from Yahoo to broker candles on
   19 September. Accuracies either side of that are not comparable, and a trend drawn across it is an
   artefact.

4. **Backtest promises versus forward reality.** The shadow book now measures 54 strategies forward.
   Where a strategy has forward trades, the gap between what its backtest promised and what it has
   since delivered is the most direct drift signal there is.

Every check reports "not enough evidence yet" when that is the truth, and no check ever guesses.

    python -m src.drift_watch report [--json]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import forward_evidence
from .runtime_paths import smartentry_data_dir

# How far a fresh challenger may sit below the champion's claim before it is worth saying so.
CHALLENGER_SHORTFALL = 0.02
# A champion older than this with no replacement is worth flagging, not because age is a fault but
# because its recorded accuracy stops describing anything current.
STALE_CHAMPION_DAYS = 14
# An edge over the majority class smaller than this is not an edge worth calling one.
MIN_EDGE_OVER_BASELINE = 0.01
SYMBOLS = ("XAUUSD", "BTCUSD")
# What Yahoo actually serves for each symbol, which is not always the same instrument.
PROXY_NOTE = {
    "XAUUSD": ". Yahoo proxies gold with the GC=F future, about 1.4 % away from broker spot, "
              "so the model learned a different instrument from the one it trades",
    "BTCUSD": ". Yahoo serves BTC-USD from its own exchange mix rather than this broker's "
              "book, so the prices differ by spread and venue rather than by a basis",
}


def _decisions(data_dir: Path) -> list:
    raw = forward_evidence._read_json_or_none(data_dir / "learning_decisions.json")
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict):
        rows = raw.get("decisions") or []
        return [r for r in rows if isinstance(r, dict)]
    return []


def majority_class_baseline(symbol: str, interval: str = "1h", bars: int = 3000) -> dict:
    """The rate you get by always guessing the label's common class. The number accuracy must beat.

    Computed on the same broker candles and the same label (``features.build_features``'s ``target``,
    which is "was the 3-bar future move positive") that the learner uses, so it is comparable.
    """
    try:
        from .features import build_features
        from .mtf_data import fetch_app_bars
    except Exception as exc:
        return {"available": False, "reason": f"cannot import the feature builder: {exc}"}
    frame = fetch_app_bars(symbol, interval, bars)
    if frame is None or frame.empty:
        return {"available": False, "reason": "no broker bars"}
    try:
        enriched = build_features(frame)
    except Exception as exc:
        return {"available": False, "reason": f"feature build failed: {exc}"}
    if "target" not in enriched or enriched["target"].empty:
        return {"available": False, "reason": "the frame carries no target column"}
    ups = float(enriched["target"].mean())
    baseline = max(ups, 1.0 - ups)
    return {"available": True, "rows": int(len(enriched)), "positive_rate": round(ups, 4),
            "majority_class_rate": round(baseline, 4), "interval": interval,
            "source": frame.attrs.get("source")}


def accuracy_against_baseline(decisions: list, symbol: str, baseline: dict) -> dict:
    """Is the champion's claimed accuracy actually better than guessing the common class?"""
    rows = [r for r in decisions if r.get("symbol") == symbol
            and (isinstance(r.get("accuracy"), (int, float))
                 or isinstance((r.get("rf_champion") or {}).get("accuracy"), (int, float)))]
    if not rows:
        return {"status": "no evidence", "why": f"no recorded accuracy for {symbol}"}
    latest = rows[-1]
    # Prefer the champion RE-SCORED on the current holdout over the figure frozen at promotion: the
    # re-scored one is measured on the same bars as the baseline below, so the two are comparable.
    rescored = (latest.get("rf_champion") or {}).get("accuracy")
    using_rescored = isinstance(rescored, (int, float))
    accuracy = float(rescored if using_rescored else latest["accuracy"])
    if not baseline.get("available"):
        return {"status": "cannot judge", "accuracy": round(accuracy, 4),
                "why": f"the baseline could not be measured: {baseline.get('reason')}"}
    rate = baseline["majority_class_rate"]
    edge = accuracy - rate
    # The champion's accuracy was measured on whatever source trained it; the baseline here is measured
    # on the broker candles the system feeds. If those differ, the comparison is cross-source and the
    # edge is not a number to act on - saying so is the whole point of this module.
    # ``accuracy`` is the CHAMPION's frozen figure, so its price source is the source of the run that
    # PROMOTED it, not of the most recent run. Reading the latest row's source here would have called a
    # Yahoo-trained accuracy "broker-measured" and reported a clean edge that does not exist.
    if using_rescored:
        # The re-scored accuracy was measured on this run's bars, so this run's source is the one
        # that matters for comparability.
        trained_on = str(latest.get("data_source") or "unknown")
    else:
        promoting = [r for r in decisions if r.get("symbol") == symbol and r.get("rf_promoted")]
        trained_on = str((promoting[-1] if promoting else latest).get("data_source") or "unknown")
    baseline_source = str(baseline.get("source") or "unknown")
    comparable = (trained_on in ("broker", "mt5") or trained_on.startswith("mt5")) and "mt5" in baseline_source
    if not comparable:
        return {"status": "not comparable", "accuracy": round(accuracy, 4), "baseline": rate,
                "edge": round(edge, 4), "trained_on": trained_on, "baseline_source": baseline_source,
                "why": (f"accuracy {accuracy:.4f} was measured on {trained_on} while the majority class "
                        f"{rate:.4f} is measured on {baseline_source}; the apparent edge of {edge:+.4f} "
                        f"compares two different price series and must not be read as an edge"),
                "measured_at": latest.get("trained_at") or latest.get("at")}
    if edge <= 0:
        status, why = "no edge", (f"accuracy {accuracy:.4f} is at or below the majority class "
                                  f"{rate:.4f} - always guessing the common class would do as well")
    elif edge < MIN_EDGE_OVER_BASELINE:
        status, why = "negligible", (f"accuracy {accuracy:.4f} beats the majority class {rate:.4f} by "
                                     f"{edge:+.4f}, which is too small to call an edge")
    else:
        status, why = "has an edge", (f"accuracy {accuracy:.4f} against a majority class of "
                                      f"{rate:.4f}, an edge of {edge:+.4f}")
    return {"status": status, "accuracy": round(accuracy, 4), "baseline": rate,
            "edge": round(edge, 4), "why": why,
            "measured_on": ("re-scored on this run's holdout" if using_rescored
                            else "the figure frozen when the champion was promoted"),
            "displayed_accuracy": latest.get("accuracy"),
            "measured_at": latest.get("trained_at") or latest.get("at")}


def champion_freshness(decisions: list, symbol: str) -> dict:
    """The champion's accuracy is frozen at promotion; challengers are measured fresh every day."""
    rows = [r for r in decisions if r.get("symbol") == symbol]
    if not rows:
        return {"status": "no evidence", "why": f"no learning decisions for {symbol}"}
    latest = rows[-1]
    # Compare challengers against the champion RE-SCORED on the same holdout they were scored on,
    # not against the figure frozen at promotion: the frozen one is a different measurement.
    rescored = (latest.get("rf_champion") or {}).get("accuracy")
    champion = rescored if isinstance(rescored, (int, float)) else latest.get("accuracy")
    displayed = latest.get("accuracy")
    claims = [r.get("accuracy") for r in rows if isinstance(r.get("accuracy"), (int, float))]
    unchanged = 0
    for value in reversed(claims):
        if displayed is not None and abs(value - displayed) < 1e-12:
            unchanged += 1
        else:
            break
    challengers = [float(r["challenger_accuracy"]) for r in rows
                   if isinstance(r.get("challenger_accuracy"), (int, float))][-10:]
    shortfall = None
    if challengers and isinstance(champion, (int, float)):
        below = [c for c in challengers if champion - c > CHALLENGER_SHORTFALL]
        shortfall = {"recent_challengers": len(challengers),
                     "materially_below_champion": len(below),
                     "worst_gap": round(champion - min(challengers), 4)}
    age = latest.get("champion_age_days")
    stale = isinstance(age, (int, float)) and age > STALE_CHAMPION_DAYS
    status = "stale" if stale else ("unchanged" if unchanged >= 3 else "current")
    return {"status": status, "champion_accuracy_rescored": champion, "displayed_accuracy": displayed,
            "champion_age_days": age,
            "identical_records_in_a_row": unchanged, "challengers": shortfall,
            "why": (f"the DISPLAYED accuracy has been identical for {unchanged} runs because it is the "
                    f"figure frozen when the champion was promoted; its re-scored accuracy on the "
                    f"latest holdout is {champion}"
                    if unchanged >= 3 else "the champion was replaced recently, so its figure is recent")}


def champion_source_mismatch(decisions: list, symbol: str, feed_source: Optional[str]) -> dict:
    """Was the champion actually in use trained on the prices the system trades?

    This is the check the owner's remark "system is feeding data from MT5/4" points at, and it is the
    most consequential one here. The feed is MT5, but 37 of the 39 recorded learning runs trained on
    Yahoo, and Yahoo proxies XAUUSD with the GC=F gold future, which carries roughly 1.4 % of basis
    against broker spot. A model fitted on one series and asked to trade another is not stale by age;
    it is describing a different instrument.
    """
    promoted = [r for r in decisions if r.get("symbol") == symbol and r.get("rf_promoted")]
    if not promoted:
        return {"status": "no evidence",
                "why": f"no promoted champion recorded for {symbol}, so its training source is unknown"}
    latest = promoted[-1]
    trained_on = str(latest.get("data_source") or "unknown")
    feed = str(feed_source or "unknown")
    feed_is_broker = feed.startswith("mt5") or feed.startswith("broker") or "mt5" in feed
    trained_on_broker = trained_on in ("broker", "mt5") or trained_on.startswith("mt5")
    if trained_on == "unknown" or feed == "unknown":
        status = "cannot judge"
    elif trained_on_broker == feed_is_broker:
        status = "matched"
    else:
        status = "MISMATCH"
    why = {
        "matched": f"the champion promoted {str(latest.get('trained_at'))[:16]} was trained on "
                   f"{trained_on}, which is the same kind of series the feed serves ({feed})",
        "MISMATCH": f"the champion promoted {str(latest.get('trained_at'))[:16]} was trained on "
                    f"{trained_on} while the system feeds {feed}" + PROXY_NOTE.get(symbol, ""),
        "cannot judge": f"training source {trained_on!r} or feed {feed!r} is not recorded clearly enough",
    }[status]
    return {"status": status, "trained_on": trained_on, "feed": feed,
            "champion_promoted_at": latest.get("trained_at"),
            "champion_accuracy": latest.get("accuracy"), "why": why}


def feed_source(symbol: str = "XAUUSD") -> Optional[str]:
    """What the app is actually serving right now, asked rather than assumed."""
    try:
        from .mtf_data import fetch_app_bars
        frame = fetch_app_bars(symbol, "1h", 60)
    except Exception:
        return None
    if frame is None or frame.empty:
        return None
    return frame.attrs.get("source")


def source_drift(decisions: list) -> dict:
    """A change of price source makes accuracies either side of it incomparable."""
    seen = [r.get("data_source") for r in decisions if r.get("data_source")]
    distinct = sorted(set(seen))
    if len(distinct) <= 1:
        return {"status": "one source", "sources": distinct,
                "why": "every recorded run used the same price source"}
    changes = [(a, b) for a, b in zip(seen, seen[1:]) if a != b]
    return {"status": "source changed", "sources": distinct, "changes": len(changes),
            "latest_change": (changes[-1] if changes else None),
            "why": (f"the learning history spans {len(distinct)} price sources ({', '.join(distinct)}), "
                    f"so accuracies either side of the change are not comparable and a trend drawn "
                    f"across it is an artefact")}


def strategy_drift(data_dir: Path) -> dict:
    """Backtest promise against forward reality, for strategies that have forward trades."""
    shadow = forward_evidence._read_json_or_none(data_dir / "strategy_lab" / "shadow_book.json") or {}
    strategies = shadow.get("strategies") or {}
    rows = []
    for record in strategies.values():
        stats = record.get("stats") or {}
        if not stats.get("trades"):
            continue
        conf = record.get("confidence") or {}
        promised = conf.get("prior_used_r")
        delivered = stats.get("expectancy_r")
        readable = not conf.get("interval_spans_zero", True)
        rows.append({"id": record.get("id"), "market": record.get("market"),
                     "description": record.get("description"),
                     "trades": stats["trades"], "promised_r": promised, "delivered_r": delivered,
                     "gap_r": (round(delivered - promised, 4)
                               if isinstance(promised, (int, float)) and isinstance(delivered, (int, float))
                               else None),
                     "readable": readable})
    readable_rows = [r for r in rows if r["readable"]]
    worse = [r for r in readable_rows if (r["gap_r"] or 0) < 0]
    return {"status": ("no evidence" if not rows else
                       "not readable yet" if not readable_rows else
                       "drifting" if worse else "holding up"),
            "tracked_with_trades": len(rows), "readable": len(readable_rows),
            "delivering_below_promise": len(worse),
            "worst": sorted(readable_rows, key=lambda r: (r["gap_r"] or 0))[:5],
            "why": ("no shadow strategy has a closed trade yet" if not rows else
                    f"{len(rows)} strategies have forward trades but none has an interval clearing zero, "
                    f"so none can be read yet" if not readable_rows else
                    f"{len(worse)} of {len(readable_rows)} readable strategies are delivering below "
                    f"what their backtest promised")}


def collect_drift(data_dir: Optional[Path] = None, with_baseline: bool = True) -> dict:
    root = Path(data_dir) if data_dir is not None else smartentry_data_dir()
    decisions = _decisions(root)
    report = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
              "reads_only": True, "decisions_seen": len(decisions), "models": {},
              "source_drift": source_drift(decisions),
              "strategy_drift": strategy_drift(root)}
    for symbol in SYMBOLS:
        baseline = majority_class_baseline(symbol) if with_baseline else {"available": False,
                                                                         "reason": "not requested"}
        report["models"][symbol] = {"baseline": baseline,
                                    "vs_baseline": accuracy_against_baseline(decisions, symbol, baseline),
                                    "freshness": champion_freshness(decisions, symbol),
                                    "source_match": champion_source_mismatch(decisions, symbol,
                                                                            feed_source(symbol))}
    concerns = []
    for symbol, model in report["models"].items():
        if model["vs_baseline"]["status"] in ("no edge", "negligible", "not comparable"):
            concerns.append(f"{symbol}: {model['vs_baseline']['why']}")
        if model["freshness"]["status"] in ("stale", "unchanged"):
            concerns.append(f"{symbol}: {model['freshness']['why']}")
        if model["source_match"]["status"] == "MISMATCH":
            concerns.append(f"{symbol}: {model['source_match']['why']}")
    if report["source_drift"]["status"] == "source changed":
        concerns.append(report["source_drift"]["why"])
    if report["strategy_drift"]["status"] == "drifting":
        concerns.append(report["strategy_drift"]["why"])
    report["concerns"] = concerns
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Notice when the system is getting worse (read-only)")
    parser.add_argument("command", choices=["report"])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-baseline", action="store_true", help="skip the majority-class measurement")
    args = parser.parse_args(argv)

    report = collect_drift(with_baseline=not args.no_baseline)
    if args.json:
        print(json.dumps(report, indent=1, default=str))
        return 0

    print(f"Drift watch at {report['generated_at']}   ({report['decisions_seen']} learning decisions read)\n")
    for symbol, model in report["models"].items():
        base, vs, fresh = model["baseline"], model["vs_baseline"], model["freshness"]
        print(f"  {symbol}")
        if base.get("available"):
            print(f"     majority class {base['majority_class_rate']:.4f} on {base['rows']:,} bars "
                  f"({base['interval']}, {base['source']})")
        else:
            print(f"     baseline unavailable: {base.get('reason')}")
        print(f"     {vs['status'].upper()}: {vs['why']}")
        print(f"     champion: {fresh['status']} - {fresh['why']}")
        match = model["source_match"]
        print(f"     price source: {match['status']} - {match['why']}")
        if fresh.get("challengers"):
            c = fresh["challengers"]
            print(f"     challengers: {c['materially_below_champion']} of {c['recent_challengers']} "
                  f"landed more than {CHALLENGER_SHORTFALL} below the champion's claim "
                  f"(worst gap {c['worst_gap']})")
    sd, st = report["source_drift"], report["strategy_drift"]
    print(f"\n  price source: {sd['status']} - {sd['why']}")
    print(f"  strategies:   {st['status']} - {st['why']}")
    for row in st.get("worst") or []:
        print(f"     {row['market']:12s} {str(row['description'])[:44]:44s} n {row['trades']:3d} "
              f"promised {row['promised_r']} delivered {row['delivered_r']} gap {row['gap_r']}")
    print(f"\n  CONCERNS: {len(report['concerns'])}")
    for concern in report["concerns"]:
        print(f"     - {concern}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
