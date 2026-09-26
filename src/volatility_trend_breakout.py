#!/usr/bin/env python3
"""Volatility Trend Breakout — Python port of the TradingView Pine v5 strategy.

Copied unchanged from github.com/th3emis31/deeptrade-ai, branch claude/terminal-installation-35zvst, commit c9fffc2,
strategies/python/volatility_trend_breakout.py (its command-line main() left out: the system calls the functions).
The port reproduced the owner's TradingView tester on Vantage broker 4H bars 2023-2026: PF 2.023 vs 2.027
(tp1_model="close", max_leverage=0). The demo executor is src/demo_volatility_breakout.py.

Purpose: let the trading system run, walk-forward and forward-test the same rules
that produced the TradingView row (178 closed legs, 74.16% wins, profit factor
1.938, 5.50% max drawdown on 2023-01-02 to 2026-09-17).

Design rules followed here:
  * Pure functions. No I/O, no globals, no model files touched.
  * Indicators reproduce Pine exactly: ta.ema, ta.rma-based ta.atr and ta.rsi,
    ta.highest/ta.lowest including the current bar.
  * The execution model reproduces the Pine broker emulator, including the two
    fill models for TP1 so the TradingView number can be matched before it is
    improved.

Stdlib only. In the source repository it also runs as a standalone backtest:

    python volatility_trend_breakout.py candles.csv --tf 4h
    python volatility_trend_breakout.py candles.csv --tp1-model close   # v1 parity
    python volatility_trend_breakout.py candles.csv --inverse           # baseline

CSV columns (header required, case-insensitive): datetime, open, high, low, close, volume
"""

from __future__ import annotations

import csv
import statistics
import math
from datetime import datetime
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence

# ---------------------------------------------------------------------------
# Configuration — defaults are the Pine script's defaults, unchanged.
# ---------------------------------------------------------------------------


@dataclass
class Config:
    ema_len: int = 50
    use_ema_filter: bool = True

    donchian_len: int = 20
    atr_len: int = 14
    atr_mult: float = 1.4          # buffer above the Donchian high is atr_mult * 0.25 * ATR

    use_rsi: bool = True
    rsi_len: int = 14
    rsi_min: float = 52.0
    use_volume: bool = True
    vol_ma_len: int = 20

    risk_percent: float = 0.85
    sl_atr_mult: float = 1.5
    tp1_r: float = 1.3
    tp2_r: float = 2.8

    use_trail: bool = True
    trail_atr: float = 2.2
    use_time_exit: bool = True
    max_bars: int = 65
    be_atr: float = 0.15

    # Execution model
    initial_capital: float = 10_000.0
    commission_pct: float = 0.04    # percent of notional, per side
    slippage_ticks: float = 2.0
    mintick: float = 0.01
    max_leverage: float = 5.0       # 0 disables the cap (Pine v1 behaviour)
    tp1_model: str = "limit"        # "limit" (v2, realistic) or "close" (v1 parity)
    inverse: bool = False           # direction baseline: take the opposite side

    # Volatility regime filter (strategies/volatility_breakout_compression.md). "all" is the
    # shipped behaviour and the demo strategy's default, so leaving this alone changes nothing.
    # "compression" takes only breakouts where ATR is below its own rolling median, "expansion"
    # only those above it. Read at the signal bar, so it sees nothing the strategy could not.
    atr_regime: str = "all"         # "all" | "expansion" | "compression"
    atr_median_len: int = 200


# ---------------------------------------------------------------------------
# Candles
# ---------------------------------------------------------------------------


@dataclass
class Candle:
    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: float


