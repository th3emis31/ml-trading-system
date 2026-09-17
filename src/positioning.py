"""Speculative positioning from the CFTC's Commitments of Traders report (owner request 2026-09-17).

What the big speculators hold in gold, silver, bitcoin, ether and the major currencies, with the same honesty the
report itself demands:

* The numbers are a Tuesday snapshot published the following Friday at 15:30 ET, so they are three to nine days old
  when you read them. Every payload carries ``as_of``, ``released``, ``age_days`` and ``live: False``.
* Source: the CFTC's own Legacy futures-only history files (https://www.cftc.gov/files/dea/history/deacot<year>.zip),
  cached per year under data/positioning. Nothing is estimated: a market missing from the file is reported missing.
* "Non-commercial" = large speculators (funds). Net = long - short; the share of open interest and the multi-year
  percentile say whether that crowding is normal or stretched for this market.
* There is no US Dollar Index contract in this report (it trades on ICE), so the dollar line is a derived proxy: the
  summed net of the six currency contracts, inverted, and labelled as a proxy.

Positioning is context, not a signal: it changes no plan, size or order here.

Run: python -m src.positioning [--years 6] [--force]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import urllib.request
import zipfile
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .runtime_paths import read_latest_json, smartentry_data_dir

HISTORY_URL = "https://www.cftc.gov/files/dea/history/deacot{year}.zip"
DEFAULT_YEARS = 6
CURRENCY_MARKETS = ("EUR", "GBP", "JPY", "AUD", "CAD", "CHF")
MARKETS = {
    "GOLD": "GOLD - COMMODITY EXCHANGE INC.",
    "SILVER": "SILVER - COMMODITY EXCHANGE INC.",
    "BITCOIN": "BITCOIN - CHICAGO MERCANTILE EXCHANGE",
    "ETHER": "ETHER CASH SETTLED - CHICAGO MERCANTILE EXCHANGE",
    "EUR": "EURO FX - CHICAGO MERCANTILE EXCHANGE",
    "GBP": "BRITISH POUND - CHICAGO MERCANTILE EXCHANGE",
    "JPY": "JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE",
    "AUD": "AUSTRALIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE",
    "CAD": "CANADIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE",
    "CHF": "SWISS FRANC - CHICAGO MERCANTILE EXCHANGE",
}
SYSTEM_SYMBOLS = {"GOLD": "XAUUSD", "BITCOIN": "BTCUSD"}   # the two markets this system trades
COLUMNS = ("market", "as_of", "open_interest", "long", "short", "spreading", "comm_long", "comm_short",
           "small_long", "small_short")
CACHE_SCHEMA = 2            # 1 = speculators only; 2 adds commercials and small traders, so version-1 files are refetched
COT_INDEX_WEEKS = 156       # three years, the usual window for the COT index


def positioning_dir() -> Path:
    return smartentry_data_dir() / "positioning"


def year_cache(year: int) -> Path:
    return positioning_dir() / f"cot_{year}.csv"


def fetch_year(year: int, force: bool = False, opener=urllib.request.urlopen) -> tuple[int, str]:
    """Cache one year of the Legacy futures-only report, filtered to the markets above. Returns (rows, note)."""
    path = year_cache(year)
    if path.exists() and not force:
        with path.open(encoding="utf-8") as fh:
            header = fh.readline().strip().split(",")
            if header == list(COLUMNS):
                return sum(1 for _ in fh), "cached"
    request = urllib.request.Request(HISTORY_URL.format(year=year), headers={"User-Agent": "SmartEntry research"})
    with opener(request, timeout=300) as response:
        blob = response.read()
    archive = zipfile.ZipFile(io.BytesIO(blob))
    text = archive.read(archive.namelist()[0]).decode("utf-8", "replace")
    wanted = {name: key for key, name in MARKETS.items()}
    rows = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) > 16 and row[0].strip() in wanted:
            rows.append([wanted[row[0].strip()], row[2].strip()] + [row[i].strip() for i in (7, 8, 9, 10, 11, 12, 15, 16)])
    rows.sort(key=lambda r: (r[1], r[0]))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)
        writer.writerows(rows)
    return len(rows), "downloaded"


def load_history(years: int = DEFAULT_YEARS, now=None) -> dict[str, list[dict]]:
    """Per market, every weekly row we have, oldest first."""
    now = now or datetime.now(timezone.utc)
    history: dict[str, list[dict]] = {key: [] for key in MARKETS}
    for year in range(now.year - years + 1, now.year + 1):
        path = year_cache(year)
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row["market"] not in history:
                    continue
                try:
                    long_, short_ = int(row["long"]), int(row["short"])
                    comm_long, comm_short = int(row.get("comm_long") or 0), int(row.get("comm_short") or 0)
                    small_long, small_short = int(row.get("small_long") or 0), int(row.get("small_short") or 0)
                    history[row["market"]].append({"as_of": row["as_of"], "open_interest": int(row["open_interest"]),
                                                   "long": long_, "short": short_, "spreading": int(row["spreading"]),
                                                   "net": long_ - short_, "comm_net": comm_long - comm_short,
                                                   "small_net": small_long - small_short})
                except (TypeError, ValueError):
                    continue
    for rows in history.values():
        rows.sort(key=lambda r: r["as_of"])
    return history


def _percentile(values: list[float], value: float) -> Optional[float]:
    if len(values) < 20:
        return None
    return round(100.0 * sum(1 for v in values if v <= value) / len(values), 1)


def cot_index(nets: list[float], weeks: int = COT_INDEX_WEEKS) -> Optional[float]:
    """Williams COT index: where the latest net sits between the lowest and highest net of the last ``weeks``."""
    window = nets[-weeks:]
    if len(window) < 26:
        return None
    low, high = min(window), max(window)
    return None if high == low else round(100.0 * (window[-1] - low) / (high - low), 1)


def _change(rows: list[dict], back: int) -> Optional[int]:
    return rows[-1]["net"] - rows[-1 - back]["net"] if len(rows) > back else None


def forward_study(rows: list[dict], closes: list[tuple[str, float]], weeks_ahead: int = 4) -> dict:
    """What the price did after each weekly reading, grouped by COT index band.

    ``closes`` is (date, close) oldest first from the broker's daily candles. For every past week the COT index is
    recomputed from the data available then, the price on that Tuesday is matched, and the move ``weeks_ahead`` later
    is measured. Descriptive history from one market's own past - not a rule, and not a prediction.
    """
    if len(rows) < 60 or len(closes) < 60:
        return {"available": False, "reason": "not enough weekly rows or prices"}
    dates = [d for d, _ in closes]
    values = [c for _, c in closes]

    def close_on(day: str) -> Optional[float]:
        i = bisect_right(dates, day) - 1
        return values[i] if i >= 0 else None

    buckets = {"crowded short (index <= 20)": [], "middle (20-80)": [], "crowded long (index >= 80)": []}
    nets = [r["net"] for r in rows]
    for i in range(26, len(rows)):
        index_then = cot_index(nets[: i + 1])
        if index_then is None:
            continue
        start = close_on(rows[i]["as_of"])
        ahead = datetime.strptime(rows[i]["as_of"], "%Y-%m-%d") + timedelta(weeks=weeks_ahead)
        end = close_on(ahead.strftime("%Y-%m-%d"))
        if not start or not end or ahead > datetime.strptime(dates[-1], "%Y-%m-%d"):
            continue
        move = 100.0 * (end / start - 1)
        key = ("crowded short (index <= 20)" if index_then <= 20 else
               "crowded long (index >= 80)" if index_then >= 80 else "middle (20-80)")
        buckets[key].append(move)
    out = {}
    for name, moves in buckets.items():
        out[name] = {"weeks": len(moves),
                     "avg_move_pct": round(sum(moves) / len(moves), 2) if moves else None,
                     "up_share_pct": round(100.0 * sum(1 for m in moves if m > 0) / len(moves), 1) if moves else None,
                     "evidence": "sufficient" if len(moves) >= 30 else "insufficient (under 30 weeks)"}
    return {"available": True, "weeks_ahead": weeks_ahead, "buckets": out,
            "note": "History of this market only, measured on broker candles; the bands were not chosen from the result. "
                    "It describes what happened, it does not say what will happen."}


def market_row(key: str, rows: list[dict]) -> dict:
    if not rows:
        return {"market": key, "available": False, "reason": "not in the cached CFTC files"}
    last, prev = rows[-1], (rows[-2] if len(rows) > 1 else None)
    oi = last["open_interest"] or 0
    net_pct = round(100.0 * last["net"] / oi, 1) if oi else None
    pct_series = [100.0 * r["net"] / r["open_interest"] for r in rows if r["open_interest"]]
    return {
        "market": key, "available": True, "symbol": SYSTEM_SYMBOLS.get(key),
        "as_of": last["as_of"], "weeks_of_history": len(rows),
        "long": last["long"], "short": last["short"], "spreading": last["spreading"], "open_interest": oi,
        "net": last["net"], "net_pct_of_open_interest": net_pct,
        "stance": "net long" if last["net"] > 0 else "net short" if last["net"] < 0 else "flat",
        "week_change_net": last["net"] - prev["net"] if prev else None,
        "week_change_direction": None if not prev else ("more long" if last["net"] > prev["net"] else
                                                        "more short" if last["net"] < prev["net"] else "unchanged"),
        "commercial_net": last.get("comm_net"), "small_trader_net": last.get("small_net"),
        "open_interest_change": oi - prev["open_interest"] if prev else None,
        "change_4w": _change(rows, 4), "change_13w": _change(rows, 13),
        "cot_index": cot_index([r["net"] for r in rows]),
        "flipped": None if not prev else ("to net long" if prev["net"] <= 0 < last["net"]
                                          else "to net short" if prev["net"] >= 0 > last["net"] else None),
        "sparkline": [r["net"] for r in rows[-104:]],
        "sparkline_weeks": min(len(rows), 104),
        "percentile_net_pct": _percentile(pct_series, pct_series[-1]) if pct_series else None,
        "extreme": None if not pct_series or _percentile(pct_series, pct_series[-1]) is None else
        ("crowded long (top 10 % of the last " + str(len(pct_series)) + " weeks)" if _percentile(pct_series, pct_series[-1]) >= 90
         else "crowded short (bottom 10 %)" if _percentile(pct_series, pct_series[-1]) <= 10 else None),
    }


def dollar_proxy(rows: list[dict]) -> dict:
    """Speculators' dollar stance derived from the six currency contracts (no ICE dollar index in this report)."""
    usable = [r for r in rows if r.get("available") and r["market"] in CURRENCY_MARKETS]
    if len(usable) < 4:
        return {"available": False, "reason": "not enough currency markets in the cached files"}
    net_sum = sum(r["net"] for r in usable)
    return {"available": True, "market": "USD (proxy)", "as_of": max(r["as_of"] for r in usable),
            "components": [r["market"] for r in usable], "net_of_currencies": net_sum,
            "stance": "net long dollars" if net_sum < 0 else "net short dollars" if net_sum > 0 else "flat",
            "note": "Derived: the summed net position of " + ", ".join(r["market"] for r in usable) +
                    ", inverted. The US Dollar Index trades on ICE and is not in this report."}


