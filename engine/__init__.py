"""The bar-by-bar backtest engine, as the owner specified it on 27 September 2026.

    NEW BAR -> market data -> indicators -> 4H / 1H / 15M structure -> liquidity -> CRT/AMD/FVG
    -> volume + POC -> regime -> candidate setup -> confirmation -> entry/SL/TP -> risk
    -> simulate execution -> manage open position -> record -> NEXT BAR

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
It is NOT a second trading system. Every capability in the owner's diagram already existed in `src/`
before this package was written - audited 27 Sep 2026, all fifteen present - but scattered across about
fifty modules with no single bar loop and no named boundaries. Each engine here is a THIN, NAMED SEAM over
the code that already works:

    data_engine       -> src.mtf_data.load_bars (MT5 only; refuses Yahoo)
    timeframe_engine  -> src.mtf_data resampling + the 4H/1H/15M cascade
    structure_engine  -> src.market_structure (swing, BOS, CHOCH)
    liquidity_engine  -> src.sweep_reversal / src.poi_liquidity
    strategy_engine   -> the registered builders in src.strategy_lab.ORDER_BUILDERS
    volume_engine     -> src.volume_profile (POC, VAH, VAL, VWAP)
    regime_engine     -> trend / range / volatility classification
    signal_engine     -> bias, confirmation, confluence, invalidation
    execution_engine  -> src.strategy_lab.simulate_orders (spread, swap, partials, BE, trail)
    risk_engine       -> position sizing, daily loss, drawdown, exposure
    ledger            -> every decision, every rejection, with its reason
    analytics         -> src.walkforward_backtest.summarize_trades (+ MAE/MFE)
    walk_forward      -> the search / validation / holdout splits
    monte_carlo       -> src.strategy_book Monte Carlo + src.strategy_lab.permutation_check

Rewriting any of that would mean re-earning trust the existing code already has: the engine is covered by
`tests/test_engine_truth.py`, which proves it finds a planted edge, reports a planted loss, charges cost
once per trade and fills at the next bar's open.

THE ONE RULE THAT IS NOT NEGOTIABLE HERE
----------------------------------------
Bars come from MT5 or the run does not happen. `data_engine.load` reports the source string it actually
received, and anything that does not start with `mt5` is refused rather than substituted - because Yahoo
proxies XAUUSD with the GC=F future at roughly 1.4 % basis, so a gold backtest on Yahoo bars is drawn on a
different instrument from the one the account trades.
"""
from __future__ import annotations

__all__ = [
    "data_engine", "timeframe_engine", "structure_engine", "liquidity_engine", "strategy_engine",
    "volume_engine", "regime_engine", "signal_engine", "execution_engine", "risk_engine",
    "ledger", "analytics", "walk_forward", "monte_carlo", "loop",
]
