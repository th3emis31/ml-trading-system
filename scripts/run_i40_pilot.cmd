@echo off
rem i40 Pilot, the system brain (task "SmartEntry i40 Pilot", hourly :40). Rebuilds data\i40_pilot\latest.json from
rem the memory files, the scheduled tasks and the running app's own endpoints. Read-only: it never trades or trains.
cd /d C:\Users\th_em\ml_trading_system
set PYTHONIOENCODING=utf-8
if not exist data\i40_pilot mkdir data\i40_pilot
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" -m src.i40_pilot >> data\i40_pilot\i40_pilot.log 2>&1
