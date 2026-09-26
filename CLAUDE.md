# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## This is the live system

Since the folder switch on 16 September 2026 at 16:45, the running trading app is
**this directory** (`C:\Users\th_em\ml_trading_system\`): `app.py` plus `data/`,
`models/`, `logs/`, and the `src/`, `ai/`, `voice/`, `trading/`, `memory/`
packages. The eight SmartEntry scheduled tasks and the Startup shortcut
(`start_trading.bat`) point here, and `data/`, `models/` and `logs/` were copied
across by `scripts\switch_live_to_ml_trading_system.ps1`.

`C:\Users\th_em\` is now the **fallback copy only**. Nothing there runs unless
`scripts\rollback_live_to_home.ps1` switches back to it. Edit here, not there.

**All SmartEntry scheduled tasks now run from this folder**, verified 19 Sep 2026
against Task Scheduler itself. **SmartEntry CRT Forward** and **SmartEntry Daily
Agent** were missed by the switch script and pointed at the fallback for a while,
which is why earlier notes said so; they were moved and no longer do. Check the
task rather than trusting any note, including this one:
`(Get-ScheduledTask -TaskName '<name>').Actions[0].Execute`.

`SESSION_STATE.md` in this directory carries the most recent handoff notes —
current runtime state, what was recently fixed, and what is still outstanding.
Read it before starting work.

## Commands

Python 3.10 on Windows, packages installed into the system interpreter.

```bash
python app.py                    # serves on port 5000, debug=True (what is normally running)
# The running app is started at login by start_trading.bat (Startup shortcut), a loop that
# relaunches app.py 5 s after it exits. To restart, stop only the python app.py process; do
# not launch another copy, or two servers end up listening on port 5000.
python -m src.system_doctor [--deep] [--fix]   # health of the whole system (never trades)
python -m src.strategy_lab run|ea|status      # rule-strategy search with a locked holdout (costs: spread + overnight swap)
python -m src.strategy_lab ea --spec tradingview [--no-swap] [--swap-mode price]   # the live EA inputs; price mode = MT5 tester
python -m src.strategy_book update|rescore|cap|status   # kept strategies: Monte Carlo, neighbours, rolling windows, presets; best 10 watchlist per market active, rest archived
python -m src.ai_employee run [--force]|context|status   # daily read-only Claude Code review (subscription, no API key)
python -m src.paper_trader [--status]         # gold 4H model forward test (+ demo hand-off)
python -m src.daily_learning [--symbols XAUUSD BTCUSD] [--frequency daily|weekly]   # gated RF+LSTM retrain (keeps new model only if better on unseen bars)
python -m src.edge_research --symbols XAUUSD --interval 4h --mtf [--rocket] --models xgb --configs tight
python -m src.app_builder build "<what you want>" --kind cli|web [--execute]   # build a whole app in a sandbox: files, tests, and a smoke run that must answer; stdlib only, never trades
python -m src.orchestration prompt [--budget N]   # the orchestration design prompt, rendered from live state
```

## Scheduled work (Windows Task Scheduler, runs while the user is logged in)

| Task | Script | When | What |
|---|---|---|---|
| SmartEntry Paper Trader | `scripts/run_paper_trader.cmd` | hourly :05 | gold 4H model decision per closed bar; hands fresh signals to the demo executor |
| SmartEntry Strategy Lab | `scripts/run_strategy_lab.cmd` | hourly :20 | 35 min / 1,000 candidates on XAUUSD+BTCUSD 15m/1h/4h/1d (spread + swap), then 15 min strategy book update (re-check kept strategies on new bars, evidence, presets); heartbeat lock |
| SmartEntry System Doctor | `scripts/run_system_doctor.cmd` | every 30 min | quick health checks, recreates missing tasks |
| SmartEntry Demo Pullback | `scripts/run_demo_pullback.cmd` | hourly :01 | gold session pullback on the Vantage DEMO account 11581419 only (`src/demo_session_pullback.py`, magic 440502, 2 x 0.01 lot, TP 1.5R/3R + break-even, flat 21:00 UTC, tier-1 event window + breaker + news gates, kill switches on its own P&L, any MT5 error halts); the app runs the cycle (`POST /api/demo-trading/cycle`, secret); `dry_run` in `data/paper_trading/demo_session_pullback.json`; page `/demo-trading` with STOP; trade memory `data/trade_memory/gold_session_pullback_demo.jsonl` |
| SmartEntry Demo Breakout | `scripts/run_demo_breakout.cmd` | hourly :03 | Volatility Trend Breakout (the owner's Pine v5 script, port `src/volatility_trend_breakout.py`) on 4H XAUUSD, Vantage DEMO 11581419 only, beside the pullback (`src/demo_volatility_breakout.py`, magic 440603, 2 x 0.01 lot: TP1 1.3R leg + 2.8R leg with break-even/trail, 65-candle time exit); same gates and kill switches on its own P&L; SENDING orders since 2026-09-17 (owner decision); `POST /api/demo-breakout/cycle|stop|resume`, section on `/demo-trading`; trade memory `data/trade_memory/volatility_breakout_demo.jsonl` |
| SmartEntry Obsidian Notes | `scripts/run_obsidian_notes.cmd` | hourly :50 | `python -m src.obsidian_notes [--vault PATH] [--register]`: daily notes, daily plan, learning verdict, research log (BASELINE.md) and trade journal into the vault in `data/obsidian.json` (default `Documents\SmartEntry Vault`); keeps text under "## My notes"; never trades |
| SmartEntry Daily Learning | `scripts/run_daily_learning.cmd` | 05:30 | self-learning: gated RF + LSTM retrain for XAUUSD and BTCUSD on new candles, decisions in `data/learning_decisions.json`, log `data/learning/daily_learning.log`; never trades |
| SmartEntry System Doctor Daily | `scripts/run_system_doctor_deep.cmd` | 06:30 | compiles all code and runs the test suite |
| SmartEntry Daily Report | `scripts/run_daily_report.cmd` | 06:45 | gold/bitcoin market analysis + system brief + schedule |
| SmartEntry AI Employee | `scripts/run_ai_employee.cmd` | 07:15 | Claude Code reads the reports (read-only: `--restricted`, no MCP, Read/Grep/Glob only), writes a brief, proposals for the owner's approval and memory notes to `data/ai_employee`; page `/ai-employee` |

Pages: `/signals` (v2, old at `/signals-classic`), `/tradingview` (v2, old at `/tradingview-classic`;
webhook needs the secret in `data/tradingview_webhook.json`; `POST /webhook/tradingview` adds smart-entry checks and
a dry-run decision journal, `GET /api/tradingview/intake`; dry run unless the config's `allow_approval_queue` is true,
which only queues for the owner's approval and never executes; the page's Daily plan card = `GET /api/tradingview/plan` SwingTrendPullback rules on broker H4 + support/resistance, saved in `data/tradingview_plans`, and the Pine indicator `strategies/tradingview/smartentry_daily_plan.pine` via `GET /api/tradingview/indicator`), `/performance` (heatmaps + tracker curves),
`/paper-trading`, `/strategy-lab`, `/system-doctor`, `/self-learning` (daily learning curve: `GET /api/learning-curve`, `src/learning_curve.py`), `/daily-report` (reports in
`data/paper_trading`, `data/strategy_lab`, `data/system_health`, `data/daily_reports`).
`python -m src.daily_report` writes today's report on demand.

## Evidence rules for models and strategies

Nothing is called profitable without: after-cost results on a holdout that selection never saw,
at least 100 trades (30 for a single rule strategy's holdout), drawdown within limits, and, when
many candidates were tried, a deflated Sharpe ratio that counts every trial. Record every result
in `ml_trading_system/.claude/memory/BASELINE.md`, including failures. Money only follows
evidence: research -> paper forward test -> demo -> (user decision) real.

Standing owner rules (approved 14 Sep 2026):

- The Strategy Lab holdout bar `min_deflated_sharpe` stays at **0.95**. Never lower it, or the gates
  and trade minimums, to let a near-miss candidate (e.g. XAUUSD 4h at ~0.75) pass; keep watching it.
- **No change to the live SwingTrendPullback inputs or preset** until a strategy book candidate clears
  deflated Sharpe 0.95 and the other holdout criteria. Research, backtests, runs in the separate
  Strategy Tester copy and new presets for later review remain allowed.

**NEVER-BLOCK, clarified 20 September 2026** (an addition to the rule above, which stands unchanged):

> The rule protects **LEARNING and GOOD signals**: every trade fires and feeds learning so the system
> always has full data. It does **NOT** mean preserving bad signals. The system **SHOULD** get smarter
> and improve away from bad signals - but **ONLY** through proven, evidence-based learning validated on
> accumulated data, **NEVER** through a naive filter or throttle that stops trades firing or starves
> learning. Improvement path = better calibration + a better model built from all the data, proven
> before it changes any live behaviour.

So "never block" is not "never improve". It forbids the *shortcut* - a filter bolted on to suppress
signals - because that starves the very data the improvement has to be proven on. It permits, and
expects, improvement earned from the accumulated record and demonstrated before it touches anything live.

```bash
python run.py                    # alternative entry: probes for a free port from 5001
python -m pytest -q              # tests live in ml_trading_system\tests
python -m src.train              # retrain RF for XAUUSD + BTCUSD
```

Recompile the MetaTrader expert without opening MetaEditor:

```bash
"C:\Users\th_em\AppData\Roaming\CMC Markets MetaTrader 4\metaeditor.exe" \
  /compile:"<path to .mq4>" /log:"<path to .log>"
```

The log is UTF-16; decode with `iconv -f UTF-16LE`. MetaEditor returns a
non-zero exit code even on success — read the `Result: N errors` line instead.
Do not assume a recompiled expert needs re-attaching: on 12 Sep MT4 reloaded
`CRT_Dashboard_EA` and the DWX bridge on its own after recompiles. Check the
terminal's `MQL4/Logs/<yyyymmdd>.log` (UTF-16) for `uninit reason 2` followed by
`loaded successfully` / `initialized` after the `.ex4` timestamp; only if that
sequence is missing, remove the expert from the chart and attach it again.

## Architecture

**`src/` — the ML pipeline.** `data.py` fetches yfinance with synthetic
fallback and never raises. `features.py` owns `build_features()`, the single
source of truth for the feature frame, and it is idempotent (`train_model()`
calls it internally while tests pass already-enriched frames). `train.py` holds
`FEATURE_COLUMNS`, a calibrated RandomForest over `TimeSeriesSplit`, and
persists to flat files in `models/`. `lstm_model.py` has its own narrower
`LSTM_FEATURE_COLUMNS` and a 30-bar lookback — **its scaler must be persisted
alongside the `.keras` file**, otherwise every prediction in a fresh process
raises `NotFittedError`.

**`app.py` — a ~19k-line Flask monolith** holding the dashboard, trading logic
and UI. Pages are module-level triple-quoted strings rendered with
`render_template_string`; eight of them share `THEME_CSS`, so a change there
moves every secondary page at once. All state is JSON on disk under `data/` —
there is no database. Indentation is inconsistent (2-space in the JARVIS
sections, 4-space elsewhere); match the surrounding block.

**Signal flow** (`_build_signal_payload_uncached`, cached ~45s): fetch data →
`build_features` → RF probability + LSTM probability → 50/50 average →
`_classify_signal()` maps it to BUY / SELL / **HOLD**. HOLD is a real state: it
produces no trade levels and must never reach execution. Note this is *not*
`src.train.ensemble_predict` (60/40 weighted), which only `DailyLearner` uses —
two ensembles exist, so changing one does not affect the other.

**Trade outcomes** come from `evaluate_signal_outcome()`, which walks bars
forward and settles on whichever of stop or target is touched first. Every
panel must use it; deriving an outcome from confidence or from a mark against
the latest close is what produced contradictory win/loss numbers before.

**`jarvis_*.py` at the repo root** follow two conventions: route packs expose
`register_<name>_routes(app)` (usually wrapping a Blueprint) and are wired at
the top of `app.py`, and engines expose a module-level singleton accessor such
as `get_autonomous_brain()`. Adding a module means editing both blocks.

**Optional integrations degrade rather than fail.** MT4/MT5, voice and OCR all
import inside try/except and report status. Preserve that: a missing dependency
should surface as a status field, not a 500. Equally, never invent data to fill
a gap — return `available: false` instead of a plausible-looking number.

## Machine paths and portability

**Never write a path that names this PC into the source.** Anything machine-specific - the
MetaTrader terminals, a terminal's data folder, the Excel workbook - lives in `config/machine.json`
and is read through `src/runtime_paths.py`:

```python
from .runtime_paths import installed_terminal, terminal_data_dir, same_path
installed_terminal("mt5_strategies")   # the executable, by stable name
terminal_data_dir("mt5_tester")        # its data folder (a per-install hash)
```

The names are `mt5_strategies`, `mt5_panel`, `mt4_bridge`, `mt5_tester`. The defaults in
`DEFAULT_MACHINE` are this machine's own values, so a missing or unreadable config file changes
nothing. `tests/test_machine_portability.py` **fails the build** if such a path reappears in `src/`,
`scripts/` or `trading/`.

Two traps it exists to prevent:

- **Slashes.** The config writes forward slashes (a json file of escaped backslashes is a trap to
  edit by hand) and Windows reports `ExecutablePath` with backslashes. Compare configured paths with
  `same_path()`, never `==` or `in` - that exact bug would make the doctor call a running terminal
  missing.
- **Guessing.** A configured path that does not exist is *reported by name*, never replaced with
  whatever happens to be installed. `check_machine_paths()` in the doctor is the first check to run
  and lists each path found / NOT FOUND.

`I40_HOME` names the folder the system owns: set it and `config/`, `models/` and `data/` all resolve
under it, which is how the USB copy runs as its own system. Unset (the normal case on this machine)
nothing changes - `models` and `data` stay relative, exactly as before. `SMARTENTRY_MODELS_DIR` and
`SMARTENTRY_DATA_DIR` still win over it, so the test suite's sandboxing is unaffected.

## MetaTrader bridge

`trading/mt4_service.py` speaks the DWX ZeroMQ protocol: PUSH on the command
port, PULL on command+1, PUB on command+2. Commands are **semicolon**
delimited (`RATES;XAUUSD`), despite comments in the EA saying otherwise.

The EA steps to the next port set (+10, four sets) when its configured ports
are taken, so several terminals can each run a bridge. `MT4Service` scans the
same sets and **verifies the account number** from the `HEARTBEAT` reply before
accepting a bridge, pinned by the `MT4_ACCOUNT` environment variable. This
matters: multiple terminals are logged into different accounts, and without the
check the client attaches to whichever answers first.

Quotes prefer MT4 → MT5 → Yahoo. Yahoo proxies XAUUSD with the `GC=F` gold
future, which carries a basis of roughly 1.4% against broker spot, so anything
priced from it will not match fills.

## Safety

This system connects to funded MetaTrader accounts and can place orders.

- Back up `app.py` before editing it.
- Prefer changing runtime state through the app's own API endpoints over
  hand-editing state JSON while the server is running.
- `auto_execute` under `autonomy` in `data/auto_trader_state.json` controls
  autonomous order placement. Confirm with the user before enabling it.
- Never let a non-directional signal reach an execution path.
