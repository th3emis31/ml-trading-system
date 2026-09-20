# Shadow calibration learner — design only, 20 September 2026

**Status: DESIGN. No code was written or changed for this document.** It is a plan, a set of
citations, and one finding that changes the order of the work.

---

## 0. The hard guarantee, stated before anything else

**How the design enforces "a good signal is never blocked".**

### Phase 1 (this design) — it is structurally incapable of blocking anything

Not "configured not to". *Incapable.*

1. **No return path to any decision.** The learner is a **sink**. It reads signals and outcomes and
   writes one file. Nothing in the live signal path reads that file. The live path
   (`_build_signal_payload_uncached`, `app.py:6700`) computes `confidence` at `app.py:6030` and the
   autonomy gate compares against `min_confidence_pct` (`app.py:1131`, `1170`, `1209`, `1244`). The
   learner touches **none** of these call sites.
2. **Enforced by test, not by intention.** A test asserts the learner's module name appears nowhere in
   `app.py`'s signal or execution paths, and that its output file is read by no module except its own
   report. If someone later wires it in, that test fails.
3. **No sizing, no fire decision, no gate.** It produces a *number in a file*. Every trade fires,
   full-size, exactly as now. The gate stays where it is.
4. **It cannot starve learning**, because it is downstream of the outcome, not upstream of the trade.

### Phase 2 (a separate future approval) — asymmetric by construction

If it is ever allowed to influence a live decision, these are the design constraints, not preferences:

1. **Up-weighting needs ordinary evidence. Down-weighting needs strong evidence.** The thresholds are
   deliberately unequal: a bucket may be up-weighted on a positive expectancy whose confidence interval
   clears zero. A bucket may only be down-weighted when its expectancy interval is **entirely below
   zero** on at least `MIN_BUCKET_N` resolved outcomes. Ties, thin samples and straddling intervals all
   resolve to **no change**.
2. **A good signal is never down-weighted, by definition of the trigger.** Down-weighting is keyed to
   *proven negative expectancy on the accumulated record*. A signal with real positive expectancy
   cannot satisfy that trigger, so it cannot be down-weighted. The false-bad error the owner named as
   the worst available is excluded by the trigger condition itself, not by tuning.
3. **A floor under every down-weight.** Even a proven-bad bucket is reduced in *weight*, never taken to
   zero and never blocked. On demo, **nothing is blocked at all** — full size, all fire, always. The
   down-weight is advisory metadata until a further, separate approval.
4. **Asymmetric burden stated numerically** so it cannot drift: up-weight requires the lower bound of a
   95 % interval above 0; down-weight requires the **upper** bound below 0 **and** `n >= MIN_BUCKET_N`
   **and** the effect reproduced in a held-out period. Three conditions to harm a signal, one to help it.

---

## 1. The finding that changes the order of the work

**The data described in the brief does not exist in this repository.** Checked, not assumed:

| Named in the brief | Reality here |
|---|---|
| `generateSignalMTF` | **No such symbol.** Zero hits across `*.py`, `*.js`, `*.html` |
| `rejections_scored` | **Does not exist** |
| `near_misses` (ledger) | **No ledger.** A `near_misses()` *function* exists in `src/strategy_lab.py`, added 19 Sep, unrelated |
| `atomic_scored` | **Does not exist** |
| "24,214 accumulated rows" | Not found. See the real count below |
| "gate stays 70" | Nearest real gate is `min_confidence_pct`, **default 68.0** (`app.py:1131`, `1170`, `1209`, `1244`). The live `autonomy` block has **no value set**, so the default applies |

**What actually accumulates with both a confidence and an outcome:**

| Source | Rows | Resolved outcomes |
|---|---|---|
| `data/signals.json` | 49 | **10** — 8 WIN, 2 LOSS; 39 unresolved |
| `data/auto_trader_state.json` (a 12 Sep backup) | 129 | not verified as resolved |
| `data/execution_rejections.jsonl` | 7 | rejections, no outcome by nature |
| `data/strategy_lab/shadow_book.json` | 56 strategies | **0 closed trades** |
| `data/strategy_lab/registry.json` | 9,887 candidates | backtest results, not live signals |

