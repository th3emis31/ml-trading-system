"""Heatmaps and tracker curves for the market, trading and the system itself.

Pure functions over data that already exists - broker candles, closed MT5 deals, and the
system's own reports - so the Performance page shows only measured numbers:

* market_heatmaps: average return, range and share of up-bars by UTC hour x weekday, and
  monthly returns by year x month, from broker candles; plus best/worst hour and day rankings.
* trading_heatmaps: net P/L, trade count and win rate by hour x weekday and year x month from
  closed deals (optionally per magic number / EA), an equity curve and its drawdown.
* tracker series: system quality (System Doctor history), learning (model accuracy per
  training run), research (Strategy Lab counts per daily report) and paper-trading equity.

Cells with too few observations are reported with their count so the page can grey them out
instead of presenting noise as a pattern.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Optional

import numpy as np
import pandas as pd

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MIN_CELL_COUNT = 20
STATUS_SCORE = {"healthy": 100, "warnings": 60, "problems": 0}


def _round(value, digits=4):
    return None if value is None or (isinstance(value, float) and not np.isfinite(value)) else round(float(value), digits)


def _grid(rows: list, columns: list, values: dict, counts: dict) -> dict:
    return {"rows": list(rows), "columns": list(columns),
            "values": [[_round(values.get((r, c))) for c in columns] for r in rows],
            "counts": [[int(counts.get((r, c), 0)) for c in columns] for r in rows]}


def market_heatmaps(hourly: pd.DataFrame, daily: Optional[pd.DataFrame] = None, min_count: int = MIN_CELL_COUNT) -> dict:
    """Return/range patterns by UTC hour x weekday (hourly bars) and by year x month (daily bars)."""
    out: dict = {"available": False}
    if hourly is not None and len(hourly) > 50:
        frame = hourly.copy()
        frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
        close, open_ = frame["close"].astype(float), frame["open"].astype(float)
        frame["ret_pct"] = (close / open_ - 1) * 100
        frame["range_pct"] = (frame["high"].astype(float) - frame["low"].astype(float)) / open_ * 100
        frame["up"] = (close > open_).astype(float)
        frame["hour"] = frame["datetime"].dt.hour
        frame["weekday"] = frame["datetime"].dt.weekday
        grouped = frame.groupby(["weekday", "hour"])
        counts = grouped.size().to_dict()
        hours = list(range(24))
        weekdays = sorted(frame["weekday"].unique().tolist())
        labels = [WEEKDAYS[d] for d in weekdays]

        def keyed(series):
            return {(WEEKDAYS[d], h): v for (d, h), v in series.items()}

        keyed_counts = {(WEEKDAYS[d], h): n for (d, h), n in counts.items()}
        out["hour_weekday"] = {
            "avg_return_pct": _grid(labels, hours, keyed(grouped["ret_pct"].mean()), keyed_counts),
            "avg_range_pct": _grid(labels, hours, keyed(grouped["range_pct"].mean()), keyed_counts),
            "up_share": _grid(labels, hours, keyed(grouped["up"].mean()), keyed_counts),
            "min_count": min_count,
        }
        by_hour = frame.groupby("hour").agg(avg_return_pct=("ret_pct", "mean"), avg_range_pct=("range_pct", "mean"),
                                            up_share=("up", "mean"), bars=("ret_pct", "size")).reset_index()
        by_hour = by_hour[by_hour["bars"] >= min_count]
        by_day = frame.groupby("weekday").agg(avg_return_pct=("ret_pct", "mean"), avg_range_pct=("range_pct", "mean"),
                                              up_share=("up", "mean"), bars=("ret_pct", "size")).reset_index()
        out["rankings"] = {
            "most_active_hours": [{"hour": int(r.hour), "avg_range_pct": _round(r.avg_range_pct), "bars": int(r.bars)}
                                  for r in by_hour.sort_values("avg_range_pct", ascending=False).head(5).itertuples()],
            "strongest_up_hours": [{"hour": int(r.hour), "avg_return_pct": _round(r.avg_return_pct), "up_share": _round(r.up_share), "bars": int(r.bars)}
                                   for r in by_hour.sort_values("avg_return_pct", ascending=False).head(5).itertuples()],
            "strongest_down_hours": [{"hour": int(r.hour), "avg_return_pct": _round(r.avg_return_pct), "up_share": _round(r.up_share), "bars": int(r.bars)}
                                     for r in by_hour.sort_values("avg_return_pct").head(5).itertuples()],
            "weekdays": [{"weekday": WEEKDAYS[int(r.weekday)], "avg_return_pct": _round(r.avg_return_pct),
                          "avg_range_pct": _round(r.avg_range_pct), "bars": int(r.bars)} for r in by_day.itertuples()],
        }
        out["hourly_bars"] = int(len(frame))
        out["hourly_period"] = f"{frame['datetime'].iloc[0]:%Y-%m-%d} to {frame['datetime'].iloc[-1]:%Y-%m-%d}"
        out["available"] = True
    if daily is not None and len(daily) > 40:
        d = daily.copy()
        d["datetime"] = pd.to_datetime(d["datetime"], utc=True)
        monthly = d.set_index("datetime")["close"].astype(float).resample("ME").last().dropna()
        returns = monthly.pct_change().dropna() * 100
        values = {(int(ts.year), MONTHS[ts.month - 1]): float(v) for ts, v in returns.items()}
        years = sorted({y for y, _ in values}, reverse=True)
        out["year_month"] = _grid(years, list(MONTHS), values, {k: 1 for k in values})
        yearly = d.set_index("datetime")["close"].astype(float).resample("YE").last().dropna().pct_change().dropna() * 100
        out["yearly_return_pct"] = [{"year": int(ts.year), "return_pct": _round(v, 2)} for ts, v in yearly.items()]
        out["daily_period"] = f"{d['datetime'].iloc[0]:%Y-%m-%d} to {d['datetime'].iloc[-1]:%Y-%m-%d}"
        out["available"] = True
    return out


# Every magic this system trades under. It listed only two for a long time, so the Performance page's
# "what did this system earn?" filter silently excluded the sweep, the daily-plan executor, the gold
# 4H model executor and the auto-trade route - four of the six. Anything added here must be a magic
# THIS system places orders with; the owner's other experts stay out, because mixing their trades in
# is what makes the question unanswerable.
SYSTEM_MAGICS = (
    440401,   # demo_executor, the gold 4H model ("GOLD4H demo model")
    440502,   # gold session pullback
    440603,   # volatility trend breakout
    440704,   # daily plan executor
    440805,   # sweep reversal
    903110,   # SmartEntry auto trader - SEE THE CAVEAT BELOW; also mt5_service's default magic
)

# 903110 is the SmartEntry auto trader, which the owner confirmed on 24 September 2026 IS part of this
# system, so it is counted. But it needs a caveat wherever it is reported, because it is also the
# DEFAULT value of the ``magic`` parameter in trading/mt5_service.py: any caller that does not set a
# magic - a dashboard button, a panel, a manual click - lands on the same number and becomes
# indistinguishable from the auto trader's own work.
#
# That ambiguity is not theoretical. This account carries 34 distinct magic numbers and thousands of
# trades from the owner's other experts, and over the period 903110 shows 94 closed trades the
# system's own execution journal has no record of placing (it recorded 8 events and 4 tickets, all
# magic 440401). So the number is reported WITH the caveat rather than as a clean figure, and the
# real repair is to give the auto trader its own explicit magic so future trades are attributable.
SHARED_DEFAULT_MAGICS = {

    903110: ("SmartEntry auto trader - but this is also mt5_service's DEFAULT magic, so anything else routed "
             "through that call without its own magic is counted here too"),
}


def trading_heatmaps(deals: Iterable[dict], magic=None) -> dict:
    """P/L patterns and equity curve from closed deals: dicts with time (UTC epoch or ISO), net or profit/swap/commission.

    ``magic`` may be one number, a list of numbers, or None for every expert on the account. The account holds other
    people's experts as well, and mixing their trades with this system's makes it impossible to say what this system
    earned - so the page asks for a list rather than showing the account total by default.
    """
    wanted = None
    if magic is not None:
        wanted = {int(m) for m in (magic if isinstance(magic, (list, tuple, set)) else [magic])}
    rows = []
    for deal in deals or []:
        if wanted is not None and int(deal.get("magic") or 0) not in wanted:
            continue
        stamp = deal.get("time")
        ts = pd.to_datetime(stamp, unit="s", utc=True) if isinstance(stamp, (int, float)) else pd.to_datetime(stamp, utc=True)
        net = deal.get("net")
        if net is None:
            net = float(deal.get("profit") or 0) + float(deal.get("swap") or 0) + float(deal.get("commission") or 0)
        rows.append({"time": ts, "net": float(net), "symbol": deal.get("symbol"), "magic": int(deal.get("magic") or 0),
                     "comment": str(deal.get("comment") or "")})
    if not rows:
        return {"available": False, "reason": "no closed trades"}
    frame = pd.DataFrame(rows).sort_values("time").reset_index(drop=True)
    frame["win"] = (frame["net"] > 0).astype(float)
    frame["hour"], frame["weekday"] = frame["time"].dt.hour, frame["time"].dt.weekday
    frame["year"], frame["month"] = frame["time"].dt.year, frame["time"].dt.month

    grouped = frame.groupby(["weekday", "hour"])
    weekdays = sorted(frame["weekday"].unique().tolist())
    keyed = lambda series: {(WEEKDAYS[d], h): v for (d, h), v in series.items()}
    count_map = keyed(grouped.size())
    hour_weekday = {"net": _grid([WEEKDAYS[d] for d in weekdays], list(range(24)), keyed(grouped["net"].sum()), count_map),
                    "win_rate": _grid([WEEKDAYS[d] for d in weekdays], list(range(24)), keyed(grouped["win"].mean()), count_map)}
    ym = frame.groupby(["year", "month"])
    years = sorted(frame["year"].unique().tolist(), reverse=True)
    ym_keyed = lambda series: {(int(y), MONTHS[int(m) - 1]): v for (y, m), v in series.items()}
    year_month = {"net": _grid(years, list(MONTHS), ym_keyed(ym["net"].sum()), ym_keyed(ym.size())),
                  "win_rate": _grid(years, list(MONTHS), ym_keyed(ym["win"].mean()), ym_keyed(ym.size()))}

    equity = frame["net"].cumsum()
    drawdown = equity - equity.cummax()
    step = max(1, len(frame) // 600)  # keep the curve light for the browser
    curve = [{"time": frame["time"].iloc[i].strftime("%Y-%m-%d %H:%M"), "equity": _round(equity.iloc[i], 2),
              "drawdown": _round(drawdown.iloc[i], 2)} for i in list(range(0, len(frame), step)) + [len(frame) - 1]]
    wins, losses = frame.loc[frame["net"] > 0, "net"].sum(), -frame.loc[frame["net"] <= 0, "net"].sum()
    by_magic = frame.groupby("magic").agg(trades=("net", "size"), net=("net", "sum"), win_rate=("win", "mean")).reset_index()
    by_hour = frame.groupby("hour").agg(trades=("net", "size"), net=("net", "sum"), win_rate=("win", "mean")).reset_index()
    return {
        "available": True,
        "trades": int(len(frame)), "net": _round(frame["net"].sum(), 2), "win_rate": _round(frame["win"].mean()),
        "profit_factor": _round(wins / losses, 3) if losses > 0 else None,
        "max_drawdown": _round(drawdown.min(), 2),
        "period": f"{frame['time'].iloc[0]:%Y-%m-%d} to {frame['time'].iloc[-1]:%Y-%m-%d}",
        "hour_weekday": hour_weekday, "year_month": year_month, "equity_curve": curve,
        "by_magic": [{"magic": int(r.magic), "trades": int(r.trades), "net": _round(r.net, 2), "win_rate": _round(r.win_rate),
                      **_magic_identity(frame[frame["magic"] == r.magic])}
                     for r in by_magic.sort_values("net", ascending=False).itertuples()],
        "best_hours": [{"hour": int(r.hour), "net": _round(r.net, 2), "trades": int(r.trades), "win_rate": _round(r.win_rate)}
                       for r in by_hour.sort_values("net", ascending=False).head(5).itertuples()],
        "worst_hours": [{"hour": int(r.hour), "net": _round(r.net, 2), "trades": int(r.trades), "win_rate": _round(r.win_rate)}
                        for r in by_hour.sort_values("net").head(5).itertuples()],
    }


def _magic_identity(rows: pd.DataFrame) -> dict:
    """What a magic number's trades look like: symbols, most common comments, first and last close."""
    comments = [c for c in rows["comment"].tolist() if c and not c.startswith(("[sl", "[tp", "so:"))]
    common = pd.Series(comments).value_counts().head(3).index.tolist() if comments else []
    return {"symbols": sorted({s for s in rows["symbol"].tolist() if s}), "comments": common,
            "first_close": rows["time"].min().strftime("%Y-%m-%d"), "last_close": rows["time"].max().strftime("%Y-%m-%d %H:%M")}


