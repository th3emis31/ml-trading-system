---
name: strategy
description: Write, review, or refine a trading strategy as a testable hypothesis document before any code. Use when the user says strategy, new idea, entry rules, exit rules, edge, setup, or asks whether an idea is worth building.
---

# Strategy document

A strategy is code only after it is a document. Create or update `strategies/<name>.md`
with exactly these sections (keep each short and concrete):

1. **Hypothesis** — one sentence: what inefficiency and why it should persist.
2. **Instruments and timeframe** — symbols, bar size, sessions allowed.
3. **Entry rules** — precise, computable conditions; direction; grade/confidence inputs.
4. **Exit rules** — stop loss, take profits, time stop, trailing rules, invalidation.
5. **Risk** — risk per trade %, max open positions, max daily loss, correlation limits.
6. **Filters** — regime, session, news/volatility, market-condition gate (no entries on AVOID).
7. **Model inputs** (if ML) — feature list, label definition, horizon, leakage review.
8. **Success metric** — the one metric that decides promotion (e.g. net expectancy > 0.2R
   with max DD < 10 % over ≥ 100 OOS trades) and the baseline to beat.
9. **Known risks / failure modes** — when it should stop trading.
10. **Status** — idea → backtested → paper → live, with dates and commit hashes.

## Review mode
When asked to review a strategy: score each section 0–2 (missing / vague / testable), list
the top three gaps, and propose the smallest experiment (a `/backtest`) that would falsify
the hypothesis. Do not start coding until sections 1–8 are testable.

## Duplication guard
Before creating a strategy file, `ls strategies/` and grep the codebase for the same idea.
Extend an existing document rather than creating a near-duplicate.
