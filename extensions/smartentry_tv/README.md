# SmartEntry Map for TradingView

A small Edge extension that draws the SmartEntry panel on the real TradingView chart: the day's
plan, how close the checklist is, and every strategy that can place an order right now with what
each is waiting for and what it has banked.

## Install (once, about twenty seconds)

1. Open `edge://extensions` (paste it into the address bar - a page like this cannot be opened for
   you by automation, which is why this one step is manual).
2. Turn on **Developer mode**, bottom-left.
3. Click **Load unpacked** and choose this folder:
   `C:\Users\th_em\ml_trading_system\extensions\smartentry_tv`
4. Open or refresh `tradingview.com/chart`. The panel appears at the top right.

`scripts\install_tv_extension.cmd` opens both the extensions page and this folder for you.

## Why an extension rather than a bot driving the browser

Three earlier attempts put the plan somewhere that was not the chart: the system's own page, a Pine
script that had to be copied by hand, and a worker that drove a browser. The worker needed a
TradingView session it did not have, and the only ways to give it one were to ask for a second
sign-in or to copy the browser's session cookies - the latter is a credential, and it was refused.

An extension sidesteps all of it. It runs inside the browser that is **already signed in**, so there
is no session to manage, no password, and no automation clicking around a live account. It cannot
touch the Pine scripts or the chart layout, because it only adds a panel of its own.

## What it does and does not do

- It **reads** `http://127.0.0.1:5000/api/tradingview/plan` every 30 seconds while the tab is
  visible, and renders the answer. That is all it does.
- It places **no orders**. The system's own strategies do that, on the demo account, as before.
- The fetch happens in the extension's service worker, not in the page, so the trading app needed no
  CORS change - nothing about the running system was altered to make this work.
- If the app is not running the panel says so plainly rather than showing stale numbers.
- Hiding the panel is remembered, and a small "SmartEntry" pill brings it back.