**The blocking fact: ten resolved live signal outcomes.** And every confidence ever recorded sits
between **0.55 and 0.69** — so the brief's test case, "does a shadow-80 earn like an 80?", cannot be
asked, because no signal has ever scored 80.

A calibration curve over 10 points, in a range 0.55–0.69, measures nothing. Building the learner now
would produce a number that looks like learning and is noise — which is the failure this system has
spent the week correcting.

**So the honest order is: fix the data supply first, then build the learner.**

---

## 2. Where it plugs in

The brief's anchor doesn't exist, so here is the real one.

**The existing calibrator, which is what this replaces:** `_jarvis_confidence_calibration`,
`app.py:6196–6216`. Today it:

- reads `_build_signal_performance_results()` (`app.py:2460`), which reads `load_signals()`
- counts wins as `status in {TP1, TP2, TP3}` and losses as `SL` (`app.py:6199–6201`)
- computes `win_rate`, then `adjustment = edge * 0.35 * trust * tf_weight` (`app.py:6205`)
- clamps to `[0.35, 0.95]` (`app.py:6207`)

**Three defects in it, and they are the reason a new learner is wanted:**

1. **Win-rate only.** `app.py:6202` divides wins by wins+losses. A 70 %-win setup returning +0.1R per
   win and −1R per loss is scored as good. R never enters the calculation.
2. **Symmetric in form but not in evidence.** `edge = win_rate - 0.5` (`app.py:6204`) moves the score
   down as readily as up, on a `trust` that reaches 1.0 at just **30 samples** (`app.py:6203`).
3. **Global, not per-setup.** One number per symbol. No per-setup or per-confidence-tier resolution, so
   a bad pocket and a good pocket average into "fine".

**Where the shadow learner sits:** *beside* it, reading the same sources, writing
`data/learning/shadow_calibration.json`. It does **not** modify `_jarvis_confidence_calibration`, and
nothing reads its output but its own report page. The live number at `app.py:6030` is untouched.

---

## 3. What it reads

Everything carrying a confidence and a resolved outcome or R:

- `data/signals.json` — confidence, `outcome`, `outcome_profit_pct`, plus the component scores already
  recorded per signal: `crt_score`, `ict_score`, `fvg_score`, `ensemble_probability`,
  `model_accuracy`, `lstm_probability`, `quality_in_range`, `bias`, `expected_move_pips`
- `data/trade_memory/*.jsonl` — the demo strategies' own fills, which carry `r_result` and `net_money`
  (`src/demo_volatility_breakout.py:173`, `src/demo_plan_trader.py`)
- `data/strategy_lab/shadow_book.json` — 56 tracked strategies, once they produce closed trades
- `data/strategy_lab/crt_forward*.json` and `forward_*.json` — the paper forward tests, `net_r` each
- `data/execution_rejections.jsonl` — **as a censoring record, not as outcomes.** A rejected signal has
  no outcome; counting it as a loss would be exactly the "starve the data" error the rule forbids

**Never read from backups.** `data/_backup_*/` is excluded: a 12 Sep snapshot double-counts signals
already in the live file.

---

## 4. How it learns

**R-aware, symmetric in method, asymmetric in burden of proof.**

- **Unit: expectancy in R after costs**, never win rate. `src/strategy_lab.py` already emits `net_r` per
  trade (added 19 Sep) and `summarize_trades` reports `expectancy_r` — reuse those, do not re-derive.
- **Buckets:** `(setup_kind) × (confidence tier)`. Tiers 0.50–0.55, 0.55–0.60, 0.60–0.65, 0.65–0.70,
  0.70+ — chosen to match the *observed* 0.55–0.69 range rather than an imagined 0–100 scale.
- **Per bucket:** n, mean net R, a 95 % interval (bootstrap; the distribution is not normal), and the
  fraction of outcomes resolved.
- **Shrinkage toward the global mean**, weight `n / (n + k)`. A three-trade bucket must not swing a
  score. `k` is declared before the first run and never tuned afterwards.
