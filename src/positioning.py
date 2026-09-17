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
COLUMNS = ("market", "as_of", "open_interest", "long", "short", "spreading")


def positioning_dir() -> Path:
    return smartentry_data_dir() / "positioning"


def year_cache(year: int) -> Path:
    return positioning_dir() / f"cot_{year}.csv"


def fetch_year(year: int, force: bool = False, opener=urllib.request.urlopen) -> tuple[int, str]:
    """Cache one year of the Legacy futures-only report, filtered to the markets above. Returns (rows, note)."""
    path = year_cache(year)
    if path.exists() and not force:
        return sum(1 for _ in path.open(encoding="utf-8")) - 1, "cached"
    request = urllib.request.Request(HISTORY_URL.format(year=year), headers={"User-Agent": "SmartEntry research"})
    with opener(request, timeout=300) as response:
        blob = response.read()
    archive = zipfile.ZipFile(io.BytesIO(blob))
    text = archive.read(archive.namelist()[0]).decode("utf-8", "replace")
    wanted = {name: key for key, name in MARKETS.items()}
    rows = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) > 10 and row[0].strip() in wanted:
            rows.append([wanted[row[0].strip()], row[2].strip(), row[7].strip(), row[8].strip(), row[9].strip(), row[10].strip()])
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
                    history[row["market"]].append({"as_of": row["as_of"], "open_interest": int(row["open_interest"]),
                                                   "long": long_, "short": short_, "spreading": int(row["spreading"]),
                                                   "net": long_ - short_})
                except (TypeError, ValueError):
                    continue
    for rows in history.values():
        rows.sort(key=lambda r: r["as_of"])
    return history


def _percentile(values: list[float], value: float) -> Optional[float]:
    if len(values) < 20:
        return None
    return round(100.0 * sum(1 for v in values if v <= value) / len(values), 1)


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


def build_report(years: int = DEFAULT_YEARS, now=None) -> dict:
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
        "system_markets": {r["symbol"]: {k: r[k] for k in ("as_of", "net", "net_pct_of_open_interest", "stance",
                                                           "week_change_net", "percentile_net_pct", "extreme")}
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
