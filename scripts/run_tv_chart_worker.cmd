@echo off
rem Keeps the SmartEntry map current on the owner's real TradingView chart (task "SmartEntry TV Chart
rem Worker", hourly at :35 - after the Strategy Lab at :20 so the board it writes is the fresh one).
rem It saves one named Pine script, "SmartEntry Daily Plan", in the worker's OWN browser profile
rem (data\tv_worker_profile). It never touches the owner's everyday Chrome, never opens the Trade
rem panel, never places an order and never creates or renames a chart layout.
rem One-off before this works: python -m src.tradingview_chart_worker login, and sign in by hand.
cd /d C:\Users\th_em\ml_trading_system
set PYTHONIOENCODING=utf-8
if not exist data\tradingview mkdir data\tradingview
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" -m src.tradingview_chart_worker run >> data\tradingview\chart_worker.log 2>&1
