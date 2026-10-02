"""Pre-market data gatherer. Collects raw facts into packet.json and judges nothing.

No conviction scores, no buckets, no opinions. Every judgement happens later in the AI prompts that
read this packet. The only logic here is arithmetic and the two deterministic eligibility flags from
WATCHLIST_CRITERIA.md, which are computed in code on purpose so no model can talk itself past a rule.

Free and keyless only: yfinance, feedparser, requests. zoneinfo is stdlib.

Run:  python scan.py
Out:  packet.json in the working directory
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import requests
import yfinance as yf

ET = ZoneInfo("America/New_York")
OUT = Path("packet.json")
CAL_CACHE = Path(".ff_calendar_cache.json")
CAL_TTL_SEC = 4 * 3600          # the feed returns 429 on rapid calls, so the week is cached

# Yahoo's gold is not the broker's spot gold. GC=F is the future and carries a basis of roughly 1.4%
# against spot, so these two rows are market CONTEXT only. Never price a trade off them.
# Gold is a list because Yahoo 404s on XAUUSD=X. The first symbol that returns data wins, and the
# packet records which one answered so nobody reads the gold future as broker spot.
INSTRUMENTS = {
    "Gold (XAUUSD)": ["XAUUSD=X", "GC=F"], "Bitcoin (BTCUSD)": "BTC-USD",
    "S&P 500": "^GSPC", "Dow": "^DJI", "Nasdaq": "^IXIC", "Russell 2000": "^RUT",
    "VIX": "^VIX", "US 10Y": "^TNX", "US 3M": "^IRX", "WTI Oil": "CL=F", "Dollar (DXY)": "DX-Y.NYB",
}

UNIVERSE = ("NVDA AMD AVGO SMCI MRVL TSLA AAPL MSFT META AMZN GOOGL NFLX DELL SNOW PLTR COIN MSTR "
            "SOFI RIVN NIO MARA RIOT BA DIS JPM BAC XOM CVX HOOD UBER CRWD PANW CELH LULU NKE CAVA "
            "DKNG ARM INTC MU").split()

FEEDS = {
    "MarketWatch Top": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "MarketWatch RealTime": "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
    "CNBC": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
    "Google News Markets": ("https://news.google.com/rss/search?q=markets+OR+earnings+when:1d"
                            "&hl=en-US&gl=US&ceid=US:en"),
}

PRIMARY_PUBLISHERS = ("bloomberg", "reuters", "cnbc", "marketwatch", "barron", "yahoo finance",
                      "wsj", "wall street journal", "financial times", "associated press", "ap news")

# A headline only counts as a catalyst if it names the ticker on a word boundary, or carries a
# DISTINCTIVE company name token of four or more letters. Three traps this closes, each one seen in
# real data: generic words like Applied, Digital, Holdings, Technologies, Strategy, Energy and Motors
# must never match a company alone, because "Applied" hits both Applied Optoelectronics and Applied
# Digital and would hand one firm's news to the other as its catalyst; a ticker that is also an
# ordinary English word (ON, ALL, IT, KEY, NOW) matched plain market copy such as "traders focused ON
# the jobs report", which is how a stock with no news ends up flagged as having one; and a company
# whose every word is generic, Applied Digital being exactly that, was left with no usable token at
# all, so the full name is matched as a PHRASE instead, which is distinctive even when no single word
# in it is.
NAME_STOP = {
    "the", "inc", "inc.", "corp", "corp.", "corporation", "company", "co", "co.", "ltd", "limited",
    "plc", "holdings", "holding", "group", "technologies", "technology", "tech", "systems",
    "solutions", "services", "international", "global", "industries", "enterprises", "partners",
    "capital", "financial", "bancorp", "bank", "trust", "fund", "class", "common", "stock", "shares",
    "digital", "applied", "advanced", "strategy", "strategies", "motors", "motor", "energy",
    "platforms", "platform", "communications", "media", "pharmaceuticals", "pharma", "sciences",
    "science", "health", "healthcare", "resources", "materials", "mining", "gold", "silver", "data",
    "cloud", "software", "semiconductor", "semiconductors", "micro", "devices", "electric", "new",
    "american", "america", "us", "usa", "national", "first", "general", "united",
}

# Tickers that are also ordinary English words. These never match on the ticker alone: the headline
# has to carry a distinctive company token or the full name phrase instead.
TICKER_WORD_TRAP = {
    "ON", "ALL", "IT", "KEY", "NOW", "OPEN", "GOOD", "CAR", "RUN", "BIG", "MAIN", "SO", "AN", "BE",
    "BY", "DO", "GO", "HE", "IF", "IN", "IS", "NO", "OR", "SEE", "ARE", "CAN", "FOR", "ONE", "OUT",
    "WELL", "WORK", "LOVE", "LIFE", "REAL", "FAST", "EAT", "PLAY", "RIDE", "TRUE", "EDIT", "HOPE",
}
MIN_TICKER_LEN = 3          # a one or two letter ticker alone is never evidence

SPAM = (re.compile(r"price\s+prediction", re.I), re.compile(r"\b20\d{2}\s*(?:to|-|‐)\s*20\d{2}\b"))

MIN_GAP_PCT, MIN_PRICE, TOP_N = 4.0, 3.0, 12


def say(msg):
    print(msg, flush=True)


def safe(label, fn, default=None):
    """Run a network call so one bad ticker or dead feed never kills the scan."""
    try:
        return fn()
    except Exception as exc:
        say(f"    warn: {label} failed: {type(exc).__name__}: {exc}")
        return default


def strip_html(text):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(text or ""))).strip()


def is_spam(title):
    return any(p.search(str(title or "")) for p in SPAM)


def num(value):
    try:
        out = float(value)
        return None if out != out else out
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- 1. market snapshot
def market_snapshot():
    say("1. market snapshot")
    out = {}
    for name, spec in INSTRUMENTS.items():
        tried = spec if isinstance(spec, list) else [spec]
        row = None
        for symbol in tried:
            def pull(s=symbol):
                hist = yf.Ticker(s).history(period="10d", interval="1d")
                closes = [num(c) for c in hist["Close"].tolist() if num(c) is not None]
                if len(closes) < 2:
                    return None
                last, prev = closes[-1], closes[-2]
                return {"symbol": s, "last": round(last, 4), "prev_close": round(prev, 4),
                        "change_pct": round((last - prev) / prev * 100.0, 3) if prev else None}
            row = safe(f"{name} [{symbol}]", pull)
            if row:
                break
        out[name] = row or {"symbol": tried[0], "last": None, "prev_close": None,
                            "change_pct": None, "note": f"no data from any of {tried}"}
        say(f"   {name:<18} {out[name].get('last')}  {out[name].get('change_pct')}%")
    return out


# --------------------------------------------------------------------------- 2. candidates
def from_screeners():
    rows = {}
    for screen in ("day_gainers", "most_actives"):
        res = safe(screen, lambda s=screen: yf.screen(s, count=100), {}) or {}
        for q in (res.get("quotes") or []):
            sym = str(q.get("symbol") or "").upper()
            if not sym or sym in rows:
                continue
            rows[sym] = {
                "ticker": sym,
                "name": q.get("shortName") or q.get("longName"),
                "price": num(q.get("regularMarketPrice")),
                "prev_close": num(q.get("regularMarketPreviousClose")),
                "gap_pct": num(q.get("regularMarketChangePercent")),
                "market_cap": num(q.get("marketCap")),
                "volume": num(q.get("regularMarketVolume")),
            }
        say(f"   {screen}: running total {len(rows)}")
    return list(rows.values())


def from_universe():
    rows = []
    for sym in UNIVERSE:
        def pull(s=sym):
            t = yf.Ticker(s)
            hist = t.history(period="10d", interval="1d")
            closes = [num(c) for c in hist["Close"].tolist() if num(c) is not None]
            if len(closes) < 2:
                return None
            last, prev = closes[-1], closes[-2]
            info = safe(f"{s} info", lambda: t.info, {}) or {}
            return {"ticker": s, "name": info.get("shortName") or info.get("longName"),
                    "price": round(last, 4), "prev_close": round(prev, 4),
                    "gap_pct": round((last - prev) / prev * 100.0, 3) if prev else None,
                    "market_cap": num(info.get("marketCap")),
                    "volume": num(hist["Volume"].tolist()[-1] if len(hist) else None)}
        row = safe(sym, pull)
        if row:
            rows.append(row)
    return rows


def gather_candidates():
    say("2. candidates")
    rows = safe("screeners", from_screeners, []) or []
    source = "yfinance predefined screeners: day_gainers + most_actives"
    if len(rows) < 5:
        say(f"   only {len(rows)} from the screeners, falling back to the static universe")
        rows = from_universe()
        source = "static UNIVERSE fallback, gap computed from the last two daily closes"
    say(f"   {len(rows)} candidates via {source}")
    return rows, source


# --------------------------------------------------------------------------- 3. gap filter
def gap_filter(rows):
    say("3. gap filter")
    kept = [r for r in rows
            if num(r.get("gap_pct")) is not None and abs(r["gap_pct"]) >= MIN_GAP_PCT
            and num(r.get("price")) is not None and r["price"] >= MIN_PRICE]
    kept.sort(key=lambda r: abs(r["gap_pct"]), reverse=True)
    say(f"   {len(kept)} passed abs(gap) >= {MIN_GAP_PCT}% and price >= ${MIN_PRICE}, keeping {TOP_N}")
    return kept[:TOP_N]


# --------------------------------------------------------------------------- 4. market news
def market_news():
    say("4. market news")
    items = []
    for name, url in FEEDS.items():
        parsed = safe(name, lambda u=url: feedparser.parse(u))
        for e in (getattr(parsed, "entries", []) or [])[:40]:
            title = strip_html(e.get("title"))
            if not title or is_spam(title):
                continue
            items.append({"publisher": name, "title": title, "link": e.get("link"),
                          "published": e.get("published") or e.get("updated"),
                          "summary": strip_html(e.get("summary"))[:400]})
        say(f"   {name}: running total {len(items)}")
    seen, out = set(), []
    for it in items:
        key = it["title"].lower()
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


# --------------------------------------------------------------------------- 5. econ calendar
def _calendar_week():
    """The weekly feed, from cache when fresh. Returns (rows, note)."""
    now = time.time()
    if CAL_CACHE.exists():
        cached = safe("calendar cache read", lambda: json.loads(CAL_CACHE.read_text("utf-8")), {}) or {}
        if now - float(cached.get("fetched_at") or 0) < CAL_TTL_SEC and cached.get("rows"):
            return cached["rows"], f"cache, fetched {cached.get('fetched_at_iso')}"
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "premarket-scan/1.0"})
        r.raise_for_status()
        rows = r.json()
        CAL_CACHE.write_text(json.dumps({
            "fetched_at": now, "fetched_at_iso": datetime.now(timezone.utc).isoformat(),
            "rows": rows}), encoding="utf-8")
        return rows, "live fetch"
    except Exception as exc:
        if CAL_CACHE.exists():
            cached = safe("calendar stale cache", lambda: json.loads(CAL_CACHE.read_text("utf-8")), {}) or {}
            if cached.get("rows"):
                return cached["rows"], (f"live fetch failed ({type(exc).__name__}), using the last "
                                        f"cached week from {cached.get('fetched_at_iso')}")
        raise


def econ_calendar():
    say("5. economic calendar")
    today = datetime.now(ET).date()
    tomorrow = today + timedelta(days=1)
    empty = {"source": "nfs.faireconomy.media ff_calendar_thisweek.json",
             "filter": "country == USD and impact == High",
             "today_date": today.isoformat(), "tomorrow_date": tomorrow.isoformat(),
             "today": [], "tomorrow": []}
    try:
        rows, note = _calendar_week()
    except Exception as exc:
        say(f"   warn: calendar unavailable: {type(exc).__name__}: {exc}")
        return {**empty, "error": f"{type(exc).__name__}: {exc}", "note": "calendar unavailable"}
    buckets = {today: [], tomorrow: []}
    for row in rows or []:
        try:
            if str(row.get("country") or "").upper() != "USD":
                continue
            if str(row.get("impact") or "").lower() != "high":
                continue
            stamp = datetime.fromisoformat(str(row.get("date")).replace("Z", "+00:00")).astimezone(ET)
            if stamp.date() in buckets:
                buckets[stamp.date()].append({
                    "time_et": stamp.strftime("%I:%M%p").lstrip("0").lower(),
                    "_sort": stamp.isoformat(), "title": row.get("title"),
                    "forecast": row.get("forecast") or None, "previous": row.get("previous") or None})
        except Exception:
            continue
    for day in buckets:
        buckets[day].sort(key=lambda e: e["_sort"])
        for e in buckets[day]:
            e.pop("_sort", None)
    say(f"   {len(buckets[today])} today, {len(buckets[tomorrow])} tomorrow ({note})")
    return {**empty, "today": buckets[today], "tomorrow": buckets[tomorrow], "note": note}


# --------------------------------------------------------------------------- 6. enrichment
def name_tokens(ticker, name):
    """What may stand as proof a headline is about this ticker.

    Returns {"ticker": str|None, "distinctive": [...], "phrase": str|None}. Each is allowed to match
    on its own; anything not in here is not evidence.
    """
    tk = str(ticker or "").upper().strip()
    usable_ticker = tk if (len(tk) >= MIN_TICKER_LEN and tk not in TICKER_WORD_TRAP) else None
    words = [w for w in re.split(r"[^A-Za-z]+", str(name or "")) if w]
    distinctive = sorted({w.upper() for w in words
                          if len(w) >= 4 and w.lower() not in NAME_STOP})
    # Accepted cost of the rule: a company whose ONLY identifying word is generic, "Strategy Inc"
    # being the case in point, can be matched by its ticker and by nothing else. A headline reading
    # "Strategy buys another 4,000 bitcoin" is deliberately missed, because matching the lone word
    # would re-open the cross-match this whole block exists to close. A missed catalyst shows up as
    # catalyst_found false, which is visible. A wrong one is silent, which is worse.
    phrase = None
    if not distinctive:
        # Every word was generic, which is Applied Digital's exact problem. The words together are
        # still distinctive, so the whole name is matched as one phrase rather than word by word.
        kept = [w.upper() for w in words if w.lower() not in {"inc", "corp", "corporation", "co",
                                                             "ltd", "plc", "the", "company"}]
        if len(kept) >= 2:
            phrase = " ".join(kept)
    return {"ticker": usable_ticker, "distinctive": distinctive, "phrase": phrase}


def headline_matches(title, toks):
    """True when the headline names this company. Returns the reason via match_reason()."""
    return match_reason(title, toks) is not None


def match_reason(title, toks):
    upper = re.sub(r"\s+", " ", str(title or "")).upper()
    if toks.get("ticker") and re.search(rf"\b{re.escape(toks['ticker'])}\b", upper):
        return f"ticker {toks['ticker']}"
    for tok in toks.get("distinctive") or []:
        if re.search(rf"\b{re.escape(tok)}\b", upper):
            return f"name token {tok}"
    if toks.get("phrase") and re.search(rf"\b{re.escape(toks['phrase'])}\b", upper):
        return f"name phrase {toks['phrase']}"
    return None


def publisher_rank(publisher):
    low = str(publisher or "").lower()
    return 0 if any(p in low for p in PRIMARY_PUBLISHERS) else 1


def catalyst_headlines(ticker, name, rss):
    toks = name_tokens(ticker, name)
    found = []
    for item in (safe(f"{ticker} news", lambda: yf.Ticker(ticker).get_news(), []) or []):
        content = item.get("content") if isinstance(item.get("content"), dict) else item
        title = strip_html(content.get("title") or item.get("title"))
        pub = ((content.get("provider") or {}).get("displayName")
               if isinstance(content.get("provider"), dict) else item.get("publisher"))
        if title and not is_spam(title):
            found.append({"title": title, "publisher": pub, "via": "yfinance"})
    for item in rss:
        if headline_matches(item["title"], toks):
            found.append({"title": item["title"], "publisher": item["publisher"], "via": "rss"})
    seen, clean = set(), []
    for f in found:
        key = f["title"].lower()
        if key in seen or not headline_matches(f["title"], toks):
            continue
        seen.add(key)
        clean.append({**f, "matched_on": match_reason(f["title"], toks)})
    clean.sort(key=lambda f: publisher_rank(f["publisher"]))
    return clean[:6]


def intraday_levels_5m(ticker):
    bars = safe(f"{ticker} 5m", lambda: yf.Ticker(ticker).history(period="1d", interval="5m",
                                                                  prepost=True))
    out = {"vwap": None, "hod": None, "lod": None, "premarket_high": None, "premarket_volume": None}
    if bars is None or not len(bars):
        return out
    pv = vol = 0.0
    pre_high, pre_vol, hi, lo = None, 0.0, None, None
    for stamp, row in bars.iterrows():
        h, l, c, v = num(row.get("High")), num(row.get("Low")), num(row.get("Close")), num(row.get("Volume")) or 0.0
        if None in (h, l, c):
            continue
        pv += (h + l + c) / 3.0 * v
        vol += v
        local = stamp.tz_convert(ET) if stamp.tzinfo else stamp
        if (local.hour, local.minute) < (9, 30):
            pre_high = h if pre_high is None else max(pre_high, h)
            pre_vol += v
        else:
            hi = h if hi is None else max(hi, h)
            lo = l if lo is None else min(lo, l)
    out.update(vwap=round(pv / vol, 4) if vol else None,
               hod=round(hi, 4) if hi is not None else None,
               lod=round(lo, 4) if lo is not None else None,
               premarket_high=round(pre_high, 4) if pre_high is not None else None,
               premarket_volume=int(pre_vol))
    return out


def daily_metrics(ticker):
    out = {"sma200": None, "prior_day_high": None, "prior_close": None, "today_open": None,
           "avg_volume_20d": None, "today_volume": None}
    hist = safe(f"{ticker} 1y", lambda: yf.Ticker(ticker).history(period="1y", interval="1d"))
    if hist is None or not len(hist):
        return out
    today = datetime.now(ET).date()
    rows = [(idx, r) for idx, r in hist.iterrows()]
    partial = [r for idx, r in rows if (idx.tz_convert(ET) if idx.tzinfo else idx).date() == today]
    closed = [r for idx, r in rows if (idx.tz_convert(ET) if idx.tzinfo else idx).date() != today]
    if partial:
        out["today_open"] = round(num(partial[-1].get("Open")) or 0, 4) or None
        out["today_volume"] = num(partial[-1].get("Volume"))
    if not closed:
        return out
    closes = [num(r.get("Close")) for r in closed if num(r.get("Close")) is not None]
    vols = [num(r.get("Volume")) for r in closed if num(r.get("Volume")) is not None]
    if len(closes) >= 200:
        out["sma200"] = round(sum(closes[-200:]) / 200.0, 4)
    out["prior_day_high"] = round(num(closed[-1].get("High")) or 0, 4) or None
    out["prior_close"] = round(num(closed[-1].get("Close")) or 0, 4) or None
    if len(vols) >= 20:
        out["avg_volume_20d"] = round(sum(vols[-20:]) / 20.0, 1)
    return out


def next_earnings(ticker):
    def pull():
        cal = yf.Ticker(ticker).calendar
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date") or []
            return str(dates[0]) if dates else None
        return None
    return safe(f"{ticker} earnings", pull)


# --------------------------------------------------------------------------- 7. eligibility flags
def flags(g):
    """The two validated rule sets from WATCHLIST_CRITERIA.md, computed in code.

    Deliberately not left to a model. These are AND filters and four out of five is a fail, which is
    exactly the sort of thing a language model rounds off when it likes the story.
    """
    gap, price = num(g.get("gap_pct")), num(g.get("price"))
    cap, rvol = num(g.get("market_cap")), num(g.get("rvol"))
    prior_high, open_px, sma200 = num(g.get("prior_day_high")), num(g.get("today_open")), num(g.get("sma200"))
    day = all([gap is not None and gap > 3, price is not None and price > 3,
               cap is not None and cap > 1_000_000_000, rvol is not None and rvol > 1.5,
               price is not None and prior_high is not None and price > prior_high])
    swing = all([gap is not None and gap >= 8, price is not None and price > 3,
                 open_px is not None and prior_high is not None and open_px > prior_high,
                 open_px is not None and sma200 is not None and open_px > sma200,
                 cap is not None and cap >= 800_000_000, bool(g.get("catalyst_found"))])
    return bool(day), bool(swing)


# --------------------------------------------------------------------------- 8. packet
CRITERIA = {
    "day_trading_trend_join_long": (
        "All required. Gap versus previous close above 3 percent, price above 3 dollars, market cap "
        "above 1 billion, relative volume above 1.5, and price above the prior day high. Backtest "
        "54.6 percent win rate, profit factor 1.59 over 280 trades."),
    "swing": (
        "All required. Gap at or above 8 percent, price above 3 dollars, open above the prior day "
        "high, open above the 200 day average, market cap at or above 800 million, and a real "
        "catalyst. Backtest 57.6 percent at profit factor 5.34 on news catalysts and 44.7 percent at "
        "2.57 on earnings catalysts. Entry and exit management is not built, so swing names are "
        "starter ideas only and carry no stop or target."),
}

GAPS_TO_FILL = [
    "Market wide earnings coverage is only partial. Per ticker earnings dates come from yfinance, "
    "but there is no full earnings calendar for the day.",
    "Intraday levels need intraday bars. VWAP, HOD, LOD and the premarket high come from 5 minute "
    "bars and are blank when the feed returns nothing for that ticker.",
    "RVOL here is FULL DAY relative volume, not premarket. yfinance reports roughly zero premarket "
    "volume, so a true premarket RVOL needs a premarket feed such as Alpaca. Treat the day_eligible "
    "flag as provisional until that feed exists.",
    "No float or short interest data, so dilution and squeeze risk cannot be measured here.",
]


def main():
    started = datetime.now(timezone.utc)
    say(f"premarket scan starting {started.isoformat()}")
    snapshot = market_snapshot()
    rows, source = gather_candidates()
    gappers = gap_filter(rows)
    news = market_news()
    calendar = econ_calendar()

    say("6. per gapper enrichment")
    enriched = []
    for i, g in enumerate(gappers, 1):
        tk = g["ticker"]
        say(f"   {i}/{len(gappers)} {tk}")
        heads = catalyst_headlines(tk, g.get("name"), news)
        g["catalyst_headlines"] = heads
        g["catalyst_found"] = bool(heads)
        g.update(intraday_levels_5m(tk))
        g.update(daily_metrics(tk))
        avg20, today_vol = num(g.get("avg_volume_20d")), num(g.get("today_volume")) or num(g.get("volume"))
        g["rvol"] = round(today_vol / avg20, 3) if (avg20 and today_vol) else None
        g["rvol_note"] = "full day relative volume, not premarket"
        g["next_earnings"] = next_earnings(tk)
        g["day_eligible"], g["swing_eligible"] = flags(g)
        enriched.append(g)

    packet = {
        "generated_at": started.isoformat(),
        "candidate_source": source,
        "trading_day_note": (f"Trading day {datetime.now(ET).date().isoformat()} ET. Scan run at "
                             f"{datetime.now(ET).strftime('%H:%M')} ET."),
        "scan_params": {"min_abs_gap_pct": MIN_GAP_PCT, "min_price": MIN_PRICE, "top_n": TOP_N,
                        "feeds": list(FEEDS), "instruments": INSTRUMENTS,
                        "universe_size": len(UNIVERSE)},
        "criteria": CRITERIA,
        "market_snapshot": snapshot,
        "econ_calendar": calendar,
        "gappers": enriched,
        "market_news": news[:20],
        "gaps_to_fill": GAPS_TO_FILL,
    }
    OUT.write_text(json.dumps(packet, indent=2, default=str), encoding="utf-8")
    day = sum(1 for g in enriched if g["day_eligible"])
    swing = sum(1 for g in enriched if g["swing_eligible"])
    say(f"wrote {OUT} with {len(enriched)} gappers, {day} day eligible, {swing} swing eligible, "
        f"{len(news)} news items, {len(calendar.get('today', []))} econ events today")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
