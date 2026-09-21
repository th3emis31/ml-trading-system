@echo off
rem The owner's 4H manipulation-candle rule on the Vantage demo account 11581419
rem (task "SmartEntry Demo Sweep", hourly at :09). Asks the running app to run one cycle - only the app
rem holds the MT5 connection. Account, magic 440805 and the 0.01 lot are hard-coded in
rem src\demo_sweep_trader.py; enabled/dry_run live in data\paper_trading\demo_sweep_trader.json.
rem Set dry_run true there to stop it placing anything.
cd /d C:\Users\th_em\ml_trading_system
set PYTHONIOENCODING=utf-8
if not exist data\paper_trading mkdir data\paper_trading
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" -m src.demo_sweep_trader cycle >> data\paper_trading\demo_sweep_trader.log 2>&1
