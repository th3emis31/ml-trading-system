---
name: train
family: trading
description: Safe model training protocol — leak-free time split, versioned artifacts, evaluation against the baseline, explicit promotion. Use when the user says train, retrain, fit the model, update the model, or improve accuracy.
---

# Train protocol (never overwrite the live model)

Locate the existing training entrypoint first (`grep -rn "def train\|fit(\|model.save\|joblib.dump"`)
and extend it. Do not create a parallel trainer.

## Before training
1. **Data audit**: date range, row count, missing values, duplicate timestamps, class
   balance of the label. Print them and record in NOTES.md.
2. **Leakage review**: every feature computed only from data at or before its bar; label
   uses only future bars; scaling/encoding fitted on the training split only.
3. **Split**: time-ordered train / validation / test. No shuffling across time. Same split
   dates recorded for every run so models are comparable.
4. **Seeds fixed** (numpy, random, framework) and recorded.
5. **Baseline read** from `.claude/memory/BASELINE.md` (current live model metrics).

## Training
6. Save to a **new versioned folder**: `models/<name>_<YYYYMMDD-HHMMSS>/` containing the
   model file(s), `metrics.json`, `features.json`, `config.json`, `split.json`, and the
   git commit hash. The live model path is untouched.
7. Evaluate on validation for selection and on test **once** for the report.

## After training
8. **Compare** with the baseline on the agreed metric and on drawdown/precision of the
   trading signal (not only accuracy). State: better / worse / not significant.
9. **Promotion is separate**: only when the user agrees (or the strategy doc's success
   metric is met), copy the versioned model to the live path, keep the previous live model
   as `*_previous`, and log the promotion with both hashes in NOTES.md and BASELINE.md.
10. **Ensemble consistency**: confirm the live prediction path and the evaluation path call
    the same ensemble function with the same weights. If not, fix that first.

Never delete old model folders; never train on the test period; never promote silently.

## This repository (live app in C:\Users\th_em)

- Live training entry: `src/daily_learning.py` `DailyLearner.run_cycle()` (buttons on
  `/pipeline/training`, `/api/train-daily`, `/api/train-weekly`, quality retrain, JARVIS
  intents). It is gated by `src/model_promotion.py`: current models are archived to
  `models/archive/<symbol>/<stamp>/`, the new RF is scored against the archived one on
  bars it never trained on, and the previous files are restored when it is worse.
  Decisions: `data/learning_decisions.json`. Synthetic data is refused.
- Research models (new labels/features/models) go through `/edge-research`, never
  straight into the live path.
- Tests: `python -m pytest -q tests/test_model_promotion.py` (run from C:\Users\th_em).

## Acceptance

```acceptance
run: python -m pytest -q tests/test_signal_parity.py
appended: data/learning_decisions.json
number: accuracy >= 0.5
ask: was the champion kept when the challenger did not clearly beat it?
```

The promotion gate is the point of this skill. A challenger promoted on a tie is a coin flip
dressed as an improvement.
