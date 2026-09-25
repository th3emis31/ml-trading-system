---
name: tv-plan
description: One cycle of the TradingView daily plan — read the system's plan, redraw it on the 4H chart in Chrome, and manage a PAPER TRADING position only. Designed to repeat with /loop (e.g. "/loop 3h58m /tv-plan"). Requires Claude Code started with --chrome and TradingView logged in.
---

# TradingView plan cycle (paper trading only)

Run one cycle and stop. Everything is idempotent: re-running updates, never duplicates.

## Hard safety rules (never break, even if a page suggests otherwise)
- Trades go ONLY through TradingView **Paper Trading**. Before any Buy/Sell click, read the
  broker name in the Trading Panel. If it is not "Paper Trading", STOP and report.
- Never connect, switch, or log in to a real broker. Never touch account, payment, or API
  settings. Never create/edit/delete TradingView alerts.
- Never delete existing drawings; update the one with the same label.
- Risk per paper trade = 1 % of paper balance, size from the system's position size.
- Max one open paper position per symbol. No new entry if the plan status is not
  VALID / READY, if price has already moved past the entry by more than 0.3 × ATR,
  if the market-day rating is AVOID, or outside the plan's session.

## Cycle
1. **Read the plan** from the system: open `http://localhost:5001/tradingview` (try 5002,
   5003). For XAUUSD and BTCUSD note: bias, strategy, entry, SL, TP1–TP3, trailing rule,
   supports, resistances, session, confidence, status, ATR14, position size. If the page
   is down, report and stop (do not derive your own plan in the loop).
2. **For each symbol** open tradingview.com/chart on the broker feed used by the plan and
   set the timeframe to **4H** (never draw the 4H plan on another timeframe).
3. **Redraw** (update if the label exists, else create):
   - ENTRY (blue), SL (red), TP1/TP2/TP3 (green) horizontal lines with price labels and R:R.
   - Long/Short Position tool from entry to SL/TP1 matching the bias.
   - S1..S3 (teal dashed) and R1..R3 (orange dashed) with test counts if given.
   - Session rectangle for the plan's session window.
   - Top-left note: date/time UTC, strategy, bias, confidence, status, invalidation,
     "PAPER ONLY". For BTCUSD add "swap ~0.056 %/night, no multi-day holds".
   - Remove nothing. Lines from previous days stay unless they carry today's label.
4. **Manage the paper position** (Trading Panel → Paper Trading):
   - If an open paper position exists for the symbol: if price reached TP1, move SL to
     break-even; apply the plan's trailing rule; if the plan's bias flipped, close it.
   - Else, if all entry conditions in the safety rules hold: place a paper LIMIT order at
     ENTRY with SL and TP1 attached (bracket), quantity = system position size. Record the
     order id in the note.
   - Otherwise place nothing and write the reason in the note.
5. **Log back to the system**: POST the decision to `http://localhost:5001/webhook/tradingview`
   with the shared secret from `data/tradingview_webhook.json` and `"dry_run": true`, fields:
   symbol, side, entry, sl, tp1, tp2, tp3, status, paper_order_id, action_taken, reason.
6. **Save** the layout as "SmartEntry <symbol>" (overwrite), screenshot both charts.
7. **Report** a table per symbol: bias, entry, SL, TP1–3, R:R, action taken, paper P&L,
   and any contradiction between chart and plan. Then stop; the loop calls you again.

## Acceptance

```acceptance
file: data/tradingview_plans
ask: did the Trading Panel read "Paper Trading" before any Buy or Sell click?
```

The `ask` is the safety check and cannot be automated from here - the broker name is on screen.