def quality_tracker(doctor_history: list) -> list:
    return [{"time": item.get("generated_at"), "score": STATUS_SCORE.get(item.get("overall"), None),
             "overall": item.get("overall"), "ok": (item.get("counts") or {}).get("ok"),
             "warn": (item.get("counts") or {}).get("warn"), "fail": (item.get("counts") or {}).get("fail"),
             "deep": bool(item.get("deep"))} for item in doctor_history or [] if item.get("generated_at")]


def learning_tracker(decisions: list) -> list:
    points = []
    for item in decisions or []:
        if not item.get("trained_at"):
            continue
        points.append({"time": str(item["trained_at"])[:16].replace("T", " "), "symbol": item.get("symbol"),
                       "rf_accuracy": _round(item.get("accuracy")), "lstm_accuracy": _round(item.get("lstm_accuracy")),
                       "rf_promoted": item.get("rf_promoted"), "lstm_promoted": item.get("lstm_promoted")})
    return sorted(points, key=lambda p: p["time"])


def research_tracker(daily_reports: list) -> list:
    points = []
    for report in daily_reports or []:
        markets = ((report.get("system") or {}).get("strategy_lab") or {}).get("markets") or {}
        if not markets:
            continue
        totals = defaultdict(int)
        for counts in markets.values():
            for key in ("evaluated", "validated", "holdout_passed"):
                totals[key] += int((counts or {}).get(key) or 0)
        points.append({"time": report.get("generated_at"), **totals})
    return sorted(points, key=lambda p: p["time"] or "")


