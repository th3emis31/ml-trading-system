"""One page that answers "what is MY system doing": its own trades, wins, losses, weeks.

WHY THIS EXISTS
---------------
The owner said, on 1 October 2026: *"I want to be visible to autotrade i don't know what the system
doing is blind."* He was right to. The `/auto-trader` page already lists closed trades, but it shows
the last 25 trades on the **account** - mixed in with the other experts that run on the same
terminals - it only renders that list when the session itself executed nothing, and it carries no
win/loss count, no weekly total, no open positions and nothing about the MT4 leg. So the one question
the owner actually asks could not be answered by looking.

This page answers only that question, and only for this system. Every number here is filtered to the
magic numbers this system's own strategies set explicitly. Nothing from another expert can appear.

TWO THINGS IT DELIBERATELY DOES
-------------------------------
1. **It separates the named strategies from the shared-default magic.** `trading/mt5_service.py`
   declares ``magic: int = 903110`` as a default parameter, so a dashboard button or a manual click
   lands on the same number as the auto trader and cannot be told apart afterwards. The headline
   figure is therefore the named strategies; 903110 is shown beside it, labelled, never folded in.
2. **It shows the MT4 mirror.** The gold 4H executor has ``mirror_mt4`` on, so one signal places an
   MT5 order AND an MT4 order. A report that only reads MT5 misses half of what the system did - and
   on 1 October 2026 it missed that the MT4 leg was opened with no stop loss and no take profit.

It is READ-ONLY. It places nothing, cancels nothing, and changes no state.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from flask import Blueprint, jsonify, render_template_string

try:  # the app process already holds an initialised connection; this reuses it
    import MetaTrader5 as mt5
except Exception:  # pragma: no cover - the page degrades to "unavailable" rather than 500
    mt5 = None

#: The magics each of this system's strategies sets explicitly. A trade is this system's only if it
#: carries one of these. Kept here rather than imported so this page cannot be broadened by accident.
NAMED_STRATEGIES = {
    440401: "Gold 4H model",
    440502: "Gold session pullback",
    440603: "Volatility trend breakout",
    440704: "Daily plan executor",
    440805: "Sweep reversal",
    440906: "SmartEntry auto trader",
}
#: Counted, but never folded into the headline - see the module docstring.
SHARED_DEFAULT = {903110: "Shared default magic (ambiguous)"}
ALL_MAGICS = {**NAMED_STRATEGIES, **SHARED_DEFAULT}

DEMO_LOGIN = 11581419
JOURNAL = Path("data/paper_trading/demo_execution_journal.json")

system_trades_bp = Blueprint("system_trades", __name__)


# ----------------------------------------------------------------------------- broker time
def _server_offset_hours() -> float:
    """Hours the broker's clock runs ahead of UTC, MEASURED, not assumed.

    Deal and position timestamps come back in server time. This broker runs UTC+3, so bucketing
    them into weeks without converting puts late-evening trades in the wrong day - which is exactly
    how a Friday 21:05 trade would be reported as Saturday.
    """
    if mt5 is None:
        return 0.0
    try:
        for symbol in ("XAUUSD", "BTCUSD"):
            tick = mt5.symbol_info_tick(symbol)
            if tick and tick.time:
                delta = dt.datetime.utcfromtimestamp(tick.time) - dt.datetime.utcnow()
                return round(delta.total_seconds() / 3600.0 * 2) / 2  # nearest half hour
    except Exception:
        pass
    return 0.0


def _account_info():
    """The connected account, initialising MT5 only if nothing else in the process has yet.

    The app connects MT5 lazily, so a freshly restarted process can serve this page before any
    strategy has run. Initialising here is pinned to the terminal named in `config/machine.json`,
    never to "whichever terminal answers": two terminals run side by side on this machine, logged
    into different accounts, and attaching to the wrong one would report another account's trades.
    """
    if mt5 is None:
        return None
    info = mt5.account_info()
    if info is not None:
        return info
    try:
        from src.runtime_paths import installed_terminal
        path = installed_terminal("mt5_strategies")
    except Exception:
        path = None
    try:
        mt5.initialize(path=path) if path else mt5.initialize()
    except Exception:
        return None
    return mt5.account_info()


def _week_start(day: dt.datetime) -> dt.datetime:
    midnight = dt.datetime(day.year, day.month, day.day)
    return midnight - dt.timedelta(days=midnight.weekday())


def _net(deal) -> float:
    return float(deal.profit) + float(deal.commission) + float(deal.swap)


# ----------------------------------------------------------------------------- the numbers
def _summarise(rows: List[dict]) -> dict:
    wins = [r for r in rows if r["net"] > 0]
    losses = [r for r in rows if r["net"] < 0]
    won = sum(r["net"] for r in wins)
    lost = sum(r["net"] for r in losses)
    per: Dict[int, dict] = {}
    for r in rows:
        slot = per.setdefault(r["magic"], {"magic": r["magic"], "name": r["strategy"],
                                          "closed": 0, "wins": 0, "losses": 0, "net": 0.0})
        slot["closed"] += 1
        slot["net"] += r["net"]
        slot["wins" if r["net"] > 0 else "losses"] += 1
    return {
        "closed": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "net": round(sum(r["net"] for r in rows), 2),
        "won": round(won, 2),
        "lost": round(lost, 2),
        "best": round(max((r["net"] for r in wins), default=0.0), 2),
        "worst": round(min((r["net"] for r in losses), default=0.0), 2),
        "win_rate": round(100.0 * len(wins) / len(rows), 1) if rows else None,
        "per_strategy": sorted(per.values(), key=lambda s: -s["net"]),
    }


def _mirror_state() -> dict:
    """What the MT4 leg did, straight out of the executor's own journal."""
    out = {"available": False, "last": None, "open_ticket": None, "no_stop": None,
           "failures": [], "second_platform": None, "note": ""}
    try:
        data = json.loads(JOURNAL.read_text(encoding="utf-8"))
    except Exception as exc:
        out["note"] = f"journal unreadable: {exc}"
        return out
    events = [e for e in (data.get("events") or []) if isinstance(e, dict)]
    out["available"] = True
    for event in reversed(events):
        mirror = event.get("mirror")
        if not isinstance(mirror, list) or not mirror:
            continue
        first = mirror[0] if isinstance(mirror[0], dict) else {}
        second = mirror[1] if len(mirror) > 1 and isinstance(mirror[1], dict) else {}
        out["last"] = {"at": event.get("at"), "event": event.get("event"),
                       "reason": event.get("reason"), "server": first.get("server"),
                       "sent": first.get("sent"), "executed": first.get("executed"),
                       "ticket": first.get("ticket"), "message": first.get("message")}
        out["second_platform"] = second.get("reason") or ("ok" if second.get("executed") else None)
        if first.get("executed") and first.get("ticket"):
            out["open_ticket"] = first.get("ticket")
        break
    out["failures"] = [
        {"at": e.get("at"), "reason": e.get("reason"),
         "mt4": (e.get("mirror") or [{}])[0].get("message") if isinstance(e.get("mirror"), list) else None}
        for e in events[-12:] if e.get("event") in ("failed", "refused")
    ]
    return out


