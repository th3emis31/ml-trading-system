"""The bar loop, exactly as the owner drew it.

    NEW BAR -> market data -> indicators -> 4H structure -> 1H structure -> 15M structure
    -> liquidity events -> CRT / AMD / FVG -> volume + POC -> regime -> candidate setup
    -> confirmation -> entry / SL / TP -> risk validation -> simulate execution
    -> manage open position -> record -> NEXT BAR

TWO THINGS THAT MAKE THIS HONEST RATHER THAN JUST ORDERED
----------------------------------------------------------
**Everything per-bar is precomputed, then read by index.** Structure, liquidity, volume and regime are
computed once over the whole frame by engines that are each causal by construction — rolling windows,
`shift(1)`, pivots confirmed `prd` bars late, higher-timeframe values aligned on CLOSE time. The loop then
reads row `i` of each. That is not a shortcut around the bar-by-bar discipline; it is the same discipline
enforced in one place per engine rather than re-argued at every step, which is where it usually breaks.

**The execution is not simulated here.** Fills, stops, targets, partials, break-even, trailing, spread and
swap all go through `strategy_lab.simulate_orders`, the one component with known-answer tests behind it. A
loop that placed its own fills would be a second execution model, and this project's two most expensive
errors both came from exactly that.

WHAT THE LOOP ADDS THAT A SWEEP DOES NOT
----------------------------------------
A sweep returns numbers. This returns a LEDGER: every setup found, every setup refused and the reason, and
the market state at each decision. When a result looks wrong, the first question is "how many setups were
there and why were they not taken", and only a ledger can answer it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from engine import (analytics, data_engine, execution_engine, ledger as ledger_mod,
                    liquidity_engine, regime_engine, risk_engine, signal_engine,
                    strategy_engine, structure_engine, volume_engine)


@dataclass
class RunConfig:
    """Everything declared BEFORE the run. Nothing here may be changed once results are visible."""

    symbol: str = "XAUUSD"
    timeframe: str = "15m"
    split: str = "holdout"
    grid: str = "sweep_reclaim"
    side: Optional[str] = None               # None = both, or "long" / "short"
    min_confluence: int = 1
    require_bias_agreement: bool = False
    limits: risk_engine.Limits = field(default_factory=risk_engine.Limits)
    structure_prd: int = 5
    liquidity_lookback: int = 20


@dataclass
class RunResult:
    config: RunConfig
    bars: int
    source: str
    ledger: ledger_mod.Ledger
    trades: List[dict]
    metrics: Dict
    excursions: Dict
    regimes: Dict
    outcome_mix: Dict
    entry_price_check: Dict = field(default_factory=dict)


def prepare(config: RunConfig) -> dict:
    """Steps 1-7 of the loop, computed once over the frame: data, structure, liquidity, volume, regime.

    Each engine is responsible for its own causality, and `data_engine.require_mt5` refuses to proceed at
    all on anything that is not broker data.
    """
    bars = data_engine.require_mt5(config.symbol, config.timeframe)
    frame = bars.frame
    return {
        "bars": bars,
        "frame": frame,
        "structure": structure_engine.per_bar(frame, config.structure_prd),
        "liquidity": liquidity_engine.sweeps(frame, config.liquidity_lookback),
        "volume": volume_engine.levels(frame),
        "regime": regime_engine.classify_regime(frame),
    }


def run(config: RunConfig, spec: Optional[dict] = None) -> RunResult:
    """One full pass: prepare the state, walk the bars recording decisions, then simulate and measure.

    `spec` may be supplied directly; otherwise the first variant of the configured grid is used, with the
    side filter applied. A real sweep calls this once per declared variant.
    """
    state = prepare(config)
    frame, bars = state["frame"], state["bars"]

    if spec is None:
        spec = strategy_engine.variants_for(config.grid, config.symbol, config.timeframe)[0]
    if config.side in ("long", "short"):
        spec = {**spec, "params": {**spec["params"], "side": config.side}}

    market = execution_engine.market_for(config.symbol, config.timeframe, frame)
    side_arr, stop_arr, target_arr = strategy_engine.orders(market.ind, spec)
    rows = market.rows[config.split]
    times = frame["datetime"]
    close = frame["close"].to_numpy(dtype=float)
    open_px = frame["open"].to_numpy(dtype=float)

    led = ledger_mod.Ledger()
    decisions: List[signal_engine.SignalDecision] = []
    exposure = risk_engine.Exposure(open_by_symbol={config.symbol: 0})

    # --- the bar loop ---------------------------------------------------------------------------
    for i in (int(r) for r in rows):
        raw_side = int(side_arr[i]) if i < len(side_arr) else 0
        if raw_side == 0:
            continue                                   # no candidate setup at this bar

        ts = str(times.iloc[i])
        direction = "BUY" if raw_side == 1 else "SELL"
        # The decision is made on this bar's CLOSE and filled at the NEXT bar's OPEN. Both are recorded,
        # because they are different numbers and an audit needs to see the gap between them: the ledger
        # previously stored the close as the entry price, which differed from the executed fill by up to
        # 10.04 points on gold 4h. The stop and target are computed against the signal price, so the risk
        # geometry below stays anchored to it - only the recorded fill changes.
        signal_price = float(close[i])
        fill_price = float(open_px[i + 1]) if i + 1 < len(open_px) else None
        entry, stop, target = signal_price, float(stop_arr[i]), float(target_arr[i])

        conditions = {
            "structure": bool(structure_engine.state_at(state["structure"], i)),
            "liquidity": any(liquidity_engine.events_at(state["liquidity"], i).values()),
            "volume": volume_engine.state_at(state["volume"], i).get("poc") is not None,
        }
        bias = signal_engine.bias_from_structure(structure_engine.state_at(state["structure"], i))

        led.setup(bar=i, ts=ts, symbol=config.symbol, timeframe=config.timeframe,
                  side=direction, price=signal_price, stop=stop, target=target,
                  reason=f"{config.grid}:{spec.get('variant')}",
                  detail={"signal_price": signal_price, "expected_fill": fill_price,
                          "regime": regime_engine.label_at(state["regime"], i),
                          "liquidity": liquidity_engine.events_at(state["liquidity"], i)})

        decision = signal_engine.decide_signal(
            i, ts, side=direction, entry=entry, stop=stop, target=target,
            conditions=conditions, bias=bias,
            min_confluence=config.min_confluence,
            require_bias_agreement=config.require_bias_agreement)
        decisions.append(decision)
        if not decision.take:
            led.rejected(bar=i, ts=ts, reason=decision.reason, symbol=config.symbol, side=direction)
            continue

        allowed = risk_engine.check_trade(config.symbol, entry, stop, target, exposure, config.limits)
        if allowed is not True:
            led.rejected(bar=i, ts=ts, reason=allowed.reason, symbol=config.symbol, side=direction,
                         detail=allowed.detail or {})
            continue

        led.entry(bar=i, ts=ts, symbol=config.symbol, side=direction,
                  price=fill_price, stop=stop, target=target, reason=decision.reason,
                  detail={"conditions": decision.present(), "signal_price": signal_price,
                          "fill_bar": i + 1,
                          "fill_time": (str(times.iloc[i + 1]) if i + 1 < len(times) else None),
                          "slippage_from_signal": (None if fill_price is None
                                                   else round(fill_price - signal_price, 5))})

    # --- execution and measurement, through the tested simulator ---------------------------------
    trades = execution_engine.run_trades(market, spec, config.split)
    for t in trades:
        led.closed(bar=int(t.get("entry_idx") or 0), ts=str(t.get("exit_time")),
                   symbol=config.symbol, side=str(t.get("side")),
                   price=t.get("exit_price"), reason=str(t.get("outcome")),
                   detail={"net_pct": t.get("net_pct"), "net_r": t.get("net_r")})

    measured = execution_engine.metrics(market, config.split, trades)
    return RunResult(
        config=config, bars=len(frame), source=bars.source, ledger=led, trades=trades,
        metrics=measured,
        excursions=analytics.excursion_summary(trades, frame),
        regimes=regime_engine.distribution(state["regime"]),
        outcome_mix=analytics.outcome_mix(trades),
        entry_price_check=reconcile_entry_prices(led, trades),
    )


def reconcile_entry_prices(led: ledger_mod.Ledger, trades: List[dict],
                           tolerance: float = 0.0005) -> Dict:
    """Prove the ledger's recorded entry price is the price the simulator actually filled at.

    The loop writes its entry row BEFORE the simulator runs, so it has to derive the fill itself from the
    next bar's open. That is a second statement of the fill rule, and a second statement is exactly how a
    ledger drifts away from the execution it claims to describe — a builder that returns resting limit
    entries, for instance, fills somewhere other than the next open, and the ledger would go on reporting
    the open with no sign that anything was wrong.

    So the derivation is CHECKED against the simulator's own `entry_price` on every run rather than
    trusted. `tolerance` exists only because the trade record rounds to 3 decimals; anything larger than
    that is a real disagreement and the run must not be reported as sound.
    """
    by_bar = {int(r.bar): r for r in led.of_kind("entry")}
    mismatches, checked, unmatched = [], 0, []
    for t in trades:
        bar = t.get("entry_idx")
        if bar is None:
            continue
        row = by_bar.get(int(bar))
        if row is None:
            unmatched.append(int(bar))
            continue
        ledger_price, fill_price = row.price, t.get("entry_price")
        if ledger_price is None or fill_price is None:
            unmatched.append(int(bar))
            continue
        checked += 1
        gap = abs(float(ledger_price) - float(fill_price))
        if gap > tolerance:
            mismatches.append({"bar": int(bar), "ledger_price": float(ledger_price),
                               "fill_price": float(fill_price), "gap": round(gap, 5)})
    return {"trades": len(trades), "checked": checked,
            "mismatches": len(mismatches), "worst": mismatches[:5],
            "unmatched_trade_bars": unmatched[:5],
            "agrees": not mismatches and not unmatched,
            "tolerance": tolerance}


def report(result: RunResult) -> None:
    """The pre-flight first, then the funnel, then the numbers — in that order, always."""
    c = result.config
    print("PRE-FLIGHT")
    print(f"  provenance  {c.symbol}:{c.timeframe} {result.bars} bars, source {result.source!r}"
          f"  {'BROKER' if str(result.source).startswith(('mt5', 'app')) else 'NOT BROKER'}")
    chk = result.entry_price_check or {}
    if chk:
        print(f"  entry price {chk.get('checked')} of {chk.get('trades')} trades checked against the "
              f"simulator's fill, {chk.get('mismatches')} mismatch(es) "
              f"{'AGREES' if chk.get('agrees') else 'DISAGREES'}")
    print(f"  split       {c.split}")
    print(f"  grid        {c.grid}  side={c.side or 'both'}  min_confluence={c.min_confluence}")
    print(f"  risk        {c.limits.risk_percent}% per trade, max {c.limits.max_open_per_symbol} open per "
          f"asset, daily stop {c.limits.daily_loss_percent}%, halt at {c.limits.max_drawdown_percent}%")
    print()
    print("LEDGER")
    print(f"  funnel      {result.ledger.funnel()}")
    reasons = result.ledger.rejection_reasons()
    if reasons:
        print("  refused because:")
        for reason, count in list(reasons.items())[:6]:
            print(f"    {count:>5}  {reason}")
    print()
    m = result.metrics
    print("RESULT")
    print(f"  trades {m.get('trades')}  win {m.get('win_rate_pct')}%  PF {m.get('profit_factor')}  "
          f"expectancy_r {m.get('expectancy_r')}")
    print(f"  net {m.get('total_return_pct')}%  drawdown {m.get('max_drawdown_pct')}%  "
          f"ambiguous exits {m.get('ambiguous_exits')}  buy&hold {m.get('buy_and_hold_pct')}%")
    print(f"  outcomes    {result.outcome_mix}")
    ex = result.excursions
    if ex.get("available"):
        print(f"  MAE/MFE     winners {ex['winners_median_mae_r']}R against them, losers "
              f"{ex['losers_median_mae_r']}R; worst {ex['worst_mae_r']}R")
