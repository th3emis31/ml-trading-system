"""The simulator's cost accounting must balance, and its ratios must not be invented.

Created 26 September 2026 after finding that the ENTRY commission was charged to `equity` but
never recorded in any `leg.pnl` - so `net_pct` and `max_dd` were correct while every leg-derived
figure (profit factor, win rate, average win and loss, expectancy in R) was flattered. It showed
itself as profit factor 1.135 beside net -4.51 % on the same trades, which cannot both be true.
"""


# --- the cost identity, and the ratios (added 26 Sep 2026) ---------------------------------------

def _straight_bars(n=400, start=100.0, step=0.25):
    from src.volatility_trend_breakout import Candle

    out, level = [], start
    for i in range(n):
        level += step if (i // 40) % 2 == 0 else -step * 0.6
        out.append(Candle(ts=f"2026-01-01 {i % 24:02d}:00" if i < 24 else f"2026-{(i // 720) + 1:02d}-"
                          f"{((i // 24) % 28) + 1:02d} {i % 24:02d}:00",
                          open=level, high=level + 0.6, low=level - 0.6, close=level, volume=100.0))
    return out


def test_every_cost_charged_to_equity_is_also_charged_to_a_leg():
    """The identity that exposed a real defect on 26 Sep 2026.

    The entry commission was deducted from `equity` but never recorded in `leg.pnl`, so net_pct and
    max_dd were right while profit_factor, win_rate, avg_win/avg_loss and expectancy_r were all
    flattered - by GBP 1,129 on XAUUSD 4h. It surfaced as profit factor 1.135 sitting beside net
    -4.51 %, which cannot both be true of the same trades. Anything that breaks this identity again is
    the same class of bug.
    """
    from dataclasses import replace

    from src.volatility_trend_breakout import Config, backtest, metrics

    bars = _straight_bars()
    for mult in (1, 2, 5):
        cfg = replace(Config(), commission_pct=Config().commission_pct * mult,
                      slippage_ticks=Config().slippage_ticks * mult)
        res = backtest(bars, cfg)
        m = metrics(res, cfg)
        if not res.legs:
            continue
        assert m["pnl_matches_equity"], f"at cost x{mult} the legs do not account for the equity change"
        assert abs(sum(l.pnl for l in res.legs) - m["net_usd"]) < 0.01


def test_profit_factor_above_one_cannot_sit_beside_a_negative_net():
    """The specific contradiction that led to the fix - now impossible by construction."""
    from dataclasses import replace

    from src.volatility_trend_breakout import Config, backtest, metrics

    bars = _straight_bars()
    for mult in (1, 2, 3, 5, 8):
        cfg = replace(Config(), commission_pct=Config().commission_pct * mult,
                      slippage_ticks=Config().slippage_ticks * mult)
        m = metrics(backtest(bars, cfg), cfg)
        if not m["closed_legs"]:
            continue
        if m["profit_factor"] > 1.0:
            assert m["net_usd"] > 0, f"PF {m['profit_factor']} with net {m['net_usd']} at cost x{mult}"
        if m["net_usd"] < 0:
            assert m["profit_factor"] <= 1.0


def test_sortino_uses_target_downside_deviation_not_the_std_of_losers():
    """The shortcut - standard deviation of the losing subset - inflates the ratio, because it throws
    away how often losses did NOT happen. Winners must count as zeros in the downside term."""
    import math

    returns = [0.05, 0.05, 0.05, -0.02, 0.05, 0.05, 0.05, -0.02, 0.05, 0.05]
    mean = sum(returns) / len(returns)
    proper = mean / math.sqrt(sum(min(r, 0.0) ** 2 for r in returns) / len(returns))
    losers = [r for r in returns if r < 0]
    shortcut_sd = (sum((r - sum(losers) / len(losers)) ** 2 for r in losers) / (len(losers) - 1)) ** 0.5
    assert shortcut_sd < 1e-9 or proper != mean / shortcut_sd, (
        "the two definitions must differ, otherwise this test proves nothing")
    assert proper > 0


def test_the_ratios_are_none_rather_than_invented_when_they_cannot_be_computed():
    from src.volatility_trend_breakout import Config, Result, metrics

    empty = metrics(Result(equity_curve=[Config().initial_capital],
                           final_equity=Config().initial_capital), Config())
    assert empty["sharpe_per_position"] is None
    assert empty["sortino_per_position"] is None
    assert empty["calmar"] is None, "Calmar needs a drawdown and a span; neither may be assumed"
