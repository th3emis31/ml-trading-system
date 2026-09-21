@echo off
rem Disarm MT4 auto-trading and put the auto-trade session back on MT5.
rem Autonomy is switched off FIRST, so nothing can place an order while the session moves.
rem It stops new orders; it does not close anything already open. Touches no expert advisor.
cd /d C:\Users\th_em\ml_trading_system
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" scripts\mt4_autotrade.py disable
pause