def paper_tracker(closed_trades: list) -> list:
    equity, points = 1.0, []
    for trade in closed_trades or []:
        equity *= 1 + float(trade.get("net_pct") or 0) / 100
        points.append({"time": trade.get("exit_time"), "equity_pct": _round((equity - 1) * 100, 3), "outcome": trade.get("outcome")})
    return points


GROWTH_PERIODS = {"daily": "D", "weekly": "W-MON", "monthly": "MS", "yearly": "YS"}


def growth_tracker(deals: Iterable[dict], magic=None, starting_balance: Optional[float] = None) -> dict:
    """Win/loss and money earned per day, week, month and year.

    The owner asks two questions constantly - is it winning, and how much - and until now the answer
    lived in heatmap cells that show a pattern rather than a total. This gives the plain figures:
    how many trades, how many won, how many lost, how much was won, how much was lost, and the net,
    for each period, with a running balance so growth is visible rather than inferred.

    Won and lost are reported SEPARATELY, not just netted. A month that made 500 and lost 480 nets
    20, and so does a month that made 30 and lost 10; they are not the same month, and a single net
    figure hides which one you had.

    Only closed deals count. An open position has no result yet, and counting floating profit as
    growth is how a losing run looks like a winning one right up until it closes.
    """
    wanted = None
    if magic is not None:
        wanted = {int(m) for m in (magic if isinstance(magic, (list, tuple, set)) else [magic])}
    rows = []
    for deal in deals or []:
        if wanted is not None and int(deal.get("magic") or 0) not in wanted:
            continue
        stamp = deal.get("time")
        ts = pd.to_datetime(stamp, unit="s", utc=True) if isinstance(stamp, (int, float)) else pd.to_datetime(stamp, utc=True)
        net = deal.get("net")
        if net is None:
            net = float(deal.get("profit") or 0) + float(deal.get("swap") or 0) + float(deal.get("commission") or 0)
        rows.append({"time": ts, "net": float(net), "symbol": str(deal.get("symbol") or ""),
                     "magic": int(deal.get("magic") or 0)})
    if not rows:
        return {"available": False, "reason": "no closed trades yet"}

    frame = pd.DataFrame(rows).sort_values("time").reset_index(drop=True)
    frame["won"] = frame["net"].clip(lower=0.0)
    frame["lost"] = (-frame["net"]).clip(lower=0.0)
    frame["is_win"] = (frame["net"] > 0).astype(int)
    frame["is_loss"] = (frame["net"] < 0).astype(int)

    def summarise(group) -> dict:
        trades = int(len(group))
        wins, losses = int(group["is_win"].sum()), int(group["is_loss"].sum())
        won, lost = float(group["won"].sum()), float(group["lost"].sum())
        decided = wins + losses          # break-even trades are neither, so they must not dilute the rate
        return {
            "trades": trades, "wins": wins, "losses": losses,
            "win_rate": _round(wins / decided, 4) if decided else None,
            "won": _round(won, 2), "lost": _round(lost, 2), "net": _round(won - lost, 2),
            # None, never infinity: json.dumps emits a bare `Infinity`, which is not valid JSON and
            # breaks JSON.parse in the browser - so a flawless run of winners would blank the page.
            # "no losses yet" is the honest reading of an undefined profit factor anyway.
            "profit_factor": _round(won / lost, 3) if lost > 0 else None,
            "all_wins": bool(lost == 0 and won > 0),
            "best": _round(float(group["net"].max()), 2), "worst": _round(float(group["net"].min()), 2),
        }

    periods = {}
    for name, rule in GROWTH_PERIODS.items():
        out, running = [], float(starting_balance or 0.0)
        for stamp, group in frame.resample(rule, on="time"):
            if group.empty:              # skip quiet periods rather than pad the table with zero rows
                continue
            row = summarise(group)
            running += row["net"]
            row["period"] = stamp.strftime("%Y-%m-%d" if name in ("daily", "weekly") else
                                           "%Y-%m" if name == "monthly" else "%Y")
            row["balance"] = _round(running, 2) if starting_balance is not None else None
            out.append(row)
        periods[name] = out

    totals = summarise(frame)
    totals["first_trade"] = frame["time"].iloc[0].strftime("%Y-%m-%d %H:%M")
    totals["last_trade"] = frame["time"].iloc[-1].strftime("%Y-%m-%d %H:%M")
    by_symbol = {sym: summarise(g) for sym, g in frame.groupby("symbol")}
    by_magic = {str(m): summarise(g) for m, g in frame.groupby("magic")}
    return {"available": True, "totals": totals, "periods": periods,
            "by_symbol": by_symbol, "by_strategy": by_magic,
            "counted_magics": sorted(wanted) if wanted else "all"}
