@echo off
rem Daily trading agent (task "SmartEntry Daily Agent", hourly :10). PAPER LEDGER ONLY - it imports no broker service
rem and cannot place a real order. Research (broker candles through the app, the daily plan with the owner's
rem TradingView inputs, the economic calendar, the ATOMIC panel as evidence) -> one decision per closed H4 candle for
rem XAUUSD and BTCUSD -> a journal entry for every decision, including the ones where it does nothing.
cd /d C:\Users\th_em\ml_trading_system
set PYTHONIOENCODING=utf-8
if not exist data\daily_agent mkdir data\daily_agent
echo ===== %date% %time% >> data\daily_agent\daily_agent.log
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" -m src.daily_agent run >> data\daily_agent\daily_agent.log 2>&1
