@echo off
rem Gold Reaper forward watch (task "SmartEntry Gold Reaper Watch", hourly :45). Records the closed trades of the
rem owner's Gold Reaper expert on the connected demo account and compares them with the vendor backtest.
rem Read-only: it never places an order and never touches the expert.
cd /d C:\Users\th_em\ml_trading_system
set PYTHONIOENCODING=utf-8
if not exist data\gold_reaper_watch mkdir data\gold_reaper_watch
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" -m src.gold_reaper_watch >> data\gold_reaper_watch\watch.log 2>&1
