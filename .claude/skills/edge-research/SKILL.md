---
name: edge-research
description: One disciplined experiment per iteration to find a real, out-of-sample trading edge for the ML/LSTM models (buy and sell). Use when the user asks to make the models profitable, improve ML/LSTM, find an edge, or run the research loop (e.g. "/loop /edge-research").
---

# Edge research loop (one experiment per run)

The live app is `C:\Users\th_em\` (not this folder). Research code lives there:

- `src/edge_research.py` — triple-barrier labels (buy and sell separately), leak-free
  stationary features, walk-forward with purge, selection on validation folds, one
  application to untouched holdout folds, baselines, pass/fail. Run:
  `cd C:\Users\th_em && python -m src.edge_research --symbols XAUUSD BTCUSD --interval 1h`
  Reports land in `data/research/edge_<symbol>_<interval>_<stamp>.json`.
- `src/walkforward_backtest.py` — backtest of the *current live* strategy (UI:
  `/pipeline/training`, runs stored in `data/backtest_runs.json`).
- `src/model_promotion.py` — the gate that decides whether a retrained live model
  replaces the current one (`data/learning_decisions.json`).
- Tests: `python -m pytest -q tests/test_edge_research.py tests/test_walkforward_backtest.py tests/test_model_promotion.py`

## Pass criteria (agreed with the user, 13 Sep 2026)

On the **holdout** period, for **each** symbol: profit factor ≥ 1.2, ≥ 100 trades,
max drawdown ≤ 20%, and total return above buy & hold after costs. Anything less is
"no edge yet". Never enable `auto_execute` because of research results; that is the
user's call after a strategy passes.

## One iteration

1. **Read memory first**: `.claude/memory/BASELINE.md` (all past research rows),
   `.claude/memory/BACKLOG.md` (Research section), `.claude/memory/LESSONS.md`.
2. **Pick exactly one hypothesis** from the Research backlog (or write one there first):
   one change only — a feature group, a label definition, a model, a regime filter, a
   timeframe. Write the hypothesis and the expected effect in NOTES.md before running.
3. **Change code additively** (new feature list or config beside the old one; never
   remove the previous experiment's path so runs stay comparable).
4. **Run tests, then the research** for both symbols on the same interval as the row
   you compare against.
5. **Record** one row per symbol in BASELINE.md: date, `edge_research`, hypothesis,
   holdout period, trades, win %, PF, expectancy, max DD, Sharpe, return vs buy & hold,
   configurations tried, pass/fail. Never edit old rows.
6. **Decide**: keep the change only if holdout improves without more searching than it
   earns; otherwise note it as a failed hypothesis in BACKLOG.md (so it is not retried).
7. **Stop** and report plainly: what was tested, holdout numbers, pass/fail, next idea.

## Rules that keep results honest

- The holdout is used once per hypothesis. Never tune anything after looking at holdout
  numbers and re-run on the same holdout; if a tweak is inspired by holdout results, it
  needs a *new* holdout (later data) to count.
- Count and report configurations tried; more searching needs stronger evidence.
- Costs always on; entries at the next bar's open; stops gap-adjusted.
- < 100 holdout trades = insufficient evidence, not a result.
- Only a passing strategy may be wired into the app, and then first as a **shadow**
  signal (recorded, never executed) for a forward period before the user decides.
- LSTM work follows the same labels, folds and holdout as the tree models so the
  numbers are directly comparable.
