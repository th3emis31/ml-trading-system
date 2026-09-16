@echo off
rem Gold session pullback on the Vantage demo account 11581419 (task "SmartEntry Demo Pullback", hourly at :01).
rem Asks the running app to run one cycle (only the app holds the MT5 connection). Account, magic 440502 and
rem 2 x 0.01 lot are hard-coded in src\demo_session_pullback.py; dry_run in data\paper_trading\demo_session_pullback.json.
cd /d C:\Users\th_em\ml_trading_system
set PYTHONIOENCODING=utf-8
if not exist data\paper_trading mkdir data\paper_trading
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" -m src.demo_session_pullback cycle >> data\paper_trading\demo_session_pullback.log 2>&1
