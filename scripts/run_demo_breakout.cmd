@echo off
rem Volatility Trend Breakout on the Vantage demo account 11581419 (task "SmartEntry Demo Breakout", hourly at :03).
rem Asks the running app to run one cycle (only the app holds the MT5 connection). Account, magic 440603 and
rem 2 x 0.01 lot are hard-coded in src\demo_volatility_breakout.py; dry_run in data\paper_trading\demo_volatility_breakout.json.
cd /d C:\Users\th_em\ml_trading_system
set PYTHONIOENCODING=utf-8
if not exist data\paper_trading mkdir data\paper_trading
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" -m src.demo_volatility_breakout cycle >> data\paper_trading\demo_volatility_breakout.log 2>&1