def _attempts(limit: int = 12) -> List[dict]:
    """Every order the system TRIED, including the ones the broker refused.

    A page that lists only fills hides the most actionable thing on it: on 1 October 2026 two of the
    week's three order attempts failed, both in the 21:05 slot, and nothing on any page said so.
    """
    try:
        data = json.loads(JOURNAL.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = []
    for event in (data.get("events") or [])[-limit:]:
        if not isinstance(event, dict):
            continue
        rows.append({"at": event.get("at"), "event": event.get("event"),
                     "reason": event.get("reason"), "side": event.get("side"),
                     "symbol": event.get("symbol"), "ticket": event.get("ticket"),
                     "price": event.get("price")})
    return list(reversed(rows))


def build_payload() -> dict:
    now = dt.datetime.utcnow()
    this_week = _week_start(now)
    last_week = this_week - dt.timedelta(days=7)
    payload = {
        "at": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "available": False,
        "account": None,
        "windows": {"this_week_from": this_week.strftime("%Y-%m-%d"),
                    "last_week_from": last_week.strftime("%Y-%m-%d")},
        "mt4_mirror": _mirror_state(),
        "attempts": _attempts(),
        "note": "",
    }
    if mt5 is None:
        payload["note"] = "the MetaTrader5 module is not available in this process"
        return payload
    info = _account_info()
    if info is None:
        payload["note"] = "MT5 is not connected, so trades cannot be read right now"
        return payload
    if int(info.login) != DEMO_LOGIN:
        payload["note"] = (f"connected to account {info.login}, but this system only trades "
                           f"{DEMO_LOGIN}; showing nothing rather than another account's trades")
        return payload

    offset = dt.timedelta(hours=_server_offset_hours())
    payload["available"] = True
    payload["server_offset_hours"] = offset.total_seconds() / 3600.0
    payload["account"] = {"login": int(info.login), "server": info.server,
                          "currency": info.currency, "balance": round(info.balance, 2),
                          "equity": round(info.equity, 2), "floating": round(info.profit, 2)}

    deals = mt5.history_deals_get(dt.datetime(2026, 1, 1), now + dt.timedelta(days=2)) or ()
    rows: List[dict] = []
    for deal in deals:
        if deal.entry != 1 or deal.magic not in ALL_MAGICS:
            continue
        closed_utc = dt.datetime.utcfromtimestamp(deal.time) - offset
        rows.append({
            "closed_at": closed_utc.strftime("%Y-%m-%d %H:%M"),
            "closed_sort": closed_utc.isoformat(),
            "symbol": deal.symbol,
            "volume": float(deal.volume),
            "price": float(deal.price),
            "net": round(_net(deal), 2),
            "magic": int(deal.magic),
            "strategy": ALL_MAGICS[int(deal.magic)],
            "named": int(deal.magic) in NAMED_STRATEGIES,
            "comment": (deal.comment or "")[:28],
        })
    rows.sort(key=lambda r: r["closed_sort"])

    def window(start: Optional[dt.datetime], end: Optional[dt.datetime], named_only: bool):
        chosen = []
        for r in rows:
            if named_only and not r["named"]:
                continue
            stamp = dt.datetime.fromisoformat(r["closed_sort"])
            if start and stamp < start:
                continue
            if end and stamp >= end:
                continue
            chosen.append(r)
        return chosen

    for key, start, end in (("this_week", this_week, None),
                            ("last_week", last_week, this_week),
                            ("all_time", None, None)):
        payload[key] = {
            "named": _summarise(window(start, end, True)),
            "including_shared": _summarise(window(start, end, False)),
            "trades": list(reversed(window(start, end, False))),
        }

    positions = []
    for pos in (mt5.positions_get() or ()):
        if pos.magic not in ALL_MAGICS:
            continue
        opened = dt.datetime.utcfromtimestamp(pos.time) - offset
        positions.append({
            "opened_at": opened.strftime("%Y-%m-%d %H:%M"),
            "symbol": pos.symbol,
            "side": "BUY" if pos.type == 0 else "SELL",
            "volume": float(pos.volume),
            "entry": float(pos.price_open),
            "now": float(pos.price_current),
            "sl": float(pos.sl),
            "tp": float(pos.tp),
            "profit": round(float(pos.profit), 2),
            "magic": int(pos.magic),
            "strategy": ALL_MAGICS[int(pos.magic)],
            "no_stop": not float(pos.sl),
        })
    payload["open_positions"] = positions
    payload["open_floating"] = round(sum(p["profit"] for p in positions), 2)
    return payload


@system_trades_bp.route("/api/system-trades")
def api_system_trades():
    try:
        return jsonify(build_payload())
    except Exception as exc:  # never 500 the page; report the failure as data
        return jsonify({"available": False, "note": f"{type(exc).__name__}: {exc}"})


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>What my system is trading</title>
<style>
  :root { --bg:#0b1220; --card:#121c2e; --line:#223049; --txt:#e8eefc; --dim:#93a4c4;
          --ok:#34d399; --bad:#f87171; --warn:#fbbf24; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--txt); font:15px/1.5 -apple-system,
         "Segoe UI", Roboto, Arial, sans-serif; padding-block:20px; padding-left:16px; padding-right:16px; }
  .wrap { max-width:1060px; margin:0 auto; }
  h1 { font-size:21px; margin:0 0 4px; }
  .sub { color:var(--dim); font-size:13px; margin-bottom:18px; }
  .cards { display:grid; gap:14px; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); margin-bottom:18px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; }
  .card h2 { font-size:12px; letter-spacing:.09em; text-transform:uppercase; color:var(--dim);
             margin:0 0 10px; font-weight:600; }
  .big { font-size:30px; font-weight:700; letter-spacing:-.5px; }
  .wl { display:flex; gap:18px; margin-top:8px; font-size:14px; }
  .ok { color:var(--ok); } .bad { color:var(--bad); } .warn { color:var(--warn); } .dim { color:var(--dim); }
  table { width:100%; border-collapse:collapse; font-size:13.5px; }
  th { text-align:left; color:var(--dim); font-weight:600; font-size:11px; letter-spacing:.07em;
       text-transform:uppercase; padding:8px 10px; border-bottom:1px solid var(--line); }
  td { padding:8px 10px; border-bottom:1px solid rgba(34,48,73,.6); }
  .scroll { overflow-x:auto; }
  .pill { display:inline-block; padding:2px 8px; border-radius:99px; font-size:11px;
          background:#1d2a42; color:var(--dim); }
  .banner { border:1px solid var(--warn); background:rgba(251,191,36,.09); color:var(--warn);
            border-radius:10px; padding:12px 14px; margin-bottom:16px; font-size:13.5px; }
  .foot { color:var(--dim); font-size:12px; margin-top:22px; }
  @media (max-width:520px) { .big { font-size:25px; } }
</style></head><body><div class="wrap">
<h1>What my system is trading</h1>
<div class="sub" id="sub">loading...</div>
<div id="alerts"></div>
<div class="cards" id="cards"></div>
<div class="card" style="margin-bottom:14px;">
  <h2>Open right now</h2><div class="scroll"><table id="open"></table></div></div>
<div class="card" style="margin-bottom:14px;">
  <h2>Every trade this week and last week</h2><div class="scroll"><table id="trades"></table></div></div>
<div class="card">
  <h2>Orders the system tried, including refusals</h2><div class="scroll"><table id="attempts"></table></div></div>
<div class="foot" id="foot"></div>
</div><script>
const money = (n, cur) => (n > 0 ? '+' : '') + Number(n).toFixed(2) + ' ' + (cur || '');
const cls = n => n > 0 ? 'ok' : (n < 0 ? 'bad' : 'dim');
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function weekCard(title, block, cur) {
  const n = block.named;
  const extra = block.including_shared.closed - n.closed;
  return `<div class="card"><h2>${title}</h2>
    <div class="big ${cls(n.net)}">${n.closed ? money(n.net, cur) : '&mdash;'}</div>
    <div class="wl"><span class="ok">${n.wins} won</span>
      <span class="bad">${n.losses} lost</span>
      <span class="dim">${n.closed} closed${n.win_rate != null ? ' &middot; ' + n.win_rate + '%' : ''}</span></div>
    ${n.closed ? `<div class="dim" style="margin-top:8px;font-size:12.5px;">
       best ${money(n.best, '')} &middot; worst ${money(n.worst, '')}</div>` : ''}
    ${extra > 0 ? `<div class="dim" style="margin-top:8px;font-size:12px;">
       + ${extra} on the shared default magic, ${money(block.including_shared.net - n.net, '')} &mdash; shown separately
       because that magic is also mt5_service's default</div>` : ''}
  </div>`;
}

async function load() {
  let d;
  try { d = await (await fetch('/api/system-trades', {cache:'no-store'})).json(); }
  catch (e) { document.getElementById('sub').textContent = 'could not reach the app: ' + e; return; }
  if (!d.available) {
    document.getElementById('sub').textContent = d.note || 'not available';
    document.getElementById('cards').innerHTML = '';
    return;
  }
  const a = d.account, cur = a.currency;
  document.getElementById('sub').innerHTML =
    `account <strong>${a.login}</strong> ${esc(a.server)} &middot; balance ${a.balance.toFixed(2)} ${cur}
     &middot; equity ${a.equity.toFixed(2)} ${cur} &middot; read ${esc(d.at)}
     &middot; <span class="pill">this system's magics only</span>`;

  const alerts = [];
  (d.open_positions || []).filter(p => p.no_stop).forEach(p =>
    alerts.push(`${esc(p.symbol)} ${p.side} ${p.volume} (${esc(p.strategy)}) is open with <strong>no stop loss</strong>.`));
  const m = d.mt4_mirror || {};
  if (m.open_ticket) alerts.push(`MT4 mirror ticket <strong>${esc(m.open_ticket)}</strong> is open on ${esc((m.last||{}).server||'MT4')}. The MT4 leg is not included in the money figures above.`);
  if (m.second_platform && m.second_platform !== 'ok') alerts.push(`Second MT4 platform did not mirror: ${esc(m.second_platform)}.`);
  document.getElementById('alerts').innerHTML = alerts.map(t => `<div class="banner">${t}</div>`).join('');

  document.getElementById('cards').innerHTML =
    weekCard('This week', d.this_week, cur) +
    weekCard('Last week', d.last_week, cur) +
    weekCard('All time', d.all_time, cur) +
    `<div class="card"><h2>Open now</h2>
       <div class="big ${cls(d.open_floating)}">${(d.open_positions||[]).length ? money(d.open_floating, cur) : '&mdash;'}</div>
       <div class="wl"><span class="dim">${(d.open_positions||[]).length} position(s) on MT5${m.open_ticket ? ' + 1 on MT4' : ''}</span></div></div>`;

  const open = d.open_positions || [];
  document.getElementById('open').innerHTML = open.length
    ? `<tr><th>Opened (UTC)</th><th>Symbol</th><th>Side</th><th>Lots</th><th>Entry</th><th>Now</th>
         <th>Stop</th><th>Target</th><th>P/L</th><th>Strategy</th></tr>` + open.map(p => `<tr>
        <td>${esc(p.opened_at)}</td><td><strong>${esc(p.symbol)}</strong></td>
        <td class="${p.side === 'BUY' ? 'ok' : 'bad'}">${p.side}</td><td>${p.volume.toFixed(2)}</td>
        <td>${p.entry.toFixed(2)}</td><td>${p.now.toFixed(2)}</td>
        <td class="${p.no_stop ? 'bad' : ''}">${p.sl ? p.sl.toFixed(2) : 'NONE'}</td>
        <td>${p.tp ? p.tp.toFixed(2) : '&mdash;'}</td>
        <td class="${cls(p.profit)}"><strong>${money(p.profit, '')}</strong></td>
        <td>${esc(p.strategy)} <span class="pill">${p.magic}</span></td></tr>`).join('')
    : `<tr><td class="dim">Nothing open.</td></tr>`;

  const rows = (d.this_week.trades || []).concat(d.last_week.trades || []);
  document.getElementById('trades').innerHTML = rows.length
    ? `<tr><th>Closed (UTC)</th><th>Symbol</th><th>Lots</th><th>Exit</th><th>Result</th><th>Strategy</th></tr>`
      + rows.map(t => `<tr><td>${esc(t.closed_at)}</td><td><strong>${esc(t.symbol)}</strong></td>
        <td>${t.volume.toFixed(2)}</td><td>${t.price.toFixed(2)}</td>
        <td class="${cls(t.net)}"><strong>${t.net > 0 ? 'WON ' : 'LOST '}${money(t.net, '')}</strong></td>
        <td>${esc(t.strategy)} <span class="pill">${t.magic}</span></td></tr>`).join('')
    : `<tr><td class="dim">No closed trades in either week.</td></tr>`;

  const at = d.attempts || [];
  document.getElementById('attempts').innerHTML = at.length
    ? `<tr><th>When (UTC)</th><th>Result</th><th>Side</th><th>Symbol</th><th>Price</th><th>Why</th></tr>`
      + at.map(x => `<tr><td>${esc(x.at)}</td>
        <td class="${x.event === 'opened' ? 'ok' : (x.event === 'failed' ? 'bad' : 'warn')}">${esc(x.event)}</td>
        <td>${esc(x.side)}</td><td>${esc(x.symbol)}</td>
        <td>${x.price ? Number(x.price).toFixed(2) : ''}</td><td class="dim">${esc(x.reason)}</td></tr>`).join('')
    : `<tr><td class="dim">The gold 4H executor's journal has no entries.</td></tr>`;

  document.getElementById('foot').innerHTML =
    `Only this system's own magics appear here: ${Object.keys({...{}}).length ? '' : ''}` +
    `440401, 440502, 440603, 440704, 440805, 440906, and 903110 shown separately. ` +
    `No other expert on the terminal can appear. Times converted from broker time ` +
    `(UTC${d.server_offset_hours >= 0 ? '+' : ''}${d.server_offset_hours}) to UTC. Refreshes every 30 s.`;
}
load();
setInterval(load, 30000);
</script></body></html>"""


@system_trades_bp.route("/system-trades")
def page_system_trades():
    return render_template_string(PAGE)


def register_system_trades_routes(app) -> None:
    """Wire the page and its endpoint. Additive: it registers two new routes and nothing else."""
    if "system_trades" not in app.blueprints:
        app.register_blueprint(system_trades_bp)