def load_csv(path: str) -> List[Candle]:
    out: List[Candle] = []
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise ValueError("CSV has no header row")
        cols = {c.lower().strip(): c for c in reader.fieldnames}
        need = ("open", "high", "low", "close")
        missing = [c for c in need if c not in cols]
        if missing:
            raise ValueError("CSV is missing columns: %s" % ", ".join(missing))
        tcol = cols.get("datetime") or cols.get("date") or cols.get("time")
        vcol = cols.get("volume")
        for row in reader:
            try:
                out.append(Candle(
                    ts=row[tcol] if tcol else "",
                    open=float(row[cols["open"]]),
                    high=float(row[cols["high"]]),
                    low=float(row[cols["low"]]),
                    close=float(row[cols["close"]]),
                    volume=float(row[vcol]) if vcol and row[vcol] not in ("", None) else 0.0,
                ))
            except (TypeError, ValueError):
                continue          # skip malformed rows rather than inventing data
    out.sort(key=lambda c: c.ts)
    return out


# ---------------------------------------------------------------------------
# Indicators — these must match Pine bar for bar.
# ---------------------------------------------------------------------------


def sma(values: Sequence[float], length: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= length:
            total -= values[i - length]
        if i >= length - 1:
            out[i] = total / length
    return out


def ema(values: Sequence[float], length: int) -> List[Optional[float]]:
    """Pine ta.ema: seeded with the SMA of the first `length` values."""
    out: List[Optional[float]] = [None] * len(values)
    alpha = 2.0 / (length + 1.0)
    prev: Optional[float] = None
    for i, v in enumerate(values):
        if i == length - 1:
            prev = sum(values[:length]) / length
        elif prev is not None:
            prev = alpha * v + (1.0 - alpha) * prev
        out[i] = prev
    return out


def rma(values: Sequence[float], length: int) -> List[Optional[float]]:
    """Pine ta.rma (Wilder): seeded with the SMA of the first `length` values."""
    out: List[Optional[float]] = [None] * len(values)
    prev: Optional[float] = None
    for i, v in enumerate(values):
        if i == length - 1:
            prev = sum(values[:length]) / length
        elif prev is not None:
            prev = (prev * (length - 1) + v) / length
        out[i] = prev
    return out


def true_range(candles: Sequence[Candle]) -> List[float]:
    out: List[float] = []
    for i, c in enumerate(candles):
        if i == 0:
            out.append(c.high - c.low)
        else:
            pc = candles[i - 1].close
            out.append(max(c.high - c.low, abs(c.high - pc), abs(c.low - pc)))
    return out


def atr(candles: Sequence[Candle], length: int) -> List[Optional[float]]:
    return rma(true_range(candles), length)


def rsi(values: Sequence[float], length: int) -> List[Optional[float]]:
    """Pine ta.rsi: RMA of gains and losses."""
    gains = [0.0] * len(values)
    losses = [0.0] * len(values)
    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]
        gains[i] = max(diff, 0.0)
        losses[i] = max(-diff, 0.0)
    ag = rma(gains, length)
    al = rma(losses, length)
    out: List[Optional[float]] = [None] * len(values)
    for i in range(len(values)):
        if ag[i] is None or al[i] is None:
            continue
        if al[i] == 0:
            out[i] = 100.0
        elif ag[i] == 0:
            out[i] = 0.0
        else:
            out[i] = 100.0 - 100.0 / (1.0 + ag[i] / al[i])
    return out


def rolling_median(values: Sequence[Optional[float]], length: int) -> List[Optional[float]]:
    """Median of the last ``length`` values, None until there are that many. Causal by construction."""
    out: List[Optional[float]] = []
    window: List[float] = []
    for value in values:
        if value is not None:
            window.append(float(value))
            if len(window) > length:
                window.pop(0)
        out.append(statistics.median(window) if len(window) == length else None)
    return out