- **Output:** `shadow_score` per signal, alongside the live `confidence`, both stored. Never written
  back into the signal record used by anything live.

**What it must not do:** no threshold search, no parameter sweep to maximise anything. This is
calibration — mapping a score to an outcome — not another 9,887-candidate search.

---

## 5. How "getting smarter" is measured

**A daily reliability curve**, the brief's own test made concrete.

For each confidence tier: predicted (the tier's midpoint, mapped to expected R) against realised (the
bucket's actual mean net R), with its interval. Written daily to
`data/learning/shadow_calibration_history.json`, one row per day per tier.

Three numbers tracked over time:

1. **Calibration error** — mean absolute gap between predicted and realised across tiers. Falling = the
   score means more than it did.
2. **Monotonicity (Spearman rank correlation)** between tier and realised R. **This is the inversion
   test.** If higher confidence currently earns *less*, the correlation is negative; the curve
   straightening out shows as it rising through zero toward +1.
3. **Resolved sample per tier** — because the first two are meaningless until this is large enough, and
   showing it prevents the other two being read too early.

A daily curve on a growing sample is what makes improvement *visible over time* rather than asserted.

---

## 6. The live gate — what it must prove first

The shadow score earns the right to drive a live decision only when **all** of these hold, measured on
**demo forward data only** — never on backtest, never on the accumulated history it was fitted to:

| Requirement | Threshold | Why |
|---|---|---|
| Resolved demo outcomes | **≥ 200**, and **≥ 30 per tier** it wants to act on | 10 exist today. Below this the curve is noise |
| Monotonicity | Spearman **≥ +0.5**, sustained **30 consecutive days** | One good day is weather |
| Calibration error | Below its own day-1 value, sustained 30 days | It must beat where it started, not an arbitrary bar |
| Out-of-period holdout | Curve holds on a period never used to fit it | The same locked-holdout discipline as every strategy here |
| Beats the incumbent | Better than `_jarvis_confidence_calibration` (`app.py:6196`) on the same rows | A replacement must beat what it replaces |
| Owner approval | Explicit, separate, after seeing the curve | Not implied by this design |

**Until every one is met, the output is a report and nothing else.**

---

## 7. Build plan, in dependency order

**Phase 0 — fix the data supply. This is the real first task.**
Ten resolved outcomes is the binding constraint. The supply now exists but is not flowing: 39 of 49
signals are unresolved, and `shadow_book.json` has tracked 56 strategies since 19 Sep with **0** closed
trades. Before any learner: make signal outcomes resolve and persist, and confirm the shadow book and
the three demo strategies (magics 440502, 440603, 440704) are writing closed outcomes with R. Estimated
wait: weeks, not hours. Nothing below is worth building until this produces rows.

**Phase 1 — the reader.** Load every source into one frame: `(timestamp, symbol, setup_kind,
confidence, resolved, net_r)`. Deduplicate against backups. Report coverage honestly, including how many
rows were unusable and why. No learning yet.

**Phase 2 — the bucketed calibrator.** Expectancy, interval, shrinkage per bucket. Emit
`shadow_calibration.json`. Still reads nothing live and is read by nothing live.

**Phase 3 — the daily curve.** Append one row per tier per day. This is where "getting smarter" becomes
visible, and it must run for weeks before it says anything.

**Phase 4 — the comparison.** Shadow score against `_jarvis_confidence_calibration` on identical rows,
same metrics, reported side by side.

**Phase 5 — gate proposal.** Only if section 6 is satisfied. A written proposal with the curve attached,
for a separate decision. Not part of this work.

---

## 8. What this design refuses to do

- No filter, throttle, or suppression anywhere. Every trade fires full-size throughout.
- No blocking on demo, ever — including of a setup proven bad.
- No wire into the gate, the fire decision, or sizing in this phase.
- No threshold search. Calibration is not a strategy hunt.
- No claim that the score is predictive until the demo data says so.

The rule this serves, as the owner clarified it on 20 September 2026: *"improvement path = better
calibration + a better model built from all the data, proven before it changes any live behaviour."*
This document is the calibration half, and section 0 is what keeps it honest.
