# Improvement backlog (additive only, one per /improve-loop iteration)

## First session
- [ ] Run `/brain fill` so CLAUDE.md describes the real entry, sizing, order, data and model code
- [ ] Add a dry-run flag on the order/execution path if none exists
- [ ] Add `validate_entry()` with direction sanity and R:R ≥ 1.5
- [ ] Add an idempotence guard so a signal is never executed twice
- [ ] Add timeouts and one retry around data-feed and broker calls
- [ ] Add unit tests for position sizing and P&L

## Research (edge-research loop; one hypothesis per iteration)
- [ ] R1 Triple-barrier labels per side + stationary features + logit/RF/XGB, 1h, both symbols (src/edge_research.py baseline run)
- [ ] R2 Same on daily bars (10-12 years) for more independent trades
- [ ] R3 LSTM/GRU on the same triple-barrier labels, folds and holdout (sequence of research features)
- [ ] R4 Regime filter: trade only when vol_ratio / atr_z in a band chosen on validation
- [ ] R5 Broker candles (MT4 HIST / MT5 copy_rates) instead of Yahoo GC=F proxy for XAUUSD
- [ ] R6 Meta-labelling: rule-based entry (CRT EA / trend) + ML filter deciding take/skip
- [ ] R7 Probability calibration (isotonic on validation) before EV thresholding
- [ ] R8 If a strategy passes: shadow signal in the app (record only) for a forward period
- [x] R1 ran 13 Sep: no pass. Daily PF ~1.7 on holdout (XAU 30 trades, BTC 35) but too few trades and mostly long; hourly holdout trade counts collapsed (10 / 6) because fixed EV thresholds stop firing when probabilities drift.
- [ ] R7a Trailing-quantile EV threshold (EV >= rolling quantile of past EV and EV > 0) to keep trade frequency stable out of sample
- [x] R3 ran 13 Sep: LSTM fails on all four holdouts and is worse than RF on daily bars (validation PF 1.6-1.7 collapsed to 0.68-1.13) - overfits a few thousand bars. Do not scale LSTM up without far more data; prefer trees + meta-labelling.
- [ ] R9 Robust selection: require profit in >=60% of validation folds separately before ranking (--selection robust); R1/R7a picks were one-best-of-many and did not transfer
- [ ] R10 Multi-timeframe: 4h and 1h (broker bars via app) with and without --mtf, 15m with --mtf, robust selection; compare holdout vs R1/R7a rows
- [ ] R11 Feed the live model broker candles (MT5 via app) instead of Yahoo GC=F once a research strategy passes on broker data
- [ ] R10 PAUSED 13 Sep by user (PC low on memory: ~0.75 GB free of 7.4 GB). Done: XAUUSD 4h no-MTF (fails). Pending: BTCUSD 4h; both 4h --mtf; both 1h --source app with/without --mtf; both 15m --mtf. Resume one symbol per process after freeing RAM: cd C:\Users\th_em && python -m src.edge_research --symbols BTCUSD --interval 4h --selection robust (add --mtf / --source app as listed). Check free RAM >= 1.2 GB before each run.
  - 13 Sep resumed (user: "4H first", "gold only first", "one by one"), as pieces (--models X --configs Y, then python -m src.research_combine). Done: XAUUSD 4h --mtf from 7/9 pieces -> holdout PF 1.29, 192 trades, +20.3% vs buy&hold +85.4% (fails only buy&hold; rf balanced/wide pieces killed for memory). Also done (all FAIL): XAUUSD 1h --mtf PF 0.94 -15.7%; XAUUSD 15m --mtf (logit only) PF 0.92 -2.6%; BTCUSD 4h --mtf PF 0.78 -26.5%. rf pieces and xgb 15m were killed for memory each time - rerun them only with >=2 GB free. Still pending: BTCUSD 1h/15m --mtf, no-MTF 1h via app.
- [ ] R6 meta-labelling (src/meta_research.py, tested) not yet run on real data; run after R10 on the most promising timeframe
- [ ] Paper trading review: after >= 100 closed paper trades (about 14 months at ~7/month) compare /paper-trading with the backtest holdout (PF 1.29, win 47.4%, DD 6.5%); only then discuss demo auto-execution. Remove the schedule with: schtasks /delete /tn "SmartEntry Paper Trader" /f
- [ ] Demo trading check-in: after the first real demo trades, confirm fills, broker stop/target and the 24h time-limit close match the paper record; stop at any time by setting enabled false in data/paper_trading/demo_execution.json
- [ ] EA presets forward test: run SwingTrendPullback v2 with the steady or trend_rider preset on the demo account (GUI: Inputs > Load) and compare its closed trades with the Strategy Lab holdout; neither passed the deflated Sharpe bar yet
- [x] R12 DONE 13 Sep: FAILS (holdout PF 1.02, 67 trades) - ROCKET features (src/rocket_features.py) through edge_research --rocket on XAUUSD 4h --mtf (logit/xgb pieces), compare holdout with the xgb MTF baseline (PF 1.29) - this is the LSTM replacement test
- [ ] System Doctor: daily health checks (app single listener, MT4/MT5, scheduled tasks, data freshness, paper/demo/lab logs, app errors, tests, disk/RAM, autonomy flags) with safe auto-fixes only, page + daily task
