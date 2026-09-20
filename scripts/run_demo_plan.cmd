@echo off
rem Daily SwingTrendPullback plan on the Vantage demo account 11581419 (task "SmartEntry Demo Plan", hourly at :07).
rem Asks the running app to run one cycle - only the app holds the MT5 connection. Account, magic 440704 and the
rem 0.01 lot are hard-coded in src\demo_plan_trader.py; enabled/dry_run live in
rem data\paper_trading\demo_plan_trader.json. Set dry_run true there to stop it placing anything.
cd /d C:\Users\th_em\ml_trading_system
set PYTHONIOENCODING=utf-8
if not exist data\paper_trading mkdir data\paper_trading
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" -m src.demo_plan_trader cycle >> data\paper_trading\demo_plan_trader.log 2>&1
