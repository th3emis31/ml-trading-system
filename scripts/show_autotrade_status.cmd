@echo off
rem Show the auto-trade wiring: session platform, autonomy, MT4 bridge and the selected account.
rem Read-only - it changes nothing and touches no expert advisor.
cd /d C:\Users\th_em\ml_trading_system
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" scripts\mt4_autotrade.py status
pause
