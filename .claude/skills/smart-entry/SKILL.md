---
name: smart-entry
family: trading
description: Design, review, or improve the automated smart entry system (entry/SL/TP, risk sizing, regime and session gating, confidence, execution safety). Use when the user mentions entries, signals, execution, risk, SL/TP, orders, or "smart entry".
---

# Smart entry system

First locate the code: grep for signal generation, position sizing, order submission, and
any regime/session logic. Record the file paths in `CLAUDE.md` under "What this project is"
if they are missing.

## Entry quality checklist (an automated entry must pass all)
1. **Structure**: direction, entry, stop loss, take profit 1 present and numeric.
2. **Direction sanity**: long → sl < entry < tp1; short → tp1 < entry < sl.
3. **Risk:reward**: |tp1 − entry| / |entry − sl| ≥ 1.5.
4. **Sizing**: from the single sizing function; risk per trade ≤ configured %; never
   hard-coded size.
5. **Regime gate**: no counter-trend entries in a strong trend unless top grade and high
   confidence.
6. **Session gate**: the instrument's allowed/best session, or explicit override.
7. **Market-condition gate**: no automated entries when conditions are rated AVOID.
8. **Confidence**: final confidence adjusted by model/history; automation only above a
   configured threshold.
9. **Correlation / exposure**: no stacked same-direction entries in correlated instruments
   beyond the risk budget; total open risk capped.
10. **Idempotence and safety**: the same signal is never executed twice; a dry-run flag
    exists; every decision is logged with reasons.

## Implementing improvements
- Put rules in pure functions, e.g. `validate_entry(signal, ctx) -> (ok, reasons)` and
  `plan_entry(signal, account, model_state, market) -> enriched signal`.
- Surface reasons (why blocked / approved) in logs and UI. Keep manual entry paths working.
- Add a unit test for every rule you add.

## Review mode
Output a table: rule → PASS/FAIL/N-A → file:line → concrete fix. Then apply the fixes
with `/safe-upgrade` unless told otherwise.

## Acceptance

```acceptance
run: python -m pytest -q tests/test_execute_api_security.py
run: python -m pytest -q tests/test_signal_never_synthetic.py
file: src/execution_guard.py contains side not in
ask: can a non-directional signal reach an execution path anywhere in the change?
```
