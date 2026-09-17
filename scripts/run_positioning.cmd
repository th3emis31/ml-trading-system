@echo off
rem CFTC Commitments of Traders positioning (task "SmartEntry Positioning", daily 21:10; the report is published
rem Friday 15:30 ET). Downloads the current year and rebuilds data\positioning\latest.json. Never trades.
cd /d C:\Users\th_em\ml_trading_system
set PYTHONIOENCODING=utf-8
if not exist data\positioning mkdir data\positioning
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" -m src.positioning >> data\positioning\positioning.log 2>&1
