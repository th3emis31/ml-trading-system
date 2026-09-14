# Session state — 2026-09-12

Handoff notes for resuming work on the JARVIS / SmartEntryProAI trading system.
Written at the end of the 12 Sep session.

## Read this first

**The live application is `C:\Users\th_em\app.py`**, with its `data/`,
`models/` and `logs/` directories directly under `C:\Users\th_em\`.

`C:\Users\th_em\ml_trading_system\` is an **older snapshot** of the same
codebase from 30 June. It is not what the server runs. A `CLAUDE.md` written
into that folder describes the stale copy and should not be trusted. Edits made
there have no effect on anything the user can see.

Quick way to tell them apart: the live `app.py` is larger, has more routes
(including `/api/telegram/config` and `/ea-panel`), and its data files carry
recent modification times.

## Current running state

| Thing | State |
|---|---|
| Flask server | Running, port 5000, started from `C:\Users\th_em` via `python app.py` under the keep-alive loop `start_trading.bat` (Startup shortcut `SmartEntryProAI.lnk`), which relaunches it 5 s after it exits. No auto-reloader: to load code changes, stop **only** the `python app.py` process and let the loop restart it; never start a second copy. (13 Sep 11:27-13:30 a manual `Start-Process` copy ran alongside the loop's copy, both listening on port 5000; fixed by stopping both. Check with `netstat -ano` that exactly one PID listens on :5000.) |
| Autonomy | **Disabled** — `enabled: false`, `auto_execute: false`, persisted in `data/auto_trader_state.json` |
| MT4 bridge | Connected, port **32778**, account **12755139** (IC Markets demo, `ICMarketsSC-Demo01`) |
| MT5 bridge | Connected (Vantage Markets, ~89,519 GBP) |
| Signals | Both XAUUSD and BTCUSD reporting **HOLD** (ensemble 0.468 / 0.506 — no directional edge) |

The MT4 terminal at `C:\Program Files (x86)\MetaTrader 4` (account 12755139)
runs two DWX bridge instances: XAUUSD H1 on ports 32778/79/80 and US500 H1 on
32788/89/90. A third terminal (CMC Markets install, logged into IronFX demo
1420704416) holds the original 32768/69/70 set.

## What was fixed this session

All changes are in `C:\Users\th_em\`.

1. **Trade outcomes were fabricated.** `_enrich_signal_record` set
   `outcome = "WIN" if confidence >= 0.55 else "LOSS"` — derived from the
   confidence score, never from price, and confidence is floored at 0.55, so
   every signal was automatically a win. Replaced with
   `evaluate_signal_outcome()`, a single path-dependent evaluator shared by the
   signals table and the performance summary: it walks bars forward from the
   signal and settles on whichever of stop or target is touched first, leaving
   undecided trades OPEN and out of the win-rate denominator. Returns now
   compound instead of being summed. Yearly went from 0W/10L/−171% to
   8W/2L/80%/+8.07%.

2. **The system could never emit BUY.** `signal = "BUY" if ensemble >= 0.55
   else "SELL"` turned every reading below the buy threshold, including a
   neutral 0.50, into a short. Added `_classify_signal()` with symmetric bands
   and a HOLD zone (buy ≥ 0.55, sell ≤ 0.45). HOLD is handled downstream: no
   trade levels are invented, and outcome evaluation skips it.

3. **Execution defaulted an unknown side to BUY.** `auto_trade_execute_api`
   contained `if side not in {'BUY','SELL'}: side = 'BUY'` and then placed the
   order. Now returns 409 and places nothing.

4. **LSTM never ran.** `LSTMTrader` fitted a `StandardScaler` during training
   but never persisted it, so every prediction in a fresh process raised
   `NotFittedError`, swallowed by a bare except and surfacing only as
   "LSTM n/a". The scaler is now saved next to the model and both networks were
   retrained. "Ensemble RF+LSTM" is finally a real ensemble.

5. **Fabricated telemetry.** `/api/ml/*` and `jarvis_ai_tools.py` served
   hard-coded tables — LSTM "97.3% accuracy, STRONG BUY", gold at 2065, BTC at
   42500, plus EURUSD/GBPUSD/SPX recommendations for instruments this system
   does not trade. The recommendation engine priced real entries and stops off
   those numbers. Now computed from live models and prices, with an explicit
   `available: false` when a source is missing.

6. **Yahoo vs broker pricing.** Quotes now prefer MT4, then MT5, then Yahoo.
   Yahoo proxies XAUUSD with the `GC=F` gold future, which ran about 60 points
   (−1.37%) away from broker spot, so entries and stops did not line up with
   fills. Model *history* still comes from Yahoo — DWX can serve bars via
   `DATA|`, but `mt4_service.py` does not implement it yet.

7. **Chat.** 93 of 250 stored messages were `str(reply)` instead of
   `reply['content']` and rendered as raw Python dicts; they are now repaired on
   read (with a regex fallback, since the reprs embed `datetime.datetime(...)`
   which defeats `literal_eval`). The topic classifier funnelled everything to
   "risk" because the dashboard sends the whole persona prompt as the message
   and that prompt contains the word "risk"; `"hi" in lowered` also matched
   inside "this". Now strips the prompt wrapper, matches on word boundaries,
   and resolves symbol intent first.

8. **Dead scheduler.** `start()` was only reachable via a manual POST, so every
   restart left it idle (last run 30 June). Now auto-resumes at boot from saved
   state, and analyses gold and BTC instead of EURUSD/GBPUSD/USDJPY.

9. **Broken stylesheet.** `THEME_CSS`, shared by eight pages, had 86 orphaned
   declarations and unbalanced braces — selectors had been lost so rules bled
   into each other and browsers silently discarded them. Rebuilt as a proper
   design system. Verified 86 orphans to 0.

10. **New EA panel** at `/ea-panel`, backed by `/api/ea/overview`: bridge
    health for MT4 and MT5, live quotes, broker-vs-reference basis, account
    info, and a tail of the MetaTrader expert log.

## The DWX expert advisor

Source: `...\MetaQuotes\Terminal\1016BE391B9DE24FE6CE93BAF88685D4\MQL4\Experts\DWX_ZeroMQ_Server_v2.0.1_RC8.mq4`,
compiled with `metaeditor.exe /compile:"<path>" /log:"<path>"` and copied to all
three terminal folders.

Bugs fixed in it:

- **Double send.** `InformPullClient()` sent the real reply, then `OnTimer()`
  additionally sent the never-populated `ZmqMsg` returned by
  `MessageHandler()`. With `setSendHighWaterMark(1)` the real reply took the
  only queue slot, so the second send always failed, logged
  `###ERROR### Sending message` (196 times in one day) and stalled the reply
  pipe. Clients timed out while the EA looked healthy from outside.
- High water marks were set *after* `bind()`, where ZeroMQ ignores them.
- `CheckServerStatus()` replied on `pullSocket`, a receive-only socket.
- A failed bind returned `INIT_FAILED`, so MT4 removed the expert and deleted
  its chart objects — a port clash left a blank chart and no explanation.
- `RATES` was never an implemented command, so `check_symbol()` timed out. Added
  a synchronous `RATES;SYMBOL` handler returning bid/ask/spread/digits.

Added: an on-chart dashboard (status, bound ports, client activity, command and
error counts, the four permission flags, account, server, quote, balance,
equity, open trades), automatic port fallback in steps of 10 across four sets so
several terminals can each run a bridge, bind retry every 10s, and account
identity in the `HEARTBEAT` reply.

Python side: `MT4Service` now scans the fallback port sets and **rejects a
bridge reporting the wrong account**, pinned by `MT4_ACCOUNT` (default
`12755139`, set to `0` to accept any).

## Evening session (12 Sep) — page-by-page audit

Every page was rendered in headless Edge/Chrome over the DevTools protocol
(script in the session scratchpad), checked for JS exceptions, console
errors, failed requests and broken text, then screenshotted and reviewed.

- **Two servers were running on port 5000** (an orphan from the earlier
  session plus `start_trading.bat`'s). Both attached to the same DWX bridge,
  stole each other's replies, and the orphan died on a libzmq assertion. Only
  the batch-managed instance runs now.
- `trading/mt4_service.py`: send/recv guarded by a lock (Flask threads shared
  one ZeroMQ socket), stale late replies drained before each command, and
  `account_info()` reads the EA's nested `_data[0]` fields — it reported a zero
  balance on every call before.
- `/jarvis-voice` had a JS syntax error (`\'` collapsed inside the Python
  string) that stopped every script on the page. `/favicon.ico` added.
- Voice listener: `recognition.lang` (was `language`, ignored), `no-speech`
  treated as routine, overlapping starts ignored, retries stop when the mic is
  refused.
- **Signal history was frozen on 28 June.** `/api/signals` re-saved the ten
  rows it read and pushed them into the live cache. `record_signal_history()`
  now appends live signals (side change, or hourly while BUY/SELL persists),
  archives overflow to `data/signals_archive.jsonl`. The ten June rows were
  produced by the old always-SELL rule; they are flagged
  `legacy_misclassified`, not deleted, and Analytics counts them separately.
- HOLD gets a Neutral bias; HOLD levels show "—" instead of null/None.
- `/jarvis-brain-status` read the wrong JSON level (0 trades, NaN%);
  `/learn` "Current Signals" had no loader; `/api/shadow-simulation` cached
  5 minutes (15s per call before); Auto Trader P&L showed the whole balance.
- `/api/jarvis/recommendations/trading` served a hard-coded table (BTC entry
  43,000, S&P 4,780, "96% accurate"). It now returns the live signals and
  plans; the engine file is untouched but no longer used by that route.
- **One grouped navigation** (Trading / Automation / AI / Data) on every page:
  a context processor for the main templates, plus an `after_request` hook
  that inserts it into any other HTML page. Wraps by group on desktop, one row
  per group on phones.
- `THEME_CSS` gained a visual layer at its end: visible borders, a colour bar on
  every card, heading accent bars, stronger tables, inputs and badges.

### Pipeline pages (added late 12 Sep)

- **`/pipeline` — Live Signal Pipeline.** `build_live_pipeline_trace()` walks
  each symbol through nine stages with the live helpers (data with source and
  bar age, features, RF, LSTM, ensemble/classify, cached signal & history,
  trade plan, risk guardrails, execution gate) and states why an order would
  or would not be placed. Read-only; cached 60s; `/api/pipeline/live`.
- **`/pipeline/training` — Training & Backtest.** `src/walkforward_backtest.py`
  runs a leak-free walk-forward backtest on real Yahoo history (2y hourly, 5y
  and 10y daily): folds train only on earlier bars with a 3-bar purge, costs in
  `BACKTEST_COSTS`, seed 42, full metric set, "insufficient evidence" under 100
  trades. It fits folds with `src.train`'s RF helper and never writes
  `models/`. Jobs run in a background thread
  (`/api/pipeline/backtest/run|status|runs`); every run is appended to
  `data/backtest_runs.json`. Tests: `tests/test_walkforward_backtest.py`
  (7 pass; pytest was installed into Python310 for this).
- First real run, BTCUSD 10y daily, test Sep 2021–Sep 2026: 50 trades (all
  long), win rate 46%, PF 1.18, return +14.7% (CAGR 2.8%), max DD 46.8%,
  Sharpe 0.23, versus buy & hold +82.7%. Insufficient evidence; no edge yet.
- `src/backtest.py` (the old one) runs on synthetic data and predicts on its
  own training bars. Left in place, not used by the new page.
- `fetch_real_data` now tags frames `attrs["source"]` = `yahoo` / `synthetic`.
- Performance summary: legacy rows excluded via `_is_legacy_misclassified()`
  (shared with History and Analytics). Its cache used to live forever; it is
  now invalidated if written before the exclusion and refreshed in the
  background after 15 minutes. `source` says whether the numbers are recorded
  signals or shadow simulation (currently simulation: no directional signal
  has been recorded since the fix).

### Self-learning, backtest and Dashboard upgrades (13 Sep, early hours)

- **Self-learning is gated.** `DailyLearner.run_cycle()` (used by the daily /
  weekly buttons, quality retrain, chart-learn, screenshot retrain and the
  JARVIS "check learning" / "system update" intents) used to overwrite the live
  RF and LSTM files with no comparison, and would have trained on synthetic
  prices when Yahoo failed. Now, via `src/model_promotion.py`:
  - synthetic data is refused (`status: skipped_synthetic_data`);
  - current models are copied to `models/archive/<symbol>/<stamp>/` first
    (never deleted);
  - the new RF is scored against the archived one on bars after the new
    model's training rows (3-bar purge); it stays only if accuracy is not lower
    (1-point tolerance when the champion is over 14 days old) and drawdown on
    band trades is not worse by more than 2 points; otherwise files are
    restored. The LSTM uses the weaker own-validation comparison;
  - every decision is appended to `data/learning_decisions.json`, shown on
    `/pipeline/training` and the Dashboard. `run_cycle(gate=False)` keeps the
    old behaviour if ever needed.
  - Tests: `tests/test_model_promotion.py` (9 pass).
  - First live gated run, XAUUSD daily (13 Sep 03:36): RF promoted (tie at
    51.9% on 445 unseen bars, Brier 0.2498 vs 0.2502, champion 31.5 days old);
    LSTM kept previous (new 52.6% < previous 54.1%). Archive:
    `models/archive/xauusd/20260913-023640`. Both RF versions lost ~2% on band
    trades over that month.
- **XAUUSD 2y hourly walk-forward (live timeframe), Jul 2025–Sep 2026:** 1,311
  trades (all long), win rate 28.4%, PF 0.73, return −60.9%, max DD 60.9%,
  Sharpe −3.8, vs buy & hold +31%. Sufficient evidence: **the live strategy at
  the 0.55 band loses money on gold.** The 0.58 band was +1.7% on 70 trades
  (insufficient, and an in-sample pick). Keep autonomy off until an edge is
  shown out of sample.
- **Backtest additions:** threshold-sensitivity table (0.52–0.65 bands on the
  same out-of-sample probabilities, flagged as in-sample selection), per-year
  breakdown, and batch runs (`symbol`/`range` = `ALL`) queued in one job.
- **Dashboard:** new System Pipeline card (per-symbol signal, stage-health dots,
  execution gate, latest backtest, latest learning decisions); market tiles keep
  the live price instead of being overwritten with "Acc 0.46"; the snapshot says
  "Simulated win rate" when performance comes from the shadow simulation.

### Edge research, data feed and multi-timeframe (13 Sep, morning)

Goal agreed with the user: find a strategy that is profitable for BUY and SELL.
Pass criteria on an untouched holdout, per symbol: profit factor ≥ 1.2, ≥ 100
trades, max drawdown ≤ 20%, return above buy & hold after costs. Nothing passes yet.

- **Research tools (all research-only; none write `models/`):**
  - `src/edge_research.py` — triple-barrier labels per side, stationary leak-free
    features, walk-forward with purge, selection on validation folds only, one
    application to holdout folds, baselines. Options: trailing-quantile EV
    thresholds (R7a), `--selection robust` (profit in ≥60% of validation folds),
    intervals 15m / 1h / 4h / 1d, `--source` (yahoo / app / auto), `--mtf`
    (higher-timeframe context from closed bars + trend-alignment flags).
  - `src/sequence_research.py` (LSTM/GRU, same labels/folds), `src/meta_research.py`
    (rule proposes, ML filters; not yet run on real data), `src/mtf_data.py`.
  - Tests: `tests/test_edge_research.py`, `test_sequence_research.py`,
    `test_meta_research.py`, `test_mtf_data.py` (all pass).
  - Reports: `data/research/*.json`; every result is a row in
    `ml_trading_system/.claude/memory/BASELINE.md`; `/edge-research` skill runs one
    experiment per iteration.
- **Results so far (holdout):** R1 trees — best daily RF PF ~1.7 on only 30–35
  trades, hourly trade counts collapsed. R3 LSTM — fails everywhere, overfits
  (validation PF 1.6–1.7 → 0.68–1.13). R7a — trade counts fixed but best-of-63
  picks (validation PF 2.2–3.5) collapsed on holdout. R10 (4h/1h/15m, broker
  bars, with/without MTF, robust selection) is running.
- **Data Feed page** `/data-feed` (+ `/api/data-feed`, `/api/data/bars`): MT5
  broker candles read through the app's own connection via
  `MT5Service.copy_rates` (bar times converted from the broker's UTC+3 server clock
  to UTC via `server_utc_offset_hours`), Yahoo fallback, indicators per 15m / 1h /
  4h / 1d, MTF alignment, MT4 / MT5 / Yahoo quotes and basis, gold market-hours
  awareness. Broker 15m depth via the app: XAUUSD from 2024-07-31, BTCUSD from
  2025-04-02 (50,000 bars in under a second).
- **Do not** call `MetaTrader5.initialize()` / `copy_rates` / `shutdown()` from a
  second Python process while `app.py` is connected: the app process restarted
  right after such a query. Use `/api/data/bars`.
- Hooks in `ml_trading_system/scripts/claude-hooks/` fixed: `py()` skips the
  Microsoft Store stub; `dup-check.sh` checks each file's own project (top-level
  defs only) against a recorded baseline of existing duplicates.

- **Memory:** this PC has ~7.4 GB RAM. The first multi-timeframe queue was stopped by the
  system for low memory. `src/train.py` now imports `LSTMTrader` lazily (TensorFlow no
  longer loads for research: 1,030 MB -> 818 MB committed), research trains with 4 threads,
  and the queue runs one symbol per process with a free-RAM guard.

- **Research paused (13 Sep, user's choice):** the PC had ~0.75 GB of 7.4 GB RAM free, so
  the multi-timeframe queue could not run. Nothing is running in the background.
  Completed: XAUUSD 4h without MTF (fails: PF 0.91, -3.1% vs buy & hold +85%).
  Still to run: BTCUSD 4h; both symbols 4h `--mtf`; 1h `--source app` with and without
  `--mtf`; 15m `--mtf`; then meta-labelling (`src/meta_research.py`). To resume: close
  memory-heavy programs (keep MetaTrader and app.py), confirm >= 1.2 GB free, and run one
  symbol per process, e.g.
  `python -m src.edge_research --symbols BTCUSD --interval 4h --selection robust`.
  The exact list is in `ml_trading_system/.claude/memory/BACKLOG.md` (R10, R6).

- **Research resumed (13 Sep, user: "4H first", "gold only first", "one by one").** Runs are
  split into pieces (one model x one label setup per process, `--models X --configs Y`,
  `RESEARCH_JOBS=1`) and pooled with `python -m src.research_combine --symbol X --interval Y
  [--mtf] --selection robust` (same validation-only ranking; `tests/test_research_combine.py`).
  Holdout results (all saved as `data/research/combined_*.json`, rows in BASELINE.md):
  - XAUUSD 4h `--mtf` (7/9 pieces): **PF 1.29, 192 trades (89 long / 103 short), DD 6.5%,
    +20.3%** vs buy & hold +85.4% — passes PF/trades/drawdown, fails only buy & hold. Lead.
  - XAUUSD 1h `--mtf`: PF 0.94, -15.7%, DD 23.8% (fails). XAUUSD 15m `--mtf` (logit only):
    PF 0.92, -2.6% (fails). BTCUSD 4h `--mtf`: PF 0.78, -26.5%, DD 30% (fails).
  - Update: all 9 XAUUSD 4h `--mtf` pieces are now done; the complete search keeps the same
    pick and the same holdout (PF 1.29, 192 trades, +20.3%).
  - The "killed for low memory" failures were Claude Code's background-task monitor, not
    real out-of-memory: the same random-forest pieces finish in the foreground (~190 s,
    peak ~850 MB committed / ~190 MB RAM). Run research in the foreground, not as a
    background task. (Older note: rf pieces and xgb on 15m were killed at ~1.2-1.5 GB free.)
  - Meta-labelling R6 (XAUUSD 4h `--mtf`, logit; `src/meta_research.py` now shares
    `load_research_frame` with edge research): holdout PF 1.41 on 86 trades, but the ML
    filter adds no value over the plain 20-bar breakout rule.
  - Rule check without ML: on gold 4h every trend/breakout rule **loses on validation and
    wins on the holdout** — the 2024-26 holdout is a gold bull-trend regime. The ML 4h MTF
    pick is the only setup positive in both periods. BTC 4h: all rules fail on holdout.
    Nothing is ready for live trading; autonomy stays off.

- **Paper trading started (13 Sep; user chose "paper-trade gold 4H first" over auto-trading).**
  - `src/paper_trader.py` forward-tests the XAUUSD 4h MTF research model (xgb, tight exits
    SL 1 / TP 1.5 ATR / 6 bars, trailing 90% EV threshold) on broker candles from
    `/api/data/bars`. It drops forming bars, trains 246 bars back so the current and
    trailing EVs are out-of-sample, settles exits with `triple_barrier_outcomes`, keeps one
    position at a time, and has **no order path** (enforced by `tests/test_paper_trader.py`).
  - State: `data/paper_trading/xauusd_4h_mtf_xgb_tight_q90.json`; log `paper_trader.log`.
  - Schedule: Windows task **"SmartEntry Paper Trader"** runs `scripts/run_paper_trader.cmd`
    hourly while logged in (acts once per closed 4h bar). Remove with
    `schtasks /delete /tn "SmartEntry Paper Trader" /f`.
  - UI: `/paper-trading` (Automation nav) with forward-vs-backtest tiles, open trade, closed
    trades, every decision; `GET /api/paper-trading`, `POST /api/paper-trading/run`.
  - First decision (bar 2026-09-11 17:00 UTC): NO_TRADE, best EV -0.30R < threshold 0.23R.
  - Judge only after >= 100 closed paper trades (~7 a month in the backtest).

- **Demo trading ON (13 Sep, user: "Do it now for demo").**
  - `src/demo_executor.py` turns fresh gold 4h model signals into orders on the **Vantage demo
    account 11581419 only** (MT5 trade_mode 0, "demo" in the server name, login must match).
    0.01 lot (cap 0.10), magic **440401**, comment "GOLD4H demo model", stop and target
    attached (no naked retry), one model position at a time, signal age <= 75 min, one
    attempt per signal bar, closed at the time limit (6th bar after the signal) by
    `/api/demo-model/sync`. Tests: `tests/test_demo_executor.py`.
  - `trading/mt5_service.py`: `place_market_order` gained optional `magic` and
    `allow_retry_without_stops` (defaults unchanged for existing callers); new `quote`,
    `positions`, `close_position`.
  - App: `POST /api/demo-model/execute`, `POST /api/demo-model/sync` (localhost only),
    `GET /api/demo-model/status`; the Paper Trading page shows the mode banner, the open
    model position and the execution log.
  - Flow: hourly task -> `python -m src.paper_trader` -> decision -> app executor.
  - **Stop it:** set `"enabled": false` in `data/paper_trading/demo_execution.json`
    (read on every call, no restart needed); `"dry_run": true` logs without sending.
  - Jarvis `autonomy.enabled` / `auto_execute` stay False; that older path is unchanged.

- **Strategy Lab + SwingTrendPullback v2 (13 Sep).**
  - `src/strategy_lab.py` (tests `tests/test_strategy_lab.py`): 5 rule families x exit grid on
    broker 4H/1H candles for XAUUSD and BTCUSD; date-fixed search / validation / holdout per
    market; gates before any holdout look; deflated Sharpe on the holdout counts every trial.
    Registry `data/strategy_lab/registry.json` (rejected candidates kept as ids only), status
    `status.json`, log `strategy_lab.log`. Windows task **"SmartEntry Strategy Lab"** runs
    `scripts/run_strategy_lab.cmd` hourly at :20 (40 min / 1,000 candidates, heartbeat lock).
    Page `/strategy-lab` (Pipeline nav), `GET /api/strategy-lab`, `POST /api/strategy-lab/run`.
    `python -m src.strategy_lab ea` backtests the expert as coded.
  - MT5 expert `SwingTrendPullback.mq5` (terminal `C:\Program Files\MetaTrader 5`, data folder
    `D0E8209F…`, Vantage demo 25446287, XAUUSD H4) rewritten as **v2.00**: see
    `strategies/swing_trend_pullback.md`. Source backups `SwingTrendPullback.mq5.bak.20260913_134720`
    next to it and in `_backups`. Compile: `MetaEditor64.exe /compile:<mq5> /log:<log>`.
  - As coded the expert fails (14 years −2.4%, DD 36%). Best lab variants are profitable in all
    three periods but none passes the deflated Sharpe bar; presets `..._steady.set` and
    `..._trend_rider.set` in `MQL5\Presets` are for demo forward testing only.

- **ROCKET (LSTM replacement) tested (13 Sep):** `src/rocket_features.py` + `--rocket` flag in
  `edge_research` / `research_combine`. XAUUSD 4h MTF combined holdout PF 1.02 on 67 trades
  (−0.2%), worse than xgb MTF without it (PF 1.29): no evidence it adds edge on gold 4h.

- **System Doctor (13 Sep):** `src/system_doctor.py` (tests `tests/test_system_doctor.py`) checks,
  from outside the app: single app listener, app self-test, MT4 account match + MT5, app
  auto-trading flags (warns only when execution is actually armed), demo executor journal,
  paper trader freshness, Strategy Lab heartbeat, scheduled tasks, broker candle freshness,
  app error log, RAM/disk; `--deep` also compiles all code and runs the tests. Never trades;
  the only automatic fix (`--fix`) recreates a missing scheduled task. Tasks "SmartEntry System
  Doctor" (30 min) and "SmartEntry System Doctor Daily" (06:30). Page `/system-doctor`
  (Pipeline nav), `GET /api/system-doctor`, `POST /api/system-doctor/run`. Reports in
  `data/system_health/` (latest, latest_deep, history).
- **Auto switches:** at 13:16 on 13 Sep someone pressed START AUTO for XAUUSD/BTCUSD on the Auto
  Trader page (`settings.asset_settings.*.auto_enabled` = true). The app could not trade by itself
  (autonomy / auto_execute false, no session). At the user's request ("Yes please all") both were
  switched off again at ~14:45 through `POST /api/auto-trade/settings` (thresholds kept at 0.60).

- **Daily Report + daily schedule (13 Sep):** `src/daily_report.py` (tests `tests/test_daily_report.py`)
  writes `data/daily_reports/<date>.json` and `latest.json`: per market (XAUUSD, BTCUSD, broker
  candles, closed bars) key levels with nearest support/resistance, volatility regime (ATR
  percentile, 20d realised vol), daily momentum, last full UTC day's Asia/London/New York ranges,
  multi-timeframe bias with reasons (context, not a signal), gold-bitcoin correlation; plus the
  system brief (doctor, deep check, Strategy Lab counts, paper trader, demo execution) and the
  schedule. No news/calendar source connected (stated in the report). Task "SmartEntry Daily
  Report" 06:45; page `/daily-report` (Data nav), `GET /api/daily-report[?date=]`,
  `POST /api/daily-report/run`. Full schedule: paper trader :05 hourly, Strategy Lab :20 hourly,
  doctor every 30 min, deep doctor 06:30, daily report 06:45.

## Outstanding

- **DWX bridge `RATES` — resolved (verified 13 Sep).** The bridge reloaded the
  19:50 build by itself at 20:09:39 on 12 Sep (BTCUSD H1 and US500 H1 charts,
  ports 32778 / 32788), and `/api/data-feed` now shows live MT4 bid/ask for
  XAUUSD and BTCUSD. No re-attach needed. (Old note: the running instances were
  the 19:46 build and predated the `RATES` handler.)
- **Model history still comes from Yahoo.** Implement `DATA|SYMBOL|TIMEFRAME|
  START|END` in `mt4_service.py` so features are built on broker candles.
- **`CRT_Dashboard_EA` trailing stop — fixed and LIVE (verified 13 Sep).** The
  Experts logs show MT4 reloaded the recompiled expert by itself ("uninit reason 2"
  -> "loaded successfully" -> "initialized"): `50CA3DFB…` (Program Files (x86) MT4)
  at 20:07:31 and `1016BE39…` (CMC MT4) at 20:09:27 on 12 Sep, after the .ex4
  compiles at 20:07 / 20:05. No manual re-attach is needed. (The CMC terminal was
  closed by the user at 09:56 on 13 Sep; its saved profile keeps the EA on XAUUSD
  M15, so it loads the fixed build when reopened.) Original note: The
  stop was never on the wrong side; the distances were. `ApplySymbolPreset()`
  scaled entry settings per symbol but break-even lock, trail start and trail
  distance stayed raw points sized for gold (15/300/180). On BTCUSD
  (Point 0.01) that trailed $1.80 behind price once $3 in profit, inside the
  spread. Four trades on 12 Sep (#650829554, #650829679, #650829952,
  #650831159) were modified and stopped out 0–0.7s later. Now the three values
  follow the preset (BTC 190/3750/2250 points, gold unchanged) and every stop
  modify is clamped outside stoplevel, freezelevel and current spread.
  Compiled 0 errors in all three terminal folders; backups are
  `CRT_Dashboard_EA.{mq4,ex4}.bak.20260912_200420`. The running charts
  (BTCUSD M15 in terminal `50CA3DFB…`, XAUUSD M15 in `50CA3DFB…` and
  `1016BE39…`) still run the old build until the expert is re-attached.
- Both models are weak — RF ~0.46–0.54 accuracy, LSTM ~0.52–0.54. The HOLD band
  is currently correct behaviour rather than a bug, but the edge needs work.

## Backups

- `C:\Users\th_em\_backups\app.py.20260912_184745.bak` and `data.20260912_184745\`
- `DWX_ZeroMQ_Server_v2.0.1_RC8.mq4.bak.20260912_191452` beside the EA source

## Useful commands

```bash
cd C:\Users\th_em
python app.py                      # serves on port 5000, debug on

curl -s http://127.0.0.1:5000/api/jarvis/autonomy-status
curl -s http://127.0.0.1:5000/api/mt4/status
curl -s http://127.0.0.1:5000/api/performance-summary
curl -s -X POST -H "Content-Type: application/json" \
     -d '{"command":"disable"}' \
     http://127.0.0.1:5000/api/jarvis/autonomy-control

netstat -ano | grep -E ":(3277[89]|32780|3276[89]|32770)"   # which bridge is where
```

## Page rebuilds (13 Sep 2026, afternoon)

- **Signals** `/signals` (old template kept at `/signals-classic`): broker candles (lightweight-charts
  served from `static/vendor`) with exact Entry/SL/TP price lines, HOLD shown as no-trade, confidence %,
  evidence panel (walk-forward runs of the live signal, gold 4H research model, demo mode), 60 s refresh.
- **TradingView** `/tradingview` (old at `/tradingview-classic`): TradingView widgets, alerts table,
  webhook setup guide. `POST /api/tradingview` now requires the shared secret (403 otherwise; secret
  stripped before storing); a missing side is stored as UNKNOWN (used to default to BUY); local-only
  `POST /api/tradingview/test`; test alerts never count towards grades. No TradingView login is stored.
- **JARVIS voice**: "enable autonomy" requires "confirm enable autonomy" within 2 minutes; the fake
  "quick trade executed" and invented "buy signal" replies are gone (quick trade = start trade approval
  flow; buy signal = live plan + hourly backtest evidence); Start/Quick Trade buttons confirm first;
  Live System Status panel.
- **Performance** `/performance`: market heatmaps (hour x weekday, month x year) from broker candles,
  real MT5 account trading (`MT5Service.deal_history`): 11,307 closed deals since 3 Jan 2026, net
  -10,477 GBP, PF 0.78; worst experts by magic 778899 (-5,924), 202503 (-3,467), 636363 (-3,359);
  worst closing hours 13 and 15 UTC, best 22 UTC. Tracker curves (quality, learning, research, paper)
  and pipeline sync table.