def next_release(as_of: str, now=None) -> dict:
    """The report is Tuesday's snapshot, published the Friday after at 15:30 ET (19:30 UTC in summer)."""
    now = now or datetime.now(timezone.utc)
    snapshot = datetime.strptime(as_of, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    released = snapshot + timedelta(days=3, hours=19, minutes=30)     # Tuesday -> Friday 15:30 ET
    age = now - snapshot
    return {"as_of_tuesday": as_of, "released": released.strftime("%Y-%m-%d %H:%M UTC"),
            "age_days": round(age.total_seconds() / 86400, 1),
            "next_release_estimate": (released + timedelta(days=7)).strftime("%Y-%m-%d %H:%M UTC"),
            "live": False, "schedule": "Tuesday positions, published Friday 15:30 ET"}


def _broker_weekly_closes(symbol: str) -> list[tuple[str, float]]:
    """(date, close) from the app's broker daily candles; empty when the app or MT5 is not available."""
    try:
        from .mtf_data import fetch_app_bars

        frame = fetch_app_bars(symbol, "1d", 6000)
        if frame is None or frame.empty:
            return []
        return [(str(t)[:10], float(c)) for t, c in zip(frame["datetime"], frame["close"])]
    except Exception:
        return []


def build_report(years: int = DEFAULT_YEARS, now=None, price_loader=_broker_weekly_closes) -> dict:
    now = now or datetime.now(timezone.utc)
    history = load_history(years, now)
    rows = [market_row(key, history.get(key, [])) for key in MARKETS]
    available = [r for r in rows if r.get("available")]
    as_of = max((r["as_of"] for r in available), default=None)
    return {
        "available": bool(available),
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "CFTC Commitments of Traders, Legacy futures-only (cftc.gov)",
        "report": next_release(as_of, now) if as_of else None,
        "traders": "non-commercial (large speculators)",
        "markets": rows,
        "usd_proxy": dollar_proxy(rows),
        "system_markets": {r["symbol"]: {**{k: r[k] for k in ("as_of", "net", "net_pct_of_open_interest", "stance",
                                                              "week_change_net", "change_4w", "cot_index",
                                                              "percentile_net_pct", "commercial_net", "extreme", "flipped")},
                                         "what_happened_next": forward_study(history.get(r["market"], []),
                                                                             price_loader(r["symbol"]))}
                           for r in available if r.get("symbol")},
        "note": "Positioning is context, not a signal. It is a weekly snapshot, already days old when published, and it "
                "changes no plan, position size or order in this system.",
    }


def refresh(years: int = DEFAULT_YEARS, force: bool = False, now=None) -> dict:
    """Download the current year (and any missing earlier year), then rebuild the report."""
    now = now or datetime.now(timezone.utc)
    fetched = {}
    for year in range(now.year - years + 1, now.year + 1):
        try:
            rows, note = fetch_year(year, force=force or year == now.year)
            fetched[year] = f"{rows} rows ({note})"
        except Exception as exc:                      # a missing year must not lose the years already cached
            fetched[year] = f"failed: {exc}"
    report = build_report(years, now)
    report["files"] = fetched
    path = positioning_dir() / "latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report





if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CFTC speculative positioning (context only, never a signal).")
    parser.add_argument("--years", type=int, default=DEFAULT_YEARS)
    parser.add_argument("--force", action="store_true", help="re-download every year, not just the current one")
    args = parser.parse_args()
    result = refresh(args.years, args.force)
    if not result["available"]:
        raise SystemExit("no positioning data")
    r = result["report"]
    print(f"CFTC {r['as_of_tuesday']} (released {r['released']}, {r['age_days']} days old)")
    for row in result["markets"]:
        if row.get("available"):
            print(f"  {row['market']:8s} {row['stance']:10s} net {row['net']:>9,} ({row['net_pct_of_open_interest']}% of OI)"
                  f"  week {row['week_change_net']:+,}  percentile {row['percentile_net_pct']}"
                  + (f"  {row['extreme']}" if row["extreme"] else ""))
    print(" ", result["usd_proxy"].get("stance"), result["usd_proxy"].get("net_of_currencies"))