def rolling_max(values: Sequence[float], length: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    for i in range(len(values)):
        if i >= length - 1:
            out[i] = max(values[i - length + 1:i + 1])
    return out


def rolling_min(values: Sequence[float], length: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    for i in range(len(values)):
        if i >= length - 1:
            out[i] = min(values[i - length + 1:i + 1])
    return out


# ---------------------------------------------------------------------------
# Signal — the pure function the trading system should call.
# ---------------------------------------------------------------------------


@dataclass
class Signal:
    index: int
    ts: str
    direction: str        # "long" or "short" (short only when inverse is on)
    entry: float
    stop: float
    tp1: float
    tp2: float
    atr: float
    reason: str


def generate_signals(candles: Sequence[Candle], cfg: Config = Config()) -> List[Signal]:
    """Every bar's signal, using only information available at that bar's close."""
    if len(candles) < max(cfg.ema_len, cfg.donchian_len, cfg.atr_len, cfg.vol_ma_len) + 2:
        return []

    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    vols = [c.volume for c in candles]

    ema_v = ema(closes, cfg.ema_len)
    atr_v = atr(candles, cfg.atr_len)
    rsi_v = rsi(closes, cfg.rsi_len)
    vma_v = sma(vols, cfg.vol_ma_len)
    upper_v = rolling_max(highs, cfg.donchian_len)
    atr_median_v = rolling_median(atr_v, cfg.atr_median_len) if cfg.atr_regime != "all" else [None] * len(candles)

    signals: List[Signal] = []
    for i in range(1, len(candles)):
        a, e, r, u = atr_v[i], ema_v[i], rsi_v[i], upper_v[i - 1]
        if a is None or u is None or (cfg.use_ema_filter and e is None):
            continue
        if cfg.use_rsi and r is None:
            continue
        if cfg.use_volume and vma_v[i] is None:
            continue

        c = candles[i]
        breakout = c.close > u + cfg.atr_mult * a * 0.25
        trend_ok = (not cfg.use_ema_filter) or (e is not None and c.close > e)
        rsi_ok = (not cfg.use_rsi) or (r is not None and r > cfg.rsi_min)
        vol_ok = (not cfg.use_volume) or (vma_v[i] is not None and c.volume > vma_v[i])

        regime_ok = True
        if cfg.atr_regime != "all":
            median = atr_median_v[i]
            regime_ok = median is not None and ((a > median) if cfg.atr_regime == "expansion" else (a < median))

        if not (breakout and trend_ok and rsi_ok and vol_ok and regime_ok):
            continue

        risk_dist = cfg.sl_atr_mult * a
        if risk_dist <= 0:
            continue

        if cfg.inverse:
            signals.append(Signal(i, c.ts, "short", c.close, c.close + risk_dist,
                                  c.close - risk_dist * cfg.tp1_r,
                                  c.close - risk_dist * cfg.tp2_r, a,
                                  "inverse baseline of the breakout"))
        else:
            signals.append(Signal(i, c.ts, "long", c.close, c.close - risk_dist,
                                  c.close + risk_dist * cfg.tp1_r,
                                  c.close + risk_dist * cfg.tp2_r, a,
                                  "donchian%d breakout + %.2f ATR buffer, ema/rsi/volume ok"
                                  % (cfg.donchian_len, cfg.atr_mult * 0.25)))
    return signals


# ---------------------------------------------------------------------------
# Execution model — reproduces the Pine broker emulator.
# ---------------------------------------------------------------------------


@dataclass
class Leg:
    """One closed piece of a position. TradingView counts these as trades."""
    entry_ts: str
    exit_ts: str
    direction: str
    entry: float
    exit: float
    qty: float
    pnl: float
    r_multiple: float
    kind: str          # tp1 / tp2 / stop / time


@dataclass
class Result:
    legs: List[Leg] = field(default_factory=list)
    positions: int = 0
    equity_curve: List[float] = field(default_factory=list)
    capped_entries: int = 0
    final_equity: float = 0.0


def _round_tick(price: float, mintick: float) -> float:
    return round(price / mintick) * mintick if mintick > 0 else price


def backtest(candles: Sequence[Candle], cfg: Config = Config(),
             signals: Optional[Sequence[Signal]] = None) -> Result:
    """Event-driven simulation.

    Fill rules, matching the Pine emulator:
      * The entry is a market order at the signal bar's close (process_orders_on_close).
      * Protective orders become active on the following bar.
      * When a bar contains both the stop and a target, the stop is taken first.
      * Stop and market fills pay slippage; limit fills do not.

    ``signals`` overrides the strategy's own entries and exists for the permutation benchmark, which
    asks whether the entry TIMING carries information by re-running these exact exits over a different
    set of entry bars. Everything downstream - the TP1/TP2 legs, break-even, the trail, the time exit,
    slippage and commission - is untouched, so only the choice of bar differs.
    """
    if signals is None:
        signals = generate_signals(candles, cfg)
    by_index = {s.index: s for s in signals}

    res = Result()
    equity = cfg.initial_capital
    res.equity_curve.append(equity)

    slip = cfg.slippage_ticks * cfg.mintick
    comm = cfg.commission_pct / 100.0

    i = 0
    n = len(candles)
    while i < n:
        sig = by_index.get(i)
        if sig is None:
            i += 1
            continue

        long = sig.direction == "long"
        fill = sig.entry + slip if long else sig.entry - slip
        risk_dist = abs(fill - sig.stop)
        if risk_dist <= 0:
            i += 1
            continue

        risk_amount = equity * (cfg.risk_percent / 100.0)
        qty = risk_amount / risk_dist
        if cfg.max_leverage > 0 and fill > 0:
            cap = equity * cfg.max_leverage / fill
            if qty > cap:
                qty = cap
                res.capped_entries += 1
        if qty <= 0:
            i += 1
            continue

        entry_cost = fill * qty * comm
        equity -= entry_cost
        res.positions += 1

        remaining = qty
        stop = sig.stop
        tp1, tp2 = sig.tp1, sig.tp2
        tp1_taken = False
        extreme = candles[i].high if long else candles[i].low
        active_stop = stop
        bars_held = 0
        j = i + 1

        def close_leg(price: float, part: float, kind: str, ts: str, pay_slip: bool) -> None:
            nonlocal equity
            px = price
            if pay_slip:
                px = price - slip if long else price + slip
            gross = (px - fill) * part if long else (fill - px) * part
            fee = px * part * comm
            # The ENTRY commission is charged to equity once, when the position opens, so it must be
            # allocated across the legs or every leg-derived figure overstates the result. Before
            # 26 September 2026 it was not: `equity` carried it but `leg.pnl` did not, so `net_pct` and
            # `max_dd` were right while `profit_factor`, `win_rate_pct`, `avg_win`, `avg_loss` and
            # `expectancy_r_per_position` were all flattered. On XAUUSD 4h that hid GBP 1,129 of real cost
            # and let profit factor read 1.269 while the equity curve had already paid it. It surfaced as
            # PF 1.135 sitting beside net -4.51 %, which cannot both be true of the same trades.
            entry_share = entry_cost * (part / qty) if qty else 0.0
            net_leg = gross - fee - entry_share
            equity += gross - fee
            initial_risk = risk_dist * qty
            res.legs.append(Leg(sig.ts, ts, sig.direction, fill, px, part,
                                net_leg, net_leg / initial_risk if initial_risk else 0.0,
                                kind))
            res.equity_curve.append(equity)

        while j < n and remaining > 1e-12:
            bar = candles[j]
            bars_held += 1

            hit_stop = bar.low <= active_stop if long else bar.high >= active_stop
            hit_tp1 = (bar.high >= tp1 if long else bar.low <= tp1) and not tp1_taken
            hit_tp2 = bar.high >= tp2 if long else bar.low <= tp2

            if hit_stop:
                close_leg(active_stop, remaining, "stop", bar.ts, True)
                remaining = 0.0
                break

            if hit_tp1:
                half = remaining * 0.5
                if cfg.tp1_model == "close":
                    # v1 behaviour: a market order filled at this bar's close.
                    close_leg(bar.close, half, "tp1", bar.ts, True)
                else:
                    close_leg(tp1, half, "tp1", bar.ts, False)
                remaining -= half
                tp1_taken = True
                a_now = sig.atr
                stop = fill + a_now * cfg.be_atr if long else fill - a_now * cfg.be_atr

            if hit_tp2 and remaining > 1e-12:
                close_leg(tp2, remaining, "tp2", bar.ts, False)
                remaining = 0.0
                break

            if cfg.use_time_exit and bars_held >= cfg.max_bars and remaining > 1e-12:
                close_leg(bar.close, remaining, "time", bar.ts, True)
                remaining = 0.0
                break

            # End-of-bar housekeeping: the trail computed now applies from the next bar.
            extreme = max(extreme, bar.high) if long else min(extreme, bar.low)
            if cfg.use_trail and tp1_taken:
                trail = extreme - cfg.trail_atr * sig.atr if long else extreme + cfg.trail_atr * sig.atr
                active_stop = max(stop, trail) if long else min(stop, trail)
            else:
                active_stop = stop
            j += 1

        if remaining > 1e-12:          # ran out of data with the position open
            close_leg(candles[n - 1].close, remaining, "eod", candles[n - 1].ts, True)

        i = j + 1 if j > i else i + 1

    res.final_equity = equity
    return res


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def metrics(res: Result, cfg: Config) -> dict:
    legs = res.legs
    wins = [l for l in legs if l.pnl > 0]
    losses = [l for l in legs if l.pnl <= 0]
    gross_win = sum(l.pnl for l in wins)
    gross_loss = -sum(l.pnl for l in losses)

    peak = cfg.initial_capital
    max_dd = 0.0
    for e in res.equity_curve:
        peak = max(peak, e)
        max_dd = max(max_dd, peak - e)

    streak = worst_streak = 0
    for l in legs:
        if l.pnl <= 0:
            streak += 1
            worst_streak = max(worst_streak, streak)
        else:
            streak = 0

    net = res.final_equity - cfg.initial_capital

    # Per-position returns as a share of the equity that existed at the time, which is what a ratio
    # needs. Leg returns would double-count a position that exited in two pieces.
    by_entry: dict = {}
    for leg in legs:
        by_entry.setdefault(leg.entry_ts, []).append(leg)
    running, position_returns = cfg.initial_capital, []
    for entry_ts in by_entry:
        pnl = sum(l.pnl for l in by_entry[entry_ts])
        if running > 0:
            position_returns.append(pnl / running)
        running += pnl

    sharpe = sortino = None
    if len(position_returns) >= 2:
        mean = sum(position_returns) / len(position_returns)
        variance = sum((r - mean) ** 2 for r in position_returns) / (len(position_returns) - 1)
        sd = math.sqrt(variance)
        sharpe = (mean / sd) if sd > 0 else None
        # Sortino uses TARGET DOWNSIDE DEVIATION against a zero target - the root mean square of the
        # negative part of every return, not the standard deviation of the losing subset. The second is a
        # common shortcut and it inflates the ratio, because it throws away how often losses did NOT
        # happen. Winners count here as zeros, which is the point.
        downside = math.sqrt(sum(min(r, 0.0) ** 2 for r in position_returns) / len(position_returns))
        sortino = (mean / downside) if downside > 0 else None

    # Calmar: annualised return over the worst drawdown. Needs a span, so it is None when the legs do not
    # carry parseable timestamps rather than being computed against an assumed year.
    calmar = years = None
    if legs and max_dd > 0:
        try:
            first = datetime.strptime(str(legs[0].entry_ts)[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
            last = datetime.strptime(str(legs[-1].exit_ts)[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
            years = (last - first).total_seconds() / (365.25 * 24 * 3600)
            if years > 0 and res.final_equity > 0:
                cagr = (res.final_equity / cfg.initial_capital) ** (1.0 / years) - 1.0
                calmar = cagr / (max_dd / cfg.initial_capital)
        except (ValueError, TypeError, ZeroDivisionError, OverflowError):
            calmar = years = None

    return {
        "closed_legs": len(legs),
        "positions": res.positions,
        "win_rate_pct": 100.0 * len(wins) / len(legs) if legs else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "net_usd": net,
        "net_pct": 100.0 * net / cfg.initial_capital,
        "avg_win": gross_win / len(wins) if wins else 0.0,
        "avg_loss": gross_loss / len(losses) if losses else 0.0,
        "expectancy_r_per_position": sum(l.r_multiple for l in legs) / res.positions if res.positions else 0.0,
        "max_dd_usd": max_dd,
        "max_dd_pct": 100.0 * max_dd / cfg.initial_capital,
        "longest_losing_streak": worst_streak,
        "size_capped_entries": res.capped_entries,
        "sharpe_per_position": round(sharpe, 4) if sharpe is not None else None,
        "sortino_per_position": round(sortino, 4) if sortino is not None else None,
        "calmar": round(calmar, 4) if calmar is not None else None,
        "years": round(years, 2) if years else None,
        # The identity that exposes any cost charged to equity but not to a leg. It held false for the
        # entry commission until 26 September 2026; anything that breaks it again is the same class of bug.
        "pnl_matches_equity": abs(sum(l.pnl for l in legs) - net) < 0.01,
    }


def format_report(m: dict, label: str) -> str:
    pf = m["profit_factor"]
    rows = [
        ("closed legs (TradingView 'trades')", "%d" % m["closed_legs"]),
        ("positions (independent entries)", "%d" % m["positions"]),
        ("win rate", "%.2f %%" % m["win_rate_pct"]),
        ("profit factor", "inf" if math.isinf(pf) else "%.3f" % pf),
        ("net", "%.2f USD  (%.2f %%)" % (m["net_usd"], m["net_pct"])),
        ("average win / loss", "%.2f / %.2f" % (m["avg_win"], m["avg_loss"])),
        ("expectancy per position", "%.3f R" % m["expectancy_r_per_position"]),
        ("max drawdown", "%.2f USD  (%.2f %%)" % (m["max_dd_usd"], m["max_dd_pct"])),
        ("longest losing streak", "%d legs" % m["longest_losing_streak"]),
        ("entries capped by leverage", "%d" % m["size_capped_entries"]),
    ]
    width = max(len(r[0]) for r in rows)
    body = "\n".join("  %-*s  %s" % (width, k, v) for k, v in rows)
    return "%s\n%s\n%s" % (label, "-" * len(label), body)


def signal_at(candles: Sequence[Candle], index: int, atr_value: float, cfg: Config) -> Optional[Signal]:
    """Build the signal this strategy WOULD have placed at ``index``, by its own level rules.

    A permutation cannot simply move a Signal object to another bar: its entry, stop and targets are
    all derived from the price and ATR of the bar it came from, so a moved signal would carry the wrong
    levels and the test would measure that error instead of the timing.
    """
    if index < 0 or index >= len(candles) or not atr_value or atr_value <= 0:
        return None
    close = candles[index].close
    risk = cfg.sl_atr_mult * atr_value
    if risk <= 0:
        return None
    if cfg.inverse:
        return Signal(index, candles[index].ts, "short", close, close + risk,
                      close - risk * cfg.tp1_r, close - risk * cfg.tp2_r, atr_value, "permuted control")
    return Signal(index, candles[index].ts, "long", close, close - risk,
                  close + risk * cfg.tp1_r, close + risk * cfg.tp2_r, atr_value, "permuted control")


def permutation_benchmark(candles: Sequence[Candle], cfg: Config = Config(), *,
                          draws: int = 500, seed: int = 20260924) -> dict:
    """Is this strategy's profit skill, or is it drift?

    The strategy is long-only in its live configuration, and gold rose 242 % over the window it is
    measured on. That alone will make a long-only rule profitable, so "it made money" cannot settle
    whether the breakout condition is picking good moments or merely picking moments.

    Each draw fires the SAME NUMBER of entries as the strategy, at randomly chosen bars, with levels
    built at those bars by the strategy's own rules, and runs them through the strategy's own exits.
    The only thing that changes is which bars were chosen. If the breakout condition carries no
    information, the real run will sit in the middle of the draws.

    Simulates only; places no orders and writes nothing.
    """
    import numpy as np

    real_signals = generate_signals(candles, cfg)
    if len(real_signals) < 10:
        return {"available": False,
                "reason": f"only {len(real_signals)} signals in this window; too few to permute"}
    atr_v = atr(candles, cfg.atr_len)
    eligible = [i for i, a in enumerate(atr_v) if a and a > 0 and i < len(candles) - 1]
    if len(eligible) < len(real_signals) * 3:
        return {"available": False,
                "reason": f"{len(eligible)} usable bars for {len(real_signals)} signals; too few to permute"}

    real = metrics(backtest(candles, cfg), cfg)
    rng = np.random.default_rng(seed)
    returns, expectancies = [], []
    for _ in range(draws):
        picks = sorted(rng.choice(len(eligible), size=len(real_signals), replace=False).tolist())
        drawn = [signal_at(candles, eligible[k], atr_v[eligible[k]], cfg) for k in picks]
        got = metrics(backtest(candles, cfg, signals=[d for d in drawn if d]), cfg)
        returns.append(float(got.get("net_pct") or 0.0))
        expectancies.append(float(got.get("expectancy_r_per_position") or 0.0))

    returns_arr = np.array(returns, dtype=float)
    expectancy_arr = np.array(expectancies, dtype=float)
    real_return = float(real.get("net_pct") or 0.0)
    real_expectancy = float(real.get("expectancy_r_per_position") or 0.0)
    # Decided on expectancy per trade for the same reason as the model benchmark: draws do not all
    # convert their entries into the same number of closed legs, and more exposure in a rising market
    # moves total return on its own.
    exp_percentile = round(100.0 * float(np.sum(expectancy_arr < real_expectancy)) / len(expectancy_arr), 1)
    exp_p = round(float(np.sum(expectancy_arr >= real_expectancy) + 1) / (len(expectancy_arr) + 1), 4)
    return {
        "available": True, "draws": int(draws), "entries_permuted": len(real_signals),
        "eligible_bars": len(eligible),
        "strategy_return_pct": round(real_return, 3),
        "strategy_expectancy_r": round(real_expectancy, 4),
        "strategy_closed_legs": real.get("closed_legs"), "strategy_positions": real.get("positions"),
        "random_mean_return_pct": round(float(np.mean(returns_arr)), 3),
        "random_median_return_pct": round(float(np.median(returns_arr)), 3),
        "random_5th_return_pct": round(float(np.percentile(returns_arr, 5)), 3),
        "random_95th_return_pct": round(float(np.percentile(returns_arr, 95)), 3),
        "random_mean_expectancy_r": round(float(np.mean(expectancy_arr)), 4),
        "return_percentile": round(100.0 * float(np.sum(returns_arr < real_return)) / len(returns_arr), 1),
        "expectancy_percentile": exp_percentile, "expectancy_p_value": exp_p,
        "beats_chance": bool(exp_p <= 0.05),
        "decided_on": "expectancy per trade, over the strategy's own exits",
        "verdict": ("The breakout's entry timing beats chance: fewer than 5 % of random entry sets did "
                    "as well per trade. The edge is in WHEN it enters, not only that it is long."
                    if exp_p <= 0.05 else
                    "No timing skill shown. Firing the same number of entries at random bars does about "
                    "as well per trade, so the profit so far is explained by being long in a rising "
                    "market rather than by the breakout condition."),
    }
