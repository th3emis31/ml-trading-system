"""Combine edge-research pieces that were run one model / label setup at a time.

This PC cannot hold a full research run (3 models x 3 label setups x 8 folds) in
memory alongside the live app and MetaTrader, so runs are split into pieces with
``python -m src.edge_research --models rf --configs tight ...``. Every piece
stores, for each of its candidates, the validation metrics used for selection and
the holdout metrics kept apart from it.

``combine_reports`` pools the candidates of pieces that share the same symbol,
interval, data window and feature set, ranks them with the same
``rank_candidates`` used by full runs (validation only), and reports the chosen
candidate's holdout, its baselines and the pass/fail verdict - the same answer a
single full run would give.
"""
from __future__ import annotations

import argparse
import glob
import json
from datetime import datetime, timezone
from pathlib import Path

from .edge_research import RESEARCH_DIR, candidate_key, evaluate_pass, rank_candidates

MATCH_KEYS = ("symbol", "interval", "bars", "data_start", "data_end", "mtf", "rocket", "validation_folds", "holdout_folds")


def combine_reports(reports: list[dict], selection: str = "robust") -> dict:
    pieces = [r for r in reports if r.get("available") and r.get("candidates_all") is not None]
    if not pieces:
        raise ValueError("no usable pieces: run edge_research pieces first (they must include candidates_all)")
    reference = pieces[0]
    for piece in pieces[1:]:
        mismatched = [k for k in MATCH_KEYS if piece.get(k) != reference.get(k)]
        if mismatched:
            raise ValueError(f"pieces do not share the same data window / setup: {mismatched}")

    candidates, holdouts, baselines_by_config, seen = [], {}, {}, set()
    for piece in pieces:
        baselines_by_config.update(piece.get("baselines_by_config") or {})
        for candidate in piece["candidates_all"]:
            key = candidate_key(candidate)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
            holdouts[key] = (piece.get("candidate_holdouts") or {}).get(key)

    ranked = rank_candidates(candidates, reference["validation_folds"], selection)
    chosen = ranked[0]
    holdout = holdouts.get(candidate_key(chosen)) or {}
    return {
        "combined": True,
        "symbol": reference["symbol"],
        "interval": reference["interval"],
        "data_source": reference.get("data_source"),
        "data_start": reference.get("data_start"),
        "data_end": reference.get("data_end"),
        "bars": reference.get("bars"),
        "mtf": reference.get("mtf"),
        "selection_method": selection,
        "pieces": len(pieces),
        "configurations_tried": len(candidates),
        "validation_folds": reference["validation_folds"],
        "holdout_folds": reference["holdout_folds"],
        "selection": {"chosen": chosen, "top_validation": ranked[:8]},
        "holdout": {"metrics": holdout, "verdict": evaluate_pass(holdout)},
        "baselines_holdout": baselines_by_config.get(chosen["config"]),
        "ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }


def find_pieces(symbol: str, interval: str, mtf: bool, rocket: bool = False) -> list[dict]:
    pattern = str(RESEARCH_DIR / f"edge_{symbol.lower()}_{interval}_piece-*.json")
    reports = []
    for path in sorted(glob.glob(pattern)):
        # file stem: edge_<symbol>_<interval>_piece-<models>-<configs>[-mtf][-rocket]_<stamp>
        flags = Path(path).name.rsplit("_", 1)[0].split("_piece-", 1)[-1].split("-")
        if ("mtf" in flags) != mtf or ("rocket" in flags) != rocket:
            continue
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        report["_path"] = path
        reports.append(report)
    if not reports:
        return []
    # Keep only pieces built on the newest data window, so an old piece never mixes in.
    newest = max(r.get("data_end") or "" for r in reports)
    return [r for r in reports if r.get("data_end") == newest]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Combine research pieces into one validation-selected result.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", required=True)
    parser.add_argument("--mtf", action="store_true")
    parser.add_argument("--rocket", action="store_true", help="combine the ROCKET-feature pieces")
    parser.add_argument("--selection", default="robust", choices=["best", "robust"])
    args = parser.parse_args()
    pieces = find_pieces(args.symbol, args.interval, args.mtf, args.rocket)
    print(f"pieces found: {len(pieces)}")
    for piece in pieces:
        print("  ", piece["_path"], "| candidates", len(piece.get("candidates_all") or []))
    result = combine_reports(pieces, selection=args.selection)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = RESEARCH_DIR / f"combined_{args.symbol.lower()}_{args.interval}{'_mtf' if args.mtf else ''}{'_rocket' if args.rocket else ''}_{stamp}.json"
    out.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    print("saved", out)
    print("chosen on validation:", result["selection"]["chosen"])
    print("HOLDOUT:", result["holdout"]["metrics"])
    print("verdict:", result["holdout"]["verdict"])
    print("baselines:", result["baselines_holdout"])
